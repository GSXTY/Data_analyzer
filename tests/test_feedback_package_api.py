import json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
import viewer
from event_store import EventStore


class FeedbackPackageApiTests(unittest.TestCase):
    def test_prepare_confirmation_revision_and_idempotent_save(self):
        previous=(viewer.recording,viewer.event_store)
        with tempfile.TemporaryDirectory() as temp:
            raw=b'time,flow\n2026-01-01 00:00:00,1\n2026-01-01 00:20:00,2\n'
            viewer.recording=viewer.Recording(raw,'fixture.csv');meta=viewer.recording.meta;fid=meta['file_id']
            viewer.event_store=EventStore(Path(temp)/'events.db')
            event=dict(id='e',start=100,end=120,mode='raw',type='流量波动减弱候选',note='',source='detector',status='candidate',channels=['flow'],updated_at='now')
            scan=viewer.event_store.write(meta,0,[event],scan=dict(events=[event],settings={'version':'test'},scan_start=0,scan_end=1200))
            viewer.event_store.write(meta,1,[dict(event,status='reviewed')])
            server=viewer.ThreadingHTTPServer(('127.0.0.1',0),viewer.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(path,packet):
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=json.dumps(packet).encode(),headers={'Content-Type':'application/json'})
                with client.open(req,timeout=10) as r:return json.load(r)
            args=dict(file_id=fid,run_id=scan['run_id'],reviewer='tester',reviewed_range=[0,600],reviewed_types=['流量波动减弱候选'],review_mode='complete')
            try:
                preview=post('/api/feedback/prepare',args);self.assertEqual(preview['confirmed_count'],1)
                self.assertFalse((Path(temp)/'feedback_exports').exists())
                with self.assertRaises(urllib.error.HTTPError) as error:post('/api/feedback/export',dict(args,expected_revision=preview['revision'],confirmed=False))
                self.assertEqual(error.exception.code,400);self.assertFalse((Path(temp)/'feedback_exports').exists())
                with self.assertRaises(urllib.error.HTTPError) as error:post('/api/feedback/export',dict(args,expected_revision=1,confirmed=True))
                self.assertEqual(error.exception.code,409)
                result=post('/api/feedback/export',dict(args,expected_revision=preview['revision'],confirmed=True))
                self.assertEqual(len(result['created']),4)
                again=post('/api/feedback/export',dict(args,expected_revision=preview['revision'],confirmed=True));self.assertEqual(again['created'],[])
                scoped=post('/api/feedback',dict(args,kind='reviewed'));self.assertEqual(scoped['version'],2)
                self.assertEqual(scoped['reviewed_range'],[0,600]);self.assertNotIn('history',scoped)
                self.assertEqual((Path(result['folder'])/'original.csv').read_bytes(),raw)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5);viewer.recording,viewer.event_store=previous
