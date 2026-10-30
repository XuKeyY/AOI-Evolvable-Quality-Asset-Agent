import io, json, struct, zipfile, httpx, pathlib, hashlib, time, random, csv, shutil
from collections import Counter
from PIL import Image, ImageOps

URL = 'https://huggingface.co/datasets/hdtech/mvtech_anomaly_detection/resolve/bf2a78feed682be5eec6ef804985d2458a2297c1/mvtech_anomaly_detection.zip'
ROOT = pathlib.Path('E:/datasets/mvtec_ad')
OUT = pathlib.Path('D:/competition/aoi-agent/data/experiments/mvtec_screw')
SEED = 20260918
SIZE = 5268276059

class RemoteZip(io.RawIOBase):
    def __init__(self):
        self.pos = 0
        self.client = httpx.Client(follow_redirects=True, timeout=120)
        self.cache = []
    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else SIZE + offset
        return self.pos
    def fetch(self, start, end):
        for lo, data in self.cache:
            if lo <= start and end <= lo + len(data): return data[start-lo:end-lo]
        for attempt in range(4):
            try:
                request_url = URL + '?download=true&segment=' + str(start) + '_' + str(end)
                with self.client.stream('GET', request_url, headers={'Range':f'bytes={start}-{end-1}'}) as r:
                    r.raise_for_status()
                    expected = f'bytes {start}-{end-1}/{SIZE}'
                    if r.status_code != 206 or r.headers.get('content-range') != expected:
                        raise RuntimeError(f'Unexpected range response {r.status_code}: {r.headers.get("content-range")}')
                    chunks=[]; count=0; last=time.monotonic()
                    for chunk in r.iter_bytes(1024*1024):
                        chunks.append(chunk); count+=len(chunk)
                        if time.monotonic()-last>15:
                            print(f'Download {count/1048576:.1f}/{(end-start)/1048576:.1f} MiB',flush=True);last=time.monotonic()
                    data=b''.join(chunks)
                if len(data)!=end-start: raise RuntimeError('Truncated download')
                self.cache.append((start,data)); return data
            except Exception as exc:
                print(f'Range retry {attempt+1}: {exc}',flush=True)
                if attempt==3: raise
                time.sleep(2)
    def read(self, n=-1):
        end = SIZE if n < 0 else min(SIZE,self.pos+n)
        if self.pos>=end:return b''
        data=self.fetch(self.pos,end); self.pos=end;return data

def digest(path):
    with path.open('rb') as f: return hashlib.file_digest(f,'sha256').hexdigest()

def main():
    remote=RemoteZip()
    z=zipfile.ZipFile(remote)
    members=[m for m in z.infolist() if 'screw' in pathlib.PurePosixPath(m.filename).parts and not m.is_dir()]
    print('Screw archive members',len(members),'example',[m.filename for m in members[:5]],flush=True)
    if not members:raise RuntimeError('No screw directory')
    segments=[]
    for m in sorted(members,key=lambda x:x.header_offset):
        lo=m.header_offset; hi=lo+30+len(m.filename.encode('utf-8'))+len(m.extra)+m.compress_size+128
        hi=min(hi,SIZE)
        if segments and lo-segments[-1][1]<1024*1024:segments[-1][1]=max(segments[-1][1],hi)
        else:segments.append([lo,hi])
    print('Download segments',segments,'total MiB',sum(b-a for a,b in segments)/1048576,flush=True)
    if '--plan' in __import__('sys').argv:return
    downloads=ROOT/'downloads'; downloads.mkdir(parents=True,exist_ok=True)
    provenance={'origin':'https://www.mvtec.com/research-teaching/datasets/mvtec-ad','mirror':URL,'mirror_revision':'bf2a78feed682be5eec6ef804985d2458a2297c1','license':'CC-BY-NC-SA-4.0','full_zip_size':SIZE,'full_zip_expected_sha256':'87408b1aa72cc1c99d490315a7629fe9634ffca8524207f866bed07a7fa1d42f','note':'Only screw ZIP ranges downloaded; full ZIP SHA256 not verified. Every extracted entry verified by ZIP CRC32; retained segments individually SHA256 hashed.','segments':[]}
    for i,(lo,hi) in enumerate(segments):
        target=downloads/f'screw_zip_segment_{i:02d}.bin'
        if target.exists() and target.stat().st_size==hi-lo:
            data=target.read_bytes();remote.cache.append((lo,data))
        else:
            data=remote.fetch(lo,hi)
            with target.open('xb') as f:f.write(data)
        provenance['segments'].append({'file':str(target),'start':lo,'end_exclusive':hi,'sha256':hashlib.sha256(data).hexdigest()})
    for m in members:
        parts=pathlib.PurePosixPath(m.filename).parts
        parts=parts[parts.index('screw'):]
        if any(p in ('..','.') or ':' in p or '\\' in p for p in parts):raise RuntimeError('Unsafe ZIP member')
        path=ROOT.joinpath(*parts)
        if not path.resolve().is_relative_to(ROOT.resolve()):raise RuntimeError('Path traversal')
        data=z.read(m)
        if path.exists():
            if digest(path)!=hashlib.sha256(data).hexdigest():raise RuntimeError('Existing source differs '+str(path))
        else:
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    (downloads/'screw_download_provenance.json').write_text(json.dumps(provenance,indent=2),encoding='utf-8')
    category=ROOT/'screw'; records=[]; raw_seen={}; pixels_seen={}
    for path in sorted(category.glob('*/*/*.png')):
        rel=path.relative_to(category)
        if rel.parts[0] not in ('train','test'):continue
        with Image.open(path) as image:
            image.load();rgb=ImageOps.exif_transpose(image).convert('RGB')
            pixel=hashlib.sha256(str(rgb.size).encode()+rgb.tobytes()).hexdigest();width,height=rgb.size
        sha=digest(path)
        if sha in raw_seen or pixel in pixels_seen:raise RuntimeError('Duplicate source image: '+str(path))
        raw_seen[sha]=str(path);pixels_seen[pixel]=str(path)
        records.append({'original_path':str(path),'source_partition':rel.parts[0],'true_label':'normal' if rel.parts[1]=='good' else 'defect','defect_type':rel.parts[1],'sha256':sha,'pixel_sha256':pixel,'width':width,'height':height,'seed':SEED})
    print('Source counts',dict(Counter((r['source_partition'],r['defect_type']) for r in records)),flush=True)
    rng=random.Random(SEED); train=[r for r in records if r['source_partition']=='train']; good=[r for r in records if r['source_partition']=='test' and r['true_label']=='normal']
    if len(train)<140 or len(good)<20:raise RuntimeError('Insufficient normal images')
    rng.shuffle(train);rng.shuffle(good); assigned=[]
    def assign(rows,group):
        for r in rows:assigned.append(dict(r,split=group))
    assign(train[:100],'initial');assign(train[100:120],'feedback_round_01');assign(train[120:140],'feedback_round_02');assign(good[:20],'validation');assign(train[140:]+good[20:],'final_test')
    types=sorted({r['defect_type'] for r in records if r['true_label']=='defect'})
    if len(types)!=5:raise RuntimeError('Expected five screw defect types; allocation needs review')
    for defect_type in types:
        rows=[r for r in records if r['true_label']=='defect' and r['defect_type']==defect_type];rng.shuffle(rows)
        if len(rows)<17:raise RuntimeError('Insufficient defect type '+defect_type)
        assign(rows[:6],'initial');assign(rows[6:12],'validation');assign(rows[12:14],'feedback_round_01');assign(rows[14:16],'feedback_round_02');assign(rows[16:],'final_test')
    if len(assigned)!=len(records):raise RuntimeError('Incomplete allocation')
    if OUT.exists() and any(OUT.iterdir()):raise RuntimeError('Experiment folder is not empty; refusing to overwrite')
    OUT.mkdir(parents=True,exist_ok=True)
    for r in assigned:
        src=pathlib.Path(r['original_path']);name=f"{r['source_partition']}__{r['defect_type']}__{src.name}"
        dst=OUT/r['split']/r['true_label']/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        if digest(dst)!=r['sha256']:raise RuntimeError('Copy mismatch')
        r['experiment_path']=str(dst)
    assigned.sort(key=lambda r:(r['split'],r['true_label'],r['original_path']))
    with (OUT/'split_manifest.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(assigned[0]));writer.writeheader();writer.writerows(assigned)
    counts={s:{label:sum(r['split']==s and r['true_label']==label for r in assigned) for label in ('normal','defect')} for s in ('initial','validation','feedback_round_01','feedback_round_02','final_test')}
    expected={'initial':{'normal':100,'defect':30},'validation':{'normal':20,'defect':30},'feedback_round_01':{'normal':20,'defect':10},'feedback_round_02':{'normal':20,'defect':10}}
    for s,v in expected.items():
        if counts[s]!=v:raise RuntimeError('Unexpected split counts')
    summary={'seed':SEED,'category':'screw','total_original_images':len(records),'source_counts':dict(Counter(r['source_partition']+'/'+r['defect_type'] for r in records)),'split_counts':counts,'defect_type_counts_by_split':{s:dict(Counter(r['defect_type'] for r in assigned if r['split']==s and r['true_label']=='defect')) for s in counts},'image_sizes':dict(Counter(f"{r['width']}x{r['height']}" for r in records)),'integrity':{'distinct_raw_sha256':len(raw_seen),'distinct_pixel_sha256':len(pixels_seen),'cross_split_duplicates':0,'copied_sha256_verified':True,'ground_truth_used_as_input':False},'final_normal_sources':dict(Counter(r['source_partition'] for r in assigned if r['split']=='final_test' and r['true_label']=='normal')),'manifest_sha256':digest(OUT/'split_manifest.csv'),'protocol':'Custom supervised few-shot adaptation and cumulative feedback experiment; not the official unsupervised MVTec AD benchmark. Final test includes remaining train/good and test/good; report them separately if measuring generalization.','download':provenance}
    (OUT/'split_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    (OUT/'README.md').write_text('# MVTec AD screw 实验分组\n\n随机种子：20260918。原始图片保持不变，实验目录内保存副本。\n\n'+ '\n'.join(f"- {s}: 正常 {v['normal']}，缺陷 {v['defect']}" for s,v in counts.items())+'\n\n缺陷分层：每种缺陷初始 6 张、验证 6 张、每轮反馈各 2 张，其余用于最终测试。正常初始及反馈来自 train/good；验证正常来自 test/good。最终正常包括未参与适配的剩余 train/good 和 test/good，清单标记 source_partition，可分别报告。\n\n验证集与最终测试集不参与训练。反馈图片需先检测，再经人工确认后进入累计反馈。两轮训练累计规模分别为 120+40、140+50（全部反馈有效时）。\n\n这是使用官方 test 缺陷参与训练的自定义少样本与反馈实验，不能称为官方标准无监督评测。ground_truth 是掩码，保留在原始数据目录，不上传为检测图片。\n\n来源：https://www.mvtec.com/research-teaching/datasets/mvtec-ad\n镜像：'+URL+'\n许可：CC BY-NC-SA 4.0，保留署名，限非商业使用。\n\n清单 split_manifest.csv 包含来源、分组、标签、缺陷类型、原文件和像素哈希。split_summary.json 保存数量、完整性检查及下载来源。\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()
