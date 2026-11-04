import unittest
from datetime import datetime,timedelta
import numpy as np
from viewer import Recording
from adaptive_test import detect,settings,A_CHANNEL
from types import SimpleNamespace
import pandas as pd

def fixture(end=220,gap=None,low=(80,120),moderate=False):
    t=np.arange(0,end,.1)
    if gap:t=t[(t<gap[0])|(t>=gap[1])]
    amp=np.where((t>=low[0])&(t<low[1]),7 if moderate else .3,20.)
    if moderate:amp=np.where((t>=96)&(t<103),.1,amp)
    flow=50+amp*np.sin(2*np.pi*t/4)
    rows=['time,流量,运行状态,治疗压']
    for i,s in enumerate(t):
        stamp=(datetime(2026,1,1)+timedelta(seconds=float(s))).strftime('%Y-%m-%d %H:%M:%S.%f')
        rows.append(f'{stamp},{flow[i]},3,400')
    return Recording('\n'.join(rows).encode(),'adaptive-synthetic.csv')

class AdaptiveTests(unittest.TestCase):
    def test_seven_seconds_is_sufficient_without_backdating_and_exit_stays_above_enter(self):
        self.assertEqual(settings()['min_duration'],7);self.assertEqual(settings()['reduction_ratio'],.5)
        for seconds in (6,7):
            a=np.full(140,40.);a[60:60+seconds]=2.;idx=np.arange(len(a))
            r=SimpleNamespace(x=idx.astype(float),channels={'流量':a,A_CHANNEL:a},meta=dict(raw_duration=140.,file_id='synthetic-seven',start='2026-01-01 00:00:00'),gaps=[],
                _adaptive_features=dict(index=idx,clock=idx+1,a=a,valid=np.ones(len(a),dtype=bool),segment=np.zeros(len(a)),med=pd.DataFrame(index=idx),count=np.full(len(a),50)))
            events=detect(r)['events']
            if seconds==6:self.assertEqual(events,[])
            else:
                self.assertEqual(len(events),1);self.assertEqual(events[0]['start'],61);self.assertEqual(events[0]['end']-events[0]['start'],7)
        for ratio in (.11,.5,.7,.8):self.assertGreater(settings(reduction_ratio=ratio)['reduced_exit_ratio'],ratio)

    def test_causal_display_no_future_and_frozen_reference(self):
        r=fixture();short=fixture(end=100)
        cut=int(np.sum(r.x<95))
        np.testing.assert_allclose(r.channels[A_CHANNEL][:cut],short.channels[A_CHANNEL][:cut],equal_nan=True)
        out=detect(r);resp=[e for e in out['events'] if e['type'].startswith('测试')]
        self.assertEqual(len(resp),1);self.assertEqual(resp[0]['type'],'测试近消失候选')
        self.assertGreaterEqual(resp[0]['start'],80)
        self.assertGreaterEqual(resp[0]['end']-resp[0]['start'],10)
        self.assertIn('冻结参考幅度',resp[0]['note']);self.assertIn('不计入事件时长',resp[0]['note'])

    def test_moderate_and_brief_near_state_keep_one_episode(self):
        r=fixture(moderate=True)
        out=detect(r);resp=[e for e in out['events'] if e['type'].startswith('测试')]
        self.assertEqual(len(resp),1);self.assertEqual(resp[0]['type'],'测试减弱候选')

    def test_gap_and_scoped_quality_intervals_are_valid_and_ids_stable(self):
        r=fixture(gap=(100,110))
        out=detect(r)
        self.assertEqual(len([e['id'] for e in out['events']]),len(set(e['id'] for e in out['events'])))
        for e in out['events']:
            self.assertTrue(0<=e['start']<e['end']<=r.meta['raw_duration'])
            if e['type'].startswith('测试'):self.assertFalse(e['start']<100 and e['end']>110)
        scoped=detect(r,150,200)
        self.assertTrue(all(e['end']>150 and e['start']<200 for e in scoped['events']))
        self.assertEqual([e['id'] for e in out['events']],[e['id'] for e in detect(r)['events']])

    def test_parameters_selection_and_scope_identity(self):
        r=fixture();all_events=detect(r)['events']
        e=next(e for e in all_events if e['type'].startswith('测试'))
        scoped=detect(r,e['start']+2,e['end']-2)
        self.assertEqual(next(x['id'] for x in scoped['events'] if x['type']==e['type']),e['id'])
        self.assertFalse(detect(r,event_types=['测试减弱候选'])['events'])
        selected=detect(r,event_types=[e['type']])['events']
        self.assertEqual(selected[0]['id'],e['id'])
        for args in (dict(event_types=[]),dict(event_types=['其他']),dict(reduction_ratio=.1),dict(reduction_ratio=float('nan')),dict(start=-1),dict(min_duration=True)):
            with self.assertRaises(ValueError):detect(r,**args)
