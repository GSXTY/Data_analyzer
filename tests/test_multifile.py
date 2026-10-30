import json,tempfile,threading,unittest,urllib.request,urllib.error
from pathlib import Path
import viewer
from event_store import EventStore


class MultiFileTests(unittest.TestCase):
    def test_switch_files_preserves_annotations_and_original_bytes(self):
        previous=(viewer.recording,viewer.event_store,viewer.recording_cache)
        viewer.recording_cache=viewer.RecordingCache()
        with tempfile.TemporaryDirectory() as temp:
            viewer.recording=None;viewer.event_store=EventStore(Path(temp)/'events.db')
            server=viewer.ThreadingHTTPServer(('127.0.0.1',0),viewer.Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path,raw=None,packet=None):
                req=urllib.request.Request(f'http://127.0.0.1:{server.server_port}'+path,data=raw if raw is not None else None if packet is None else json.dumps(packet).encode(),headers={'Content-Type':'application/json','X-Filename':'fixture.csv'})
                with client.open(req,timeout=10) as r:
                    data=r.read();return data if path.startswith('/api/current-csv') else json.loads(data)
            a=b'time,flow\n2026-01-01 00:00:00,1\n2026-01-01 00:00:30,2\n'
            b=a.replace(b',2\n',b',3\n')
            try:
                first=request('/api/open',raw=a);fid=first['file_id']
                event=dict(id='manual',start=1,end=10,mode='raw',type='波形异常',source='manual',status='reviewed',channels=['flow'],note='A only',updated_at='now')
                request('/api/events',packet=dict(file_id=fid,revision=0,events=[event]))
                self.assertEqual(request('/api/current-csv?file_id='+fid),a)
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/close',packet=dict(file_id='wrong-file'))
                self.assertEqual(error.exception.code,409)
                self.assertTrue(request('/api/close',packet=dict(file_id=fid))['closed'])
                with self.assertRaises(urllib.error.HTTPError):request('/api/meta')
                request('/api/open',raw=a)
                self.assertEqual(request('/api/events?file_id='+fid)['events'],[event])
                second=request('/api/open',raw=b);self.assertNotEqual(second['file_id'],fid)
                self.assertEqual(request('/api/events?file_id='+second['file_id'])['events'],[])
                cached_a=viewer.recording_cache.get(fid)
                activated=request('/api/activate',packet=dict(file_id=fid));self.assertTrue(activated['hit'])
                self.assertIs(viewer.recording,cached_a)
                self.assertEqual(request('/api/events?file_id='+fid)['events'],[event])
                request('/api/activate',packet=dict(file_id=second['file_id']))
                self.assertTrue(request('/api/cache/release',packet=dict(file_id=fid))['released'])
                self.assertFalse(request('/api/activate',packet=dict(file_id=fid))['hit'])
                with self.assertRaises(urllib.error.HTTPError) as error:request('/api/current-csv?file_id='+fid)
                self.assertEqual(error.exception.code,409)
                with self.assertRaises(urllib.error.HTTPError):request('/api/open',raw=b'bad,csv\nwrong,1')
                self.assertEqual(request('/api/meta')['file_id'],second['file_id'])
                request('/api/open',raw=a)
                self.assertEqual(request('/api/events?file_id='+fid)['events'],[event])
                self.assertEqual(request('/api/current-csv?file_id='+fid),a)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5);viewer.recording,viewer.event_store,viewer.recording_cache=previous
