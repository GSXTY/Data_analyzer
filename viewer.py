"""Local respiratory CSV viewer. Run: python viewer.py"""
from __future__ import annotations
import argparse
import io
import hashlib
import json
import threading
import webbrowser
import sys
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd
from event_store import EventStore, Conflict
from detector import detect, DETECTABLE_TYPES, VERSION
from app_version import APP_VERSION, APP_ID
from feedback_export import scoped_review,save_package,folder_for
from recording_cache import RecordingCache

ROOT = Path(__file__).resolve().parent
ASSETS = Path(getattr(sys, '_MEIPASS', ROOT))
DEFAULT = os.environ.get('RESPIRATORY_CSV','')
PRESSURE_ROLES = {'气压1':'内部压力','气压2':'出风口压力','气压3':'标准气压',
                  '控制压力':'控制压力','压力':'压力','治疗压':'治疗压力','吸气压':'吸气压力','呼气压':'呼气压力',
                  '气压1−气压3（补偿）':'内部相对标准气压','气压2−气压3（补偿）':'出风口相对标准气压','气压1−气压2（差压）':'内部与出风口差压'}


class Recording:
    def __init__(self, raw, name):
        self.raw_csv=raw
        for encoding in ('utf-8-sig', 'gb18030'):
            try:
                df = pd.read_csv(io.BytesIO(raw), encoding=encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError('无法识别文件编码，请使用 UTF-8 或 GB18030 CSV。')
        df = df.dropna(how='all').reset_index(drop=True)
        if len(df.columns) < 2 or not len(df):
            raise ValueError('文件至少需要时间列和一个数据列。')
        times = pd.to_datetime(df.iloc[:, 0], errors='coerce')
        if times.isna().any():
            raise ValueError('第一列包含无效时间，请检查 CSV。')
        seconds = (times - times.iloc[0]).dt.total_seconds().to_numpy()
        if np.any(np.diff(seconds) < 0):
            raise ValueError('时间戳存在倒序，请先核对记录顺序。')
        # Keep source timestamps exactly: repeated timestamps are not a verified sampling clock.
        self.x = seconds
        # Match the reference script's estimated within-timestamp expansion.
        # This is a display coordinate, never a replacement for source timestamps.
        starts = np.r_[0, np.flatnonzero(np.diff(seconds) != 0) + 1]
        lengths = np.diff(np.r_[starts, len(seconds)])
        positive = np.diff(seconds)
        positive = positive[positive > 0]
        typical = float(np.median(positive)) if len(positive) else 1.0
        threshold = max(typical * 3, typical + 120, 1.0)
        normal = positive[positive <= threshold]
        span = max(float(np.median(normal)) if len(normal) else typical, 1e-9)
        deltas = np.diff(seconds[starts])
        candidates = (lengths[:-1] > 1) & (deltas > 0) & (deltas <= threshold)
        nominal = float(np.median(deltas[candidates] / lengths[:-1][candidates])) if candidates.any() else span / max(lengths.max(), 1)
        steps = np.full(len(starts), max(nominal, 1e-12))
        regular = (deltas > 0) & (deltas <= threshold)
        steps[:-1] = np.where(regular, deltas / lengths[:-1], steps[:-1])
        steps = np.minimum(np.maximum(steps, 1e-12), span / lengths)
        offsets = np.arange(len(seconds)) - np.repeat(starts, lengths)
        self.display_x = seconds + offsets * np.repeat(steps, lengths)
        self.channels = {}
        for col in df.columns[1:]:
            values = pd.to_numeric(df[col], errors='coerce').to_numpy(dtype=float)
            if np.isfinite(values).any():
                self.channels[str(col)] = values
        if not self.channels:
            raise ValueError('没有找到数值通道。')
        # Reference script's pressure compensation, exposed as additional channels.
        # Preserve raw channels and do not apply device-specific byte conversion implicitly.
        if all(name in self.channels for name in ('气压1', '气压2', '气压3')):
            self.channels['气压1−气压3（补偿）'] = self.channels['气压1'] - self.channels['气压3']
            self.channels['气压2−气压3（补偿）'] = self.channels['气压2'] - self.channels['气压3']
            self.channels['气压1−气压2（差压）'] = self.channels['气压1'] - self.channels['气压2']
        self.engineering = {}
        for raw_name in ('气压1', '气压2'):
            compensated = raw_name + '−气压3（补偿）'
            if compensated in self.channels:
                self.engineering[raw_name] = self.channels[compensated]
        byte_column = str(df.columns[13]) if len(df.columns) > 13 else None
        if byte_column in PRESSURE_ROLES:byte_column=None
        if byte_column in self.channels:
            values = self.channels[byte_column]
            self.engineering[byte_column] = np.where(values > 125, values - 256, values)
        gaps = np.flatnonzero(np.diff(seconds) > 1)
        self.gaps = [[float(seconds[i]), float(seconds[i + 1])] for i in gaps]
        reference_breaks = np.flatnonzero(np.diff(seconds) > threshold)
        reference_gaps = [[float(self.display_x[i]), float(self.display_x[i+1])] for i in reference_breaks]
        bins,counts=np.unique(np.floor(seconds).astype(np.int64),return_counts=True)
        interior=counts[(bins>bins[0])&(bins<bins[-1])]
        if not len(interior):interior=counts
        self.meta = dict(name=name, rows=len(df), start=str(times.iloc[0]), end=str(times.iloc[-1]),
                         file_id=hashlib.sha256(raw).hexdigest(),app_version=APP_VERSION,
                         duration=float(seconds[-1]), channels=list(self.channels), gaps=self.gaps,
                         median_records_per_second=float(np.median(interior)),
                         repeated_timestamps=bool(times.duplicated().any()), encoding=encoding)
        self.meta.update(channel_units={name:('Pa' if name in PRESSURE_ROLES else 'L/min') for name in self.channels if name in PRESSURE_ROLES or name in ('流量','漏气量')},
                         channel_roles={name:(PRESSURE_ROLES[name] if name in PRESSURE_ROLES else '机器总流量（计算值）' if name=='流量' else '瞬时总漏气率（计算值）') for name in self.channels if name in PRESSURE_ROLES or name in ('流量','漏气量')},
                         device_profile='当前设备使用者确认配置；其他设备导入需另行核对',
                         sampling_info=dict(record_count_p10=float(np.quantile(interior,.1)),record_count_p90=float(np.quantile(interior,.9)),
                            recorded_seconds=len(bins),pressure_nominal_hz=50,pressure_reported_hz=[47,48],
                            computed_flow_leak_reported_hz=30,computed_rate_verified=False,uniform_sampling_verified=False,
                            note='每秒行数是记录密度，不等于独立采样或更新频率；重复值不能据此推断为未更新。机器总流量与瞬时总漏气率单位均为L/min，不相减；不按50Hz重采样或插值。'))
        self.meta.update(display_duration=float(self.display_x[-1]), engineering_gaps=reference_gaps,
                         engineering_gap_threshold=threshold, engineering_byte_column=byte_column,
                         engineering_pressure_compensated='气压1' in self.engineering,
                         raw_duration=self.meta['duration'], raw_gaps=self.gaps)

    def wave(self, start, end, names, pixels, mode='raw'):
        if mode not in ('raw', 'engineering'):
            raise ValueError('不支持的显示模式')
        x = self.display_x if mode == 'engineering' else self.x
        start, end = max(0, start), min(float(x[-1]), end)
        if end < start:
            raise ValueError('无效的时间范围。')
        lo = int(np.searchsorted(x, start, side='left'))
        hi = int(np.searchsorted(x, end, side='right'))
        lo, hi = max(0, lo - 1), min(len(x), hi + 1)
        result = {}
        for name in names:
            if name not in self.channels:
                continue
            y = self.engineering.get(name, self.channels[name]) if mode == 'engineering' else self.channels[name]
            # Min/max envelope preserves narrow peaks, unlike uniform subsampling.
            size = max(1, int(np.ceil((hi - lo) / max(100, min(pixels, 9000)))))
            block = y[lo:hi]
            if not len(block):
                result[name] = []
                continue
            if size == 1:
                indices = np.arange(lo, hi)
            else:
                # Vectorized block reductions: avoid thousands of tiny NumPy calls per pan.
                count = (len(block) + size - 1) // size
                padded = np.full(count * size, np.nan)
                padded[:len(block)] = block
                matrix = padded.reshape(count, size)
                finite = np.isfinite(matrix)
                starts = np.arange(count) * size
                mins = starts + np.argmin(np.where(finite, matrix, np.inf), axis=1)
                maxs = starts + np.argmax(np.where(finite, matrix, -np.inf), axis=1)
                # Include every finite/missing transition so missing runs always break lines.
                transitions = np.flatnonzero(np.diff(np.isfinite(block)))
                local = np.unique(np.concatenate((starts, np.minimum(starts+size-1, len(block)-1),
                                                   mins, maxs, transitions, transitions+1)))
                indices = local[local < len(block)] + lo
            points = [[float(x[i]), float(y[i]) if np.isfinite(y[i]) else None, int(i)]
                      for i in indices]
            result[name] = points
        return dict(series=result, start=start, end=end, source_rows=max(0, hi-lo))


recording = None
recording_lock = threading.Lock()
event_store = None
scan_lock = threading.Lock()
recording_cache = RecordingCache()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def send(self, value, status=200, content_type='application/json; charset=utf-8'):
        raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        try:
            url = urlparse(self.path)
            if url.path == '/':
                return self.send((ASSETS / 'index.html').read_bytes(), content_type='text/html; charset=utf-8')
            if url.path == '/api/health':
                return self.send(dict(app_id=APP_ID,app_version=APP_VERSION))
            if url.path in ('/viewer.js', '/annotation-model.js'):
                return self.send((ASSETS / url.path.lstrip('/')).read_bytes(), content_type='text/javascript; charset=utf-8')
            with recording_lock:
                current = recording
            if current is None:
                return self.send({'error': '请先打开 CSV 文件。'}, 400)
            if url.path == '/api/meta':
                return self.send(current.meta)
            if url.path == '/api/current-csv':
                if parse_qs(url.query).get('file_id',[''])[0]!=current.meta['file_id']:
                    return self.send({'error':'当前文件已变更，请重新读取'},409)
                return self.send(current.raw_csv,content_type='application/octet-stream')
            if url.path == '/api/device-summary':
                if parse_qs(url.query).get('file_id',[''])[0]!=current.meta['file_id']:
                    return self.send({'error':'文件已切换'},409)
                for scan in event_store.scans(current.meta['file_id']):
                    if scan['settings'].get('profile')=='device_candidates':
                        snapshot=event_store.feedback(current.meta,scan['run_id'],'automatic')
                        return self.send(dict(run_id=scan['run_id'],device_metrics=snapshot.get('device_metrics')))
                return self.send(dict(device_metrics=None))
            if url.path == '/api/events':
                file_id = parse_qs(url.query).get('file_id', [''])[0]
                if file_id != current.meta['file_id']:
                    return self.send({'error': '当前文件已变更，请重新读取文件信息'}, 409)
                return self.send(event_store.read(file_id))
            if url.path == '/api/scans':
                q=parse_qs(url.query)
                if q.get('file_id',[''])[0]!=current.meta['file_id']:
                    return self.send({'error':'文件已切换'},409)
                return self.send(event_store.scan_page(current.meta['file_id'],int(q.get('limit',['10'])[0]),int(q.get('offset',['0'])[0]),q.get('q',[''])[0],q.get('state',['active'])[0],q.get('selected',[''])[0]))
            if url.path == '/api/wave':
                q = parse_qs(url.query)
                if 'file_id' in q and q['file_id'][0] != current.meta['file_id']:
                    return self.send({'error': '当前文件已变更'}, 409)
                return self.send(current.wave(float(q['start'][0]), float(q['end'][0]),
                                              q.get('channel', list(current.channels)[:3]),
                                              int(q.get('pixels', ['1200'])[0]),
                                              q.get('mode', ['raw'])[0]))
            self.send({'error': '不存在的地址'}, 404)
        except Exception as exc:
            self.send({'error': str(exc)}, 400)

    def do_POST(self):
        global recording
        # Reject cross-site browser requests to the local service.
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.send({'error': '不允许跨站请求'}, 403)
        try:
            if urlparse(self.path).path in ('/api/activate','/api/cache/release'):
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<1000:raise ValueError('缓存请求大小无效')
                args=json.loads(self.rfile.read(length));fid=args.get('file_id')
                if not isinstance(fid,str) or len(fid)!=64:raise ValueError('文件指纹无效')
                with recording_lock:
                    if urlparse(self.path).path.endswith('/release'):
                        return self.send(dict(released=recording_cache.release(fid) if recording is None or recording.meta['file_id']!=fid else False))
                    recording_cache.put(recording)
                    cached=recording_cache.get(fid)
                    if cached is None:return self.send(dict(hit=False))
                    recording=cached
                    return self.send(dict(hit=True,meta=cached.meta))
            if urlparse(self.path).path=='/api/close':
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<1000:raise ValueError('关闭请求大小无效')
                args=json.loads(self.rfile.read(length))
                with recording_lock:
                    if recording is None or args.get('file_id')!=recording.meta['file_id']:raise Conflict('当前文件已变化，关闭已取消')
                    recording_cache.release(recording.meta['file_id'])
                    recording=None
                return self.send(dict(closed=True))
            if urlparse(self.path).path=='/api/feedback/open-folder':
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<1000:raise ValueError('请求大小无效')
                args=json.loads(self.rfile.read(length))
                with recording_lock:
                    current=recording
                    if current is None or args.get('file_id')!=current.meta['file_id']:raise Conflict('文件已切换')
                    folder=folder_for(Path(event_store.path).parent/'feedback_exports',current.meta).resolve()
                if not folder.is_dir():raise ValueError('尚未保存反馈文件夹')
                if os.name!='nt':raise ValueError('请复制保存路径后在文件管理器打开')
                os.startfile(str(folder))
                return self.send(dict(opened=True))
            if urlparse(self.path).path in ('/api/feedback/prepare','/api/feedback/export'):
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<20000:raise ValueError('反馈请求大小无效')
                args=json.loads(self.rfile.read(length))
                with recording_lock:
                    current=recording
                    if current is None or args.get('file_id')!=current.meta['file_id']:raise Conflict('文件已切换')
                    automatic=event_store.feedback(current.meta,args.get('run_id'),'automatic')
                    packet=event_store.read(current.meta['file_id'])
                    reviewed=scoped_review(current.meta,automatic,packet,args.get('reviewer'),args.get('reviewed_range'),args.get('reviewed_types'),args.get('review_mode'))
                    root=Path(event_store.path).parent/'feedback_exports'
                    if urlparse(self.path).path.endswith('/prepare'):
                        return self.send(dict(folder=str(folder_for(root,current.meta).resolve()),revision=packet['revision'],file_name=current.meta['name'],
                             run_id=automatic['run_id'],reviewer=reviewed['reviewer'],reviewed_range=reviewed['reviewed_range'],reviewed_types=reviewed['reviewed_types'],review_mode=reviewed['review_mode'],
                             event_count=len(reviewed['events']),confirmed_count=len(reviewed['training_scope']['positive_event_ids']),boundary_count=len(reviewed['boundary_events']),estimated_count=len(reviewed['estimated_time_events']),pending_count=len(reviewed['training_scope']['pending_event_ids'])))
                    if isinstance(args.get('expected_revision'),bool) or args.get('expected_revision')!=packet['revision']:raise Conflict('标记已变化，请重新预览并确认后导出')
                return self.send(save_package(root,current.meta,current.raw_csv,automatic,reviewed,args.get('confirmed',False)))
            if urlparse(self.path).path in ('/api/scans/edit','/api/scans/match'):
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<20000:raise ValueError('批次请求大小无效')
                args=json.loads(self.rfile.read(length))
                with recording_lock:
                    current=recording
                    if current is None or args.get('file_id')!=current.meta['file_id']:raise Conflict('文件已切换')
                    if urlparse(self.path).path.endswith('/edit'):
                        return self.send(event_store.edit_scan(current.meta['file_id'],args.get('run_id'),args.get('title'),args.get('archived')))
                    profile=args.get('profile','engineering');types=args.get('event_types')
                    if profile not in DETECTABLE_TYPES:raise ValueError('检测方案无效')
                    if types is not None and (not isinstance(types,list) or not types or any(t not in DETECTABLE_TYPES[profile] for t in types)):raise ValueError('检测类型无效')
                    settings=dict(version=VERSION if profile=='engineering' else 'machine-flow-candidates-v2',profile=profile,
                                  min_duration=args.get('min_duration',10),reduction_ratio=args.get('reduction_ratio',.3),event_types=types or list(DETECTABLE_TYPES[profile]))
                    return self.send(dict(match=event_store.matching_scan(current.meta['file_id'],args.get('start',0),args.get('end',current.meta['raw_duration']),settings)))
            if urlparse(self.path).path == '/api/feedback':
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<20000:raise ValueError('反馈请求大小无效')
                args=json.loads(self.rfile.read(length))
                with recording_lock:
                    current=recording
                    if current is None or args.get('file_id')!=current.meta['file_id']:raise Conflict('文件已切换')
                    if args.get('kind')=='reviewed' and 'reviewed_types' in args:
                        automatic=event_store.feedback(current.meta,args.get('run_id'),'automatic')
                        return self.send(scoped_review(current.meta,automatic,event_store.read(current.meta['file_id']),args.get('reviewer'),args.get('reviewed_range'),args.get('reviewed_types'),args.get('review_mode')))
                    return self.send(event_store.feedback(current.meta,args.get('run_id'),args.get('kind'),args.get('reviewer',''),args.get('reviewed_range')))
            if urlparse(self.path).path == '/api/detect':
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length < 20000:
                    raise ValueError('扫描请求过大')
                args = json.loads(self.rfile.read(length))
                with recording_lock:
                    current = recording
                    if current is None or args.get('file_id') != current.meta['file_id']:
                        raise Conflict('当前文件已变化，请重新读取')
                    previous = event_store.read(current.meta['file_id'])
                if not scan_lock.acquire(blocking=False):
                    raise Conflict('已有扫描正在进行，请等待完成')
                try:
                    result = detect(current,args.get('start',0),args.get('end'),args.get('min_duration',10),args.get('reduction_ratio',.3),args.get('profile','engineering'),args.get('event_types'))
                    saved=[];skipped=0;event_links={}
                    for e in result['events']:
                        duplicate=False
                        for old in previous['events']+saved:
                            overlap=max(0,min(old['end'],e['end'])-max(old['start'],e['start']))
                            span=max(old['end'],e['end'])-min(old['start'],e['start'])
                            if old['id']==e['id'] or (old['source']=='detector' and old['type']==e['type'] and old['mode']==e['mode'] and overlap/span>=.5):
                                duplicate=True;event_links[e['id']]=old['id'];break
                        if duplicate:skipped+=1
                        else:saved.append(e)
                    if len(saved)>1000:
                        raise ValueError('候选超过1000条，请缩小范围或调整阈值；本次未保存')
                    with recording_lock:
                        if recording is not current:raise Conflict('扫描期间文件已变化，本次未保存')
                        snapshot={k:result[k] for k in ('events','settings','scan_start','scan_end','warnings','analyzed_rows','eligible_seconds')}
                        snapshot['event_links']=event_links
                        if 'device_metrics' in result:snapshot['device_metrics']=result['device_metrics']
                        packet=event_store.write(current.meta,previous['revision'],previous['events']+saved,scan=snapshot)
                    result.update(added=len(saved),skipped=skipped,detected_count=len(result['events']),packet=packet)
                    result.pop('events')
                    return self.send(result)
                finally:
                    scan_lock.release()
            if urlparse(self.path).path == '/api/events':
                length = int(self.headers.get('Content-Length', '0'))
                if length < 1 or length > 10*1024*1024:
                    raise ValueError('标记请求超出大小限制')
                packet = json.loads(self.rfile.read(length))
                with recording_lock:
                    if recording is None or packet.get('file_id') != recording.meta['file_id']:
                        return self.send({'error': '当前文件已变更，请重新读取文件信息'}, 409)
                    result = event_store.write(recording.meta, packet.get('revision'), packet.get('events'),reviewer=packet.get('reviewer',''))
                return self.send(result)
            if urlparse(self.path).path != '/api/open':
                return self.send({'error': '不存在的地址'}, 404)
            length = int(self.headers.get('Content-Length', '0'))
            if length <= 0 or length > 1024**3:
                raise ValueError('请选择大小在 1 GB 以内的 CSV 文件。')
            from urllib.parse import unquote
            raw=self.rfile.read(length);fid=hashlib.sha256(raw).hexdigest()
            with recording_lock:
                recording_cache.put(recording)
                new=recording_cache.get(fid)
            if new is None:new=Recording(raw, unquote(self.headers.get('X-Filename', 'recording.csv')))
            with recording_lock:
                recording_cache.put(new)
                recording = new
            self.send(new.meta)
        except Conflict as exc:
            self.send({'error': str(exc)}, 409)
        except Exception as exc:
            self.send({'error': str(exc)}, 400)


def main():
    global recording, event_store
    parser = argparse.ArgumentParser()
    parser.add_argument('file', nargs='?', default='' if getattr(sys,'frozen',False) else DEFAULT)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--data-dir',type=Path)
    parser.add_argument('--version',action='version',version=APP_VERSION)
    args = parser.parse_args()
    data_dir=args.data_dir or (Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'VVFLY' / 'RespiratoryViewer' if getattr(sys,'frozen',False) else ROOT / 'data')
    event_store = EventStore(data_dir / 'events.sqlite3')
    if args.file and Path(args.file).is_file():
        recording = Recording(Path(args.file).read_bytes(), Path(args.file).name)
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as exc:
        print(f'Cannot start viewer on port {args.port}. Close the previous viewer first. {exc}',flush=True)
        if getattr(sys,'frozen',False):input('Press Enter to close...')
        return 1
    url = f'http://127.0.0.1:{args.port}'
    print(f'Respiratory waveform viewer: {url}', flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    sys.exit(main() or 0)
