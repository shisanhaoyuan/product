"""Thread-safe, exclusive phase timings. No prompts, credentials or product facts."""
import copy
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar

_CURRENT = ContextVar('generation_timing', default=None)


class GenerationTimings:
    def __init__(self, initial=None):
        self.lock = threading.RLock()
        self.stages = copy.deepcopy((initial or {}).get('stages', {}))
        self.rows = copy.deepcopy((initial or {}).get('rows', {}))
        self.active = {}

    @contextmanager
    def measure(self, stage, sku=None):
        parent = _CURRENT.get()
        span = dict(stage=stage, sku=sku, start=time.perf_counter(), children=0.0, parent=parent)
        with self.lock:
            self.active[id(span)] = span
        token = _CURRENT.set((self, span))
        try:
            yield
        finally:
            end = time.perf_counter()
            with self.lock:
                elapsed = end - span['start']
                self._add(self.stages, stage, max(0, elapsed - span['children']) * 1000)
                if sku:
                    self._add(self.rows.setdefault(sku, {}), stage, max(0, elapsed - span['children']) * 1000)
                self.active.pop(id(span), None)
                if parent and parent[0] is self:
                    parent[1]['children'] += elapsed
            _CURRENT.reset(token)

    @staticmethod
    def _add(target, stage, milliseconds, count=1):
        entry = target.setdefault(stage, dict(ms=0, count=0))
        entry['ms'] = round(entry['ms'] + milliseconds, 2)
        entry['count'] += count

    def add(self, stage, milliseconds):
        with self.lock:
            self._add(self.stages, stage, milliseconds)

    def snapshot(self):
        with self.lock:
            stages, rows = copy.deepcopy(self.stages), copy.deepcopy(self.rows)
            now = time.perf_counter()
            for span in self.active.values():
                children = sum(now - child['start'] for child in self.active.values()
                               if child['parent'] and child['parent'][1] is span)
                ms = max(0, now - span['start'] - span['children'] - children) * 1000
                self._add(stages, span['stage'], ms, 0)
                if span['sku']:
                    self._add(rows.setdefault(span['sku'], {}), span['stage'], ms, 0)
            return dict(version=1, mode='exclusive_worker_time', stages=stages, rows=rows)


@contextmanager
def phase(stage):
    current = _CURRENT.get()
    if current:
        with current[0].measure(stage, current[1]['sku']):
            yield
    else:
        yield


class TimedAI:
    def __init__(self, client, timings, sku):
        self.client, self.timings, self.sku = client, timings, sku
        self.settings = client.settings

    def query(self, purpose, payload):
        with self.timings.measure('ai_response', self.sku):
            return self.client.query(purpose, payload)
