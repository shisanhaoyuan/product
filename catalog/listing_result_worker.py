"""Recoverable read-only feed polling. Never resubmit feeds or write inventory."""
import asyncio
import json
import time
import uuid
from types import SimpleNamespace

from fastapi import HTTPException

from .database import write_lock
from .listing_ai_client import ListingAISettings


def enqueue(db,aid,actor_id):
    db.execute('''INSERT INTO listing_result_tasks
        (attempt_id,actor_id,state,next_at,tries,token,lease_until,last_error) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(attempt_id)
        DO UPDATE SET state=excluded.state,next_at=excluded.next_at,actor_id=excluded.actor_id,last_error='',token='',lease_until=0 ''',
        (aid,actor_id,'pending',int(time.time())+15,0,'',0,''))


def authorized(db,app,actor_id):
    if not app.state.auth:
        return {}
    user=db.execute('SELECT id,role,active,must_change_password FROM users WHERE id=?',(actor_id,)).fetchone()
    if not user or not user['active'] or user['role']!='admin' or user['must_change_password']:
        raise HTTPException(403,'原提交管理员权限已失效，后台查询暂停；请由管理员手动查询')
    return dict(user)


def tick(app):
    """At most one due task. Each process uses a lease, feed refresh has its own lock."""
    catalog=app.state.catalog;token=uuid.uuid4().hex;task=None
    with catalog.connect() as db:
        write_lock(db)
        record=db.execute("""SELECT t.*,a.batch_id,a.state AS attempt_state,a.results_json FROM listing_result_tasks t
            JOIN walmart_listing_attempts a ON a.id=t.attempt_id
            WHERE t.state IN ('pending','running','repair_pending') AND t.next_at<=? AND t.lease_until<?
            ORDER BY t.next_at,t.attempt_id LIMIT 1""",(int(time.time()),int(time.time()))).fetchone()
        if not record:
            return False
        task=dict(record)
        db.execute("UPDATE listing_result_tasks SET state='running',token=?,lease_until=? WHERE attempt_id=?",
                   (token,int(time.time())+300,task['attempt_id']))
    state='pending';error='';repair=False
    try:
        with catalog.connect() as db:
            user=authorized(db,app,task['actor_id'])
        if task['repair_job_id']:
            from .listing_generation_jobs import POOL, run_job
            with catalog.connect() as db:
                job=db.execute('SELECT * FROM listing_generation_jobs WHERE id=?',(task['repair_job_id'],)).fetchone()
                if job and job['state']=='running' and job['updated_at']<time.time()-600:
                    db.execute("UPDATE listing_generation_jobs SET state='interrupted',message=? WHERE id=? AND state='running'",
                        ('服务中断后修复未完成，已保存资料保留，请重新生成并校验',job['id']))
                    job=db.execute('SELECT * FROM listing_generation_jobs WHERE id=?',(job['id'],)).fetchone()
            if not job or job['state'] in ('failed','interrupted'):
                state='paused';error='自动修复草稿未完成，已保存资料保留；请在批次详情重新生成并校验'
            elif job['state']=='complete':
                state='complete'
            else:
                state='repair_pending'
                if job['state']=='queued':
                    # run_job claims queued -> running transactionally, so a
                    # restart may safely dispatch the same durable job again.
                    from .listing_ai_client import ListingAIClient
                    POOL.submit(run_job,catalog,app.state.walmart_publisher,job['id'],ListingAIClient())
        elif task['attempt_state'] in ('complete','rejected'):
            state='complete'
            repair=any(v.get('status')=='failed' and v.get('ingestion_status')=='DATA_ERROR' for v in json.loads(task['results_json']).values())
        else:
            from .walmart_publishing import refresh_attempt
            request=SimpleNamespace(app=app,state=SimpleNamespace(user=user))
            result=refresh_attempt(catalog,task['batch_id'],task['attempt_id'],request,sync_inventory=False)
            if result['state']=='complete':
                state='complete'
                repair=any(v.get('status')=='failed' and v.get('ingestion_status')=='DATA_ERROR' for v in result['results'].values())
    except HTTPException as exc:
        error=str(exc.detail)
        state='paused' if exc.status_code in (403,404) else 'pending'
    except Exception:
        error='后台查询连接异常，稍后继续查询原批次，不会重复提交'
    if repair and not ListingAISettings().configured:
        repair=False
        error='平台资料失败已记录，AI 未配置；请配置后在批次详情重新生成并校验'
    if repair:
        # Commit intent first. A crash before/after queueing is recovered through
        # start_job's durable identity rather than issuing another submission.
        with catalog.connect() as db:
            write_lock(db)
            db.execute("UPDATE listing_result_tasks SET state='repair_pending' WHERE attempt_id=? AND token=?",(task['attempt_id'],token))
        try:
            with catalog.connect() as db:
                authorized(db,app,task['actor_id'])
            from .listing_generation_jobs import start_job
            job=start_job(catalog,app.state.walmart_publisher,task['batch_id'],task['actor_id'])
            with catalog.connect() as db:
                write_lock(db)
                db.execute('UPDATE listing_result_tasks SET repair_job_id=? WHERE attempt_id=? AND token=?',
                           (job['id'],task['attempt_id'],token))
            state='complete' if job['state']=='complete' else 'repair_pending'
        except Exception:
            state='paused'
            error='平台资料失败已记录，自动修复草稿未启动，请在批次详情重新生成并校验'
    with catalog.connect() as db:
        write_lock(db)
        db.execute('''UPDATE listing_result_tasks SET state=?,next_at=?,tries=tries+1,token='',lease_until=0,last_error=?
            WHERE attempt_id=? AND token=?''',(state,int(time.time())+min(1800,30*2**min(task['tries'],6)),error,
                                               task['attempt_id'],token))
    return True


async def serve(app):
    while True:
        try:
            await asyncio.to_thread(tick,app)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # Health endpoints remain available; durable tasks survive a restart.
        await asyncio.sleep(15)
