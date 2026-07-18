# -*- coding: utf-8 -*-
"""StoryboardAgent V3 — 真人短剧分镜标准"""
import json, logging, re
from core.agent_base_v3 import AgentV3
from core.safety_filter import clean_text
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是短剧分镜执行师。你的唯一任务：严格按导演指令拆分分镜，不得擅自增减镜头数量或改变导演意图。

【核心原则】
1. 导演给出的分镜表必须严格执行，镜号、时长、景别、画面、台词一字不改
2. 严禁添加导演没有安排的镜头
3. 严禁修改导演安排的台词
4. 严禁修改导演安排的时长

【导演指令优先级最高】导演说多少镜就多少镜，导演说什么风格就什么风格。你只负责将导演的意图拆解为可执行的分镜表。

【输出格式】JSON数组：
[{"shot_num":1,"start_time":"00:00","end_time":"00:12","duration_sec":12,"shot_type":"全景","camera_movement":"固定","description":"画面描述，40-80字","dialogue":"台词（标注说话人和情绪）","sound":"音效","focus_character":"焦点角色","location":"地点","note":"备注"}]"""


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

        # 必须拿到剧本
        if not script:
            return {"success": False, "error": "导演未提供剧本数据", "pipeline_id": task.get("pipeline_id", "")}

        # 导演指令（必须拿到）
        dir_brief = ""
        tasks = data.get("tasks", director.get("tasks", {}))
        sb_task = tasks.get("storyboard", "") if isinstance(tasks, dict) else ""
        if not sb_task:
            return {"success": False, "error": "导演未提供分镜指令", "pipeline_id": task.get("pipeline_id", "")}
        dir_brief = f"【导演分镜指令】{sb_task}\n"

        # 角色信息
        char_info = ""
        if chars:
            char_info = "角色:\n" + "\n".join([
                f"{c.get('name','?')}: {c.get('appearance', c.get('features',''))[:60]}" 
                for c in chars[:10] if c.get("name")
            ])

        prompt = (
            f"══════ 剧本 ══════\n{script}\n\n"
            f"{dir_brief}\n{char_info}\n"
            f"题材: {genre}\n\n"
            f"请严格按导演指令拆分分镜。镜头数量和时长由导演指令决定，不要自行计算。严禁添加剧本中没有的镜头。"
        )

        try:
            result = self.call_with_safety_retry(
                None, 3,
                UnifiedModel.llm,
                prompt=prompt,
                system=SYSTEM_PROMPT,
                max_tokens=8192,
                timeout=300,
            )
            if not result.get("success", True):
                err = result.get("error", "LLM调用失败")
                return {"success": False, "error": err[:200]}
            
            content = result.get("text", "[]")
            shots = self._parse_json(content)
            if not shots or not isinstance(shots, list):
                logger.warning("[Storyboard] JSON解析为空")
                return {"success": False, "error": "分镜JSON解析为空", "pipeline_id": task.get("pipeline_id", "")}

            logger.info(f"[Storyboard] {len(shots)} shots")
            resp = {"success": True, "shots": shots, "total_shots": len(shots), "pipeline_id": task.get("pipeline_id", "")}
            
            # 记录用量
            try:
                from services.usage_tracker import log_usage
                log_usage(
                    model_name="agnes-2.0-flash",
                    provider="agnes",
                    model_type="llm",
                    status="success",
                    user_id=task.get("data", {}).get("user_id", 0),
                    drama_id=task.get("pipeline_id", ""),
                    char_count=len(script),
                )
            except Exception as e:
                logger.warning(f"[Storyboard] 记录用量失败: {e}")
            
            return resp
        except Exception as e:
            logger.error("[Storyboard] " + str(e))
            return {"success": False, "error": str(e)[:200], "pipeline_id": task.get("pipeline_id", "")}

    def _parse_json(self, text: str):
        if not text: return []
        text = text.strip()
        # 1. 直接解析
        try: return json.loads(text)
        except: pass
        # 2. 提取 ```json ... ``` 块
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m:
            try: return json.loads(m.group(1).strip())
            except: pass
        # 3. 提取 [] 并强制补全
        m = re.search(r'\[[\s\S]*', text)
        if m:
            raw = m.group(0)
            # 去掉末尾逗号
            raw = raw.rstrip()
            if raw.endswith(','):
                raw = raw[:-1]
            # 补全括号
            open_brackets = raw.count('[')
            close_brackets = raw.count(']')
            if open_brackets > close_brackets:
                raw += ']' * (open_brackets - close_brackets)
            # 尝试解析
            try: return json.loads(raw)
            except: pass
            # 更激进：逐层剥离到最后有效的JSON
            for i in range(len(raw), 0, -10):
                try:
                    candidate = raw[:i].rstrip().rstrip(',')
                    if open_brackets > close_brackets:
                        candidate += ']' * (raw[:i].count('[') - raw[:i].count(']'))
                    result = json.loads(candidate)
                    if isinstance(result, list):
                        result = self._clean_result(result)
        return result
                except:
                    continue
        return []
