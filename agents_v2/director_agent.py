# -*- coding: utf-8 -*-
"""DirectorAgent V3 — 导演中心制：读剧本，建脑图，分派任务"""
import json, logging, re, time
from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是短剧总导演。完整阅读剧本后，在脑中构建完整的视觉画面，然后给各部门下达具体指令。

你必须输出以下JSON，每个字段都必须填写：

{
  "genre": "古装/现代/仙侠/玄幻/武侠/宫廷/商战/职场/甜宠/悬疑/科幻/恐怖/逆袭/重生/穿越/复仇/军旅/民国/乡村/校园/家庭/搞笑",
  "core_conflict": "核心冲突一句话",
  "director_vision": "导演总纲：整体风格、视觉基调、叙事手法",
  "emotional_curve": "情绪曲线：从开场到结尾的情绪变化",
  "highlight_moments": "3-5个关键高光时刻",
  "pacing_notes": "节奏要求",

  "characters": [
    {
      "name": "角色名",
      "gender": "男/女",
      "age": "年龄(数字+岁)",
      "personality": "性格特征",
      "role_type": "主角/配角",
      "appearance": "按短剧标准：五官+发型+体形+穿搭。示例：18岁男高中生，清瘦白净，黑色短发碎盖，单眼皮，穿宽松白色校服T恤",
      "voice_style": "音色描述"
    }
  ],

  "scenes": [
    {
      "scene_num": 1,
      "location": "地点",
      "time": "白天/黑夜",
      "atmosphere": "氛围",
      "dialogue_summary": "对白概要"
    }
  ],

  "给角色设计师": "每个角色的服装款式+颜色+材质、发型、妆容重点、气质方向。要具体，不要笼统",
  "给分镜师": "按短剧标准拆分：开篇钩子(0-15s特写抓眼球)→中段冲突(正反打快速切换)→结尾悬念(最后1镜留钩子)。每镜3-6秒/冲突≤5秒。特写40%+中近景35%+全景15%+空镜10%。情绪转折必切镜，关键道具单独分镜，动作拆2-3镜。根据剧本内容灵活确定镜头数量。格式：镜1丨4s丨特写丨推丨画面描述丨台词(情绪)丨音效",
  "给摄影师": "机位角度(俯拍/仰拍/平视)、镜头焦段(广角/长焦/标准)、光影方案(主光方向+色温)、构图法则、景深控制",
  "给场景设计师": "场景色调、主光源方向、光影氛围、空间布局、关键道具",
  "给配音师": "每个角色的声音年龄、语速、情绪基调、特殊语气(如哽咽/咆哮/耳语)",
  "给剪辑师": "转场方式、剪辑节奏快慢、特效需求、色彩调性",

  "director_analysis": "导演内心独白：你对这个剧本的理解，你脑中看到的画面，你想传达的情感。100-200字"
}

要求：
1. 完整阅读剧本，理解每个角色的身份和处境
2. 根据角色年龄和身份推断外貌（年轻人/中年人/老人各有特征）
3. 所有字段必须填写，不得留空，不得写无
4. 输出纯JSON"""


class DirectorAgent(AgentV3):
    name = "director"

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        script = data.get("script_text", task.get("script_text", ""))
        genre_hint = data.get("genre", "")
        title = data.get("title", "")

        if not script or len(script.strip()) < 10:
            return {"success": False, "error": "剧本内容过短"}

        prompt = "剧本：\n" + script[:8000] + "\n\n" + ("题材参考：" + genre_hint if genre_hint else "") + "\n请输出JSON。"

        try:
            result = UnifiedModel.llm(
                prompt=prompt, system=SYSTEM_PROMPT,
                model=None, timeout=300, max_tokens=4096,
            )
            content = result.text if hasattr(result, 'text') else result.get("text", "{}")
            analysis = self._parse_json(content)
            
            if not analysis:
                logger.warning("[Director] JSON解析失败")
                return {"success": False, "error": "导演分析JSON解析失败"}

            # 整理输出：把中文task名映射为英文key，统一放在tasks下
            chars = analysis.get("characters", [])
            scenes = analysis.get("scenes", [])
            
            analysis["refined_script"] = {
                "title": title or analysis.get("title", ""),
                "characters": chars,
                "scenes": scenes,
            }
            
            analysis["tasks"] = {
                "storyboard": analysis.pop("给分镜师", "") or "根据剧本设计分镜",
                "character": analysis.pop("给角色设计师", "") or "根据角色信息设计造型",
                "cinematographer": analysis.pop("给摄影师", "") or "根据场景设计摄影方案",
                "scene": analysis.pop("给场景设计师", "") or "根据剧本设计场景",
                "audio": analysis.pop("给配音师", "") or "根据角色性格设计配音",
                "video": analysis.pop("给剪辑师", "") or "根据节奏设计剪辑方案",
            }

            g = analysis.get("genre", "")
            logger.info(f"[Director] genre={g}, chars={len(chars)}, tasks={list(analysis['tasks'].keys())}")
            return {"success": True, "data": analysis}
            
        except Exception as e:
            logger.error("[Director] " + str(e))
            return {"success": False, "error": str(e)[:200]}

    def _parse_json(self, text: str) -> dict:
        if not text: return {}
        try: return json.loads(text)
        except: pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m:
            try: return json.loads(m.group(1))
            except: pass
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try: return json.loads(m.group(0))
            except: pass
        return {}
