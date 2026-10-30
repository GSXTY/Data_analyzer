"""Verify the Windows EXE independently with synthetic data and temporary storage."""
import json
import math
import runpy
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime,timedelta


def main():
    exe=Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as temp:
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        process=subprocess.Popen([str(exe),'--port',str(port),'--no-browser','--data-dir',temp],cwd=temp,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path,packet=None,raw=None):
            data=raw if raw is not None else (json.dumps(packet,ensure_ascii=False).encode() if packet is not None else None)
            req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=data,headers={'Content-Type':'application/json','X-Filename':'synthetic.csv'})
            with client.open(req,timeout=20) as r:return json.load(r)
        try:
            deadline=time.monotonic()+45
            while True:
                try:health=request('/api/health');break
                except (OSError,urllib.error.URLError):
                    if process.poll() is not None or time.monotonic()>deadline:raise RuntimeError('EXE startup failed: '+process.communicate(timeout=5)[1].decode(errors='replace'))
                    time.sleep(.2)
            assert health['app_version']==runpy.run_path(str(Path(__file__).resolve().parents[1]/'app_version.py'))['APP_VERSION']
            with client.open(f'http://127.0.0.1:{port}/') as r:assert 'export-automatic' in r.read().decode()
            rows=['time,流量,漏气量,压力,运行状态']
            for i in range(150*25):
                sec=i/25;stamp=(datetime(2026,1,1)+timedelta(seconds=sec)).strftime('%Y-%m-%d %H:%M:%S.%f')
                amplitude=.4 if 80<=sec<102 else 20
                rows.append(f'{stamp},{120+amplitude*math.sin(sec*math.pi/2)},10,400,3')
            meta=request('/api/open',raw='\n'.join(rows).encode());fid=meta['file_id']
            scan=request('/api/detect',dict(file_id=fid));assert scan['added']>=1
            run=scan['packet']['run_id'];automatic=request('/api/feedback',dict(file_id=fid,run_id=run,kind='automatic'))
            packet=scan['packet'];packet['events'][0]['status']='reviewed';packet['events'][0]['note']='synthetic review'
            request('/api/events',packet)
            reviewed=request('/api/feedback',dict(file_id=fid,run_id=run,kind='reviewed',reviewer='package-test',reviewed_range=[0,meta['raw_duration']]))
            assert automatic['events'][0]['status']=='candidate'
            assert reviewed['automatic']['events'][0]['status']=='candidate'
            assert reviewed['events'][0]['status']=='reviewed'
            assert reviewed['history'][-1]['after'][0]['note']=='synthetic review'
            device=request('/api/detect',dict(file_id=fid,profile='device_candidates',reduction_ratio=.7,event_types=['流量波动近消失候选']))
            assert device['settings']['event_types']==['流量波动近消失候选']
            assert device['device_metrics']['by_type']['流量波动减弱候选']['count'] is None
            packet=device['packet'];near=[e for e in packet['events'] if e['type']=='流量波动近消失候选'];assert near
            for event in near:event['status']='reviewed'
            request('/api/events',packet)
            args=dict(file_id=fid,run_id=packet['run_id'],reviewer='package-test',reviewed_range=[70,110],reviewed_types=['流量波动近消失候选'],review_mode='complete')
            preview=request('/api/feedback/prepare',args);assert preview['confirmed_count']>=1
            saved=request('/api/feedback/export',dict(args,confirmed=True,expected_revision=preview['revision']))
            assert (Path(saved['folder'])/'original.csv').read_bytes()=='\n'.join(rows).encode()
            repeat=request('/api/feedback/export',dict(args,confirmed=True,expected_revision=preview['revision']));assert not repeat['created']
            page=request('/api/scans?file_id='+fid);assert len(page['scans'])<=10
            snapshot=request('/api/feedback',dict(file_id=fid,run_id=packet['run_id'],kind='automatic'))
            request('/api/scans/edit',dict(file_id=fid,run_id=packet['run_id'],archived=True))
            assert request('/api/feedback',dict(file_id=fid,run_id=packet['run_id'],kind='automatic'))==snapshot
            request('/api/scans/edit',dict(file_id=fid,run_id=packet['run_id'],archived=False))
            assert meta['channel_units']['压力']=='Pa'
            assert meta['channel_units']['流量']=='L/min' and meta['channel_units']['漏气量']=='L/min'
            assert not meta['sampling_info']['uniform_sampling_verified']
            raw_a='\n'.join(rows).encode();raw_b=raw_a.replace(b'400,3',b'401,3',1)
            other=request('/api/open',raw=raw_b);assert other['file_id']!=fid
            activated=request('/api/activate',dict(file_id=fid));assert activated['hit'] and activated['meta']['file_id']==fid
            assert request('/api/cache/release',dict(file_id=other['file_id']))['released']
            assert not request('/api/activate',dict(file_id=other['file_id']))['hit']
            prior=request('/api/events?file_id='+fid)
            assert request('/api/close',dict(file_id=fid))['closed']
            request('/api/open',raw=raw_a)
            assert request('/api/events?file_id='+fid)==prior
            print('PASS: standalone Windows EXE starts, serves UI, loads CSV, detects, preserves automatic JSON and exports reviewed JSON/history',flush=True)
            print('PASS: standalone device selection, partial-review confirmation/export dedup and batch archive/snapshot retention',flush=True)
            print('PASS: standalone cached CSV activation, release/close/reopen, annotation retention and Pa/L/min metadata',flush=True)
        finally:
            process.terminate();process.communicate(timeout=15)
if __name__=='__main__':main()
