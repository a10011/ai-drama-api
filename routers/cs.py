"""客服智能体 - Hermes AI 助手"""
import logging, json
from fastapi import APIRouter, Query
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/cs", tags=["客服"])

SYSTEM_PROMPT = """你是AI短剧创作平台的智能客服助手"爱马仕"，回答要简洁、友好。

平台能力：
1. 创作短剧：上传剧本或输入剧情梗概，AI自动生成剧本、角色、分镜、配音、字幕、BGM
2. 动漫/真人两种视觉风格可选
3. 角色设计：AI自动提取角色并生成立绘形象（支持上传照片换脸）
4. 分镜生成：AI根据剧本生成分镜画面
5. 配音合成：多种AI语音可选
6. 视频合成：自动合成完整短视频
7. 积分系统：免费用户每日有积分额度，可充值提升
8. 会员权益：VIP用户享受更多功能

回复规则：
- 简洁直接，每段不超过3句话
- 不知道的就如实说不知道，不要编造
- 如果用户想创作，引导先上传剧本或输入剧情梗概
- 如果是技术问题，建议联系技术支持
"""

def _chat(text: str, model: str = "claude-sonnet-4-20250514") -> str:
    """客服聊天 - 默认走 Claude，可通过 model 参数切换"""
    try:
        from services.model_client import call_llm
        result = call_llm(
            prompt=text,
            system=SYSTEM_PROMPT,
            model=model,
            timeout=45,
            max_tokens=500
        )
        if result.get("success"):
            return result["text"].strip()
        logger.warning(f"CS chat failed: {result.get('error')}")
        return "客服暂时繁忙，请稍后再试或联系管理员。"
    except Exception as e:
        logger.warning(f"CS chat exception: {e}")
        return "客服暂时繁忙，请稍后再试或联系管理员。"


@router.get("/chat")
async def cs_chat(msg: str = Query(..., description="用户消息"),
                  model: str = Query("claude-sonnet-4-20250514", description="模型: claude-sonnet-4-20250514 / deepseek-chat")):
    """Hermes 客服聊天接口"""
    reply = _chat(msg, model=model)
    return {"reply": reply, "model": model}
