import unittest
from unittest.mock import patch
from recording_cache import RecordingCache

class CacheTests(unittest.TestCase):
    def test_lru_budget_and_release(self):
        class Item:
            def __init__(self,key,size):self.meta={'file_id':key};self.size=size
        with patch.object(RecordingCache,'size',staticmethod(lambda r:r.size)):
            cache=RecordingCache(max_items=3,max_bytes=100)
            a,b,c=Item('a',40),Item('b',40),Item('c',40)
            cache.put(a);cache.put(b);self.assertIs(cache.get('a'),a)
            cache.put(c);self.assertIsNone(cache.get('b'));self.assertIs(cache.get('a'),a)
            self.assertTrue(cache.release('a'));self.assertIsNone(cache.get('a'))
            large=Item('large',200);cache.put(large)
            self.assertEqual(len(cache.items),1);self.assertIs(cache.get('large'),large)
