"""
角色设计专用工具 — 确保角色视觉一致性、形象真实性
"""
import re
import logging
from tools.base import AgentTool, ToolResult

logger = logging.getLogger("tools.character")


class CharacterConsistencyCheck(AgentTool):
    name = "character_consistency_check"
    description = "检查角色在不同分镜中的形象是否一致。参数: characters(dict,角色名→描述), storyboard(list,分镜文本数组)"
    category = "character"

    async def execute(self, characters: dict = None, storyboard: list = None) -> ToolResult:
        if not characters or not storyboard:
            return self._fail("缺少角色或分镜数据")

        issues = []
        total_mentions = 0
        consistent_mentions = 0

        for name, desc in (characters or {}).items():
            key_traits = self._extract_traits(desc)
            for i, shot in enumerate(storyboard or []):
                if name not in str(shot):
                    continue
                total_mentions += 1
                shot_traits = self._extract_traits(str(shot))
                conflicts = [t for t in key_traits if t not in shot_traits and self._opposite_trait(t) in shot_traits]
                if not conflicts:
                    consistent_mentions += 1
                elif len(conflicts) <= 1:
                    consistent_mentions += 0.5

        consistency = consistent_mentions / max(total_mentions, 1)
        score = round(consistency * 10, 1)

        if score < 6:
            issues.append("角色形象在不同分镜中存在严重不一致")
        elif score < 8:
            issues.append("角色形象基本一致，部分细节有偏差")

        return self._ok(
            {"consistency_score": score, "issues": issues,
             "suggestion": "统一角色核心特征（发型/服装/体型）的用词" if score < 7 else "角色一致性良好"},
            score * 10
        )

    def _extract_traits(self, text: str) -> set:
        patterns = [
            r'长[发发]|短发|卷发|直发|马尾|寸头',
            r'白[衬衫衣]|黑[衬衫衣]|红[衬衫衣]|长裙|短裙|牛仔裤|西装|古装|汉服',
            r'高[挑瘦大]|矮[小胖]|苗条|魁梧|瘦弱|微胖',
            r'眼镜|胡须|马尾|丸子头',
        ]
        traits = set()
        for p in patterns:
            for m in re.findall(p, text):
                traits.add(m)
        return traits

    def _opposite_trait(self, trait: str) -> str:
        opposites = {"长发": "短发", "短发": "长发", "高挑": "矮小", "苗条": "魁梧"}
        return opposites.get(trait, "")


class CharacterVisualPrompt(AgentTool):
    name = "character_visual_prompt"
    description = "优化角色图生图prompt，自动添加反卡通/反3D/反动漫约束"
    category = "character"

    async def execute(self, name: str = "", description: str = "", style: str = "现代", size: str = "1024x1024") -> ToolResult:
        if not name or not description:
            return self._fail("缺少角色名或描述")

        anti = "NOT cartoon, NOT anime, NOT 3D rendered, NOT illustration"
        anti_cn = "不是卡通不是动漫不是3D渲染不是插画风格"

        style_map = {
            "古装": "traditional Chinese costume, period drama aesthetic",
            "现代": "contemporary fashion, modern attire",
            "科幻": "futuristic sci-fi outfit, cyberpunk aesthetic",
        }
        style_en = style_map.get(style, "contemporary fashion")

        prompt = (
            f"Photorealistic portrait of {name}, a {style_en} character. "
            f"{description}. "
            f"Cinematic lighting, 8K resolution, professional photography. "
            f"{anti}. "
            f"Chinese: {name}的写实肖像，{description}，{style}风格，{anti_cn}。"
        )
        return self._ok({"prompt": prompt, "style": style}, 90)


class CharacterTraitValidator(AgentTool):
    name = "character_trait_validator"
    description = "验证角色设定的合理性：年龄/身份/外貌是否匹配"
    category = "character"

    async def execute(self, name: str = "", traits: dict = None) -> ToolResult:
        if not traits:
            return self._fail("缺少角色属性")
        issues = []
        age = traits.get("age", 0)
        role = str(traits.get("role", ""))
        appearance = str(traits.get("appearance", ""))

        if isinstance(age, (int, float)):
            if age < 16 and "总裁" in role:
                issues.append("未成年总裁不合理")
        if len(appearance) < 10:
            issues.append("外貌描述过短，建议补充发型、服装、体型")
        if any(w in appearance for w in ["卡通", "动漫", "3D"]):
            issues.append("外貌描述含禁止词(卡通/动漫/3D)")

        score = max(10 - len(issues) * 2, 0)
        suggestions = []
        for issue in issues:
            if "未成年" in issue:
                suggestions.append("[年龄] 未成年总裁→改为25-35岁成熟精英，增加职场背景")
            elif "过短" in issue:
                suggestions.append("[外观] 外貌描述不足→补充：发型(长发/短发/卷发)、服装风格(西装/休闲/连衣裙)、体型(高挑/魁梧/苗条)、面部特征(丹凤眼/薄唇/剑眉)")
            elif "禁止词" in issue:
                suggestions.append("[违规词] 去掉卡通/动漫/3D等词→改为：写实真人、照片级质感、电影人像")
        return self._ok({"issues": issues, "valid": len(issues) == 0}, score * 10, suggestions)
