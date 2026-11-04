import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import urlencode
from urllib.request import build_opener,ProxyHandler,Request
from urllib.error import HTTPError

import numpy as np
import viewer
from dynamic_baseline_web import analyze,waveform_channels,names,VERSION
from dynamic_baseline_comparison import compare
from event_store import EventStore
from test_adaptive_test import fixture


class DynamicWebTests(unittest.TestCase):
    def test_web_matches_offline_and_keeps_full_scoped_boundaries(self):
        r=fixture();source,trials=compare(r)
        result=analyze(r,method='centered_rms')
        expected=sum(len(t['events']) for t in trials if t['method']=='centered_rms' and t['window_seconds'] in (2,8))
        self.assertEqual(len(result['events']),expected)
        self.assertTrue(result['read_only']);self.assertFalse(result['settings']['patient_flow_verified'])
        self.assertEqual(result['settings']['min_duration'],10)
        e=result['events'][0];scoped=analyze(r,e['start']+2,e['end']-2,method='centered_rms',window=e['window_seconds'])
        same=next(p for p in scoped['events'] if p['id']==e['id'])
        self.assertEqual((same['start'],same['end']),(e['start'],e['end']))
        self.assertIn('跨选区，保留完整候选边界',same['review_flags'])
        self.assertEqual({e['window_seconds'] for e in analyze(r,method='centered_rms',window=8)['events']},{8})
        five=analyze(r,method='centered_rms',window=5)
        self.assertEqual({e['window_seconds'] for e in five['events']},{5})
        self.assertEqual([t['window_seconds'] for t in result['by_window']],[2,8],'legacy both stays 2+8')
        self.assertIsNone(result['events'][0]['clinical_classification'])
        json.dumps(result,allow_nan=False)
        for kwargs in (dict(method='patient_flow'),dict(window=True),dict(window='2'),dict(start=-1),dict(end=float('nan'))):
            with self.assertRaises(ValueError):analyze(r,**kwargs)

    def test_waveform_uses_same_method_reference_and_cache(self):
        r=fixture();result=analyze(r,method='centered_rms',window=2)
        e=result['events'][0];keys=names(2);waves=waveform_channels(r,keys,'centered_rms')
        offset=np.searchsorted(r.x,e['start'])
        self.assertAlmostEqual(waves[keys[2]][offset],waves[keys[1]][offset]*.25)
        self.assertAlmostEqual(waves[keys[3]][offset],waves[keys[1]][offset]*.5)
        self.assertAlmostEqual(waves[keys[4]][offset],waves[keys[0]][offset]/waves[keys[1]][offset]*100)
        cache=r._dynamic_baseline_comparison
        waveform_channels(r,keys,'absolute_mean')
        self.assertIs(cache,r._dynamic_baseline_comparison)

    def test_http_read_only_and_file_switch_guard_and_method_specific_waves(self):
        original=viewer.recording,viewer.event_store
        with tempfile.TemporaryDirectory() as temp:
            r=fixture();viewer.recording=r;viewer.event_store=EventStore(Path(temp)/'events.db')
            fid=r.meta['file_id']
            mark=dict(id='reviewed',start=5.,end=15.,mode='raw',channels=['流量'],type='其他',source='manual',status='reviewed',note='keep',updated_at='now')
            viewer.event_store.write(r.meta,0,[mark]);before=viewer.event_store.read(fid)
            server=viewer.ThreadingHTTPServer(('127.0.0.1',0),viewer.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=build_opener(ProxyHandler({}))
            def request(path,body=None):
                req=Request(f'http://127.0.0.1:{server.server_port}'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                with client.open(req,timeout=20) as response:return json.load(response)
            try:
                history=request('/api/scans?'+urlencode(dict(file_id=fid)))
                result=request('/api/dynamic-baseline',dict(file_id=fid,method='centered_rms',window='both'))
                self.assertEqual(result['version'],VERSION)
                self.assertEqual(viewer.event_store.read(fid),before)
                self.assertEqual(request('/api/scans?'+urlencode(dict(file_id=fid))),history)
                wave=request('/api/wave?'+urlencode(dict(file_id=fid,start=60,end=130,pixels=1000,mode='raw',channel=names(2),dynamic_method='centered_rms'),doseq=True))
                self.assertTrue(all(any(p[1] is not None for p in wave['series'][key]) for key in names(2)))
                five=request('/api/dynamic-baseline',dict(file_id=fid,method='centered_rms',window=5))
                self.assertEqual([t['window_seconds'] for t in five['by_window']],[5])
                wave=request('/api/wave?'+urlencode(dict(file_id=fid,start=60,end=130,pixels=1000,mode='raw',channel=names(5),dynamic_method='centered_rms'),doseq=True))
                self.assertTrue(all(any(p[1] is not None for p in wave['series'][key]) for key in names(5)))
                self.assertEqual(viewer.event_store.read(fid),before)
                for args,status in ((dict(file_id='stale'),409),(dict(file_id=fid,method='invalid'),400)):
                    with self.assertRaises(HTTPError) as error:request('/api/dynamic-baseline',args)
                    self.assertEqual(error.exception.code,status)
                real=viewer.dynamic_analyze
                def switch(*args,**kwargs):
                    result=real(*args,**kwargs);viewer.recording=None;return result
                with patch.object(viewer,'dynamic_analyze',switch):
                    with self.assertRaises(HTTPError) as error:request('/api/dynamic-baseline',dict(file_id=fid))
                    self.assertEqual(error.exception.code,409)
                self.assertEqual(viewer.event_store.read(fid),before)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
                viewer.recording,viewer.event_store=original


if __name__=='__main__':unittest.main()
