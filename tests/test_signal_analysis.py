import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
import numpy as np
import viewer
from event_store import EventStore
from signal_analysis import analyze_recording
from test_detector import fixture


class SignalAnalysisTests(unittest.TestCase):
    def test_short_reference_disagreement_is_flagged_not_clinically_classified(self):
        r=fixture()
        t=r.x
        amplitude=np.select([(t>=90)&(t<110),(t>=113)&(t<145)],[1.,4.],default=20.)
        r.channels['流量']=120+.12*t+amplitude*np.sin(2*np.pi*t/4)
        out=analyze_recording(r)
        flagged=[e for e in out['representative_events'] if any('紧邻前30秒幅度未降低' in f for f in e['review_flags'])]
        self.assertTrue(flagged)
        self.assertGreater(flagged[0]['per_second_amplitude_during'],flagged[0]['per_second_amplitude_before'])
        self.assertIsNone(out['clinical_classification']['hypopnea'])

    def test_complete_rows_and_machine_evidence_without_clinical_labels(self):
        r=fixture(leak=True,gap=True)
        original=r.channels['流量'].copy()
        result=analyze_recording(r,max_examples=1)
        self.assertEqual(result['counts']['流量波动近消失候选'],1)
        self.assertEqual(result['counts']['总漏气升高'],1)
        event=result['representative_events'][0]
        self.assertTrue(80<=event['start']<=84)
        self.assertLess(event['per_second_amplitude_during'],event['per_second_amplitude_before']*.1)
        self.assertEqual(event['before_30s_medians']['漏气量'],10)
        self.assertEqual(event['changes']['压力'],0)
        self.assertTrue(all(value is None for key,value in result['clinical_classification'].items() if key!='reason'))
        self.assertIsNone(result['device_metrics']['ahi'])
        self.assertEqual(sum(h['candidate_count_by_start'] for h in result['hourly']),1)
        np.testing.assert_array_equal(r.channels['流量'],original)
        json.dumps(result,allow_nan=False)
        for limit in (0,51,True,'10'):
            with self.assertRaises(ValueError):analyze_recording(r,max_examples=limit)

    def test_http_analysis_preserves_events_and_scan_history_and_rejects_stale_file(self):
        previous=viewer.recording,viewer.event_store
        with tempfile.TemporaryDirectory() as temp:
            viewer.recording=fixture();viewer.event_store=EventStore(Path(temp)/'events.db')
            meta=viewer.recording.meta;fid=meta['file_id']
            mark=dict(id='human',start=90,end=95,type='其他',channels=['流量'],mode='raw',source='manual',status='reviewed',note='human judgement',updated_at='2026-01-01T00:00:00+00:00')
            viewer.event_store.write(meta,0,[mark],scan=dict(events=[],settings={'version':'fixture'},scan_start=0,scan_end=100))
            before=viewer.event_store.read(fid)
            server=viewer.ThreadingHTTPServer(('127.0.0.1',0),viewer.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path,body=None):
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json'})
                with client.open(req,timeout=10) as response:return json.load(response)
            try:
                history=request('/api/scans?file_id='+fid)
                result=request('/api/analyze',dict(file_id=fid))
                self.assertTrue(result['read_only']);self.assertEqual(result['file_id'],fid)
                self.assertEqual(viewer.event_store.read(fid),before)
                self.assertEqual(request('/api/scans?file_id='+fid),history)
                for payload,status in ((dict(file_id='stale'),409),(dict(file_id=fid,start=-1),400),(dict(file_id=fid,max_examples=51),400)):
                    with self.assertRaises(urllib.error.HTTPError) as error:request('/api/analyze',payload)
                    self.assertEqual(error.exception.code,status)
                original=viewer.analyze_recording
                def switch(*args,**kwargs):
                    out=original(*args,**kwargs);viewer.recording=None;return out
                with patch.object(viewer,'analyze_recording',switch):
                    with self.assertRaises(urllib.error.HTTPError) as error:request('/api/analyze',dict(file_id=fid))
                    self.assertEqual(error.exception.code,409)
                self.assertEqual(viewer.event_store.read(fid),before)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
                viewer.recording,viewer.event_store=previous
