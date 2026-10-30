import math
from services.store import identifier, now

DEFAULT_GATE={'min_per_class':5,'min_balanced_accuracy':0.80,'max_false_positive_rate':0.20,'max_false_negative_rate':0.20,
    'allowed_fpr_increase':0.0,'allowed_fnr_increase':0.0,'allowed_balanced_accuracy_drop':0.002}

def metrics(results, labels):
    by_asset={r['asset_id']:r for r in results}
    if set(by_asset)!=set(labels) or len(results)!=len(labels):
        raise ValueError('Validation results do not cover the fixed labelled dataset')
    normal=sum(label=='normal' for label in labels.values())
    defect=len(labels)-normal
    if not normal or not defect:
        raise ValueError('Independent validation needs both normal and defect images')
    fp=sum(labels[asset]=='normal' and by_asset[asset]['result']=='NG' for asset in labels)
    fn=sum(labels[asset]=='defect' and by_asset[asset]['result']=='OK' for asset in labels)
    return {'normal':normal,'defect':defect,'false_positives':fp,'false_negatives':fn,
        'false_positive_rate':fp/normal,'false_negative_rate':fn/defect,'accuracy':1-(fp+fn)/len(labels),'recall':1-fn/defect,'balanced_accuracy':1-0.5*(fp/normal+fn/defect)}

def gate(candidate, baseline, policy):
    minimum=int(policy['min_per_class'])
    if minimum<1:
        raise ValueError('Regression gate min_per_class must be positive')
    for key in ('min_balanced_accuracy','max_false_positive_rate','max_false_negative_rate','allowed_fpr_increase','allowed_fnr_increase','allowed_balanced_accuracy_drop'):
        if not math.isfinite(float(policy[key])) or not 0<=float(policy[key])<=1:
            raise ValueError('Invalid regression gate policy')
    failures=[]
    if min(candidate['normal'],candidate['defect'])<minimum:
        failures.append('insufficient independent validation samples')
    if candidate['balanced_accuracy']+1e-12<float(policy['min_balanced_accuracy']):
        failures.append('balanced_accuracy below absolute gate')
    for metric,absolute,relative in (
        ('false_positive_rate','max_false_positive_rate','allowed_fpr_increase'),
        ('false_negative_rate','max_false_negative_rate','allowed_fnr_increase')):
        if candidate[metric]>float(policy[absolute])+1e-12:
            failures.append(metric+' exceeds absolute gate')
        if baseline and candidate[metric]>baseline[metric]+float(policy[relative])+1e-12:
            failures.append(metric+' regressed against parent')
    if baseline and candidate['balanced_accuracy'] < baseline['balanced_accuracy']-policy['allowed_balanced_accuracy_drop']-1e-12:
        failures.append('balanced_accuracy regressed against parent')
    return {'passed':not failures,'failures':failures}

class VersionService:
    def __init__(self,aoi):
        self.aoi=aoi
        self.store=aoi.store
        self.lock=aoi.lock

    def publish(self,task_id,model_version,operator,expected_current=None):
        with self.lock:
            if not operator.strip():
                raise ValueError('Operator is required')
            task=self.aoi.get_task(task_id)
            current=task.get('published_model')
            if expected_current!=current:
                raise ValueError('Published model changed; refresh before publishing')
            model=self.aoi.check_model(model_version)
            if model['task_id']!=task_id or model.get('development'):
                raise ValueError('Only formal models belonging to this task can be published')
            if model.get('parent_model')!=current:
                raise ValueError('Candidate parent is stale; evolve from current published model')
            if not model.get('validation_job'):
                raise ValueError('Validate the candidate before publication')
            job=self.store.get('job',model['validation_job'])
            report=job.get('comparison')
            if job['status']!='succeeded' or not report or not report['passed']:
                raise ValueError('Candidate has not passed independent regression validation')
            if report['validation_version']!=task.get('validation_version') or report['baseline_model']!=current:
                raise ValueError('Validation dataset or baseline no longer matches')
            if report['policy']!=self.aoi.gate_policy():
                raise ValueError('Regression policy changed; validate again')
            if report['model_hashes']!=model['file_hashes']:
                raise ValueError('Validated model files changed')
            release={'id':identifier('release'),'task_id':task_id,'action':'publish','from_model':current,
                'to_model':model_version,'validation_job':job['id'],'operator':operator,'created_at':now()}
            task['published_model']=model_version
            model['status']='released'
            if current:
                old=self.aoi.check_model(current)
                old['status']='retired'
                entries=[('model',old)]
            else:
                entries=[]
            self.store.put_many(entries+[('model',model),('task',task),('release',release)])
            return release

    def rollback(self,task_id,model_version,operator,expected_current):
        with self.lock:
            task=self.aoi.get_task(task_id)
            current=task.get('published_model')
            if current!=expected_current or current==model_version:
                raise ValueError('Rollback current model mismatch')
            target=self.aoi.check_model(model_version)
            if target['task_id']!=task_id or not any(r['task_id']==task_id and r['to_model']==model_version for r in self.store.all('release')):
                raise ValueError('Rollback target must have been released for this task')
            release={'id':identifier('release'),'task_id':task_id,'action':'rollback','from_model':current,
                'to_model':model_version,'operator':operator,'created_at':now()}
            task['published_model']=model_version
            target['status']='released'
            old=self.store.get('model',current)
            old['status']='retired'
            self.store.put_many([('model',target),('model',old),('task',task),('release',release)])
            return release
