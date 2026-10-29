import json,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from event_store import EventStore


class ScanHistoryTests(unittest.TestCase):
    def test_paging_archive_rename_matching_and_immutable_exports(self):
        meta=dict(file_id='f',name='fixture.csv',start='2026-01-01',end='2026-01-02',rows=20,raw_duration=100,display_duration=100.9,channels=['flow'])
        settings=dict(version='machine-flow-candidates-v2',profile='device_candidates',min_duration=10,reduction_ratio=.7,event_types=['流量波动减弱候选','数据缺失'])
        event=dict(id='e',start=10,end=25,mode='raw',type='流量波动减弱候选',note='original',source='detector',status='reviewed',channels=['flow'],updated_at='now')
        with tempfile.TemporaryDirectory() as temp:
            store=EventStore(Path(temp)/'events.db');ids=[]
            for i in range(25):
                ids.append(store.write(meta,i,[event],scan=dict(events=[event],settings=settings,scan_start=0,scan_end=100))['run_id'])
            before=store.read('f');original=store.feedback(meta,ids[-1],'automatic')
            page=store.scan_page('f');self.assertEqual(page['total'],25);self.assertEqual(len(page['scans']),10)
            self.assertEqual(page['scans'][0]['run_id'],ids[-1]);self.assertNotIn('events',page['scans'][0])
            second=store.scan_page('f',offset=20);self.assertEqual(len(second['scans']),5)
            matched=store.matching_scan('f',0,100,dict(settings,event_types=list(reversed(settings['event_types']))))
            self.assertEqual(matched['run_id'],ids[-1])
            self.assertIsNone(store.matching_scan('f',0,99,settings));self.assertIsNone(store.matching_scan('other',0,100,settings))
            store.edit_scan('f',ids[-1],title='首轮人工复核',archived=True)
            self.assertEqual(store.scan_page('f')['total'],24)
            self.assertEqual(store.scan_page('f',state='archived')['total'],1)
            self.assertEqual(store.scan_page('f',state='all',query='首轮人工')['total'],1)
            pinned=store.scan_page('f',selected=ids[-1])['selected'];self.assertTrue(pinned['archived'])
            self.assertEqual(store.feedback(meta,ids[-1],'automatic'),original)
            reviewed=store.feedback(meta,ids[-1],'reviewed','tester',[0,100])
            self.assertEqual(reviewed['automatic'],original);self.assertEqual(store.read('f'),before)
            store.edit_scan('f',ids[-1],archived=False);self.assertEqual(store.scan_page('f')['total'],25)
            for args in (dict(limit=0),dict(offset=-1),dict(state='bad')):
                with self.assertRaises(ValueError):store.scan_page('f',**args)
            for args in (dict(title=''),dict(archived='yes')):
                with self.assertRaises(ValueError):store.edit_scan('f',ids[-1],**args)
            with self.assertRaises(ValueError):store.edit_scan('other',ids[-1],title='wrong file')
            # Simulate an old installation with no metadata index; only this temp DB.
            with closing(sqlite3.connect(store.path)) as db,db:db.execute('DROP TABLE scan_info')
            upgraded=EventStore(store.path);self.assertEqual(upgraded.scan_page('f')['total'],25)
            self.assertEqual(upgraded.feedback(meta,ids[-1],'automatic'),original)
            self.assertEqual(EventStore(store.path).scan_page('f')['total'],25)
