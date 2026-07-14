"""角色造型智能体：角色建模 + 多套服装设计 + 造型方案"""
import json
import time
import logging
from typing import Optional
from .agent_base_legacy import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

COSTUME_PROMPT = """你是一位金牌影视造型设计师，精通服装史、色彩心理学、面料学与角色造型语言。曾为多部爆款短剧设计标志性造型，深谙"服装即角色"的道理——观众第一眼就能从穿着读懂角色身份、性格与处境。

【专业知识】
1. 色彩心理学（服装配色直接传递角色信息）：
   - 红色：权力/热情/危险（霸总、反派、强势女主）
   - 黑色：神秘/冷酷/权威（总裁、黑化角色、保镖）
   - 白色：纯洁/脆弱/高贵（灰姑娘、白月光、仙侠师尊）
   - 蓝色：冷静/理性/忧郁（学霸、医生、隐忍角色）
   - 粉色：少女/甜蜜/天真（甜宠女主、校园）
   - 金色/暗金：奢华/野心/上位者（豪门、皇室）
   - 灰色：平庸/隐忍/低调（赘婿伪装期、卧底）
   - 渐变/对比色：角色转变期（如从白到红暗示黑化）
2. 面料语言（质感传递阶层与情绪）：
   - 丝绸/缎面：富贵、顺滑、高级感（豪门、宴会）
   - 棉麻：质朴、日常、平民（校园、居家、穷角色）
   - 皮革：硬朗、叛逆、危险（机车、黑道、反派）
   - 雪纺/薄纱：轻盈、梦幻、脆弱（仙侠、少女、回忆）
   - 粗纺呢料：厚重、沉稳、权力（军装、霸总西装）
3. 历史服饰考证（古装/仙侠必须符合朝代）：
   - 唐风：齐胸襦裙、圆领袍、帔帛，色彩浓艳，开放华丽
   - 宋风：褙子、对襟、素雅收敛，理性审美
   - 明风：袄裙、马面裙、立领，端庄规整
   - 仙侠：飘逸广袖、云纹刺绣、渐变纱，脱离具体朝代偏写意
   - 清末民国：旗袍、长衫、中山装，中西交融
4. 造型语言（服装要有"角色记忆点"）：
   - 标志性单品：一个让人记住的配件（霸总的定制袖扣、女主的蝴蝶发夹）
   - 身份反差：伪装期服装刻意低调，身份揭露时换装形成视觉冲击
   - 情绪外化：黑化换深色、觉醒换利落款、失恋换宽松灰暗
5. 连续性：同一角色不同场景服装要在色彩/风格上保持基因统一（如始终带某色系或某配饰）。

返回JSON（不要markdown代码块）：
{
  "outfits": [
    {
      "name": "造型名称（如：戎装/日常装/晚宴装）",
      "scene_type": "适用场景类型（日常/战斗/宴会/户外/居家/宫廷/仙侠）",
      "description": "服装描述，包含面料材质、颜色、款式剪裁、细节工艺（80-150字）",
      "fabric": "面料（如：重磅真丝/粗纺呢/雪纺）",
      "reference_prompt": "用于AI绘图的全量prompt（含角色外貌、服装面料质感、发型、配饰、光线、氛围，英文更佳）",
      "color_scheme": ["主色(含色彩心理学含义)", "辅色", "点缀色"],
      "accessories": ["配饰1（含象征意义）", "配饰2"],
      "hair_style": "发型描述",
      "makeup": "妆容描述（男性角色可省略）",
      "character_signal": "这套造型向观众传递的角色信息（如：暗示隐忍/彰显权力）"
    }
  ],
  "appearance_summary": "角色外貌总览（脸型、五官、身材、标志性特征，用于长期锁定角色形象）",
  "style_notes": "造型风格要点（3-5点，让各场景风格统一）"
}

要求：
- 3套造型必须风格统一（同一角色基因），但适合不同场景且传递不同角色状态
- reference_prompt 要含面料质感词，足够详细可直接用于AI绘图
- 古装/仙侠必须符合朝代特征或仙侠美学规范"""


class CostumeAgent(BaseAgent):
    """角色造型智能体：角色建模、多套服装设计、统一造型方案"""

    name = "角色造型智能体"
    description = "角色建模、多套服装设计、统一造型方案"
    version = "1.0.0"

    def __init__(self, **kwargs):
        super().__init__()
        
    def design(self, char_name: str, role_type: str = "", gender: str = "",
               description: str = "", personality: str = "",
               appearance: str = "", script_context: str = "",
               genre: str = "", **kwargs) -> AgentResult:
        """为角色设计完整造型方案"""
        start = time.time()
        try:
            user_prompt = (
                f"角色名：{char_name}\n"
                f"角色类型：{role_type or '未知'}\n"
                f"性别：{gender or '未知'}\n"
                f"角色描述：{description or personality or '无'}\n"
                f"外貌特征：{appearance or '无'}\n"
                f"故事背景：{script_context[:600] if script_context else '无'}\n"
                f"故事类型：{genre or '未知'}"
            )
            result = self._call_llm_json(COSTUME_PROMPT, user_prompt, temp=0.4, agent_id="costume")
            if result:
                return AgentResult(
                    data={
                        "char_name": char_name,
                        "modeling": result,
                        "outfit_count": len(result.get("outfits", []))
                    },
                    duration_ms=int((time.time() - start) * 1000)
                )
            return AgentResult(success=False, error="LLM返回空结果", duration_ms=int((time.time()-start)*1000))
        except Exception as e:
            logger.error(f"[Costume] design failed for {char_name}: {e}")
            return AgentResult(success=False, error=str(e), duration_ms=int((time.time()-start)*1000))

    def design_batch(self, characters: list, script_context: str = "", genre: str = "") -> AgentResult:
        """批量角色造型设计"""
        start = time.time()
        results = []
        for ch in characters:
            if isinstance(ch, dict):
                name = ch.get("name", "")
                role = ch.get("role", ch.get("role_type", ""))
                gender = ch.get("gender", "")
                desc = ch.get("description", ch.get("personality", ""))
                appearance = ch.get("appearance", "")
            else:
                name = str(ch) if ch else ""
                role = gender = desc = appearance = ""
            if not name:
                continue
            r = self.design(name, role, gender, desc, "", appearance, script_context, genre)
            if r.success:
                results.append(r.data)
        return AgentResult(
            data={"models": results, "total": len(results)},
            duration_ms=int((time.time() - start) * 1000)
        )

    def run(self, action: str = "design", **kwargs) -> AgentResult:
        if "params" in kwargs and isinstance(kwargs["params"], dict):
            params = {**kwargs.pop("params")}
            kwargs.update(params)
        if action in ("design", "design_costume"):
            return self.design(**kwargs)
        elif action == "batch":
            return self.design_batch(
                kwargs.get("characters", kwargs.get("chars", [])),
                kwargs.get("script_context", ""),
                kwargs.get("genre", "")
            )
        elif action == "select_outfit":
            return self.select_outfit(**kwargs)
        return AgentResult(success=False, error=f"未知动作: {action}")

    def select_outfit(self, char_name: str = "", scene_type: str = "",
                      emotion: str = "", action_desc: str = "",
                      existing_outfits: list = None, **kw) -> AgentResult:
        """根据分镜场景/情绪/动作，从已有造型中选最合适的服装"""
        if not existing_outfits:
            return AgentResult(success=True, data={"outfit": {}, "note": "无可用造型"})
        prompt = f"""从角色已有的造型方案中，为当前场景选择最合适的服装。

角色：{char_name}
场景类型：{scene_type}
场景情绪：{emotion}
角色动作：{action_desc}

已有造型方案：
{json.dumps(existing_outfits, ensure_ascii=False, indent=2)}

返回JSON（不要markdown）：
{{
  "selected": "选中造型的名称",
  "reason": "选择理由",
  "adjustments": "相对原造型的微调说明（如颜色变暗、加披风等）"
}}"""
        try:
            result = self._call_llm_json(
                "你是一个服装搭配师，根据场景选最合适的造型。",
                prompt, temp=0.3, agent_id="costume"
            )
            return AgentResult(success=True, data=result)
        except Exception as e:
            logger.warning(f"select_outfit失败: {e}")
            return AgentResult(success=True, data={
                "selected": existing_outfits[0].get("name", "默认") if existing_outfits else "默认",
                "reason": "自动选择默认"
            })