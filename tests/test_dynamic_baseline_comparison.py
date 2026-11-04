from types import SimpleNamespace
import unittest

import numpy as np

from dynamic_baseline_comparison import source_seconds, short_strength, compare, ideal_stop, evaluate
from test_adaptive_test import fixture


def source_fixture(offset=0., variable_density=False, gap=False):
    x,flow=[],[]
    for sec in range(160):
        if gap and 80<=sec<84:continue
        count=50 if variable_density and sec%2 else 10
        amp=.2 if 60<=sec<100 else 20.
        x.extend([float(sec)]*count)
        flow.extend([offset-amp,offset+amp]*(count//2))
    return SimpleNamespace(x=np.asarray(x),channels={'流量':np.asarray(flow),'漏气量':np.ones(len(x))},
                           meta={'raw_duration':160.})


class DynamicComparisonTests(unittest.TestCase):
    def test_eight_second_window_delays_and_shortens_ideal_stop(self):
        short=ideal_stop(2,20);long=ideal_stop(8,20)
        self.assertAlmostEqual(short['qualifying_seconds'],19,delta=.03)
        self.assertAlmostEqual(long['qualifying_seconds'],16,delta=.03)
        self.assertGreater(long['first_qualifying'],short['first_qualifying'])
        self.assertTrue(long['meets_ten_seconds'])
        self.assertFalse(ideal_stop(8,13)['meets_ten_seconds'])
        self.assertFalse(ideal_stop(2,10)['meets_ten_seconds'],'short windows do not guarantee all true10s stops qualify')

    def test_centered_rms_invariant_to_constant_offset_and_source_row_density(self):
        for window in (2,5,8):
            outputs=[short_strength(source_seconds(source_fixture(**kw)),window,'centered_rms') for kw in
                     ({},{'offset':1000.},{'variable_density':True})]
            for values in outputs[1:]:np.testing.assert_allclose(outputs[0],values,equal_nan=True,atol=1e-7)

    def test_unremoved_background_can_hide_stop_but_spread_comparison_is_not_patient_flow(self):
        _,trials=compare(source_fixture(offset=60.))
        absolute=[t for t in trials if t['method']=='absolute_mean']
        centered=[t for t in trials if t['method']=='centered_rms']
        self.assertTrue(all(t['events']==[] for t in absolute))
        self.assertTrue(all(any(e['type']=='强度显著下降候选' for e in t['events']) for t in centered))
        self.assertTrue(all(t['settings']['patient_flow_verified'] is False for t in trials))

    def test_gap_never_bridged_and_no_leak_subtraction(self):
        r=source_fixture(gap=True);source,trials=compare(r)
        self.assertTrue(all(not(e['start']<80 and e['end']>84) for t in trials for e in t['events']))
        before=short_strength(source,2,'centered_rms')
        r.channels['漏气量']*=100000
        after=short_strength(source_seconds(r),2,'centered_rms')
        np.testing.assert_allclose(before,after,equal_nan=True)

    def test_causal_and_freezes_reference_before_confirmation(self):
        full=fixture();short=fixture(end=100)
        source1,trials1=compare(full);source2,trials2=compare(short)
        for full_trial,short_trial in zip(trials1,trials2):
            np.testing.assert_allclose(full_trial['strength'][:90],short_trial['strength'][:90],equal_nan=True)
            np.testing.assert_allclose(full_trial['baseline'][:90],short_trial['baseline'][:90],equal_nan=True)
        trial=next(t for t in trials1 if t['method']=='centered_rms' and t['window_seconds']==2)
        e=trial['events'][0]
        active=(source1['clock']>=e['parent_start'])&(source1['clock']<e['parent_end'])
        self.assertTrue(np.allclose(trial['baseline'][active],e['reference']))

    def test_invalid_window_and_scope_parameters(self):
        source=source_seconds(source_fixture())
        with self.assertRaises(ValueError):short_strength(source,True,'centered_rms')
        with self.assertRaises(ValueError):short_strength(source,4,'centered_rms')
        five=short_strength(source,5,'centered_rms')
        self.assertTrue(np.isnan(five[:4]).all());self.assertTrue(np.isfinite(five[4]))
        with self.assertRaises(ValueError):evaluate(np.ones(160),source,160,minimum=0)


if __name__=='__main__':unittest.main()
