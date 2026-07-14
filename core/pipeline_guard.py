"""
PipelineGuard — 轻量管线监督者（纯规则校验，不烧 token）
每个阶段完成后自动检查，不合格打回重做。
"""
import json
import logging
from typing import Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

# 违禁词黑名单
BLOCKED_WORDS = [
    "九死一生", "永诀", "赴死", "崩溃痛哭", "伤口", "血迹", "血淋淋",
    "伤痕", "伤疤", "泪痕", "脏污", "衣衫褴褛", "现代短发", "寸头", "染发",
    "蒙毅", "玉漱",
]


class PipelineGuard:
    """管线监督者：每个阶段完成后调 check()，返回 (passed, reason)"""

    @staticmethod
    def check_storyboard(result: dict) -> Tuple[bool, str]:
        """检查分镜输出"""
        shots = result.get("shots", result.get("scenes", []))
        if not shots:
            return False, "分镜为空"

        issues = []
        for i, s in enumerate(shots):
            sid = s.get("id", i + 1)
            if not s.get("description"):
                issues.append(f"镜{sid}: 缺场景描述")
            # focus_character 仅记录不阻断（远景/空镜/群像可省略）
            if not s.get("focus_character"):
                logger.info(f"[Guard] 镜{sid}: 无focus_character（远景/群像允许）")
            if not s.get("scene"):
                issues.append(f"镜{sid}: 缺 scene 字段")

        if issues:
            return False, "; ".join(issues[:5])

        return True, f"{len(shots)}镜通过"

    @staticmethod
    def check_characters(result: dict, source_chars: list = None) -> Tuple[bool, str]:
        """检查角色肖像：数量、违禁描述"""
        portraits = result.get("portraits", [])
        if not portraits:
            return True, "无角色数据（可能跳过）"

        # 检查违禁词
        for p in portraits:
            name = p.get("name", "?")
            desc = json.dumps(p, ensure_ascii=False)
            for w in BLOCKED_WORDS:
                if w in desc:
                    return False, f"角色[{name}]含违禁词: {w}"

        # 数量匹配
        if source_chars:
            src_names = {c.get("name", "") for c in source_chars if c.get("name")}
            portrait_names = {p.get("name", "") for p in portraits}
            missing = src_names - portrait_names
            if missing:
                return False, f"缺角色肖像: {missing}"

        return True, f"{len(portraits)}角色通过"

    @staticmethod
    def check_scenes(result: dict, expected_shots: list = None) -> Tuple[bool, str]:
        """检查场景图：数量、违禁词"""
        images = result.get("scene_images", result.get("images", []))
        if not images:
            return True, "无场景图（可能跳过）"

        # 检查违禁词
        for img in images:
            prompt = img.get("prompt", "") if isinstance(img, dict) else str(img)
            for w in BLOCKED_WORDS:
                if w in prompt:
                    return False, f"场景图含违禁词: {w}"

        # 数量匹配：不阻断，缺失的让用户单独补生成

        return True, f"{len(images) if isinstance(images, list) else '?'}场景图通过"

    @staticmethod
    def check_audio(result: dict, scenes: list = None) -> Tuple[bool, str]:
        """检查配音：台词是否都有对应 TTS"""
        audio_files = result.get("audio_files", [])
        if not audio_files:
            return True, "无配音数据"

        if scenes:
            dialogue_count = sum(1 for s in scenes if s.get("dialogue"))
            if len(audio_files) < dialogue_count:
                return False, f"配音不足: {len(audio_files)}/{dialogue_count}"

        return True, f"{len(audio_files)}条配音通过"

    @staticmethod
    def check_video(result: dict, scenes: list = None) -> Tuple[bool, str]:
        """检查视频：片段数、URL 有效性"""
        videos = result.get("videos", result.get("clips", []))
        if not videos:
            return False, "无视频输出"

        failed = []
        for v in videos:
            url = v.get("url", v.get("video_url", "")) if isinstance(v, dict) else v
            if not url:
                failed.append(v.get("shot_id", "?"))

        if failed:
            return False, f"{len(failed)}个视频片段生成失败"

        if scenes and len(videos) < len(scenes):
            return False, f"视频片段不足: {len(videos)}/{len(scenes)}"

        return True, f"{len(videos)}视频片段通过"

    @staticmethod
    def check_safety(text: str) -> Tuple[bool, str]:
        """通用安全审查"""
        for w in BLOCKED_WORDS:
            if w in text:
                return False, f"含违禁词: {w}"
        return True, "安全通过"


# 全局单例
guard = PipelineGuard()
