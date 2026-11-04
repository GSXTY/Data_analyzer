import json
from datetime import datetime,timedelta
import unittest
import tempfile
import threading
from pathlib import Path
from urllib.request import build_opener,ProxyHandler,Request
from urllib.parse import urlencode
from urllib.error import HTTPError
import numpy as np
import viewer
from viewer import Recording
from dynamic_baseline_v2 import compute,detect,settings,names,VERSION,waveform_channels
from event_store import validate_event,scan_fingerprint
from event_store import EventStore
from test_adaptive_test import fixture


def controlled(low=((80,90),),offset=50.,reduced=.2,end=230,gap=None,variable_density=False):
    rows=['time,流量,运行状态,治疗压']
    for sec in range(end):
        if gap and gap[0]<=sec<gap[1]:continue
        amp=reduced if any(a<=sec<b for a,b in low) else 20.
        count=60 if variable_density and sec%2 else 20
        for j in range(count):
            stamp=(datetime(2026,1,1)+timedelta(seconds=sec+j/count)).strftime('%Y-%m-%d %H:%M:%S.%f')
            rows.append(f'{stamp},{offset+(amp if j%2 else -amp)},3,400')
    return Recording('\n'.join(rows).encode(),'optimized-controlled.csv')


class OptimizedTests(unittest.TestCase):
    def test_true_ten_second_source_bins_are_recovered_without_window_compensation(self):
        for w in (2,5,8):
            r=controlled();out=detect(r,window_seconds=w)
            respiratory=[e for e in out['events'] if e['type'].startswith('强度')]
            self.assertEqual([(e['start'],e['end']) for e in respiratory],[(80.,90.)])
            e=respiratory[0];v=e['evidence']
            self.assertEqual(v['confirmed_at'],90);self.assertEqual(v['estimated_waveform_interval'],[80.,90.])
            self.assertEqual(v['boundary_duration_seconds'],10);self.assertLess(v['boundary_remaining_max'],.25)
            self.assertEqual(v['boundary_resolution_seconds'],1);self.assertTrue(v['extremely_low_strength'])
            self.assertNotEqual(v['main_qualified_intervals'],[[80.,90.]])
            self.assertEqual(validate_event(e,r.meta),e)
            json.dumps(out,allow_nan=False)
        self.assertEqual(detect(controlled(low=((80,89),)))['events'],[])
        close_events=detect(controlled(low=((80,98),(120,140))))['events']
        self.assertEqual([(e['start'],e['end']) for e in close_events],[(80.,98.),(120.,140.)],'event recovery must not impose an unnecessary 60s blind period')

    def test_background_offset_invariance_and_causal_freeze(self):
        trials=[compute(controlled(offset=x)) for x in (0,50,1000)]
        for t in trials[1:]:
            np.testing.assert_allclose(t['traces']['strength'],trials[0]['traces']['strength'],equal_nan=True,atol=1e-8)
            self.assertEqual([(e['start'],e['end']) for e in t['events']],[(e['start'],e['end']) for e in trials[0]['events']])
        density=compute(controlled(offset=0,variable_density=True))
        np.testing.assert_allclose(density['traces']['background'],trials[0]['traces']['background'],equal_nan=True,atol=1e-8)
        long=compute(fixture());short=compute(fixture(end=100))
        self.assertEqual(long['events'][0]['type'],'强度显著下降候选','background must not jump between coarse respiratory phases')
        for key in long['traces']:np.testing.assert_allclose(long['traces'][key][:95],short['traces'][key][:95],equal_nan=True)
        selected=(long['source']['clock']>=82)&(long['source']['clock']<115)
        self.assertEqual(len(np.unique(long['traces']['background'][selected])),1)

    def test_short_rejected_dips_return_to_reference_at_original_times(self):
        r=controlled(low=((80,83),(100,103),(120,123),(140,143)),reduced=8)
        t=compute(r)
        self.assertFalse(t['events']);self.assertGreater(t['restored_seconds'],0)
        self.assertAlmostEqual(t['traces']['reference'][175],np.mean(t['traces']['strength'][75:175]))
        self.assertLess(t['traces']['reference'][175],10)
        pulsatile=controlled(low=((80,83),),reduced=8)
        pulsatile.channels['治疗压']=400+200*np.sin(pulsatile.x*np.pi/2)
        self.assertGreater(compute(pulsatile)['restored_seconds'],0,'ordinary pressure oscillations are review hints, not invalid source data')
        leak_step=controlled(low=((80,83),),reduced=8)
        leak_step.channels['漏气量']=np.where(leak_step.x>=80,20.,0.)
        self.assertEqual(compute(leak_step)['restored_seconds'],0,'persistent total-leak shift must not be admitted as normal background')

    def test_gap_resets_and_long_event_flag_does_not_truncate(self):
        r=controlled(low=((80,120),),gap=(95,98));out=detect(r)
        self.assertTrue(any(e['type']=='数据缺失' for e in out['events']))
        self.assertFalse(any(e['start']<95 and e['end']>98 for e in out['events'] if e['type'].startswith('强度')))
        out=detect(controlled(low=((80,215),),end=260));e=out['events'][0]
        self.assertEqual(e['end']-e['start'],135)
        self.assertTrue(any('120秒' in flag for flag in e['evidence']['review_flags']))
        renewed=compute(controlled(low=((80,215),),end=340,gap=(230,233)))['traces']
        gaps=np.flatnonzero(~np.isfinite(renewed['strength'][220:]))+220
        self.assertTrue(len(gaps))
        first=int(gaps[-1]+1)
        self.assertTrue(np.isfinite(renewed['strength'][first]))
        self.assertTrue(np.isnan(renewed['reference'][first:first+29]).all(),'background warmup must reset old short/reference windows, not mix nonadjacent samples')

    def test_pressure_changes_are_review_evidence_and_scope_keeps_full_bounds(self):
        r=controlled();r.channels['治疗压']=np.where(r.x>=80,500.,400.)
        full=detect(r)['events'][0];scoped=detect(r,82,88)['events'][0]
        self.assertEqual(scoped['id'],full['id']);self.assertEqual((scoped['start'],scoped['end']),(80,90))
        self.assertIn('治疗压变化需复核',scoped['evidence']['review_flags'])
        self.assertIn('跨选区，保留完整估计区间',scoped['evidence']['review_flags'])
        self.assertFalse(settings()['device_control'])

    def test_weak_class_and_configuration_identity(self):
        out=detect(controlled(reduced=7,low=((80,105),)))
        self.assertEqual(out['events'][0]['type'],'强度减弱候选')
        r=controlled();a=detect(r);b=detect(r,window_seconds=8);c=detect(r,dynamic_method='centered_rms')
        self.assertNotEqual(a['events'][0]['id'],b['events'][0]['id']);self.assertNotEqual(a['events'][0]['id'],c['events'][0]['id'])
        self.assertNotEqual(scan_fingerprint(0,200,a['settings']),scan_fingerprint(0,200,b['settings']))
        for kwargs in (dict(window_seconds=True),dict(window_seconds=4),dict(dynamic_method='patient_flow'),dict(min_duration=7)):
            with self.assertRaises(ValueError):settings(**kwargs)
        self.assertEqual(settings()['version'],VERSION)
        self.assertTrue(all(n in r.meta['channels'] for n in names(5)))

    def test_http_snapshots_review_dedup_wave_method_and_feedback_isolation(self):
        original=viewer.recording,viewer.event_store
        with tempfile.TemporaryDirectory() as temp:
            r=controlled(low=((80,120),));viewer.recording=r;viewer.event_store=EventStore(Path(temp)/'events.db');fid=r.meta['file_id']
            server=viewer.ThreadingHTTPServer(('127.0.0.1',0),viewer.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=build_opener(ProxyHandler({}))
            def call(path,args=None):
                data=None if args is None else json.dumps(args).encode()
                with client.open(Request(f'http://127.0.0.1:{server.server_port}'+path,data=data,headers={'Content-Type':'application/json'}),timeout=20) as response:return json.load(response)
            args=dict(file_id=fid,profile='dynamic_baseline_v2')
            try:
                before=viewer.event_store.read(fid)
                readonly=call('/api/analyze',args);self.assertTrue(readonly['read_only']);self.assertEqual(readonly['settings']['method'],'detrended_abs');self.assertEqual(viewer.event_store.read(fid),before)
                old=call('/api/detect',dict(file_id=fid,profile='dynamic_baseline'))
                self.assertGreater(old['added'],0)
                self.assertIsNone(call('/api/scans/match',args)['match'])
                result=call('/api/detect',args);self.assertGreater(result['added'],0)
                self.assertIsNotNone(call('/api/scans/match',args)['match'])
                e=next(e for e in result['packet']['events'] if e['detection_profile']=='dynamic_baseline_v2')
                original_e=json.loads(json.dumps(e));self.assertEqual(e['detection_method'],'detrended_abs');self.assertEqual(e['detection_window_seconds'],5)
                self.assertEqual(e['evidence']['estimated_waveform_interval'],[80,120])
                packet=viewer.event_store.read(fid)
                next(x for x in packet['events'] if x['id']==e['id']).update(status='reviewed',start=81.,note='human changed')
                reviewed=call('/api/events',packet);changed=next(x for x in reviewed['events'] if x['id']==e['id'])
                self.assertEqual(changed['evidence'],original_e['evidence'])
                repeated=call('/api/detect',args);self.assertEqual(repeated['added'],0)
                self.assertEqual(next(x for x in repeated['packet']['events'] if x['id']==e['id']),changed)
                automatic=viewer.event_store.feedback(r.meta,result['packet']['run_id'],'automatic');self.assertEqual(automatic['events'][0],original_e)
                scope=dict(file_id=fid,run_id=result['packet']['run_id'],reviewer='tester',reviewed_range=[0,r.meta['raw_duration']],reviewed_types=['强度显著下降候选'],review_mode='complete')
                feedback=call('/api/feedback',dict(scope,kind='reviewed'))
                self.assertEqual([x['id'] for x in feedback['events']],[e['id']],'V1 same-type pending candidates cannot enter V2 review')
                preview=call('/api/feedback/prepare',scope)
                exported=call('/api/feedback/export',dict(scope,expected_revision=preview['revision'],confirmed=True))
                files=list((Path(exported['folder'])/'reviewed').glob('*.json'));self.assertTrue(files)
                saved=json.loads(files[0].read_text(encoding='utf-8'));self.assertEqual(saved['events'][0]['evidence'],original_e['evidence'])
                wave=call('/api/wave?'+urlencode(dict(file_id=fid,start=70,end=130,channel=names(5),pixels=1200,mode='raw',dynamic_method='centered_rms',optimized_method='detrended_abs'),doseq=True))
                self.assertTrue(all(any(p[1] is not None for p in wave['series'][name]) for name in names(5)))
                compute_result=compute(r);observed=dict((round(p[0],6),p[1]) for p in wave['series'][names(5)[0]] if p[1] is not None)
                self.assertTrue(observed)
                self.assertEqual(compute_result['settings']['method'],'detrended_abs')
                expected=waveform_channels(r,names(5),'detrended_abs')
                for name,points in wave['series'].items():
                    for _,value,index in points:
                        if value is not None:self.assertAlmostEqual(value,expected[name][index])
                with self.assertRaises(HTTPError) as error:call('/api/detect',dict(args,file_id='stale'))
                self.assertEqual(error.exception.code,409)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5);viewer.recording,viewer.event_store=original


if __name__=='__main__':unittest.main()
