"""Behaviour tests use fixture algorithms; they do not prove AOI model accuracy."""
import csv
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from PIL import Image
from fastapi.testclient import TestClient
import httpx
import numpy as np
from api.app import create_app
from services.aoi_service import AOIService,MODEL_FILES

def picture(index,label='normal'):
    stream=io.BytesIO()
    Image.new('RGB',(8,8),(index%256,index//256,20 if label=='normal' else 200)).save(stream,format='PNG')
    stream.seek(0)
    return stream

class FixtureService(AOIService):
    def _check_environment(self):
        pass

def wait(service,job):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        result=service.get_status(job['id'])
        if result['status'] in ('succeeded','failed'):
            return result
        time.sleep(0.01)
    raise AssertionError('Job did not finish')

class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.config={'data_dir':str(root/'data'),'original_dir':str(root),'algorithm_python':str(root/'python'),
            'weights':str(root/'weights'),'allow_development':True,'cloud':{'enabled':False}}
        (root/'weights').write_bytes(b'fixture')
        self.commands=[]
        self.bad_next=False
        self.fail_next=False
        self.service=FixtureService(self.config,self.algorithm)
        self.task=self.service.create_task('fixture product')
        self.index=2000

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def algorithm(self,command,folder):
        self.commands.append(command)
        def arg(key):
            return command[command.index(key)+1]
        if self.fail_next:
            self.fail_next=False
            raise RuntimeError('fixture algorithm failure')
        if '--normal-dir' in command:
            out=Path(arg('--output-dir'))
            out.mkdir()
            (out/'detector.json').write_text(json.dumps({'format':'aoi_100plus30_detector_v1',
                'fixture':True,'bad':self.bad_next,'support_oof':{'balAcc':1.0}}),encoding='utf-8')
            self.bad_next=False
            np.savez_compressed(out/'detector_arrays.npz',threshold=np.asarray(0.95))
        else:
            meta=json.loads((Path(arg('--model-dir'))/'detector.json').read_text(encoding='utf-8'))
            with Path(arg('--csv')).open('w',encoding='utf-8-sig',newline='') as stream:
                writer=csv.DictWriter(stream,fieldnames=['path','score','threshold','prediction','label'])
                writer.writeheader()
                for path in sorted(Path(arg('--input')).glob('*')):
                    with Image.open(path) as image:
                        defect=image.getpixel((0,0))[2]>100
                    score=1.23456789012345 if defect or meta['bad'] else 0.2
                    writer.writerow({'path':str(path.resolve()),'score':score,'threshold':0.95,
                        'prediction':int(score>=0.95),'label':'NG' if score>=0.95 else 'OK'})

    def asset(self,index=None,label='normal'):
        if index is None:
            self.index+=1
            index=self.index
        return self.service.assets.register(picture(index,label),'image.png')['id']

    def dataset(self):
        for role,count,offset,label in [('normal',100,0,'normal'),('defect',30,100,'defect'),
                ('validation_normal',5,500,'normal'),('validation_defect',5,600,'defect')]:
            ids=[self.asset(offset+i,label) for i in range(count)]
            self.service.attach(self.task['id'],role,ids)

    def initial(self,publish=True):
        self.dataset()
        job=wait(self.service,self.service.submit_initial_adaptation(self.task['id']))
        self.assertEqual(job['status'],'succeeded',job)
        version=job['model_version']
        if publish:
            report=wait(self.service,self.service.submit_validation(version))
            self.assertTrue(report['comparison']['passed'],report)
            self.service.versions.publish(self.task['id'],version,'tester',None)
        return version

    def detect(self,assets,mode='review_test'):
        job=wait(self.service,self.service.inspect_images(assets,mode=mode,task_id=self.task['id']))
        self.assertEqual(job['status'],'succeeded',job)
        return job

    def feedback(self,label='normal'):
        asset=self.asset(label=label)
        row=self.detect([asset])['results'][0]
        review=self.service.review(row['record_id'],label,'tester','fixture confirmation')
        return asset,review

    def test_initial_retry_uses_latest_labels(self):
        self.initial(False)
        task=self.service.get_task(self.task['id'])
        normal,defect=task['normal'][0],task['defect'][0]
        self.service.revise_initial_label(task['id'],normal,'defect','tester')
        self.service.revise_initial_label(task['id'],defect,'normal','tester')
        retry=wait(self.service,self.service.submit_initial_adaptation(task['id']))
        self.assertEqual(retry['status'],'succeeded',retry)
        manifest=json.loads((Path(retry['output_dir'])/'dataset_manifest.json').read_text(encoding='utf-8'))
        labels={a['asset_id']:a['role'] for a in manifest}
        self.assertEqual(labels[normal],'defect')
        self.assertEqual(labels[defect],'normal')
        self.assertEqual(retry['feedback_cutoff'],2)

    def test_corrupt_parent_blocks_publication(self):
        parent=self.initial()
        self.feedback()
        version=wait(self.service,self.service.submit_evolution(self.task['id']))['model_version']
        wait(self.service,self.service.submit_validation(version))
        model=self.service.store.get('model',parent)
        (Path(model['model_dir'])/'detector_arrays.npz').write_bytes(b'changed parent')
        with self.assertRaises(ValueError):
            self.service.versions.publish(self.task['id'],version,'tester',parent)
        self.assertEqual(self.service.get_task(self.task['id'])['published_model'],parent)

    def test_initial_counts_and_duplicate_validation(self):
        with self.assertRaises(ValueError):
            self.service.submit_initial_adaptation(self.task['id'])
        asset=self.asset(1)
        self.service.attach(self.task['id'],'normal',[asset])
        duplicate=self.asset(1)
        with self.assertRaises(ValueError):
            self.service.attach(self.task['id'],'defect',[duplicate])
        with self.assertRaises(ValueError):
            self.service.attach(self.task['id'],'validation_normal',[duplicate])

    def test_candidate_needs_validation_and_release(self):
        version=self.initial(False)
        with self.assertRaises(ValueError):
            self.service.inspect_images([self.asset()],version)
        with self.assertRaises(ValueError):
            self.service.versions.publish(self.task['id'],version,'tester')
        report=wait(self.service,self.service.submit_validation(version))
        self.assertTrue(report['comparison']['passed'])
        self.service.versions.publish(self.task['id'],version,'tester')
        self.assertEqual(self.service.get_task(self.task['id'])['published_model'],version)
        with self.assertRaises(ValueError):
            self.service.attach(self.task['id'],'normal',[self.asset()])
        with self.assertRaises(ValueError):
            self.service.attach(self.task['id'],'validation_normal',[self.asset()])

    def test_modes_share_detection_and_sync_but_machine_forbids_review(self):
        self.initial()
        asset=self.asset(label='defect')
        human=self.detect([asset],'review_test')
        machine=self.detect([asset],'machine_test')
        original=dict(self.service.store.get('inspection_record',human['results'][0]['record_id']))
        reviewed=self.service.review(original['id'],'normal','tester','corrected')
        self.assertTrue(reviewed['learning_eligible'])
        self.assertEqual(original,self.service.store.get('inspection_record',original['id']))
        with self.assertRaises(ValueError):
            self.service.review(machine['results'][0]['record_id'],'normal','tester')
        for job in (human,machine):
            row=job['results'][0]
            self.assertEqual(row['final_score'],1.23456789012345)
            self.assertEqual(row['threshold'],0.95)
            event=self.service.store.get('sync_event',row['cloud_event_id'])
            self.assertEqual(event['status'],'pending_configuration')
            self.assertEqual(event['payload']['record']['mode'],job['mode'])
        self.assertEqual(len(self.service.store.all('sync_event')),3)

    def test_cumulative_feedback_cutoff_and_formal_evolution(self):
        parent=self.initial()
        assets=[self.asset(),self.asset(label='defect')]
        rows=self.detect(assets)['results']
        by_asset={r['asset_id']:r for r in rows}
        first=self.service.review(by_asset[assets[0]]['record_id'],'normal','tester')
        evolution=self.service.submit_evolution(self.task['id'])
        self.service.review(by_asset[assets[1]]['record_id'],'defect','tester')
        first_job=wait(self.service,evolution)
        self.assertEqual(first_job['feedback_cutoff'],first['sequence'])
        self.assertEqual(sum(first_job['training_counts'].values()),131)
        self.assertEqual(first_job['parent_model'],parent)
        command=json.loads((Path(first_job['output_dir'])/'command.json').read_text(encoding='utf-8'))
        self.assertIn('--allow-nonstandard-counts',command)
        self.assertTrue(command[2].endswith('run_adapt.py'))
        report=wait(self.service,self.service.submit_validation(first_job['model_version']))
        self.assertTrue(report['comparison']['passed'])
        self.service.versions.publish(self.task['id'],first_job['model_version'],'tester',parent)
        second_job=wait(self.service,self.service.submit_evolution(self.task['id']))
        self.assertEqual(sum(second_job['training_counts'].values()),132)

    def test_original_label_corrections_keep_history(self):
        self.initial()
        original=self.service.get_task(self.task['id'])['normal'][0]
        self.service.revise_initial_label(self.task['id'],original,'defect','tester','correction')
        snapshot=self.service.training_snapshot(self.task['id'])
        self.assertEqual(snapshot['counts'],{'normal':99,'defect':31})
        self.service.revise_initial_label(self.task['id'],original,'normal','tester','second correction')
        self.assertEqual(self.service.training_snapshot(self.task['id'])['counts'],{'normal':100,'defect':30})
        self.assertEqual(len(self.service.store.all('label_revision')),2)
        events=[e for e in self.service.store.all('sync_event') if e['kind']=='support_label_revision']
        self.assertEqual(len(events),2)
        self.assertTrue(all(e['status']=='pending_configuration' for e in events))

    def test_feedback_duplicates_conflicts_and_uncertain(self):
        self.initial()
        a=self.asset(2300)
        b=self.asset(2300)
        rows=self.detect([a,b])['results']
        by_asset={r['asset_id']:r for r in rows}
        self.service.review(by_asset[a]['record_id'],'normal','tester')
        self.service.review(by_asset[b]['record_id'],'defect','tester')
        snapshot=self.service.training_snapshot(self.task['id'])
        self.assertEqual(sum(snapshot['counts'].values()),130)
        self.assertEqual(len(snapshot['excluded']),2)
        self.service.review(by_asset[b]['record_id'],'normal','tester')
        self.assertEqual(sum(self.service.training_snapshot(self.task['id'])['counts'].values()),131)
        self.service.review(by_asset[a]['record_id'],'uncertain','tester')
        self.service.review(by_asset[b]['record_id'],'uncertain','tester')
        self.assertEqual(sum(self.service.training_snapshot(self.task['id'])['counts'].values()),130)

    def test_original_conflict_requires_later_confirmation(self):
        self.initial()
        duplicate=self.asset(0)
        row=self.detect([duplicate])['results'][0]
        self.service.review(row['record_id'],'defect','tester')
        snapshot=self.service.training_snapshot(self.task['id'])
        self.assertEqual(sum(snapshot['counts'].values()),129)
        original=self.service.get_task(self.task['id'])['normal'][0]
        self.service.revise_initial_label(self.task['id'],original,'normal','tester','resolve conflict')
        self.assertEqual(sum(self.service.training_snapshot(self.task['id'])['counts'].values()),130)

    def test_validation_never_enters_feedback_training(self):
        self.initial()
        asset=self.service.get_task(self.task['id'])['validation_normal'][0]
        row=self.detect([asset])['results'][0]
        review=self.service.review(row['record_id'],'defect','tester','validation correction note')
        self.assertFalse(review['learning_eligible'])
        self.assertEqual(sum(self.service.training_snapshot(self.task['id'])['counts'].values()),130)
        with self.assertRaises(ValueError):
            self.service.submit_evolution(self.task['id'])

    def test_failed_regression_keeps_old_published_model(self):
        parent=self.initial()
        self.feedback()
        self.bad_next=True
        candidate=wait(self.service,self.service.submit_evolution(self.task['id']))['model_version']
        comparison=wait(self.service,self.service.submit_validation(candidate))
        self.assertFalse(comparison['comparison']['passed'])
        with self.assertRaises(ValueError):
            self.service.versions.publish(self.task['id'],candidate,'tester',parent)
        self.assertEqual(self.service.get_task(self.task['id'])['published_model'],parent)

    def test_stale_parent_and_rollback(self):
        parent=self.initial()
        self.feedback()
        first=wait(self.service,self.service.submit_evolution(self.task['id']))['model_version']
        second=wait(self.service,self.service.submit_evolution(self.task['id']))['model_version']
        for version in (first,second):
            self.assertTrue(wait(self.service,self.service.submit_validation(version))['comparison']['passed'])
        self.service.versions.publish(self.task['id'],first,'tester',parent)
        with self.assertRaises(ValueError):
            self.service.versions.publish(self.task['id'],second,'tester',first)
        with self.assertRaises(ValueError):
            self.service.versions.rollback(self.task['id'],second,'tester',first)
        self.service.versions.rollback(self.task['id'],parent,'tester',first)
        self.assertEqual(self.service.get_task(self.task['id'])['published_model'],parent)
        self.assertEqual(len(self.service.store.all('release')),3)

    def test_corrupt_validated_model_cannot_publish(self):
        version=self.initial(False)
        wait(self.service,self.service.submit_validation(version))
        model=self.service.store.get('model',version)
        (Path(model['model_dir'])/'detector_arrays.npz').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            self.service.versions.publish(self.task['id'],version,'tester')

    def test_failed_adaptation_does_not_register_model(self):
        self.dataset()
        self.fail_next=True
        result=wait(self.service,self.service.submit_initial_adaptation(self.task['id']))
        self.assertEqual(result['status'],'failed')
        self.assertEqual(self.service.store.all('model'),[])
        self.assertIn('fixture algorithm failure',result['error'])

    def test_inspection_model_is_pinned_at_submission(self):
        first=self.initial()
        self.feedback()
        candidate=wait(self.service,self.service.submit_evolution(self.task['id']))['model_version']
        wait(self.service,self.service.submit_validation(candidate))
        queued=self.service.inspect_images([self.asset()],task_id=self.task['id'],mode='machine_test')
        self.service.versions.publish(self.task['id'],candidate,'tester',first)
        result=wait(self.service,queued)
        self.assertEqual(result['model_version'],first)
        self.assertEqual(result['results'][0]['model_version'],first)

    def test_cloud_retry_idempotency_and_review_dependency(self):
        self.initial()
        asset,review=self.feedback()
        calls=[]
        def transport(request):
            calls.append(request)
            if len(calls)==1:
                return httpx.Response(503,json={'error':'fixture unavailable'})
            self.assertIn(b'PNG',request.read())
            return httpx.Response(200,json={'event_id':request.headers['Idempotency-Key'],'status':'accepted'})
        cloud=self.service.cloud
        cloud.config={'enabled':True,'endpoint':'http://cloud.test/events','max_attempts':2}
        cloud.transport=httpx.MockTransport(transport)
        record=self.service.store.get('inspection_record',review['record_id'])
        blocked=cloud.sync_one(review['cloud_event_id'],True)
        self.assertNotEqual(blocked['status'],'synced')
        self.assertEqual(len(calls),0)
        failed=cloud.sync_one(record['cloud_event_id'],True)
        self.assertEqual(failed['status'],'retry_wait')
        delivered=cloud.sync_one(record['cloud_event_id'],True)
        self.assertEqual(delivered['status'],'synced')
        self.assertEqual(calls[0].headers['Idempotency-Key'],calls[1].headers['Idempotency-Key'])
        self.assertEqual(cloud.sync_one(review['cloud_event_id'],True)['status'],'synced')
        count=len(calls)
        cloud.sync_one(record['cloud_event_id'],True)
        self.assertEqual(len(calls),count)

    def test_cloud_rejects_mismatched_ack(self):
        self.initial()
        _,review=self.feedback()
        record=self.service.store.get('inspection_record',review['record_id'])
        cloud=self.service.cloud
        cloud.config={'enabled':True,'endpoint':'http://cloud.test/events','max_attempts':1}
        cloud.transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'event_id':'wrong','status':'accepted'}))
        result=cloud.sync_one(record['cloud_event_id'])
        self.assertEqual(result['status'],'failed')

    def test_api_validates_mode_and_machine_review(self):
        self.initial()
        asset=self.asset()
        with TestClient(create_app(self.config,self.service)) as client:
            health=client.get('/health').json()
            self.assertEqual(health['version'],'0.3.2')
            self.assertEqual(health['algorithm_family'],'unknown')
            self.assertEqual(client.post('/inspections',json={'asset_ids':[asset],'task_id':self.task['id'],'mode':'bad'}).status_code,422)
            job=client.post('/inspections',json={'asset_ids':[asset],'task_id':self.task['id'],'mode':'machine_test'}).json()
            record=wait(self.service,job)['results'][0]
            self.assertEqual(client.post('/inspection-records/'+record['record_id']+'/reviews',json={'label':'normal','operator':'tester'}).status_code,400)
            self.assertEqual(client.get('/inspection-records?task_id='+self.task['id']).status_code,200)
            self.assertEqual(client.get('/jobs/missing').status_code,404)
            self.assertEqual(client.get('/tasks/'+self.task['id']+'/feedback').json()['effective_samples'],[])

    def test_restart_marks_unfinished_work_failed(self):
        job=self.service._job('adapt',self.task['id'])
        self.service.store.put('job',job)
        recovered=FixtureService(self.config,self.algorithm)
        try:
            self.assertEqual(recovered.get_status(job['id'])['status'],'failed')
        finally:
            recovered.close()

if __name__=='__main__':
    unittest.main()


