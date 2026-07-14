"""
pipeline_router.py — 启动 pipeline 子进程 + 查询状态
"""
import logging, subprocess, os, json
from fastapi import APIRouter
from pydantic import BaseModel
from routers.context_agent import read, write

logger = logging.getLogger("api.pipeline")
router = APIRouter(prefix="/api/v1/pipeline", tags=["流水线"])

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PIPELINE_SCRIPT = os.path.join(BASE, "run_pipeline.py")

class StartRequest(BaseModel):
    project_id: str = "default"
    premise: str = ""
    script: str = ""

@router.post("/start")
def start_pipeline(req: StartRequest):
    """启动 pipeline 子进程"""
    pid = req.project_id
    premise = req.premise
    script = req.script

    # 初始化上下文
    init_context(pid, premise, script)

    # 启动子进程
    cmd = ["python3", PIPELINE_SCRIPT, pid, premise]
    logger.info(f"[{pid}] 启动 pipeline: {' '.join(cmd)}")
    subprocess.Popen(
        cmd,
        cwd=os.path.dirname(PIPELINE_SCRIPT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True
    )

    return {"success": True, "data": {"project_id": pid, "message": "pipeline started"}}

def init_context(pid, premise, script):
    """初始化项目上下文"""
    ctx = {
        "premise": premise,
        "script": script,
        "title": "",
        "genre": "都市",
        "progress": 0,
        "current_stage": "",
        "log": "初始化中...",
        "director_analysis": {},
        "characters": [],
        "shots": [],
        "videos": [],
        "audio": [],
        "subtitles": [],
        "bgm": {},
        "done": False,
        "error": None,
        "_updated": 0
    }
    write(pid, ctx)
