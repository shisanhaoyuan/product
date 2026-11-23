"""Bounded request lanes and executors; overload never retries platform writes."""
import asyncio
import os
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from starlette.responses import JSONResponse


def setting(name, default, minimum=1, maximum=10000):
    value = int(os.environ.get(name, default))
    if not minimum <= value <= maximum:
        raise ValueError(f'{name} 必须在 {minimum}–{maximum} 之间')
    return value


class WorkQueueFull(RuntimeError):
    pass


async def background_call(fn, *args):
    """Finish the current DB-using worker before shutdown closes its pool."""
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            pass  # Preserve cancellation; the durable job owns failure reporting.
        raise


class BoundedExecutor(ThreadPoolExecutor):
    def __init__(self, max_workers, max_pending, **kwargs):
        super().__init__(max_workers=max_workers, **kwargs)
        self.capacity = max_workers + max_pending
        self.slots = threading.BoundedSemaphore(self.capacity)
        self.lock = threading.Lock()
        self.outstanding = 0

    def submit(self, fn, /, *args, **kwargs):
        if not self.slots.acquire(blocking=False):
            raise WorkQueueFull('后台任务队列已满')
        with self.lock:
            self.outstanding += 1
        try:
            future = super().submit(fn, *args, **kwargs)
        except BaseException:
            self._release()
            raise
        future.add_done_callback(lambda _: self._release())
        return future

    def _release(self):
        with self.lock:
            self.outstanding -= 1
        self.slots.release()

    def stats(self):
        with self.lock:
            return dict(capacity=self.capacity, outstanding=self.outstanding,
                        workers=self._max_workers)


class AdmissionBusy(Exception):
    pass


def request_lane(scope):
    path, method = scope['path'], scope['method']
    if '/images/' in path or path.startswith('/api/listing-media/'):
        return 'images'
    if (method not in ('GET', 'HEAD', 'OPTIONS') or path.startswith('/api/store-items')
            or path in ('/api/export.csv', '/api/export.json')):
        return 'work'
    return 'read'


class RequestGate:
    def __init__(self, active=48, waiting=64, work=8, images=8, timeout=2):
        self.limit, self.wait_limit, self.timeout = active, waiting, timeout
        self.limits = {'work': work, 'images': images}
        self.active = Counter()
        self.waiting = self.rejected = self.peak = 0
        self.condition = asyncio.Condition()

    def available(self, lane):
        return (self.active.total() < self.limit
                and self.active[lane] < self.limits.get(lane, self.limit))

    @asynccontextmanager
    async def enter(self, lane):
        async with self.condition:
            if not self.available(lane):
                if self.waiting >= self.wait_limit:
                    self.rejected += 1
                    raise AdmissionBusy()
                self.waiting += 1
                try:
                    await asyncio.wait_for(self.condition.wait_for(lambda: self.available(lane)),
                                           timeout=self.timeout)
                except TimeoutError:
                    self.rejected += 1
                    raise AdmissionBusy() from None
                finally:
                    self.waiting -= 1
            self.active[lane] += 1
            self.peak = max(self.peak, self.active.total())
        try:
            yield
        finally:
            async with self.condition:
                self.active[lane] -= 1
                self.condition.notify_all()

    def stats(self):
        return dict(active=dict(self.active), waiting=self.waiting, rejected=self.rejected,
                    peak=self.peak, limit=self.limit, waiting_limit=self.wait_limit,
                    lane_limits=self.limits)


def busy_response():
    return JSONResponse({'detail': '系统当前请求较多，请稍后重试；上架或改价操作请先核对处理记录，避免重复提交'},
                        status_code=503, headers={'Retry-After': '3', 'Cache-Control': 'no-store'})


class AdmissionMiddleware:
    def __init__(self, app, gate):
        self.app, self.gate = app, gate

    async def __call__(self, scope, receive, send):
        if (scope['type'] != 'http' or not scope['path'].startswith('/api/')
                or scope['path'] == '/api/health'):
            return await self.app(scope, receive, send)
        # Only admission failures are converted. Never hide an application error
        # after a response has started, or replay a request that may have written.
        async with self._admit(scope, receive, send) as accepted:
            if accepted:
                await self.app(scope, receive, send)

    @asynccontextmanager
    async def _admit(self, scope, receive, send):
        context = self.gate.enter(request_lane(scope))
        try:
            await context.__aenter__()
        except AdmissionBusy:
            await busy_response()(scope, receive, send)
            yield False
        else:
            try:
                yield True
            finally:
                await context.__aexit__(None, None, None)
