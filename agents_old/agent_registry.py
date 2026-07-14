"""智能体注册中心 — 统一管理所有智能体"""
import logging
from typing import Dict, Type, Optional
from .agent_base_legacy import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


_agent_registry: Dict[str, Type[BaseAgent]] = {}

# 所有智能体列表
AGENTS_META = [
    {
        "id": "script",
        "name": "剧本智能体",
        "icon": "fa-solid fa-scroll",
        "description": "梗概扩写、剧本优化、自动埋剧集结尾钩子",
        "color": "#8b5cf6",
        "actions": [
            {"id": "expand", "name": "梗概扩写", "description": "一句话梗概→完整大纲"},
            {"id": "create", "name": "剧本生成", "description": "完整结构化剧本（带角色+分场台词）"},
            {"id": "optimize", "name": "剧本优化", "description": "节奏/冲突/人物优化"},
            {"id": "hook", "name": "结尾钩子", "description": "自动生成悬念钩子"}
        ]
    },
    {
        "id": "character",
        "name": "人设智能体",
        "icon": "fa-solid fa-user",
        "description": "人脸锁定、多服饰生成、角色存档",
        "color": "#ec4899",
        "actions": [
            {"id": "create", "name": "角色创建", "description": "详细角色人设生成"},
            {"id": "wardrobe", "name": "服装设计", "description": "多场景服装搭配"},
            {"id": "save", "name": "角色存档", "description": "保存到角色库"}
        ]
    },
    {
        "id": "costume",
        "name": "角色造型智能体",
        "icon": "fa-solid fa-vest",
        "description": "角色建模、多套服装设计、统一造型方案",
        "color": "#f43f5e",
        "actions": [
            {"id": "design", "name": "造型设计", "description": "为角色设计3套完整造型方案（含AI绘图prompt）"},
            {"id": "batch", "name": "批量造型", "description": "为多个角色批量造型设计"}
        ]
    },
    {
        "id": "storyboard",
        "name": "分镜智能体",
        "icon": "fa-solid fa-clapperboard",
        "description": "自动运镜参数、镜头时长分配",
        "color": "#f59e0b",
        "actions": [
            {"id": "generate", "name": "分镜生成", "description": "完整分镜脚本"},
            {"id": "revise", "name": "单镜修改", "description": "根据反馈修改分镜"},
            {"id": "optimize", "name": "运镜优化", "description": "镜头调度优化"}
        ]
    },
    {
        "id": "scene",
        "name": "场景绘图智能体",
        "icon": "fa-solid fa-mountain",
        "description": "天气/光影一键批量修改",
        "color": "#10b981",
        "actions": [
            {"id": "design", "name": "场景设计", "description": "新场景设计生成"},
            {"id": "weather", "name": "天气修改", "description": "批量修改天气"},
            {"id": "lighting", "name": "光影调整", "description": "批量调整光影"}
        ]
    },
    {
        "id": "video",
        "name": "视频生成智能体",
        "icon": "fa-solid fa-video",
        "description": "可灵调度、动态运镜生成",
        "color": "#3b82f6",
        "actions": [
            {"id": "optimize", "name": "提示词优化", "description": "分镜→AI视频提示词"},
            {"id": "generate", "name": "视频生成", "description": "调用模型生成视频"},
            {"id": "batch", "name": "批量优化", "description": "全部分镜批量处理"}
        ]
    },
    {
        "id": "tts",
        "name": "情绪配音智能体",
        "icon": "fa-solid fa-microphone",
        "description": "多音色区分、情绪变速",
        "color": "#06b6d4",
        "actions": [
            {"id": "casting", "name": "配音选角", "description": "角色配音方案"},
            {"id": "annotate", "name": "情绪标注", "description": "台词情绪语速标注"},
            {"id": "speech", "name": "生成配音", "description": "TTS语音生成"}
        ]
    },
    {
        "id": "subtitle",
        "name": "字幕智能体",
        "icon": "fa-solid fa-closed-captioning",
        "description": "自动时间轴、字幕样式自定义",
        "color": "#8b5cf6",
        "actions": [
            {"id": "timeline", "name": "生成时间轴", "description": "字幕时间轴"},
            {"id": "style", "name": "样式设计", "description": "字幕样式方案"},
            {"id": "export", "name": "导出字幕", "description": "SRT格式导出"}
        ]
    },
    {
        "id": "bgm",
        "name": "BGM配乐智能体",
        "icon": "fa-solid fa-music",
        "description": "剧情自动匹配BGM、卡点音效",
        "color": "#ef4444",
        "actions": [
            {"id": "match", "name": "BGM匹配", "description": "情绪→BGM方案"},
            {"id": "sfx", "name": "音效添加", "description": "卡点/环境音效"}
        ]
    },
    {
        "id": "composite",
        "name": "成片合成智能体",
        "icon": "fa-solid fa-film",
        "description": "多素材拼接、画质增强、成片打包",
        "color": "#6366f1",
        "actions": [
            {"id": "collect", "name": "素材收集", "description": "收集项目素材"},
            {"id": "assemble", "name": "合成脚本", "description": "生成ffmpeg合成命令"},
            {"id": "enhance", "name": "画质增强", "description": "视频画质优化"}
        ]
    }
]


def register_agent(agent_id: str, agent_class: Type[BaseAgent]):
    """注册智能体"""
    _agent_registry[agent_id] = agent_class
    # Patch: 设置默认 agent_id 用于自动模型选择
    original_init = agent_class.__init__
    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.agent_id = agent_id
    agent_class.__init__ = patched_init
    logger.info(f"智能体已注册: {agent_id} -> {agent_class.__name__}")


def get_agent(agent_id: str) -> Optional[BaseAgent]:
    """获取智能体实例"""
    cls = _agent_registry.get(agent_id)
    if cls:
        return cls()
    return None


def get_all_agents_meta() -> list:
    """获取所有智能体元信息"""
    return AGENTS_META


def init_registry():
    """初始化注册所有智能体"""
    from .agent_orchestrator import OrchestratorAgent
    from .agent_script import ScriptAgent
    from .agent_character import CharacterAgent
    from .agent_costume import CostumeAgent
    from .agent_storyboard import StoryboardAgent
    from .agent_scene import SceneAgent
    from .agent_video import VideoAgent
    from .agent_tts import TTSAgent
    from .agent_subtitle import SubtitleAgent
    from .agent_bgm import BGMAgent
    from .agent_composite import CompositeAgent

    register_agent("orchestrator", OrchestratorAgent)
    register_agent("script", ScriptAgent)
    register_agent("character", CharacterAgent)
    register_agent("storyboard", StoryboardAgent)
    register_agent("costume", CostumeAgent)
    register_agent("scene", SceneAgent)
    register_agent("video", VideoAgent)
    register_agent("tts", TTSAgent)
    register_agent("subtitle", SubtitleAgent)
    register_agent("bgm", BGMAgent)
    register_agent("composite", CompositeAgent)

    logger.info(f"✅ 9大智能体全部注册完成")


# 自动初始化
init_registry()
