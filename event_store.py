"""Shared, revision-checked local storage for browser and MCP annotations."""
import json
import math
import sqlite3
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
import uuid
from app_version import APP_VERSION

TYPES = ('疑似暂停', '疑似低通气', '漏气', '波形异常', '伪迹/干扰', '其他', '呼吸波动减弱', '总漏气升高', '数据缺失', '信号异常')


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
    return {**{k: e[k] for k in ('id', 'start', 'end', 'mode', 'type', 'note', 'updated_at')},
            'channels': list(dict.fromkeys(e['channels'])), 'source': source, 'status': status}


class EventStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS records (file_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, events TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS scans (run_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS changes (file_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(file_id,revision))')

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
        return dict(file_id=meta['file_id'], revision=current + 1, events=checked,run_id=run_id)

    def scans(self, file_id):
        with closing(sqlite3.connect(self.path)) as db:
            rows=db.execute('SELECT payload FROM scans WHERE file_id=? ORDER BY rowid DESC',(file_id,)).fetchall()
        return [{k:p[k] for k in ('run_id','created_at','app_version','settings','scan_start','scan_end')} for (raw,) in rows for p in [json.loads(raw)]]

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
