"""Exercise the real script's input-count failures before model extraction."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT/'config.json').read_text(encoding='utf-8-sig'))
folder = ROOT/'data/count_validation'
for role in ('normal','defect'):
    (folder/role).mkdir(parents=True,exist_ok=True)
results=[]
for development in (False,True):
    cmd=[config['algorithm_python'],'-X','utf8','-B',str(Path(config['original_dir'])/'code/run_adapt.py'),
        '--normal-dir',str(folder/'normal'),'--defect-dir',str(folder/'defect'),
        '--output-dir',str(folder/('development' if development else 'official'))]
    if development:
        cmd.append('--allow-nonstandard-counts')
    run=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',timeout=60)
    error=run.stdout+run.stderr
    expected='at least two normal and two defect' if development else 'exactly 100 normal + 30 defect'
    if run.returncode==0 or expected not in error:
        raise RuntimeError('Unexpected count-validation behaviour: '+error)
    results.append({'development':development,'status':'expected_failure','exit_code':run.returncode,'error':error.strip()})
(folder/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(results,ensure_ascii=False,indent=2))
