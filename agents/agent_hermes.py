# -*- coding: utf-8 -*-
"""
agent_hermes.py — Hermes A+级剧本智能体（生产版）
完全遵循 2026-07-13 设计文档：
  参数校验 → 3套差异化大纲 → 人工确认 → 正文生成 → 三级质检 → 定向重写 → 归档

依赖: services/model_client.py → UnifiedModel.llm()
"""

import json
import logging
import re
from typing import Optional, List, Dict, Tuple

# 延迟导入避免循环依赖
_REVIEWERS = None
_DB = None

def _get_reviewers():
    global _REVIEWERS
    if _REVIEWERS is None:
        from agents import hermes_reviewers
        _REVIEWERS = hermes_reviewers
    return _REVIEWERS

def _get_db():
    global _DB
    if _DB is None:
        from agents import hermes_db
        _DB = hermes_db
    return _DB

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────
MAX_RETRY_TIMES = 3
MAX_OUTLINE_SCHEMES = 3

BLACK_LIST_WORDS = [
    "叼烟", "吸烟", "倚靠椅背",
    "暴力伤人细节", "低俗色情描写", "歪曲历史人物",
    "吸毒", "自残",
]

CLICHE_LIST = [
    "车祸失忆", "强行带球跑", "霸总强制囚禁",
    "白血病绝症烂梗", "跳崖坠崖必得秘籍",
    "契约情侣假戏真做",
]

AUDIENCE_HINTS = {
    "女频": "侧重情感刻画、人物关系细腻、成长线",
    "男频": "侧重冲突升级、逆袭爽感、世界观展开",
    "全年龄": "平衡情感与剧情，避免极端设定",
}

ENDINGS = ["开放式", "圆满", "悲剧", "悬疑留白"]

# ──────────────────────────────────────────────
# 数据模型
# ──────────────────────────────────────────────

class HermesCharacter:
    """人物档案"""
    def __init__(self, name: str, age: int, identity: str,
                 motive: str, personality: str,
                 taboo_behavior: List[str], speak_style: str):
        self.name = name
        self.age = age
        self.identity = identity
        self.motive = motive
        self.personality = personality
        self.taboo_behavior = taboo_behavior
        self.speak_style = speak_style

    def to_dict(self) -> dict:
        return {
            "name": self.name, "age": self.age,
            "identity": self.identity, "motive": self.motive,
            "personality": self.personality,
            "taboo_behavior": self.taboo_behavior,
            "speak_style": self.speak_style,
        }


class HermesConfig:
    """全局前置参数——缺项阻断"""
    def __init__(self, era: str, genre: str, audience: str,
                 total_length: str, world_rule: str,
                 core_plot: str, core_conflict: str,
                 ending_type: str,
                 special_limits: str = "",
                 characters: Optional[List[HermesCharacter]] = None):
        self.era = era
        self.genre = genre
        self.audience = audience
        self.total_length = total_length
        self.world_rule = world_rule
        self.core_plot = core_plot
        self.core_conflict = core_conflict
        self.ending_type = ending_type
        self.special_limits = special_limits
        self.characters = characters or []

    def check_mandatory(self) -> Tuple[bool, str]:
        missing = []
        if not self.era: missing.append("时代背景")
        if not self.genre: missing.append("题材赛道")
        if not self.core_plot: missing.append("故事梗概")
        if not self.core_conflict: missing.append("核心矛盾")
        if not self.characters: missing.append("人物档案（至少1个角色）")
        if missing:
            return False, f"缺失必填硬性信息：{'、'.join(missing)}，Hermes无法启动"
        return True, "参数校验通过"

    def check_conflicts(self) -> List[str]:
        """检查参数冲突（如古代+都市人物等）"""
        warnings = []
        ancient_eras = {"古代", "民国", "古装"}
        modern_genres = {"都市", "职场", "校园"}
        if self.era in ancient_eras and any(g in self.genre for g in modern_genres):
            warnings.append(f"时代「{self.era}」与题材「{self.genre}」可能存在冲突")
        return warnings


# ──────────────────────────────────────────────
# Hermes 智能体
# ──────────────────────────────────────────────

class HermesScriptAgent:
    """Hermes A+剧本智能体 — 纯剧本创作，不含分镜/画面/剪辑"""

    def __init__(self, config: HermesConfig, llm_func=None, session_id: str = ""):
        """
        llm_func: callable(prompt, system, max_tokens, timeout) → str
        默认使用 UnifiedModel.llm()
        """
        self.config = config
        self._llm = llm_func or self._default_llm
        self.session_id = session_id
        self.retry_count = 0
        self.best_outline: Optional[str] = None
        self.final_script: Optional[str] = None
        self.defects: List[dict] = []
        self.positive_samples: list = []
        self.negative_samples: list = []

    # ─── LLM 调用 ──────────────────────────────

    @staticmethod
    def _default_llm(prompt: str, system: str = "",
                     max_tokens: int = 8192, timeout: int = 120) -> str:
        """默认 LLM 调用：走 UnifiedModel"""
        from services.model_client import UnifiedModel
        result = UnifiedModel.llm(
            prompt=prompt,
            system=system,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        if result.get("success"):
            return result.get("data", result.get("text", ""))
        raise RuntimeError(f"LLM调用失败: {result.get('error', '未知错误')}")

    def _llm_json(self, prompt: str, system: str = "",
                  max_tokens: int = 8192, timeout: int = 120) -> dict:
        """调用 LLM 并强制解析 JSON 返回"""
        resp = self._llm(prompt, system, max_tokens, timeout)
        # 尝试从 markdown 代码块提取
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', resp, re.DOTALL)
        text = json_match.group(1) if json_match else resp
        text = text.strip()
        return json.loads(text)

    # ─── 步骤1: 三套差异化大纲 ─────────────────

    def generate_outlines(self) -> List[dict]:
        """
        并行生成 3 套差异化创意大纲
        返回 [{"name": str, "outline": str, "highlights": str}, ...]
        """
        chars_json = json.dumps(
            [c.to_dict() for c in self.config.characters],
            ensure_ascii=False, indent=2
        )

        system = """你是一位A+级金牌编剧Hermes，擅长产出差异化创意剧本大纲。
输出严格JSON格式，不要多余文字。"""

        prompt = f"""根据以下设定，产出 {MAX_OUTLINE_SCHEMES} 套完全差异化的大纲方案。

【时代】{self.config.era}
【题材】{self.config.genre}
【受众】{self.config.audience}
【世界规则】{self.config.world_rule}
【故事梗概】{self.config.core_plot}
【核心矛盾】{self.config.core_conflict}
【结局要求】{self.config.ending_type}
{'' if not self.config.special_limits else '【特殊限制】' + self.config.special_limits}

【人物档案】
{chars_json}

硬性要求：
1. 三套方案必须路线不同：方案A稳妥商业版、方案B反转创新版、方案C现实细腻版
2. 每套包含：开篇钩子、中段反转、长线伏笔、高潮、结局
3. 主动规避烂俗套路：{', '.join(CLICHE_LIST)}
4. 禁止出现违禁内容：{', '.join(BLACK_LIST_WORDS)}
5. {AUDIENCE_HINTS.get(self.config.audience, '')}

输出JSON格式：
{{
  "schemes": [
    {{
      "name": "方案A-稳妥商业版",
      "outline": "完整大纲文本，至少500字",
      "highlights": "创意亮点说明",
      "hook_3s": "开篇3秒钩子设计",
      "mid_reverse": "中段反转设计"
    }},
    ...
  ]
}}"""
        raw = self._llm_json(prompt, system, max_tokens=8192, timeout=120)
        schemes = raw.get("schemes", [])
        logger.info(f"Hermes: 生成 {len(schemes)} 套大纲")
        return schemes

    # ─── 步骤2: 确认大纲 ──────────────────────

    def set_outline(self, user_outline: str):
        """用户确认/融合后的大纲"""
        self.best_outline = user_outline
        logger.info("Hermes: 大纲已锁定")

    # ─── 步骤3: 正文生成 ──────────────────────

    def generate_script_draft(self) -> str:
        """基于定稿大纲生成完整剧本"""
        if not self.best_outline:
            raise ValueError("未设置大纲，请先调用 set_outline()")

        chars_json = json.dumps(
            [c.to_dict() for c in self.config.characters],
            ensure_ascii=False, indent=2
        )

        system = """你是一位A+级金牌编剧Hermes，擅长撰写高质量剧本。
输出严格分场景格式，每个场景包含：场景号、昼夜、环境、旁白、人物动作、台词。"""

        prompt = f"""根据定稿大纲完整撰写A+标准剧本。

【时代】{self.config.era}
【题材】{self.config.genre}
【受众】{self.config.audience}
【篇幅】{self.config.total_length}
【结局】{self.config.ending_type}
{'' if not self.config.special_limits else '【特殊限制】' + self.config.special_limits}

【人物档案】
{chars_json}

【定稿大纲】
{self.best_outline}

剧本强制规范：
1. 分场景输出，区分【场景号】【昼夜】【环境】【旁白】【人物动作】【台词】
2. 每场至少1个有效冲突，开篇强钩子，中段反转，伏笔全部回收
3. 人物行为100%匹配人设，禁止出现禁忌行为
4. 全程规避违禁词：{', '.join(BLACK_LIST_WORDS)}
5. 规避烂俗套路：{', '.join(CLICHE_LIST)}
6. 增加细腻感官细节，提升氛围感
7. 篇幅符合「{self.config.total_length}」要求

输出格式：纯剧本文本，不要JSON包装。"""

        script = self._llm(prompt, system, max_tokens=16384, timeout=300)
        logger.info(f"Hermes: 剧本初稿生成完成，长度 {len(script)} 字")
        return script

    # ─── 步骤4: 三级质检 ──────────────────────

    def quality_inspect(self, script: str) -> List[dict]:
        """
        五维质检（原三层 + 三评审交叉 + 创意细节）：
        1. 合规黑名单（关键词）
        2. 人设崩坏 + 结构完整（LLM语义判断）
        3. 创意细节校验（感官描写/伏笔回收/冲突多元化)
        4. 三评审子智能体交叉校验（观众/编剧/市场）

        返回 [{"level": "serious"|"warn", "desc": str, "position": str}, ...]
        """
        defects = []
        config_dict = {
            "era": self.config.era, "genre": self.config.genre,
            "audience": self.config.audience,
            "core_plot": self.config.core_plot,
            "core_conflict": self.config.core_conflict,
            "characters": [c.to_dict() for c in self.config.characters],
        }

        # ── L1：合规黑名单关键词扫描 ──
        for word in BLACK_LIST_WORDS:
            if word in script:
                defects.append({
                    "level": "serious",
                    "desc": f"检测到违禁内容：{word}",
                    "position": "L1合规"
                })

        # ── L2：人设崩坏 + 结构完整（LLM语义判断） ──
        chars_json = json.dumps(
            [c.to_dict() for c in self.config.characters],
            ensure_ascii=False, indent=2
        )

        qc_prompt = f"""你是一位资深剧本质检专家。请严格审核以下剧本，输出缺陷清单。

【时代】{self.config.era}
【题材】{self.config.genre}
【结局要求】{self.config.ending_type}

【人物档案】
{chars_json}

【剧本内容】
{script[:12000]}

审核维度：
1. 人设崩坏：角色行为是否与档案中的人设/禁忌行为冲突？
2. 时代错位：是否出现跨时代道具、行为、台词？
3. 主线偏移：剧情是否偏离既定梗概「{self.config.core_plot}」？
4. 结构缺失：是否有开篇钩子？中段反转？伏笔回收？
5. 台词生硬/语句重复（warn级）
6. 烂俗套路检测：{', '.join(CLICHE_LIST)}（warn级）

输出JSON格式：
{{
  "defects": [
    {{
      "level": "serious" 或 "warn",
      "desc": "具体问题描述",
      "position": "问题类型（L2逻辑）"
    }}
  ],
  "summary": "总体评价，是否建议重写"
}}

只输出JSON，不要多余文字。"""

        try:
            qc_result = self._llm_json(qc_prompt, max_tokens=4096, timeout=90)
            qc_defects = qc_result.get("defects", [])
            defects.extend(qc_defects)
        except Exception as e:
            logger.warning(f"Hermes L2质检失败: {e}")

        # ── L3：创意细节校验（感官描写/伏笔回收/冲突多元化） ──
        creative_prompt = f"""请审核以下剧本的创意细节质量，输出缺陷清单。

【剧本内容】
{script[:8000]}

审核维度（warn级，不阻断）：
1. 微观氛围感：是否缺乏感官细节？（气味、光影、微小肢体动作）
2. 长线伏笔：每一幕是否预埋了长线伏笔？结尾是否回收？
3. 冲突多元化：是否仅靠吵架/背叛制造矛盾？有没有道德两难/心理博弈？
4. 反套路：是否有意规避了{', '.join(CLICHE_LIST)}这类模板桥段？

输出JSON格式：
{{
  "defects": [
    {{
      "level": "warn",
      "desc": "具体创意细节问题",
      "position": "L3创意细节",
      "suggestion": "优化建议"
    }}
  ]
}}

只输出JSON。"""

        try:
            creative_result = self._llm_json(creative_prompt, max_tokens=4096, timeout=90)
            creative_defects = creative_result.get("defects", [])
            defects.extend(creative_defects)
        except Exception as e:
            logger.warning(f"Hermes L3创意质检失败: {e}")

        # ── L4：三评审子智能体交叉校验 ──
        try:
            reviewers = _get_reviewers()
            reviewer_defects = reviewers.run_all_reviews(script, config_dict)
            defects.extend(reviewer_defects)
        except Exception as e:
            logger.warning(f"Hermes 三评审异常: {e}")
            defects.append({
                "level": "warn",
                "desc": f"三评审子智能体未完成: {e}",
                "position": "L4评审"
            })

        return defects

    # ─── 步骤5: 定向重写 ──────────────────────

    def rewrite_by_defects(self, script: str, defects: List[dict]) -> str:
        """携带缺陷清单定向重写"""
        serious = [d for d in defects if d.get("level") == "serious"]
        serious_text = "\n".join(f"- {d['desc']}" for d in serious)

        chars_json = json.dumps(
            [c.to_dict() for c in self.config.characters],
            ensure_ascii=False, indent=2
        )

        prompt = f"""以下剧本存在严重缺陷，请定向修正问题段落，保留无问题内容。

【原有剧本】
{script[:15000]}

【必须整改的严重缺陷】
{serious_text}

硬性约束不变：
- 时代：{self.config.era}
- 题材：{self.config.genre}
- 人物人设不变，禁止出现禁忌行为
- 剧本格式：分场景（场景号/昼夜/环境/旁白/动作/台词）
- 开篇钩子、中段反转、伏笔回收
- 规避：{', '.join(BLACK_LIST_WORDS)}
- 规避：{', '.join(CLICHE_LIST)}

输出修正后的完整合规A+剧本。"""

        new_script = self._llm(prompt, max_tokens=16384, timeout=300)
        self.retry_count += 1
        logger.info(f"Hermes: 第{self.retry_count}次重写完成")
        return new_script

    # ─── 主循环 ────────────────────────────────

    def run(self) -> Tuple[Optional[str], List[dict]]:
        """
        全自动生成+质检+重写闭环
        返回 (final_script, all_defects)
        - 成功: final_script=剧本文本, defects=全部缺陷(含warn)
        - 失败: final_script=None, defects=严重缺陷清单
        """
        # 前置校验
        valid, msg = self.config.check_mandatory()
        if not valid:
            logger.error(f"Hermes 参数校验失败: {msg}")
            return None, [{"level": "serious", "desc": msg, "position": "参数校验"}]

        conflicts = self.config.check_conflicts()
        for c in conflicts:
            logger.warning(f"Hermes 参数冲突: {c}")

        if not self.best_outline:
            return None, [{"level": "serious", "desc": "未设置大纲", "position": "流程"}]

        # 正文生成
        current = self.generate_script_draft()

        # 质检+重写循环
        while self.retry_count < MAX_RETRY_TIMES:
            self.defects = self.quality_inspect(current)
            serious_bugs = [d for d in self.defects if d.get("level") == "serious"]

            if not serious_bugs:
                self.final_script = current
                logger.info(f"✅ Hermes A+剧本质检通过，剩余警告{len(self.defects)}条")
                self._archive()
                return self.final_script, self.defects

            logger.warning(f"Hermes 检测到{len(serious_bugs)}条严重缺陷，重写中...")
            current = self.rewrite_by_defects(current, self.defects)

        # 重试耗尽
        logger.error(f"Hermes 已达最大重试{MAX_RETRY_TIMES}次，仍有严重缺陷")
        return None, self.defects

    # ─── 归档 ──────────────────────────────────

    def _archive(self):
        """归档优质/缺陷样本（内存+SQLite持久化）"""
        warnings = [d for d in self.defects if d.get("level") == "warn"]

        # 内存归档
        if self.final_script:
            self.positive_samples.append({
                "config": self.config.__dict__,
                "outline": self.best_outline,
                "script": self.final_script[:500],
                "warnings": warnings,
            })
        self.negative_samples.append({
            "era": self.config.era,
            "genre": self.config.genre,
            "defects": self.defects,
        })

        # SQLite 持久化
        try:
            db = _get_db()
            if self.final_script:
                db.save_positive(
                    era=self.config.era,
                    genre=self.config.genre,
                    config={
                        "era": self.config.era,
                        "genre": self.config.genre,
                        "audience": self.config.audience,
                        "core_plot": self.config.core_plot[:200],
                        "core_conflict": self.config.core_conflict[:200],
                    },
                    outline=self.best_outline or "",
                    script=self.final_script or "",
                    warnings=warnings,
                )
                db.save_output(
                    session_id=self.session_id,
                    era=self.config.era,
                    genre=self.config.genre,
                    audience=self.config.audience,
                    outline=self.best_outline or "",
                    script=self.final_script or "",
                    retry_count=self.retry_count,
                    defects=self.defects,
                )
            if self.defects:
                db.save_negative(
                    era=self.config.era,
                    genre=self.config.genre,
                    defects=self.defects,
                )
        except Exception as e:
            logger.warning(f"Hermes SQLite归档失败: {e}")

        logger.info(f"Hermes: 归档完成（正向{len(self.positive_samples)}条，反向{len(self.negative_samples)}条）")
