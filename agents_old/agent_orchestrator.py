"""总控智能体 v2 — 剧本分析 + 多模型路由调度 + 动态 agent_instructions"""
import json, time, re, logging, urllib.request, urllib.parse
from typing import Dict, Optional, Any
from .agent_base_legacy import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

# ===== 质量/预算模式 =====
QUALITY_PRESETS = {
    "省钱": {
        "model_tier": "fast",
        "script_detail": "大纲级",
        "character_detail": "基础",
        "storyboard_count": "auto_low",
        "scene_detail": "基础",
        "video_quality": "sd",
        "tts_quality": "标准",
        "bgm_count": 1,
        "face_lock": False,
        "budget_note": "用最便宜的模型，不锁脸，降低复杂度控制成本"
    },
    "均衡": {
        "model_tier": "standard",
        "script_detail": "详细",
        "character_detail": "详细",
        "storyboard_count": "auto",
        "scene_detail": "详细",
        "video_quality": "hd",
        "tts_quality": "高",
        "bgm_count": 2,
        "face_lock": True,
        "budget_note": "平衡质量与成本，默认模式，开启锁脸"
    },
    "高质量": {
        "model_tier": "premium",
        "script_detail": "精修级",
        "character_detail": "精细",
        "storyboard_count": "auto_high",
        "scene_detail": "精细",
        "video_quality": "4k",
        "tts_quality": "极高",
        "bgm_count": 3,
        "face_lock": True,
        "budget_note": "全部用最优模型，高质量锁脸"
    },
}

COMPLEXITY_KEYWORDS = {
    "high": ["古装", "宫廷", "仙侠", "玄幻", "科幻", "战争", "末世", "穿越", "重生", "魔法", "神话", "史诗",
             "大规模", "群像", "多线", "江湖", "武林", "帝国", "宇宙", "王朝", "三界", "诸神", "妖兽",
             "大场面", "特效", "动作", "打斗", "战斗", "大战", "战争"],
    "medium": ["都市", "校园", "职场", "悬疑", "推理", "恋爱", "情感", "家庭", "喜剧", "轻喜剧",
               "现代", "青春", "成长", "励志", "创业", "商战", "逆袭"],
    "low": ["日常", "生活", "vlog", "吐槽", "单人", "采访", "教程", "解说", "盘点", "流水账"],
}
COMPLEXITY_WEIGHTS = {"high": 3, "medium": 2, "low": 1}

# ===== 模型路由规则（类也会从 ai_providers 加载最新版，但这里兜底）=====
IMAGE_ROUTES = {
    "省钱": {"primary": "agnes", "model": "agnes-image-2.1-flash", "fallback": "wanxiang"},
    "均衡": {"primary": "ark", "model": "doubao-seedream-4-0-250828", "fallback": "wanxiang"},
    "高质量": {"primary": "ark", "model": "doubao-seedream-4-0-250828", "fallback": "wanxiang"},
}

FACE_ROUTES = {
    "省钱": {"primary": "agnes", "model": "agnes-image-2.1-flash", "fallback": "wanxiang", "face_ref": False},
    "均衡": {"primary": "ark", "model": "doubao-seedream-4-0-250828", "fallback": "wanxiang", "face_ref": False},
    "高质量": {"primary": "ark", "model": "doubao-seedream-4-0-250828", "fallback": "wanxiang", "face_ref": False},
}

VIDEO_ROUTES = {
    "省钱": {"primary": "static", "model": "img2vid", "fallback": None},
    "均衡": {"primary": "seedance", "model": "doubao-seedance-1-5-pro-251215", "closeup_model": "doubao-seedance-2-0-260128", "fallback": "static"},
    "高质量": {"primary": "seedance", "model": "doubao-seedance-1-5-pro-251215", "closeup_model": "doubao-seedance-2-0-260128", "fallback": "static"},
}

LLM_ROUTES = {
    "省钱": "deepseek-chat",
    "均衡": "deepseek-chat",
    "高质量": "deepseek-chat",
}


class OrchestratorAgent(BaseAgent):
    name = "orchestrator"
    agent_id = "orchestrator"
    description = "剧本分析、质量成本调度、流水线优化、多模型路由、agent_instructions"
    version = "2.1.0"

    def analyze_script(self, script: str, budget_mode: str = "均衡") -> dict:
        if not script:
            return {"error": "剧本为空", "quality_config": QUALITY_PRESETS["均衡"]}

        script_lower = script.lower()
        word_count = len(script)
        estimated_episodes = max(1, round(word_count / 450))

        scores = {"high": 0, "medium": 0, "low": 0}
        for level, keywords in COMPLEXITY_KEYWORDS.items():
            for kw in keywords:
                count = script_lower.count(kw.lower())
                if count > 0:
                    scores[level] += count * COMPLEXITY_WEIGHTS[level]

        if scores["high"] >= 3 or (scores["high"] * 3 + scores["medium"] * 2) >= 10:
            complexity = "high"
        elif scores["medium"] >= 2 or (scores["high"] + scores["medium"]) >= 3:
            complexity = "medium"
        else:
            complexity = "low"

        char_mentions = self._count_characters(script)
        scene_types = self._analyze_scenes(script)

        quality_config = QUALITY_PRESETS.get(budget_mode, QUALITY_PRESETS["均衡"]).copy()

        if complexity == "high" and budget_mode == "省钱":
            quality_config["model_tier"] = "standard"
            quality_config["budget_note"] += "（但因剧本复杂，自动提升一档模型）"

        if complexity == "low" and budget_mode == "高质量":
            quality_config["model_tier"] = "standard"
            quality_config["budget_note"] += "（因剧本简单，自动降一档避免浪费）"

        # 生成模型路由表
        model_routes = self._build_routes(budget_mode, complexity)

        # 生成每个智能体的执行指令
        agent_instructions = self._build_instructions({
            "complexity": complexity,
            "character_count": len(char_mentions),
            "character_names": char_mentions[:10],
            "scene_types": scene_types,
            "estimated_episodes": estimated_episodes,
            "budget_mode": budget_mode,
            "quality_config": quality_config,
        })

        analysis = {
            "word_count": word_count,
            "estimated_episodes": estimated_episodes,
            "complexity": complexity,
            "complexity_scores": scores,
            "character_count": len(char_mentions),
            "character_names": char_mentions[:10],
            "scene_types": scene_types,
            "budget_mode": budget_mode,
            "quality_config": quality_config,
            "model_routes": model_routes,
            "agent_instructions": agent_instructions,
            "recommendation": self._get_recommendation(complexity, char_mentions, scene_types),
        }
        return analysis

    def _build_instructions(self, analysis):
        """根据剧本分析结果，为每个智能体生成执行指令"""
        complexity = analysis.get("complexity", "medium")
        char_count = analysis.get("character_count", 0)
        char_names = analysis.get("character_names", [])
        scene_types = analysis.get("scene_types", ["通用"])
        episodes = analysis.get("estimated_episodes", 1)
        budget_mode = analysis.get("budget_mode", "均衡")
        quality_config = analysis.get("quality_config", {})
        face_lock = quality_config.get("face_lock", True)
        instructions = {}

        # ===== scene 智能体指令 =====
        scene_i = {}
        if "古装" in scene_types:
            scene_i["default_mood"] = "古风暖调"
            scene_i["ambient_sound"] = "风铃声/木门声"
        if "都市" in scene_types:
            scene_i["default_mood"] = "现代冷调"
        if "室内" in scene_types and len(scene_types) == 1:
            scene_i["batch_mode"] = True
        if episodes > 5:
            scene_i["diversity_boost"] = True
        instructions["scene"] = scene_i

        # ===== character 智能体指令 =====
        char_i = {}
        if face_lock and char_count > 0:
            char_i["face_lock"] = True
            char_i["primary_reference"] = char_names[0] if char_names else ""
        if char_count <= 3:
            char_i["detail_level"] = "精细"
        elif char_count > 8:
            char_i["detail_level"] = "重点"
            if len(char_names) >= 3:
                char_i["economy_sidekicks"] = True
        if budget_mode == "省钱":
            char_i["face_lock"] = False
        instructions["character"] = char_i

        # ===== video 智能体指令 =====
        vid_i = {}
        if complexity == "high":
            vid_i["dynamic_camera"] = True
            vid_i["transition"] = "丰富的转场效果"
        if budget_mode == "省钱" or not face_lock:
            vid_i["face_lock"] = False
        else:
            vid_i["face_lock"] = True
        if episodes > 10:
            vid_i["batch_priority"] = "先批量优化提示词，再逐个生成"
        instructions["video"] = vid_i

        # ===== tts 智能体指令 =====
        tts_i = {}
        if episodes > 5:
            tts_i["speed_boost"] = 1.1
        if complexity == "high":
            tts_i["emotion_range"] = "丰富"
        instructions["tts"] = tts_i

        # ===== bgm 智能体指令 =====
        bgm_i = {}
        moods = []
        if "古装" in scene_types: moods.append("古风")
        if "都市" in scene_types: moods.append("现代")
        if complexity == "high": moods.append("大气")
        if complexity == "low": moods.append("轻松")
        if moods:
            bgm_i["style_hint"] = "、".join(moods)
        instructions["bgm"] = bgm_i

        # ===== composite 智能体指令 =====
        comp_i = {}
        if episodes > 5:
            comp_i["output_segments"] = True
        comp_i["format"] = "mp4"
        comp_i["codec"] = "h264"
        instructions["composite"] = comp_i

        return instructions

    def _build_routes(self, budget_mode: str, complexity: str) -> dict:
        """根据预算 + 复杂度生成完整模型路由表"""
        budget = budget_mode if budget_mode in ("省钱", "均衡", "高质量") else "均衡"
        routes = {}
        routes["image"] = dict(IMAGE_ROUTES.get(budget, IMAGE_ROUTES["均衡"]))
        routes["face"] = dict(FACE_ROUTES.get(budget, FACE_ROUTES["均衡"]))
        routes["video"] = dict(VIDEO_ROUTES.get(budget, VIDEO_ROUTES["均衡"]))
        routes["llm"] = {"primary": "bailian", "model": LLM_ROUTES.get(budget, "deepseek-chat"), "fallback": None}
        routes["figure"] = dict(IMAGE_ROUTES.get(budget, IMAGE_ROUTES["均衡"]))
        return routes

    def _count_characters(self, script: str) -> list:
        names = set()
        patterns = [
            r'[（(]\s*(.*?)[)）]',
            r'【(.+?)】',
            r'[：:]\s*(\S{2,4})[：:]',
        ]
        for pat in patterns:
            for m in re.finditer(pat, script):
                name = m.group(1).strip()
                if 1 < len(name) < 6 and not re.search(r'[0-9a-zA-Z]', name):
                    names.add(name)
        return list(names)

    def _analyze_scenes(self, script: str) -> list:
        scene_keywords = {
            "室内": ["房间", "屋里", "室内", "办公室", "教室", "客厅", "卧室", "厨房"],
            "室外": ["街道", "公园", "野外", "山上", "河边", "海边", "广场", "操场"],
            "古装": ["宫殿", "客栈", "酒楼", "战场", "庭院", "花园", "山寨", "王府"],
            "都市": ["公司", "商场", "地铁", "餐厅", "咖啡厅", "酒吧", "健身房"],
        }
        scenes = []
        script_lower = script.lower()
        for scene_type, keywords in scene_keywords.items():
            for kw in keywords:
                if kw in script_lower:
                    scenes.append(scene_type)
                    break
        return list(set(scenes)) if scenes else ["通用"]

    def _get_recommendation(self, complexity: str, chars: list, scenes: list) -> str:
        tips = []
        if complexity == "high":
            tips.append("剧本内容丰富，建议分配较长时间生成")
        if len(chars) > 8:
            tips.append(f"角色较多({len(chars)}个)，角色建模会占用较多资源")
        if len(scenes) > 3:
            tips.append("场景切换频繁，分镜数会较多")
        if not tips:
            tips.append("适合快速生成")
        return "；".join(tips)

    def _get_model_info(self, service: str = "image", budget: str = "均衡") -> dict:
        """查询某个模型服务的推荐链、价格和限流信息"""
        model_db = {
            "image": {
                "recommended_chain": [
                    {"name": "wanxiang", "model": "wan2.7-image", "price": "¥0.04/张", "timeout": 8, "service": "wanxiang", "provider": "TongyiWanxiangProvider", "notes": "阿里通义万相，最便宜"},
                    {"name": "seedream", "model": "doubao-seedream-4-0-250828", "price": "¥0.12/张", "timeout": 45, "service": "ark", "provider": "ARKImageProvider", "notes": "火山Seedream 5.0，50QPS"},
                    {"name": "agnes-2.1", "model": "agnes-image-2.1-flash", "price": "免费(额度未知)", "timeout": 30, "service": "agnes", "provider": "AgnesAIProvider", "notes": "Agnes Hub备用"},
                    {"name": "fallback", "model": "ffmpeg", "price": "免费", "timeout": 5, "service": "ffmpeg", "provider": "ffmpeg", "static": True, "notes": "本地处理保底"},
                ],
                "analysis": "万相¥0.04/张最便宜，Seedream¥0.12/张有免费100万tokens，Agnes免费备用"
            },
            "face": {
                "recommended_chain": [
                    {"name": "wanxiang", "model": "wan2.7-image", "price": "¥0.04/张", "timeout": 8, "service": "wanxiang", "provider": "TongyiWanxiangProvider", "notes": "阿里通义万相，最便宜"},
                    {"name": "seedream", "model": "doubao-seedream-4-0-250828", "price": "¥0.12/张", "timeout": 45, "service": "ark", "provider": "ARKImageProvider", "notes": "火山Seedream 5.0，50QPS"},
                    {"name": "agnes-2.1", "model": "agnes-image-2.1-flash", "price": "免费(额度未知)", "timeout": 30, "service": "agnes", "provider": "AgnesAIProvider"},
                    {"name": "fallback", "model": "ffmpeg", "price": "免费", "timeout": 5, "service": "ffmpeg", "provider": "ffmpeg", "static": True},
                ],
                "analysis": "同image链"
            },
            "video": {
                "recommended_chain": [
                    {"name": "kling", "model": "kling-v2-6", "price": "¥0.2/5秒", "concurrency": 3, "timeout": 60, "service": "kling", "provider": "KlingProvider", "notes": "可灵Kling，3并发，最便宜"},
                    {"name": "seedance", "model": "doubao-seedance-1-5-pro-251215", "price": "¥0.4/次", "timeout": 30, "service": "ark", "provider": "seedance", "notes": "火山Seedance"},
                    {"name": "happyhorse", "model": "happyhorse", "price": "未知", "timeout": 20, "service": "happyhorse", "provider": "happyhorse"},
                    {"name": "ffmpeg", "model": "ffmpeg", "price": "免费", "timeout": 30, "service": "ffmpeg", "provider": "ffmpeg", "static": True},
                ],
                "analysis": "Kling¥0.2/5秒最优，Seedance¥0.4/次备用"
            },
            "tts": {
                "recommended_chain": [
                    {"name": "edge-tts", "model": "edge-tts", "price": "免费", "timeout": 15, "service": "edge", "provider": "EdgeTTSProvider", "notes": "Edge TTS免费效果好"},
                    {"name": "cosyvoice", "model": "cosyvoice-v2", "price": "¥2/万字符", "timeout": 20, "service": "qwen", "provider": "cosyvoice", "notes": "阿里百炼CosyVoice"},
                    {"name": "silent", "model": "silent", "price": "免费", "timeout": 1, "service": "silent", "provider": "silent", "static": True},
                ],
                "analysis": "Edge免费优先，CosyVoice ¥2/万字符备用"
            },
            "llm": {
                "recommended_chain": [
                    {"name": "deepseek-chat", "model": "deepseek-chat", "price_input": "$0.14/M", "price_output": "$0.28/M", "concurrency": 2500, "timeout": 60, "service": "deepseek", "provider": "deepseek", "notes": "⚠️ 2026/07/24停用，需迁移到v4-flash"},
                    {"name": "deepseek-v4-flash", "model": "deepseek-v4-flash", "price_input": "$0.14/M", "price_output": "$0.28/M", "concurrency": 2500, "timeout": 60, "service": "deepseek", "provider": "deepseek", "notes": "deepseek-chat替代品"},
                ],
                "analysis": "DeepSeek v4-flash 输入$0.14/M(缓存$0.0028/M)，输出$0.28/M，并发2500",
                "warnings": ["deepseek-chat 2026/07/24 停用"]
            },
            "bgm": {
                "recommended_chain": [
                    {"name": "music-api", "model": "bgm-generator", "timeout": 30, "service": "music", "provider": "music_api"},
                ],
                "analysis": "music-api生成，失败则明确报错不兜底"
            },
        }
        info = model_db.get(service, model_db["image"])
        return {
            "service": service,
            "budget": budget,
            "recommended_chain": info["recommended_chain"],
            "pricing_analysis": info["analysis"],
            "warnings": info.get("warnings", []),
        }

    def run(self, action: str = "analyze", **params) -> AgentResult:
        start = time.time()

        if action == "analyze":
            script = params.get("script", params.get("premise", ""))
            budget_mode = params.get("budget_mode", "均衡")
            analysis = self.analyze_script(script, budget_mode)
            return AgentResult(
                success=True,
                data={
                    "analysis": analysis,
                    "quality_config": analysis["quality_config"],
                    "model_routes": analysis["model_routes"],
                    "agent_instructions": analysis.get("agent_instructions", {}),
                    "complexity": analysis["complexity"],
                    "estimated_episodes": analysis["estimated_episodes"],
                },
                duration_ms=int((time.time() - start) * 1000)
            )

        elif action == "optimize_flow":
            analysis = params.get("analysis", {})
            quality_config = analysis.get("quality_config", QUALITY_PRESETS["均衡"])
            model_routes = analysis.get("model_routes", self._build_routes(quality_config.get("budget_mode", "均衡"), "medium"))
            return AgentResult(
                success=True,
                data={
                    "quality_config": quality_config,
                    "model_routes": model_routes,
                    "agent_instructions": analysis.get("agent_instructions", {}),
                    "flow_params": {
                        "script": {"model_tier": quality_config["model_tier"], "detail_level": quality_config["script_detail"]},
                        "character": {"model_tier": quality_config["model_tier"], "detail_level": quality_config["character_detail"]},
                        "storyboard": {"model_tier": quality_config["model_tier"], "count_mode": quality_config["storyboard_count"]},
                        "scene": {"model_tier": quality_config["model_tier"], "detail_level": quality_config["scene_detail"]},
                        "video": {"quality": quality_config["video_quality"]},
                        "tts": {"quality": quality_config["tts_quality"]},
                        "bgm": {"count": quality_config["bgm_count"]},
                        "subtitle": {},
                        "composite": {},
                    },
                    "budget_note": quality_config["budget_note"],
                },
                duration_ms=int((time.time() - start) * 1000)
            )

        elif action == "get_presets":
            return AgentResult(
                success=True,
                data={"presets": list(QUALITY_PRESETS.keys())},
                duration_ms=0
            )

        elif action == "model_info":
            """查某个模型的价格/限流信息"""
            service = params.get("service", "image")
            budget = params.get("budget", "均衡")
            return AgentResult(success=True, data=self._get_model_info(service, budget))

        elif action == "compare_models":
            """对比多个模型服务的价格和限流"""
            services = params.get("services", ["image", "video", "llm"])
            budget = params.get("budget", "均衡")
            results = {}
            for s in services:
                results[s] = self._get_model_info(s, budget)
            return AgentResult(
                success=True,
                data=results,
                duration_ms=0
            )

        elif action == "deepseek_migration":
            """检查 deepseek-chat 迁移状态"""
            try:
                req = urllib.request.Request('https://api.deepseek.com/models',
                    headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req, timeout=10, context=s) as resp:
                    models_data = json.loads(resp.read().decode('utf-8'))
                    model_ids = [m.get('id') for m in models_data.get('data', [])]
            except Exception:
                logger.warning('model fetch failed', exc_info=True)
                model_ids = []
            return AgentResult(
                success=True,
                data={
                    "current_model": "deepseek-chat",
                    "target_model": "deepseek-v4-flash",
                    "available_models_on_api": model_ids[:20],
                    "legacy_still_available": "deepseek-chat" in model_ids,
                    "target_available": "deepseek-v4-flash" in model_ids,
                    "deadline": "2026-07-24 23:59 北京时间",
                    "recommendation": "建议迁移到 deepseek-v4-flash（价格功能完全一致）"
                },
                duration_ms=0
            )

        return AgentResult(
            success=False, data={}, error=f"未知动作: {action}",
            duration_ms=int((time.time() - start) * 1000)
        )


orchestrator = OrchestratorAgent()
