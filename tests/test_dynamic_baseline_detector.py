import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import build_opener,ProxyHandler,Request

import viewer
from detector import detect
from dynamic_baseline_detector import settings,VERSION
from dynamic_baseline_web import analyze
from event_store import EventStore,scan_fingerprint,validate_event
from signal_analysis import analyze_recording
from test_adaptive_test import fixture


class DynamicDetectorTests(unittest.TestCase):
    def test_saved_engine_equals_comparison_and_scope_does_not_crop(self):
        r=fixture();result=detect(r,profile='dynamic_baseline')
        expected=analyze(r,method='centered_rms',window=2)['events']
        self.assertEqual([(e['start'],e['end'],e['type']) for e in result['events']],[(e['start'],e['end'],e['type']) for e in expected])
        self.assertTrue(result['events'])
        e=result['events'][0]
        scoped=detect(r,e['start']+2,e['end']-2,profile='dynamic_baseline')['events'][0]
        self.assertEqual(scoped['id'],e['id']);self.assertEqual(scoped['start'],e['start']);self.assertEqual(scoped['end'],e['end'])
        self.assertEqual(e['evidence']['confirmed_at'],e['start']+10)
        self.assertLess(e['evidence']['remaining_ratio_max'],.25)
        self.assertEqual(e['detection_method'],'centered_rms');self.assertEqual(e['detection_window_seconds'],2)
        self.assertFalse(result['settings']['patient_flow_verified']);self.assertFalse(result['settings']['leak_subtraction'])
        self.assertEqual(validate_event(e,r.meta),e)
        json.dumps(result,allow_nan=False)
        self.assertEqual(detect(r,profile='dynamic_baseline',dynamic_method='absolute_mean')['events'],[], 'DC machine background can hide events; never call no detection normal')

    def test_configuration_identity_and_fixed_rules_are_explicit(self):
        r=fixture();two=detect(r,profile='dynamic_baseline');eight=detect(r,profile='dynamic_baseline',window_seconds=8)
        self.assertTrue(eight['events'])
        self.assertTrue({e['id'] for e in two['events']}.isdisjoint(e['id'] for e in eight['events']))
        limited=detect(r,profile='dynamic_baseline',event_types=['强度显著下降候选'])
        self.assertEqual(two['events'][0]['id'],limited['events'][0]['id'])
        five=detect(r,profile='dynamic_baseline',window_seconds=5)
        self.assertTrue(five['events'])
        self.assertTrue({e['id'] for e in five['events']}.isdisjoint(e['id'] for e in two['events']+eight['events']))
        self.assertEqual(five['settings']['min_duration'],10)
        configs=[settings(),settings(window_seconds=5),settings(window_seconds=8),settings(dynamic_method='absolute_mean')]
        self.assertEqual(len({scan_fingerprint(0,220,c) for c in configs}),4)
        for kw in (dict(window_seconds='both'),dict(window_seconds=True),dict(dynamic_method='patient_flow'),dict(min_duration=7),dict(min_duration=None),dict(event_types=['疑似暂停'])):
            with self.assertRaises(ValueError):settings(**kw)

    def test_gap_not_bridged_and_readonly_agent_analysis_retains_evidence(self):
        r=fixture(gap=(95,98))
        result=detect(r,profile='dynamic_baseline')
        self.assertTrue(any(e['type']=='数据缺失' for e in result['events']))
        self.assertFalse(any(e['start']<95<98<e['end'] for e in result['events'] if e['type'].startswith('强度')))
        evidence=analyze_recording(fixture(),profile='dynamic_baseline',window_seconds=8)
        self.assertTrue(evidence['read_only']);self.assertEqual(evidence['settings']['window_seconds'],8)
        self.assertIsNone(evidence['clinical_classification']['apnea'])

    def test_http_saves_review_and_snapshots_without_cross_configuration_dedup(self):
        original=viewer.recording,viewer.event_store
        with tempfile.TemporaryDirectory() as temp:
            r=fixture();viewer.recording=r;store=viewer.event_store=EventStore(Path(temp)/'events.db');fid=r.meta['file_id']
            human=dict(id='human',start=4.,end=14.,mode='raw',channels=['流量'],type='其他',source='manual',status='reviewed',note='keep',updated_at='now')
            store.write(r.meta,0,[human]);before=store.read(fid)
            server=viewer.ThreadingHTTPServer(('127.0.0.1',0),viewer.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();client=build_opener(ProxyHandler({}))
            def request(path,body=None):
                data=None if body is None else json.dumps(body).encode()
                with client.open(Request(f'http://127.0.0.1:{server.server_port}'+path,data=data,headers={'Content-Type':'application/json'}),timeout=20) as response:return json.load(response)
            args=dict(file_id=fid,profile='dynamic_baseline',dynamic_method='centered_rms',window_seconds=2,start=0,end=r.meta['raw_duration'],min_duration=10)
            try:
                readonly=request('/api/analyze',args);self.assertTrue(readonly['read_only']);self.assertEqual(store.read(fid),before)
                self.assertIsNone(request('/api/scans/match',args)['match'])
                result=request('/api/detect',args);self.assertGreater(result['added'],0)
                self.assertEqual(store.read(fid)['events'][0],human)
                e=next(e for e in result['packet']['events'] if e['source']=='detector');automatic=dict(e)
                self.assertEqual(e['detection_version'],VERSION)
                self.assertIsNotNone(request('/api/scans/match',args)['match'])
                packet=store.read(fid)
                for item in packet['events']:
                    if item['id']==e['id']:item.update(status='reviewed',start=item['start']+1,type='其他',note='人工修改')
                reviewed=request('/api/events',packet)
                changed=next(item for item in reviewed['events'] if item['id']==e['id'])
                self.assertEqual(changed['evidence'],automatic['evidence']);self.assertEqual(changed['detection_method'],'centered_rms')
                again=request('/api/detect',args);self.assertEqual(again['added'],0);self.assertGreater(again['skipped'],0)
                self.assertEqual(next(item for item in store.read(fid)['events'] if item['id']==e['id']),changed)
                feedback=store.feedback(r.meta,result['packet']['run_id'],'automatic')
                self.assertEqual(feedback['events'][0],automatic,'automatic snapshot must remain original after human review')
                alternate=dict(args,window_seconds=8)
                self.assertIsNone(request('/api/scans/match',alternate)['match'])
                self.assertGreater(request('/api/detect',alternate)['added'],0)
                five_args=dict(args,window_seconds=5)
                self.assertIsNone(request('/api/scans/match',five_args)['match'])
                five=request('/api/detect',five_args);self.assertGreater(five['added'],0)
                five_event=next(item for item in five['packet']['events'] if item.get('detection_window_seconds')==5)
                self.assertEqual(five_event['evidence']['window_seconds'],5)
                self.assertEqual(request('/api/detect',five_args)['added'],0)
                self.assertEqual(next(item for item in store.read(fid)['events'] if item['id']==e['id']),changed)
                scope=dict(file_id=fid,run_id=result['packet']['run_id'],reviewer='tester',reviewed_range=[0,r.meta['raw_duration']],reviewed_types=['强度显著下降候选','其他'],review_mode='spot')
                reviewed_json=request('/api/feedback',dict(scope,kind='reviewed'))
                self.assertFalse(reviewed_json['training_scope']['background_labels_allowed'])
                self.assertEqual({item['id'] for item in reviewed_json['events']},{human['id'],changed['id']},'8s comparison must not leak into the selected 2s review batch')
                checked=next(item for item in reviewed_json['events'] if item['id']==changed['id'])
                self.assertEqual(checked['evidence'],automatic['evidence']);self.assertEqual(checked['detection_window_seconds'],2)
                complete=request('/api/feedback',dict(scope,kind='reviewed',review_mode='complete'))
                self.assertTrue(complete['training_scope']['background_labels_allowed'],'pending 8s candidates cannot block completed 2s review')
                preview=request('/api/feedback/prepare',scope)
                exported=request('/api/feedback/export',dict(scope,expected_revision=preview['revision'],confirmed=True))
                folder=Path(exported['folder'])
                self.assertEqual((folder/'original.csv').read_bytes(),r.raw_csv)
                exported_review=json.loads(next((folder/'reviewed').glob('*.json')).read_text(encoding='utf-8'))
                self.assertEqual(exported_review,reviewed_json)
                repeat=request('/api/feedback/export',dict(scope,expected_revision=preview['revision'],confirmed=True))
                self.assertEqual(repeat['created'],[])
                five_json=request('/api/feedback',dict(scope,kind='reviewed',run_id=five['packet']['run_id']))
                self.assertEqual({item['detection_window_seconds'] for item in five_json['events'] if item['source']=='detector'},{5})
                saved=store.read(fid)
                for invalid,status in ((dict(args,window_seconds='both'),400),(dict(args,min_duration=7),400),(dict(args,file_id='stale'),409)):
                    with self.assertRaises(HTTPError) as error:request('/api/detect',invalid)
                    self.assertEqual(error.exception.code,status)
                self.assertEqual(store.read(fid),saved)
                real=viewer.detect
                def switch(*a,**kw):
                    result=real(*a,**kw);viewer.recording=None;return result
                with patch.object(viewer,'detect',switch):
                    with self.assertRaises(HTTPError) as error:request('/api/detect',args)
                    self.assertEqual(error.exception.code,409)
                self.assertEqual(store.read(fid),saved)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
                viewer.recording,viewer.event_store=original


if __name__=='__main__':unittest.main()
