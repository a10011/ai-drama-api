"""
Pipeline Task Queue — 基于 SQLite 的消息队列
零外部依赖，天然持久化，进程重启不丢任务
"""
import json
import logging
import time
import sqlite3
import threading
import os

logger = logging.getLogger(__name__)

DB_PATH = "/www/wwwroot/api.mzsh.top/data/short_drama.db"

AGENT_QUEUES = {
    "director":   "queue_director",
    "script":     "queue_script",
    "character":  "queue_character",
    "storyboard": "queue_storyboard",
    "scene":      "queue_scene",
    "audio":      "queue_audio",
    "video":      "queue_video",
    "composite":  "queue_composite",
}

COMPLETED_TOPIC = {
    "director":   "event:director.done",
    "script":     "event:script.done",
    "character":  "event:character.done",
    "storyboard": "event:storyboard.done",
    "scene":      "event:scene.done",
    "audio":      "event:audio.done",
    "video":      "event:video.done",
    "composite":  "event:composite.done",
}

# 初始化队列表
def _init():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pipeline_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                queue TEXT NOT NULL,
                payload TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                created_at REAL DEFAULT 0,
                consumed_at REAL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_queue_status ON pipeline_tasks(queue, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pipeline_tasks_created ON pipeline_tasks(queue, created_at) WHERE status='pending'")
        # 迁移：如果旧表没有 payload 列，添加
        try:
            conn.execute("ALTER TABLE pipeline_tasks ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")
        except:
            pass  # 已存在或表是新创建的
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[TaskQueue] 初始化失败: {e}")

_init()


class TaskQueue:
    """SQLite 任务队列"""

    @staticmethod
    def push(queue: str, data: dict):
        """入队"""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO pipeline_tasks (queue, payload, status, created_at) VALUES (?, ?, 'pending', ?)",
                (queue, json.dumps(data, ensure_ascii=False), time.time())
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[TaskQueue] push 失败 [{queue}]: {e}")

    @staticmethod
    def pop(queue: str, timeout: int = 5) -> dict:
        """出队（FIFO，锁定一条待处理任务）"""
        try:
            conn = sqlite3.connect(DB_PATH)
            # 查找最早的一条 pending 任务
            row = conn.execute(
                "SELECT id, payload FROM pipeline_tasks WHERE queue=? AND status='pending' ORDER BY created_at ASC LIMIT 1",
                (queue,)
            ).fetchone()
            if row:
                task_id, payload = row
                # 原子锁定
                conn.execute(
                    "UPDATE pipeline_tasks SET status='processing', consumed_at=? WHERE id=? AND status='pending'",
                    (time.time(), task_id)
                )
                conn.commit()
                if conn.total_changes > 0:
                    conn.close()
                    return json.loads(payload)
            conn.close()
        except Exception as e:
            logger.error(f"[TaskQueue] pop 失败 [{queue}]: {e}")
        return None

    @staticmethod
    def complete(task_id: int):
        """标记任务完成并删除"""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM pipeline_tasks WHERE id=?", (task_id,))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[TaskQueue] complete 失败: {e}")

    @staticmethod
    def retry(task_id: int):
        """任务失败，恢复为 pending"""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE pipeline_tasks SET status='pending', consumed_at=0 WHERE id=?",
                (task_id,)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.error(f"[TaskQueue] retry 失败: {e}")

    @staticmethod
    def cleanup(max_age_hours=24):
        """清理过期任务记录"""
        try:
            cutoff = time.time() - max_age_hours * 3600
            conn = sqlite3.connect(DB_PATH)
            count = conn.execute(
                "DELETE FROM pipeline_tasks WHERE consumed_at > 0 AND consumed_at < ?",
                (cutoff,)
            ).rowcount
            conn.commit()
            conn.close()
            if count > 0:
                logger.info(f"[TaskQueue] 清理 {count} 条过期任务")
        except Exception as e:
            logger.error(f"[TaskQueue] cleanup 失败: {e}")

    @staticmethod
    def count_pending(queue: str) -> int:
        """查看队列中待处理任务数"""
        try:
            conn = sqlite3.connect(DB_PATH)
            count = conn.execute(
                "SELECT COUNT(*) FROM pipeline_tasks WHERE queue=? AND status='pending'",
                (queue,)
            ).fetchone()[0]
            conn.close()
            return count
        except:
            return 0


# 后台清理线程（每 30 分钟清理一次）
def _cleanup_loop():
    while True:
        time.sleep(1800)
        try:
            TaskQueue.cleanup()
        except Exception as e:
            logger.error(f"[TaskQueue] cleanup loop error: {e}")

_thread = threading.Thread(target=_cleanup_loop, daemon=True)
_thread.start()


# 兼容旧 API 的包装器
class MQ:
    def push(self, queue: str, data: dict):
        TaskQueue.push(queue, data)

    def pop(self, queue: str, timeout: int = 5) -> dict:
        return TaskQueue.pop(queue, timeout)

    def publish(self, channel: str, data: dict):
        """事件发布 — 直接存 topic 表，供前端轮询消费"""
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO pipeline_tasks (queue, payload, status, created_at) VALUES (?, ?, 'done', ?)",
                (f"topic_{channel}", json.dumps(data, ensure_ascii=False), time.time())
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"[TaskQueue] publish 失败 [{channel}]: {e}")

    def subscribe(self, *channels):
        """订阅事件 — 返回通道列表"""
        return channels

    def get_message(self, channels: list, timeout: float = 1.0) -> dict:
        """从 topic 队列获取最新事件"""
        try:
            conn = sqlite3.connect(DB_PATH)
            placeholders = ",".join(["?" for _ in channels])
            row = conn.execute(
                f"""SELECT payload FROM pipeline_tasks 
                    WHERE queue IN ({placeholders}) AND status='done' 
                    ORDER BY created_at DESC LIMIT 1""",
                channels
            ).fetchone()
            conn.close()
            if row:
                return json.loads(row[0])
        except:
            pass
        return None

    def close(self):
        pass  # SQLite 不需要关闭


mq = MQ()
