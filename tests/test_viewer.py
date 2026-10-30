import unittest
import numpy as np
from viewer import Recording


class RecordingTests(unittest.TestCase):
    def test_pressure_profile_and_actual_record_density(self):
        rows=['time,气压1,气压2,气压3,流量,漏气量']
        for i in range(12):rows.append(f'2026-01-01 00:00:0{i//4}.{(i%4)*250:03},101500,101400,101000,12,10')
        r=Recording('\n'.join(rows).encode(),'profile.csv')
        self.assertEqual(r.meta['channel_units']['气压1'],'Pa')
        self.assertEqual(r.meta['channel_roles']['气压2'],'出风口压力')
        self.assertEqual(r.meta['channel_units']['流量'],'L/min')
        self.assertEqual(r.meta['channel_units']['漏气量'],'L/min')
        self.assertEqual(r.meta['channel_roles']['漏气量'],'瞬时总漏气率（计算值）')
        self.assertEqual(r.meta['median_records_per_second'],4)
        self.assertFalse(r.meta['sampling_info']['uniform_sampling_verified'])
        self.assertFalse(r.meta['sampling_info']['computed_rate_verified'])
        self.assertTrue((r.engineering['气压1']==500).all())
        self.assertTrue((r.engineering['气压2']==400).all())
        self.assertTrue((r.channels['气压1']==101500).all())

    def test_pressure_column_never_gets_legacy_byte_conversion(self):
        cols=['time']+['c'+str(i) for i in range(1,13)]+['气压1']
        r=Recording((','.join(cols)+'\n'+','.join(['2026-01-01']+['0']*12+['101500'])).encode(),'pressure.csv')
        self.assertEqual(r.wave(0,0,['气压1'],100,'engineering')['series']['气压1'][0][1],101500)
    def test_encoding_time_gaps_and_missing(self):
        raw = ('时间,流量,压力\n2026-10-16 01:00:00,1,2\n'
               '2026-10-16 01:00:00,4,3\n2026-10-16 01:00:03,,4\n').encode('gb18030')
        r = Recording(raw, 'test.csv')
        self.assertEqual(r.meta['rows'], 3)
        self.assertEqual(r.x.tolist(), [0, 0, 3])
        self.assertEqual(r.gaps, [[0, 3]])
        self.assertTrue(r.meta['repeated_timestamps'])
        self.assertIsNone(r.wave(0, 3, ['流量'], 100)['series']['流量'][-1][1])

    def test_peaks_survive_downsampling(self):
        n = 10000
        rows = ['time,flow']
        for i in range(n):
            rows.append(f'2026-10-16 00:{i//6000:02}:{i//100%60:02},{999 if i == 5555 else -888 if i == 5556 else 0}')
        r = Recording('\n'.join(rows).encode(), 'peaks.csv')
        points = r.wave(0, 99, ['flow'], 100)['series']['flow']
        self.assertIn(999, [p[1] for p in points])
        self.assertIn(-888, [p[1] for p in points])
        self.assertLess(len(points), 500)

    def test_reject_bad_time(self):
        for raw in [b'time,flow\nbad,1', b'time,flow\n2026-01-02,1\n2026-01-01,2']:
            with self.assertRaises(ValueError):
                Recording(raw, 'bad.csv')

    def test_compensation_preserves_originals(self):
        raw = '时间,气压1,气压2,气压3\n2026-01-01,101200,101100,101000\n'.encode()
        r = Recording(raw, 'pressure.csv')
        self.assertEqual(r.channels['气压1'][0], 101200)
        self.assertEqual(r.channels['气压1−气压3（补偿）'][0], 200)
        self.assertEqual(r.channels['气压2−气压3（补偿）'][0], 100)
        self.assertEqual(r.channels['气压1−气压2（差压）'][0], 100)

    def test_all_missing_blocks_and_internal_breaks(self):
        raw = ('time,flow,other\n' + '\n'.join(
            f'2026-01-01 00:00:00,{"" if 100 <= i < 300 else i},1'
            for i in range(1000))).encode()
        r = Recording(raw, 'missing.csv')
        points = r.wave(0, 0, ['flow'], 100)['series']['flow']
        self.assertTrue(any(p[1] is None for p in points))
        self.assertIn(99, [p[2] for p in points])
        self.assertIn(300, [p[2] for p in points])

    def test_engineering_mode_expands_seconds_and_compensates(self):
        raw = ('时间,气压1,气压2,气压3\n'
               '2026-01-01 00:00:00,101200,101100,101000\n'
               '2026-01-01 00:00:00,101300,101150,101000\n'
               '2026-01-01 00:00:01,101400,101200,101000\n'
               '2026-01-01 00:00:01,101500,101250,101000\n'
               '2026-01-01 00:00:02,101400,101200,101000\n'
               '2026-01-01 00:00:02,101500,101250,101000\n'
               '2026-01-01 00:00:03,101400,101200,101000\n'
               '2026-01-01 00:00:03,101500,101250,101000\n'
               '2026-01-01 00:02:30,101200,101100,101000\n'
               '2026-01-01 00:02:30,101300,101150,101000\n').encode()
        r = Recording(raw, 'reference.csv')
        np.testing.assert_allclose(r.display_x, [0, .5, 1, 1.5, 2, 2.5, 3, 3.5, 150, 150.5])
        self.assertEqual(r.meta['engineering_gaps'], [[3.5, 150]])
        points = r.wave(0, 2, ['气压1'], 100, 'engineering')['series']['气压1']
        self.assertEqual(points[0][:2], [0, 200])
        self.assertEqual(points[1][:2], [.5, 300])
        self.assertEqual(r.channels['气压1'][0], 101200)
        self.assertEqual(r.x[1], 0)

    def test_engineering_byte_rule_is_explicit(self):
        columns = ['time'] + ['c'+str(i) for i in range(1,14)]
        row = ['2026-01-01'] + ['0']*12 + ['255']
        r = Recording((','.join(columns)+'\n'+','.join(row)).encode(), 'byte.csv')
        self.assertEqual(r.wave(0,0,['c13'],100)['series']['c13'][0][1],255)
        self.assertEqual(r.wave(0,0,['c13'],100,'engineering')['series']['c13'][0][1],-1)


if __name__ == '__main__':
    unittest.main()
