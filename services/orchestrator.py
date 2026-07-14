"""
管线编排器 v3 — "管家"模块（断点续跑 + 自动重试版）
=====================================================
v3 新增:
  1. stage_callback: 每阶段完成后回调（含成功/失败），供 pipeline.py 持久化
  2. 失败不阻断整个管线：标记失败但继续执行不依赖该阶段的后续阶段
  3. retry_count 跟踪: 通过 pipeline.py 的回调实现 2分钟后自动重试，最多3次

原则: 所有 agent 不改代码；失败就停，不降级不假数据。
"""

from __future__ import annotations

import asyncio
import time
import json
import os
import logging
from typing import Dict, Any, List, Optional, Callable, Tuple, Set
from utils.path_util import local_path_to_url
from app_config import BASE_URL
from dataclasses import dataclass, field
from enum import Enum

import httpx
import subprocess

logger = logging.getLogger("orchestrator")

# ═══════════════════════════════════════════════════════════════════════════
# Stage definition & DAG
# ═══════════════════════════════════════════════════════════════════════════

class Stage(Enum):
    DIRECTOR    = "director"
    SCRIPT      = "script"
    CHARACTER   = "character"
    STORYBOARD  = "storyboard"
    SCENE       = "scene"
    TTS         = "tts"
    SUBTITLE    = "subtitle"
    BGM         = "bgm"
    VIDEO       = "video"
    COMPOSITE   = "composite"
    CINEMATOGRAPHER = "cinematographer"
    WARDROBE        = "wardrobe"
    SFX             = "sfx"

STAGE_DEPENDS: Dict[Stage, List[Stage]] = {
    Stage.DIRECTOR:   [],
    Stage.SCRIPT:     [Stage.DIRECTOR],
    Stage.CHARACTER:  [Stage.DIRECTOR, Stage.SCRIPT],
    Stage.STORYBOARD: [Stage.DIRECTOR, Stage.SCRIPT, Stage.CHARACTER],
    Stage.SCENE:      [Stage.STORYBOARD],
    Stage.TTS:        [Stage.STORYBOARD, Stage.CHARACTER],
    Stage.SUBTITLE:   [Stage.SCRIPT, Stage.TTS],
    Stage.BGM:        [Stage.STORYBOARD],
    Stage.VIDEO:      [Stage.STORYBOARD, Stage.SCENE, Stage.TTS],
    Stage.COMPOSITE:       [Stage.VIDEO],
    Stage.CINEMATOGRAPHER: [Stage.STORYBOARD],
    Stage.WARDROBE:        [Stage.STORYBOARD, Stage.CHARACTER],
    Stage.SFX:             [Stage.SCENE],
}

STAGE_ORDER: List[Stage] = [
    Stage.DIRECTOR, Stage.SCRIPT, Stage.CHARACTER, Stage.STORYBOARD,
    Stage.CINEMATOGRAPHER, Stage.WARDROBE,
    Stage.SCENE,
    Stage.SFX,
    Stage.TTS, Stage.SUBTITLE, Stage.BGM,
    Stage.VIDEO, Stage.COMPOSITE,
]

STAGE_LABELS = {
    Stage.DIRECTOR: "导演分析", Stage.SCRIPT: "剧本创作",
    Stage.CHARACTER: "角色设计", Stage.STORYBOARD: "分镜生成",
    Stage.SCENE: "场景生成", Stage.TTS: "配音合成",
    Stage.SUBTITLE: "字幕生成", Stage.BGM: "BGM配乐",
    Stage.VIDEO: "视频生成", Stage.COMPOSITE: "视频合成",
    Stage.CINEMATOGRAPHER: "摄影指导", Stage.WARDROBE: "服化道设计",
    Stage.SFX: "特效设计",
}

STAGE_ICONS = {
    Stage.DIRECTOR: "🎬", Stage.SCRIPT: "📝",
    Stage.CHARACTER: "🎭", Stage.STORYBOARD: "🎞️",
    Stage.SCENE: "🏔️", Stage.TTS: "🎙️",
    Stage.SUBTITLE: "💬", Stage.BGM: "🎵",
    Stage.VIDEO: "🎬", Stage.COMPOSITE: "📺",
    Stage.CINEMATOGRAPHER: "📷", Stage.WARDROBE: "👗",
    Stage.SFX: "✨",
}

# Agent 调用配置
AGENT_BASE_URL = "http://127.0.0.1:8000/api/v1/agents/execute"
HTTP_TIMEOUT = 300
VIDEO_HTTP_TIMEOUT = 3000
PORTRAIT_HTTP_TIMEOUT = 150

# ═══════════════════════════════════════════════════════════════════════════
# Agent 参数规格
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class AgentParamSpec:
    """声明一个 Agent 的参数需求"""
    agent_id: str
    action: str
    # 参数映射: context_path → agent_param_name
    # 支持 "." 分隔的嵌套路径, 如 "results.director.tasks.character_design"
    param_map: Dict[str, str] = field(default_factory=dict)
    # 固定参数
    fixed_params: Dict[str, Any] = field(default_factory=dict)
    # pre_hook: 调 agent 前执行的钩子名 (修改 ctx._computed)
    pre_hook: str = ""
    # post_process: agent 返回后执行的钩子名
    post_process: str = ""
    # timeout 覆盖 (秒)
    timeout: int = 0


# ── 🔍 数据自检：每个阶段需要的上游数据（空跑拦截）──
CRITICAL_PARAMS = {
    # stage → {param: "提示"}
    "director":        {},
    "script":          {"script_text": "剧本创作需要synopsis→找导演分析补剧本数据"},
    "character":       {"script_text": "角色设计需要剧本→找剧本创作补"},
    "storyboard":      {"script_text": "分镜需要剧本→找剧本创作补"},
    "cinematographer": {"shots": "摄影指导需要分镜→找分镜生成补镜头列表"},
    "wardrobe":        {"shots": "服化道设计需要分镜→找分镜生成补镜头列表"},
    "sfx":             {"shots": "特效设计需要分镜→找分镜生成补镜头列表"},
    "scene":           {"shots": "场景图需要分镜→找分镜生成补镜头列表"},
    "tts":             {"shots": "配音配音需要分镜→找分镜生成补台词数据"},
    "subtitle":        {"script_text": "字幕需要剧本→找剧本创作补", "tts_audio": "字幕需要配音→找配音合成补"},
    "bgm":             {},
    "video":           {"shots": "视频生成需要分镜→找分镜生成补镜头列表"},
    "composite":       {"clips": "合成需要视频片段→找视频生成补视频"},
}


# ── 🔬 级联故障诊断 ──
import re as _re_diag

class FailureReason:
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    QUOTA_EXHAUSTED = "quota_exhausted"       # 余额用完/免费额度耗尽 → 停
    TIMEOUT = "timeout"
    PROVIDER_DOWN = "provider_down"            # 上游宕机 → 可等
    BAD_REQUEST = "bad_request"               # 参数错误/prompt太长 → 可自修
    BUG = "bug"
    UPSTREAM_MISSING = "upstream_missing"      # 上游没产出 → 停
    CONTENT_FILTERED = "content_filtered"     # 内容审核拦截 → 可调prompt重试
    UNKNOWN = "unknown"
    
    # 哪些原因可以重试
    RETRYABLE = {RATE_LIMITED, TIMEOUT, PROVIDER_DOWN, BAD_REQUEST, CONTENT_FILTERED}
    # 哪些必须立即停
    FATAL = {AUTH_FAILED, QUOTA_EXHAUSTED, UPSTREAM_MISSING, BUG}

def diagnose_error(error_str: str):
    s = (error_str or "").lower()
    
    # 限流
    if _re_diag.search(r'429|rate.?limit|throttl|频次|quota.*exceed|too.?many.?request', s):
        m = _re_diag.search(r'(\d+)\s*(?:秒|second|s)\b', s)
        wait = int(m.group(1)) if m else 60
        return {"reason": FailureReason.RATE_LIMITED, "action": "wait_retry", 
                "detail": "触发API限流", "wait_s": min(wait, 300)}
    
    # 认证/余额
    if _re_diag.search(r'401|403|unauthorized|invalid.?api.?key|auth.*fail|余额|欠费|balance.*insufficient|account.*balance', s):
        return {"reason": FailureReason.AUTH_FAILED, "action": "notify_user",
                "detail": "账号认证失败或余额不足，需充值/更换密钥"}
    
    # 超时
    if _re_diag.search(r'timeout|timed.?out|超时', s):
        return {"reason": FailureReason.TIMEOUT, "action": "retry_longer",
                "detail": "上游响应超时"}
    
    # 模型不可用
    if _re_diag.search(r'503|502|unavailable|service.*error|internal.*error', s):
        return {"reason": FailureReason.PROVIDER_DOWN, "action": "switch_model",
                "detail": "模型服务不可用"}
    
    # 上游没数据
    if _re_diag.search(r'无剧本|数据不足|empty|none|null.*param|缺少', s):
        return {"reason": FailureReason.UPSTREAM_MISSING, "action": "fix_upstream",
                "detail": "上游阶段未产出所需数据"}
    # 内容审核拦截（可 sanitize 后重试）
    if _re_diag.search(r'content.*(filter|violat)|审核|违规|敏感词|policy|safety|nsfw|porn|血腥|暴力|图画.*含|detected.*content', s):
        return {"reason": FailureReason.CONTENT_FILTERED, "action": "sanitize_retry",
                "detail": "内容触发安全审核，自动清洗后重试"}
    
    # 参数/请求错误（可自修参数后重试）
    if _re_diag.search(r'400|422|invalid.*param|参数.*错|unexpected.*keyword|typeerror|valueerror|got an unexpected', s):
        return {"reason": FailureReason.BAD_REQUEST, "action": "fix_param",
                "detail": "请求参数错误，自动修复后重试"}
    
    # Python 代码异常（bug，重试无用，直接停报）
    if _re_diag.search(r'traceback|attributeerror|keyerror|indexerror|nameerror|modulenotfound|importerror|zerodivision', s):
        return {"reason": FailureReason.BUG, "action": "report",
                "detail": f"代码异常(需人工修复):{error_str[:80]}"}
    
    
    return {"reason": FailureReason.UNKNOWN, "action": "log_and_skip",
            "detail": f"未知错误:{error_str[:100]}"}

# 阶段→上游映射
STAGE_UPSTREAM = {
    "director":        None,
    "script":          "director",
    "character":       "script",
    "storyboard":      "script",
    "cinematographer": "storyboard",
    "wardrobe":        "storyboard",
    "sfx":             "storyboard",
    "scene":           "storyboard",
    "tts":             "script",
    "subtitle":        "script",
    "bgm":             "script",
    "video":           "storyboard",
    "composite":       "video",
}

AGENT_SPECS: Dict[Stage, AgentParamSpec] = {
    Stage.DIRECTOR: AgentParamSpec(
        agent_id="director", action="analyze_script",
        param_map={
            "script_text":    "script_text",
        },
    ),
    Stage.SCRIPT: AgentParamSpec(
        agent_id="script", action="generate_script",
        param_map={
            "synopsis":       "premise",
            "genre":          "genre",
            "project_id":     "project_id",
        },
        fixed_params={"genre": "都市"},
    ),
    Stage.CHARACTER: AgentParamSpec(
        agent_id="character", action="extract",
        param_map={
            "script_text":    "script_text",
            "director_task":  "director_task",
            "genre":          "genre",
        },
        pre_hook="extract_director_tasks",
        post_process="gen_portraits",
    ),
    Stage.STORYBOARD: AgentParamSpec(
        agent_id="storyboard", action="generate",
        param_map={
            "script_text":    "script_text",
            "characters":     "characters",
            "scenes": "scenes",
            "genre":          "genre",
            "director_beats": "director_beats",
        },
        pre_hook="build_storyboard_context",
    ),
    Stage.SCENE: AgentParamSpec(
        agent_id="scene", action="batch_generate",
        param_map={
            "shots":          "shots",
            "genre":          "genre",
            "characters":     "characters",
            "scene_masters":  "scene_masters",  # P0-5: 跨 resume 传递已生成的场景底图
            "script_text":    "script_text",    # 让场景智能体理解剧本，按剧情设计场景
        },
        post_process="inject_images_to_shots",
        timeout=1800,   # 场景图并发 i2i 实测需4分钟+，全局300s必超时
    ),
    Stage.TTS: AgentParamSpec(
        agent_id="tts", action="generate",
        param_map={
            "shots":                "shots",
            "character_voices":     "character_voices",
            "script_text":          "script_text",  # 让配音智能体理解剧本，把握情感语气
        },
        pre_hook="build_character_voices",
        post_process="inject_audio_to_shots",
    ),
    Stage.SUBTITLE: AgentParamSpec(
        agent_id="subtitle", action="generate",
        param_map={
            "script_text":    "script_text",
        },
    ),
    Stage.BGM: AgentParamSpec(
        agent_id="bgm", action="generate_bgm",
        param_map={
            "shots":         "shots",
            "script_text":   "script_text",  # 让BGM智能体理解剧本，按剧情情绪配乐
            "genre":         "genre",
        },
    ),
    Stage.VIDEO: AgentParamSpec(
        agent_id="video", action="generate",
        param_map={
            "shots":              "shots",
            "genre":              "genre",
            "title":              "title",
            "characters":         "characters",
            "director_analysis":  "director_analysis",
        },
        pre_hook="ensure_shot_media",
        timeout=VIDEO_HTTP_TIMEOUT,
    ),
    Stage.COMPOSITE: AgentParamSpec(
        agent_id="composite", action="composite",
        param_map={
            "clips":          "clips",
        },
        pre_hook="collect_composite_inputs",
    ),
    Stage.CINEMATOGRAPHER: AgentParamSpec(
        agent_id="cinematographer", action="design",
        param_map={
            "shots":     "shots",
            "script_text": "script_text",
            "genre":     "genre",
            "director_beats": "director_beats",
        },
        timeout=90,
    ),
    Stage.WARDROBE: AgentParamSpec(
        agent_id="wardrobe", action="design",
        param_map={
            "shots":      "shots",
            "characters": "characters",
            "script_text": "script_text",
            "genre":      "genre",
        },
        timeout=90,
    ),
    Stage.SFX: AgentParamSpec(
        agent_id="sfx", action="design",
        param_map={
            "shots":        "shots",
            "scene_images": "scene_images",
            "genre":        "genre",
            "script_text":  "script_text",
        },
        timeout=90,
    ),
}

# ═══════════════════════════════════════════════════════════════════════════
# PipelineContext — 统一数据池
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class PipelineContext:
    """大海 — 所有阶段数据统一存在这里"""
    # 输入
    synopsis: str = ""
    script_text: str = ""
    genre: str = "都市"
    title: str = ""
    user_script: str = ""
    polish_only: bool = False
    user_id: int = 0

    # 各阶段产出 (key = Stage.value)
    results: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    # 角色/场景等中间数据 (直接挂顶层)
    characters: List[Dict] = field(default_factory=list)
    character_portraits: Dict[str, str] = field(default_factory=dict)
    character_voices: Dict[str, Dict] = field(default_factory=dict)
    shots: List[Dict] = field(default_factory=list)
    scene_images: List[Dict] = field(default_factory=list)
    tts_audio: List[Dict] = field(default_factory=list)
    bgm_url: str = ""
    bgm_files: List[str] = field(default_factory=list)
    video_clips: List[Dict] = field(default_factory=list)
    subtitles: List[Dict] = field(default_factory=list)
    final_video: str = ""

    # 预计算参数池 (pre_hook 填充, _translate_params 优先读取)
    _computed: Dict[str, Any] = field(default_factory=dict)

    # 进度
    completed_stages: List[str] = field(default_factory=list)
    current_stage: str = ""
    pipeline_id: str = ""
    project_id: str = ""

    # 自动重试跟踪 (v3 新增)
    stage_retry_count: Dict[str, int] = field(default_factory=dict)

    def get(self, path: str, default: Any = None) -> Any:
        """从 Context 取数据, 支持嵌套路径 'results.director.tasks.character_design'"""
        # 优先从 _computed 池取
        if path in self._computed:
            return self._computed[path]

        parts = path.split(".")
        val: Any = self
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p, default)
            elif isinstance(val, list):
                try:
                    idx = int(p)
                    val = val[idx] if 0 <= idx < len(val) else default
                except ValueError:
                    val = val.get(p, default) if hasattr(val, 'get') else default
            elif hasattr(val, p):
                val = getattr(val, p, default)
            else:
                return default
        return val

    def set_result(self, stage: Stage, data: Dict[str, Any]):
        """存储一个阶段的原始结果"""
        self.results[stage.value] = data
        if stage.value not in self.completed_stages:
            self.completed_stages.append(stage.value)

    def snapshot(self) -> Dict[str, Any]:
        """序列化为可存 DB 的字典 (精简版，只存关键字段)"""
        return {
            "synopsis": self.synopsis,
            "script_text": self.script_text[:2000] if self.script_text else "",
            "genre": self.genre,
            "title": self.title,
            "results": self.results,
            "characters": self.characters[:20] if self.characters else [],
            "character_portraits": self.character_portraits,
            "shots": self.shots[:50] if self.shots else [],
            "scene_images": self.scene_images[:20] if self.scene_images else [],
            "tts_audio": self.tts_audio[:50] if self.tts_audio else [],
            "bgm_url": self.bgm_url,
            "bgm_files": self.bgm_files[:10] if self.bgm_files else [],
            "video_clips": self.video_clips[:30] if self.video_clips else [],
            "final_video": self.final_video,
            "completed_stages": self.completed_stages,
            "current_stage": self.current_stage,
            "stage_retry_count": self.stage_retry_count,
        }

    @classmethod
    def from_snapshot(cls, data: Dict[str, Any]) -> "PipelineContext":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        ctx = cls(**valid)
        # 恢复已完成阶段
        if data.get("results"):
            for stage_val in data.get("completed_stages", []):
                if stage_val not in ctx.completed_stages:
                    ctx.completed_stages.append(stage_val)
        return ctx


# ═══════════════════════════════════════════════════════════════════════════
# PipelineOrchestrator — 管家
# ═══════════════════════════════════════════════════════════════════════════


# ── 自动重试配置 ──
RETRY_CONFIG = {
    Stage.VIDEO:     {"max_retries": 6, "backoff_s": [10, 30, 60, 120, 300, 600]},
    Stage.COMPOSITE: {"max_retries": 4, "backoff_s": [15, 45, 120, 300]},
    Stage.SCENE:     {"max_retries": 3, "backoff_s": [10, 30, 60]},
    Stage.TTS:       {"max_retries": 2, "backoff_s": [10, 20]},
    Stage.BGM:       {"max_retries": 2, "backoff_s": [10, 20]},
    # LLM阶段（导演/剧本/角色/分镜/摄影/服化道/特效/字幕）: 1次重试
}
DEFAULT_RETRIES = 1
DEFAULT_BACKOFF = [5, 15]

class PipelineOrchestrator:
    """大纲管家: 接收用户剧本, 自动调度 13 个阶段, 支持 DAG 并行执行 + 断点续跑"""

    def __init__(self, context: PipelineContext, pipeline_id: str = "",
                 db_save: Optional[Callable] = None,
                 stage_callback: Optional[Callable] = None,
                 media_registry=None):
        self.ctx = context
        self.pipeline_id = pipeline_id or context.pipeline_id
        self._db_save = db_save          # fn(snapshot_dict) → 写 DB
        self._stage_callback = stage_callback  # fn(stage_name, status, data, error) → 写 DB (v3 新增)
        self._media_registry = media_registry
        self._on_progress: Optional[Callable] = None
        self.http_timeout = HTTP_TIMEOUT

        # Hook 注册表: hook_name → async method
        self._pre_hooks: Dict[str, Callable] = {
            "extract_director_tasks": self._hook_extract_director_tasks,
            "build_storyboard_context": self._hook_build_storyboard_context,
            "build_character_voices": self._hook_build_character_voices,
            "ensure_shot_media": self._hook_ensure_shot_media,
            "first_frame_connection": self._hook_first_frame_connection,
            "collect_composite_inputs": self._hook_collect_composite_inputs,
        }
        self._post_processes: Dict[str, Callable] = {
            "gen_portraits": self._post_gen_portraits,
            "inject_images_to_shots": self._post_inject_images_to_shots,
            "inject_audio_to_shots": self._post_inject_audio_to_shots,
        }

    def on_progress(self, callback: Callable):
        self._on_progress = callback

    def _enqueue_stage_retry(self, stage: Stage):
        """把失败阶段放入后台重试队列"""
        try:
            import sqlite3
            db = getattr(self, 'db_path', '/www/wwwroot/api.mzsh.top/data/short_drama.db')
            conn = sqlite3.connect(db, timeout=10)
            row_id = conn.execute(
                """INSERT INTO retry_queue (pipeline_id, stage, model_name, call_type, call_args, retry_count, max_retries, status, created_at, next_retry_at)
                   VALUES (?, ?, ?, ?, ?, 0, 100, 'pending', ?, ?)""",
                (self.pipeline_id, stage.value, 'auto', 'agent',
                 json.dumps({'stage': stage.value, 'project_id': self.ctx.project_id}),
                 time.strftime('%Y-%m-%d %H:%M:%S'), time.strftime('%Y-%m-%d %H:%M:%S'))
            ).lastrowid
            conn.commit()
            conn.close()
            logger.info(f"[管家] 阶段 {stage.value} 已入后台重试队列 (pipeline={self.pipeline_id}, id={row_id})")
        except Exception as e:
            logger.error(f"[管家] 入队失败: {e}")
    
    def _notify(self, stage: Stage, status: str, data: Any = None):
        if self._on_progress:
            try:
                self._on_progress(stage.value, status, data)
            except Exception as _pe:
                logger.warning(f"[管家] _notify失败: {_pe}")

    # ── 进度持久化 ──

    def _persist_snapshot(self):
        """每阶段完成后写 DB，支持断点续跑"""
        if self._db_save and self.pipeline_id:
            try:
                snap = self.ctx.snapshot()
                snap["pipeline_id"] = self.pipeline_id
                self._db_save(snap)
            except Exception as e:
                logger.warning(f"[管家] 持久化快照失败: {e}")

    def _persist_stage(self, stage: Stage, status: str, data: Dict = None, error: str = ""):
        """v3: 通过 stage_callback 持久化单阶段进度到 pipeline_progress 表"""
        if self._stage_callback and self.pipeline_id:
            try:
                self._stage_callback(
                    pipeline_id=self.pipeline_id,
                    stage=stage.value,
                    status=status,
                    data=data or {},
                    error=error,
                )
            except Exception as e:
                logger.warning(f"[管家] stage_callback 失败: {e}")

    # ── 参数翻译 (增强版: 优先读 _computed) ──

    def _translate_params(self, stage: Stage) -> Dict[str, Any]:
        """把 PipelineContext 翻译成 Agent 能懂的参数，优先从 _computed 池取"""
        spec = AGENT_SPECS.get(stage)
        if not spec:
            return {}

        params: Dict[str, Any] = dict(spec.fixed_params)

        for ctx_path, agent_key in spec.param_map.items():
            val = self.ctx.get(ctx_path)
            # Always pass the param, even if empty — agents handle empty inputs
            params[agent_key] = val if val is not None else None

        params["user_id"] = self.ctx.user_id
        return params

    async def _call_agent(self, stage: Stage) -> Tuple[bool, Dict[str, Any], str]:
        """调用 Agent — 智能诊断+自修+重试，全部交给 _do_call_agent_with_retry"""
        logger.info(f"[管家] 启动 {STAGE_LABELS[stage]} (智能诊断+自动修复)")
        return await self._do_call_agent(stage)

    async def _do_call_agent(self, stage: Stage) -> Tuple[bool, Dict[str, Any], str]:
        """调用 Agent (自动重试: 3次进程内 + 失败后入后台队列)"""
        spec = AGENT_SPECS.get(stage)
        if not spec:
            return False, {}, f"未找到阶段配置: {stage}"
        
        return await self._do_call_agent_with_retry(stage, spec)
    
    async def _do_call_agent_with_retry(self, stage: Stage, spec, attempt: int = 0) -> Tuple[bool, Dict[str, Any], str]:
        """智能重试：先诊断、再决定→可修自修/该停就停/该等信息上报
    确定性链路：按阶段合理重试（RETRY_CONFIG），不假成功、不偷偷降级换模型。
    重试到真没救才标 failed，保留诊断供后端排查。会员侧只感知最终成功。"""
        # 按阶段读重试配置（scene 3次、video 6次、tts/bgm 2次、LLM类 1次）
        _rc = RETRY_CONFIG.get(stage, {"max_retries": DEFAULT_RETRIES, "backoff_s": DEFAULT_BACKOFF})
        MAX_RETRIES = _rc.get("max_retries", DEFAULT_RETRIES)
        _backoff_s = _rc.get("backoff_s", DEFAULT_BACKOFF)
        BASE_WAIT = _backoff_s[0] if _backoff_s else 10
        
        # 记录错误用于递增退避策略
        self._retry_history = getattr(self, '_retry_history', {})
        stage_key = stage.value
        if stage_key not in self._retry_history:
            self._retry_history[stage_key] = {"errors": [], "diagnoses": []}

        # 1) 执行 pre_hook → 填充 ctx._computed
        if spec.pre_hook:
            hook_fn = self._pre_hooks.get(spec.pre_hook)
            if hook_fn:
                try:
                    await hook_fn()
                except Exception as e:
                    logger.error(f"[管家] pre_hook '{spec.pre_hook}' 失败: {e}")
                    return False, {}, f"预处理失败: {e}"

        # 2a) 数据自检：pre_hook 已跑完，现在检查关键参数
        critical = CRITICAL_PARAMS.get(stage.value, {})
        if critical:
            missing = []
            for param_name, hint in critical.items():
                val = self.ctx.get(param_name)
                if val is None or (isinstance(val, (str, list, dict)) and len(val) == 0):
                    missing.append(f"{param_name}({hint})")
            if missing:
                enriched = []
                for item in missing:
                    upstream = STAGE_UPSTREAM.get(stage.value, "?")
                    if upstream:
                        up_data = self.ctx.results.get(upstream, {})
                        up_err = up_data.get("error", up_data.get("_error", ""))
                        if up_err:
                            diag = diagnose_error(str(up_err))
                            item += f" | 上游[{upstream}]失败原因: {diag['detail']}"
                        else:
                            item += f" | 上游[{upstream}]未产出(可能未跑或被跳过)"
                    enriched.append(item)
                msg = f"🚫 数据不足，停止空跑！缺少: {'; '.join(enriched)}"
                logger.error(f"[管家] {STAGE_LABELS[stage]} {msg}")
                self._persist_stage(stage, "blocked", {}, msg)
                self._notify(stage, "blocked", {"error": msg, "missing": missing, "fix": "请先完成前置阶段"})
                return False, {"blocked": True, "missing": missing}, msg

        # 2) 翻译参数 (此时 _computed 已填充)
        params = self._translate_params(stage)
        if self.pipeline_id:
            params["pipeline_id"] = self.pipeline_id

        payload = {
            "agent_id": spec.agent_id,
            "action": spec.action,
            "params": params,
        }

        label = STAGE_LABELS.get(stage, stage.value)
        timeout = spec.timeout or self.http_timeout
        logger.info(f"[管家] → 调 {label} ({spec.agent_id}/{spec.action}) params={list(params.keys())}")

        self._notify(stage, "running")
        start = time.time()

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.post(AGENT_BASE_URL, json=payload)
                r.raise_for_status()
                resp = r.json()
        except Exception as e:
            elapsed = int((time.time() - start) * 1000)
            msg = f"{label} 调用异常: {e}"
            resp = {"success": False, "data": {}, "error": msg}

        elapsed = int((time.time() - start) * 1000)
        success = resp.get("success", False)
        data = resp.get("data", {})
        error_msg = resp.get("error", "")
        
        if success:
            self._retry_history.pop(stage_key, None)  # 成功后清除错误历史
            logger.info(f"[管家] ← {label} 完成 ({elapsed}ms)")
            self.ctx.set_result(stage, data)
            self._notify(stage, "completed", data)
            self._sync_top_level(stage, data)
            if spec.post_process:
                pp_fn = self._post_processes.get(spec.post_process)
                if pp_fn:
                    try: await pp_fn(data)
                    except Exception as e: logger.warning(f"[管家] post_process {spec.post_process} 失败: {e}")
            return True, data, ""
        
        # ── 失败：先诊断，再决定 ──
        diag = diagnose_error(error_msg)
        self._retry_history[stage_key]["errors"].append(error_msg)
        self._retry_history[stage_key]["diagnoses"].append(diag)
        
        reason = diag.get("reason", FailureReason.UNKNOWN)
        action = diag.get("action", "log_and_retry")
        user_msg = diag.get("user_msg", error_msg[:80])
        
        # 上报给用户
        self._notify(stage, "diagnosing", {"diagnosis": diag, "attempt": attempt+1})
        
        # ── 致命错误：立即停 ──
        if reason in FailureReason.FATAL:
            logger.error(f"[管家] {label} 💀 致命错误 [{reason}]: {diag['detail']}")
            self._notify(stage, "fatal", {"error": diag["detail"], "user_msg": user_msg})
            return False, {"_diagnosis": diag, "_fatal": True}, error_msg
        
        # ── 可自修：尝试修 ──
        if reason in (FailureReason.BAD_REQUEST, FailureReason.CONTENT_FILTERED) and attempt < MAX_RETRIES:
            # 运维智能体：尝试自动修复（参数截断/内容清洗），修好重试
            fixed_payload = self._try_auto_fix(stage, payload, error_msg)
            if fixed_payload != payload:
                logger.info(f"[管家] {label} 🔧 自动修复参数，重试 ({attempt+1}/{MAX_RETRIES})")
                self._notify(stage, "retrying", {"error": user_msg, "attempt": attempt+1, "max": MAX_RETRIES, "fix": "auto_fixed"})
                await asyncio.sleep(BASE_WAIT * (2 ** attempt))
                # 使用修复后的payload重试
                params = fixed_payload.get("params", params)
                payload = fixed_payload
                # Fall through to recursive retry
                return await self._do_call_agent_with_retry(stage, spec, attempt + 1)
        
        # ── 可重试错误 ──
        if reason in FailureReason.RETRYABLE and attempt < MAX_RETRIES:
            # 用阶段专属退避序列（超出索引则取最后一个）
            wait_s = _backoff_s[min(attempt, len(_backoff_s) - 1)] if _backoff_s else BASE_WAIT * (2 ** attempt)
            if reason == FailureReason.RATE_LIMITED:
                wait_s = max(wait_s, diag.get("wait_s", 60))
            elif reason == FailureReason.TIMEOUT:
                wait_s = wait_s * 2
            
            logger.warning(f"[管家] {label} [{reason}] {diag['detail']} → {wait_s}s后重试 ({attempt+1}/{MAX_RETRIES})")
            self._notify(stage, "retrying", {"error": user_msg, "attempt": attempt+1, "max": MAX_RETRIES, "wait_s": wait_s})
            await asyncio.sleep(wait_s)
            return await self._do_call_agent_with_retry(stage, spec, attempt + 1)
        
        # ── 未知错误/重试次数用尽 ──
        # 确定性链路：不再假成功、不再入后台队列偷偷重试。失败即停、报真错。
        if attempt >= MAX_RETRIES:
            logger.error(f"[管家] {label} 重试/诊断用尽 [{reason}] → 标记失败，不假成功: {diag['detail']}")
            self._persist_stage(stage, "failed", {"diagnosis": diag}, f"{diag['detail']}: {error_msg[:120]}")
            self._notify(stage, "failed", {"error": user_msg, "diagnosis": diag})
            return False, {"_diagnosis": diag}, error_msg
        
        # 运维智能体：BUG（代码异常）重试无用，立即停报；UNKNOWN 给1次重试机会
        if reason == FailureReason.BUG:
            logger.error(f"[运维] {label} 代码异常(BUG)，停止重试: {diag['detail']}")
            self._persist_stage(stage, "failed", {"diagnosis": diag}, f"BUG: {error_msg[:120]}")
            self._notify(stage, "failed", {"error": user_msg, "diagnosis": diag})
            return False, {"_diagnosis": diag, "_fatal": False}, error_msg
        # UNKNOWN：可能瞬时错误，给1次重试（attempt < MAX_RETRIES 时落到上面的 RETRYABLE/重试用尽分支）
        # 若 attempt 已达上限，则落到重试用尽分支标 failed
        if reason == FailureReason.UNKNOWN and attempt < MAX_RETRIES:
            wait_s = _backoff_s[min(attempt, len(_backoff_s) - 1)] if _backoff_s else BASE_WAIT
            logger.warning(f"[运维] {label} 未知错误，给1次重试机会 ({wait_s}s后): {diag['detail']}")
            self._notify(stage, "retrying", {"error": user_msg, "attempt": attempt+1, "reason": "unknown_retry"})
            await asyncio.sleep(wait_s)
            return await self._do_call_agent_with_retry(stage, spec, attempt + 1)
        # 其他不可恢复错误
        logger.error(f"[运维] {label} 无法自动恢复 [{reason}]: {diag['detail']}")
        self._notify(stage, "failed", {"error": user_msg, "diagnosis": diag})
        return False, {"_diagnosis": diag, "_fatal": False}, error_msg
    
    def _try_auto_fix(self, stage: Stage, payload: dict, error: str) -> dict:
        """运维智能体：尝试自动修复参数/内容问题，修好后重试"""
        diag = diagnose_error(error)
        reason = diag["reason"]
        # 只处理可自修的两类：参数错误 + 内容审核
        if reason not in (FailureReason.BAD_REQUEST, FailureReason.CONTENT_FILTERED):
            return payload
        
        params = payload.get("params", {})
        fixed = False
        
        # ── 内容审核拦截 → 调 content_safety 清洗 shots/prompt ──
        if reason == FailureReason.CONTENT_FILTERED:
            try:
                from agents.content_safety import sanitize_shots
                shots = params.get("shots", [])
                if shots and isinstance(shots, list):
                    before = str(shots)[:200]
                    sanitize_shots(shots)  # 原地替换敏感词
                    if before != str(shots)[:200]:
                        params["shots"] = shots
                        fixed = True
                        logger.info(f"[运维] {stage.value} 内容安全清洗完成，shots 已更新")
                # 单独的 prompt/description 字段也清洗
                for key in ("prompt", "description", "text", "scene_description"):
                    if key in params and isinstance(params[key], str):
                        from agents.content_safety import sanitize_shot
                        cleaned = sanitize_shot({"description": params[key]})
                        if cleaned.get("description", "") != params[key]:
                            params[key] = cleaned["description"]
                            fixed = True
                            logger.info(f"[运维] {stage.value} 清洗 {key}")
            except Exception as e:
                logger.warning(f"[运维] {stage.value} 内容安全清洗失败: {e}")
            # 兼容旧逻辑：内容过滤也尝试截短过长的 prompt
            for key in ("prompt", "description", "text", "scene_description"):
                if key in params and isinstance(params[key], str) and len(params[key]) > 500:
                    params[key] = params[key][:500] + "，简洁版"
                    fixed = True
                    logger.info(f"[运维] {stage.value} 截短 {key} (len={len(params[key])})")
        
        # ── 参数错误 → 截断过长的上下文 ──
        if reason == FailureReason.BAD_REQUEST:
            if "context" in error.lower() or "too long" in error.lower() or "token" in error.lower():
                for key in ("script", "full_script", "script_text", "context"):
                    if key in params and isinstance(params[key], str) and len(params[key]) > 8000:
                        params[key] = params[key][:8000]
                        fixed = True
                        logger.info(f"[运维] {stage.value} 截短 {key} → 8000字符")
        
        if fixed:
            payload["params"] = params
            logger.info(f"[运维] {stage.value} 自动修复完成 [{reason}]，准备重试")
        else:
            logger.info(f"[运维] {stage.value} 无可修复项 [{reason}]")
        return payload
    
    def _sync_top_level(self, stage: Stage, data: Dict[str, Any]):
        """把 agent 返回的关键数据提升到 Context 顶层"""
        s = stage

        if s == Stage.DIRECTOR:
            self.ctx.results["director"] = data
            # 提取结构化数据
            refined = data.get("analysis", data.get("refined_script", {}))
            if isinstance(refined, dict):
                # P0fix: 用户已编辑角色时不覆盖
                if not self.ctx.characters or len(self.ctx.characters) == 0:
                    chars = refined.get("characters", [])
                    if chars:
                        self.ctx.characters = chars
                rscenes = refined.get("scenes", [])
                if rscenes:
                    self.ctx.shots = rscenes
                # 存下 refined_script 供分镜使用
                self.ctx._computed["refined_script"] = refined
                # 提取题材类型（导演分析）
                genre_from_analysis = refined.get("genre", "")
                if genre_from_analysis:
                    self.ctx.genre = genre_from_analysis

        elif s == Stage.SCRIPT:
            self.ctx.script_text = data.get("script", data.get("outline", data.get("text", "")))
            self.ctx.title = data.get("title", self.ctx.title)
            # P0fix: 用户已编辑角色时不覆盖
            if not self.ctx.characters or len(self.ctx.characters) == 0:
                chars = data.get("characters", [])
                if chars:
                    self.ctx.characters = chars
            # 提取题材类型（脚本 agent 也可能返回 genre）
            genre_from_script = data.get("genre", "")
            if genre_from_script:
                self.ctx.genre = genre_from_script

        elif s == Stage.CHARACTER:
            # P0fix: 用户已编辑角色时不覆盖
            if not self.ctx.characters or len(self.ctx.characters) == 0:
                chars = data.get("characters", [])
                if chars:
                    self.ctx.characters = chars

        elif s == Stage.STORYBOARD:
            shots = data.get("shots", [])
            if shots:
                self.ctx.shots = shots or []

        elif s == Stage.CINEMATOGRAPHER:
            shots = data.get("shots", [])
            if shots:
                # 合并摄影参数到ctx.shots
                if self.ctx.shots:
                    for i, s in enumerate(shots):
                        if i < len(self.ctx.shots):
                            self.ctx.shots[i]["camera_movement"] = s.get("camera_movement", self.ctx.shots[i].get("camera_movement",""))
                            self.ctx.shots[i]["camera_angle"] = s.get("camera_angle", self.ctx.shots[i].get("camera_angle",""))
                            self.ctx.shots[i]["shot_type"] = s.get("shot_type", self.ctx.shots[i].get("shot_type",""))
                            self.ctx.shots[i]["lighting"] = s.get("lighting", self.ctx.shots[i].get("lighting",""))
                            self.ctx.shots[i]["transition"] = s.get("transition", self.ctx.shots[i].get("transition",""))
                            self.ctx.shots[i]["flow_notes"] = s.get("flow_notes", self.ctx.shots[i].get("flow_notes",""))
                            self.ctx.shots[i]["rationale"] = s.get("rationale","")
                else:
                    self.ctx.shots = shots or []
            self.ctx._computed["overall_style"] = data.get("overall_style", "")

        elif s == Stage.WARDROBE:
            shots = data.get("shots", [])
            if shots:
                if self.ctx.shots:
                    for i, s in enumerate(shots):
                        if i < len(self.ctx.shots):
                            self.ctx.shots[i]["outfit"] = s.get("outfit", self.ctx.shots[i].get("outfit",{}))
                            self.ctx.shots[i]["props"] = s.get("props", self.ctx.shots[i].get("props",{}))
                            self.ctx.shots[i]["makeup"] = s.get("makeup", self.ctx.shots[i].get("makeup",{}))
                            self.ctx.shots[i]["char_ages"] = s.get("char_ages", self.ctx.shots[i].get("char_ages",{}))
                            self.ctx.shots[i]["wardrobe_notes"] = s.get("wardrobe_notes","")
                else:
                    self.ctx.shots = shots or []
            self.ctx._computed["continuity_notes"] = data.get("continuity_notes", "")
            self.ctx._computed["special_scenes"] = data.get("special_scenes", [])

        elif s == Stage.SFX:
            shots = data.get("shots", [])
            if shots:
                if self.ctx.shots:
                    for i, s in enumerate(shots):
                        if i < len(self.ctx.shots):
                            self.ctx.shots[i]["needs_sfx"] = s.get("needs_sfx", False)
                            self.ctx.shots[i]["action_effects"] = s.get("action_effects", [])
                            self.ctx.shots[i]["atmosphere_effects"] = s.get("atmosphere_effects", [])
                            self.ctx.shots[i]["transition_effect"] = s.get("transition_effect", "")
                            self.ctx.shots[i]["sfx_intensity"] = s.get("sfx_intensity", 0)
                            self.ctx.shots[i]["color_grade"] = s.get("color_grade", "")
                            self.ctx.shots[i]["sfx_reason"] = s.get("sfx_reason", "")
                else:
                    self.ctx.shots = shots or []
            self.ctx._computed["overall_color_palette"] = data.get("overall_color_palette", "")
            self.ctx._computed["vfx_notes"] = data.get("vfx_notes", "")

        elif s == Stage.SCENE:
            images = data.get("images", data.get("scene_images", []))
            if images:
                self.ctx.scene_images = images
            # P0-5: 持久化 scene_masters 到 _computed，跨 resume 复用
            scene_masters = data.get("scene_masters", {})
            if scene_masters:
                self.ctx._computed["scene_masters"] = scene_masters

        elif s == Stage.TTS:
            audio = data.get("audio_files", data.get("audio", []))
            if audio:
                self.ctx.tts_audio = audio

        elif s == Stage.SUBTITLE:
            subtitles = data.get("subtitles", [])
            if subtitles:
                self.ctx.subtitles = subtitles

        elif s == Stage.BGM:
            # bgm agent 返回: {bgm_style, mood, bgm_list:[{name,url,...}], audio_file, ...}
            bgm = data.get("audio_file", data.get("bgm_url", data.get("url", "")))
            if bgm:
                self.ctx.bgm_url = bgm
            bgm_files = data.get("bgm_files", data.get("files", []))
            if not bgm_files and data.get("bgm_list"):
                # 从 bgm_list 提取 url
                for item in data["bgm_list"]:
                    if isinstance(item, dict) and item.get("url"):
                        bgm_files.append(item["url"])
            if bgm_files:
                self.ctx.bgm_files = bgm_files

        elif s == Stage.VIDEO:
            clips = data.get("clips", data.get("videos", []))
            if clips:
                self.ctx.video_clips = clips

        elif s == Stage.COMPOSITE:
            video = data.get("video_url", data.get("url", data.get("output", "")))
            if video:
                # COMPOSITE 返回的 output 是本地路径（/www/wwwroot/storage/.../final_xxx.mp4），
                # 前端无法直接下载/观看。转成公网 URL。
                # 早期代码直接存本地路径 → /status 和 run_dag 回传的 final_video 不可访问。
                if video.startswith("/www/wwwroot/") or video.startswith("/www/wwwroot"):
                    try:
                        from agents.agent_video import _to_public_url
                        video = _to_public_url(video)
                    except Exception as e:
                        logger.warning(f"[管家] final_video 路径转URL失败，保留原值: {e}")
                self.ctx.final_video = video

    # ═══════════════════════════════════════════════════════════════════
    # Pre-Hooks: 调 agent 前准备数据
    # ═══════════════════════════════════════════════════════════════════

    async def _hook_extract_director_tasks(self):
        """从导演结果提取各阶段任务指令 → ctx._computed"""
        director_data = self.ctx.results.get("director", {})
        tasks = director_data.get("tasks", {})
        self.ctx._computed["director_task"] = tasks.get(
            "character_design", tasks.get("character", "")
        )
        self.ctx._computed["director_analysis"] = director_data

    async def _hook_build_storyboard_context(self):
        """为分镜 agent 拼装增强上下文: refined_characters, refined_scenes, director_beats"""
        director_data = self.ctx.results.get("director", {})
        refined = director_data.get("analysis", director_data.get("refined_script", {}))
        if isinstance(refined, dict):
            self.ctx._computed["scenes"] = refined.get("scenes", [])
            self.ctx._computed["refined_characters"] = refined.get("characters", [])

        # 导演节拍分析
        beats = director_data.get("beats", director_data.get("beat_analysis", []))
        if beats:
            blines = []
            for b in beats[:15]:
                bn = b.get("beat_num", b.get("number", b.get("id", "")))
                bd = b.get("description", str(b)[:120])
                bi = b.get("importance", b.get("weight", "medium"))
                blines.append(f"节拍{bn}: {bd} [重要度:{bi}]")
            self.ctx._computed["director_beats"] = (
                "导演节拍分析（用于为每个分镜分配importance等级）：\n" + "\n".join(blines)
            )

    async def _hook_build_character_voices(self):
        """从 characters 构建 character_voices 字典 → ctx.character_voices"""
        voices = {}
        for c in self.ctx.characters:
            name = c.get("name", "")
            if name:
                voices[name] = {
                    "voice": c.get("voice", c.get("tts_voice", "longyan")),
                    "gender": c.get("gender", "女"),
                    "age": c.get("age", "青年"),
                }
        self.ctx.character_voices = voices

    async def _hook_ensure_shot_media(self):
        """视频生成前: 给 shots 注入 scene_image + character_image + tts_audio
        确定性链路：改调统一注入函数 inject_shot_media，与 step 端点/single-video 走同一套规则。
        只信 DB（持久态），不依赖内存 ctx 的易失字段（避免续跑时 portraits/scene_images 丢失）。"""
        shots = self.ctx.shots
        if not shots:
            return
        project_id = self.pipeline_id.split("_")[-1] if self.pipeline_id else ""
        if not project_id or project_id == "default":
            # pipeline_id 格式 pipe_{ts}_{project_id}，取最后一段
            logger.warning(f"[管家] ensure_shot_media 无法解析 project_id from pipeline_id={self.pipeline_id}, 跳过统一注入")
            return
        try:
            from agents.shot_media import inject_shot_media
            _inj = inject_shot_media(shots, project_id, include_tts=True)
            logger.info(f"[管家] ensure_shot_media 统一注入完成: {_inj}")
        except Exception as e:
            logger.error(f"[管家] ensure_shot_media 统一注入失败: {e}")

        # 首尾帧连接已禁用：它会覆盖角色锁脸图，导致第2镜以后角色脸全变。
        # 每个镜头始终用自己的角色锁脸图，保证全剧锁脸一致。

    async def _hook_first_frame_connection(self):
        """从刚生成的视频截取末帧，缓存到 _computed["last_video_frame"]"""
        videos = self.ctx.results.get("video", {}).get("videos", [])
        if not videos:
            logger.info("[管家] 首尾帧连接跳过: 无视频结果")
            return
        # 取最后一镜的视频URL
        last_shot = videos[-1] if isinstance(videos, list) else videos
        result = last_shot.get("result", {})
        video_url = result.get("video_url", "") if isinstance(result, dict) else ""
        if not video_url:
            logger.info("[管家] 首尾帧连接跳过: 无视频URL")
            return
        from agents.agent_video import _extract_last_frame, _to_public_url
        frame_path = f"/tmp/frame_{int(time.time())}.jpg"
        url_path = _extract_last_frame(video_url, frame_path)
        if url_path:
            self.ctx._computed["last_video_frame"] = _to_public_url(frame_path)
            logger.info(f"[管家] 首尾帧连接: {url_path[:60]}")

    async def _hook_collect_composite_inputs(self):
        """收集合成所需: clips 列表 (含 video, audio, bgm, subtitle)"""
        shots = self.ctx.shots
        video_data = self.ctx.results.get("video", {})
        videos = video_data.get("videos", video_data.get("clips", self.ctx.video_clips))
        tts_audio = self.ctx.tts_audio
        bgm_url = self.ctx.bgm_url or ""
        bgm_files = self.ctx.bgm_files or []

        # 按 shot_index 索引 video 和 audio
        video_by_idx: Dict[int, str] = {}
        for v in videos:
            if isinstance(v, dict):
                idx = v.get("shot_index", v.get("index", -1))
                result = v.get("result", {})
                url = ""
                if isinstance(result, dict):
                    url = result.get("video_url", result.get("url", ""))
                if not url:
                    url = v.get("video_url", v.get("url", ""))
                if url and idx >= 0:
                    video_by_idx[idx] = url

        audio_by_idx: Dict[int, str] = {}
        for a in tts_audio:
            if isinstance(a, dict):
                idx = a.get("shot_index", -1)
                url = a.get("audio_url", a.get("url", ""))
                if url and idx >= 0:
                    audio_by_idx[idx] = url

        clips = []
        for i, shot in enumerate(shots):
            if not isinstance(shot, dict):
                continue
            clip = {
                "shot_index": i,
                "desc": shot.get("description", shot.get("scene", "")),
                "subtitle": shot.get("dialogue", shot.get("text", "")),
                "duration_sec": float(shot.get("duration_sec", shot.get("duration", 5))),
            }
            if i in video_by_idx:
                clip["video"] = video_by_idx[i]
            if i in audio_by_idx:
                clip["audio"] = audio_by_idx[i]
            # 第一个 clip 带 bgm
            if i == 0 and bgm_url:
                clip["bgm"] = bgm_url
            elif i == 0 and bgm_files:
                clip["bgm"] = bgm_files[0] if isinstance(bgm_files[0], str) else bgm_files[0].get("url", "")
            clips.append(clip)

        self.ctx._computed["clips"] = clips
        self.ctx.video_clips = clips

    # ═══════════════════════════════════════════════════════════════════
    # Post-Processes: agent 返回后额外处理
    # ═══════════════════════════════════════════════════════════════════

    async def _post_gen_portraits(self, data: Dict[str, Any]):
        """
        角色肖像生成 — 用 asyncio 并行调 agent 生图
        先查素材库，命中则跳过；未命中调 character/generate_figure 或 scene/generate_image
        """
        characters = self.ctx.characters
        if not characters:
            logger.info("[管家] 无角色，跳过肖像生成")
            return

        # 素材库命中检查
        char_images: Dict[str, str] = {}
        if self._media_registry:
            # 禁用素材库角色图缓存 — 新剧必须重新生成，保持一致风格
            logger.info("[管家] 强制重新生成角色肖像 (跳过素材库)")

        # 构建并行任务
        async def gen_one(ch: Dict, idx: int) -> Tuple[str, str]:
            name = ch.get("name", f"角色{idx+1}")
            if name in char_images:
                return name, char_images[name]

            # 一名一张脸：角色已有 portrait_url（用户手动建模/上传的图）则直接复用，
            # 不重新生成、不覆盖。否则全量跑 /start 会把用户已建模的脸换成另一张。
            existing_portrait = str(ch.get("portrait_url", "")).strip()
            if existing_portrait and (existing_portrait.startswith("http") or existing_portrait.startswith("/storage")):
                ep = existing_portrait
                if ep.startswith("/storage"):
                    ep = local_path_to_url(ep)
                logger.info(f"[管家] 角色{name} 已有肖像，复用不重新生成: {ep[:60]}")
                return name, ep

            user_photo = str(ch.get("photo", ch.get("avatar", ch.get("image_url", ch.get("ref_image", ""))))).strip()
            # 同时从 characters 顶层找 ref_image
            if not user_photo and ch.get("ref_image"):
                user_photo = str(ch.get("ref_image")).strip()
            has_photo = user_photo and (user_photo.startswith("http") or user_photo.startswith("/storage"))
            logger.info(f"[管家] 角色{name} 照片检测: avatar={ch.get('avatar','')[:40]}... photo={str(ch.get('photo',''))[:40]}... ref_image={str(ch.get('ref_image',''))[:40]}... has_photo={has_photo}")
            # Convert /storage/ to full HTTPS for Seedream accessibility
            if user_photo and user_photo.startswith("/storage"):
                user_photo = local_path_to_url(user_photo)
                has_photo = True
            # data: URL → 解码保存到存储
            if user_photo and user_photo.startswith("data:"):
                try:
                    import base64, hashlib
                    _, b64 = user_photo.split(",", 1)
                    raw = base64.b64decode(b64)
                    h = hashlib.md5(raw).hexdigest()[:12]
                    fdir = "/www/wwwroot/storage/figures/"
                    os.makedirs(fdir, exist_ok=True)
                    fpath = f"{fdir}uploaded_{h}.jpg"
                    with open(fpath, "wb") as f:
                        f.write(raw)
                    user_photo = local_path_to_url(fpath)
                    has_photo = True
                    logger.info(f"[管家] data URL已保存: {user_photo}")
                except Exception as e:
                    logger.warning(f"[管家] data URL保存失败: {e}")
            gender = ch.get("gender", "女")
            age = ch.get("age", "青年")

            # 过滤面部特征详情，只保留发型衣着气质等可生成内容
            def _strip_face(s):
                bans = ['苍白','煞白','惨白','深陷','凹陷','阴鸷','蛇蝎','诡','狡黠','狰狞',
                        '魔焰','黑气','邪气','鬼气','戾气','虎狼','可怕','邪恶','凶恶','猥琐']
                for w in bans:
                    s = s.replace(w, '')
                return s.strip('，, ')
            desc_parts = []
            for k in ["personality", "appearance", "role_type", "trait", "description"]:
                v = ch.get(k, "")
                if v and len(str(v).strip()) > 1:
                    v = _strip_face(str(v).strip())
                    if v: desc_parts.append(v)
            person_desc = "，".join(desc_parts[:3]) if desc_parts else f"{gender}性{age}"

            prompt = (
                f"一位中国真人，{person_desc}，{gender}性，{age}，面部特写肖像，"
                f"肩膀以上构图，脸部占据画面主体，正脸直视镜头，电影级光影，"
                f"皮肤质感真实细腻，8K超高清，真人照片，真人电影摄影质感 | "
                f"photorealistic Chinese person, close-up face portrait, head and shoulders, "
                f"face fills composition, looking at camera, studio lighting, 8K, "
                f"real human photo, live action portrait photography"
            )

            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=PORTRAIT_HTTP_TIMEOUT) as client:
                        if has_photo:
                            r = await client.post(AGENT_BASE_URL, json={
                                "agent_id": "character", "action": "generate_figure",
                                "params": {"character": ch, "title": getattr(self.ctx, "title", ""), "genre": self.ctx.genre,
                                           "reference_image": user_photo, "prompt_hint": prompt}
                            })
                        else:
                            # 无照片: 直接调 character/generate_figure (人脸优化链，不走wanxiang)
                            r = await client.post(AGENT_BASE_URL, json={
                                "agent_id": "character", "action": "generate_figure",
                                "params": {"character": ch, "title": getattr(self.ctx, "title", ""), "genre": self.ctx.genre,
                                           "prompt_hint": prompt, "force_t2i": True}
                            })
                        # 两个分支统一解析响应。
                        # 注意：character agent 的 generate_figure 返回字段名是 figure_url
                        # （见 agents/agent_character.py 的 AgentResult.data），
                        # 早期代码只查 image_url/url/portrait_url → 永远拿不到，导致
                        # has_photo 分支 url 未定义抛 UnboundLocalError、t2i 分支返回空。
                        img_data = (r.json() or {}).get("data", {}) or {}
                        url = (img_data.get("figure_url")
                               or img_data.get("image_url")
                               or img_data.get("url")
                               or img_data.get("portrait_url")
                               or "")
                        if url:
                            logger.info(f"[管家] 角色{name}肖像 OK (尝试{attempt+1})")
                            # 立即下载到本地存储（OSS 链接有时效，seedream i2i 需要本地可访问）
                            if url and not url.startswith("/storage/") and BASE_URL not in url and "ai.mzsh.top" not in url:
                                try:
                                    local_r = await client.get(url, timeout=30)
                                    if local_r.status_code == 200:
                                        import hashlib
                                        raw = local_r.content
                                        h = hashlib.md5(raw).hexdigest()[:16]
                                        ext = "png" if b"PNG" in raw[:100] else "jpg"
                                        fdir = "/www/wwwroot/storage/figures/"
                                        os.makedirs(fdir, exist_ok=True)
                                        fpath = f"{fdir}char_{name}_{h}.{ext}"
                                        if not os.path.exists(fpath):
                                            with open(fpath, "wb") as f:
                                                f.write(raw)
                                        local_url = local_path_to_url(fpath)
                                        logger.info(f"[管家] 角色{name}已本地化: {local_url[:60]}")
                                        url = local_url
                                except Exception as dl_e:
                                    logger.warning(f"[管家] 角色{name}本地化失败: {dl_e}")
                            return name, url
                except Exception as e:
                    logger.warning(f"[管家] 角色{name}第{attempt+1}次失败: {e}")
                    if attempt < 2:
                        await asyncio.sleep(1)
            return name, ""

        # 并行执行 (最多6个角色)
        tasks = [gen_one(ch, i) for i, ch in enumerate(characters[:6])]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, tuple):
                name, url = result
                if url:
                    char_images[name] = url
            elif isinstance(result, Exception):
                logger.warning(f"[管家] 肖像生成异常: {result}")

        if char_images:
            self.ctx.character_portraits = char_images
            # 下载所有角色图到本地存储（确保 i2i 下游能访问）。
            # 自包含实现：不依赖 agents/agent_scene.download_to_local，避免其顶部
            # `from services.media_registry import ...` 触发的模块级 DB 连接副作用与
            # 潜在循环导入。每个角色的本地化独立 try/except，单点失败不拖累其它
            # 角色的 portrait_url 回写（早期代码这里调用未导入的 download_to_local
            # 会抛 NameError，导致整段回写被外层 except 吞掉）。
            async with httpx.AsyncClient(timeout=60, verify=False) as dl_client:
                for name in list(char_images.keys()):
                    url = char_images[name]
                    if not (url and not url.startswith('/storage/') and BASE_URL not in url and 'ai.mzsh.top' not in url):
                        continue
                    try:
                        local_r = await dl_client.get(url, timeout=30)
                        if local_r.status_code != 200 or len(local_r.content) < 1000:
                            logger.warning(f"[管家] 角色{name} 二次本地化跳过(status={local_r.status_code}, {len(local_r.content)}B)")
                            continue
                        import hashlib as _hl2
                        h = _hl2.md5(local_r.content).hexdigest()[:16]
                        ct = local_r.headers.get("content-type", "")
                        ext = "png" if "png" in ct else ("webp" if "webp" in ct else "jpg")
                        fdir = "/www/wwwroot/storage/figures/"
                        os.makedirs(fdir, exist_ok=True)
                        fname = f"char_{name}_{h}.{ext}"
                        fpath = f"{fdir}{fname}"
                        if not os.path.exists(fpath):
                            with open(fpath, "wb") as f:
                                f.write(local_r.content)
                        local_url = local_path_to_url(fpath)
                        char_images[name] = local_url
                        logger.info(f"[管家] 角色{name} 図已下載到本地: {local_url[:60]}")
                    except Exception as dl_e:
                        logger.warning(f"[管家] 角色{name} 二次本地化失败(保留原URL): {dl_e}")
            # 回写到 characters 列表
            for ch in self.ctx.characters:
                name = ch.get("name", "")
                if name in char_images:
                    ch["portrait_url"] = char_images[name]
                    ch["photo"] = char_images[name]
                    ch["avatar"] = char_images[name]

            # 回写到 character_result
            if "character" in self.ctx.results:
                self.ctx.results["character"]["char_images"] = char_images

            logger.info(f"[管家] 角色肖像完成: {len(char_images)}/{len(characters[:6])}")

    async def _post_inject_images_to_shots(self, data: Dict[str, Any]):
        """场景生成后: 把 image_map 注入 shots"""
        image_map = data.get("image_map", {})
        if not image_map or not self.ctx.shots:
            return

        injected = 0
        for i, shot in enumerate(self.ctx.shots):
            if not isinstance(shot, dict):
                continue
            for key in [str(i), i, str(i + 1)]:
                url = image_map.get(key, "")
                if url:
                    shot["image_url"] = url
                    shot["scene_image"] = url
                    injected += 1
                    break
        if injected:
            logger.info(f"[管家] 场景图注入: {injected}/{len(self.ctx.shots)} shots")

    async def _post_inject_audio_to_shots(self, data: Dict[str, Any]):
        """TTS 完成后: 把 audio_files 按 shot_index 注入 shots"""
        audio_files = data.get("audio_files", data.get("audio", []))
        if not audio_files or not self.ctx.shots:
            return

        injected = 0
        for af in audio_files:
            if not isinstance(af, dict):
                continue
            idx = af.get("shot_index", -1)
            url = af.get("audio_url", af.get("url", ""))
            if idx >= 0 and url and idx < len(self.ctx.shots):
                self.ctx.shots[idx]["tts_audio"] = url
                injected += 1
        if injected:
            logger.info(f"[管家] 配音注入: {injected}/{len(audio_files)} files")

    # ═══════════════════════════════════════════════════════════════════
    # 后处理：统一调色
    # ═══════════════════════════════════════════════════════════════════

    async def _post_color_grading(self, data: Dict[str, Any]):
        """全片统一调色 + 颗粒感"""
        genre = self.ctx.genre or "都市"
        final_video = self.ctx.final_video
        if not final_video:
            logger.info("[管家] 调色跳过: 无 final_video")
            return
        color_params = {
            "古装": {"grain": 0.03},
            "都市": {"grain": 0.01},
            "科幻": {"grain": 0.015},
            "古装战": {"grain": 0.03},
        }.get(genre, {"grain": 0.015})
        output = f"/tmp/graded_{int(time.time())}.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", final_video,
            "-vf", f"grain={color_params['grain']}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            output
        ]
        try:
            subprocess.run(cmd, capture_output=True, timeout=300)
            if os.path.exists(output):
                from agents.agent_video import _to_public_url
                graded_url = _to_public_url(output)
                self.ctx.final_video = graded_url
                logger.info(f"[管家] 调色完成: {graded_url[:60]}")
        except Exception as e:
            logger.warning(f"[管家] 调色失败: {e}")

    # ═══════════════════════════════════════════════════════════════════
    # 主调度
    # ═══════════════════════════════════════════════════════════════════

    async def run(self, start_from: Optional[Stage] = None) -> Dict[str, Any]:
        """
        顺序执行所有阶段 (兼容旧接口)。
        start_from: 断点续跑起点, None 表示从头开始
        """
        start_idx = 0
        if start_from:
            try:
                start_idx = STAGE_ORDER.index(start_from)
            except ValueError:
                pass

        results_summary: Dict[str, Dict] = {}

        for i, stage in enumerate(STAGE_ORDER[start_idx:], start=start_idx):
            # 检查依赖
            deps = STAGE_DEPENDS.get(stage, [])
            missing = [d for d in deps if d.value not in self.ctx.completed_stages]
            if missing:
                dep_names = [STAGE_LABELS.get(d, d.value) for d in missing]
                logger.warning(f"[管家] {STAGE_LABELS[stage]} 跳过: 依赖未完成 {dep_names}")
                results_summary[stage.value] = {"success": False, "error": f"依赖未完成: {dep_names}"}
                self._notify(stage, "skipped", {"reason": f"依赖未完成: {dep_names}"})
                continue

            self.ctx.current_stage = stage.value
            success, data, error = await self._call_agent(stage)

            results_summary[stage.value] = {
                "success": success,
                "data": data if success else {},
                "error": error if not success else "",
            }

            # 持久化阶段进度 (v3)
            if success:
                self._persist_stage(stage, "completed", data, "")
            else:
                self._persist_stage(stage, "failed", data, error)

            # 持久化快照
            self._persist_snapshot()

            if not success:
                logger.warning(f"[管家] 停在 {STAGE_LABELS[stage]}，后续阶段跳过")
                break

        return {
            "pipeline_id": self.pipeline_id,
            "stages": results_summary,
            "completed": list(self.ctx.completed_stages),
            "final_video": self.ctx.final_video,
            "success": (
                self.ctx.completed_stages[-1] == Stage.COMPOSITE.value
                if self.ctx.completed_stages else False
            ),
        }

    async def run_dag(self, start_from: Optional[Stage] = None) -> Dict[str, Any]:
        """
        DAG 并行执行: 按依赖关系分批并行执行无依赖阶段。
        v3: 失败不阻断整个管线，标记失败后继续执行不依赖该阶段的后续阶段
        """
        completed: Set[Stage] = set()
        failed: Set[Stage] = set()

        # 标记断点之前的阶段为已完成
        if start_from:
            skip = True
            for s in STAGE_ORDER:
                if s == start_from:
                    skip = False
                if skip:
                    completed.add(s)
                    if s.value not in self.ctx.completed_stages:
                        self.ctx.completed_stages.append(s.value)

        stage_results: Dict[str, Dict] = {}

        while len(completed) + len(failed) < len(STAGE_ORDER):
            # 找所有依赖已满足的阶段（排除已失败和已完成）
            ready = [
                s for s in STAGE_ORDER
                if s not in completed and s not in failed
                and all(d in completed for d in STAGE_DEPENDS.get(s, []))
            ]
            if not ready:
                remaining = set(STAGE_ORDER) - completed - failed
                logger.warning(f"[管家] DAG 死锁或无可用阶段! completed={[s.value for s in completed]}, failed={[s.value for s in failed]}, remaining={[s.value for s in remaining]}")
                # 标记剩余为 skipped
                for s in remaining:
                    stage_results[s.value] = {"success": False, "error": "依赖阶段失败，无法执行"}
                    self._persist_stage(s, "failed", None, "依赖阶段失败，无法执行")
                    failed.add(s)
                break

            logger.info(f"[管家] DAG 波次: {[STAGE_LABELS[s] for s in ready]}")

            # 并行执行当前波次
            async def _exec(stage: Stage) -> Tuple[Stage, Dict]:
                self.ctx.current_stage = stage.value
                ok, data, err = await self._call_agent(stage)
                return stage, {
                    "success": ok,
                    "data": data if ok else {},
                    "error": err if not ok else "",
                }

            tasks = [_exec(s) for s in ready]
            batch_results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in batch_results:
                if isinstance(result, Exception):
                    logger.error(f"[管家] DAG 波次异常: {result}")
                    continue
                stage, info = result
                stage_results[stage.value] = info
                if info["success"]:
                    completed.add(stage)
                    # 持久化: 成功
                    self._persist_stage(stage, "completed", info.get("data", {}), "")
                else:
                    logger.warning(f"[管家] DAG {STAGE_LABELS[stage]} 失败: {info.get('error','')[:80]}")
                    failed.add(stage)
                    # 持久化: 失败（v3: 不停止管线，标记失败继续）
                    self._persist_stage(stage, "failed", info.get("data", {}), info.get("error", ""))

            # 持久化快照
            self._persist_snapshot()

            # v3: 不再因部分失败停止整个管线
            # 失败的阶段不会进入 completed，其依赖阶段在后续波次中会被跳过
            if failed:
                logger.info(f"[管家] 本波次 {len(failed)} 个阶段失败，继续执行不依赖它们的后续阶段")

        # 判断最终成功：所有阶段都完成且 COMPOSITE 完成
        all_completed = len(completed) == len(STAGE_ORDER)
        composite_done = Stage.COMPOSITE in completed

        return {
            "pipeline_id": self.pipeline_id,
            "stages": stage_results,
            "completed": [s.value for s in completed],
            "failed": [s.value for s in failed],
            "final_video": self.ctx.final_video,
            "success": all_completed and composite_done,
        }

    async def run_single(self, stage: Stage) -> Tuple[bool, Dict[str, Any], str]:
        """单独跑一个阶段 (用于 script_only 等模式)"""
        self.ctx.current_stage = stage.value
        return await self._call_agent(stage)

    def run_sync(self, start_from: Optional[Stage] = None,
                 use_dag: bool = True) -> Dict[str, Any]:
        """同步包装器: 在线程中运行 (供 ThreadPoolExecutor 使用)"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 已有事件循环，创建新的
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        lambda: asyncio.run(self.run_dag(start_from) if use_dag else self.run(start_from))
                    )
                    return future.result(timeout=7200)
            else:
                return loop.run_until_complete(
                    self.run_dag(start_from) if use_dag else self.run(start_from)
                )
        except RuntimeError:
            return asyncio.run(self.run_dag(start_from) if use_dag else self.run(start_from))


# ═══════════════════════════════════════════════════════════════════════════
# 工厂函数
# ═══════════════════════════════════════════════════════════════════════════

def create_context(
    synopsis: str = "",
    script_text: str = "",
    genre: str = "都市",
    title: str = "",
    user_script: str = "",
    polish_only: bool = False,
    project_id: str = "",
    characters: List[Dict] = None,
    user_id: int = 0,
) -> PipelineContext:
    """从用户输入创建统一上下文"""
    return PipelineContext(
        synopsis=synopsis or script_text,
        script_text=script_text or synopsis,
        genre=genre or "都市",
        title=title,
        user_script=user_script,
        polish_only=polish_only,
        project_id=project_id,
        characters=characters or [],
        user_id=user_id,
    )
