import csv, json, hashlib
from pathlib import Path
from collections import Counter
from PIL import Image, ImageOps

root=Path('D:/competition/aoi-agent/data/experiments/mvtec_screw')
with (root/'split_manifest.csv').open(encoding='utf-8-sig',newline='') as f:rows=list(csv.DictReader(f))
raw=set();pixels=set();sources=set();destinations=set()
for row in rows:
    source=Path(row['original_path']);dest=Path(row['experiment_path'])
    assert dest.resolve().is_relative_to(root.resolve())
    assert source.resolve().is_relative_to(Path('E:/datasets/mvtec_ad/screw').resolve())
    assert 'ground_truth' not in source.parts
    assert source not in sources and dest not in destinations
    sources.add(source);destinations.add(dest)
    for path in (source,dest):
        with path.open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==row['sha256']
    with Image.open(dest) as image:
        rgb=ImageOps.exif_transpose(image).convert('RGB')
        assert rgb.size==(int(row['width']),int(row['height']))
        pixel=hashlib.sha256(str(rgb.size).encode()+rgb.tobytes()).hexdigest()
    assert pixel==row['pixel_sha256']
    assert pixel not in pixels and row['sha256'] not in raw
    pixels.add(pixel);raw.add(row['sha256'])
    assert row['true_label']==('normal' if row['defect_type']=='good' else 'defect')
    if row['split']=='validation' and row['true_label']=='normal':assert row['source_partition']=='test'
    if row['split'] in ('initial','feedback_round_01','feedback_round_02') and row['true_label']=='normal':assert row['source_partition']=='train'
assert set(root.glob('*/*/*.png'))==destinations
summary=json.loads((root/'split_summary.json').read_text(encoding='utf-8'))
with (root/'split_manifest.csv').open('rb') as f:assert hashlib.file_digest(f,'sha256').hexdigest()==summary['manifest_sha256']
assert len(rows)==summary['total_original_images']
counts={split:{label:sum(r['split']==split and r['true_label']==label for r in rows) for label in ('normal','defect')} for split in summary['split_counts']}
assert counts==summary['split_counts']
print(json.dumps({'verified':True,'images':len(rows),'split_counts':counts,'distinct_raw_hashes':len(raw),'distinct_pixel_hashes':len(pixels),'image_sizes':summary['image_sizes'],'final_normal_sources':summary['final_normal_sources']},ensure_ascii=False,indent=2))
