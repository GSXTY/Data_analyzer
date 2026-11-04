"""Shared, revision-checked local storage for browser and MCP annotations."""
import json
import math
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
import uuid
import hashlib
from app_version import APP_VERSION

TYPES = ('疑似暂停', '疑似低通气', '漏气', '波形异常', '伪迹/干扰', '其他', '呼吸波动减弱', '总漏气升高', '数据缺失', '信号异常', '流量波动近消失候选', '流量波动减弱候选', '公司暂停候选', '公司低通气候选','测试近消失候选','测试减弱候选')
TYPES += ('强度显著下降候选','强度减弱候选')


def scan_fingerprint(start,end,settings):
    profile=settings.get('profile','engineering')
    defaults=['流量波动近消失候选','流量波动减弱候选','总漏气升高','数据缺失','信号异常'] if profile=='device_candidates' else ['呼吸波动减弱','总漏气升高','数据缺失','信号异常']
    key=[float(start),float(end),settings.get('version'),profile,float(settings.get('min_duration',10)),
         float(settings.get('reduction_ratio',.3)),sorted(set(settings.get('event_types',defaults)))]
    if profile in ('dynamic_baseline','dynamic_baseline_v2','dynamic_baseline_v3'):key += [settings.get('method'),settings.get('window_seconds')]
    return hashlib.sha256(json.dumps(key,ensure_ascii=False).encode()).hexdigest()


class Conflict(ValueError):
    pass


def validate_event(e, meta):
    if not isinstance(e, dict):
        raise ValueError('事件格式错误')
    a, b, mode = e.get('start'), e.get('end'), e.get('mode')
    if mode not in ('raw', 'engineering'):
        raise ValueError('必须明确时间轴模式')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (a, b)):
        raise ValueError('事件时间必须为有限秒数')
    limit = meta['display_duration'] if mode == 'engineering' else meta['raw_duration']
    if a < 0 or b <= a or b > limit + 1e-6:
        raise ValueError('事件起止时间超出范围')
    if e.get('type') not in TYPES:
        raise ValueError('事件类型无效')
    if not isinstance(e.get('channels'), list) or not e['channels'] or any(c not in meta['channels'] for c in e['channels']):
        raise ValueError('事件通道无效')
    for key, maxlen in (('id', 100), ('note', 2000), ('updated_at', 100)):
        if not isinstance(e.get(key), str) or len(e[key]) > maxlen or (key != 'note' and not e[key]):
            raise ValueError('事件字段无效: ' + key)
    source = e.get('source', 'manual')
    status = e.get('status', 'candidate' if source in ('agent','detector') else 'reviewed')
    if source not in ('manual', 'agent', 'detector') or status not in ('candidate', 'reviewed', 'ignored'):
        raise ValueError('事件来源或复核状态无效')
    result={**{k: e[k] for k in ('id', 'start', 'end', 'mode', 'type', 'note', 'updated_at')},
            'channels': list(dict.fromkeys(e['channels'])), 'source': source, 'status': status}
    if source=='detector':
        profile=e.get('detection_profile')
        if profile is None:profile='company_rules' if e['type'].startswith('公司') or '公司规则试验' in e['note'] else 'device_candidates' if e['type'].startswith('流量波动') else 'engineering'
        if profile not in ('company_rules','device_candidates','engineering','adaptive_test','adaptive_multifeature','dynamic_baseline','dynamic_baseline_v2','dynamic_baseline_v3'):raise ValueError('事件检测方案来源无效')
        result['detection_profile']=profile
        if 'detection_input' in e:
            if e['detection_input'] not in ('csv_flow','pressure_q'):raise ValueError('检测输入来源无效')
            result['detection_input']=e['detection_input']
        if profile in ('adaptive_test','adaptive_multifeature','dynamic_baseline','dynamic_baseline_v2','dynamic_baseline_v3'):
            version=e.get('detection_version')
            if version is None:
                match=re.search(r'adaptive-flow-test-v[\w-]+',e['note']);version=match.group(0) if match else 'unknown'
            if not isinstance(version,str) or len(version)>80:raise ValueError('测试算法版本无效')
            result['detection_version']=version
        if profile in ('dynamic_baseline','dynamic_baseline_v2','dynamic_baseline_v3'):
            method=e.get('detection_method');window=e.get('detection_window_seconds')
            if method not in (('absolute_mean','centered_rms','detrended_abs') if profile in ('dynamic_baseline_v2','dynamic_baseline_v3') else ('absolute_mean','centered_rms')) or isinstance(window,bool) or window not in (range(1,11) if profile=='dynamic_baseline_v3' else (2,5,8)):raise ValueError('动态基线方法或窗口无效')
            result.update(detection_method=method,detection_window_seconds=window)
            if profile=='dynamic_baseline_v3' and 'detection_min_duration' in e:
                duration=e['detection_min_duration']
                if isinstance(duration,bool) or not isinstance(duration,(int,float)) or not math.isfinite(duration) or duration not in range(5,11):raise ValueError('优化v3最短持续应为5～10秒整数')
                result['detection_min_duration']=duration
        if profile in ('adaptive_multifeature','dynamic_baseline','dynamic_baseline_v2','dynamic_baseline_v3') and 'evidence' in e:
            evidence=e['evidence']
            if not isinstance(evidence,dict) or evidence.get('schema')!=1:
                raise ValueError('检测依据格式无效')
            try:
                encoded=json.dumps(evidence,ensure_ascii=False,allow_nan=False)
            except (ValueError,TypeError,RecursionError) as exc:
                raise ValueError('检测依据必须为有限JSON数据') from exc
            if len(encoded)>10000:raise ValueError('检测依据过长')
            result['evidence']=json.loads(encoded)
    return result


class EventStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS records (file_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, events TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS scans (run_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS changes (file_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(file_id,revision))')
            db.execute('CREATE TABLE IF NOT EXISTS scan_info (run_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, created_at TEXT NOT NULL, title TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0, fingerprint TEXT NOT NULL, summary TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS scan_info_file_date ON scan_info(file_id,archived,created_at DESC)')
            db.execute('CREATE INDEX IF NOT EXISTS scan_info_match ON scan_info(file_id,fingerprint)')
            # Build a small index once for old snapshots; original payloads stay immutable.
            for (payload,) in db.execute('SELECT s.payload FROM scans s LEFT JOIN scan_info i ON i.run_id=s.run_id WHERE i.run_id IS NULL').fetchall():
                self._index_scan(db,json.loads(payload))

    @staticmethod
    def _index_scan(db,snapshot):
        summary={k:snapshot[k] for k in ('run_id','created_at','app_version','settings','scan_start','scan_end')}
        summary['detected_count']=len(snapshot.get('events',[]))
        label={'device_candidates':'设备候选','company_rules':'公司规则试验','adaptive_test':'自适应测试（旧版）','adaptive_multifeature':'多特征自适应测试'}.get(snapshot['settings'].get('profile'),'工程筛查')
        if snapshot['settings'].get('profile') in ('dynamic_baseline','dynamic_baseline_v2','dynamic_baseline_v3'):label=({'dynamic_baseline_v2':'动态基线优化v2','dynamic_baseline_v3':'动态基线优化v3'}.get(snapshot['settings']['profile'],'动态基线'))+' / '+{'centered_rms':'去均值RMS','detrended_abs':'去背景绝对值','absolute_mean':'绝对值均值'}[snapshot['settings']['method']]+f' / {snapshot["settings"]["window_seconds"]}秒'
        if snapshot['settings'].get('profile')=='dynamic_baseline_v3':label+=f' / 持续≥{snapshot["settings"].get("min_duration",10)}秒'
        title=f"{label} {snapshot['scan_start']:g}—{snapshot['scan_end']:g}秒"
        db.execute('INSERT INTO scan_info VALUES (?,?,?,?,?,?,?)',(snapshot['run_id'],snapshot['file_id'],snapshot['created_at'],title,0,
                   scan_fingerprint(snapshot['scan_start'],snapshot['scan_end'],snapshot['settings']),json.dumps(summary,ensure_ascii=False)))

    @staticmethod
    def _scan_info(row):
        return dict(json.loads(row[0]),title=row[1],archived=bool(row[2]))

    def read(self, file_id):
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute('SELECT revision, events FROM records WHERE file_id=?', (file_id,)).fetchone()
        return dict(file_id=file_id, revision=row[0] if row else 0, events=json.loads(row[1]) if row else [])

    def write(self, meta, revision, events, reviewer='', scan=None):
        if isinstance(revision, bool) or not isinstance(revision, int) or not isinstance(events, list) or len(events) > 10000:
            raise ValueError('标记请求格式无效（最多10000条）')
        checked = [validate_event(e, meta) for e in events]
        if len({e['id'] for e in checked}) != len(checked):
            raise ValueError('重复事件编号')
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision,events FROM records WHERE file_id=?', (meta['file_id'],)).fetchone()
            current = row[0] if row else 0
            if revision != current:
                raise Conflict('事件已被网页或Agent更新，请重新读取后再保存；当前编辑未覆盖其他标记')
            db.execute('INSERT OR REPLACE INTO records VALUES (?, ?, ?)',
                       (meta['file_id'], current + 1, json.dumps(checked, ensure_ascii=False, allow_nan=False)))
            before=json.loads(row[1]) if row else []
            change=dict(revision=current+1,at=datetime.now(timezone.utc).isoformat(),reviewer=str(reviewer)[:100],
                        before=before,after=checked,app_version=APP_VERSION)
            db.execute('INSERT INTO changes VALUES (?,?,?)',(meta['file_id'],current+1,json.dumps(change,ensure_ascii=False)))
            run_id=None
            if scan is not None:
                run_id=str(uuid.uuid4())
                snapshot=dict(version=1,kind='automatic',file_id=meta['file_id'],run_id=run_id,app_version=APP_VERSION,
                              created_at=change['at'],recording={k:meta[k] for k in ('name','start','end','rows')},**scan)
                db.execute('INSERT INTO scans VALUES (?,?,?)',(run_id,meta['file_id'],json.dumps(snapshot,ensure_ascii=False,allow_nan=False)))
                self._index_scan(db,snapshot)
        return dict(file_id=meta['file_id'], revision=current + 1, events=checked,run_id=run_id)

    def scans(self, file_id):
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('SELECT summary,title,archived FROM scan_info WHERE file_id=? ORDER BY created_at DESC,rowid DESC',(file_id,)).fetchall()
        return [self._scan_info(row) for row in rows]

    def scan_page(self,file_id,limit=10,offset=0,query='',state='active',selected=None):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=100 or isinstance(offset,bool) or not isinstance(offset,int) or offset<0:
            raise ValueError('批次分页参数无效')
        if state not in ('active','archived','all') or not isinstance(query,str) or len(query)>200:raise ValueError('批次筛选条件无效')
        where='file_id=?';args=[file_id]
        if state!='all':where+=' AND archived=?';args.append(int(state=='archived'))
        if query.strip():
            pattern='%'+query.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
            where+=" AND (title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\')";args.extend([pattern,pattern])
        with closing(sqlite3.connect(self.path)) as db:
            total=db.execute('SELECT COUNT(*) FROM scan_info WHERE '+where,args).fetchone()[0]
            rows=db.execute('SELECT summary,title,archived FROM scan_info WHERE '+where+' ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?',args+[limit,offset]).fetchall()
            chosen=db.execute('SELECT summary,title,archived FROM scan_info WHERE file_id=? AND run_id=?',(file_id,selected)).fetchone() if selected else None
        return dict(scans=[self._scan_info(r) for r in rows],total=total,limit=limit,offset=offset,selected=self._scan_info(chosen) if chosen else None)

    def edit_scan(self,file_id,run_id,title=None,archived=None):
        if title is None and archived is None:raise ValueError('未指定批次修改')
        if title is not None and (not isinstance(title,str) or not title.strip() or len(title)>100):raise ValueError('批次名称需为1～100字')
        if archived is not None and not isinstance(archived,bool):raise ValueError('归档状态无效')
        with closing(sqlite3.connect(self.path)) as db,db:
            row=db.execute('SELECT summary,title,archived FROM scan_info WHERE file_id=? AND run_id=?',(file_id,run_id)).fetchone()
            if not row:raise ValueError('未找到当前文件对应的批次')
            db.execute('UPDATE scan_info SET title=?,archived=? WHERE file_id=? AND run_id=?',(title.strip() if title is not None else row[1],int(archived) if archived is not None else row[2],file_id,run_id))
            return self._scan_info((row[0],title.strip() if title is not None else row[1],int(archived) if archived is not None else row[2]))

    def matching_scan(self,file_id,start,end,settings):
        with closing(sqlite3.connect(self.path)) as db:
            row=db.execute('SELECT summary,title,archived FROM scan_info WHERE file_id=? AND fingerprint=? ORDER BY created_at DESC,rowid DESC LIMIT 1',(file_id,scan_fingerprint(start,end,settings))).fetchone()
        return self._scan_info(row) if row else None

    def feedback(self, meta, run_id, kind, reviewer='', reviewed_range=None):
        if kind not in ('automatic','reviewed'):raise ValueError('反馈类型无效')
        with closing(sqlite3.connect(self.path)) as db, db:
            row=db.execute('SELECT payload FROM scans WHERE run_id=? AND file_id=?',(run_id,meta['file_id'])).fetchone()
            if not row:raise ValueError('未找到对应扫描，请先自动检测')
            snapshot=json.loads(row[0])
            if kind=='automatic':return snapshot
            if not reviewer.strip():raise ValueError('请填写校验人姓名或代号')
            if not isinstance(reviewed_range,list) or len(reviewed_range)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in reviewed_range) or not 0<=reviewed_range[0]<reviewed_range[1]<=meta['raw_duration']:
                raise ValueError('请明确已人工检查的原始秒级时间范围')
            current=db.execute('SELECT revision,events FROM records WHERE file_id=?',(meta['file_id'],)).fetchone()
            history=[json.loads(r[0]) for r in db.execute('SELECT payload FROM changes WHERE file_id=? ORDER BY revision',(meta['file_id'],))]
        return dict(version=1,kind='reviewed',file_id=meta['file_id'],run_id=run_id,app_version=APP_VERSION,
                    recording=snapshot['recording'],exported_at=datetime.now(timezone.utc).isoformat(),reviewer=reviewer[:100],reviewed_range=reviewed_range,
                    events=json.loads(current[1]) if current else [],revision=current[0] if current else 0,
                    automatic=snapshot,history=history,
                    note='已检查范围由校验人声明；仍为candidate的事件尚未确认，不能作为正常或已完成复核的依据')
