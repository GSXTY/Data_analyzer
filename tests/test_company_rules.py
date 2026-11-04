import json
import unittest
from datetime import datetime,timedelta
from unittest.mock import patch
import numpy as np
from viewer import Recording
from company_rules import pressure_flow,Q_CHANNEL,A_CHANNEL,FLOW_A_CHANNEL,amplitude_channel,detect_company
from signal_analysis import analyze_recording
from event_store import validate_event

def fixture(seconds=60,reference=100000,missing=False,delta=0):
    rows=['time,气压1,气压2,气压3']
    for i,t in enumerate(np.arange(0,seconds,.1)):
        if missing and 20<=t<25:continue
        p1=reference+400+(delta if i%2 else -delta)
        stamp=(datetime(2026,1,1)+timedelta(seconds=float(t))).strftime('%Y-%m-%d %H:%M:%S.%f')
        rows.append(f'{stamp},{p1},{reference+400},{reference}')
    return Recording('\n'.join(rows).encode(),'company-synthetic.csv')

class CompanyRuleTests(unittest.TestCase):
    def test_csv_flow_input_needs_no_pressure_and_preserves_origin(self):
        from test_detector import fixture as flow_fixture
        r=flow_fixture();before=r.channels['流量'].copy()
        out=detect_company(r,flow_source='csv_flow')
        pauses=[e for e in out['events'] if e['type']=='公司暂停候选']
        self.assertEqual(len(pauses),1)
        self.assertEqual(out['settings']['input_channel'],'流量')
        self.assertEqual(out['settings']['version'],'csv-flow-amplitude-v1')
        self.assertEqual(pauses[0]['detection_input'],'csv_flow')
        self.assertIn(FLOW_A_CHANNEL,pauses[0]['channels'])
        revised=validate_event({**pauses[0],'type':'伪迹/干扰','note':'reviewed'},r.meta)
        self.assertEqual(revised['detection_input'],'csv_flow')
        np.testing.assert_array_equal(before,r.channels['流量'])
        np.testing.assert_allclose(r.channels[FLOW_A_CHANNEL],amplitude_channel(r.x,before,r.meta['raw_duration']),equal_nan=True)
        with self.assertRaises(ValueError):detect_company(r,flow_source='wrong')

    def test_review_edit_preserves_company_origin_even_if_type_and_note_change(self):
        r=fixture();event=detect_company(r)['events'][0]
        revised=validate_event({**event,'type':'伪迹/干扰','note':'reviewer correction','status':'reviewed'},r.meta)
        self.assertEqual(revised['detection_profile'],'company_rules')
        self.assertEqual(revised['status'],'reviewed')

    def test_display_a_uses_the_exact_past_five_seconds_and_resets_at_gaps(self):
        r=fixture(delta=9,missing=True);a=r.channels[A_CHANNEL];q=r.channels[Q_CHANNEL]
        self.assertTrue(np.isnan(a[r.x<5]).all())
        for t in (5,6,12,20,26,29,30,40):
            rows=np.flatnonzero(np.floor(r.x)==t)
            if not len(rows):continue
            prior=(r.x>=t-5)&(r.x<t)
            complete=len(np.unique(np.floor(r.x[prior])))==5
            if complete:np.testing.assert_allclose(a[rows],np.max(q[prior])-np.min(q[prior]))
            else:self.assertTrue(np.isnan(a[rows]).all())
        self.assertTrue(np.isnan(a[(r.x>=25)&(r.x<30)]).all())

    def test_formula_reference_compensation_and_csv_preservation(self):
        self.assertAlmostEqual(float(pressure_flow(np.array([0.]),np.array([0.]))[0]),-8.26876272861618)
        a,b=fixture(delta=9),fixture(reference=99000,delta=9)
        np.testing.assert_array_equal(a.channels[Q_CHANNEL],b.channels[Q_CHANNEL])
        np.testing.assert_allclose(a.channels[Q_CHANNEL],pressure_flow(a.channels['气压1']-a.channels['气压3'],a.channels['气压2']-a.channels['气压3']))
        raw=a.raw_csv;result=detect_company(a)
        self.assertEqual(a.raw_csv,raw)
        self.assertTrue(any(e['type']=='公司低通气候选' for e in result['events']))
        self.assertFalse(any(e['type']=='公司暂停候选' for e in result['events']))
        self.assertEqual(result['settings']['flow_unit'],'L/min')
        self.assertFalse(result['settings']['device_control'])
        json.dumps(result,allow_nan=False)

    def test_strict_duration_and_no_backdating(self):
        self.assertFalse(detect_company(fixture(13))['events'])
        event=detect_company(fixture(14))['events'][0]
        self.assertEqual((event['start'],event['end']),(5,13))
        self.assertEqual(event['status'],'candidate')

    def test_threshold_equalities_and_gaps(self):
        r=fixture()
        for amplitude in (10.,17.):
            with patch('company_rules.pressure_flow',side_effect=lambda a,b:np.where(np.arange(len(a))%2,amplitude,0.)):
                self.assertFalse(detect_company(r)['events'])
        result=detect_company(fixture(missing=True))
        self.assertTrue(any(e['type']=='数据缺失' for e in result['events']))
        pauses=[e for e in result['events'] if e['type']=='公司暂停候选']
        self.assertTrue(pauses)
        self.assertFalse(any(e['start']<25 and e['end']>20 for e in pauses))

    def test_analysis_selection_and_parameter_protection(self):
        r=fixture();out=analyze_recording(r,profile='company_rules')
        self.assertTrue(out['read_only']);self.assertIsNone(out['clinical_classification']['OA'])
        selected=detect_company(r,event_types=['公司低通气候选'])
        self.assertEqual(selected['events'],[])
        for args in (dict(event_types=[]),dict(event_types=['流量波动减弱候选']),dict(min_duration=True),dict(start=-1)):
            with self.assertRaises(ValueError):detect_company(r,**args)
