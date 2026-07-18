# -*- coding: utf-8 -*-
"""
EpisodeWriter — 古风短剧分集写作智能体

遵循规范：
  一、固定全局基底（一次性注入上下文）
  二、每集动态输入
  三、先框架后正文
  四、强制输出标注

下游：pipeline.py / 人工写作流程
"""
import json, logging, re
from typing import Optional

from services.model_client import UnifiedModel
from core.safety_filter import clean_text

logger = logging.getLogger(__name__)

# ============================================================
# 一、固定全局基底
# ============================================================

PROJECT_BASE = """
【项目基础】
- 剧集定位：竖屏 2 分钟微短剧 / 古风架空军旅悲情 / 总 80 集
- 字数标准：单集 440-520 字（含镜头描写+对白+留白）
- 输出规范：先输出 4 项剧情框架，人工确认后再写完整剧本
- 文风标准：电影级镜头描写、克制古风短句，台词内敛不口水
- 写作哲学：作品必须有深度有内容
  - 自然融入成语、名言、诗词、易经八卦等中华文化资源
  - 不直接掉书袋，用典于无形
  - 每集至少有一处可回味的金句或意境
""".strip()

CHARACTER_CARDS = """
【人物卡 · 锁死不改变】
陆惊珩（男·主角）：
  - 身份：少年玄甲战神，北境玄甲军统帅
  - 外形：清峭劲瘦骨相，轻玄甲覆征尘，身形挺拔锋利，眉目英锐含锋芒
  - 性格：外显桀骜，沙场锐气逼人；内里背负家国重担，隐忍愧疚深藏于心
  - 声线：低沉克制，语速偏慢，情绪波动时更沉
  - 说话风格：话少，字字有分量，不轻易许诺但绝不食言
  - 核心矛盾：忠义难两全——守国则负卿，守卿则负国
  - 关键词：桀骜 / 隐忍 / 愧疚 / 孤勇

江月泠（女·主角）：
  - 身份：白衣素纱，常年居断崖边独居
  - 外形：容貌清润清冷，素衣如谪仙，身姿纤弱却立如松
  - 性格：温柔有风骨，泪不喧哗，等候的执念深如磐石
  - 声线：轻柔，似山间泉音，悲痛时更轻更淡
  - 说话风格：话不多但句句真心，从不质问只陈述，悲时含笑
  - 核心设定：她在等一个人回来，不问归期
  - 关键词：清冷 / 执念 / 温柔 / 风骨

【补充约束】
- 两人均为凡人，无超自然能力
- 禁止给角色添加金手指、前世记忆、天选之人等设定
""".strip()

WORLD_RULES = """
【架空世界观 · 不可使用真实元素】
- 无真实朝代名称（禁：唐/宋/明/清等）
- 无真实地名（禁：长安/洛阳/汴京/临安等）
- 无真实历史将领（禁：岳飞/霍去病/卫青/李靖等）
- 无真实战役名称（禁：赤壁/垓下/郾城等）
- 战场统称：北境荒原、边关防线、玄甲关
- 核心城关：玄甲关（边境要塞）、临渊城（后方重镇）
- 地名可造：苍北、寒渊渡、落雁坡、孤云岭
- 战争核心目的：守百姓、护边关，不是复仇杀戮
- 军事体系：架空军职（玄甲将军/督军/校尉），不套用真实官制
""".strip()

COMPLIANCE_BLACKLIST = """
【合规红线 · 以下内容严格禁止】
一、禁真实历史人物/事件
  - 不得出现真实历史人物
  - 不得使用正史战争名称，不得影射真实历史事件

二、禁暴力画面
  - 不得描写伤口特写、流血画面、尸骸、断肢
  - 战场表现方式：远景大全景、旌旗、烟尘、马蹄声、兵器碰撞声
  - 死亡用落幕/远行/长眠替代，不直接描写死亡过程

三、禁敏感剧情
  - 禁殉情、禁轻生念头和行为
  - 禁灭门、禁私刑、禁血腥复仇
  - 禁强制情爱、禁雌竞、禁三角恋
  - 禁玄幻/鬼神/修仙/转世/重生元素
  - 禁宿命/天意/劫数等宿命论叙事

四、价值观底线
  - 家国与情爱不极端对立——保家卫国的同时可以思念
  - 双向坚守：不是单方面牺牲
  - 结局留有希望，不写彻底的悲剧收尾
""".strip()

WRITING_PRINCIPLES = """
【写作深度与文化原则 · 永久生效】

一、深度要求
- 每集至少有 1-2 处可回味的表达：诗词化用、典故嵌入、哲思金句
- 台词有出处感，让人觉得是有底子的话
- 情绪不直接说出来，用动作、景物、典故烘托
- 战争的底色有悲悯感，不让观众觉得"打仗就是砍人"

二、文化资源使用（自由使用，不限量）
- 成语、谚语、俗语——中华民族公共文化资源，任意使用
- 名言警句（如君子厚德载物、知我者谓我心忧等）
- 诗词名句可化用可引用，不抄袭整首
- 易经八卦（乾/坤/坎/离等卦象卦辞）自然融入场景或台词
- 阴阳五行观念可作为人物信念或世界观底色
- 兵法典籍（孙子兵法、三十六计）的战术思想可引用
- 以上全部用于润色而非说教，不刻意堆砌

三、历史借鉴原则
- 可以借鉴真实历史中的场景片段、战场氛围、战术逻辑
  - 例：借垓下的执手诀别氛围（不写项羽虞姬）
  - 例：借李陵以少敌众的绝境（不写李陵本人）
  - 例：借漠北之战的荒原苍茫感（不写具体战役）
- 严禁：真实历史人名、地名、朝代名
- 严禁：照搬真实历史事件完整经过
- 借鉴的是味道和质感，不是剧情本身

四、战场写实
- 古战场战术必须真实可用，不虚构假桥段（如烧旗敌军就崩了）
- 常见真实战法：断粮道、疲敌、夜袭、诱敌深入、离间计
- 战争残酷面真实展现：缺粮、减员、以少敌多的压迫感
- 不用血腥特写，通过远景/声响/人物表情传递

五、爽感来源（非戏说，是真本事）
- 将帅用兵的从容感——敌人越多他越冷静
- 以少胜多的智谋快感——观众能看懂每一步
- 绝境中仍不乱的强者气度
- 女主不在战场场景中出现，通过书信/信物暗线传递存在感
""".strip()

FOUR_ACT_STRUCTURE = """
【四段式剧本结构 · 每集严格遵守】
总时长 2 分钟（120 秒），按以下四段分配：

(1) 开篇 / 0-27 秒（约 80-100 字）
  - 远景环境空镜，建立本集场景氛围
  - 人物外形/神态/站位铺垫
  - 无对白或极简旁白
  - 目的：让观众一眼进入情绪

(2) 中段 / 27 秒-1 分 20 秒（约 180-220 字）
  - 动作细节描写，人物交互
  - 核心对话推进标题主线
  - 本集主要信息量集中在此段
  - 对白与画面交替，节奏均匀

(3) 高潮 / 1 分 20 秒-1 分 40 秒（约 100-120 字）
  - 内心两难情绪爆发点
  - 隐忍落泪、内心拉扯、沉默胜过千言
  - 镜头推向人物微表情（眼部特写、手部特写）
  - 台词最少化，画面承载情绪

(4) 结尾 / 1 分 40 秒-2 分（约 80-100 字）
  - 氛围感定格画面
  - 情绪收束，不把话说完
  - 预埋下集钩子（一个动作、一个眼神、一句话头）
  - 留白，给观众回味的空间
""".strip()

GLOBAL_BASE = "\n\n".join([
    "【以下为固定全局设定，每集通用，严格执行】",
    PROJECT_BASE,
    CHARACTER_CARDS,
    WORLD_RULES,
    COMPLIANCE_BLACKLIST,
    WRITING_PRINCIPLES,
    FOUR_ACT_STRUCTURE,
])


# ============================================================
# 三、框架提示词
# ============================================================

FRAMEWORK_SYSTEM = f"""{GLOBAL_BASE}

【当前任务：输出本集剧情框架】
你是一位古风短剧编剧。收到本集动态输入后，请先输出以下四项框架内容，不要写剧本正文：

输出格式（JSON）：
{{
  "framework": {{
    "剧情核心": "一句话紧扣人工标题，不超过 30 字",
    "冲突转折": "本集单集内的起伏、矛盾点，50-80 字",
    "人物情绪": {{
      "陆惊珩": "本集中他的情绪变化线，30-50 字",
      "江月泠": "本集中她的情绪变化线，30-50 字"
    }},
    "下集悬念": "本集结尾引向下集的钩子，30-50 字"
  }},
  "compliance_check": "pass/fail，如有违规说明原因"
}}

注意：
1. 框架必须紧扣人工分集标题，不偏离
2. 不写任何剧本正文
3. 框架通过后方可写完整剧本
"""


# ============================================================
# 四、完整剧本提示词
# ============================================================

FULL_SCRIPT_SYSTEM = f"""{GLOBAL_BASE}

【当前任务：输出完整分镜剧本】
以下已确认的本集剧情框架，请据此写出完整剧本。

输出格式（JSON）：
{{
  "script": {{
    "title": "本集标题",
    "phase": "所属主线阶段",
    "tone": "情绪基调",
    "total_words": 总字数,
    "total_seconds": 120,
    "acts": [
      {{
        "act": 1,
        "name": "开篇",
        "time_range": "0-27s",
        "words": "本段字数",
        "content": [
          {{"tag": "环境镜头", "text": "画面描写内容"}},
          {{"tag": "人物外形动作", "text": "角色动作/神态描写"}}
        ]
      }},
      {{
        "act": 2,
        "name": "中段",
        "time_range": "27s-1min20s",
        "words": "本段字数",
        "content": [
          {{"tag": "环境镜头", "text": "..."}},
          {{"tag": "人物外形动作", "text": "..."}},
          {{"tag": "人物台词", "text": "角色：对白内容", "speaker": "角色名"}},
          {{"tag": "内心情绪", "text": "角色内心活动/情绪描写"}}
        ]
      }},
      {{
        "act": 3,
        "name": "高潮",
        "time_range": "1min20s-1min40s",
        "words": "本段字数",
        "content": [...]
      }},
      {{
        "act": 4,
        "name": "结尾",
        "time_range": "1min40s-2min",
        "words": "本段字数",
        "content": [...]
      }}
    ]
  }},
  "copyright_note": "本集标题由人工独立拟定，AI 仅依据人工框架扩写文字内容",
  "compliance_check": "pass"
}}

关键要求：
1. 严格按照四段式结构分配时长和字数
2. 每段 content 必须带 tag：【环境镜头】【人物外形动作】【人物台词】【内心情绪】
3. 台词标注说话角色名
4. 总字数严格控制在 440-520 字
5. 文风：电影级镜头描写、克制古风短句，不水词
6. 遵守合规红线，违者标 compliance_check=fail
7. 下集钩子自然融入第四段结尾
8. 遵循写作深度原则：每集至少一处可回味的表达
"""


# ============================================================
# 输出格式示例
# ============================================================

EXAMPLE_FRAMEWORK = """
【示例 / 框架输出】
人工分集标题：残阳断崖，执手诀别
本集主线阶段：开篇离别线
本集剧情边界：仅写将军出征前崖边道别，不插入战场厮杀内容
本集情绪基调：不舍愧疚，温柔隐忍

输出：
{
  "framework": {
    "剧情核心": "出征前夜，陆惊珩与江月泠断崖执手诀别",
    "冲突转折": "他欲言又止的承诺与她含笑的沉默——两人都知此行凶险，却都不说破",
    "人物情绪": {
      "陆惊珩": "从强作镇定到喉头哽咽，铠甲下的手微微颤抖",
      "江月泠": "从温柔浅笑到泪悬于睫，始终未让泪落下"
    },
    "下集悬念": "他翻身上马时回头一望，她立在崖边未动，残阳如血"
  },
  "compliance_check": "pass"
}
""".strip()

EXAMPLE_FULL_SCRIPT = """
【示例 / 完整剧本片段】
{
  "script": {
    "title": "残阳断崖，执手诀别",
    "phase": "开篇离别线",
    "tone": "不舍愧疚，温柔隐忍",
    "total_words": 470,
    "total_seconds": 120,
    "acts": [
      {
        "act": 1,
        "name": "开篇",
        "time_range": "0-27s",
        "words": 90,
        "content": [
          {"tag": "环境镜头", "text": "残阳如血，铺满整片北境荒原。断崖之上，孤松迎风，远处玄甲关旌旗猎猎。"},
          {"tag": "人物外形动作", "text": "江月泠白衣素纱立于崖边，风卷衣袂猎猎作响。她望向关隘方向，眸中平静如水。"}
        ]
      },
      {
        "act": 2,
        "name": "中段",
        "time_range": "27s-1min20s",
        "words": 200,
        "content": [
          {"tag": "人物外形动作", "text": "身后传来沉重的脚步声。玄甲铿锵，陆惊珩缓步上前，在她身侧三步处停住。"},
          {"tag": "人物台词", "text": "陆惊珩：明日一早，我便动身了。", "speaker": "陆惊珩"},
          {"tag": "人物外形动作", "text": "他没有看她，目光投向远方关隘。"},
          {"tag": "人物台词", "text": "江月泠：我知道。", "speaker": "江月泠"},
          {"tag": "内心情绪", "text": "她声音轻柔如常，却让陆惊珩喉头一紧。她什么都懂，只是不问。"},
          {"tag": "人物台词", "text": "陆惊珩：等我回来。", "speaker": "陆惊珩"}
        ]
      }
    ]
  },
  "copyright_note": "本集标题由人工独立拟定，AI 仅依据人工框架扩写文字内容",
  "compliance_check": "pass"
}
""".strip()


# ============================================================
# EpisodeWriter — 分集写作智能体
# ============================================================

class EpisodeWriter:
    """古风短剧分集写作智能体"""

    def __init__(self):
        self.model = "deepseek-v4-flash"
        self.fallback_model = "deepseek-reasoner"
        self.max_retries = 2
        self.timeout = 180

    # -- 通用 --

    def _call_llm(self, system: str, prompt: str, max_tokens: int = 8192) -> Optional[dict]:
        """安全调用 LLM，含重试"""
        models = [self.model, self.fallback_model]
        for attempt in range(self.max_retries + 1):
            for model in models:
                try:
                    result = UnifiedModel.llm(
                        prompt=prompt,
                        system=system,
                        model=model,
                        max_tokens=max_tokens,
                        timeout=self.timeout,
                    )
                    text = result.get("text", "")
                    if not text:
                        continue
                    return self._extract_json(text)
                except Exception as e:
                    logger.warning(f"[EpisodeWriter] {model} attempt {attempt + 1} failed: {e}")
                    continue
        return None

    def _extract_json(self, text: str) -> Optional[dict]:
        """解析 LLM 输出中的 JSON"""
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r'```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        m = re.search(r'(\{.*\})', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _build_episode_prompt(self, episode_input: dict) -> str:
        """构建每集动态输入"""
        parts = ["【本集动态输入】"]
        parts.append(f"人工分集标题：{episode_input.get('title', '')}")
        parts.append(f"本集主线阶段：{episode_input.get('phase', '')}")
        parts.append(f"本集剧情边界：{episode_input.get('boundary', '')}")
        parts.append(f"本集情绪基调：{episode_input.get('tone', '')}")
        prev = episode_input.get('previous_hook', '')
        if prev:
            parts.append(f"上集结尾伏笔（需承接）：{prev}")
        return "\n".join(parts)

    # -- 第一步：输出框架 --


    def _clean_result(self, result: dict) -> dict:
        """递归清洗结果中的文本字段"""
        if not isinstance(result, dict):
            result = self._clean_result(result)
        return result
        for k, v in result.items():
            if isinstance(v, str):
                result[k] = clean_text(v)
            elif isinstance(v, dict):
                result[k] = self._clean_result(v)
            elif isinstance(v, list):
                result[k] = [self._clean_result(item) if isinstance(item, dict) else clean_text(item) if isinstance(item, str) else item for item in v]
        result = self._clean_result(result)
        return result

    def write_framework(self, episode_input: dict) -> dict:
        """输出四段剧情框架。返回 {"success": bool, "framework": dict, "error": str}"""
        prompt = self._build_episode_prompt(episode_input)
        prompt += "\n\n请输出本集的四段剧情框架（JSON格式），不要写剧本正文。"
        prompt += f"\n\n参考示例框架格式：\n{EXAMPLE_FRAMEWORK}"

        result = self._call_llm(FRAMEWORK_SYSTEM, prompt, max_tokens=4096)
        if not result:
            return {"success": False, "error": "框架生成失败：模型无返回"}

        framework = result.get("framework", result)
        compliance = result.get("compliance_check", "pass")

        if compliance == "fail":
            return {
                "success": False,
                "error": f"框架违规：{result.get('reason', '未知违规')}",
                "framework": framework,
                "compliance_check": "fail"
            }

        return {
            "success": True,
            "framework": framework,
            "compliance_check": "pass",
            "raw": result,
        }

    # -- 第二步：输出完整剧本 --

    def write_full_script(self, episode_input: dict, framework: dict = None) -> dict:
        """输出完整分镜剧本。返回 {"success": bool, "script": dict, "error": str}"""
        prompt = self._build_episode_prompt(episode_input)

        if framework:
            prompt += f"\n\n【已确认的剧情框架】\n{json.dumps(framework, ensure_ascii=False, indent=2)}"
        else:
            prompt += "\n\n（无预先确认的框架，请直接输出完整剧本）"

        prompt += "\n\n请输出完整分镜剧本（JSON格式）。"
        prompt += f"\n\n参考示例剧本格式：\n{EXAMPLE_FULL_SCRIPT}"

        result = self._call_llm(FULL_SCRIPT_SYSTEM, prompt, max_tokens=12288)
        if not result:
            return {"success": False, "error": "剧本生成失败：模型无返回"}

        script = result.get("script", result)
        compliance = result.get("compliance_check", "pass")

        total_words = 0
        acts = script.get("acts", []) if isinstance(script, dict) else []
        for act in acts:
            for item in act.get("content", []):
                total_words += len(item.get("text", ""))

        result_data = {
            "success": True,
            "script": script,
            "total_words": total_words,
            "compliance_check": compliance,
            "copyright_note": result.get("copyright_note", "本集标题由人工独立拟定，AI 仅依据人工框架扩写文字内容"),
            "raw": result,
        }

        if total_words < 400 or total_words > 600:
            logger.warning(f"[EpisodeWriter] 字数异常: {total_words}（标准 440-520）")

        result = self._clean_result(result)
        result = self._clean_result(result)
        return result_data

    # -- 一键生成 --

    def write_auto(self, episode_input: dict) -> dict:
        """自动化管线调用：先框架再剧本（省略人工确认）"""
        fw_result = self.write_framework(episode_input)
        if not fw_result.get("success"):
            return fw_result
        script_result = self.write_full_script(episode_input, fw_result.get("framework"))
        script_result["framework"] = fw_result.get("framework")
        return script_result


# 单例
episode_writer = EpisodeWriter()
