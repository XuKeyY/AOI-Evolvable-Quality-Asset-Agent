"""Initial adaptation, cumulative human feedback, two inspection modes and gated releases."""
import csv
import hashlib
import json
import shutil
from pathlib import Path
from services.base_service import BaseAOIService, MODEL_FILES, WEIGHT_SHA256
from services.asset_service import sha256
from services.cloud_service import CloudSyncService
from services.version_service import VersionService, DEFAULT_GATE, metrics, gate
from services.store import identifier, now
from services.trace_service import TraceService
from services.document_service import DocumentService
from services.graph_service import GraphService

BUSINESS_ROOT=Path(__file__).resolve().parents[1]
MODES={'review_test','machine_test'}

def digest(items):
    return hashlib.sha256(json.dumps(items,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class AOIService(BaseAOIService):
    def __init__(self,config,runner=None,cloud_transport=None):
        super().__init__(config,runner)
        self.traces=TraceService(self.store)
        self.documents=DocumentService(self.assets,self.store,self.traces)
        self.graph=GraphService(self.store,self.documents,self.traces)
        self.cloud=CloudSyncService(self.store,config.get('cloud',{}),self.lock,cloud_transport)
        self.versions=VersionService(self)
        # Legacy candidates are not silently published. Migrate their lineage only.
        for model in self.store.all('model'):
            if 'adaptation_type' not in model and not model.get('development'):
                task=self.get_task(model['task_id'])
                if (len(task['normal']),len(task['defect']))==(100,30):
                    manifest=json.loads((Path(model['adapt_output'])/'dataset_manifest.json').read_text(encoding='utf-8'))
                    semantic=sorted([{**{k:a[k] for k in ('asset_id','sha256','pixel_sha256')},'label':a['role']} for a in manifest],key=lambda a:a['asset_id'])
                    model.update(adaptation_type='initial',parent_model=None,feedback_cutoff=0,
                        dataset_sha256=digest(semantic),training_counts={'normal':100,'defect':30})
                    task.update(initial_locked=True)
                    task.setdefault('initial_model',model['id'])
                    self.store.put_many([('model',model),('task',task)])

    def close(self):
        super().close()
        self.cloud.close()

    def get_task(self,task_id):
        task=self.store.get('task',task_id)
        for key,value in {'validation_normal':[],'validation_defect':[],'published_model':None,'initial_locked':False,'validation_locked':False}.items():
            task.setdefault(key,value)
        return task

    def attach(self,task_id,role,asset_ids):
        if role not in ('normal','defect','validation_normal','validation_defect'):
            raise ValueError('Unknown dataset role')
        with self.lock:
            task=self.get_task(task_id)
            validation=role.startswith('validation_')
            if task['validation_locked'] if validation else task['initial_locked']:
                raise ValueError('This dataset is frozen; labels may be revised with audit history')
            if not asset_ids or len(set(asset_ids))!=len(asset_ids):
                raise ValueError('Provide unique asset IDs')
            seen={self.assets.resolve(i)['pixel_sha256'] for key in ('normal','defect','validation_normal','validation_defect') for i in task[key]}
            if validation:
                seen.update(r['pixel_sha256'] for r in self.store.all('review') if r['task_id']==task_id and r['label'] in ('normal','defect'))
            for asset_id in asset_ids:
                asset=self.assets.resolve(asset_id)
                if asset.get('type')!='image':
                    raise ValueError('Only image assets can be attached to AOI datasets')
                if asset['pixel_sha256'] in seen:
                    raise ValueError('Duplicate, conflicting label, or train/validation overlap')
                seen.add(asset['pixel_sha256'])
            if not validation and len(task[role])+len(asset_ids)>(100 if role=='normal' else 30):
                raise ValueError('Initial support exceeds 100 normal / 30 defect')
            task[role].extend(asset_ids)
            task['revision']+=1
            return self.store.put('task',task)

    def _validation_pixels(self,task):
        return {self.assets.resolve(i)['pixel_sha256'] for key in ('validation_normal','validation_defect') for i in task[key]}

    def revise_initial_label(self,task_id,asset_id,label,operator,note=''):
        if label not in ('normal','defect') or not operator.strip():
            raise ValueError('Confirmed normal/defect label and operator are required')
        with self.lock:
            task=self.get_task(task_id)
            if not task['initial_locked'] or asset_id not in task['normal']+task['defect']:
                raise ValueError('Only frozen original support labels may be corrected')
            asset=self.assets.resolve(asset_id)
            revision={'id':identifier('label_revision'),'task_id':task_id,'asset_id':asset_id,
                'pixel_sha256':asset['pixel_sha256'],'label':label,'operator':operator,'note':note,
                'sequence':self.store.next_sequence(),'created_at':now()}
            event=self.cloud.enqueue('support_label_revision',{'revision':revision,'model_version':task['published_model']},[self._attachment(asset)])
            revision['cloud_event_id']=event['id']
            self.store.put_many([('label_revision',revision),('sync_event',event)])
            return revision

    def review(self,record_id,label,operator,note=''):
        if label not in ('normal','defect','uncertain') or not operator.strip():
            raise ValueError('Label normal/defect/uncertain and operator are required')
        with self.lock:
            record=self.store.get('inspection_record',record_id)
            if record['mode']!='review_test':
                raise ValueError('Machine-test records cannot generate learning feedback')
            if self.store.get('job',record['inspection_id'])['status']!='succeeded':
                raise ValueError('Inspection is not complete')
            task=self.get_task(record['task_id'])
            asset=self.assets.resolve(record['asset_id'])
            learning=label in ('normal','defect') and asset['pixel_sha256'] not in self._validation_pixels(task)
            review={'id':identifier('review'),'record_id':record_id,'inspection_id':record['inspection_id'],
                'task_id':record['task_id'],'asset_id':record['asset_id'],'pixel_sha256':asset['pixel_sha256'],
                'label':label,'operator':operator,'note':note,'learning_eligible':learning,
                'sequence':self.store.next_sequence(),'created_at':now()}
            event=self.cloud.enqueue('human_review',{'review':review,'machine_record':record},
                [self._attachment(asset)],record['cloud_event_id'])
            review['cloud_event_id']=event['id']
            self.store.put_many([('review',review),('sync_event',event)])
            return review

    def training_snapshot(self,task_id,cutoff=None):
        task=self.get_task(task_id)
        annotations=[r for kind in ('review','label_revision') for r in self.store.all(kind) if r['task_id']==task_id]
        if cutoff is None:
            cutoff=max((r['sequence'] for r in annotations),default=0)
        annotations=[r for r in annotations if r['sequence']<=cutoff]
        validation=self._validation_pixels(task)
        latest_reviews={}
        for r in sorted((r for r in annotations if 'record_id' in r),key=lambda r:r['sequence']):
            latest_reviews[r['asset_id']]=r
        groups={}
        for asset_id,r in latest_reviews.items():
            if r['label'] in ('normal','defect') and r['pixel_sha256'] not in validation:
                groups.setdefault(r['pixel_sha256'],[]).append(r)
        base={}
        excluded=[]
        for role in ('normal','defect'):
            for asset_id in task[role]:
                asset=self.assets.resolve(asset_id)
                confirmations=[r for r in annotations if r['asset_id']==asset_id and r['label'] in ('normal','defect')]
                confirmation=max(confirmations,key=lambda r:r['sequence']) if confirmations else None
                label=confirmation['label'] if confirmation else role
                pixel=asset['pixel_sha256']
                others=groups.get(pixel,[])
                # A later explicit correction of the original resolves earlier conflicting duplicates.
                conflicting=[r for r in others if r['label']!=label and (not confirmation or r['sequence']>confirmation['sequence'])]
                if pixel in validation or conflicting:
                    excluded.append({'asset_id':asset_id,'reason':'validation_overlap' if pixel in validation else 'conflicting_labels'})
                    continue
                base[pixel]={**asset,'label':label,'source':'initial','label_sequence':confirmation['sequence'] if confirmation else 0}
        original_pixels={self.assets.resolve(i)['pixel_sha256'] for i in task['normal']+task['defect']}
        for pixel,reviews in groups.items():
            if pixel in original_pixels:
                continue
            labels={r['label'] for r in reviews}
            if len(labels)>1:
                excluded.extend({'asset_id':r['asset_id'],'reason':'conflicting_labels'} for r in reviews)
                continue
            r=min(reviews,key=lambda r:r['asset_id'])
            asset=self.assets.resolve(r['asset_id'])
            base[pixel]={**asset,'label':r['label'],'source':'feedback','label_sequence':r['sequence']}
        items=sorted(base.values(),key=lambda a:a['asset_id'])
        semantic=[{k:a[k] for k in ('asset_id','sha256','pixel_sha256','label')} for a in items]
        return {'items':items,'excluded':excluded,'feedback_cutoff':cutoff,
            'dataset_sha256':digest(semantic),'dataset_version':'dataset_'+digest(semantic)[:16],
            'counts':{label:sum(a['label']==label for a in items) for label in ('normal','defect')}}

    def submit_adaptation(self,task_id,development=False):
        return self.submit_initial_adaptation(task_id,development)

    def submit_initial_adaptation(self,task_id,development=False):
        with self.lock:
            task=self.get_task(task_id)
            if task['published_model']:
                raise ValueError('Initial model is published; use evolution')
            counts=(len(task['normal']),len(task['defect']))
            if development:
                if not self.config.get('allow_development') or counts[0]<12 or counts[1]<3:
                    raise ValueError('Development initial adaptation needs at least 12 normal and 3 defect images')
            elif counts!=(100,30):
                raise ValueError(f'Initial adaptation requires exactly 100 normal + 30 defect; got {counts}')
            snapshot=self.training_snapshot(task_id)
            if (snapshot['counts']['normal'],snapshot['counts']['defect'])!=counts:
                raise ValueError('Corrected initial data must still contain 100 normal + 30 defect without exclusions')
            return self._queue_adaptation(task,'initial',snapshot,None,development)

    def submit_evolution(self,task_id):
        with self.lock:
            task=self.get_task(task_id)
            parent=task['published_model']
            if not parent or not task.get('initial_model'):
                raise ValueError('Publish the initial model before evolving')
            snapshot=self.training_snapshot(task_id)
            if snapshot['counts']['normal']<9 or snapshot['counts']['defect']<3:
                raise ValueError('Formal evolution requires at least 9 valid normal and 3 defect samples')
            model=self.check_model(parent)
            if model.get('dataset_sha256')==snapshot['dataset_sha256']:
                raise ValueError('No effective training-data change since published model')
            return self._queue_adaptation(task,'evolution',snapshot,parent,False)

    def _queue_adaptation(self,task,adaptation_type,snapshot,parent,development):
        if any(j['task_id']==task['id'] and j['kind']=='adapt' and j['status'] in ('queued','running') for j in self.store.all('job')):
            raise ValueError('This task already has an active adaptation')
        self._check_environment()
        job=self._job('adapt',task['id'])
        folder=Path(job['output_dir'])
        manifest=[]
        for label in ('normal','defect'):
            target=folder/'support'/label
            target.mkdir(parents=True)
            for asset in snapshot['items']:
                if asset['label']!=label:
                    continue
                path=target/(asset['id']+Path(asset['path']).suffix)
                shutil.copy2(asset['path'],path)
                manifest.append({**asset,'path':str(path),'role':label})
        job.update(adaptation_type=adaptation_type,parent_model=parent,model_version=identifier('model'),
            dataset_version=snapshot['dataset_version'],dataset_sha256=snapshot['dataset_sha256'],
            dataset_revision=task['revision'],feedback_cutoff=snapshot['feedback_cutoff'],
            excluded_samples=snapshot['excluded'],training_counts=snapshot['counts'],
            development=development,weights_sha256=sha256(self.weights))
        (folder/'dataset_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
        if adaptation_type=='initial':
            task['initial_locked']=True
        self.store.put_many([('job',job),('task',task)])
        self.pool.submit(self._execute,job['id'])
        return job

    def _algorithm_device(self):
        device=str(self.config.get('device','cpu')).lower()
        if device=='auto':
            try:
                import torch
                return 'cuda' if torch.cuda.is_available() else 'cpu'
            except Exception:
                return 'cpu'
        if device not in ('cpu','cuda'):
            raise ValueError('device must be cpu, cuda, or auto')
        return device

    def check_model(self,model_version):
        model=self.store.get('model',model_version)
        if model.get('algorithm_family')!='AOI_100plus30_detector_v1':
            raise ValueError('This model belongs to the retired AOI core; create a new candidate with the current core')
        for name in MODEL_FILES:
            path=Path(model['model_dir'])/name
            if not path.is_file() or sha256(path)!=model['file_hashes'][name]:
                raise ValueError('Model files missing or changed')
        return model

    def inspect_images(self,asset_ids,model_version=None,mode='review_test',task_id=None):
        if mode not in MODES:
            raise ValueError('mode must be review_test or machine_test')
        with self.lock:
            if task_id is None:
                if not model_version:
                    raise ValueError('task_id is required when model version is omitted')
                task_id=self.check_model(model_version)['task_id']
            task=self.get_task(task_id)
            selected=task['published_model']
            if not selected or (model_version and model_version!=selected):
                raise ValueError('Detection uses only the current published model')
            return self._queue_inference(asset_ids,selected,mode,'inspect')

    def inspect_candidate_images(self,asset_ids,model_version):
        # CLI diagnostic only. No cloud upload, human-feedback generation or publishing.
        with self.lock:
            return self._queue_inference(asset_ids,model_version,'candidate_probe','probe')

    def _queue_inference(self,asset_ids,model_version,mode,kind):
        if not asset_ids or len(set(asset_ids))!=len(asset_ids):
            raise ValueError('Provide unique nonempty asset IDs')
        model=self.check_model(model_version)
        self._check_environment()
        job=self._job(kind,model['task_id'])
        folder=Path(job['output_dir'])/'inputs'
        folder.mkdir()
        mapping={}
        for asset_id in asset_ids:
            asset=self.assets.resolve(asset_id)
            path=folder/(asset_id+Path(asset['path']).suffix)
            shutil.copy2(asset['path'],path)
            mapping[str(path.resolve()).casefold()]=asset_id
        job.update(model_version=model_version,model_hashes=model['file_hashes'],dataset_version=model['dataset_version'],
            mode=mode,asset_ids=asset_ids,input_mapping=mapping)
        self.store.put('job',job)
        self.pool.submit(self._execute,job['id'])
        return job

    def gate_policy(self):
        return {**DEFAULT_GATE,**self.config.get('regression_gate',{})}

    def submit_validation(self,model_version):
        with self.lock:
            model=self.check_model(model_version)
            task=self.get_task(model['task_id'])
            if model.get('development'):
                raise ValueError('Development models cannot pass formal release validation')
            policy=self.gate_policy()
            if min(len(task['validation_normal']),len(task['validation_defect']))<policy['min_per_class']:
                raise ValueError('Not enough independent labelled validation images')
            if model.get('parent_model')!=task['published_model']:
                raise ValueError('Validate against the current parent; candidate is stale')
            labels={i:label for label in ('normal','defect') for i in task['validation_'+label]}
            pixels=self._validation_pixels(task)
            for version in (model_version,model.get('parent_model')):
                if version:
                    candidate=self.check_model(version)
                    manifest=json.loads((Path(candidate['adapt_output'])/'dataset_manifest.json').read_text(encoding='utf-8'))
                    if any(a['pixel_sha256'] in pixels for a in manifest):
                        raise ValueError('Validation overlaps candidate or baseline training data')
            version=digest([{**{k:self.assets.resolve(i)[k] for k in ('sha256','pixel_sha256')},'asset_id':i,'label':label} for i,label in sorted(labels.items())])
            task.update(validation_locked=True,validation_version='validation_'+version[:16])
            job=self._job('validate',task['id'])
            folder=Path(job['output_dir'])/'inputs'
            folder.mkdir()
            mapping={}
            for asset_id in labels:
                asset=self.assets.resolve(asset_id)
                path=folder/(asset_id+Path(asset['path']).suffix)
                shutil.copy2(asset['path'],path)
                mapping[str(path.resolve()).casefold()]=asset_id
            job.update(model_version=model_version,baseline_model=model.get('parent_model'),
                validation_version=task['validation_version'],validation_labels=labels,policy=policy,
                asset_ids=list(labels),input_mapping=mapping,model_hashes=model['file_hashes'])
            model['validation_job']=job['id']
            self.store.put_many([('task',task),('job',job),('model',model)])
            self.pool.submit(self._execute,job['id'])
            return job

    def _inference(self,job,model_version,output):
        model=self.check_model(model_version)
        output.mkdir(parents=True,exist_ok=True)
        inputs=Path(job['output_dir'])/'inputs'
        paths=sorted(path for path in inputs.iterdir() if path.is_file())
        batch_size=max(1,int(self.config.get('inference_batch_size',20)))
        commands=[]
        rows=[]
        for start in range(0,len(paths),batch_size):
            chunk=paths[start:start+batch_size]
            batched=len(paths)>batch_size
            batch_output=output/(f'batch_{start//batch_size:03d}' if batched else '')
            batch_output.mkdir(parents=True,exist_ok=True)
            csv_path=batch_output/'predictions.csv'
            input_args=list(map(str,chunk)) if batched else [str(inputs)]
            command=[str(self.python),'-u',str(self.original/'code/run_infer.py'),
                '--model-dir',model['model_dir'],'--input',*input_args,'--csv',str(csv_path),
                '--weights',str(self.weights),'--device',self._algorithm_device(),
                '--cache-dir',str(output/'feature_cache'),'--quiet']
            commands.append(command)
            self.runner(command,batch_output)
            with csv_path.open(encoding='utf-8-sig',newline='') as stream:
                rows.extend(csv.DictReader(stream))
        (output/'command.json').write_text(json.dumps({'batch_size':batch_size,'commands':commands},ensure_ascii=False,indent=2),encoding='utf-8')
        if not rows:
            raise RuntimeError('Inference produced no result rows')
        with (output/'predictions.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        converted={**job,'model_version':model_version}
        return self.csv_to_results(output/'predictions.csv',converted)

    @staticmethod
    def _attachment(asset):
        return {k:asset[k] for k in ('asset_id','path','sha256')}

    def _save_inspections(self,job,results):
        entries=[]
        for row in results:
            asset=self.assets.resolve(row['asset_id'])
            row.update(record_id=identifier('record'),mode=job['mode'],task_id=job['task_id'])
            record={'id':row['record_id'],**row,'created_at':now()}
            snapshot=Path(job['output_dir'])/'inputs'/(asset['id']+Path(asset['path']).suffix)
            attachment={**self._attachment(asset),'path':str(snapshot)}
            event=self.cloud.enqueue('inspection',{'record':record,'asset':{k:asset[k] for k in ('asset_id','sha256','pixel_sha256','width','height','type')},'dataset_version':job['dataset_version']},[attachment])
            record['cloud_event_id']=event['id']
            row['cloud_event_id']=event['id']
            entries.extend([('inspection_record',record),('sync_event',event)])
        self.store.put_many(entries)

    def _execute(self,job_id):
        job=self.store.get('job',job_id)
        folder=Path(job['output_dir'])
        try:
            job.update(status='running',stage=job['kind'],started_at=now())
            self.store.put('job',job)
            if job['kind']=='adapt':
                manifest=json.loads((folder/'dataset_manifest.json').read_text(encoding='utf-8'))
                if any(sha256(Path(a['path']))!=a['sha256'] for a in manifest):
                    raise ValueError('Frozen training data changed after submission')
                model_dir=folder/'support_model'
                command=[str(self.python),'-u',str(self.original/'code/run_adapt.py'),
                    '--normal-dir',str(folder/'support/normal'),'--defect-dir',str(folder/'support/defect'),
                    '--output-dir',str(model_dir),'--weights',str(self.weights),
                    '--device',self._algorithm_device()]
                if job['adaptation_type']=='evolution' or job['development']:
                    command+=['--allow-nonstandard-counts']
                (folder/'command.json').write_text(json.dumps(command,ensure_ascii=False,indent=2),encoding='utf-8')
                self.runner(command,folder)
                if any(not (model_dir/name).is_file() for name in MODEL_FILES):
                    raise RuntimeError('Adaptation did not produce all required model files')
                detector_meta=json.loads((model_dir/'detector.json').read_text(encoding='utf-8'))
                if detector_meta.get('format')!='aoi_100plus30_detector_v1':
                    raise RuntimeError('Unexpected detector model format')
                summary={'status':'ok','selected_method':'DINO_GLOBAL_LINEAR',
                    'selected_threshold':float(__import__('numpy').load(model_dir/'detector_arrays.npz',allow_pickle=False)['threshold']),
                    'support_oof_metrics':detector_meta.get('support_oof'),
                    'detector':detector_meta}
                model={'id':job['model_version'],'model_version':job['model_version'],'task_id':job['task_id'],
                    'dataset_version':job['dataset_version'],'dataset_sha256':job['dataset_sha256'],
                    'model_dir':str(model_dir),'adapt_output':str(folder),'file_hashes':{name:sha256(model_dir/name) for name in MODEL_FILES},
                    'development':job['development'],'adaptation_type':job['adaptation_type'],
                    'parent_model':job['parent_model'],'feedback_cutoff':job['feedback_cutoff'],
                    'training_counts':job['training_counts'],'algorithm_family':'AOI_100plus30_detector_v1',
                    'status':'candidate','created_at':now()}
                with self.lock:
                    task=self.get_task(job['task_id'])
                    if job['adaptation_type']=='initial':
                        task['initial_model']=model['id']
                    self.store.put_many([('model',model),('task',task)])
                job['summary']=summary
            elif job['kind']=='validate':
                if self.check_model(job['model_version'])['file_hashes']!=job['model_hashes']:
                    raise ValueError('Candidate changed after validation submission')
                candidate=self._inference(job,job['model_version'],folder/'candidate')
                cm=metrics(candidate,job['validation_labels'])
                bm=None
                if job['baseline_model']:
                    baseline=self._inference(job,job['baseline_model'],folder/'baseline')
                    bm=metrics(baseline,job['validation_labels'])
                decision=gate(cm,bm,job['policy'])
                job['comparison']={**decision,'candidate':cm,'baseline':bm,'baseline_model':job['baseline_model'],
                    'candidate_model':job['model_version'],'validation_version':job['validation_version'],
                    'policy':job['policy'],'model_hashes':job['model_hashes']}
                (folder/'comparison.json').write_text(json.dumps(job['comparison'],indent=2),encoding='utf-8')
            else:
                if self.check_model(job['model_version'])['file_hashes']!=job['model_hashes']:
                    raise ValueError('Pinned model changed after inspection submission')
                results=self._inference(job,job['model_version'],folder)
                if job['kind']=='inspect':
                    self._save_inspections(job,results)
                (folder/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
                job['results']=results
            job.update(status='succeeded',stage='completed',finished_at=now())
        except Exception as exc:
            job.update(status='failed',stage='failed',error=str(exc),finished_at=now())
        self.store.put('job',job)

