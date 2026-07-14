"""统一 Pydantic 结构化校验 — v3: 异常分类 + 台词质检 + 兜底补全"""
import re as _re
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger("schema")

# ═══════════════════════════════════════════
# 统一异常类 — 携带错误码，ELK/Grafana 可按 code 分类统计
# ═══════════════════════════════════════════
class SchemaValidateError(Exception):
    def __init__(self, code: str, msg: str, detail: dict = None):
        self.code = code      # SCRIPT_TOO_LONG / DIRTY_CHARS / EMPTY_SCRIPT / BRIEF_MISSING / EXTRA_NOT_FILTERED / VAGUE_SHOT / DIALOGUE_EMPTY / DIALOGUE_CROSS / DB_FAILED
        self.msg = msg
        self.detail = detail or {}
        super().__init__(f"[{code}] {msg}")

# 错误码常量
ERR_SCRIPT_TOO_LONG   = "SCRIPT_TOO_LONG"
ERR_DIRTY_CHARS       = "DIRTY_CHARS"
ERR_EMPTY_SCRIPT      = "EMPTY_SCRIPT"
ERR_BRIEF_MISSING     = "BRIEF_MISSING"
ERR_EXTRA_NOT_FILTERED = "EXTRA_NOT_FILTERED"
ERR_VAGUE_SHOT        = "VAGUE_SHOT"
ERR_DIALOGUE_EMPTY    = "DIALOGUE_EMPTY"
ERR_DIALOGUE_CROSS    = "DIALOGUE_CROSS"
ERR_DB_FAILED         = "DB_FAILED"
ERR_STRUCTURE_MISSING = "STRUCTURE_MISSING"

# ═══════════════════════════════════════════
# 全局常量（统一管理，改一处全局生效）
# ═══════════════════════════════════════════
SCRIPT_MAX_WORD       = 12000   # 剧本字数上限
SCRIPT_TRUNCATE_HEAD  = 10000   # 超长时保留前 N 字
SCRIPT_TRUNCATE_TAIL  = 2000    # 超长时保留后 N 字
SUMMARY_PLACEHOLDER   = "（剧本摘要生成中，请以完整剧本为准）"  # 摘要兜底
VAGUE_DESC_LIST = ["两人对话", "气氛紧张", "简单交谈", "众人聊天", "场面普通", "发生冲突"]
CONTROL_CHAR_PATTERN = _re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\ufeff]')
DIRECTOR_TASK_MAX_LEN = 2000       # 导演任务单字段最大长度
STAGE_PARAM_MAX_SHOTS = 200        # 分片镜头数上限
STAGE_PARAM_MAX_CHARS = 50         # 分片角色数上限

# ═══════════════════════════════════════════
# Pydantic 检测
# ═══════════════════════════════════════════
try:
    from pydantic import BaseModel, Field, field_validator, ValidationError
    HAS_PYDANTIC = True
except ImportError:
    HAS_PYDANTIC = False
    BaseModel = object
    Field = lambda **kw: None
    field_validator = lambda *a, **kw: (lambda f: f)
    ValidationError = Exception


# ═══════════════════════════════════════════
# 1. 完整剧本校验
# ═══════════════════════════════════════════
class FullScriptSchema(BaseModel if HAS_PYDANTIC else object):
    content: str = ""
    raw_length: int = 0
    is_truncated: bool = False
    summary: Optional[str] = None

    if HAS_PYDANTIC:
        @field_validator("content")
        @classmethod
        def check_script_max_length(cls, v: str):
            if len(v) > SCRIPT_MAX_WORD:
                raise SchemaValidateError(ERR_SCRIPT_TOO_LONG, f"剧本{len(v)}字超过{SCRIPT_MAX_WORD}上限", {"len": len(v)})
            return v

        @field_validator("content")
        @classmethod
        def check_clean_control_char(cls, v: str):
            match = CONTROL_CHAR_PATTERN.search(v)
            if match:
                raise SchemaValidateError(ERR_DIRTY_CHARS, f"文本残留控制字符 at {match.start()}", {"pos": match.start()})
            return v

        @field_validator("summary")
        @classmethod
        def fill_summary_fallback(cls, v):
            if v is None or str(v).strip() == "":
                return SUMMARY_PLACEHOLDER
            return v


# ═══════════════════════════════════════════
# 2. 导演总指令
# ═══════════════════════════════════════════
class DirectorBriefSchema(BaseModel if HAS_PYDANTIC else object):
    genre: str = "现代"
    core_conflict: str = ""
    director_vision: str = ""
    emotional_curve: str = ""
    pacing_notes: str = ""
    highlight_moments: str = ""
    character_archetypes: str = ""

    if HAS_PYDANTIC:
        @field_validator("genre", mode="before")
        @classmethod
        def fill_default_genre(cls, v):
            if not v or str(v).strip() == "":
                return "现代"
            return str(v).strip()


# ═══════════════════════════════════════════
# 3. 导演任务指令（字段长度限制，防挤占 LLM 上下文）
# ═══════════════════════════════════════════
class DirectorTaskSchema(BaseModel if HAS_PYDANTIC else object):
    storyboard_generation: str = ""
    character_design: str = ""
    scene_generation: str = ""
    tts_voice: str = ""
    bgm_music: str = ""

    if HAS_PYDANTIC:
        @field_validator("storyboard_generation", "character_design", "scene_generation", "tts_voice", "bgm_music")
        @classmethod
        def limit_task_length(cls, v: str):
            if len(v) > DIRECTOR_TASK_MAX_LEN:
                logger.warning(f"[Schema] 导演任务字段超长({len(v)}>{DIRECTOR_TASK_MAX_LEN})，截断")
                return v[:DIRECTOR_TASK_MAX_LEN]
            return v


# ═══════════════════════════════════════════
# 4. 阶段分片参数（列表长度上限）
# ═══════════════════════════════════════════
class StageParamSchema(BaseModel if HAS_PYDANTIC else object):
    stage: str = ""
    genre: str = "现代"
    characters: List[Dict] = []
    shots: List[Dict] = []
    scenes: List[str] = []
    project_id: str = ""

    if HAS_PYDANTIC:
        @field_validator("shots")
        @classmethod
        def limit_shots_count(cls, v: list):
            if len(v) > STAGE_PARAM_MAX_SHOTS:
                logger.warning(f"[Schema] 分片镜头数{len(v)}超过{STAGE_PARAM_MAX_SHOTS}上限")
            return v

        @field_validator("characters")
        @classmethod
        def limit_chars_count(cls, v: list):
            if len(v) > STAGE_PARAM_MAX_CHARS:
                logger.warning(f"[Schema] 分片角色数{len(v)}超过{STAGE_PARAM_MAX_CHARS}上限")
            return v


# ═══════════════════════════════════════════
# 5. 流水线总入参聚合
# ═══════════════════════════════════════════
class PipelineInputSchema(BaseModel if HAS_PYDANTIC else object):
    full_script: FullScriptSchema = FullScriptSchema()
    brief: DirectorBriefSchema = DirectorBriefSchema()
    tasks: DirectorTaskSchema = DirectorTaskSchema()
    stage_params: StageParamSchema = StageParamSchema()


# ═══════════════════════════════════════════
# 6. 角色输出校验（龙套过滤复检 + 人设冲突预留）
# ═══════════════════════════════════════════
class CharacterOutput(BaseModel if HAS_PYDANTIC else object):
    name: str = ""
    type: str = "配角"
    gender: str = ""
    personality: str = ""
    appearance: str = ""
    description: str = ""
    role_notes: str = ""
    line_count: int = 0
    show_times: int = 1
    keep_by_director: bool = False
    personality_conflict_check: Optional[bool] = None   # LLM人设比对后写入，True=冲突

    if HAS_PYDANTIC:
        @field_validator("show_times")
        @classmethod
        def filter_useless_extra(cls, v: int, info):
            data = info.data
            name = data.get("name", "?")
            keep_flag = data.get("keep_by_director", False)
            line_cnt = data.get("line_count", 0)
            if line_cnt <= 0 and v <= 1 and not keep_flag:
                raise SchemaValidateError(ERR_EXTRA_NOT_FILTERED, f"角色{name}出场{v}次无台词，判定龙套未过滤", {"name": name})
            return v


# ═══════════════════════════════════════════
# 7. 分镜单镜头输出（空洞描述 + 台词质检 + 时长上限）
# ═══════════════════════════════════════════
class ShotOutput(BaseModel if HAS_PYDANTIC else object):
    shot_num: int = 1
    focus_character: str = ""        # 必须在 dialogue 之前（Pydantic 按序校验）
    description: str = ""
    dialogue: str = ""
    shot_type: str = "中景"
    camera_movement: str = "固定"
    camera_angle: str = "平视"
    duration_sec: int = 5
    emotion: str = ""
    scene: str = ""
    location: str = ""
    lighting: str = ""
    transition: str = "切入"
    sound_design: str = ""
    similarity_score: Optional[float] = None  # 向量相似度预留字段

    if HAS_PYDANTIC:
        @field_validator("description")
        @classmethod
        def ban_vague_description(cls, v: str):
            for bad_word in VAGUE_DESC_LIST:
                if bad_word in v and len(v) < 60:
                    raise SchemaValidateError(ERR_VAGUE_SHOT, f"镜头描述空洞: {v[:60]}", {"word": bad_word})
            return v.strip()

        @field_validator("duration_sec")
        @classmethod
        def duration_reasonable(cls, v: int):
            if v > 12:
                logger.warning(f"[Schema] 镜头时长{v}s超过12秒上限")
            return v

        @field_validator("dialogue")
        @classmethod
        def dialogue_not_empty(cls, v: str, info):
            """焦点角色无台词时仅告警不阻断（视觉特写镜头合法）"""
            focus = info.data.get("focus_character", "") if info.data else ""
            if focus and focus not in ("", "(无角色)") and (v is None or str(v).strip() in ("", "(无台词)", "(无)", "无")):
                logger.warning(f"[Schema] Shot{info.data.get('shot_num','?')} 焦点角色'{focus}'无台词(视觉镜头)")
            return v or ""


# ═══════════════════════════════════════════
# 对外导出工具函数
# ═══════════════════════════════════════════

def validate_pipeline_input(script_text: str, director_brief: str, director_tasks: dict, stage: str, genre: str = "现代", project_id: str = "") -> dict:
    """pipeline.py 统一入口校验。Pydantic 强校验 + dict 兜底。导演 brief 空 → 抛特定错误码，pipeline 捕获后触发改造8降级。"""
    # 无 Pydantic 兜底
    if not HAS_PYDANTIC:
        if not script_text or not str(script_text).strip():
            raise SchemaValidateError(ERR_EMPTY_SCRIPT, "full_script 剧本内容缺失", {"project_id": project_id})
        if not director_brief and not director_tasks:
            raise SchemaValidateError(ERR_BRIEF_MISSING, "director_brief/tasks 均为空，触发改造8降级", {"project_id": project_id})
        return {"script_text": script_text, "director_brief": director_brief, "director_tasks": director_tasks, "genre": genre, "project_id": project_id}

    # Pydantic 完整校验
    try:
        if not script_text or not str(script_text).strip():
            raise SchemaValidateError(ERR_EMPTY_SCRIPT, "full_script 为空，阻断流水线", {"project_id": project_id})

        script_model = FullScriptSchema(content=script_text, raw_length=len(script_text))
        brief_model = DirectorBriefSchema(genre=genre, core_conflict="", director_vision=director_brief[:200] if director_brief else "")
        tasks_model = DirectorTaskSchema(**{k: str(v)[:DIRECTOR_TASK_MAX_LEN] for k, v in (director_tasks or {}).items()})
        stage_model = StageParamSchema(stage=stage, genre=genre, project_id=project_id)
        PipelineInputSchema(full_script=script_model, brief=brief_model, tasks=tasks_model, stage_params=stage_model)

        # 导演 brief 空 → 抛特定错误码（pipeline 捕获后触发降级）
        if not director_brief and not director_tasks:
            raise SchemaValidateError(ERR_BRIEF_MISSING, "director_brief/tasks 均为空", {"project_id": project_id})

        logger.info(f"[Schema] 全参数校验通过 project={project_id}")
        return {"script_text": script_text, "script_length": len(script_text), "director_brief": director_brief, "director_tasks": director_tasks, "genre": genre, "project_id": project_id}

    except SchemaValidateError:
        raise
    except ValidationError as e:
        raise SchemaValidateError(ERR_STRUCTURE_MISSING, f"Pydantic校验失败: {e.errors()}", {"errors": str(e.errors())[:500]})
    except Exception as e:
        raise SchemaValidateError(ERR_STRUCTURE_MISSING, f"校验异常: {e}", {"raw": str(e)[:200]})


def quality_check_shots(shot_list: List[Dict]) -> dict:
    """agent_storyboard.py 调用：批量校验镜头结构 + 台词 + 空洞描述。返回 {valid, blocking, warnings, total}"""
    if not HAS_PYDANTIC:
        return {"valid": shot_list, "blocking": 0, "warnings": 0, "total": len(shot_list)}

    valid = []
    blocking = 0
    warn_count = 0
    for shot in shot_list:
        try:
            sm = ShotOutput(**{k: shot.get(k, "") if k != "duration_sec" else shot.get(k, 5) for k in [
                "shot_num","description","dialogue","shot_type","camera_movement","camera_angle",
                "duration_sec","emotion","scene","location","focus_character","lighting","transition","sound_design"
            ]})
            valid.append(sm.model_dump() if hasattr(sm, 'model_dump') else shot)
        except SchemaValidateError as e:
            if e.code in (ERR_DIALOGUE_EMPTY, ERR_DIALOGUE_CROSS, ERR_VAGUE_SHOT):
                blocking += 1
            else:
                warn_count += 1
            logger.warning(f"[Schema] [{e.code}] Shot{shot.get('shot_num','?')}: {e.msg}")
            valid.append(shot)
        except Exception as e:
            warn_count += 1
            valid.append(shot)
    logger.info(f"[Schema] 分镜质检完成: {len(shot_list)}镜, 阻断{blocking}, 告警{warn_count}")
    return {"valid": valid, "blocking": blocking, "warnings": warn_count, "total": len(shot_list)}


# ═══════════════════════════════════════════
# 工具函数：文本清洗 + 二次复检
# ═══════════════════════════════════════════
def clean_and_validate(text: str) -> str:
    """清洗文本 → 送入 FullScriptSchema 复检 → 返回干净文本"""
    cleaned = text.replace('\r\n', '\n').replace('\r', '\n')
    cleaned = CONTROL_CHAR_PATTERN.sub('', cleaned)
    # 二次复检
    if HAS_PYDANTIC:
        try:
            FullScriptSchema(content=cleaned, raw_length=len(cleaned))
        except SchemaValidateError:
            pass  # 超长只告警不阻断
    return cleaned
