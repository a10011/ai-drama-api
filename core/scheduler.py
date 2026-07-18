import os
import time, logging
from .mq_client import mq, AGENT_QUEUES
logger = logging.getLogger(__name__)

STAGE_ORDER = ["script", "director", "character", "storyboard", "scene", "video", "composite"]  # video=音画同出, audio removed

STAGE_DAG = {}

def _set_status(pipeline_id, status, video_url=""):
    conn = None
    try:
        import sqlite3
        conn = sqlite3.connect(os.environ.get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")))
        conn.execute("UPDATE pipelines SET status=?, updated=? WHERE id=?", (status, time.time(), pipeline_id))
        if video_url:
            conn.execute("UPDATE projects SET video_url=?, status='completed' WHERE pipeline_id=?", (video_url, pipeline_id))
        conn.commit()
    except Exception as e:
        logger.warning(f"set_status failed: {e}")
    finally:
        if conn:
            conn.close()

def start_pipeline(pipeline_id, user_id, init_data=None):
    _set_status(pipeline_id, "running:script")
    task = {"pipeline_id": pipeline_id, "user_id": user_id, "stage": "script", "data": init_data or {}}
    mq.push(AGENT_QUEUES["script"], task)
    logger.info(f"[Scheduler] {pipeline_id} started -> script")
    return pipeline_id

def get_next_stage(current):
    try:
        idx = STAGE_ORDER.index(current)
        return STAGE_ORDER[idx + 1] if idx + 1 < len(STAGE_ORDER) else None
    except ValueError:
        return None
