"""
Pipeline Task Queue — SQLite based message broker
No external dependencies, no Redis, no Kafka
"""
import json
import logging
import time
import sqlite3
import threading
import os

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db"))

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

def _init():
    conn = None
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
        try:
            conn.execute("ALTER TABLE pipeline_tasks ADD COLUMN payload TEXT NOT NULL DEFAULT '{}'")
        except:
            pass
        conn.commit()
    except Exception as e:
        logger.error(f"[TaskQueue] init failed: {e}")
    finally:
        if conn:
            conn.close()

_init()


class TaskQueue:

    @staticmethod
    def push(queue: str, data: dict):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO pipeline_tasks (queue, payload, status, created_at) VALUES (?, ?, 'pending', ?)",
                (queue, json.dumps(data, ensure_ascii=False), time.time())
            )
            conn.commit()
        except Exception as e:
            logger.error(f"[TaskQueue] push failed [{queue}]: {e}")
        finally:
            if conn:
                conn.close()

    @staticmethod
    def pop(queue: str, timeout: int = 5) -> dict:
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            row = conn.execute(
                "SELECT id, payload FROM pipeline_tasks WHERE queue=? AND status='pending' ORDER BY created_at ASC LIMIT 1",
                (queue,)
            ).fetchone()
            if row:
                task_id, payload = row
                conn.execute(
                    "UPDATE pipeline_tasks SET status='processing', consumed_at=? WHERE id=? AND status='pending'",
                    (time.time(), task_id)
                )
                conn.commit()
                if conn.total_changes > 0:
                    result = json.loads(payload)
                    conn.close()
                    return result
        except Exception as e:
            logger.error(f"[TaskQueue] pop failed [{queue}]: {e}")
        finally:
            if conn:
                conn.close()
        return None

    @staticmethod
    def complete(task_id: int):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute("DELETE FROM pipeline_tasks WHERE id=?", (task_id,))
            conn.commit()
        except Exception as e:
            logger.error(f"[TaskQueue] complete failed: {e}")
        finally:
            if conn:
                conn.close()

    @staticmethod
    def retry(task_id: int):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "UPDATE pipeline_tasks SET status='pending', consumed_at=0 WHERE id=?",
                (task_id,)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"[TaskQueue] retry failed: {e}")
        finally:
            if conn:
                conn.close()

    @staticmethod
    def cleanup(max_age_hours=24):
        conn = None
        try:
            cutoff = time.time() - max_age_hours * 3600
            conn = sqlite3.connect(DB_PATH)
            count = conn.execute(
                "DELETE FROM pipeline_tasks WHERE consumed_at > 0 AND consumed_at < ?",
                (cutoff,)
            ).rowcount
            conn.commit()
            if count > 0:
                logger.info(f"[TaskQueue] cleaned {count} old tasks")
        except Exception as e:
            logger.error(f"[TaskQueue] cleanup failed: {e}")
        finally:
            if conn:
                conn.close()

    @staticmethod
    def count_pending(queue: str) -> int:
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            count = conn.execute(
                "SELECT COUNT(*) FROM pipeline_tasks WHERE queue=? AND status='pending'",
                (queue,)
            ).fetchone()[0]
            return count
        except:
            return 0
        finally:
            if conn:
                conn.close()


def _cleanup_loop():
    while True:
        time.sleep(1800)
        try:
            TaskQueue.cleanup()
        except Exception as e:
            logger.error(f"[TaskQueue] cleanup loop error: {e}")

_thread = threading.Thread(target=_cleanup_loop, daemon=True)
_thread.start()


class MQ:
    def push(self, queue: str, data: dict):
        TaskQueue.push(queue, data)

    def pop(self, queue: str, timeout: int = 5) -> dict:
        return TaskQueue.pop(queue, timeout)

    def publish(self, channel: str, data: dict):
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            conn.execute(
                "INSERT INTO pipeline_tasks (queue, payload, status, created_at) VALUES (?, ?, 'done', ?)",
                (f"topic_{channel}", json.dumps(data, ensure_ascii=False), time.time())
            )
            conn.commit()
        except Exception as e:
            logger.warning(f"[TaskQueue] publish failed [{channel}]: {e}")
        finally:
            if conn:
                conn.close()

    def subscribe(self, *channels):
        return channels

    def get_message(self, channels: list, timeout: float = 1.0) -> dict:
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH)
            placeholders = ",".join(["?" for _ in channels])
            row = conn.execute(
                f"""SELECT payload FROM pipeline_tasks 
                    WHERE queue IN ({placeholders}) AND status='done' 
                    ORDER BY created_at DESC LIMIT 1""",
                channels
            ).fetchone()
            if row:
                return json.loads(row[0])
        except:
            pass
        finally:
            if conn:
                conn.close()
        return None

    def close(self):
        pass

mq = MQ()
