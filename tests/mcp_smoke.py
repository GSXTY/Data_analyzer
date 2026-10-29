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
                    tools=await session.list_tools();assert len(tools.tools)==7
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
                    print('PASS: stdio + 7 tools + shared events + automatic scan + duplicate/ignored preservation + reviewed/file-switch protection',flush=True)
        finally:
            server.shutdown();server.server_close()


if __name__=='__main__':asyncio.run(main())
