import tempfile
import unittest
from pathlib import Path
from event_store import EventStore, Conflict


class StoreTests(unittest.TestCase):
    def test_snapshot_is_immutable_and_review_contains_history(self):
        meta=dict(file_id='f',name='fixture.csv',start='2026-01-01',end='2026-01-02',rows=20,raw_duration=100,display_duration=100.9,channels=['flow'])
        e=dict(id='original',start=10,end=25,mode='raw',type='呼吸波动减弱',note='automatic',source='detector',status='candidate',channels=['flow'],updated_at='now')
        with tempfile.TemporaryDirectory() as temp:
            store=EventStore(Path(temp)/'events.db')
            scan=dict(events=[e],settings={'version':'test'},scan_start=0,scan_end=100)
            initial=store.write(meta,0,[e],scan=scan);run=initial['run_id']
            corrected=dict(e,start=11,end=24,status='reviewed',note='corrected')
            store.write(meta,1,[corrected],reviewer='tester')
            original=store.feedback(meta,run,'automatic')
            self.assertEqual(original['events'][0]['start'],10)
            reviewed=store.feedback(meta,run,'reviewed','tester',[0,100])
            self.assertEqual(reviewed['events'][0]['start'],11)
            self.assertEqual(reviewed['automatic']['events'][0]['start'],10)
            self.assertEqual(reviewed['history'][-1]['reviewer'],'tester')
            self.assertEqual(store.scans('f')[0]['run_id'],run)
            for reviewer,ranges in [('',[0,100]),('t',[1,101]),('t',[1,0])]:
                with self.assertRaises(ValueError):store.feedback(meta,run,'reviewed',reviewer,ranges)
            with self.assertRaises(ValueError):store.feedback(dict(meta,file_id='other'),run,'automatic')

    def test_revision_and_validation(self):
        meta=dict(file_id='abc',raw_duration=100,display_duration=100.9,channels=['flow'])
        e=dict(id='e',start=1,end=12,mode='raw',type='疑似暂停',note='',source='agent',status='candidate',channels=['flow'],updated_at='now')
        with tempfile.TemporaryDirectory() as temp:
            store=EventStore(Path(temp)/'events.db')
            self.assertEqual(store.read('abc')['revision'],0)
            self.assertEqual(store.write(meta,0,[e])['revision'],1)
            with self.assertRaises(Conflict):store.write(meta,0,[])
            self.assertEqual(store.read('abc')['events'][0]['source'],'agent')
            with self.assertRaises(ValueError):store.write(meta,1,[dict(e,end=101)])
            self.assertEqual(store.read('abc')['revision'],1)
            self.assertEqual(store.read('other')['events'],[])
            self.assertEqual(EventStore(Path(temp)/'events.db').read('abc')['events'][0]['status'],'candidate')
