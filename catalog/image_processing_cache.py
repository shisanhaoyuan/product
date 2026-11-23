"""Bounded image preprocessing reuse, isolated to one generation job."""
import threading
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT = ContextVar('image_processing_cache', default=None)


class KeyedLocks:
    """Only identical active requests share a lock; idle keys are discarded."""
    def __init__(self):
        self.guard = threading.Lock()
        self.entries = {}

    @contextmanager
    def hold(self, key):
        with self.guard:
            entry = self.entries.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        try:
            with entry[0]:
                yield
        finally:
            with self.guard:
                entry[1] -= 1
                if not entry[1]:
                    del self.entries[key]


class ImageProcessingCache:
    def __init__(self, capacity=64, max_bytes=32 * 1024 * 1024):
        self.capacity, self.max_bytes = capacity, max_bytes
        self.entries = OrderedDict()
        self.bytes = self.hits = self.misses = 0
        self.lock = threading.RLock()
        self.stripes = [threading.RLock() for _ in range(32)]

    @contextmanager
    def activate(self):
        token = _CURRENT.set(self)
        try:
            yield
        finally:
            _CURRENT.reset(token)

    def get(self, key):
        with self.lock:
            entry = self.entries.get(key)
            if entry:
                self.entries.move_to_end(key)
                self.hits += 1
            return entry

    def put(self, key, value):
        size = len(value[1])
        with self.lock:
            if key in self.entries or size > self.max_bytes:
                return
            self.entries[key] = value
            self.bytes += size
            self.misses += 1
            while len(self.entries) > self.capacity or self.bytes > self.max_bytes:
                _, removed = self.entries.popitem(last=False)
                self.bytes -= len(removed[1])

    def stats(self):
        with self.lock:
            return dict(hits=self.hits, prepared=self.misses, entries=len(self.entries), bytes=self.bytes)


def current_cache():
    return _CURRENT.get()
