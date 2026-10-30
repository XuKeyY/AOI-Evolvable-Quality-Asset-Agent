"""Check original environment and weight integrity, without modifying source."""
import json
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from services.asset_service import sha256
from services.aoi_service import WEIGHT_SHA256

config = json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
original = Path(config['original_dir'])
weights = Path(config['weights'])
digest = sha256(weights)
if digest != WEIGHT_SHA256:
    raise SystemExit('Weight SHA256 mismatch')
report = {'weights_sha256':digest,'weight_integrity':'passed','original_dir':str(original),
    'source_hashes':{str(path.relative_to(original)):sha256(path) for path in sorted(original.rglob('*')) if path.is_file() and path.suffix in {'.py','.sh','.md','.json','.txt'} and '.venv' not in path.parts and 'weights' not in path.parts},
    'algorithm_family':'AOI_100plus30_detector_v1','real_adaptation':'pending real samples','real_inspection':'pending adapted model'}
for args in (['-m','pip','check'],[str(original/'code/run_adapt.py'),'--help'],[str(original/'code/run_infer.py'),'--help']):
    run = subprocess.run([config['algorithm_python'],*args],capture_output=True,text=True,encoding='utf-8',errors='replace')
    if run.returncode:
        raise SystemExit(run.stdout + run.stderr)
report['algorithm_entrypoints'] = 'passed'
smoke_code = "import sys,torch,timm,json; torch.set_num_threads(4); m=timm.create_model('vit_small_patch14_dinov2.lvd142m',pretrained=False,num_classes=0,checkpoint_path=sys.argv[1]).eval(); x=torch.zeros(1,3,518,518); y=m.forward_features(x); print(json.dumps({'tokens':list(y.shape),'cuda_available':torch.cuda.is_available()}))"
smoke = subprocess.run([config['algorithm_python'],'-c',smoke_code,str(weights)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
if smoke.returncode:
    raise SystemExit(smoke.stdout+smoke.stderr)
report['synthetic_weight_forward'] = json.loads(smoke.stdout.strip().splitlines()[-1])
(ROOT/'data').mkdir(exist_ok=True)
(ROOT/'data/source_baseline.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in report.items() if k!='source_hashes'},ensure_ascii=False,indent=2))
