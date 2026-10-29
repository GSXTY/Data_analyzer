import unittest
import numpy as np
from detector import detect,device_metrics
from test_detector import fixture


class DeviceCandidateTests(unittest.TestCase):
    def test_severe_and_moderate_and_unavailable_metrics(self):
        r=fixture();out=detect(r,reduction_ratio=.7,profile='device_candidates')
        self.assertEqual(sum(e['type']=='流量波动近消失候选' for e in out['events']),1)
        self.assertFalse(any(e['type']=='流量波动减弱候选' for e in out['events']))
        self.assertIsNone(out['device_metrics']['ahi'])
        self.assertIsNone(out['device_metrics']['central_apnea_duration_seconds'])
        r.channels['流量']=r.channels['流量'].copy()
        reduced=(r.x>=80)&(r.x<102)
        r.channels['流量'][reduced]=120+.12*r.x[reduced]+10*np.sin(2*np.pi*r.x[reduced]/4)
        out=detect(r,reduction_ratio=.7,profile='device_candidates')
        self.assertTrue(any(e['type']=='流量波动减弱候选' for e in out['events']))
        self.assertFalse(any(e['type']=='流量波动近消失候选' for e in out['events']))

    def test_confounds_and_missing_are_not_apneas(self):
        for r in (fixture(reduce=False),fixture(pressure=True)):
            result=detect(r,reduction_ratio=.7,profile='device_candidates')
            if not any(e['type'].startswith('流量波动') for e in result['events']):continue
            # Pressure transitions must split/exclude at least their exclusion window.
            self.assertTrue(all(not(e['start']<=79<e['end']) for e in result['events'] if e['type'].startswith('流量波动')))
        r=fixture(gap=True);r.channels['流量']=r.channels['流量'].copy();r.channels['流量'][(r.x>=80)&(r.x<102)]=np.nan
        out=detect(r,reduction_ratio=.7,profile='device_candidates')
        self.assertTrue(any(e['type']=='信号异常' for e in out['events']))
        self.assertFalse(any(e['type'].startswith('流量波动') for e in out['events']))

    def test_union_not_sum_and_parameter_validation(self):
        events=[dict(type='流量波动近消失候选',start=0,end=20),dict(type='流量波动减弱候选',start=10,end=30),dict(type='总漏气升高',start=50,end=90)]
        self.assertEqual(device_metrics(events,100)['total_candidate_duration_seconds'],30)
        for args in (dict(profile='bad'),dict(profile='device_candidates',reduction_ratio=.1),dict(reduction_ratio=None)):
            with self.assertRaises(ValueError):detect(fixture(),**args)

    def test_selected_types_preserve_ids_and_record_scan_scope(self):
        r=fixture(leak=True,gap=True)
        full=detect(r,reduction_ratio=.7,profile='device_candidates')
        kind='流量波动近消失候选'
        selected=detect(r,reduction_ratio=.7,profile='device_candidates',event_types=[kind])
        self.assertEqual([e['id'] for e in selected['events']],[e['id'] for e in full['events'] if e['type']==kind])
        self.assertEqual(selected['settings']['event_types'],[kind])
        self.assertIsNone(selected['device_metrics']['by_type']['流量波动减弱候选']['count'])
        leak=detect(r,reduction_ratio=.7,profile='device_candidates',event_types=['总漏气升高'])
        self.assertTrue(leak['events']);self.assertTrue(all(e['type']=='总漏气升高' for e in leak['events']))
        self.assertIsNone(leak['device_metrics']['total_candidate_duration_seconds'])
        for value in ([],['中枢性暂停'],['流量波动近消失候选',False],'总漏气升高'):
            with self.assertRaises(ValueError):detect(r,profile='device_candidates',event_types=value)
