"""智能体：摄影指导 — 为每个镜头配置运镜/角度/景别/光线/转场 + 相邻两镜衔接"""
import json
import time
import logging
from typing import Dict, List, Optional
from .agent_base_legacy import BaseAgent, AgentResult
from services.film_refs import get_genre_film_ref

logger = logging.getLogger(__name__)

CINEMATOGRAPHER_PROMPT = """

【铁律】手上有完整剧本。所有角色、场景、道具、台词必须来自剧本原文。剧本没写的不要编造。不确定时回看剧本。
你是一位短剧摄影指导。两个任务：
1. 为每个镜头配置运镜/角度/景别/光线
2. ⚠️ 确保上下两镜衔接平滑，不让观众跳戏（最重要！）

# 运镜字典
固定=稳定客观，推=引导注意力/揭示，拉=展示空间/收束，摇=横向扫视，移=纵向扫视，跟=与主体同步移动，升降=大范围视角，环绕=绕主体旋转

# 景别字典（按画面范围从小到大）
大特写 < 特写 < 近景 < 中景 < 全景 < 远景

# 角度字典
平视=客观平等，仰视=威严压迫，俯视=渺小孤独，倾斜=混乱不安，鸟瞰=全知疏离

# 光线字典
柔光=温馨浪漫，硬光=锐利对峙，逆光=神圣梦幻，侧光=戏剧立体，暗调=神秘压抑，自然光=真实日常，暖光=温暖怀旧，冷光=疏离紧张

# ⚠️ 上下镜头衔接规则（每镜必检）

## 景别衔接
❌ 远景→特写 = 跳级！观众不知道中间发生了什么 → 必须插中景过渡
❌ 连续4个同景别 = 死板 → 至少每3镜变一次
✅ 远景→全景→中景 = 逐级推进
✅ 中景→近景→中景 = 对话正反打

## 运镜衔接
❌ 左摇→右摇 = 反向，观众晕 → 中间插 固定 缓冲
❌ 快推→快推 = 连续加速窒息
✅ 固定→推→固定 = 推镜后回归稳定

## 角度衔接
❌ 仰→俯→仰 = 频繁切换头晕
❌ 平→平→平 = 单调
✅ 平→略仰→仰 = 权力感递增

## 光线衔接
❌ 暖→冷→暖 = 光源忽变虚假
✅ 同场景光线一致，情绪渐变才变光

# 流程

对每个镜头 N：

1. 读 description + dialogue + emotion → 理解内容
2. 看前镜 N-1 的景别/角度/光线 → 本镜需平滑承接
3. 看后镜 N+1 的内容 → 本镜需铺垫
4. 确定本镜 visual 参数

transition（从前镜过来的方式）：
- 同场景/秒级 → 切入
- 几分钟后 → 叠化
- 大时间跳跃 → 淡出淡入
- 情绪爆发后 → 闪白
- 回忆进入 → 叠化+柔光

flow_notes（必填）：
"承接Shot{N-1}的{前镜景别}/{前镜角度}，用{运镜}过渡至本镜{本镜景别}/{本镜角度}，衔接自然；为Shot{N+1}的{后镜内容}做铺垫"

返回纯JSON：
{
  "shots": [
    {
      "shot_num": 1,
      "camera_movement": "固定",
      "camera_angle": "平视",
      "shot_type": "全景",
      "lighting": "自然光",
      "transition": "切入",
      "flow_notes": "首镜用全景定场，固定+平视让观众建立空间感；为Shot2的中景引入主角做铺垫",
      "rationale": "开篇定场，建立空间关系"
    }
  ],
  "overall_style": "全片视觉风格"
}
"""


class CinematographerAgent(BaseAgent):
    """摄影指导：为每个镜头配置运镜/角度/景别/光线/转场 + 衔接检查"""

    name = "摄影指导"
    description = "根据分镜内容，为每个镜头配置最佳运镜、角度、景别、光线和转场，确保上下衔接平滑"
    version = "1.1.0"

    def design_shots(self, shots: List[dict] = None, script: str = "",
                     genre: str = "", director_beats: str = "", **kwargs) -> AgentResult:
        shots = shots or []
        start = time.time()
        try:
            if not shots:
                return AgentResult(success=False, error="无分镜数据", duration_ms=0)

            shots_info = []
            for s in shots:
                info = {
                    "shot_num": s.get("shot_num", s.get("id", "")),
                    "description": s.get("description", s.get("desc", "")),
                    "dialogue": s.get("dialogue", ""),
                    "emotion": s.get("emotion", ""),
                    "importance": s.get("importance", "medium"),
                }
                shots_info.append(info)

            shots_json = json.dumps(shots_info, ensure_ascii=False, indent=2)
            genre_str = f"题材：{genre}" if genre else ""
            beats_str = f"导演节拍：{director_beats[:500]}" if director_beats else ""

            script_text = kwargs.get("script_text", "") or script or ""
            script_ctx = "\n\n【剧情上下文】（运镜贴合剧本情绪节奏）：\n" + script_text[:500] if script_text else ""
            film_ref = get_genre_film_ref(genre, aspect="cinematography")
            user_prompt = f"""{film_ref}{genre_str}
{beats_str}{script_ctx}

分镜脚本（含台词，请同时参考 dialogue 和 description 理解内容）：
{shots_json}

逐镜配置运镜/角度/景别/光线/转场。每镜必须写 flow_notes 说明如何承接前镜+铺垫后镜。不要套模板。"""

            result = self._call_llm_json(CINEMATOGRAPHER_PROMPT, user_prompt, retries=1, agent_id='cinematographer')

            if isinstance(result, dict):
                designed_shots = result.get("shots", [])
                overall_style = result.get("overall_style", "")

                merged_shots = []
                for i, s in enumerate(shots):
                    s_copy = dict(s)
                    if i < len(designed_shots):
                        d = designed_shots[i]
                        s_copy["camera_movement"] = d.get("camera_movement", s_copy.get("camera_movement", "固定"))
                        s_copy["camera_angle"] = d.get("camera_angle", s_copy.get("camera_angle", "平视"))
                        s_copy["shot_type"] = d.get("shot_type", s_copy.get("shot_type", "中景"))
                        s_copy["lighting"] = d.get("lighting", s_copy.get("lighting", ""))
                        s_copy["transition"] = d.get("transition", s_copy.get("transition", "切入"))
                        s_copy["flow_notes"] = d.get("flow_notes", "")
                        s_copy["rationale"] = d.get("rationale", "")
                    merged_shots.append(s_copy)

                elapsed = int((time.time() - start) * 1000)
                logger.info(f"[Cinematographer] {len(merged_shots)} shots in {elapsed}ms")
                return AgentResult(
                    success=True,
                    data={"shots": merged_shots, "overall_style": overall_style},
                    duration_ms=elapsed
                )

            return AgentResult(success=False, error="摄影方案生成失败", duration_ms=0)

        except Exception as e:
            logger.error(f"[Cinematographer] failed: {e}")
            return AgentResult(success=False, error=str(e),
                               duration_ms=int((time.time() - start) * 1000))

    def run(self, action: str = "design", **kwargs) -> AgentResult:
        return self.execute(action, **kwargs)

    def execute(self, action: str = "design", **kwargs) -> AgentResult:
        # ═══ 导演指令 ═══
        if "director_beats" not in kwargs or not kwargs.get("director_beats"):
            da = kwargs.get("director_analysis", kwargs.get("params", {}).get("director_analysis", {}) if "params" in kwargs else {})
            if isinstance(da, dict):
                beats = []
                if da.get("pacing_notes"): beats.append("节奏：" + str(da["pacing_notes"]))
                if da.get("emotional_curve"): beats.append("情绪：" + str(da["emotional_curve"]))
                if da.get("highlight_moments"): beats.append("高光：" + str(da["highlight_moments"]))
                if beats:
                    kwargs["director_beats"] = " | ".join(beats)
        if action in ("design", "design_shots"):
            return self.design_shots(**kwargs)
        if "shots" in kwargs:
            return self.design_shots(**kwargs)
        return AgentResult(success=False, error=f"未知 action: {action}")
