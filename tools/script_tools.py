"""
剧本智能体工具集 (P0)
5个工具: brainstorm_hooks, plot_twist_generator, dialogue_polish,
         tropes_checker, audience_simulator
"""
import json, re
from tools.base import AgentTool, ToolResult


TROPE_KEYWORDS = [
    ("失忆", "角色突然失忆推动剧情"), ("车祸", "用车祸制造冲突"),
    ("替身", "A替B身份或感情"), ("误会", "靠误会延宕剧情超过3次"),
    ("绝症", "角色得绝症煽情"), ("重生", "死后重生开挂"),
    ("总裁", "霸总爱上我"), ("契约", "契约婚姻/恋爱"),
    ("下药", "靠下药推进关系"), ("堕胎", "堕胎情节"),
    ("认亲", "突然发现亲戚关系"), ("失散", "失散多年重逢"),
    ("白月光", "白月光/朱砂痣"), ("黑化", "无条件黑化"),
    ("金手指", "无理由的开挂能力"), ("降智", "角色为剧情强行降智"),
    ("嘴硬", "明明喜欢偏说不"), ("摔倒吻", "摔倒必接吻"),
    ("壁咚", "壁咚/床咚"), ("雨中", "雨中大哭/分手"),
    ("追妻", "追妻火葬场"), ("绿茶", "绿茶/白莲花工具人"),
    ("绑架", "绑架推动剧情"), ("坠崖", "坠崖不死"),
    ("失火", "火灾救人"), ("前女友", "前女友/前男友搅局"),
    ("私生子", "私生子身份"), ("联姻", "商业联姻"),
    ("偷听", "偷听只听到一半"), ("醉酒", "酒后吐真言/乱性"),
    ("出国", "突然出国"), ("N年后", "N年后字幕转场"),
    ("失联", "失联多年"), ("英雄救美", "英雄救美"),
    ("复仇", "复仇剧情"), ("黑道", "黑道/江湖"),
]


class BrainstormHooks(AgentTool):
    name = "brainstorm_hooks"
    description = "批量生成开场钩子（前3秒抓人的开篇），按惊喜度排序"
    category = "creative"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("genre"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "genre": {"type": "string", "description": "题材"},
                "count": {"type": "integer", "description": "生成数量", "default": 10}
            }
        }

    async def execute(self, genre: str, count: int = 10, **kwargs) -> ToolResult:
        try:
            prompt = f"""你是短剧编剧。为"{genre}"题材生成{count}个开场钩子。
每个钩子20-50字，必须在前3秒抓住观众。要求：高冲突、强悬念、反常识。
返回JSON: {{"hooks":[{{"text":"...","type":"冲突/悬念/反常识/情感","surprise_score":1-10}}]}}
只返回JSON，不多说。"""
            raw = self._call_llm(prompt)
            # 兼容 dict 和 str 返回
            if isinstance(raw, dict):
                raw = raw.get("content", raw.get("text", str(raw)))
            raw_str = str(raw) if raw else ""
            # 提取JSON块
            if "```json" in raw_str:
                raw_str = raw_str.split("```json")[-1].split("```")[0]
            elif "```" in raw_str:
                raw_str = raw_str.split("```")[-2] if raw_str.count("```") >= 2 else raw_str.split("```")[-1]
            raw_str = raw_str.strip().strip("`")
            if not raw_str:
                return self._fail(f"LLM返回空内容: {raw_str[:100]}")
            data = json.loads(raw_str)
            hooks = sorted(data.get("hooks", []), key=lambda h: -h.get("surprise_score", 0))
            score = min(100, len(hooks) * 8 + 20)
            return self._ok({"hooks": hooks}, score)
        except Exception as e:
            return self._fail(str(e))


class PlotTwistGenerator(AgentTool):
    name = "plot_twist_generator"
    description = "生成多级反转点，验证逻辑自洽"
    category = "creative"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("current_plot"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "current_plot": {"type": "string", "description": "当前剧情摘要"},
                "count": {"type": "integer", "description": "反转数量", "default": 3}
            }
        }

    async def execute(self, current_plot: str, count: int = 3, **kwargs) -> ToolResult:
        try:
            prompt = f"""你是反转大师。为以下剧情设计{count}个反转点：
剧情：{current_plot[:2000]}
每个反转：1)在第几场 2)反转内容 3)需要的前置伏笔 4)逻辑自洽验证
返回JSON: {{"twists":[{{"at_scene":"...","twist":"...","foreshadowing":"...","logic_check":"..."}}]}}
只返回JSON。"""
            raw = self._call_llm(prompt)
            # 兼容 dict 和 str 返回
            if isinstance(raw, dict):
                raw = raw.get("content", raw.get("text", str(raw)))
            raw_str = str(raw) if raw else ""
            # 提取JSON块
            if "```json" in raw_str:
                raw_str = raw_str.split("```json")[-1].split("```")[0]
            elif "```" in raw_str:
                raw_str = raw_str.split("```")[-2] if raw_str.count("```") >= 2 else raw_str.split("```")[-1]
            raw_str = raw_str.strip().strip("`")
            if not raw_str:
                return self._fail(f"LLM返回空内容: {raw_str[:100]}")
            data = json.loads(raw_str)
            twists = data.get("twists", [])
            score = min(100, len(twists) * 25 + 25)
            return self._ok({"twists": twists}, score)
        except Exception as e:
            return self._fail(str(e))


class DialoguePolish(AgentTool):
    name = "dialogue_polish"
    description = "润色对白——自然度、角色辨识度、节奏感"
    category = "creative"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("dialogue")) and bool(kwargs.get("character"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "dialogue": {"type": "string", "description": "原始对白"},
                "character": {"type": "string", "description": "角色描述"},
                "scene_context": {"type": "string", "description": "场景上下文"}
            }
        }

    async def execute(self, dialogue: str, character: str, scene_context: str = "", **kwargs) -> ToolResult:
        try:
            prompt = f"""润色以下对白。角色设定：{character}
场景：{scene_context or '未指定'}
原始对白：{dialogue}
要求：1)符合角色说话方式 2)口语化自然 3)节奏紧凑 4)保留原意
返回JSON: {{"polished":"...","changes":[{{"original":"...","changed":"...","reason":"..."}}]}}
只返回JSON。"""
            raw = self._call_llm(prompt)
            # 兼容 dict 和 str 返回
            if isinstance(raw, dict):
                raw = raw.get("content", raw.get("text", str(raw)))
            raw_str = str(raw) if raw else ""
            # 提取JSON块
            if "```json" in raw_str:
                raw_str = raw_str.split("```json")[-1].split("```")[0]
            elif "```" in raw_str:
                raw_str = raw_str.split("```")[-2] if raw_str.count("```") >= 2 else raw_str.split("```")[-1]
            raw_str = raw_str.strip().strip("`")
            if not raw_str:
                return self._fail(f"LLM返回空内容: {raw_str[:100]}")
            data = json.loads(raw_str)
            score = 80
            return self._ok(data, score)
        except Exception as e:
            return self._fail(str(e))


class TropesChecker(AgentTool):
    name = "tropes_checker"
    description = "检测陈词滥调/狗血桥段"
    category = "analysis"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("script_text"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "script_text": {"type": "string", "description": "剧本全文"}
            }
        }

    async def execute(self, script_text: str, **kwargs) -> ToolResult:
        found = []
        for keyword, desc in TROPE_KEYWORDS:
            for m in re.finditer(keyword, script_text):
                pos = m.start()
                context = script_text[max(0, pos - 20):pos + len(keyword) + 20]
                found.append({"trope": keyword, "description": desc, "position": pos, "context": context.strip()})
        score = max(0, 100 - len(found) * 5)
        tips = []
        if found:
            for f in found:
                trope = f["trope"]
                desc = f["description"]
                # 每个狗血桥段给出具体优化建议
                fix_hints = {
                    "失忆": "用「选择性遗忘关键信息」替代完全失忆，或让角色故意隐瞒而非失忆",
                    "车祸": "用「意外相遇/突发危机」制造冲突，车祸太廉价",
                    "替身": "改为「性格反差契约关系」—A雇B扮演特定角色但有明确边界",
                    "误会": "减少误会次数，改为「信息不对称+主动试探」更有张力",
                    "绝症": "用「慢性病/心理创伤」替代绝症，更真实且可持续",
                    "重生": "改为「大难不死后的性格转变」而非真的重生",
                    "总裁": "给总裁加上「具体行业+独特癖好」去模板化",
                    "契约": "改为「利益捆绑的暂时联盟」而非契约婚姻",
                    "下药": "禁止使用，用「酒后真心话/深夜谈心」替代",
                    "堕胎": "敏感题材，建议替换为「误会产生的重大决定」",
                    "认亲": "提前埋隐藏线索，让认亲有伏笔回收感",
                    "白月光": "白月光要有独立人格，不只是工具人",
                    "黑化": "黑化需有渐进过程，给出3步变化阶梯",
                    "金手指": "给能力加限制条件或代价，避免无脑爽",
                    "降智": "降智段需重写——让角色有合理动机做错误选择",
                    "壁咚": "改为更克制的身体语言——靠近但不侵犯",
                    "雨中": "雨中戏保留但减少频率，只在情感高潮用1次",
                    "追妻": "追妻需有实质性成长，不能只靠死缠烂打",
                    "绿茶": "反派要有动机和层次，不是纯工具人",
                }
                hint = fix_hints.get(trope, f"建议创新处理「{trope}」—{desc}")
                tips.append(f"[{trope}] {hint}")
        else:
            tips = ["未发现常见狗血桥段"]
        return self._ok({"tropes_found": found, "count": len(found)}, score, tips)


class AudienceSimulator(AgentTool):
    name = "audience_simulator"
    description = "模拟5种观众人设，逐段标注反应，找出弃剧风险点"
    category = "analysis"

    PERSONAS = [
        {"type": "爽剧党", "prefers": "快节奏/反转/打脸", "drops": "拖沓/说教"},
        {"type": "情感党", "prefers": "虐恋/甜宠/深情", "drops": "冷漠/机械"},
        {"type": "逻辑党", "prefers": "严谨/合理/伏笔", "drops": "漏洞/降智"},
        {"type": "颜值党", "prefers": "好看/氛围/画面", "drops": "粗糙/出戏"},
        {"type": "路人党", "prefers": "轻松/有趣/新颖", "drops": "老套/无聊"},
    ]

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("script_text"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "script_text": {"type": "string", "description": "剧本全文"}
            }
        }

    async def execute(self, script_text: str, **kwargs) -> ToolResult:
        try:
            personas_desc = "\n".join([f"- {p['type']}（喜欢{p['prefers']}，弃剧原因：{p['drops']}）" for p in self.PERSONAS])
            prompt = f"""你是观众调研专家。模拟以下5种观众阅读短剧剧本：
{personas_desc}
剧本：{script_text[:3000]}
为每种观众给出：1)整体代入感(1-10) 2)最可能弃剧的位置和原因 3)最喜欢的段落
返回JSON: {{"results":[{{"persona":"...","engagement":5,"drop_point":"第X场...","drop_reason":"...","favorite_part":"..."}}]}}
只返回JSON。"""
            raw = self._call_llm(prompt)
            # 兼容 dict 和 str 返回
            if isinstance(raw, dict):
                raw = raw.get("content", raw.get("text", str(raw)))
            raw_str = str(raw) if raw else ""
            # 提取JSON块
            if "```json" in raw_str:
                raw_str = raw_str.split("```json")[-1].split("```")[0]
            elif "```" in raw_str:
                raw_str = raw_str.split("```")[-2] if raw_str.count("```") >= 2 else raw_str.split("```")[-1]
            raw_str = raw_str.strip().strip("`")
            if not raw_str:
                return self._fail(f"LLM返回空内容: {raw_str[:100]}")
            data = json.loads(raw_str)
            results = data.get("results", [])
            drop_count = sum(1 for r in results if r.get("drop_point"))
            score = max(0, 100 - drop_count * 15)
            tips = []
            drop_fixes = {
                "拖沓": "删除铺垫性对话，直接从冲突场景切入；每场戏控制在3-5句对话内",
                "说教": "把道理改成角色行动——让角色用选择而非语言表达价值观",
                "冷漠": "给角色加微表情/小动作——摸戒指、叹气、眼神闪躲",
                "机械": "打破对话节奏——用打断、抢话、沉默制造真实感",
                "漏洞": "补逻辑：这段的因果链是否完整？动机→行动→结果",
                "降智": "给角色加一个合理动机——ta为什么必须这样做？",
                "粗糙": "加1-2句环境/氛围描写——光线、温度、气味",
                "出戏": "检查角色语言是否符合人设——总裁不说网络用语",
                "老套": "加一个意料之外的转折——观众以为A，实际发生B",
                "无聊": "提高冲突密度——每场戏必须有明确对抗（人物vs人物/人物vs环境/人物vs自己）",
                "套路": "打破套路——在套路后加一个反套路反转",
                "平淡": "加情绪起伏——每3场戏需要一个情绪变化（升/降）",
            }
            for r in results:
                if r.get("drop_point"):
                    reason = r.get("drop_reason", "")
                    persona = r["persona"]
                    fix = ""
                    for kw, hint in drop_fixes.items():
                        if kw in reason:
                            fix = hint
                            break
                    if fix:
                        tips.append(f"[{persona}] {r['drop_point']}，原因：{reason}。修复：{fix}")
                    else:
                        tips.append(f"[{persona}] {r['drop_point']}，原因：{reason}。建议重写该段增强冲突")
            return self._ok({"results": results, "drop_risk": drop_count}, score, tips)
        except Exception as e:
            return self._fail(str(e))
