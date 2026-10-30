"""Run real adaptation, service inference and direct inference on identical inputs."""
import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.aoi_service import AOIService
from services.asset_service import IMAGE_EXTS
from services.store import identifier

def images(folder):
    return sorted(p for p in Path(folder).rglob('*') if p.is_file() and p.suffix.lower() in IMAGE_EXTS)

def wait(service,job):
    while True:
        result = service.get_status(job['id'])
        if result['status']=='succeeded':
            return result
        if result['status']=='failed':
            raise RuntimeError(json.dumps(result,ensure_ascii=False,indent=2))
        time.sleep(3)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--normal-dir',required=True)
    parser.add_argument('--defect-dir',required=True)
    parser.add_argument('--test-dir',required=True)
    parser.add_argument('--development',action='store_true')
    args=parser.parse_args()
    config=json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
    config['allow_development']=args.development
    # Isolated validation storage: do not recover or interrupt the running API's jobs.
    config['data_dir']=str(ROOT/'data/validation'/identifier('run'))
    service=AOIService(config)
    try:
        task=service.create_task('real-validation')
        for role,folder in [('normal',args.normal_dir),('defect',args.defect_dir)]:
            ids=[]
            for path in images(folder):
                with path.open('rb') as stream:
                    ids.append(service.assets.register(stream,path.name)['id'])
            service.attach(task['id'],role,ids)
        adaptation=wait(service,service.submit_adaptation(task['id'],args.development))
        ids=[]
        for path in images(args.test_dir):
            with path.open('rb') as stream:
                ids.append(service.assets.register(stream,path.name)['id'])
        inspection=wait(service,service.inspect_candidate_images(ids,adaptation['model_version']))
        folder=Path(inspection['output_dir'])
        command=json.loads((folder/'command.json').read_text(encoding='utf-8'))
        direct=folder/'direct'
        direct.mkdir()
        command[command.index('--csv')+1]=str(direct/'predictions.csv')
        service._run(command,direct)
        original=service.csv_to_results(direct/'predictions.csv',inspection)
        by_asset={r['asset_id']:r for r in original}
        for result in inspection['results']:
            other=by_asset[result['asset_id']]
            for field in ['result','model_version','selected_method']:
                if result[field]!=other[field]:
                    raise RuntimeError('Result mismatch: '+field)
            for field in ['final_score','threshold']:
                if not math.isclose(result[field],other[field],rel_tol=1e-8,abs_tol=1e-8):
                    raise RuntimeError('Numeric mismatch: '+field)
        report={'status':'passed','development':args.development,'task_id':task['id'],'model_version':adaptation['model_version'],
            'dataset_version':adaptation['dataset_version'],'images':len(ids),'numeric_tolerance':1e-8,
            'service_csv':str(folder/'predictions.csv'),'direct_csv':str(direct/'predictions.csv'),
            'note':'Pipeline consistency verified; detection accuracy requires human-labelled independent cases.'}
        (folder/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        service.close()

if __name__=='__main__':
    main()

