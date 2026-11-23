"""Process-wide, per-key admission for AI requests. Never expose credentials."""
import threading
import time
from collections import deque
from contextlib import contextmanager


class AIChannelsBusy(RuntimeError):
    pass


class AIKeyPool:
    def __init__(self, keys, parallel=1):
        self.keys = tuple(keys)
        self.parallel = parallel
        self.active = [0] * len(keys)
        self.until = [0.0] * len(keys)
        self.next = 0
        self.waiters = deque()
        self.condition = threading.Condition()

    @contextmanager
    def acquire(self, excluded=(), timeout=180):
        deadline = time.monotonic() + timeout
        ticket = object()
        with self.condition:
            self.waiters.append(ticket)
            try:
                while True:
                    now = time.monotonic()
                    available = [i for i in range(len(self.keys)) if i not in excluded and self.until[i] <= now]
                    if not available:
                        raise AIChannelsBusy('AI 通道暂时不可用，请稍后重新生成未完成项')
                    index = next((i for step in range(len(self.keys))
                                  if (i := (self.next + step) % len(self.keys)) in available
                                  and self.active[i] < self.parallel), None)
                    if self.waiters[0] is ticket and index is not None:
                        self.active[index] += 1
                        self.next = (index + 1) % len(self.keys)
                        break
                    remaining = deadline - now
                    if remaining <= 0:
                        raise AIChannelsBusy('AI 通道繁忙，请稍后重新生成未完成项')
                    self.condition.wait(remaining)
            finally:
                self.waiters.remove(ticket)
                self.condition.notify_all()
        try:
            yield index, self.keys[index]
        finally:
            with self.condition:
                self.active[index] -= 1
                self.condition.notify_all()

    def cooldown(self, index, seconds):
        with self.condition:
            self.until[index] = max(self.until[index], time.monotonic() + seconds)
            self.condition.notify_all()

    def stats(self):
        with self.condition:
            return dict(keys=len(self.keys), active=sum(self.active), per_key_parallel=self.parallel,
                        waiting=len(self.waiters),
                        cooling=sum(deadline > time.monotonic() for deadline in self.until))


_lock = threading.Lock()
_pools = {}


def key_pool(base_url, keys, parallel):
    # Private in-memory identity only; neither keys nor hashes appear in API/logs.
    identity = (base_url, tuple(keys), parallel)
    with _lock:
        if identity not in _pools:
            _pools[identity] = AIKeyPool(keys, parallel)
        return _pools[identity]
