# -*- coding: utf-8 -*-
"""
hermes_reviewers.py — Hermes 三评审子智能体
交叉校验创意短板：观众视角 / 专业编剧视角 / 市场题材视角
"""

import json
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)


def _call_llm(reviewer_role: str, reviewer_focus: str, script: str, config: dict) -> List[Dict]:
    """
    调用 LLM 执行单一评审视角
    返回 [{"level":"serious"|"warn", "desc":"...", "position":"...", "suggestion":"..."}, ...]
    """
    from services.model_client import UnifiedModel

    prompt = f"""你是一位资深剧本评审专家，担任「{reviewer_role}」角色。
请从 {reviewer_focus} 视角严格评审以下剧本，找出创意缺陷。

【剧本设定】
时代: {config.get('era', '')}
题材: {config.get('genre', '')}
受众: {config.get('audience', '')}
故事梗概: {config.get('core_plot', '')}
核心矛盾: {config.get('core_conflict', '')}

【人物档案】
{json.dumps(config.get('characters', []), ensure_ascii=False, indent=2)}

【剧本内容】
{script[:10000]}

评审要求：
1. 从你的专业视角找出剧情/创意/节奏/人设方面的具体问题
2. 每个问题标注严重程度（serious=需要修改 / warn=建议优化）
3. 给出具体的修改建议（suggestion字段）
4. 至少找出 2 个问题，最多 5 个
5. 如果剧本质量优秀无明显缺陷，返回空数组

输出JSON格式：
{{
  "reviewer": "{reviewer_role}",
  "findings": [
    {{
      "level": "serious" 或 "warn",
      "desc": "具体问题描述",
      "position": "问题类型/位置",
      "suggestion": "修改建议"
    }}
  ]
}}

只输出JSON，不要多余文字。"""

    result = UnifiedModel.llm(
        prompt=prompt,
        system=f"你是资深{reviewer_role}，擅长从{reviewer_focus}角度发现剧本创意问题。",
        max_tokens=4096,
        timeout=90,
    )

    if not result.get("success"):
        logger.warning(f"Hermes评审[{reviewer_role}] LLM调用失败: {result.get('error')}")
        return []

    text = result.get("data", result.get("text", ""))
    # 提取 JSON
    import re
    json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    content = json_match.group(1) if json_match else text
    content = content.strip()

    try:
        data = json.loads(content)
        findings = data.get("findings", [])
        logger.info(f"Hermes评审[{reviewer_role}]: 发现 {len(findings)} 个问题")
        return findings
    except json.JSONDecodeError as e:
        logger.warning(f"Hermes评审[{reviewer_role}] JSON解析失败: {e}")
        return [{"level": "warn", "desc": f"评审[{reviewer_role}]结果解析异常", "position": "评审模块"}]


def review_audience(script: str, config: dict) -> List[Dict]:
    """
    观众视角评审
    预判剧情是否乏味、反转是否可提前猜到、有无审美疲劳桥段
    """
    return _call_llm(
        reviewer_role="观众视角评审专家",
        reviewer_focus="普通观众：预判剧情乏味度、反转可预测性、审美疲劳桥段",
        script=script,
        config=config,
    )


def review_professional(script: str, config: dict) -> List[Dict]:
    """
    专业编剧视角评审
    情节单薄、冲突浅层、人物扁平问题
    """
    return _call_llm(
        reviewer_role="专业编剧评审专家",
        reviewer_focus="资深编剧：情节厚度、冲突层次、人物弧光完整度",
        script=script,
        config=config,
    )


def review_market(script: str, config: dict) -> List[Dict]:
    """
    市场题材视角评审
    同赛道对比、同质化段落、创新方向建议
    """
    # 先查同类目已知缺陷
    common_defects = []
    try:
        from agents.hermes_db import get_common_defects
        common_defects = get_common_defects(config.get("era", ""), config.get("genre", ""))
    except ImportError:
        try:
            from hermes_db import get_common_defects
            common_defects = get_common_defects(config.get("era", ""), config.get("genre", ""))
        except ImportError:
            pass

    extra_hint = ""
    if common_defects:
        extra_hint = f"\n同类目历史常见问题（请重点审查是否有重复）：\n" + "\n".join(f"- {d}" for d in common_defects)

    return _call_llm(
        reviewer_role="市场与题材评审专家",
        reviewer_focus=f"市场数据分析：同赛道对比、同质化检测、创新空间{extra_hint}",
        script=script,
        config=config,
    )


def run_all_reviews(script: str, config: dict) -> List[Dict]:
    """
    运行全部三个评审视角
    返回合并后的缺陷列表
    """
    all_findings = []

    for reviewer_fn, name in [
        (review_audience, "观众视角"),
        (review_professional, "专业编剧"),
        (review_market, "市场题材"),
    ]:
        try:
            findings = reviewer_fn(script, config)
            all_findings.extend(findings)
        except Exception as e:
            logger.exception(f"Hermes评审[{name}]异常: {e}")
            all_findings.append({
                "level": "warn",
                "desc": f"[{name}]评审异常: {e}",
                "position": "评审模块",
                "suggestion": "请人工复核此维度",
            })

    logger.info(f"Hermes: 三评审完成，共 {len(all_findings)} 条建议")
    return all_findings
