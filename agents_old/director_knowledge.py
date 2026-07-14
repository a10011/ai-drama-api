"""总导演知识库引擎

功能：
1. 从 knowledge/ 目录加载专业知识文件
2. 根据剧本场景类型自动匹配相关知识
3. 支持项目经验积累（self learning）
4. 支持手动喂知识（learn action）
5. 支持大片/短剧模式切换
"""

import json
import os
import time
import glob
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

# 知识库目录
KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "knowledge")
EXPERIENCE_FILE = os.path.join(KNOWLEDGE_DIR, "_experiences.json")


def load_all_knowledge() -> str:
    """从 knowledge/ 目录加载所有 .md 文件"""
    if not os.path.exists(KNOWLEDGE_DIR):
        return ""

    files = sorted(glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md")))
    snippets = []

    for fp in files:
        basename = os.path.basename(fp)
        if basename.startswith("_"):
            continue  # 跳过私有文件
        try:
            with open(fp, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if content:
                # 提取标题
                title = basename.replace("_", " ").replace(".md", "")
                # 取前200个字符总结主题
                header = content.split("\n")[0] if content else title
                snippets.append(f"【{title}】\n{content[:8000]}")
        except Exception as e:
            logger.warning(f"加载知识文件失败 {basename}: {e}")

    return "\n\n---\n\n".join(snippets) if snippets else ""


def load_knowledge_for_shot(shot: dict, knowledge_text: str) -> str:
    """根据镜头内容提取相关知识片段"""
    if not knowledge_text:
        return ""

    # 提取场景关键词
    scene_desc = shot.get("scene_description", shot.get("description", ""))
    emotion = shot.get("_emotion", "")
    atmosphere = shot.get("_atmosphere", "")

    keywords = []
    if emotion:
        keywords.append(emotion)
    if atmosphere:
        keywords.append(atmosphere)

    # 简单关键词过滤 — 返回包含关键词的段落
    lines = knowledge_text.split("\n")
    relevant = []
    for line in lines:
        for kw in keywords:
            if kw and kw in line:
                relevant.append(line)
                break

    return "\n".join(relevant[:30]) if relevant else knowledge_text[:2000]


def load_experiences(mode: str = "all") -> List[dict]:
    """加载历史项目经验"""
    if not os.path.exists(EXPERIENCE_FILE):
        return []

    try:
        with open(EXPERIENCE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if mode == "all":
            return data
        return [e for e in data if e.get("mode", "短剧") == mode]
    except Exception as e:
        logger.warning(f"加载经验文件失败: {e}")
        return []


def save_experience(experience: dict):
    """保存一次项目经验"""
    # 确保目录存在
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

    experiences = load_experiences()
    experiences.append({
        "timestamp": datetime.now().isoformat(),
        "project_id": experience.get("project_id", ""),
        "mode": experience.get("mode", "短剧"),
        "script_genre": experience.get("genre", ""),
        "shot_count": experience.get("shot_count", 0),
        "success_rate": experience.get("success_rate", 0),
        "good_practices": experience.get("good_practices", []),
        "bad_practices": experience.get("bad_practices", []),
        "agent_feedbacks": experience.get("feedbacks", []),
        "user_notes": experience.get("notes", ""),
        "lessons_learned": experience.get("lessons", []),
    })

    # 只保留最近100条
    if len(experiences) > 100:
        experiences = experiences[-100:]

    try:
        with open(EXPERIENCE_FILE, "w", encoding="utf-8") as f:
            json.dump(experiences, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存经验失败: {e}")


def get_recent_experiences(limit: int = 5) -> str:
    """获取最近的几个项目的经验总结"""
    exps = load_experiences()
    if not exps:
        return "暂无积累经验。"

    recent = exps[-limit:]
    lines = ["【近期项目经验总结】"]
    for i, e in enumerate(recent):
        lessons = e.get("lessons_learned", [])
        goods = e.get("good_practices", [])
        bads = e.get("bad_practices", [])
        notes = e.get("user_notes", "")

        lines.append(f"\n项目{i+1}({e.get('mode','短剧')}/{e.get('script_genre','未知')}):")
        if goods:
            lines.append(f"  ✅ 好的: {'; '.join(goods[:3])}")
        if bads:
            lines.append(f"  ❌ 问题: {'; '.join(bads[:3])}")
        if lessons:
            lines.append(f"  📖 经验: {'; '.join(lessons[:3])}")
        if notes:
            lines.append(f"  📝 备注: {notes}")

    return "\n".join(lines)


def learn_from_user(text: str) -> dict:
    """手动喂知识给导演
    用户发来一段话/文章/经验，自动写入知识库
    """
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)

    # 自动检测主题
    topics = {
        "构图": ["构图", "三分法", "黄金分割", "对称", "景别", "镜头"],
        "灯光": ["灯光", "打光", "光影", "照明", "布光", "逆光", "侧光", "硬光", "柔光"],
        "颜色": ["颜色", "色彩", "色调", "色温", "饱和度", "配色", "冷暖"],
        "运镜": ["运镜", "镜头运动", "推拉", "摇移", "跟拍", "斯坦尼康"],
        "情绪": ["情绪", "表情", "心理", "演绎", "表演"],
        "剪辑": ["剪辑", "转场", "节奏", "蒙太奇", "跳切"],
        "音频": ["配音", "BGM", "音效", "配乐", "旁白", "音乐", "声效"],
        "剧本": ["剧本", "对白", "台词", "人设", "剧情", "叙事"],
    }

    detected = []
    for topic, keywords in topics.items():
        for kw in keywords:
            if kw in text:
                detected.append(topic)
                break

    topic_name = "手动知识_" + "_".join(set(detected)) if detected else "手动知识_通用"
    # 取文本前30个字符做标识
    text_prefix = text.strip()[:30].replace("\n", " ").replace("/", "_").replace(" ", "_")
    filename = f"{topic_name}_{text_prefix}.md"
    # 替换特殊字符
    filename = "".join(c for c in filename if c.isalnum() or c in "._-")
    if len(filename) > 100:
        filename = filename[:100]

    filepath = os.path.join(KNOWLEDGE_DIR, filename)

    try:
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# 手动知识: {', '.join(set(detected)) if detected else '通用'}\n\n")
            f.write(f"> 收录时间: {datetime.now().isoformat()}\n\n")
            f.write(text.strip())
        return {"success": True, "filename": filename, "topics": detected, "path": filepath}
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_builtin_prompt_suffix(mode: str = "短剧", shot: Optional[dict] = None,
                               knowledge_text: str = "") -> str:
    """为导演 prompt 附加上下文知识"""
    parts = []

    # 1. 知识库内容
    if knowledge_text:
        relevant = load_knowledge_for_shot(shot or {}, knowledge_text) if shot else knowledge_text[:4000]
        if relevant:
            parts.append(f"【参考知识库】\n{relevant}")

    # 2. 历史经验
    experiences = get_recent_experiences(3)
    if experiences and experiences != "暂无积累经验。":
        parts.append(f"【近期项目经验】\n{experiences}")

    # 3. 模式说明
    if mode == "大片":
        parts.append("【当前模式: 大片】— 请使用电影级别的镜头语言、构图规范、光影设计。"
                      "注意画面质感、深度、艺术性。情绪表达要有层次和留白。")

    return "\n\n".join(parts) if parts else ""
