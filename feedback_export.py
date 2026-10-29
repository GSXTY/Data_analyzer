"""Local, content-addressed feedback exports with explicit partial-review scope."""
import hashlib,json,math,os,threading,uuid
from pathlib import Path
from event_store import TYPES
from app_version import APP_VERSION

export_lock=threading.Lock()

def encode(packet):
    return json.dumps(packet,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False).encode('utf-8')

def scoped_review(meta,automatic,current,reviewer,reviewed_range,reviewed_types,review_mode):
    if not isinstance(reviewer,str) or not reviewer.strip() or len(reviewer)>100:raise ValueError('请填写1～100字校验人')
    if not isinstance(reviewed_range,list) or len(reviewed_range)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in reviewed_range):raise ValueError('请填写已检查的起止秒数')
    a,b=reviewed_range
    if not 0<=a<b<=meta['raw_duration'] or not automatic['scan_start']<=a<b<=automatic['scan_end']:raise ValueError('校验范围必须位于当前文件和所选扫描批次范围内')
    if not isinstance(reviewed_types,list) or not reviewed_types or any(not isinstance(t,str) or t not in TYPES for t in reviewed_types):raise ValueError('请至少勾选一种实际检查过的事件类型')
    if review_mode not in ('spot','complete'):raise ValueError('请指定逐条复核或完整检查')
    types=sorted(set(reviewed_types))
    relevant=lambda e:e['type'] in types and e['start']<b and e['end']>a
    inside=lambda e:e['start']>=a and e['end']<=b
    selected=[e for e in current['events'] if relevant(e)]
    # Estimated engineering coordinates cannot establish raw-second coverage.
    wrong_axis=[e for e in selected if e['mode']!='raw']
    selected=[e for e in selected if e['mode']=='raw']
    events=[e for e in selected if inside(e)]
    boundary=[e for e in selected if not inside(e)]
    originals=[e for e in automatic['events'] if relevant(e)]
    links=automatic.get('event_links',{})
    known={e['id']:e for e in selected}
    unresolved=[e['id'] for e in originals if links.get(e['id'],e['id']) not in known]
    pending=[e['id'] for e in selected if e['status']=='candidate']
    if review_mode=='complete' and (pending or unresolved or wrong_axis):raise ValueError(f'不能声明完整检查：有{len(pending)}条待复核、{len(unresolved)}条自动事件缺少对应结论、{len(wrong_axis)}条估算时间标记。请先处理，或选择逐条复核。')
    excerpt=dict(file_id=meta['file_id'],run_id=automatic['run_id'],events=[e for e in originals if inside(e)],
                 event_links={e['id']:links[e['id']] for e in originals if e['id'] in links},
                 settings=automatic['settings'],excerpt=True,reviewed_range=[a,b])
    return dict(version=2,kind='reviewed',file_id=meta['file_id'],run_id=automatic['run_id'],app_version=APP_VERSION,
                recording=automatic['recording'],reviewer=reviewer.strip(),reviewed_range=[a,b],reviewed_types=types,review_mode=review_mode,
                events=sorted(events,key=lambda e:(e['start'],e['id'])),boundary_events=sorted(boundary,key=lambda e:(e['start'],e['id'])),
                automatic=excerpt,unresolved_original_ids=sorted(unresolved),estimated_time_event_ids=sorted(e['id'] for e in wrong_axis),estimated_time_events=wrong_axis,
                training_scope=dict(time_mode='raw',interval='[start,end)',positive_event_ids=sorted(e['id'] for e in events if e['status']=='reviewed'),
                    explicit_rejected_candidate_ids=sorted(e['id'] for e in events if e['status']=='ignored'),
                    pending_event_ids=sorted(pending),background_labels_allowed=review_mode=='complete',
                    excluded_boundary_intervals=[[max(a,e['start']),min(b,e['end'])] for e in boundary],
                    outside_scope='unknown',unreviewed_event_types='unknown',
                    note='仅按所选类型解释标签；拒绝候选不等于所有信号正常。逐条复核不能把其余空白区间当作正常。边界事件不裁剪为训练正样本。'),
                note='仅包含所选校验范围和类型的原始秒级标记；automatic为范围内摘录，完整自动快照单独保存。未复核候选、未检查区间和其他类型均不是正常标签。')

def folder_for(root,meta):
    # Identity is content-based even if the same CSV is renamed or reopened.
    return Path(root)/('recording_'+meta['file_id'][:32])

def _write_once(path,content):
    if path.exists():
        if hashlib.sha256(path.read_bytes()).digest()!=hashlib.sha256(content).digest():raise ValueError('已存在的导出文件内容不同，已停止，未覆盖：'+str(path))
        return False
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as f:f.write(content)
        os.replace(temp,path)
    finally:
        if temp.exists():temp.unlink()
    return True

def save_package(root,meta,raw,automatic,reviewed,confirmed=False):
    if confirmed is not True:raise ValueError('请先确认导出内容和保存位置')
    if hashlib.sha256(raw).hexdigest()!=meta['file_id']:raise ValueError('CSV原始字节与内容指纹不匹配')
    folder=folder_for(root,meta)
    auto_bytes=encode(automatic);auto_hash=hashlib.sha256(auto_bytes).hexdigest()
    review_bytes=encode(reviewed);review_hash=hashlib.sha256(review_bytes).hexdigest()
    files={'csv':'original.csv','automatic':'automatic/'+automatic['run_id']+'.automatic.json',
           'reviewed':'reviewed/'+review_hash[:32]+'.reviewed.json'}
    manifest=dict(version=1,file_id=meta['file_id'],original_name=automatic['recording']['name'],run_id=automatic['run_id'],
                  review_id=review_hash,files=files,sha256=dict(csv=meta['file_id'],automatic=auto_hash,reviewed=review_hash),
                  reviewed_range=reviewed['reviewed_range'],reviewed_types=reviewed['reviewed_types'],review_mode=reviewed['review_mode'],
                  note='训练导入以CSV内容指纹去重；校验JSON为版本快照，不可直接把重复或重叠区间当作独立训练样本。跨版本矛盾需人工裁定。')
    payloads={files['csv']:raw,files['automatic']:auto_bytes,files['reviewed']:review_bytes,'manifests/'+review_hash[:32]+'.json':encode(manifest)}
    created=[];reused=[]
    with export_lock:
        for relative,content in payloads.items():
            (created if _write_once(folder/relative,content) else reused).append(relative)
    return dict(folder=str(folder.resolve()),created=created,reused=reused,files=files,review_id=review_hash,
                note='同一CSV仅保存一次；同批自动快照和相同校验内容复用。负责人已有对应CSV和自动快照时，可只回传新增校验JSON及清单；新批次自动JSON也需补交。')
