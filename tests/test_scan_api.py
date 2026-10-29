import json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
import viewer
from event_store import EventStore


class ScanApiTests(unittest.TestCase):
    def test_pages_match_and_metadata_edits_do_not_modify_annotations(self):
        previous=(viewer.recording,viewer.event_store)
        with tempfile.TemporaryDirectory() as temp:
            viewer.recording=viewer.Recording(b'time,flow\n2026-01-01 00:00:00,1\n2026-01-01 00:00:30,2\n','fixture.csv')
            viewer.event_store=EventStore(Path(temp)/'events.db');meta=viewer.recording.meta;fid=meta['file_id']
            ids=[]
            for i in range(12):ids.append(viewer.event_store.write(meta,i,[],scan=dict(events=[],settings={'version':'machine-flow-v1'},scan_start=0,scan_end=30))['run_id'])
            before=viewer.event_store.read(fid);snapshot=viewer.event_store.feedback(meta,ids[-1],'automatic')
            server=viewer.ThreadingHTTPServer(('127.0.0.1',0),viewer.Handler)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path,packet=None):
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=None if packet is None else json.dumps(packet).encode(),headers={'Content-Type':'application/json'})
                with client.open(req,timeout=10) as r:return json.load(r)
            try:
                page=request('/api/scans?file_id='+fid);self.assertEqual(page['total'],12);self.assertEqual(len(page['scans']),10)
                matched=request('/api/scans/match',dict(file_id=fid,start=0,end=30))['match'];self.assertEqual(matched['run_id'],ids[-1])
                self.assertEqual(viewer.event_store.read(fid),before)
                request('/api/scans/edit',dict(file_id=fid,run_id=ids[-1],title='manual-review',archived=True))
                self.assertEqual(request('/api/scans?file_id='+fid)['total'],11)
                self.assertEqual(request('/api/scans?file_id='+fid+'&state=archived')['total'],1)
                self.assertEqual(request('/api/scans?file_id='+fid+'&selected='+ids[-1])['selected']['title'],'manual-review')
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/scans/edit',dict(file_id='wrong',run_id=ids[-1],archived=False))
                self.assertEqual(error.exception.code,409)
                self.assertEqual(viewer.event_store.feedback(meta,ids[-1],'automatic'),snapshot)
                self.assertEqual(viewer.event_store.read(fid),before)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)
                viewer.recording,viewer.event_store=previous
