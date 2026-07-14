"""
agent_video.py v5.0 — 视频生成智能体（完整重写）
架构: VideoPromptBuilder → VideoRouter → SceneAgent -> 多模型视频生成 + 配音合并 + 入库
"""
import json
import time
import logging
import os
import re
import subprocess
import httpx
import shutil
import sqlite3
import random
from typing import Optional, Dict, List, Tuple
from .agent_base_legacy import BaseAgent, AgentResult
from services.ai_providers import _get_key
from utils.storage_path import video_path, local_to_url
from app_config import BASE_URL

logger = logging.getLogger(__name__)

# ─── 阿里云百炼常量 ───────────────────────────────────────
BASE_BAILIAN = "https://dashscope.aliyuncs.com"
WAN2_ENDPOINT = f"{BASE_BAILIAN}/api/v1/services/aigc/video-generation/video-synthesis"
TASK_STATUS = f"{BASE_BAILIAN}/api/v1/tasks/{{task_id}}"
VR_ENDPOINT = f"{BASE_BAILIAN}/api/v1/services/aigc/image2video/video-synthesis/"
PUBLIC_DIR = "/www/wwwroot/ai.mzsh.top/dist"
PUBLIC_URL = BASE_URL  # 统一从 app_config 读取

# 景别 → 预期时长（秒）映射
_SHOT_DURATION_MAP = {
    "特写": (5, 5),       # 特写要给足时间看清表情/细节
    "大特写": (5, 5),
    "近景": (5, 5),       # 近景看清角色表演
    "中景": (5, 5),       # 中景看清互动
    "全景": (5, 5),       # 全景交代空间
    "远景": (5, 5),       # 远景大场面
    "大远景": (5, 5),     # 航拍/大全景
}

# 景别 → 运动幅度（0~1）
_SHOT_MOTION_INTENSITY = {
    "远景": 0.7,
    "全景": 0.7,
    "中景": 0.6,
    "近景": 0.5,
    "特写": 0.4,
    "大特写": 0.4,
}

# 景别 → 英文描述
_SHOT_TYPE_EN = {
    "远景": "wide establishing shot showing the full environment and character scale",
    "全景": "full shot framing the entire body with surrounding space",
    "中景": "medium shot from the waist up focusing on character interaction",
    "近景": "tight close-up shot filling the frame with the character face and upper body, every micro-expression clearly visible, intimate and detailed",
    "特写": "extreme close-up filling the entire screen with eyes or face or hands, showing the finest details of emotion, skin texture, sweat and tension, the camera stays close and does not pull away",
    "大特写": "macro extreme close-up on a single detail like eyes or weapon or bleeding wound, maximum intimacy and detail",
    "特写": "extreme close-up on specific detail eyes hands or object",
    "大特写": "macro close-up extreme detail shot",
}

# 运镜 → 英文描述
_CAMERA_MOVEMENT_EN = {
    "固定": "completely still static locked-off shot no camera movement at all for entire duration tripod shot",
    "推": "slow steady push-in camera gradually moves closer intensifying emotional focus creating intimacy",
    "拉": "gentle pull-back camera slowly retreats revealing more space creating sense of distance isolation or awe",
    "摇": "smooth horizontal pan camera sweeps across the scene following action or revealing landscape",
    "移": "vertical tilt movement camera tilts up or down revealing vertical space",
    "跟": "tracking shot camera smoothly follows the subject's movement maintaining consistent framing",
    "升降": "slow crane shot camera rises or descends changing perspective from high to low or vice versa",
    "环绕": "orbital rotation shot camera circles around the subject creating dramatic three-dimensional effect",
    "航拍(无人机)": "aerial drone shot camera flies high above looking down at the scene from birds eye perspective revealing grand landscape and spatial layout",
    "穿越机": "crane-through fly-through shot camera rapidly moves through obstacles crevices or narrow spaces creating immersive first-person momentum",
    "飞越": "flyover shot camera arcs over the subject from one side to the other smoothly traversing the environment",
    "手持晃动": "handheld shaky cam intentional camera jitter and natural sway creating documentary-style immediacy tension and raw realism",
    "斯坦尼康": "steadicam gimbal stabilized follow shot camera glides smoothly behind or alongside the subject maintaining steady frame while moving through environment",
    "大范围移动(滑动变焦)": "dolly zoom vertigo effect camera physically moves toward subject while zooming out or vice versa creating disorienting perspective distortion",
    "推进环绕": "push-in combined with orbital rotation camera moves closer while circling the subject intensifying focus from all angles",
    "横移跟拍": "lateral tracking dolly shot camera moves sideways parallel to subject keeping consistent distance and framing",
    "长镜头": "long take continuous unbroken shot following character through multiple actions and spaces without cut creating temporal immersion",
    "微距": "macro extreme close-up camera focuses on minute details with shallow depth of field creating abstract intimate perspective",
}

# 角色外貌描述关键词（视频 prompt 禁止出现）
_CHARACTER_APPEARANCE_KEYWORDS = [
    "帅气", "美丽", "漂亮", "英俊", "秀气", "浓眉", "大眼",
    "长发", "短发", "卷发", "直发", "马尾", "辫子", "刘海",
    "面容", "脸庞", "脸型", "瓜子脸", "圆脸", "方脸",
    "皮肤", "肤色", "白皙", "小麦色", "古铜",
    "嘴唇", "鼻梁", "眼睛", "睫毛", "眉毛",
    "身材", "苗条", "健壮", "魁梧", "丰满", "纤细",
    "穿着", "服装", "衣服", "衣", "裙", "裤", "袍",
    "银甲", "红袍", "白袍", "盔甲", "铠甲", "战袍",
    "赤发", "白发", "黑发", "金发",
]

def _filter_character_appearance(text: str) -> str:
    """从 prompt 中移除角色外貌描述"""
    for kw in _CHARACTER_APPEARANCE_KEYWORDS:
        text = re.sub(r'[^。；；\n]*' + re.escape(kw) + r'[^。；；\n]*[。；；\n]', '', text)
    return text.strip()

# 角度 → 英文描述
_CAMERA_ANGLE_EN = {
    "平视": "eye-level angle neutral balanced perspective connecting viewer to character",
    "仰视": "low-angle shot looking up making subject appear powerful dominant imposing",
    "俯视": "high-angle overhead shot looking down making subject appear vulnerable small isolated",
    "倾斜": "dutch tilt canted angle creating disorientation unease psychological tension",
}


# ═══════════════════════════════════════════════════════════
# [配脑子] 情绪 → 具体肢体/面部动作描写（视频模型理解不了抽象情绪词，
# 必须转成可视化的具体动作，这是视频生成质量的关键）
# ═══════════════════════════════════════════════════════════
EMOTION_ACTION_MAP = {
    "愤怒": "eyes wide open with lowered knitted brows, jaw clenched with bulging cheeks, right hand gripping tightly with whitening knuckles, rapid heaving chest, facial muscles twitching slightly",
    "生气": "brows furrowed, lips pressed thin, nostrils slightly flared, sharp jabbing finger pointing, body leaning forward aggressively",
    "悲伤": "eyes reddening with tears welling up but not falling, lips trembling slightly, hands falling limply, shoulders quivering, looking down",
    "难过": "head bowed low, eyes glistening with unshed tears, lower lip caught between teeth, slow deep sigh, hands clutching fabric",
    "紧张": "pupils dilated, fine sweat beads on forehead, adam's apple bobbing, fingers tapping rapidly on surface, eyes darting left and right",
    "激动": "eyes shining brightly, mouth open wide, both hands gesturing animatedly, body leaning forward, eyebrows raised high",
    "开心": "eyes curving into crescents, wide genuine smile showing teeth, slight head tilt, hands clapping or gesturing upward, relaxed shoulders",
    "委屈": "lower lip protruding slightly, eyes looking up through lashes, brows slanting upward inwardly, small hesitant movements, hugging self",
    "温柔": "soft gaze with gently crinkled eyes, warm slight smile, slow deliberate movements, head tilted slightly, hand reaching out gently",
    "羞涩": "eyes darting away then back, cheeks flushing pink, hand touching back of neck or tucking hair behind ear, slight smile suppressed",
    "冷漠": "flat unblinking stare, lips in a straight line, body perfectly still, chin slightly raised, deliberate slow movements",
    "无奈": "eyes rolling slightly, deep sigh, shoulders dropping, corner of mouth twitching downward, hand rubbing temple",
    "惊讶": "eyes widening suddenly, mouth falling open, eyebrows shooting up, slight backward step, hand rising to cover mouth",
    "恐惧": "eyes wide with contracted pupils, face draining of color, body shrinking back, hands raised defensively, rapid shallow breathing",
    "得意": "smirk with one corner of mouth raised, chin tilted up, arms crossed or hands behind back, slow confident nod",
    "轻蔑": "upper lip curled, nostril flare, eyes half-lidded looking down, single raised eyebrow, dismissive hand wave",
}


# ═══════════════════════════════════════════════════════════
# 1. 视频 Prompt 构建器
# ═══════════════════════════════════════════════════════════

class VideoPromptBuilder:
    """
    三步构建最终视频 prompt
    Part A：场景上下文（中文）— scene_prompt, emotion, weather, dialogue
    Part B：电影摄影语言（英文）— 运镜/景别/角度/光线 英文描述
    Part C：时长标记（根据景别估算秒数）
    """

    @staticmethod
    def _build_part_a(shot: dict) -> str:
        """Part A：场景上下文（中文）"""
        parts = []

        # 氛围（天气+光线）
        weather = shot.get("weather", "").strip()
        lighting = shot.get("lighting", "").strip()
        atmosphere = []
        if weather:
            atmosphere.append(weather)
        if lighting:
            atmosphere.append(lighting)
        if atmosphere:
            parts.append("Atmosphere: " + "，".join(atmosphere))

        # 情绪
        emotion = shot.get("emotion", "").strip()
        if emotion:
            parts.append(f"Emotion: {emotion}")

        # 核心场景描述（中文）
        desc = shot.get("scene_description", shot.get("description", "")).strip()
        if desc:
            # 打斗场景加强动作描述
            fight_keywords = ["打", "杀", "冲", "劈", "砍", "刺", "挡", "踢", "挥", "斩", "搏", "战", "攻", "防"]
            is_action = any(kw in desc for kw in fight_keywords)
            if is_action:
                desc += "，physically intense combat scene with fast dynamic movements, characters actively fighting with full body motion, weapons clashing, dust and debris flying, realistic weight and impact in every strike"
            parts.append(f"Scene: {desc}")

        return " | ".join(parts)

    @staticmethod
    def _build_part_b(shot: dict) -> str:
        """Part B：电影摄影语言（英文）"""
        movement = shot.get("camera_movement", "")
        angle = shot.get("camera_angle", "")
        shot_type = shot.get("shot_type", "")
        emotion = shot.get("emotion", "").strip()

        st_en = _SHOT_TYPE_EN.get(shot_type, "")
        mv_en = _CAMERA_MOVEMENT_EN.get(movement, "")
        ag_en = _CAMERA_ANGLE_EN.get(angle, "")

        direction_parts = []
        if mv_en:
            direction_parts.append(mv_en)
        if st_en:
            direction_parts.append(st_en)
        if ag_en:
            direction_parts.append(ag_en)
        # [配脑子] 情绪转成具体可视化的肢体/面部动作（视频模型理解不了抽象情绪词）
        if emotion:
            action_desc = EMOTION_ACTION_MAP.get(emotion, "")
            if action_desc:
                direction_parts.append(f"character action: {action_desc}")
            else:
                direction_parts.append(f"emotional tone: {emotion}")

        return ". ".join(direction_parts)

    @staticmethod
    def _build_part_c(shot: dict) -> str:
        """Part C：时长标记（根据景别估算秒数）"""
        dur = shot.get("estimated_duration", 4)
        return f"Duration: ~{dur}s"

    @staticmethod
    def _estimate_duration(shot: dict) -> int:
        """根据剧情内容智能判断时长（seedance 最长5秒，所以统一5秒）
        但标记节奏类型，供合成时决定转场速度"""
        desc = (shot.get("description", "") + shot.get("dialogue", "") + shot.get("scene", "")).lower()
        shot_type = shot.get("shot_type", "")

        # 判断节奏类型
        if any(kw in desc for kw in ["对峙", "蓄力", "沉默", "凝视", "等待", "静止", "预备", "缓缓", "慢慢"]):
            shot["pace"] = "slow"  # 蓄力对峙：慢节奏，张力积累
        elif any(kw in desc for kw in ["冲锋", "撞击", "爆炸", "瞬间", "猛地", "突然", "爆发"]):
            shot["pace"] = "fast"  # 冲撞爆发：快节奏
        elif any(kw in desc for kw in ["说", "道", "喊", "问", "答", "笑", "哭", "叹"]):
            shot["pace"] = "dialogue"  # 对话：中等
        else:
            shot["pace"] = "normal"

        # seedance 最长5秒，所有镜头统一5秒（宁可长不可短，让观众看清）
        dur = 5
        shot["estimated_duration"] = dur
        return dur

    @staticmethod
    def _fix_action_chain(text: str) -> str:
        """修正动作描述，确保动作连贯完整（过程→结果）。
        模型需要知道动作怎么开始、怎么进行、怎么结束。"""
        if not text:
            return text
        fixes = [
            # 眼泪：必须是"溢出→滑落→滴落"的过程，不能黏在脸上
            ("流泪", "泪水从眼角缓缓溢出，沿脸颊滑落"),
            ("落泪", "泪水在眼眶积聚，从眼角溢出沿脸颊缓缓滑下，最后从下巴滴落"),
            ("泪流满面", "泪水从双眼泪腺溢出，沿脸颊两侧缓缓滑落，在下巴汇成水珠滴落"),
            ("哭了", "眼眶泛红，泪水从眼角溢出，沿脸颊缓缓滑落到下巴"),
            ("含泪", "泪水在眼眶里打转但未落下，眼角微微泛红"),
            # 笑：要有从无到有的过程
            ("微笑", "嘴角缓缓上扬，眼角微微弯起"),
            ("笑了", "嘴角从紧抿到缓缓上扬，眼角微微弯起"),
            # 摔倒：要有过程
            ("摔在地上", "身体失去平衡向前倾，双膝先触地，然后整个人趴倒"),
            ("倒在地上", "身体摇晃后失去平衡，侧身倒下，手撑了一下没撑住"),
            # 起身：要有过程
            ("站起来", "双手撑住桌面/扶手，身体缓缓升起"),
            # 转身：要有过程
            ("转身", "身体缓缓转动，从正面变为侧面再到背影"),
            # 握拳：要有过程
            ("握拳", "手指从张开到缓缓攥紧，指节发白"),
        ]
        for bad, good in fixes:
            if bad in text:
                text = text.replace(bad, good)
        return text

    @staticmethod
    def build_prompt(shot: dict) -> str:
        """视频 prompt 构建——核心原则：动作连贯完整，模型听得懂。
        不写死字数，但只保留模型需要的：画面动作链+运镜+表演+禁止项。"""
        desc = shot.get("description", "")
        director_shot = shot.get("director_shot", "")
        shot_type = shot.get("shot_type", "")
        movement = shot.get("camera_movement", "")
        emotion = shot.get("emotion", "")
        lighting = shot.get("lighting", "")
        dialogue = shot.get("dialogue", "")
        inner_voice = shot.get("inner_voice", "")
        genre = shot.get("_genre", "") or shot.get("genre", "")

        # 1. 画面动作链（最核心）— 用 director_shot 优先，没有用 description
        # 修正动作描述确保连贯完整（眼泪要"溢出→滑落→滴落"不能黏脸上）
        action = VideoPromptBuilder._fix_action_chain(director_shot or desc)

        # 2. 运镜+景别（英文，模型理解更好）
        shot_en = _SHOT_TYPE_EN.get(shot_type, "")
        cam_en = ""
        if movement:
            cam_en = _CAMERA_MOVEMENT_EN.get(movement, movement)
        cam_parts = [p for p in [cam_en, shot_en] if p]

        # 3. 台词/内心独白（影响角色嘴部动作）
        speak_hint = ""
        if dialogue and dialogue != "(无台词)":
            speak_hint = "character speaking, lips moving naturally"
        elif inner_voice and inner_voice != "(无)":
            speak_hint = "mouth closed, eyes showing inner emotion through micro-expressions"

        # 4. 情绪转英文动作（模型理解动作不理解抽象情绪词）
        emotion_action = EMOTION_ACTION_MAP.get(emotion, "")

        # 5. 特效（精简到关键词，不写长描述）
        vfx = ""
        desc_lower = (desc + director_shot).lower()
        if any(k in desc_lower for k in ["箭", "arrow"]): vfx = "arrow rain effect"
        elif any(k in desc_lower for k in ["火", "fire", "爆炸"]): vfx = "fire and smoke"
        elif any(k in desc_lower for k in ["雨", "rain"]): vfx = "rain drops falling"
        elif any(k in desc_lower for k in ["马", "horse", "骑兵"]): vfx = "galloping horses with dust"
        elif any(k in desc_lower for k in ["刀", "剑", "劈", "砍", "sword"]): vfx = "weapon sparks"

        # 6. 光线（简短英文）
        light_hint = ""
        if lighting:
            light_map = {"侧光": "side lighting", "逆光": "backlight", "暖光": "warm light",
                         "冷光": "cold light", "自然光": "natural light", "剪影": "silhouette",
                         "金黄昏": "golden hour", "冷月": "moonlight"}
            light_hint = light_map.get(lighting, "")

        # 拼接（不写死字数，但结构清晰，模型容易理解）
        parts = []
        if action:
            parts.append(action)  # 动作链放最前面（最高优先级）
        if cam_parts:
            parts.append(" | Camera: " + ", ".join(cam_parts))
        if emotion_action:
            parts.append(" | " + emotion_action)
        if speak_hint:
            parts.append(" | " + speak_hint)
        if vfx:
            parts.append(" | " + vfx)
        if light_hint:
            parts.append(" | " + light_hint)
        parts.append(" | realistic human, natural physics, cinematic")
        # 类型锁 + 中式古战场兜底：genre 判错或为空时，关键词检测补上战场描述
        _bkw = ["骑兵","步兵","军阵","战马","长戈","箭雨","号角","铁骑","冲锋","盾","甲","旗","主帅","荒原","战场","厮杀"]
        _is_battle = any(kw in (desc + director_shot) for kw in _bkw)
        if genre or _is_battle:
            from agents.genre_lock import get_genre_constraint
            _lg = genre if genre else ("" if _is_battle else "")
            gc = get_genre_constraint(_lg) if _lg else {}
            forbidden = gc.get("forbidden", [])
            face_c = gc.get("face_constraint", "")
            visual = gc.get("visual_style", "")
            warfare = gc.get("warfare_hint", "")
            if _is_battle:
                if not warfare:
                    warfare = "写实中式古代冷兵器古战场,华夏秦汉风格,中式玄铁札甲明光铠,环首刀长戈陌刀,中原战马配皮质马铠,刺绣龙纹玄鸟的汉字军旗,青铜战鼓,烽火台,黄土边关荒原"
                if not visual:
                    visual = "张艺谋英雄赤壁式中国古战场影视风格,土黄赭石暗红色调,水墨厚重国风写实"
                if not forbidden:
                    forbidden = ["medieval knight","plate armor","european shield","crusader","castle"]
            if warfare:
                parts.append(" | " + warfare)
            if forbidden:
                parts.append(" | NO " + ", ".join(forbidden[:5]))
            if face_c:
                parts.append(" | " + face_c)
            if visual:
                parts.append(" | " + visual)

        result = " | ".join(parts)
        # 最终截断到800字（战场镜头需要更多描述空间）
        if len(result) > 800:
            result = result[:800]
        logger.info(f"[VideoPromptBuilder] 视频 prompt ({len(result)}字): {result[:120]}...")
        return result
        all_parts.append(part_d)
        if part_e:
            all_parts.append(f"VFX: {part_e}")

        # Part F: 战争场景史诗氛围加强
        desc_full = (shot.get("description", "") + shot.get("scene", "") + shot.get("emotion", "")).lower()
        war_keywords = ["军", "战", "冲锋", "骑兵", "阵", "大军", "对冲", "杀", "攻城", "对决", "单挑", "震撼", "爆裂", "热血", "勇猛"]
        is_war_scene = any(kw in desc_full for kw in war_keywords)
        if is_war_scene:
            movement = shot.get("camera_movement", "")
            war_epic = ""
            if "航拍" in movement or "俯冲" in movement:
                war_epic = "epic aerial dive shot descending into the heart of a massive battlefield, THOUSANDS of soldiers filling the ENTIRE frame edge to edge as far as the eye can see, two colossal armies colliding with devastating earth-shaking impact, massive cavalry charge with hundreds of horses thundering across the plain kicking up towering dust clouds, war banners stretching to the horizon, this is a GRAND WAR SCENE of overwhelming scale, NOT a retreat, NOT a small skirmish, victorious aggressive momentum, the camera captures the IMMENSITY of tens of thousands of warriors, like Lord of the Rings Helms Deep charge energy"
            elif "手持" in movement or "跟拍" in movement:
                war_epic = "intense close combat following a warrior charging through enemy lines, powerful and dominant, riding forward with unstoppable momentum, enemies falling before him, heroic charge energy, adrenaline-pumping action, the background shows thousands more soldiers fighting"
            elif "大全景" in shot.get("shot_type", "") or "远景" in shot.get("shot_type", ""):
                war_epic = "epic extreme wide shot of a massive ancient battlefield, TWO VAST ARMIES stretching across the entire horizon clashing like tsunami waves, THOUSANDS of armored soldiers in massive formation charging forward, cavalry regiments thundering with earth-shaking hooves, the scale is OVERWHELMING like a historical war epic movie, soldiers fill every inch of the frame, this looks like a VICTORIOUS CHARGE of a massive army, NOT a small group, NOT a defeat"
            else:
                war_epic = "powerful war scene with aggressive momentum, soldiers fighting fiercely with full force, heroic and dominant energy, cinematic battle intensity"
            if war_epic:
                all_parts.append(f"Epic: {war_epic}")

        result = " | ".join(all_parts)
        logger.info(f"[VideoPromptBuilder] 视频 prompt ({len(result)}字): {result[:120]}...")
        return result


# ═══════════════════════════════════════════════════════════
# 2. 视频路由决策器
# ═══════════════════════════════════════════════════════════

class VideoRouter:
    """
    根据 shot 类型和可用素材自动决策
    I2V（图生视频）：近景/特写/中景 → 用角色图或场景图做参考
    T2V（文生视频）：远景/空镜/全景 → 纯 prompt
    """

    @staticmethod
    def _should_use_i2v(shot: dict) -> Tuple[bool, str]:
        """
        Smart I2V routing
        Returns (use_i2v: bool, reason: str)
        """
        shot_type = shot.get("shot_type", "")
        dialogue = shot.get("dialogue", "")
        action = shot.get("description", shot.get("action", ""))
        characters = shot.get("characters", [])
        emotion = shot.get("emotion", "")

        # Rule 1: 有对话或角色动作 → I2V
        if dialogue and dialogue not in ("", "(无台词)"):
            return True, "dialogue scene"
        if action and any(kw in action for kw in ("说", "看", "走", "站", "坐", "拿", "抱", "推", "拉")):
            return True, "character action"
        if emotion and emotion not in ("", "平静"):
            return True, "emotional expression"
        if characters and len(str(characters)) > 3:
            return True, "has characters"

        # Rule 2: 近景/特写 → I2V（人脸细节重要）
        if shot_type in ("近景", "特写", "大特写", "中景"):
            return True, "framed shot"

        # Rule 2b: 导演在 director_shot 里规划了"定脸/锁脸/看清脸"的大全景 → I2V
        # 真实拍剧标准：开场拉近后定住人脸让观众记住，这种镜头必须锁脸
        director_shot = shot.get("director_shot", "")
        if director_shot and any(kw in director_shot for kw in ("定在", "定住", "锁脸", "看清脸", "看清.脸", "面部特写", "脸部", "正脸", "脸庞")):
            return True, "director planned face lock"

        # Rule 3: 远景/空镜 → T2V
        return False, "establishing shot, text suffices"

    @staticmethod
    def decide(shot: dict, scene_image: str = "", character_image: str = "") -> Tuple[str, str, str]:
        """
        路由决策
        Returns (mode: str, reference_image: str, prefix_prompt: str)
        mode: "i2v" or "t2v"
        reference_image: i2v 的参考图 URL（空字符串表示无）
        prefix_prompt: t2v 时追加的前缀
        """
        use_i2v, reason = VideoRouter._should_use_i2v(shot)
        logger.info(f"[VideoRouter] 路由决策: {'I2V' if use_i2v else 'T2V'} ({reason})")

        if not use_i2v:
            return "t2v", "", "cinematic photorealistic vertical 9:16 live action film. "

        # I2V: 选择参考图
        # 核心原则：有人物的镜头优先用角色锁脸图（character_image），
        # 让角色面部清晰可见。场景图(scene_image)里人物太小，锁脸无效。
        shot_type = shot.get("shot_type", "")
        is_closeup = shot_type in ("近景", "特写", "大特写")
        is_medium = shot_type in ("中景", "近景", "特写", "大特写")

        # 近景/特写/中景 → 角色锁脸图（脸要清晰）
        if is_medium and character_image:
            ref_img = character_image
            logger.info(f"[VideoRouter] 参考图=角色锁脸图(近中景) shot_type={shot_type}")
        # 全景/远景 有人在 → 也优先角色图（比场景图锁脸效果好）
        elif character_image:
            ref_img = character_image
            logger.info(f"[VideoRouter] 参考图=角色锁脸图(全景远景) shot_type={shot_type}")
        # 没有角色图 → 用场景图
        elif scene_image:
            ref_img = scene_image
            logger.info(f"[VideoRouter] 参考图=场景图(无角色图)")
        else:
            ref_img = ""

        if not ref_img:
            logger.warning("[VideoRouter] I2V 模式但无参考图，降级 T2V")
            return "t2v", "", "cinematic photorealistic vertical 9:16 live action film. "

        return "i2v", ref_img, ""


# ═══════════════════════════════════════════════════════════
# 3. 辅助函数
# ═══════════════════════════════════════════════════════════

def local_to_url(file_path: str) -> str:
    """本地路径 → 公网 URL。纯路径转换，不检查文件存在性。
    统一委托 utils.path_util，移除早期硬编码域名。
    非 /storage/ 的本地文件仍 copy 到 PUBLIC_DIR 保证可访问。"""
    if not file_path:
        return ""
    if file_path.startswith("http"):
        return file_path
    # /storage/ 或 /www/wwwroot/ 路径 → 直接转换
    if "/storage/" in file_path or file_path.startswith("/www/wwwroot/"):
        return local_path_to_url(file_path)
    # 其它本地文件（如 /tmp/）→ copy 到 PUBLIC_DIR 再返回 URL
    filename = f"vr_{int(time.time())}_{os.path.basename(file_path)}"
    dest = os.path.join(PUBLIC_DIR, filename)
    try:
        shutil.copy2(file_path, dest)
    except Exception:
        pass
    return f"{PUBLIC_URL}/{filename}"


def _extract_last_frame(video_url: str, output_path: str) -> str:
    """用 ffmpeg 截取视频最后一帧，返回截图路径"""
    cmd = ["ffmpeg", "-y", "-sseof", "-1", "-i", video_url,
           "-vframes", "1", "-q:v", "2", output_path]
    subprocess.run(cmd, capture_output=True, timeout=30)
    return output_path if os.path.exists(output_path) else ""


def _get_db() -> sqlite3.Connection:
    """获取 media_library 数据库连接"""
    db_path = "/www/wwwroot/api.mzsh.top/data/short_drama.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ═══════════════════════════════════════════════════════════
# 4. 视频生成智能体
# ═══════════════════════════════════════════════════════════

class VideoAgent(BaseAgent):
    name = "视频生成智能体"
    description = "VideoPromptBuilder + VideoRouter + 多模型视频生成 + 配音合并"
    version = "5.0.0"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.api_key = _get_key("aliyun_bailian")
        self._headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        os.makedirs(PUBLIC_DIR, exist_ok=True)
        self._video_user_id = 0  # run() 入口会从 kwargs 覆盖

    # ─── 视频路由代理方法 ─────────────────────────────────

    @staticmethod
    def decide_route(shot: dict, scene_image: str = "", character_image: str = "") -> Tuple[str, str, str]:
        """调用 VideoRouter.decide()"""
        return VideoRouter.decide(shot, scene_image, character_image)

    @staticmethod
    def build_video_prompt(shot: dict) -> str:
        """调用 VideoPromptBuilder.build_prompt()"""
        return VideoPromptBuilder.build_prompt(shot)

    # ─── 配音合并 ────────────────────────────────────────

    def _merge_audio_to_video(self, video_url: str, audio_path: str) -> str:
        """ffmpeg 混音：视频 + 配音 → 合并输出"""
        local_video = f"/tmp/wan_{int(time.time())}_{id(self):x}.mp4"
        merged = None
        try:
            # 下载/拷贝视频源
            if video_url.startswith("http"):
                r = httpx.get(video_url, timeout=60)
                with open(local_video, "wb") as f:
                    f.write(r.content)
            elif os.path.exists(video_url):
                shutil.copy2(video_url, local_video)
            else:
                logger.warning(f"视频源不存在: {video_url}")
                return ""

            merged = f"/tmp/merged_{int(time.time())}_{id(self):x}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-i", local_video, "-i", audio_path,
                "-c:v", "copy", "-c:a", "aac", "-shortest",
                "-map", "0:v:0", "-map", "1:a:0", merged,
            ]
            subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if os.path.exists(merged):
                out_name = f"merged_{int(time.time())}_{id(self):x}.mp4"
                pipeline_id = getattr(self, "_pipeline_id", "")
                pipeline_dir = os.path.join(PUBLIC_DIR, pipeline_id) if pipeline_id else PUBLIC_DIR
                os.makedirs(pipeline_dir, exist_ok=True)
                dest = os.path.join(pipeline_dir, out_name)
                shutil.copy2(merged, dest)
                logger.info(f"[_merge_audio] 成功: {dest}")
                return dest
            return ""
        except Exception as e:
            logger.warning(f"[_merge_audio] 失败: {e}")
            return ""
        finally:
            for p in [local_video, merged]:
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    # ─── 音频增强 ────────────────────────────────────────

    def _boost_audio(self, tts_audio: str) -> str:
        """增强 TTS 音频音量，驱动口型更明显"""
        if not tts_audio or not os.path.exists(tts_audio):
            return ""

        boosted = tts_audio.replace('.mp3', '_boost.mp3').replace('.wav', '_boost.wav')
        try:
            subprocess.run([
                'ffmpeg', '-y', '-i', tts_audio,
                '-af', 'loudnorm=I=-16:TP=-1.5:LRA=11,volume=4dB',
                '-q:a', '2', boosted,
            ], capture_output=True, timeout=30)
            if os.path.exists(boosted) and os.path.getsize(boosted) > 500:
                logger.info(f"音频增强: {tts_audio} -> {boosted} ({os.path.getsize(boosted)} bytes)")
                return boosted
        except Exception as e:
            logger.warning(f"音频增强失败: {e}")
        return tts_audio

    # ─── 对口型调用 ──────────────────────────────────────

    def _vr_submit(self, image_url: str, audio_url: str) -> str:
        """提交 VideoRetalk 对口型任务"""
        payload = {
            "model": "videoretalk",
            "input": {"video_url": image_url, "audio_url": audio_url},
            "parameters": {"video_extension": False},
        }
        with httpx.Client(timeout=60, verify=True) as client:
            resp = client.post(VR_ENDPOINT, json=payload, headers=self._headers)
        if resp.status_code != 200:
            raise Exception(f"VideoRetalk 失败 {resp.status_code}: {resp.text[:200]}")
        return resp.json().get("output", {}).get("task_id", "")

    def _vr_poll(self, task_id: str, max_wait: int = 300) -> str:
        """轮询 VideoRetalk 任务结果"""
        for i in range(max_wait // 10):
            time.sleep(10)
            with httpx.Client(timeout=30, verify=True) as client:
                resp = client.get(
                    TASK_STATUS.format(task_id=task_id),
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
            if resp.status_code != 200:
                continue
            data = resp.json().get("output", {})
            status = data.get("task_status", "")
            logger.info(f"VideoRetalk poll[{i * 10}s]: {status}")
            if status == "SUCCEEDED":
                return data.get("video_url", "")
            elif status in ("FAILED", "UNKNOWN"):
                raise Exception(f"VideoRetalk 失败: {data.get('message', '')}")
        raise Exception("VideoRetalk 超时")

    # ─── 核心视频生成 ────────────────────────────────────

    def generate_video(self, shot: dict, model_routes: dict = None) -> AgentResult:
        """
        视频生成核心流程
        1. 视频 Prompt 构建
        2. 路由决策
        3. 视频生成（多模型 fallback）
        4. 配音合并
        5. 入库
        """
        self.report_progress("分析分镜动作设计...", 10)
        start = time.time()
        duration = shot.get("duration_sec", 5)

        # 内容安全过滤：确保传给视频模型的prompt不会触发审核
        try:
            from agents.content_safety import sanitize_shot
            shot = sanitize_shot(shot)
        except Exception:
            pass

        # 把类型锁注入 shot，供 build_prompt 使用
        _td = getattr(self, "_task_data", {})
        if _td.get("genre"):
            shot["_genre"] = _td["genre"]
        if _td.get("_genre_lock"):
            shot["_genre"] = _td["_genre_lock"]

        # === Step 1: 构建视频 Prompt ===
        video_prompt = VideoPromptBuilder.build_prompt(shot)

        # === 上下文增强 ===
        ctx_prompt = self._build_shot_context(shot)
        full_prompt = video_prompt if video_prompt else ctx_prompt

        # 订单号：每个镜头唯一标识，用于防重复提交
        shot_index = shot.get("shot_index", shot.get("shot_num", 0))
        order_id = f"shot_{shot_index}"
        _pipe_id = shot.get("pipeline_id", "") or getattr(self, "_pipeline_id", "") or ""

        # === Step 2: 路由决策 ===
        scene_image = shot.get("scene_image", shot.get("image_url", shot.get("image", "")))
        character_image = shot.get("character_image", shot.get("portrait_url", shot.get("char_image_url", "")))
        # 多角色锁脸：双人/多人镜头收集所有角色图，R2V 支持1-9张多图锁脸
        # 之前只传第一张 → 双人镜头女主脸被男主盖掉
        character_images = shot.get("character_images", [])
        if not character_images and character_image:
            character_images = [character_image]

        # 音频准备
        tts_audio = shot.get("tts_audio", shot.get("audio_path", shot.get("audio_url", "")))
        audio_public = ""
        if tts_audio and os.path.exists(tts_audio):
            boosted = self._boost_audio(tts_audio)
            audio_public = local_to_url(boosted) if boosted else local_to_url(tts_audio)

        # 路由决策
        mode, ref_image, prefix = VideoRouter.decide(shot, scene_image, character_image)
        public_ref_url = local_to_url(ref_image) if ref_image else ""

        # T2V 模式下追加前缀
        if mode == "t2v":
            full_prompt = prefix + full_prompt

        self.report_progress("生成视频画面...", 60)

        # === Step 3: 视频生成 ===
        from services.model_client import generate_video as _gen_video
        from services.vertical_spec import VERT
        resolution = VERT.VIDEO_RESOLUTION

        logger.info(f"视频生成: mode={mode} resolution={resolution} prompt={full_prompt[:60]}")

        # 注入运动幅度控制 + 分档时长
        shot_type = shot.get("shot_type", "")
        motion = _SHOT_MOTION_INTENSITY.get(shot_type, 0.5)
        # 分档时长：台词镜头10秒、特写8秒、其他5秒（省费用但效果够）
        dialogue = shot.get("dialogue", "")
        if dialogue and dialogue != "(无台词)":
            duration = min(duration, 10)  # 有台词给10秒
        elif shot_type in ("特写", "大特写", "近景"):
            duration = min(duration, 8)   # 特写8秒
        else:
            duration = 5                   # 其他5秒
        kwargs = {"duration": duration}

        # 首帧锁定：角色锁脸图优先（保证脸对），无角色图时才用尾帧衔接（画面连贯）
        if public_ref_url:
            first_frame_url = public_ref_url   # 角色锁脸图 or 场景图（VideoRouter 选的）
        elif shot.get("first_frame"):
            first_frame_url = shot["first_frame"]  # 上一镜尾帧（仅空镜衔接用）
        else:
            first_frame_url = ""

        # 确定性链路：按 VideoRouter 的 mode 显式指定模型
        # i2v(有人物) → happyhorse-r2v 锁脸; t2v(空镜) → happyhorse-t2v
        preferred_model = "happyhorse-r2v" if mode == "i2v" else "happyhorse-t2v"
        # 多角色锁脸：把所有角色图传给 R2V（model_client 的 r2v 分支会读 character_images）
        # 双人镜头蒙毅+玉漱都会锁脸，不再只锁第一个
        if character_images and len(character_images) > 1 and mode == "i2v":
            kwargs["character_images"] = [local_to_url(ci) if ci and not ci.startswith("http") else ci for ci in character_images if ci]
            logger.info(f"[VideoAgent] 多角色锁脸: {len(kwargs['character_images'])}张图 -> {first_frame_url[:50]}")
        result = _gen_video(
            full_prompt or shot.get("dialogue", "角色表演"),
            image_url=first_frame_url,
            audio_url=audio_public,
            preferred=preferred_model,
            timeout=600,
            resolution=resolution,
            order_id=order_id,
            pipeline_id=_pipe_id,
            **kwargs,
        )

        # 提取尾帧，供下一镜衔接
        if result.get("success") and result.get("last_frame_url"):
            self._last_frame_url = result["last_frame_url"]
            logger.info(f"[VideoAgent] 尾帧已获取: {result['last_frame_url'][:60]}")

        if result.get("success") and result.get("url"):
            video_url = result["url"]
            model_used = result.get("model", "unknown")
            logger.info(f"视频生成成功: {model_used} -> {video_url[:80]}")

            # 视频本地化（OSS临时URL会过期，合成时403）
            if video_url and "ai.mzsh.top" not in video_url:
                try:
                    import os as _os, hashlib as _hl, httpx as _hx
                    h = _hl.md5(video_url.encode()).hexdigest()[:12]
                    vdir = "/www/wwwroot/storage/videos/"
                    _os.makedirs(vdir, exist_ok=True)
                    vpath = f"{vdir}video_{h}.mp4"
                    if not _os.path.exists(vpath):
                        with _hx.Client(timeout=120, verify=False) as dl:
                            r = dl.get(video_url)
                            if r.status_code == 200 and len(r.content) > 10000:
                                with open(vpath, "wb") as f:
                                    f.write(r.content)
                                video_url = f"https://ai.mzsh.top/storage/videos/video_{h}.mp4"
                                logger.info(f"[VideoAgent] 视频已本地化: {video_url[:60]}")
                            else:
                                logger.warning(f"[VideoAgent] 视频下载失败: status={r.status_code} size={len(r.content)}")
                    else:
                        video_url = f"https://ai.mzsh.top/storage/videos/video_{h}.mp4"
                except Exception as dl_e:
                    logger.warning(f"[VideoAgent] 视频本地化失败: {dl_e}")

            # === Step 4: 入库 ===
            self._register_video_result(video_url, model_used, shot)

            # wan2.7_i2v 已内嵌口型+配音
            if "wan2.7_i2v" in model_used:
                logger.info(f"对口型视频(已内嵌配音): {model_used}")
                return AgentResult(
                    data={"video_url": video_url, "duration_sec": duration, "model": model_used},
                    duration_ms=int((time.time() - start) * 1000),
                )

            # 其他模型 → ffmpeg 叠加配音
            if tts_audio and os.path.exists(tts_audio):
                try:
                    merged = self._merge_audio_to_video(video_url, tts_audio)
                    if merged:
                        merged_url = local_to_url(merged)
                        return AgentResult(
                            data={"video_url": merged_url, "duration_sec": duration, "model": f"{model_used}+audio"},
                            duration_ms=int((time.time() - start) * 1000),
                        )
                except Exception as e:
                    logger.warning(f"配音合并失败: {e}")

            return AgentResult(
                data={"video_url": video_url, "duration_sec": duration, "model": model_used},
                duration_ms=int((time.time() - start) * 1000),
            )

        # 不再做 LivePortrait 兜底：主模型失败就如实返回原因，
        # 由 run() 循环标记该镜头失败，最后单独补，保证整部剧最终完整。
        return AgentResult(
            success=False,
            error=result.get('error', '视频生成返回空'),
            duration_ms=int((time.time() - start) * 1000),
        )

    # ─── 入库 ────────────────────────────────────────────

    def _register_video_result(self, video_url: str, model: str, shot: dict):
        """将视频结果注册到 media_library"""
        try:
            from services.media_registry import register_video
            r = httpx.get(video_url, timeout=30, verify=False)
            if r.status_code == 200:
                register_video(
                    r.content,
                    f"video_{int(time.time())}",
                    tags=[f"model:{model}", shot.get("shot_type", ""), shot.get("shot_num", "0")],
                    project_id=str(shot.get("project_id", "")),
                    pipeline_id=str(shot.get("pipeline_id", "")),
                user_id=self._video_user_id,
                )
        except Exception as e:
            logger.warning(f"[VideoAgent] 视频入库失败: {e}")

    # ─── 镜头上下文构建 ──────────────────────────────────

    def _build_shot_context(self, shot: dict, all_shots: list = None) -> str:
        """为单镜头拼接全局上下文——让视频模型真正理解这部剧"""
        ctx = []
        td = getattr(self, "_task_data", {})
        genre = td.get("genre", "")
        title = td.get("title", "")
        chars = td.get("characters", [])
        script_text = td.get("script_text", "")
        director_analysis = td.get("director_analysis", {})
        director_tasks = td.get("director_tasks", {})
        shot_index = shot.get("shot_index", shot.get("shot_num", 0))

        # === 导演分析：让模型理解整部剧的基调 ===
        if isinstance(director_analysis, dict):
            genre_analysis = director_analysis.get("genre_analysis", "")
            core_conflict = director_analysis.get("core_conflict", "")
            emotional_curve = director_analysis.get("emotional_curve", "")
            if genre_analysis:
                ctx.append(f"【导演定位】{genre_analysis}")
            if core_conflict:
                ctx.append(f"【核心冲突】{core_conflict}")
            if emotional_curve:
                ctx.append(f"【情绪曲线】{emotional_curve}")

        # === 导演对视频/场景的拍摄指令 ===
        if isinstance(director_tasks, dict):
            scene_task = director_tasks.get("scene_generation", "")
            storyboard_task = director_tasks.get("storyboard_generation", "")
            if scene_task:
                ctx.append(f"【导演-场景指导】{scene_task[:300]}")
            if storyboard_task:
                ctx.append(f"【导演-镜头节奏】{storyboard_task[:300]}")

        if genre or title:
            ctx.append(f"这是{genre}短剧《{title or '未命名'}》的片段")

        # 喂剧本大纲（前500字，让模型理解整部剧讲什么）
        if script_text and len(script_text) > 50:
            ctx.append(f"【剧情背景】{script_text[:500]}")

        # === 题材强约束：古装类禁止现代服装 ===
        _g = (genre or "").lower()
        if any(kw in _g for kw in ["古装", "仙侠", "武侠", "历史", "宫廷", "玄幻", "修真", "古代", "江湖", "战争"]):
            ctx.append("【题材严格约束：古装剧，所有人物必须穿中国古代服饰，发型/配饰均为古代样式，严禁任何现代服装/物品/建筑/武器】")
            ctx.append("【所有角色必须是中国古代人面孔，严禁出现西方人/外国人/现代人】")

        # === 角色信息：让模型知道每个角色是谁 ===
        if chars:
            for c in chars[:5]:
                name = c.get("name", "")
                appearance = c.get("appearance", c.get("description", ""))
                costume = c.get("costume", c.get("clothing", ""))
                if name:
                    line = f"角色【{name}】"
                    if appearance:
                        line += f"：{appearance[:60]}"
                    if costume:
                        line += f"，穿{costume}"
                    ctx.append(line)

        # === 镜头位置和节奏 ===
        if all_shots and len(all_shots) > 1:
            total = len(all_shots)
            ctx.append(f"这是第{shot_index + 1}/{total}个镜头")
            # 剧情位置提示
            if shot_index < 3:
                ctx.append("【剧情阶段：开场，节奏偏慢，建立世界观和角色】")
            elif shot_index >= total - 3:
                ctx.append("【剧情阶段：结尾，节奏放缓，留余韵】")
            else:
                ctx.append("【剧情阶段：高潮，节奏紧凑，冲突激烈】")
            # 上下镜头衔接
            if shot_index > 0:
                prev = all_shots[shot_index - 1]
                prev_desc = prev.get("description", prev.get("scene_description", ""))
                if prev_desc:
                    ctx.append(f"上一个镜头：{prev_desc[:80]}")
            if shot_index + 1 < total:
                next_sh = all_shots[shot_index + 1]
                next_desc = next_sh.get("description", next_sh.get("scene_description", ""))
                if next_desc:
                    ctx.append(f"下一个镜头：{next_desc[:80]}")

        desc = shot.get("description", shot.get("scene_description", ""))
        if desc:
            ctx.append(f"当前镜头内容：{desc}")

        # === 导演逐秒拍摄指令（机位单，视频模型照着拍）===
        director_shot = shot.get("director_shot", "")
        if director_shot:
            ctx.append(f"【导演机位单】{director_shot}")

        # === 当前镜头的拍摄指导 ===
        dialogue = shot.get("dialogue", "")
        if dialogue and dialogue != "(无台词)":
            ctx.append(f"【台词】{dialogue[:100]}")
            # 语气提示（场景智能匹配的 delivery_hint）
            delivery = shot.get("delivery_hint", "")
            if delivery:
                ctx.append(f"【语气指导】{delivery}")
            ctx.append("【表演要求】角色正在说这句台词，嘴唇有明显开合动作，面部表情配合台词情绪，语速自然，说话期间镜头保持稳定不切换")

        # 内心独白（嘴不动，旁白配音）
        inner_voice = shot.get("inner_voice", "")
        if inner_voice and inner_voice != "(无)":
            ctx.append(f"【内心独白】{inner_voice[:100]}")
            ctx.append("【表演要求】角色嘴部闭合不动，不说话，通过眼神和微表情表达内心活动——瞳孔变化、眉头微蹙、嘴角微抿、眼神闪烁。内心独白用旁白配音呈现，画面是角色沉默的特写")

        # 全局旁白
        narration = shot.get("narration", "")
        if narration and narration != "(无)":
            ctx.append(f"【旁白】{narration[:100]}")

        # === 音效提示（场景智能匹配的 sound_design）===
        sound_design = shot.get("sound_design", "")
        if sound_design:
            ctx.append(f"【音效设计】{sound_design[:150]}")

        emotion = shot.get("emotion", "")
        if emotion:
            ctx.append(f"【情绪】{emotion}——角色的表情和肢体语言要体现这个情绪")

        return "\n".join(ctx)

    # ─── 进度更新 ────────────────────────────────────────

    def _update_shot_progress(self, pipeline_id: str, shot_idx: int, shot: dict, results: list):
        """每完成一个 shot 更新 pipelines 表"""
        if not pipeline_id:
            return
        try:
            db_path = "/www/wwwroot/api.mzsh.top/data/short_drama.db"
            conn = sqlite3.connect(db_path)
            shot_items = []
            for r in results:
                si = r.get("shot_index", 0)
                rd = r.get("result", {})
                video_url = rd.get("video_url", rd.get("url", "")) if isinstance(rd, dict) else ""
                shot_items.append({
                    "shot_index": si,
                    "status": "done" if video_url else "failed",
                    "video_url": str(video_url)[:200] if video_url else "",
                    "scene_image": shot.get("scene_image", "")[:200],
                })
            merge = json.dumps({
                "shot_videos": shot_items,
                "total_shots": len(self._task_data.get("shots", [])),
                "completed_shots": len(results),
            })
            conn.execute(
                "UPDATE pipelines SET step_results = json_replace(step_results, '$.shot_progress', json(?)) WHERE id=?",
                (merge, pipeline_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.debug(f"[VideoAgent] shot_progress update skipped: {e}")

    # ─── run() 入口 ──────────────────────────────────────

    def run(self, action: str = "generate", **kwargs) -> AgentResult:
        self._task_data = kwargs  # 注入上下文数据
        self._video_user_id = kwargs.get("user_id", 0)

        if action in ("generate", "generate_video"):
            shots = kwargs.get("shots", [kwargs.get("shot", kwargs)])
            if isinstance(shots, dict):
                shots = [shots]

            results = []
            failed_count = 0
            tts_result = kwargs.get("tts_result", {})
            tts_data = tts_result.get("data", tts_result)
            audio_files = tts_data.get("audio_files", [])

            # 尾帧衔接：用上一镜尾帧作为下一镜首帧（画面连贯，不覆盖角色锁脸图）
            self._last_frame_url = ""

            # 3并发分批执行（HappyHorse R2V 支持5并发，用3个留余量）
            from concurrent.futures import ThreadPoolExecutor, as_completed
            BATCH_SIZE = 3

            # 先预处理所有 shots（注入 TTS、pipeline_id）
            for i, s in enumerate(shots):
                if audio_files and not s.get("tts_audio"):
                    for af in audio_files:
                        si = af.get("shot_index", af.get("shot_num", -1))
                        if si == i or si == i + 1:
                            af_local = af.get("local_path", af.get("file_path", ""))
                            audio_url = af.get("audio_url", af.get("url", af.get("file_path", "")))
                            s["tts_audio"] = af_local or audio_url
                            break
                s["pipeline_id"] = kwargs.get("pipeline_id", "")

            # 分批生成，每批3个并发
            total = len(shots)
            for batch_start in range(0, total, BATCH_SIZE):
                batch_end = min(batch_start + BATCH_SIZE, total)
                batch_indices = list(range(batch_start, batch_end))
                logger.info(f"[VideoAgent] 批次 {batch_start//BATCH_SIZE+1}: 镜头{batch_start+1}-{batch_end} (3并发)")

                def _gen_one(idx):
                    """单个镜头生成（线程池内执行）"""
                    s = shots[idx]
                    try:
                        r = self.generate_video(s, kwargs.get("model_routes"))
                        return idx, r
                    except Exception as e:
                        logger.warning(f"[VideoAgent] 第{idx+1}镜异常: {e}")
                        from .agent_base_legacy import AgentResult as _AR
                        return idx, _AR(success=False, error=str(e))

                with ThreadPoolExecutor(max_workers=BATCH_SIZE) as pool:
                    futures = {pool.submit(_gen_one, idx): idx for idx in batch_indices}
                    for future in as_completed(futures, timeout=600):
                        try:
                            idx, r = future.result(timeout=600)
                            if r.success:
                                results.append({"shot_index": idx, "result": r.data})
                            else:
                                failed_count += 1
                                results.append({"shot_index": idx, "result": {"error": r.error or "未知错误"}})
                            # 更新中间状态
                            self._update_shot_progress(kwargs.get("pipeline_id"), idx, shots[idx], results)
                        except Exception as fe:
                            idx = futures.get(future, 0)
                            failed_count += 1
                            logger.warning(f"[VideoAgent] 镜头{idx+1}超时: {fe}")
                            results.append({"shot_index": idx, "result": {"error": "生成超时"}})

                logger.info(f"[VideoAgent] 批次完成: 成功{len(results)-failed_count}, 失败{failed_count}")

            logger.info(
                f"[VideoAgent] 视频生成完成: {len(results)}镜, "
                f"成功{len(results) - failed_count}, 失败{failed_count}"
            )
            all_failed = failed_count == len(results)
            return AgentResult(
                success=not all_failed,
                data={
                    "videos": results,
                    "total": len(results),
                    "failed": failed_count,
                },
                error="" if not all_failed else f"{failed_count}/{len(results)} shots all failed",
            )

        if action in ("wan2", "img2vid"):
            shot = kwargs.get("shot", kwargs)
            return self.generate_video(shot, kwargs.get("model_routes"))

        return AgentResult(success=False, error=f"未知动作: {action}")

    def execute(self, shot: dict, audio_url: str = "", **kwargs):
        """唯一入口：生成视频"""
        self._task_data = {"model_routes": kwargs, **kwargs}
        s = dict(shot)
        if audio_url:
            s["audio_url"] = audio_url
        return self.generate_video(s, kwargs.get("model_routes"))


from .agent_registry import register_agent

from services.balance_manager import record_cost
register_agent("video", VideoAgent)