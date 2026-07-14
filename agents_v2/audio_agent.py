"""
Hermes AudioAgent — 配音 + BGM Worker（进化版）
TTS: cosyvoice-v2   BGM: doubao-music-v1  辅助: doubao-lite-pro
记忆：角色名→音色指令（最值钱）+ 题材→BGM 偏好
"""
import json
import logging
from services.model_client import UnifiedModel
from core.agent_base_v3 import AgentV3
from core.safety_filter import clean_text

logger = logging.getLogger(__name__)


class AudioAgent(AgentV3):
    name = "audio"
    max_workers = 4

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        scenes = data.get("scenes", data.get("shots", data.get("scene_images", [])))
        chars = data.get("characters", [])
        genre = data.get("genre", "")
        pipeline_id = task.get("pipeline_id", "")

        # 收集角色→音色映射
        role_voices = {}
        for ch in chars:
            name = ch.get("name", "")
            voice = ch.get("voice_style", "")
            age = ch.get("age", "")
            gender = ch.get("gender", "")
            if name:
                role_voices[name] = {"voice_style": voice, "age": age, "gender": gender}

        audio_files = []

        for sc in scenes:
            dialogue = sc.get("dialogue", "")
            emotion = sc.get("emotion", "")
            scene_id = sc.get("scene_id", "")

            if not dialogue:
                continue

            # 识别人物对话
            character = "旁白"
            text = dialogue
            for rname in role_voices:
                if dialogue.startswith(rname + "：") or dialogue.startswith(rname + ":"):
                    character = rname
                    text = dialogue[len(rname) + 1:]
                    break

            text = clean_text(text, replace_ips=False)

            # ── 音色 instruction：先查记忆！ ──
            instruction = ""
            cached_voice = self.memory.lookup("voice_instruct", character)
            if cached_voice:
                instruction = cached_voice["value"].get("instruction", "")
                logger.info(f"[AudioAgent] 角色[{character}] 音色缓存命中")

            if not instruction:
                rv = role_voices.get(character, {})
                style = rv.get("voice_style", "")
                age = rv.get("age", "")
                gender = rv.get("gender", "")

                instr_prompt = (
                    f"为以下场景角色生成 CosyVoice TTS 音色控制指令（只输出指令文本）：\n"
                    f"角色：{character}\n年龄：{age}\n性别：{gender}\n"
                    f"角色设定风格：{style}\n"
                    f"要求：描述年龄感、音色类型、语速、情绪基调，禁止混入朗读台词。"
                )

                instr_result = self.call_with_safety_retry(
                    "doubao-lite-pro", 2,
                    UnifiedModel.llm,
                    prompt=instr_prompt,
                    max_tokens=256,
                    timeout=20,
                )
                instruction = instr_result.get("text",
                    "自然中性语气，中等语速")
                instruction = instruction.strip().strip('"').strip("'")

                # 存记忆：角色名→音色指令（永不过期）
                self.memory.save(
                    {"instruction": instruction, "character": character,
                     "voice_style": style, "age": age},
                    "voice_instruct", character, tags=genre
                )

            # ── TTS 调用（双参数隔离） ──
            tts_result = self.call_with_safety_retry(
                "cosyvoice-v2", 3,
                UnifiedModel.tts,
                text=text,
                voice=instruction,
                speed=1.0,
                timeout=60,
            )

            audio_path = tts_result.get("path", "")
            if audio_path:
                aid = self.log_asset("dialogue_audio", audio_path, meta={
                    "pipeline_id": pipeline_id, "character": character,
                    "scene_id": str(scene_id), "emotion": emotion,
                })
                audio_files.append({
                    "asset_id": aid,
                    "scene_id": str(scene_id),
                    "character": character,
                    "text": text[:50],
                    "instruction": instruction,
                    "audio_path": audio_path,
                    "duration": tts_result.get("duration", 0),
                })

        # ── BGM 生成 ──
        bgm_path = ""
        if scenes:
            # 查记忆：同类题材的 BGM 风格
            cached_bgm = self.memory.lookup("bgm_style", genre)
            bgm_style_hint = cached_bgm["value"].get("bgm_template", "") if cached_bgm else ""

            bgm_prompt = f"生成{genre}短剧背景音乐"
            if bgm_style_hint:
                bgm_prompt += f"，风格参考：{bgm_style_hint}"
            bgm_prompt += f"，烘托{scenes[0].get('emotion','叙事')}氛围，纯音乐无歌词，时长30秒"

            bgm_result = self.call_with_safety_retry(
                "doubao-music-v1", 2,
                UnifiedModel.llm,
                prompt=bgm_prompt,
                max_tokens=512,
                timeout=60,
            )
            bgm_path = bgm_result.get("path", "")
            if bgm_path:
                self.log_asset("bgm", bgm_path, meta={"pipeline_id": pipeline_id})

        return {
            "success": True,
            "audio_files": audio_files,
            "bgm_path": bgm_path,
            "pipeline_id": pipeline_id,
            "genre": genre,
        }

    def _check_memory(self, task: dict) -> dict | None:
        """精确匹配：同一台词组合 → 跳过（极少发生）"""
        scenes = task.get("data", {}).get("scene_images", [])
        if not scenes:
            return None
        key = str(hash(json.dumps([s.get("dialogue", "") for s in scenes], sort_keys=True)))
        return self.memory.lookup("audio_result", key)

    def _find_similar_memory(self, task: dict) -> list:
        genre = task.get("data", {}).get("genre", "")
        if not genre:
            return []
        return self.memory.find_similar(genre, limit=3)

    def _save_memory(self, task: dict, result: dict):
        """存音频结果 + BGM 风格偏好"""
        if not result.get("success"):
            return
        scenes = task.get("data", {}).get("scene_images", [])
        key = str(hash(json.dumps([s.get("dialogue", "") for s in scenes], sort_keys=True)))
        self.memory.save(result, "audio_result", key, tags=result.get("genre", ""))

        # BGM 风格学习
        genre = result.get("genre", "")
        if genre and result.get("bgm_path"):
            self.memory.save(
                {"bgm_template": f"舒缓{genre}纯音乐，叙事氛围"},
                "bgm_style", genre, tags=genre
            )
