"""Bounded in-memory LRU of parsed CSVs; no disk copies or annotations."""
from collections import OrderedDict

class RecordingCache:
    def __init__(self,max_items=3,max_bytes=768*1024*1024):
        self.max_items=max_items;self.max_bytes=max_bytes;self.items=OrderedDict()

    @staticmethod
    def size(recording):
        arrays=[recording.x,recording.display_x,*recording.channels.values(),*recording.engineering.values()]
        return len(recording.raw_csv)+sum(a.nbytes for a in {id(a):a for a in arrays}.values())

    def put(self,recording):
        if recording is None:return
        key=recording.meta['file_id'];self.items.pop(key,None)
        self.items[key]=(recording,self.size(recording))
        while len(self.items)>1 and (len(self.items)>self.max_items or sum(v[1] for v in self.items.values())>self.max_bytes):self.items.popitem(last=False)

    def get(self,key):
        entry=self.items.get(key)
        if entry:self.items.move_to_end(key)
        return entry[0] if entry else None

    def release(self,key):
        return self.items.pop(key,None) is not None
