"""Isolated HTTP + real stdio MCP roundtrip; no writes to user recordings/events."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import respiratory_mcp  # also loads the project's official SDK dependencies
import asyncio
import os
import tempfile
import threading
from datetime import timedelta
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import viewer
from event_store import EventStore


async def main():
    with tempfile.TemporaryDirectory() as temp:
        viewer.recording=viewer.Recording('time,流量,pressure\n2026-01-01 00:00:00,1,2\n2026-01-01 00:00:30,2,3\n'.encode(),'fixture.csv')
        viewer.event_store=EventStore(Path(temp)/'events.db')
        server=viewer.ThreadingHTTPServer(('127.0.0.1',0),viewer.Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        params=StdioServerParameters(command=sys.executable,args=[str(Path(viewer.ROOT)/'respiratory_mcp.py')],env={**os.environ,'RESPIRATORY_VIEWER_URL':f'http://127.0.0.1:{server.server_port}'})
        try:
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=20)) as session:
                    await session.initialize()
                    tools=await session.list_tools();assert len(tools.tools)==8
                    async def call(name,args):
                        result=await session.call_tool(name,args)
                        assert not result.isError,result
                        import json
                        return result.structuredContent or json.loads(result.content[0].text)
                    meta=await call('get_current_recording',{});fid=meta['file_id']
                    await call('get_waveform',dict(file_id=fid,start=0,end=10,channels=['流量'],mode='raw'))
                    created=await call('create_event',dict(file_id=fid,start=1,end=12,event_type='疑似暂停',channels=['流量'],mode='raw',note='isolated MCP smoke test'))
                    assert created['event']['source']=='agent' and created['event']['status']=='candidate'
                    assert viewer.event_store.read(fid)['events'][0]['id']==created['event']['id']
                    summary=await call('summarize_events',dict(file_id=fid));assert summary['total']==1
                    await call('update_candidate_event',dict(file_id=fid,event_id=created['event']['id'],start=2,end=13,event_type='波形异常',channels=['流量'],mode='raw',note='updated'))
                    packet=viewer.event_store.read(fid);packet['events'][0]['status']='reviewed';viewer.event_store.write(meta,packet['revision'],packet['events'])
                    before=viewer.event_store.read(fid)
                    analysis=await call('analyze_recording',dict(file_id=fid))
                    assert analysis['read_only'] and analysis['clinical_classification']['OA'] is None
                    assert viewer.event_store.read(fid)==before
                    denied=await session.call_tool('update_candidate_event',dict(file_id=fid,event_id=created['event']['id'],start=3,end=14,event_type='其他',channels=['流量'],mode='raw',note='must fail'))
                    assert denied.isError
                    stale=await session.call_tool('list_events',dict(file_id='wrong-file'));assert stale.isError
                    scan=await call('detect_events',dict(file_id=fid));assert scan['added']>=1
                    repeat=await call('detect_events',dict(file_id=fid));assert repeat['added']==0
                    packet=viewer.event_store.read(fid)
                    detected=next(e for e in packet['events'] if e['source']=='detector');detected['status']='ignored'
                    viewer.event_store.write(meta,packet['revision'],packet['events'])
                    repeat=await call('detect_events',dict(file_id=fid));assert repeat['added']==0
                    assert next(e for e in viewer.event_store.read(fid)['events'] if e['source']=='detector')['status']=='ignored'
                    device=await call('detect_events',dict(file_id=fid,profile='device_candidates',reduction_ratio=.7))
                    assert device['settings']['profile']=='device_candidates'
                    assert device['device_metrics']['central_apnea_duration_seconds'] is None
                    assert device['device_metrics']['ahi'] is None
                    automatic=viewer.event_store.feedback(meta,device['packet']['run_id'],'automatic')
                    assert automatic['device_metrics']==device['device_metrics']
                    subset=await call('detect_events',dict(file_id=fid,profile='device_candidates',reduction_ratio=.7,event_types=['数据缺失']))
                    assert subset['settings']['event_types']==['数据缺失']
                    assert subset['device_metrics']['by_type']['流量波动减弱候选']['count'] is None
                    automatic=viewer.event_store.feedback(meta,subset['packet']['run_id'],'automatic')
                    assert all(e['type']=='数据缺失' for e in automatic['events'])
                    from test_company_rules import fixture as company_fixture
                    viewer.recording=company_fixture()
                    cm=await call('get_current_recording',{});cf=cm['file_id']
                    analysis=await call('analyze_recording',dict(file_id=cf,profile='company_rules'))
                    assert analysis['counts']['公司暂停候选']==1 and analysis['read_only']
                    assert viewer.event_store.read(cf)['events']==[]
                    company=await call('detect_events',dict(file_id=cf,profile='company_rules'))
                    assert company['settings']['min_duration']==7
                    assert company['settings']['profile']=='company_rules'
                    before=viewer.event_store.read(cf)
                    respiratory_mcp.BASE=f'http://127.0.0.1:{server.server_port}'
                    matched=respiratory_mcp.request('/api/scans/match',dict(file_id=cf,profile='company_rules'))
                    assert matched['match']['run_id']==company['packet']['run_id']
                    snap=viewer.event_store.feedback(cm,company['packet']['run_id'],'automatic')
                    assert snap['settings']['coefficients']==company['settings']['coefficients']
                    assert all(e['status']=='candidate' for e in snap['events'])
                    assert viewer.event_store.read(cf)==before
                    original=company_fixture().raw_csv.decode().splitlines()
                    viewer.recording=viewer.Recording(('\n'.join(line+(',流量' if i==0 else ',2') for i,line in enumerate(original))).encode(),'two-inputs.csv')
                    dual=await call('get_current_recording',{});dfid=dual['file_id']
                    pressure_run=await call('detect_events',dict(file_id=dfid,profile='company_rules',flow_source='pressure_q'))
                    flow_run=await call('detect_events',dict(file_id=dfid,profile='company_rules',flow_source='csv_flow'))
                    assert pressure_run['added']==1 and flow_run['added']==1
                    assert flow_run['settings']['flow_source']=='csv_flow'
                    assert len(viewer.event_store.read(dfid)['events'])==2
                    repeat=await call('detect_events',dict(file_id=dfid,profile='company_rules',flow_source='csv_flow'));assert repeat['added']==0
                    match=respiratory_mcp.request('/api/scans/match',dict(file_id=dfid,profile='company_rules',flow_source='pressure_q'))
                    assert match['match']['run_id']==pressure_run['packet']['run_id']
                    snapshot=viewer.event_store.feedback(dual,flow_run['packet']['run_id'],'automatic')
                    assert snapshot['events'][0]['detection_input']=='csv_flow'
                    from test_adaptive_test import fixture as adaptive_fixture
                    viewer.recording=adaptive_fixture();am=await call('get_current_recording',{});af=am['file_id']
                    before=viewer.event_store.read(af)
                    analysis=await call('analyze_recording',dict(file_id=af,profile='adaptive_test'))
                    assert analysis['read_only'] and analysis['counts']['测试近消失候选']==1
                    assert viewer.event_store.read(af)==before
                    adaptive=await call('detect_events',dict(file_id=af,profile='adaptive_test'))
                    assert adaptive['added']==1 and adaptive['settings']['reduction_ratio']==.5 and adaptive['settings']['min_duration']==7
                    match=respiratory_mcp.request('/api/scans/match',dict(file_id=af,profile='adaptive_test'))
                    assert match['match']['run_id']==adaptive['packet']['run_id']
                    again=await call('detect_events',dict(file_id=af,profile='adaptive_test'));assert again['added']==0
                    snapshot=viewer.event_store.feedback(am,adaptive['packet']['run_id'],'automatic')
                    assert snapshot['events'][0]['detection_profile']=='adaptive_test'
                    assert snapshot['settings']['boundary_reference'].startswith('preceding5s')
                    before=viewer.event_store.read(af)
                    analysis=await call('analyze_recording',dict(file_id=af,profile='adaptive_multifeature'))
                    assert analysis['read_only'] and analysis['counts']['测试近消失候选']==1
                    assert viewer.event_store.read(af)==before
                    multi=await call('detect_events',dict(file_id=af,profile='adaptive_multifeature'))
                    assert multi['added']==1 and multi['settings']['min_duration']==7 and multi['settings']['reduction_ratio']==.5
                    match=respiratory_mcp.request('/api/scans/match',dict(file_id=af,profile='adaptive_multifeature'))
                    assert match['match']['run_id']==multi['packet']['run_id']
                    snapshot=viewer.event_store.feedback(am,multi['packet']['run_id'],'automatic')
                    original=snapshot['events'][0];assert original['evidence']['remaining_ratio_max']<=.1
                    packet=viewer.event_store.read(af)
                    next(e for e in packet['events'] if e['id']==original['id']).update(status='reviewed',note='human v3 review')
                    viewer.event_store.write(am,packet['revision'],packet['events'])
                    again=await call('detect_events',dict(file_id=af,profile='adaptive_multifeature'));assert again['added']==0
                    preserved=next(e for e in again['packet']['events'] if e['id']==original['id'])
                    assert preserved['status']=='reviewed' and preserved['note']=='human v3 review'
                    assert preserved['evidence']==original['evidence']
                    channels=['多特征幅度A（5秒鲁棒）','多特征参考B','多特征近消失门限','多特征减弱门限']
                    wave=await call('get_waveform',dict(file_id=af,start=60,end=130,channels=channels,mode='raw'))
                    assert all(any(p[1] is not None for p in wave['series'][c]) for c in channels)
                    custom=await call('get_waveform',dict(file_id=af,start=55,end=65,channels=channels,mode='raw',test_ratio=.7))
                    reference={p[2]:p[1] for p in custom['series'][channels[1]] if p[1] is not None}
                    threshold={p[2]:p[1] for p in custom['series'][channels[3]] if p[1] is not None}
                    common=set(reference)&set(threshold);assert common
                    assert all(abs(threshold[k]-reference[k]*.7)<1e-7 for k in common)
                    dynamic=await call('get_waveform',dict(file_id=af,start=60,end=130,channels=['动态2秒强度','动态2秒参考','动态2秒25%门限','动态2秒剩余比例'],mode='raw',dynamic_method='centered_rms'))
                    assert dynamic['dynamic_comparison_method']=='centered_rms'
                    assert all(any(p[1] is not None for p in points) for points in dynamic['series'].values())
                    before=viewer.event_store.read(af)
                    baseline=await call('analyze_recording',dict(file_id=af,profile='dynamic_baseline',dynamic_method='centered_rms',window_seconds=2))
                    assert baseline['read_only'] and baseline['counts']['强度显著下降候选']==1
                    assert viewer.event_store.read(af)==before
                    baseline=await call('detect_events',dict(file_id=af,profile='dynamic_baseline',dynamic_method='centered_rms',window_seconds=2))
                    assert baseline['added']==1 and baseline['settings']['min_duration']==10
                    be=next(e for e in baseline['packet']['events'] if e.get('detection_profile')=='dynamic_baseline')
                    assert be['detection_method']=='centered_rms' and be['detection_window_seconds']==2 and be['evidence']['schema']==1
                    again=await call('detect_events',dict(file_id=af,profile='dynamic_baseline',dynamic_method='centered_rms',window_seconds=2))
                    assert again['added']==0
                    alternative=await call('detect_events',dict(file_id=af,profile='dynamic_baseline',dynamic_method='centered_rms',window_seconds=8))
                    assert alternative['added']==1
                    five=await call('detect_events',dict(file_id=af,profile='dynamic_baseline',dynamic_method='centered_rms',window_seconds=5))
                    assert five['added']==1 and five['settings']['window_seconds']==5
                    five_wave=await call('get_waveform',dict(file_id=af,start=60,end=130,channels=['动态5秒强度','动态5秒参考','动态5秒剩余比例'],dynamic_method='centered_rms'))
                    assert five_wave['dynamic_comparison_method']=='centered_rms'
                    assert all(any(p[1] is not None for p in points) for points in five_wave['series'].values())
                    before=viewer.event_store.read(af)
                    optimized=await call('analyze_recording',dict(file_id=af,profile='dynamic_baseline_v2'))
                    assert optimized['read_only'] and optimized['settings']['method']=='detrended_abs' and optimized['settings']['window_seconds']==5
                    assert viewer.event_store.read(af)==before
                    optimized=await call('detect_events',dict(file_id=af,profile='dynamic_baseline_v2'))
                    assert optimized['added']>=1
                    oe=next(e for e in optimized['packet']['events'] if e.get('detection_profile')=='dynamic_baseline_v2')
                    assert oe['evidence']['boundary_duration_seconds']>=10 and oe['evidence']['main_qualified_intervals']
                    again=await call('detect_events',dict(file_id=af,profile='dynamic_baseline_v2'));assert again['added']==0
                    optimized_wave=await call('get_waveform',dict(file_id=af,start=60,end=140,channels=['优化5秒强度','优化5秒参考','优化5秒1秒复核强度','优化5秒背景估计'],dynamic_method='centered_rms',optimized_method='detrended_abs'))
                    assert optimized_wave['optimized_method']=='detrended_abs'
                    assert all(any(p[1] is not None for p in points) for points in optimized_wave['series'].values())
                    before_v3=viewer.event_store.read(af)
                    v3=await call('analyze_recording',dict(file_id=af,profile='dynamic_baseline_v3'))
                    assert v3['read_only'] and v3['settings']['version']=='dynamic-baseline-optimized-v3-configurable-v13'
                    assert viewer.event_store.read(af)==before_v3
                    v3=await call('detect_events',dict(file_id=af,profile='dynamic_baseline_v3'))
                    assert v3['added']>0
                    v3_event=next(e for e in v3['packet']['events'] if e.get('detection_profile')=='dynamic_baseline_v3')
                    assert v3_event['evidence']['leak_policy']=='review_only_no_reference_reset'
                    repeat_v3=await call('detect_events',dict(file_id=af,profile='dynamic_baseline_v3'));assert repeat_v3['added']==0
                    v3_wave=await call('get_waveform',dict(file_id=af,start=60,end=140,channels=['优化v35秒强度','优化v35秒参考'],optimized_method='detrended_abs'))
                    assert v3_wave['optimized_method']=='detrended_abs'
                    assert all(any(p[1] is not None for p in points) for points in v3_wave['series'].values())
                    flexible=await call('analyze_recording',dict(file_id=af,profile='dynamic_baseline_v3',window_seconds=10,min_duration=5))
                    assert flexible['settings']['window_seconds']==10 and flexible['settings']['min_duration']==5
                    flexible=await call('detect_events',dict(file_id=af,profile='dynamic_baseline_v3',window_seconds=10,min_duration=5))
                    assert flexible['added']>0
                    flexible_wave=await call('get_waveform',dict(file_id=af,start=60,end=140,channels=['优化v310秒强度','优化v310秒参考'],optimized_method='detrended_abs',optimized_duration=5))
                    assert flexible_wave['optimized_duration']==5
                    assert all(any(p[1] is not None for p in points) for points in flexible_wave['series'].values())
                    print('PASS: 8 MCP tools, optimized baseline, immutable timing evidence, separate versions/windows, wave traces and protected annotations',flush=True)
        finally:
            server.shutdown();server.server_close()


if __name__=='__main__':asyncio.run(main())
