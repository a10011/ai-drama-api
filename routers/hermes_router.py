# -*- coding: utf-8 -*-
"""
hermes_router.py — Hermes A+剧本智能体 API 路由
提供完整的工作流端点：参数校验 → 大纲生成 → 正文生成 → 质检 → 导出
"""

import json
import logging
from typing import Optional, List
from pydantic import BaseModel, Field

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

logger = logging.getLogger("hermes")

router = APIRouter(prefix="/api/v1/hermes", tags=["Hermes剧本智能体"])

# ──────────────────────────────────────────────
# 请求/响应模型
# ──────────────────────────────────────────────

class CharacterRequest(BaseModel):
    name: str = Field(..., description="角色名")
    age: int = Field(..., ge=0, le=200)
    identity: str = Field(..., description="身份")
    motive: str = Field(..., description="核心动机")
    personality: str = Field(..., description="性格")
    taboo_behavior: List[str] = Field(default_factory=list, description="禁忌行为")
    speak_style: str = Field("", description="说话风格")


class HermesStartRequest(BaseModel):
    """启动Hermes的完整参数"""
    era: str = Field(..., description="时代：古代/现代/民国/玄幻架空")
    genre: str = Field(..., description="题材：悬疑/都市甜宠/权谋/现实")
    audience: str = Field("女频", description="受众：女频/男频/全年龄")
    total_length: str = Field("中篇完整剧本", description="篇幅")
    world_rule: str = Field("", description="世界观规则")
    core_plot: str = Field(..., description="完整故事梗概")
    core_conflict: str = Field(..., description="核心矛盾")
    ending_type: str = Field("开放式", description="结局：开放式/圆满/悲剧/悬疑留白")
    special_limits: str = Field("", description="特殊限制")
    characters: List[CharacterRequest] = Field(..., min_length=1, description="人物档案至少1人")


class OutlineConfirmRequest(BaseModel):
    outline: str = Field(..., description="用户确认/融合后的大纲文本")


class RunRequest(BaseModel):
    outline: str = Field(..., description="已确认的大纲")


# ──────────────────────────────────────────────
# 全局 session 缓存（生产环境应改用 Redis/DB）
# ──────────────────────────────────────────────
_session_store: dict = {}


def _get_agent(session_id: str):
    agent = _session_store.get(session_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"session {session_id} 不存在，请先 POST /start")
    return agent


# ──────────────────────────────────────────────
# API 端点
# ──────────────────────────────────────────────

@router.post("/start", summary="步骤1: 初始化 + 参数校验")
async def hermes_start(req: HermesStartRequest):
    """
    初始化 Hermes 智能体，校验参数完整性及冲突。
    成功返回 session_id，后续操作需携带此 id。
    """
    from agents.agent_hermes import HermesConfig, HermesCharacter, HermesScriptAgent

    chars = [
        HermesCharacter(
            name=c.name, age=c.age, identity=c.identity,
            motive=c.motive, personality=c.personality,
            taboo_behavior=c.taboo_behavior, speak_style=c.speak_style,
        )
        for c in req.characters
    ]

    config = HermesConfig(
        era=req.era, genre=req.genre, audience=req.audience,
        total_length=req.total_length, world_rule=req.world_rule,
        core_plot=req.core_plot, core_conflict=req.core_conflict,
        ending_type=req.ending_type, special_limits=req.special_limits,
        characters=chars,
    )

    valid, msg = config.check_mandatory()
    if not valid:
        return JSONResponse(status_code=400, content={"success": False, "error": msg})

    conflicts = config.check_conflicts()

    session_id = f"hermes_{id(config)}_{len(_session_store)}"
    agent = HermesScriptAgent(config, session_id=session_id)
    _session_store[session_id] = agent

    return {
        "success": True,
        "data": {
            "session_id": session_id,
            "validation": {"passed": True, "message": msg},
            "conflicts": conflicts,
            "character_count": len(chars),
        }
    }


@router.post("/generate_outlines", summary="步骤2: 生成三套差异化大纲")
async def generate_outlines(session_id: str):
    """
    基于已初始化的配置，生成3套差异化创意大纲。
    返回三套方案列表供用户选择/融合。
    """
    agent = _get_agent(session_id)
    try:
        schemes = agent.generate_outlines()
        return {"success": True, "data": {"schemes": schemes}}
    except Exception as e:
        logger.exception(f"Hermes 大纲生成失败")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/confirm_outline", summary="步骤3: 用户确认大纲")
async def confirm_outline(session_id: str, req: OutlineConfirmRequest):
    """用户选择/融合大纲后锁定"""
    agent = _get_agent(session_id)
    agent.set_outline(req.outline)
    return {"success": True, "data": {"message": "大纲已锁定，可开始正文生成"}}


@router.post("/run", summary="步骤4: 全自动生成+质检+重写")
async def hermes_run(session_id: str):
    """
    执行完整闭环：正文生成 → 三级质检 → 严重缺陷定向重写(最多3次) → 输出
    返回最终成品剧本 + 缺陷清单
    """
    agent = _get_agent(session_id)
    try:
        script, defects = agent.run()
        if script:
            return {
                "success": True,
                "data": {
                    "script": script,
                    "defects": defects,
                    "retry_count": agent.retry_count,
                    "warnings": [d for d in defects if d.get("level") == "warn"],
                }
            }
        else:
            return JSONResponse(status_code=422, content={
                "success": False,
                "error": "剧本质检未通过，已达最大重试次数",
                "defects": defects,
            })
    except Exception as e:
        logger.exception(f"Hermes run 失败")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.post("/quick", summary="一键生成：参数校验→大纲→确认→正文→质检→输出")
async def hermes_quick(req: HermesStartRequest):
    """
    极速模式：传入全部参数，自动完成全流程。
    注：大纲阶段跳过人工选择，默认走方案A稳妥路线。
    """
    from agents.agent_hermes import HermesConfig, HermesCharacter, HermesScriptAgent

    chars = [
        HermesCharacter(
            name=c.name, age=c.age, identity=c.identity,
            motive=c.motive, personality=c.personality,
            taboo_behavior=c.taboo_behavior, speak_style=c.speak_style,
        )
        for c in req.characters
    ]

    config = HermesConfig(
        era=req.era, genre=req.genre, audience=req.audience,
        total_length=req.total_length, world_rule=req.world_rule,
        core_plot=req.core_plot, core_conflict=req.core_conflict,
        ending_type=req.ending_type, special_limits=req.special_limits,
        characters=chars,
    )

    valid, msg = config.check_mandatory()
    if not valid:
        return JSONResponse(status_code=400, content={"success": False, "error": msg})

    import time
    session_id = f"hermes_quick_{int(time.time())}"
    agent = HermesScriptAgent(config, session_id=session_id)

    # 自动走大纲方案A
    schemes = agent.generate_outlines()
    if not schemes:
        return JSONResponse(status_code=500, content={"success": False, "error": "大纲生成失败"})

    auto_outline = schemes[0].get("outline", schemes[0].get("outline", ""))
    agent.set_outline(auto_outline)

    script, defects = agent.run()
    if script:
        return {
            "success": True,
            "data": {
                "script": script,
                "defects": defects,
                "retry_count": agent.retry_count,
                "scheme_used": schemes[0],
                "warnings": [d for d in defects if d.get("level") == "warn"],
            }
        }
    else:
        return JSONResponse(status_code=422, content={
            "success": False, "error": "剧本质检未通过",
            "defects": defects,
        })
