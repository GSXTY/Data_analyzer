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
def analyze_recording(file_id: str,start: float=0,end: float | None=None,max_examples: int=10,profile: str='device_candidates',flow_source: str='pressure_q',reduction_ratio: float | None=None,dynamic_method: str | None=None,window_seconds: int | None=None,min_duration: int | None=None) -> dict:
    """Read-only analysis. dynamic_baseline_v3: detrended_abs/5s and min_duration=10 defaults; window_seconds=1..10 and min_duration=5..10 integers; strong=1s review<25% for min_duration plus main support; weak=selected main-window<50% continuously for min_duration (no per-second requirement), window-end timing. Total-leak changes are review hints, do not reset references. Old v2 stays separate. dynamic_baseline_v2 defaults to detrended_abs/5s (also centered_rms/absolute_mean,2|5|8): causal30s trimmed background,100s reference, freeze at suspicion, rollback rejected valid dips;3s pretrigger,1s review bins continuously>=10s plus main-window support. Estimated boundaries and main qualified intervals are separate, not fixed backdating. No patient flow/clinical scoring. dynamic_baseline: dynamic_method=centered_rms|absolute_mean, window_seconds=2|5|8 (one only), fixed10s, previous100s mean with30 valid seconds, freeze at remaining<50%, strict<25% strong/<50% weak; unverified CSV machine total flow, not patient flow or manufacturer code. adaptive_multifeature: independent v3 strict10%/50%,7s, uncalibrated noise evidence. adaptive_test retains v2 floor. company_rules retains fixed5s ranges and10/17 thresholds. No clinical classification or writes."""
    check(file_id)
    return request('/api/analyze',dict(file_id=file_id,start=start,end=end,max_examples=max_examples,profile=profile,flow_source=flow_source,reduction_ratio=reduction_ratio,dynamic_method=dynamic_method,window_seconds=window_seconds,min_duration=min_duration))


@mcp.tool(annotations=READ)
def get_waveform(file_id: str, start: float, end: float, channels: list[str], mode: str = 'raw', pixels: int = 400, test_duration: float=7, test_ratio: float=.5, dynamic_method: str='absolute_mean',optimized_method: str | None=None,optimized_duration: int=10) -> dict:
    """Read display-selected points, NOT analysis samples. <=1h, <=8 channels. Multi-feature traces match test_duration/test_ratio to scan settings. Dynamic2/5/8s comparison traces use dynamic_method=absolute_mean or centered_rms; these are unverified machine-flow proxies, not patient flow. Optimized traces use optimized_method=detrended_abs|centered_rms|absolute_mean (or explicit dynamic_method when optimized_method omitted); match optimized scan method. Mixed V1/V2 curves can use independent method args. Time offsets use the explicitly selected clock."""
    meta = check(file_id)
    if mode not in ('raw', 'engineering') or not 0 <= start < end or end-start > 3600 or not channels or len(channels)>8 or any(c not in meta['channels'] for c in channels):
        raise ValueError('时间/模式/通道参数无效；最多1小时、8通道')
    q = urlencode({'file_id':file_id,'start': start, 'end': end, 'channel': channels, 'mode': mode, 'pixels': max(100, min(1200,pixels)), 'test_duration':test_duration,'test_ratio':test_ratio,'dynamic_method':dynamic_method,**({'optimized_method':optimized_method} if optimized_method is not None else {}),'optimized_duration':optimized_duration}, doseq=True)
    result = request('/api/wave?' + q)
    result.update(file_id=file_id, time_basis=mode, sampled_for_display=True, viewer_url=BASE)
    if any(c.startswith(('动态2秒','动态5秒','动态8秒')) for c in channels):result['dynamic_comparison_method']=dynamic_method
    if any(c.startswith(('优化2秒','优化5秒','优化8秒','优化v3')) for c in channels):result['optimized_method']=dynamic_method if optimized_method is None else optimized_method
    if any(c.startswith('优化v3') for c in channels):result['optimized_duration']=optimized_duration
    result.update(channel_units={c:meta.get('channel_units',{}).get(c) for c in channels},
                  channel_roles={c:meta.get('channel_roles',{}).get(c) for c in channels},sampling_info=meta.get('sampling_info'))
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
def detect_events(file_id: str, start: float = 0, end: float | None = None, min_duration: float | None = None, reduction_ratio: float | None = None, profile: str = 'engineering', event_types: list[str] | None = None,flow_source: str='pressure_q',dynamic_method: str | None=None,window_seconds: int | None=None) -> dict:
    """Save candidates, never confirm. dynamic_baseline_v3 supports detrended_abs|centered_rms|absolute_mean,window_seconds=1..10,min_duration=5..10 integers (defaults5/10): weak main-window<50% continuously for selected duration, strong1s<25% with main support. Leak changes are review hints, not reference resets. Separate v3 provenance/evidence and snapshots. dynamic_baseline_v2 defaults to detrended_abs/5s,1s review>=10s plus main-window support,3s pretrigger, causal background and rejected-dip rollback; estimated/source-bin interval, main-window qualified intervals and confirmation remain separate in immutable evidence. Long episodes flagged, not automatically normal. dynamic_baseline uses dynamic_method=centered_rms|absolute_mean, window_seconds=2|5|8 (one only), fixed10s, prior100s mean/30 valid seconds; freeze at first remaining<50%, strict<25% strong/<50% weak, no backdating. Method/window have separate IDs and batches; unverified machine total flow, not Patient Flow or ResMed code. adaptive_multifeature retains v3 strict10%/50%,7s and uncalibrated noise evidence; adaptive_test retains v2. Reviewed marks protected. No device control or clinical diagnosis."""
    check(file_id)
    if min_duration is None:min_duration=7 if profile in ('company_rules','adaptive_test','adaptive_multifeature') else 10
    if reduction_ratio is None:reduction_ratio=.5 if profile in ('adaptive_test','adaptive_multifeature') else .3
    return request('/api/detect',dict(file_id=file_id,start=start,end=end,min_duration=min_duration,reduction_ratio=reduction_ratio,profile=profile,event_types=event_types,flow_source=flow_source,dynamic_method=dynamic_method,window_seconds=window_seconds))


if __name__=='__main__':
    mcp.run(transport='stdio')
