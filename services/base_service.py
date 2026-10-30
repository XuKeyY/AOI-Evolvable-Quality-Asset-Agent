"""Run original algorithms in isolated subprocesses; never rescale scores."""
import csv
import hashlib
import json
import math
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from services.asset_service import AssetService, sha256
from services.store import Store, identifier, now

MODEL_FILES = ('detector.json', 'detector_arrays.npz')
WEIGHT_SHA256 = '04d27f3400d059fc0cfd7d17dd1909a75bf3ea8fb3eeb48b97cb99e57ee20081'

class BaseAOIService:
    def __init__(self, config, runner=None):
        self.config = config
        self.store = Store(config['data_dir'])
        self.assets = AssetService(self.store)
        self.original = Path(config['original_dir']).resolve()
        self.python = Path(config['algorithm_python']).resolve()
        self.weights = Path(config['weights']).resolve()
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='aoi')
        self.runner = runner or self._run
        # Run ONE API worker. MCP is a client of the API, not a second task worker.
        for job in self.store.all('job'):
            if job['status'] in ('queued', 'running'):
                job.update(status='failed', stage='interrupted', error='Service restarted; submit a new job', finished_at=now())
                self.store.put('job', job)

    def close(self):
        self.pool.shutdown(wait=True)

    def create_task(self, name):
        task_id = identifier('task')
        return self.store.put('task', {'id': task_id, 'task_id': task_id, 'name': name, 'normal': [], 'defect': [], 'revision': 0, 'created_at': now()})

    def attach(self, task_id, role, asset_ids):
        if role not in ('normal', 'defect'):
            raise ValueError('role must be normal or defect')
        with self.lock:
            task = self.store.get('task', task_id)
            existing = [self.assets.resolve(i) for i in task['normal'] + task['defect']]
            seen = {a['pixel_sha256'] for a in existing}
            for asset_id in asset_ids:
                asset = self.assets.resolve(asset_id)
                if asset['pixel_sha256'] in seen:
                    raise ValueError('Duplicate image or conflicting label in support dataset')
                seen.add(asset['pixel_sha256'])
            if len(task[role]) + len(asset_ids) > (100 if role == 'normal' else 30):
                raise ValueError('Support dataset exceeds 100 normal / 30 defect limit')
            task[role].extend(asset_ids)
            task['revision'] += 1
            return self.store.put('task', task)

    def _check_environment(self):
        for path in (self.python, self.weights, self.original / 'code/run_adapt.py', self.original / 'code/run_infer.py'):
            if not path.is_file():
                raise ValueError(f'Required file missing: {path}')
        if sha256(self.weights) != WEIGHT_SHA256:
            raise ValueError('DINOv2 weights SHA256 does not match original project')

    def _job(self, kind, task_id=None):
        job_id = identifier('job')
        folder = self.store.root / 'jobs' / job_id
        folder.mkdir(parents=True)
        return {'id': job_id, 'job_id': job_id, 'task_id': task_id, 'kind': kind, 'status': 'queued', 'stage': 'queued', 'output_dir': str(folder), 'created_at': now(), 'error': None}

    def submit_adaptation(self, task_id, development=False):
        with self.lock:
            task = self.store.get('task', task_id)
            counts = (len(task['normal']), len(task['defect']))
            if development:
                if not self.config.get('allow_development', False):
                    raise ValueError('Development adaptation is disabled in config')
                if min(counts) < 3:
                    raise ValueError('Development requires at least 3 images of each class; success is not guaranteed')
            elif counts != (100, 30):
                raise ValueError(f'Exactly 100 normal + 30 defect required; got {counts}')
            if any(j['task_id'] == task_id and j['kind'] == 'adapt' and j['status'] in ('queued', 'running') for j in self.store.all('job')):
                raise ValueError('An adaptation for this task is already queued or running')
            self._check_environment()
            job = self._job('adapt', task_id)
            folder = Path(job['output_dir'])
            manifest = []
            for role in ('normal', 'defect'):
                target = folder / 'support' / role
                target.mkdir(parents=True)
                for asset_id in task[role]:
                    asset = self.assets.resolve(asset_id)
                    path = target / (asset_id + Path(asset['path']).suffix)
                    shutil.copy2(asset['path'], path)
                    manifest.append({'asset_id': asset_id, 'role': role, 'sha256': asset['sha256'], 'pixel_sha256': asset['pixel_sha256'], 'path': str(path)})
            dataset_hash = hashlib.sha256(json.dumps([{k: m[k] for k in ('asset_id', 'role', 'sha256')} for m in manifest], sort_keys=True).encode()).hexdigest()
            job.update(dataset_version='dataset_' + dataset_hash[:16], dataset_sha256=dataset_hash, dataset_revision=task['revision'], development=development, model_version=identifier('model'), weights_sha256=sha256(self.weights))
            (folder / 'dataset_manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
            self.store.put('job', job)
            self.pool.submit(self._execute, job['id'])
            return job

    def inspect_images(self, asset_ids, model_version):
        with self.lock:
            if not asset_ids or len(set(asset_ids)) != len(asset_ids):
                raise ValueError('Provide one or more unique asset IDs')
            model = self.store.get('model', model_version)
            for name in MODEL_FILES:
                path = Path(model['model_dir']) / name
                if not path.is_file() or sha256(path) != model['file_hashes'][name]:
                    raise ValueError('Model files missing or changed')
            assets = [self.assets.resolve(i) for i in asset_ids]
            manifest = json.loads((Path(model['adapt_output']) / 'dataset_manifest.json').read_text(encoding='utf-8'))
            support = {m['pixel_sha256'] for m in manifest}
            if any(a['pixel_sha256'] in support for a in assets):
                raise ValueError('Use independent test images, not support images')
            self._check_environment()
            job = self._job('inspect', model['task_id'])
            folder = Path(job['output_dir']) / 'inputs'
            folder.mkdir()
            mapping = {}
            for asset in assets:
                target = folder / (asset['id'] + Path(asset['path']).suffix)
                shutil.copy2(asset['path'], target)
                mapping[str(target.resolve()).casefold()] = asset['id']
            job.update(model_version=model_version, dataset_version=model['dataset_version'], asset_ids=asset_ids, input_mapping=mapping)
            self.store.put('job', job)
            self.pool.submit(self._execute, job['id'])
            return job

    def get_status(self, job_id):
        job = self.store.get('job', job_id)
        logs = sorted(Path(job['output_dir']).rglob('process.log'))
        chunks = []
        for log in logs[-8:]:
            with log.open('rb') as stream:
                stream.seek(max(0, log.stat().st_size - 3000))
                chunks.append(f'[{log.parent.name}]\n' + stream.read().decode('utf-8', errors='replace'))
        if chunks:
            job['log_tail'] = '\n'.join(chunks)[-12000:]
        return job

    def _run(self, command, folder):
        env = os.environ.copy()
        env.update(PYTHONUTF8='1', PYTHONUNBUFFERED='1', AOI_LOCAL_WEIGHTS=str(self.weights), AOI_DISABLE_MODEL_DOWNLOAD='1', AOI_FEATURE_BACKEND='torch', AOI_ORIGINAL_DIR=str(self.original))
        with (folder / 'process.log').open('wb') as log:
            process = subprocess.Popen(command, cwd=self.original, env=env, stdout=log, stderr=subprocess.STDOUT)
            try:
                code = process.wait(timeout=self.config.get('job_timeout_seconds', 43200))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise RuntimeError('Algorithm job timed out; see process.log')
        if code:
            unsigned = code & 0xffffffff
            if unsigned == 0xc0000005:
                raise RuntimeError('Algorithm native process crashed with Windows access violation (0xC0000005); reduce memory pressure or inference batch size; see process.log')
            raise RuntimeError(f'Algorithm exited with code {code}; see process.log')

    def _execute(self, job_id):
        job = self.store.get('job', job_id)
        folder = Path(job['output_dir'])
        try:
            job.update(status='running', stage='adaptation' if job['kind'] == 'adapt' else 'inference', started_at=now())
            self.store.put('job', job)
            if job['kind'] == 'adapt':
                model_dir = folder / 'support_model'
                command = [str(self.python), '-u', str(self.original / 'code/adapt_support.py'), '--normal-dir', str(folder / 'support/normal'), '--defect-dir', str(folder / 'support/defect'), '--output-dir', str(model_dir), '--weights', str(self.weights), '--feature-backend', 'torch', '--device', self.config.get('device', 'auto')]
                if job['development']:
                    command += ['--allow-nonstandard-counts', '--oof-folds', '2', '--oof-repeats', '1', '--adapter-epochs', '1']
            else:
                model = self.store.get('model', job['model_version'])
                command = [str(self.python), '-u', str(self.original / 'code/hidden_test_eval.py'), '--model-dir', model['model_dir'], '--test-dir', str(folder / 'inputs'), '--output', str(folder / 'predictions.csv'), '--weights', str(self.weights), '--feature-backend', 'torch', '--device', self.config.get('device', 'auto'), '--adapter-device', self.config.get('device', 'auto')]
            (folder / 'command.json').write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding='utf-8')
            self.runner(command, folder)
            if job['kind'] == 'adapt':
                if any(not (model_dir / name).is_file() for name in MODEL_FILES):
                    raise RuntimeError('Adaptation did not produce all required model files')
                summary = json.loads((model_dir / 'adaptation_summary.json').read_text(encoding='utf-8'))
                if summary.get('status') != 'ok':
                    raise RuntimeError('Adaptation summary is not successful')
                model = {'id': job['model_version'], 'model_version': job['model_version'], 'task_id': job['task_id'], 'dataset_version': job['dataset_version'], 'model_dir': str(model_dir), 'adapt_output': str(folder), 'file_hashes': {name: sha256(model_dir / name) for name in MODEL_FILES}, 'development': job['development'], 'status': 'candidate', 'created_at': now()}
                self.store.put('model', model)
                job['summary'] = summary
            else:
                results = self.csv_to_results(folder / 'predictions.csv', job)
                (folder / 'results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
                job['results'] = results
            job.update(status='succeeded', stage='completed', finished_at=now())
        except Exception as exc:
            job.update(status='failed', stage='failed', error=str(exc), finished_at=now())
        self.store.put('job', job)

    @staticmethod
    def csv_to_results(path, job):
        with Path(path).open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.DictReader(stream))
        results, seen = [], set()
        for row in rows:
            image_value = row.get('path') or row.get('image')
            key = str(Path(image_value).resolve()).casefold()
            asset_id = job['input_mapping'].get(key)
            score = float(row.get('score', row.get('final_score')))
            threshold = float(row['threshold'])
            result = row.get('label') or row.get('result')
            expected = 'NG' if score >= threshold else 'OK'
            if asset_id is None or asset_id in seen:
                raise ValueError('Unexpected or duplicate image in algorithm CSV')
            if not math.isfinite(score) or not math.isfinite(threshold):
                raise ValueError('Algorithm returned a non-finite score')
            if result != expected:
                raise ValueError('CSV verdict disagrees with algorithm threshold rule')
            seen.add(asset_id)
            results.append({'inspection_id':job['id'],'asset_id':asset_id,'result':result,
                'final_score':score,'threshold':threshold,'model_version':job['model_version'],
                'evidence_asset_ids':[],'selected_method':row.get('selected_method','DINO_GLOBAL_LINEAR')})
        if seen != set(job['asset_ids']):
            raise ValueError('Algorithm CSV does not cover every requested asset')
        return results
