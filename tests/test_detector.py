import unittest
from datetime import datetime,timedelta
import numpy as np
from detector import detect
from viewer import Recording


def fixture(offset=120,reduce=True,leak=False,gap=False,pressure=False):
    t=np.arange(0,220,.04)
    if gap:t=t[(t<170)|(t>=185)]
    amplitude=np.where((t>=80)&(t<102),.4 if reduce else 20,20)
    flow=offset+.12*t+amplitude*np.sin(2*np.pi*t/4)
    leakage=np.where((t>=135)&(t<155),60 if leak else 10,10)
    p=np.where((t>=79)&(t<103),600 if pressure else 400,400)
    rows=['time,流量,漏气量,压力,运行状态']
    for i,sec in enumerate(t):
        stamp=(datetime(2026,1,1)+timedelta(seconds=float(sec))).strftime('%Y-%m-%d %H:%M:%S.%f')
        rows.append(f'{stamp},{flow[i]},{leakage[i]},{p[i]},3')
    return Recording('\n'.join(rows).encode(),'synthetic.csv')


class DetectionTests(unittest.TestCase):
    def test_machine_flow_reduction_without_zero_flow(self):
        r=fixture();events=detect(r)['events']
        low=[e for e in events if e['type']=='呼吸波动减弱']
        self.assertEqual(len(low),1)
        self.assertTrue(80<=low[0]['start']<=84)
        self.assertTrue(100<=low[0]['end']<=103)
        self.assertEqual(low[0]['source'],'detector')
        self.assertGreater(r.channels['流量'].min(),0)
        second=detect(r)['events'];self.assertEqual([e['id'] for e in events],[e['id'] for e in second])

    def test_constant_offset_does_not_create_events(self):
        self.assertFalse(any(e['type']=='呼吸波动减弱' for e in detect(fixture(reduce=False))['events']))
        a=[(e['start'],e['end']) for e in detect(fixture(offset=120))['events'] if e['type']=='呼吸波动减弱']
        b=[(e['start'],e['end']) for e in detect(fixture(offset=1200))['events'] if e['type']=='呼吸波动减弱']
        self.assertEqual(a,b)

    def test_total_leak_and_gaps_are_separate(self):
        events=detect(fixture(leak=True,gap=True))['events']
        self.assertTrue(any(e['type']=='总漏气升高' and 135<=e['start']<=137 for e in events))
        self.assertTrue(any(e['type']=='数据缺失' and e['start']<185<=e['end'] for e in events))
        self.assertFalse(any(e['type']=='呼吸波动减弱' and e['start']<185 and e['end']>170 for e in events))

    def test_scope_and_invalid_parameters(self):
        r=fixture()
        self.assertEqual(detect(r,start=10,end=50)['events'],[])
        self.assertFalse(any(e['type']=='呼吸波动减弱' for e in detect(r,start=99,end=110)['events']))
        for args in [dict(min_duration=0),dict(reduction_ratio=2),dict(end=float('nan')),dict(start=-1)]:
            with self.assertRaises(ValueError):detect(r,**args)
