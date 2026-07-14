# -*- coding: utf-8 -*-
"""StoryboardAgent V3 — 真人短剧分镜标准"""
import json, logging, re
from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是短剧分镜执行师。你的唯一任务：严格按导演指令拆分分镜，不得擅自增减镜头数量或改变导演意图。

【导演指令优先级最高】导演说多少镜就多少镜，导演说什么风格就什么风格。你只负责将导演的意图拆解为可执行的分镜表。

【硬性规则】导演指定了镜头数量就必须严格遵守。每镜3-6秒。9:16竖屏。

【输出格式】JSON数组：
[{"shot_num":1,"duration_sec":4,"shot_type":"特写","camera_movement":"固定","description":"画面描述","dialogue":"台词","sound":"音效","focus_character":"焦点角色","location":"地点"}]

【硬性规则】
- 9:16竖屏短剧，单集1-3分钟
- 每镜3-6秒，冲突镜头不超5秒，情感戏不超8秒
- 对话2句一切镜，情绪变化立刻切特写
- 根据剧本长度灵活确定镜头数，3分钟单集约20镜

【三段式结构】
- 开篇钩子(0-15s)：第一镜必须抓眼球——特写眼泪/争吵/秘密/打脸，禁止开场走路喝水远景
- 中段冲突(15s-结尾前10s)：反转/对峙/拉扯，正反打快速切换
- 结尾悬念(最后5-10s)：反派阴笑特写、主角震惊瞳孔、关键物证定格

【镜头比例分布】
- 特写40%：情绪爆发、眼泪、眼神、手部、关键道具
- 中近景35%：对话、对峙、双人正反打
- 全景15%：开场交代场景、肢体冲突
- 空镜10%：钟表、下雨、酒杯、转场过渡

【关键规则】
1. 情绪转折必切镜：平静→生气、委屈→落泪，立刻换镜头
2. 关键道具单独分镜：合同、戒指、手机消息、病历、亲子鉴定，单独1镜2-3秒
3. 动作拆解多镜：一个动作拆2-3镜，不一镜到底
4. 同角度不连续使用：特写↔中近景交替
5. 结尾最后一镜必须留悬念钩子

【输出格式】JSON数组：
[{"shot_num":1,"duration_sec":4,"shot_type":"特写","camera_movement":"固定","description":"画面描述，40-80字","dialogue":"台词（标注说话人和情绪）","sound":"音效","focus_character":"焦点角色","location":"地点","note":"备注"}]"""


class StoryboardAgent(AgentV3):
    name = "storyboard"

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        script = data.get("script_text", task.get("script_text", ""))
        chars = data.get("characters", [])
        director = data.get("director_analysis", {})
        if not isinstance(director, dict) and data.get("director_vision"):
            director = data
        genre = data.get("genre", director.get("genre", "现代"))

        if not script:
            return {"success": False, "error": "无剧本"}

        # 导演指令
        dir_brief = ""
        tasks = data.get("tasks", director.get("tasks", {}))
        sb_task = tasks.get("storyboard", "") if isinstance(tasks, dict) else ""
        if sb_task:
            dir_brief = f"【导演分镜指令】{sb_task}\n"

        # 角色信息
        char_info = ""
        if chars:
            char_info = "角色:\n" + "\n".join([
                f"{c.get('name','?')}: {c.get('appearance','')[:60]}" 
                for c in chars[:10] if c.get("name")
            ])

        prompt = (
            f"══════ 剧本 ══════\n{script[:12000]}\n\n"
            f"{dir_brief}\n{char_info}\n"
            f"题材: {genre}\n\n"
            f"请按短剧行业标准拆分{18 if len(script)<500 else 25}个左右的分镜JSON数组。"
        )

        try:
            result = UnifiedModel.llm(
                prompt=prompt, system=SYSTEM_PROMPT,
                model=None, timeout=300, max_tokens=8192,
            )
            if not result.get("success", True):
                err = result.get("error", "LLM调用失败")
                return {"success": False, "error": err[:200]}
            
            content = result.get("text", result.get("content", "[]"))
            shots = self._parse_json(content)
            if not shots or not isinstance(shots, list):
                shots = []

            # 修正时长
            for s in shots:
                d = s.get("duration_sec", s.get("duration", 5))
                if isinstance(d, (int, float)):
                    if d > 8: s["duration_sec"] = 8
                    elif d < 2: s["duration_sec"] = 3

            logger.info(f"[Storyboard] {len(shots)} shots")
            return {"success": True, "data": {"shots": shots, "total_shots": len(shots)}}
        except Exception as e:
            logger.error("[Storyboard] " + str(e))
            return {"success": False, "error": str(e)[:200]}

    def _parse_json(self, text: str):
        try: return json.loads(text)
        except: pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m:
            try: return json.loads(m.group(1))
            except: pass
        m = re.search(r'\[[\s\S]*\]', text)
        if m:
            try: return json.loads(m.group(0))
            except: pass
        return []
