import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

import numpy as np
import pandas as pd

from multifeature_test import detect, settings, _compute, features, waveform_channels, A, B, NEAR, WEAK, RATIO, VERSION, PROFILE
from detector import detect as dispatch
from event_store import EventStore
from test_adaptive_test import fixture


def controlled(length=150, baseline=40., value=2., seconds=7, noise=.01):
    idx=np.arange(length)
    amplitude=np.full(length,baseline);amplitude[60:60+seconds]=value
    med=pd.DataFrame({'flow':50+np.sin(idx*np.pi/2),'治疗压':400.,'压力':400.,'漏气量':10.},index=idx)
    f=dict(index=idx,clock=idx+1,a=amplitude,noise=np.full(length,noise),valid=np.ones(length,dtype=bool),
           segment=np.zeros(length),med=med,count=np.full(length,50),spread=amplitude.copy())
    return SimpleNamespace(x=idx.astype(float),channels={'流量':amplitude,A:amplitude},
        meta=dict(raw_duration=float(length),display_duration=float(length),file_id='controlled-v3',
                  start='2026-01-01 00:00:00',channels=['流量',A],name='controlled.csv'),gaps=[],_multifeature_features=f)


class MultiFeatureTests(unittest.TestCase):
    def test_seven_seconds_strict_and_no_onset_backdating(self):
        self.assertEqual(settings()['min_duration'],7)
        self.assertEqual(settings()['reduction_ratio'],.5)
        for duration in (6,7):
            result=detect(controlled(seconds=duration))
            events=[e for e in result['events'] if e['type'].startswith('测试')]
            if duration==6:self.assertEqual(events,[])
            else:
                e=events[0];self.assertEqual((e['start'],e['end']),(61.,68.))
                self.assertEqual(e['evidence']['confirmed_at'],68.)
                self.assertLessEqual(e['evidence']['remaining_ratio_max'],.1)

    def test_fixed_three_litre_floor_does_not_create_false_ninety_percent(self):
        # B20/A2.5 is only87.5% reduction; old max(3,.1B) classified it near.
        e=detect(controlled(baseline=20.,value=2.5,seconds=12))['events'][0]
        self.assertEqual(e['type'],'测试减弱候选')
        self.assertEqual(e['evidence']['near_threshold'],2.)
        self.assertEqual(e['evidence']['remaining_ratio_max'],.125)
        boundary=detect(controlled(baseline=20.,value=2.,seconds=7))['events'][0]
        self.assertEqual(boundary['type'],'测试近消失候选')

    def test_hysteresis_does_not_count_nonstrict_seconds(self):
        r=controlled(value=2.,seconds=18)
        r._multifeature_features['a'][67:69]=4.4
        events=detect(r)['events']
        self.assertEqual([(e['start'],e['end']) for e in events],[(61.,68.),(70.,79.)])
        self.assertTrue(all(e['evidence']['remaining_ratio_max']<=.1 for e in events))
        r=controlled(value=19.,seconds=15)
        r._multifeature_features['a'][66:69]=22.
        self.assertEqual(detect(r)['events'],[],'six+six weak seconds separated by above50% must not qualify')

    def test_noise_is_quality_not_a_relaxed_threshold(self):
        e=detect(controlled(baseline=20.,value=1.,seconds=12,noise=1.))['events'][0]
        self.assertEqual(e['type'],'测试减弱候选')
        self.assertEqual(e['evidence']['near_threshold'],2.)
        self.assertTrue(any('分辨率不足' in s for s in e['evidence']['review_flags']))
        r=controlled(baseline=2.,value=.1,seconds=12,noise=1.)
        self.assertFalse(any(e['type'].startswith('测试') for e in detect(r)['events']))
        self.assertTrue(any(e['type']=='信号异常' for e in detect(r)['events']))

    def test_pressure_and_leak_changes_keep_event_and_explain(self):
        r=controlled(seconds=16)
        r._multifeature_features['med'].loc[60:76,'治疗压']=600.
        r._multifeature_features['med'].loc[60:76,'漏气量']=30.
        e=detect(r)['events'][0]
        self.assertEqual(e['type'],'测试近消失候选')
        self.assertTrue(any('治疗压' in s for s in e['evidence']['review_flags']))
        self.assertTrue(any('漏气量' in s for s in e['evidence']['review_flags']))

    def test_gap_end_does_not_add_unobserved_seconds_and_partial_is_censored(self):
        r=controlled(seconds=40)
        f=r._multifeature_features;f['valid'][70:75]=False;f['a'][70:75]=np.nan;f['segment'][70:75]=1;f['segment'][75:]=2
        r.gaps=[[70.,75.]]
        e=next(e for e in detect(r)['events'] if e['type'].startswith('测试'))
        self.assertEqual(e['end'],70.)
        self.assertTrue(e['evidence']['boundary_censored'])
        self.assertTrue(all(not (e['start']<70 and e['end']>75) for e in detect(r)['events']))

    def test_scope_types_ids_settings_and_export_keep_evidence(self):
        r=controlled(seconds=16)
        all_events=dispatch(r,profile=PROFILE)['events'];e=all_events[0]
        sub=detect(r,e['start']+2,e['end']-2,event_types=['测试近消失候选'])['events'][0]
        self.assertEqual(e['id'],sub['id'])
        self.assertEqual(e['evidence'],sub['evidence'])
        with tempfile.TemporaryDirectory() as temp:
            store=EventStore(Path(temp)/'events.db')
            packet=store.write(r.meta,0,all_events)
            self.assertEqual(packet['events'][0]['evidence'],e['evidence'])
            packet['events'][0].update(status='reviewed',note='人工修改',type='伪迹/干扰')
            newer=store.write(r.meta,packet['revision'],packet['events'])
            self.assertEqual(newer['events'][0]['detection_version'],VERSION)
            self.assertEqual(newer['events'][0]['evidence'],e['evidence'])
        for args in (dict(min_duration=True),dict(reduction_ratio=.1),dict(reduction_ratio=float('nan')),dict(event_types=[]),dict(start=-1)):
            with self.assertRaises(ValueError):detect(r,**args)

    def test_original_rows_causal_traces_and_parameter_specific_plot(self):
        full=fixture();short=fixture(end=100)
        f1=_compute(full,settings())['trace'];f2=_compute(short,settings())['trace']
        for key in f1:
            np.testing.assert_allclose(f1[key][:90],f2[key][:90],equal_nan=True)
        e=next(e for e in detect(full)['events'] if e['type'].startswith('测试'))
        self.assertEqual(e['type'],'测试近消失候选')
        i=int(e['start'])-1
        self.assertAlmostEqual(f1[NEAR][i],f1[B][i]*.1)
        self.assertAlmostEqual(f1[RATIO][i],f1[A][i]/f1[B][i]*100)
        channels=waveform_channels(full)
        idx=np.searchsorted(full.x,e['start'])
        self.assertAlmostEqual(channels[A][idx],f1[A][i])
        self.assertAlmostEqual(channels[B][idx],f1[B][i])
        different=_compute(full,settings(reduction_ratio=.7))['trace']
        self.assertAlmostEqual(different[WEAK][50],different[B][50]*.7)
        self.assertEqual(len(full._multifeature_cache),2)

    def test_new_features_do_not_reset_for_therapy_pressure(self):
        r=fixture();r.channels['治疗压'][r.x>=80]=600
        f=features(r)
        self.assertEqual(f['segment'][79],f['segment'][80])
        r2=fixture();r2.channels['运行状态'][r2.x>=80]=4
        f2=features(r2)
        self.assertNotEqual(f2['segment'][79],f2['segment'][80])


if __name__=='__main__':unittest.main()
