"""持久重试队列 — 模型调用失败后按递增间隔重试，直到成功"""
import sqlite3
import json
import time
import logging
import threading
import concurrent
import concurrent.futures
from typing import Optional, Dict, Any, List

logger = logging.getLogger("retry_queue")

DB = "/www/wwwroot/api.mzsh.top/data/short_drama.db"

RETRY_INTERVALS = [5, 10, 30, 60, 60, 120]

def init_retry_table():
    """初始化重试队列表"""
    try:
        conn = sqlite3.connect(DB, timeout=10)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS retry_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                model_name TEXT NOT NULL,
                call_type TEXT NOT NULL DEFAULT 'image',
                call_args TEXT NOT NULL DEFAULT '{}',
                retry_count INTEGER DEFAULT 0,
                next_retry_at REAL NOT NULL,
                status TEXT DEFAULT 'pending',
                result TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to init retry table: {e}")

def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def enqueue(pipeline_id: str, stage: str, model_name: str, 
            call_type: str, call_args: dict) -> int:
    """入队一个重试任务"""
    init_retry_table()
    now = time.time()
    delay = RETRY_INTERVALS[0] * 60
    try:
        conn = _get_conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO retry_queue (pipeline_id, stage, model_name, call_type, call_args,
                                      retry_count, next_retry_at, status, created_at, updated_at)
            VALUES (?,?,?,?,?,0,?,'pending',?,?)
        """, (pipeline_id, stage, model_name, call_type, json.dumps(call_args),
              now + delay, now, now))
        rid = c.lastrowid
        conn.commit()
        conn.close()
        logger.info(f"[RetryQueue] 入队 #{rid}: pipeline={pipeline_id} stage={stage} model={model_name} next_retry={delay//60}min后")
        return rid
    except Exception as e:
        logger.error(f"[RetryQueue] 入队失败: {e}")
        return 0

def get_next_interval(retry_count: int) -> int:
    idx = min(retry_count, len(RETRY_INTERVALS) - 1)
    return RETRY_INTERVALS[idx] * 60

def check_due() -> List[Dict]:
    """检查到期的重试任务"""
    init_retry_table()
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM retry_queue WHERE status='pending' AND next_retry_at <= ? ORDER BY next_retry_at ASC LIMIT 20",
            (time.time(),)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"[RetryQueue] check_due failed: {e}")
        return []

def mark_processing(retry_id: int):
    try:
        conn = _get_conn()
        conn.execute("UPDATE retry_queue SET status='processing' WHERE id=?", (retry_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"[RetryQueue] mark_processing failed: {e}")

def mark_done(retry_id: int, result: dict):
    try:
        conn = _get_conn()
        conn.execute("UPDATE retry_queue SET status='done', result=?, updated_at=? WHERE id=?",
                     (json.dumps(result), time.time(), retry_id))
        conn.commit()
        conn.close()
        logger.info(f"[RetryQueue] ✅ #{retry_id} 重试成功")
    except Exception as e:
        logger.warning(f"[RetryQueue] mark_done failed: {e}")

def mark_failed(retry_id: int, error: str):
    try:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM retry_queue WHERE id=?", (retry_id,)).fetchone()
        if not row:
            conn.close()
            return
        
        new_count = row["retry_count"] + 1
        delay = get_next_interval(new_count)
        next_at = time.time() + delay
        
        conn.execute("""
            UPDATE retry_queue SET status='pending', retry_count=?, next_retry_at=?,
                   error=?, updated_at=? WHERE id=?
        """, (new_count, next_at, error[:500], time.time(), retry_id))
        conn.commit()
        conn.close()
        logger.warning(f"[RetryQueue] ⚡ #{retry_id} 第{new_count}次重试失败，{delay//60}分钟后重试 | {error[:100]}")
    except Exception as e:
        logger.warning(f"[RetryQueue] mark_failed error: {e}")

def give_up(retry_id: int, error: str):
    try:
        conn = _get_conn()
        conn.execute("UPDATE retry_queue SET status='failed', error=?, updated_at=? WHERE id=?",
                     (error[:500], time.time(), retry_id))
        conn.commit()
        conn.close()
        logger.error(f"[RetryQueue] 💀 #{retry_id} 所有重试耗尽，放弃 | {error[:100]}")
    except Exception as e:
        logger.warning(f"[RetryQueue] give_up error: {e}")

def get_pipeline_retries(pipeline_id: str) -> List[Dict]:
    try:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM retry_queue WHERE pipeline_id=? ORDER BY created_at DESC",
            (pipeline_id,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[RetryQueue] get_pipeline_retries failed: {e}")
        return []

def get_pending_summary() -> dict:
    try:
        conn = _get_conn()
        total = conn.execute("SELECT COUNT(*) as c FROM retry_queue").fetchone()
        pending = conn.execute("SELECT COUNT(*) as c FROM retry_queue WHERE status='pending'").fetchone()
        processing = conn.execute("SELECT COUNT(*) as c FROM retry_queue WHERE status='processing'").fetchone()
        done = conn.execute("SELECT COUNT(*) as c FROM retry_queue WHERE status='done'").fetchone()
        failed = conn.execute("SELECT COUNT(*) as c FROM retry_queue WHERE status='failed'").fetchone()
        conn.close()
        return {
            "total": total["c"] if total else 0,
            "pending": pending["c"] if pending else 0,
            "processing": processing["c"] if processing else 0,
            "done": done["c"] if done else 0,
            "failed": failed["c"] if failed else 0,
        }
    except Exception as e:
        logger.warning(f"[RetryQueue] get_pending_summary failed: {e}")
        return {"total": 0, "pending": 0, "processing": 0, "done": 0, "failed": 0}


_model_semaphores: Dict[str, threading.Semaphore] = {}
def _get_model_sem(model_name: str) -> threading.Semaphore:
    if model_name not in _model_semaphores:
        _model_semaphores[model_name] = threading.Semaphore(2)
    return _model_semaphores[model_name]

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="retry")

def _process_one_task(task: dict):
    """处理单个重试任务"""
    rid = task["id"]
    model_name = task.get("model_name", "unknown")
    sem = _get_model_sem(model_name)
    
    acquired = sem.acquire(timeout=30)
    if not acquired:
        logger.warning(f"[RetryWorker] ⏳ #{rid} 模型 {model_name} 通道满，稍后重试")
        return
    
    try:
        mark_processing(rid)
        call_type = task["call_type"]
        call_args_raw = task.get("call_args", "{}")
        call_args = json.loads(call_args_raw) if isinstance(call_args_raw, str) else call_args_raw
        
        logger.info(f"[RetryWorker] 🔄 #{rid} 开始重试 model={model_name} type={call_type} retry_count={task['retry_count']}")
        
        if call_type == "image":
            from services.model_client import generate_image
            result = generate_image(
                prompt=call_args.get("prompt", ""),
                size=call_args.get("size"),
                preferred=call_args.get("preferred"),
                pipeline_id=""
            )
        elif call_type == "video":
            from services.model_client import generate_video
            result = generate_video(
                prompt=call_args.get("prompt", ""),
                image_url=call_args.get("image_url", ""),
                audio_url=call_args.get("audio_url", ""),
                resolution=call_args.get("resolution", "720P"),
                pipeline_id=""
            )
        else:
            mark_failed(rid, f"Unknown call_type: {call_type}")
            return
        
        if result.get("success"):
            mark_done(rid, result)
            _try_resume_pipeline(task["pipeline_id"], task["stage"], result)
        elif result.get("retrying"):
            mark_failed(rid, "Unexpected retry loop")
        else:
            if task["retry_count"] >= len(RETRY_INTERVALS) - 1:
                give_up(rid, result.get("error", "max retries"))
            else:
                mark_failed(rid, result.get("error", "retry failed"))
    except Exception as e:
        logger.error(f"[RetryWorker] 处理 #{rid} 出错: {e}")
        try:
            mark_failed(rid, str(e))
        except Exception as ex_: logger.warning(f"[retry_manager]  {ex_}")
    finally:
        sem.release()

def retry_worker_loop():
    """后台重试工作线程主循环"""
    logger.info("[RetryWorker] 后台重试线程启动（并行模式，每模型限流2，池大小4），每30秒扫描")
    submitted: set = set()
    
    while True:
        try:
            time.sleep(30)
            due_tasks = check_due()
            if not due_tasks:
                continue
            
            due_tasks.sort(key=lambda t: t.get("next_retry_at", 0))
            
            stats_pending = 0
            for task in due_tasks:
                rid = task["id"]
                if rid in submitted:
                    continue
                
                submitted.add(rid)
                future = _executor.submit(_process_one_task, task)
                future.add_done_callback(lambda f, r=rid: submitted.discard(r))
                stats_pending += 1
            
            if stats_pending > 0:
                logger.info(f"[RetryWorker] 📋 提交 {stats_pending} 个重试任务到并行队列 "
                           f"(已提交未完成: {len(submitted)})")
        except Exception as e:
            logger.error(f"[RetryWorker] 主循环出错: {e}")
            time.sleep(10)


def _try_resume_pipeline(pipeline_id: str, stage: str, result: dict):
    """尝试恢复 pipeline 执行"""
    try:
        conn = sqlite3.connect(DB, timeout=10)
        conn.row_factory = sqlite3.Row
        pipe_row = conn.execute(
            "SELECT step_results, script_text, genre, project_id FROM pipelines WHERE id=?",
            (pipeline_id,)
        ).fetchone()
        if not pipe_row:
            conn.close()
            return
        
        step_results = json.loads(pipe_row[0] or "{}")
        stage_key_map = {
            "image": "character_result",
        }
        key = stage_key_map.get(stage, stage)
        
        if stage == "image":
            step_results["character_result"] = {"data": {"figure_url": result.get("url", "")}, "success": True}
        elif stage == "video":
            step_results["video_result"] = {"data": {"video_url": result.get("url", "")}, "success": True}
        
        conn.execute("UPDATE pipelines SET step_results=?, status='pending_resume' WHERE id=?",
                     (json.dumps(step_results, ensure_ascii=False), pipeline_id))
        conn.commit()
        
        script_text = pipe_row[1] or ""
        genre = pipe_row[2] or ""
        project_id = str(pipe_row[3])
        
        # 尝试恢复 pipeline
        try:
            from routers.pipeline import _executor as pipe_executor, _run_pipeline
            pipe_executor.submit(_run_pipeline, pipeline_id, project_id, genre, [],
                                 8, script_text, resume_from=step_results)
            logger.info(f"[RetryWorker] ✅ Pipeline {pipeline_id} 恢复执行: stage={stage}")
        except Exception as e:
            logger.warning(f"[RetryWorker] 恢复 pipeline 失败: {e}")
        
        conn.close()
    except Exception as e:
        logger.error(f"[RetryWorker] 恢复 pipeline 异常: {e}")