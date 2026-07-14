"""智能体：特效师 — 特效方案设计 + 后期视觉效果指导"""
import json
import time
import logging
from typing import Dict, List, Optional
from .agent_base_legacy import BaseAgent, AgentResult
from services.film_refs import get_genre_film_ref

logger = logging.getLogger(__name__)

SFX_PROMPT = """你是一位影视后期特效指导（VFX Supervisor）。你的任务：为短剧分镜设计特效方案和视觉效果。

# 特效类型

## 动作特效
| 特效 | 描述 | 适用场景 |
|------|------|----------|
| 刀光剑影 | 武器轨迹光效 | 打斗、武术 |
| 冲击波 | 能量爆发扩散 | 对决、法术 |
| 速度线 | 运动轨迹模糊 | 快速移动、追逐 |
| 残影 | 快速移动留下的影像 | 轻功、瞬移 |
| 粒子 | 火花、碎片、灰尘 | 爆炸、破坏 |

## 法术/仙侠特效
| 特效 | 描述 | 适用场景 |
|------|------|----------|
| 灵气环绕 | 角色周身光晕 | 修仙、出场 |
| 符文化形 | 符文浮现变法术 | 施法 |
| 元素特效 | 火/水/风/雷/冰 | 法术攻击 |
| 结界 | 能量屏障 | 防御、封印 |
| 飞升 | 光柱冲天 | 突破、成仙 |

## 氛围特效
| 特效 | 描述 | 适用场景 |
|------|------|----------|
| 雨/雪/雾 | 天气粒子 | 氛围渲染 |
| 烟尘 | 烟雾、灰尘 | 战场、废弃 |
| 光晕/镜头光斑 | 强光散射 | 回忆、神圣感 |
| 暗角 | 画面边缘变暗 | 压抑、聚焦 |

## 转场特效
| 特效 | 描述 | 适用场景 |
|------|------|----------|
| 闪白 | 白屏过渡 | 回忆/梦境进入 |
| 叠化 | 画面叠加过渡 | 时间流逝 |
| 碎裂 | 画面碎裂转场 | 冲击、破碎 |
| 旋转缩放 | 旋转+缩放过渡 | 动感转场 |

## 调色/LUT
| 风格 | 色调 | 适用场景 |
|------|------|----------|
| 暖金 | 橙+金 | 古装正剧、温馨 |
| 冷蓝 | 蓝+灰 | 悬疑、夜晚、悲伤 |
| 高饱和 | 色彩浓烈 | 仙侠、奇幻 |
| 低饱和 | 接近黑白 | 压抑、严肃 |
| 青橙 | Teal & Orange | 都市、现代 |
| 胶片 | 颗粒+褪色 | 怀旧、年代 |

# 分析规则

对每个镜头：
1. 判断是否需要特效：纯对话一般不插特效，动作/法术/氛围镜头按需配置
2. 特效强度分级：轻(1-3) 中(4-6) 重(7-10)
3. 特效要有叙事目的，不要为炫而炫

返回纯JSON（无markdown代码块）：
{
  "effects_per_shot": [
    {
      "shot_num": 1,
      "needs_sfx": false,
      "reason": "纯对话镜头，不需要特效",
      "color_grade": "暖金"
    },
    {
      "shot_num": 2,
      "needs_sfx": true,
      "action_effects": ["刀光剑影", "冲击波"],
      "atmosphere_effects": ["烟尘"],
      "transition_effect": "闪白",
      "intensity": 8,
      "color_grade": "冷蓝",
      "reason": "打斗高潮，需要强烈的武器光效和冲击波"
    }
  ],
  "overall_color_palette": "暖金基调，高潮场景切冷蓝",
  "vfx_notes": "特效风格指导建议"
}
"""


class SFXAgent(BaseAgent):
    """特效师：为每个镜头设计特效方案、转场效果、调色指导"""

    name = "特效师"
    description = "设计特效方案、转场效果、调色指导，为后期合成提供视觉特效蓝图"
    version = "1.0.0"

    def design_effects(self, shots: List[dict] = None, scene_images: List[str] = None,
                       genre: str = "", script: str = "", **kwargs) -> AgentResult:
        shots = shots or []
        """为分镜设计特效方案"""
        start = time.time()
        try:
            if not shots:
                return AgentResult(success=False, error="无分镜数据", duration_ms=0)

            # 分析哪些镜头需要特效
            has_action = False
            has_magic = False
            for s in shots:
                desc = s.get("description", s.get("desc", ""))
                if any(w in desc for w in ["打", "战", "攻", "杀", "击", "拳", "剑", "刀", "枪"]):
                    has_action = True
                if any(w in desc for w in ["法", "灵", "仙", "魔", "妖", "咒", "阵", "飞", "光"]):
                    has_magic = True

            # 构建镜头信息
            shots_info = []
            for s in shots:
                info = {
                    "shot_num": s.get("shot_num", s.get("id", "")),
                    "description": s.get("description", s.get("desc", "")),
                    "emotion": s.get("emotion", ""),
                    "importance": s.get("importance", "medium"),
                    "has_action": has_action,
                    "has_magic": has_magic,
                }
                shots_info.append(info)

            shots_json = json.dumps(shots_info, ensure_ascii=False, indent=2)
            genre_str = f"题材：{genre}" if genre else ""

            film_ref = get_genre_film_ref(genre, aspect="sfx")
            _sfx_script = kwargs.get("script_text", "") or script or ""
            script_ctx = "\n\n【剧情上下文】（特效服务剧本氛围）：\n" + _sfx_script[:500] if _sfx_script else ""
            user_prompt = f"""{film_ref}{genre_str}
检测到动作戏：{'是' if has_action else '否'}
检测到法术/仙侠元素：{'是' if has_magic else '否'}{script_ctx}

分镜数据：
{shots_json}

请逐镜头判断是否需要特效，并设计：动作特效/氛围特效/转场特效/调色方案。
注意：纯对话镜头不需要堆特效。特效服务于叙事，不是炫技。"""

            result = self._call_llm_json(SFX_PROMPT, user_prompt, retries=1)

            if isinstance(result, dict):
                effects = result.get("effects_per_shot", [])
                overall_palette = result.get("overall_color_palette", "")
                vfx_notes = result.get("vfx_notes", "")

                # 合并回原始 shots
                merged_shots = []
                for i, s in enumerate(shots):
                    s_copy = dict(s)
                    if i < len(effects):
                        efx = effects[i]
                        s_copy["needs_sfx"] = efx.get("needs_sfx", False)
                        s_copy["action_effects"] = efx.get("action_effects", [])
                        s_copy["atmosphere_effects"] = efx.get("atmosphere_effects", [])
                        s_copy["transition_effect"] = efx.get("transition_effect", "")
                        s_copy["sfx_intensity"] = efx.get("intensity", 0)
                        s_copy["color_grade"] = efx.get("color_grade", "")
                        s_copy["sfx_reason"] = efx.get("reason", "")
                    merged_shots.append(s_copy)

                elapsed = int((time.time() - start) * 1000)
                sfx_count = sum(1 for s in merged_shots if s.get("needs_sfx"))
                logger.info(f"[SFX] Designed effects for {sfx_count}/{len(merged_shots)} shots in {elapsed}ms")
                return AgentResult(
                    success=True,
                    data={
                        "shots": merged_shots,
                        "overall_color_palette": overall_palette,
                        "vfx_notes": vfx_notes,
                        "sfx_shot_count": sfx_count
                    },
                    duration_ms=elapsed
                )

            return AgentResult(success=False, error="特效方案生成失败", duration_ms=0)

        except Exception as e:
            logger.error(f"[SFX] failed: {e}")
            return AgentResult(success=False, error=str(e),
                               duration_ms=int((time.time() - start) * 1000))

    def run(self, action: str = "design", **kwargs) -> AgentResult:
        return self.execute(action, **kwargs)

    def execute(self, action: str = "design", **kwargs) -> AgentResult:
        """统一入口"""
        action_map = {
            "design": self.design_effects,
            "design_effects": self.design_effects,
        }
        if action in action_map:
            return action_map[action](**kwargs)
        if "shots" in kwargs:
            return self.design_effects(**kwargs)
        return AgentResult(success=False, error=f"未知 action: {action}")
