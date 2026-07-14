"""
剧本审查+优化端点 — AI自动改风控词等硬伤，返回diff供会员确认
"""
import json, re, logging
from fastapi import APIRouter, Request
from pydantic import BaseModel
from services.model_client import UnifiedModel
from core.agent_base_v3 import AgentV3

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/pipeline", tags=["剧本审查"])


class ReviewRequest(BaseModel):
    script_text: str = ""
    genre: str = ""


class TempReviewAgent(AgentV3):
    name = "reviewer"
    def execute(self, task): pass


# 必须改的风控词 → 替换词
RISKY_REPLACE = {
    "九死一生": "征途艰险",
    "永诀": "暂别",
    "赴死": "出征",
    "崩溃痛哭": "掩面抽泣",
    "伤口": "征尘",
    "血迹": "尘泥",
    "血淋淋": "泥泞",
    "泪流满面": "眼含泪光",
    "泪如雨下": "泪光闪烁",
    "嚎啕大哭": "低声哽咽",
    "伤疤": "旧痕",
}


def auto_fix_risky(script: str) -> tuple:
    """自动替换风控词，返回 (修复后文本, 修改列表)"""
    fixed = script
    changes = []
    for bad, good in RISKY_REPLACE.items():
        if bad in fixed:
            fixed = fixed.replace(bad, good)
            changes.append({"type": "风控", "severity": "高",
                           "original": bad, "rewritten": good,
                           "suggestion": f"'{bad}' 触发审核拦截，已自动替换为 '{good}'"})
    return fixed, changes


@router.post("/review-script")
async def review_script(body: ReviewRequest, request: Request):
    """审查+优化剧本，返回诊断+优化后剧本，会员确认后应用"""
    try:
        script = (body.script_text or "").strip()
        if len(script) < 30:
            return {"success": False, "error": "剧本太短，至少30字"}

        genre = body.genre or ""

        # 1. 自动修风控词
        safe_script, risky_fixes = auto_fix_risky(script)

        # 2. AI 深度审查 + 优化
        prompt = (
            "你是资深剧本医生。请审查以下短剧剧本，找出所有需要优化的问题，并给出优化后的完整剧本。" + chr(10) +
            "题材：" + genre + chr(10) + chr(10) +
            "【审查维度】" + chr(10) +
            "1. 节奏：镜头是否太碎/太拖，高潮分布是否合理" + chr(10) +
            "2. 台词：是否自然、符合角色身份、无违禁词" + chr(10) +
            "3. 结构：开场/发展/高潮/收尾是否完整" + chr(10) +
            "4. 情绪：描写是否含蓄克制（禁止崩溃/血泪/伤口特写）" + chr(10) + chr(10) +
            "【优化原则】" + chr(10) +
            "- 风控词必须替换（九死一生/永诀/赴死/崩溃痛哭 等→征途遥远/暂别/出征/掩面抽泣）" + chr(10) +
            "- 情绪描写改为含蓄版（泪流满面→眼含泪光，嚎啕→哽咽）" + chr(10) +
            "- 只改硬伤不改创意，保留原作的剧情走向和人物性格" + chr(10) + chr(10) +
            "剧本：" + chr(10) + safe_script[:4000] + chr(10) + chr(10) +
            "输出JSON（只输出JSON）：" + chr(10) +
            '{"score":85,"issues":[{"type":"节奏|台词|结构|角色|合规","severity":"高|中|低",'
            '"original":"原文片段","suggestion":"为什么改","rewritten":"优化后文本"}],'
            '"optimized_script":"优化后的完整剧本","summary":"整体评价"}'
        )

        agent = TempReviewAgent(0)
        result = agent.call_with_safety_retry(
            "deepseek-v4-flash", 5,
            UnifiedModel.llm,
            prompt=prompt,
            max_tokens=8192,
            timeout=90,
        )

        text = result.get("text", "")
        try:
            review = json.loads(text)
        except Exception:
            m = re.search(r'\{.*\}', text, re.DOTALL)
            review = json.loads(m.group()) if m else {
                "score": 0, "issues": [], "optimized_script": script, "summary": "审查解析失败"
            }

        # 合并风控自动修复到 issues 最前面
        all_issues = risky_fixes + review.get("issues", [])
        review["issues"] = all_issues
        review["original_script"] = script
        review["auto_fixed_risky"] = len(risky_fixes) > 0

        return {"success": True, "data": review}

    except Exception as e:
        logger.error(f"剧本审查失败: {e}")
        return {"success": False, "error": str(e)[:200]}
