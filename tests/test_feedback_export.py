import hashlib,json,tempfile,unittest
from copy import deepcopy
from pathlib import Path
from feedback_export import scoped_review,save_package,folder_for


class FeedbackExportTests(unittest.TestCase):
    def fixture(self):
        raw=b'time,flow\n2026-01-01 00:00:00,1\n2026-01-01 00:20:00,2\n'
        meta=dict(file_id=hashlib.sha256(raw).hexdigest(),name='fixture.csv',raw_duration=1200)
        def event(id,a,b,status='reviewed',kind='流量波动减弱候选'):
            return dict(id=id,start=a,end=b,mode='raw',type=kind,status=status,source='detector',channels=['flow'],note='',updated_at='now')
        events=[event('yes',100,120),event('pending',200,220,'candidate'),event('boundary',590,610),event('outside',700,720),event('other',300,320,kind='总漏气升高'),event('ignored',400,420,'ignored')]
        automatic=dict(version=1,kind='automatic',file_id=meta['file_id'],run_id='run-1',recording=dict(name='fixture.csv'),scan_start=0,scan_end=1200,settings={'version':'test'},events=[dict(e,status='candidate') for e in events])
        return raw,meta,automatic,dict(events=events,revision=1)

    def test_partial_scope_and_incomplete_are_not_normal(self):
        raw,meta,auto,current=self.fixture()
        packet=scoped_review(meta,auto,current,'reviewer',[0,600],['流量波动减弱候选'],'spot')
        self.assertEqual([e['id'] for e in packet['events']],['yes','pending','ignored'])
        self.assertEqual([e['id'] for e in packet['boundary_events']],['boundary'])
        self.assertEqual(packet['training_scope']['positive_event_ids'],['yes'])
        self.assertEqual(packet['training_scope']['pending_event_ids'],['pending'])
        self.assertFalse(packet['training_scope']['background_labels_allowed'])
        self.assertNotIn('history',packet);self.assertTrue(packet['automatic']['excerpt'])
        with self.assertRaises(ValueError):scoped_review(meta,auto,current,'reviewer',[0,600],['流量波动减弱候选'],'complete')
        current['events'][1]['status']='reviewed'
        complete=scoped_review(meta,auto,current,'reviewer',[0,600],['流量波动减弱候选'],'complete')
        self.assertTrue(complete['training_scope']['background_labels_allowed'])
        self.assertEqual(complete['training_scope']['excluded_boundary_intervals'],[[590,600]])
        current['events']=[e for e in current['events'] if e['id']!='yes']
        with self.assertRaises(ValueError):scoped_review(meta,auto,current,'reviewer',[0,600],['流量波动减弱候选'],'complete')

    def test_confirmation_dedup_content_identity_and_new_review(self):
        raw,meta,auto,current=self.fixture();packet=scoped_review(meta,auto,current,'reviewer',[0,600],['流量波动减弱候选'],'spot')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'exports'
            with self.assertRaises(ValueError):save_package(root,meta,raw,auto,packet)
            self.assertFalse(root.exists())
            first=save_package(root,meta,raw,auto,packet,True);folder=Path(first['folder']);mtime=(folder/'original.csv').stat().st_mtime_ns
            self.assertEqual(len(first['created']),4)
            repeat=save_package(root,dict(meta,name='renamed.csv'),raw,auto,packet,True)
            self.assertEqual(repeat['created'],[]);self.assertEqual(len(repeat['reused']),4)
            self.assertEqual((folder/'original.csv').stat().st_mtime_ns,mtime)
            current['events'][3]['note']='outside scope changed';current['revision']=99
            unchanged=scoped_review(meta,auto,current,'reviewer',[0,600],['流量波动减弱候选'],'spot')
            self.assertEqual(unchanged,packet)
            current['events'][0]['note']='review updated'
            changed=scoped_review(meta,auto,current,'reviewer',[0,600],['流量波动减弱候选'],'spot')
            second=save_package(root,meta,raw,auto,changed,True)
            self.assertEqual(len(second['created']),2);self.assertEqual(len(list(folder.glob('*.csv'))),1)
            self.assertEqual(len(list((folder/'automatic').glob('*.json'))),1)
            self.assertEqual(len(list((folder/'reviewed').glob('*.json'))),2)
            self.assertEqual((folder/'original.csv').read_bytes(),raw)
            # Existing unexpected content must never be overwritten.
            (folder/'original.csv').write_bytes(b'changed')
            with self.assertRaises(ValueError):save_package(root,meta,raw,auto,changed,True)
