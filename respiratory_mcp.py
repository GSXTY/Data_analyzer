"""Official MCP SDK stdio bridge to the running local waveform viewer."""
from pathlib import Path
import sys
import site
dependencies = str(Path(__file__).resolve().parent / '.mcp-deps')
sys.path.insert(0, dependencies)
site.addsitedir(dependencies)
import json
import uuid
import os
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

BASE = os.environ.get('RESPIRATORY_VIEWER_URL', 'http://127.0.0.1:8765')
from urllib.parse import urlparse
parsed_base = urlparse(BASE)
if parsed_base.scheme != 'http' or parsed_base.hostname != '127.0.0.1' or parsed_base.path not in ('', '/') or parsed_base.username:
    raise ValueError('MCP仅支持本机127.0.0.1 HTTP查看器')
http = build_opener(ProxyHandler({}))
mcp = FastMCP('respiratory-local', instructions='Inspect local CSV waveforms and persist candidate annotations shared with the browser. Time offsets are seconds from recording start. Engineering time is estimated. Do not diagnose or claim clinical AHI.')
READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)


def request(path, payload=None):
    raw = None if payload is None else json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
    req = Request(BASE + path, data=raw, headers={'Content-Type': 'application/json'})
    try:
        with http.open(req, timeout=30) as response:
            return json.load(response)
    except Exception as exc:
        detail = exc.read().decode('utf-8') if hasattr(exc, 'read') else str(exc)
        raise ValueError('本机查看器请求失败。请确认已启动查看器。' + detail) from exc


def check(file_id):
    meta = request('/api/meta')
    if meta['file_id'] != file_id:
        raise ValueError('文件已切换，请重新调用 get_current_recording')
    return meta


def read_events(file_id):
    check(file_id)
    return request('/api/events?' + urlencode({'file_id': file_id}))


@mcp.tool(annotations=READ)
def get_current_recording() -> dict:
    """Read active CSV identity, channels, time span and raw/estimated time limitations."""
    return request('/api/meta')


@mcp.tool(annotations=READ)
def get_waveform(file_id: str, start: float, end: float, channels: list[str], mode: str = 'raw', pixels: int = 400) -> dict:
    """Read display-selected waveform points (NOT complete analysis samples). Explicit seconds and time mode required; <=1h, <=8 channels, 100..1200 pixel buckets."""
    meta = check(file_id)
    if mode not in ('raw', 'engineering') or not 0 <= start < end or end-start > 3600 or not channels or len(channels)>8 or any(c not in meta['channels'] for c in channels):
        raise ValueError('时间/模式/通道参数无效；最多1小时、8通道')
    q = urlencode({'file_id':file_id,'start': start, 'end': end, 'channel': channels, 'mode': mode, 'pixels': max(100, min(1200,pixels))}, doseq=True)
    result = request('/api/wave?' + q)
    result.update(file_id=file_id, time_basis=mode, sampled_for_display=True, viewer_url=BASE)
    return result


@mcp.tool(annotations=READ)
def list_events(file_id: str) -> dict:
    """List shared browser/Agent annotations, including source, review status and revision."""
    return read_events(file_id)


@mcp.tool(annotations=WRITE)
def create_event(file_id: str, start: float, end: float, event_type: str, channels: list[str], mode: str, note: str = '') -> dict:
    """Create an Agent CANDIDATE event visible in the browser. Types: 疑似暂停,疑似低通气,漏气,波形异常,伪迹/干扰,其他. No automatic clinical classification."""
    packet = read_events(file_id)
    event = dict(id=str(uuid.uuid4()), start=start, end=end, type=event_type, channels=channels, mode=mode, note=note,
                 source='agent', status='candidate', updated_at=datetime.now(timezone.utc).isoformat())
    packet['events'].append(event)
    result = request('/api/events', packet)
    return dict(event=next(e for e in result['events'] if e['id']==event['id']), revision=result['revision'], viewer_url=BASE)


@mcp.tool(annotations=WRITE)
def update_candidate_event(file_id: str, event_id: str, start: float, end: float, event_type: str, channels: list[str], mode: str, note: str) -> dict:
    """Update an unreviewed Agent candidate only. Human/reviewed events are protected."""
    packet = read_events(file_id)
    event = next((e for e in packet['events'] if e['id']==event_id), None)
    if event is None or event['source']!='agent' or event['status']!='candidate':
        raise ValueError('仅可修改未复核的Agent候选事件')
    event.update(start=start,end=end,type=event_type,channels=channels,mode=mode,note=note,updated_at=datetime.now(timezone.utc).isoformat())
    return request('/api/events',packet)


@mcp.tool(annotations=READ)
def summarize_events(file_id: str) -> dict:
    """Counts and durations by time mode, type and review status. Does NOT calculate AHI."""
    packet = read_events(file_id)
    groups = {}
    for e in packet['events']:
        key = (e['mode'],e['type'],e['status'])
        group = groups.setdefault(key,dict(mode=e['mode'],type=e['type'],status=e['status'],count=0,total_seconds=0))
        group['count'] += 1
        group['total_seconds'] += e['end']-e['start']
    return dict(file_id=file_id,total=len(packet['events']),groups=list(groups.values()),note='人工/候选事件统计，重叠事件时长逐条相加，非临床AHI；engineering时长基于估算时间')


@mcp.tool(annotations=WRITE)
def detect_events(file_id: str, start: float = 0, end: float | None = None, min_duration: float = 10, reduction_ratio: float = .3) -> dict:
    """Scan full-resolution machine TOTAL flow and TOTAL leak, save engineering-screening candidates. Source-second times; not clinical apnea/AHI. Types include 呼吸波动减弱,总漏气升高,数据缺失,信号异常. Existing reviewed/ignored detections are retained. end=None scans to file end."""
    check(file_id)
    return request('/api/detect',dict(file_id=file_id,start=start,end=end,min_duration=min_duration,reduction_ratio=reduction_ratio))


if __name__=='__main__':
    mcp.run(transport='stdio')
