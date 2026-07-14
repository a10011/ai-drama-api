"""
Pipeline 步骤缓存 — 每个智能体的结果自动保存到本地
按 project_id + stage 索引，失败时可复用
"""
import json, os, time, logging, hashlib
from datetime import datetime

logger = logging.getLogger(__name__)

PIPELINE_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "pipeline_cache")


def _ensure_pipe_dir():
    os.makedirs(PIPELINE_CACHE_DIR, exist_ok=True)


def _stage_key(project_id: str, stage: str) -> str:
    return f"{project_id}_{stage}".replace(" ", "_")


def _stage_path(project_id: str, stage: str) -> str:
    _ensure_pipe_dir()
    return os.path.join(PIPELINE_CACHE_DIR, f"{_stage_key(project_id, stage)}.json")


def save_stage_result(project_id: str, stage: str, agent_id: str, action: str,
                      input_params: dict, output: dict, success: bool):
    record = {
        "project_id": project_id,
        "stage": stage,
        "agent_id": agent_id,
        "action": action,
        "input": input_params,
        "output": output,
        "success": success,
        "timestamp": time.time(),
        "created_at": datetime.now().isoformat()
    }
    path = _stage_path(project_id, stage)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    logger.info(f"pipeline 缓存已保存: {stage} -> {path}")
    return path


def load_stage_result(project_id: str, stage: str) -> dict:
    path = _stage_path(project_id, stage)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"pipeline 缓存读取失败 {path}: {e}")
        return {}


def get_stage_output(project_id: str, stage: str) -> dict:
    record = load_stage_result(project_id, stage)
    if record and record.get("success"):
        return record.get("output", {})
    return {}


def get_all_stage_outputs(project_id: str) -> dict:
    _ensure_pipe_dir()
    outputs = {}
    for fname in os.listdir(PIPELINE_CACHE_DIR):
        if not fname.startswith(project_id) or not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(PIPELINE_CACHE_DIR, fname), "r") as f:
                record = json.load(f)
            if record.get("success"):
                outputs[record["stage"]] = record.get("output", {})
        except Exception:
            continue
    return outputs


def clear_project_cache(project_id: str):
    _ensure_pipe_dir()
    for fname in os.listdir(PIPELINE_CACHE_DIR):
        if fname.startswith(project_id) and fname.endswith(".json"):
            os.remove(os.path.join(PIPELINE_CACHE_DIR, fname))
    logger.info(f"pipeline 缓存已清除: project={project_id}")