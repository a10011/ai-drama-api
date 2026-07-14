"""QuickCreate processor — directly calls Agnes for video/image generation"""
import json, logging, time, threading, traceback
from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/queue", tags=["极简一键"])

QUEUE_FILE = "/www/wwwroot/api.mzsh.top/data/task_queue.json"
_lock = threading.Lock()
_queue = []
_processing = False

def _load():
    global _queue
    try:
        with open(QUEUE_FILE) as f: _queue = json.load(f)
    except:
        _queue = []

def _save():
    with open(QUEUE_FILE, "w") as f:
        json.dump(_queue, f, ensure_ascii=False, indent=2)

_load()


@router.post("/submit")
async def submit_task(request: Request):
    body = await request.json()
    uid = getattr(request.state, "user_id", 0) or body.get("user_id", 0)
    prompt = body.get("prompt", "").strip()
    if not prompt:
        return {"success": False, "error": "请输入内容"}
    ctype = body.get("type", "drama")
    task = {
        "id": str(int(time.time() * 1000000)),
        "type": ctype,
        "prompt": prompt,
        "user_id": uid,
        "status": "queued",
        "progress": 0,
        "stages": [
            {"key": "script", "label": "内容策划", "status": "pending"},
            {"key": "video", "label": "视频生成", "status": "pending"},
            {"key": "finish", "label": "合成输出", "status": "pending"},
        ],
        "video_url": "",
        "error": "",
        "created_at": int(time.time()),
    }
    with _lock:
        _queue.append(task)
        pos = len(_queue) - 1
        _save()
    return {
        "success": True,
        "data": {
            "id": task["id"],
            "project_id": task["id"],
            "queue_position": pos,
        },
    }


@router.get("/status/{task_id}")
async def get_status(task_id: str):
    with _lock:
        for t in _queue:
            if t["id"] == task_id:
                return {"success": True, "data": {
                    "queue_position": _queue.index(t),
                    "status": t["status"],
                    "project_id": t.get("project_id", ""),
                    "error": t.get("error", ""),
                }}
    return {"success": False, "error": "任务不存在"}


@router.get("/progress/{task_id}")
async def get_progress(task_id: str):
    with _lock:
        for t in _queue:
            if t["id"] == task_id:
                return {
                    "success": True,
                    "data": {
                        "status": t["status"],
                        "progress": t.get("progress", 0),
                        "stages": t.get("stages", []),
                        "video_url": t.get("video_url", ""),
                        "error": t.get("error", ""),
                        "finished": t["status"] in ("completed", "failed"),
                    },
                }
    return {"success": False, "error": "任务不存在"}


@router.get("/templates")
async def get_templates(type: str = "drama"):
    t = {
        "ad": ["智能手表，开启智能生活", "花茶，一口品到天然", "蓝牙音箱，时刻享受音乐"],
        "drama": ["古风诀别：将军与白衣女子的断崖之约", "城市甜宠：前台小姐与傲娇CEO", "悬疑反转：古城堡的秘密"],
        "promo": ["新店开业活动宣传片", "企业文化宣传片", "产品发布会宣传片"],
    }
    return {"success": True, "data": t.get(type, t["drama"])}


@router.get("/list")
async def list_tasks(request: Request):
    uid = getattr(request.state, "user_id", 0)
    with _lock:
        tasks = [t for t in _queue if t["user_id"] == uid]
    return {"success": True, "data": tasks}


# ===== Background Processor =====

def _set_stage(task, idx, status, progress=None):
    with _lock:
        stages = task.get("stages", [])
        if idx < len(stages):
            stages[idx]["status"] = status
        if progress is not None:
            task["progress"] = progress
        _save()


def _process():
    global _processing
    while True:
        task = None
        with _lock:
            if not _processing and _queue and _queue[0]["status"] == "queued":
                task = _queue[0]
                task["status"] = "running"
                _processing = True
                _save()
        if not task:
            time.sleep(2)
            continue

        try:
            ctype = task.get("type", "drama")
            prompt = task.get("prompt", "")

            # Stage 1: content planning
            _set_stage(task, 0, "running", 5)
            import sys
            sys.path.insert(0, "/www/wwwroot/api.mzsh.top")

            # For drama type, generate a script description
            if ctype == "drama":
                from services.model_client import UnifiedModel
                script_result = UnifiedModel.llm(
                    prompt=f"你是一个短剧编剧。根据以下创意，输出一个30秒短剧的分镜描述（200字以内）：{prompt}\n\n直接输出分镜描述文本，不要输出任何代码或格式。",
                    system="你是一个专业的AI短剧编剧。输出纯文本描述，不要包含任何markdown格式。",
                    model="agnes-2.0-flash",
                    timeout=30,
                    max_tokens=1024,
                )
                desc = script_result.data if hasattr(script_result, "data") else str(script_result)
            else:
                desc = prompt

            _set_stage(task, 0, "completed", 20)

            # Stage 2: video generation via Agnes
            _set_stage(task, 1, "running", 30)
            from services.ai_providers import AgnesAIProvider
            ag = AgnesAIProvider()

            # Generate image first? No — simpler to just generate video directly
            video_urls = ag.generate_video(prompt=desc, duration=15)
            if not video_urls:
                raise Exception("Agnes 视频生成返回空")

            _set_stage(task, 1, "completed", 80)

            # Stage 3: finish
            _set_stage(task, 2, "running", 90)
            with _lock:
                task["video_url"] = video_urls[0]
                task["status"] = "completed"
                task["progress"] = 100
                task["stages"][2]["status"] = "completed"
                _save()

        except Exception as e:
            logger.error(f"[Queue] 任务失败: {e}\n{traceback.format_exc()}")
            with _lock:
                task["status"] = "failed"
                task["error"] = str(e)[:200]
                _save()

        finally:
            with _lock:
                # Remove completed/failed from queue after processing
                if _queue and _queue[0]["id"] == task["id"]:
                    _queue.pop(0)
                    _save()
                _processing = False


# Start processor thread
_thread = threading.Thread(target=_process, daemon=True)
_thread.start()
logger.info("[Queue] Background processor started")
