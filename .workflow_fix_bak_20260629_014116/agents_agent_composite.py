"""智能体9：成片合成智能体 v3 — 自动处理data URL + FFmpeg"""
import json, time, logging, os, subprocess, base64, uuid, shutil
from typing import Optional, Dict, List
from .agent_base_legacy import BaseAgent, AgentResult
import httpx
from services.ai_providers import BailianVideoProvider

logger = logging.getLogger(__name__)


def _register_video(data: bytes, name: str, tags: list = None, project_id: str = "",
                    pipeline_id: str = "", duration: float = 0, width: int = 0, height: int = 0,
                    user_id: int = 0):
    """注册视频到媒体库"""
    try:
        from services.media_registry import register_video as _rv
        return _rv(data, name, tags=tags or [], project_id=project_id,
                   pipeline_id=pipeline_id, duration=duration, width=width, height=height, user_id=user_id)
    except Exception as e:
        logger.warning(f"[MediaRegistry] video failed: {e}")
        return None


class CompositeAgent(BaseAgent):
    name = "成片合成智能体"
    description = "FFmpeg 合成 (自动处理data URL)"
    version = "3.0.0"
    use_model = False

    def _save_data_url(self, data_url: str, ext: str = ".mp4") -> str:
        """保存 data URL 或远程 URL 到本地临时文件"""
        if not data_url:
            return ""
        # Handle data: URLs (base64)
        if data_url.startswith("data:"):
            try:
                _, encoded = data_url.split(",", 1)
                data = base64.b64decode(encoded)
                path = f"/tmp/asset_{uuid.uuid4().hex[:12]}{ext}"
                with open(path, "wb") as f:
                    f.write(data)
                return path
            except Exception as e:
                logger.warning(f"保存data URL失败: {e}")
                return ""
        # Handle remote HTTP/HTTPS URLs — download to local file
        if data_url.startswith("http://") or data_url.startswith("https://"):
            try:
                path = f"/tmp/asset_{uuid.uuid4().hex[:12]}{ext}"
                logger.info(f"[Composite] 下载远程: {data_url[:80]}...")
                r = httpx.get(data_url, timeout=120, follow_redirects=True)
                r.raise_for_status()
                with open(path, "wb") as f:
                    f.write(r.content)
                logger.info(f"[Composite] 下载完成: {len(r.content)} bytes")
                return path
            except Exception as e:
                logger.warning(f"[Composite] 下载远程失败: {e}")
                return ""  # fallback
        return data_url

    def _cleanup_temp_files(self, *paths):
        """清理临时文件"""
        for p in paths:
            if p and os.path.exists(p) and p.startswith("/tmp/"):
                try:
                    os.remove(p)
                except Exception as ex_: logger.warning(f"[agent_composite]  {ex_}")

    def composite(self, clips: list, output_path: str = "", workdir: str = "/tmp",
                  recipe: dict = None) -> AgentResult:
        start = time.time()
        if not clips:
            return AgentResult(success=False, error="无分镜素材")
        
        recipe = recipe or {}

        os.makedirs(workdir, exist_ok=True)
        ts = int(time.time())
        concat_file = os.path.join(workdir, f"concat_{ts}.txt")
        video_merged = os.path.join(workdir, f"video_merged_{ts}.mp4")
        audio_merged = os.path.join(workdir, f"audio_merged_{ts}.wav")
        pipeline_id = kwargs.get("pipeline_id", "")
        final_output_dir = f"/www/wwwroot/storage/{pipeline_id}/videos" if pipeline_id else "/www/wwwroot/storage/videos"
        os.makedirs(final_output_dir, exist_ok=True)
        final_output = output_path or os.path.join(final_output_dir, f"final_{ts}.mp4")

        temp_files = [concat_file, video_merged, audio_merged]

        try:
            # 处理data URL
            processed = []
            for clip in clips:
                p = {}
                for k in ("video", "audio", "bgm", "subtitle"):
                    v = clip.get(k, "")
                    if v:
                        ext_map = {"video": ".mp4", "audio": ".mp3", "bgm": ".mp3", "subtitle": ".srt"}
                        local_path = self._save_data_url(v, ext_map.get(k, ".tmp"))
                        p[k] = local_path
                        if local_path and local_path != v:
                            temp_files.append(local_path)
                processed.append(p)

            with open(concat_file, "w") as f:
                has_video = False
                for clip in processed:
                    v = clip.get("video", "")
                    if v and os.path.exists(v):
                        f.write(f"file '{v}'\n")
                        has_video = True
                if not has_video:
                    return AgentResult(success=False, error="无视频素材", data={"processed": len(processed)})

            self._run_ffmpeg([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_file, "-c", "copy", video_merged
            ], desc="拼接视频轨")

            # 混音 - 按分镜时序对齐
            audio_inputs = []
            mix_count = 0
            filter_parts = []
            bgm_paths = []
            cum_duration = 0.0  # 累计视频时长(秒)

            for i, clip in enumerate(processed):
                a = clip.get("audio", "")
                if a and os.path.exists(a):
                    delay_ms = int(cum_duration * 1000)
                    audio_inputs.extend(["-i", a])
                    filter_parts.append(f"[{mix_count}:a]adelay={delay_ms}|{delay_ms}[a{mix_count}]")
                    mix_count += 1
                # 从原始clips获取分镜时长
                shot_dur = float(clips[i].get("duration_sec", clips[i].get("duration", 5)))
                cum_duration += shot_dur

            # 收集所有唯一BGM路径
            seen_bgm = set()
            for clip in processed:
                b = clip.get("bgm", "")
                if b and os.path.exists(b) and b not in seen_bgm:
                    bgm_paths.append(b)
                    seen_bgm.add(b)
            for bgm_path in bgm_paths:
                audio_inputs.extend(["-i", bgm_path])
                mix_count += 1
            bgm_count = len(bgm_paths)

            if mix_count > 0:
                inputs = ["ffmpeg", "-y"] + audio_inputs
                if mix_count == 1 and bgm_count == 0:
                    filter_str = f"{filter_parts[0].split('adelay')[0]}adelay=0|0[out]"
                else:
                    # 把所有带延迟的音频流喂给 amix
                    delayed = [f"[a{i}]" for i in range(mix_count - bgm_count)]
                    for i in range(bgm_count):
                        delayed.append(f"[{mix_count - bgm_count + i}:a]")
                    gain_str = ",".join(["1"] * (mix_count - bgm_count) + ["0.3"] * bgm_count) if bgm_count > 0 else ",".join(["1"] * mix_count)
                    filter_str = (";".join(filter_parts))
                    filter_str += (";" if filter_parts else "") + "".join(delayed)
                    filter_str += f"amix=inputs={mix_count}:duration=longest:dropout_transition=0:weights={gain_str.replace(",", "\\,")}"
                    filter_str += f",volume=1.0[out]"
                inputs.extend(["-filter_complex", filter_str, "-map", "[out]",
                               "-ac", "2", "-ar", "44100", audio_merged])
                self._run_ffmpeg(inputs, desc="混音对齐")
            else:
                self._run_ffmpeg([
                    "ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", "1", audio_merged
                ], desc="静音音频")

            # 合并输出
            subtitle_file = None
            for clip in processed:
                s = clip.get("subtitle", "")
                if s and os.path.exists(s):
                    subtitle_file = s
                    break

            merge_cmd = ["ffmpeg", "-y", "-i", video_merged, "-i", audio_merged]
            if subtitle_file:
                # 转义字幕路径中的特殊字符供 ffmpeg filter 使用
                # 用单引号包裹路径以保护冒号、方括号等特殊字符，仅转义路径中的单引号
                escaped_sub = subtitle_file.replace("'", "'\\''")
                merge_cmd.extend(["-vf", f"subtitles='{escaped_sub}'"])
            merge_cmd.extend([
                "-c:v", "libx264", "-preset", "medium", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", final_output
            ])
            self._run_ffmpeg(merge_cmd, desc="合并")

            # ── 口型同步 (videoretalk) ──
            # 检测是否有对白镜头（带 audio 的 clip 表明有人物说话 → 需要口型同步）
            has_dialogue = any(clip.get("audio", "") for clip in processed)
            if has_dialogue:
                try:
                    # 确保音频文件可被 API 访问（复制到 web 目录）
                    audio_retalk_path = f"/www/wwwroot/storage/audio/retalk_{ts}.wav"
                    os.makedirs(os.path.dirname(audio_retalk_path), exist_ok=True)
                    if os.path.exists(audio_merged):
                        shutil.copy2(audio_merged, audio_retalk_path)

                    video_url = f"https://ai.mzsh.top/storage/videos/final_{ts}.mp4"
                    audio_url = f"https://ai.mzsh.top/storage/audio/retalk_{ts}.wav"

                    provider = BailianVideoProvider()
                    retalk_task_id = provider.submit_videoretalk(
                        video_url=video_url, audio_url=audio_url
                    )
                    if retalk_task_id:
                        logger.info(f"[Composite] videoretalk 任务已提交: {retalk_task_id}")
                        retalk_video_url = provider.poll_videoretalk(retalk_task_id, max_wait=600)
                        if retalk_video_url:
                            logger.info(f"[Composite] videoretalk 成功: {retalk_video_url}")
                            r = httpx.get(retalk_video_url, timeout=300, follow_redirects=True)
                            r.raise_for_status()
                            with open(final_output, "wb") as f:
                                f.write(r.content)
                            logger.info(f"[Composite] 口型同步视频已替换最终输出: {final_output}")
                        else:
                            logger.warning("[Composite] videoretalk 返回空 URL，使用原始视频")
                    else:
                        logger.warning("[Composite] videoretalk 提交失败，使用原始视频")
                except Exception as e:
                    logger.warning(f"[Composite] 口型同步失败，使用原始视频: {e}")

            # 清理临时文件
            self._cleanup_temp_files(*temp_files)

            fs = os.path.getsize(final_output) if os.path.exists(final_output) else 0
            dur = self._get_media_duration(final_output)
            # Register in media library
            if os.path.exists(final_output) and os.path.getsize(final_output) > 100:
                try:
                    with open(final_output, "rb") as vf:
                        vdata = vf.read()
                    _register_video(vdata, recipe.get("title", "") or "video",
                        tags=[recipe.get("title", ""), recipe.get("episode", "")],
                        project_id=recipe.get("project_id", "") or "",
                        pipeline_id=recipe.get("pipeline_id", "") or "",
                        duration=dur, width=1920, height=1080,
                        user_id=user_id)
                except Exception as e:
                    logger.warning(f"[MediaRegistry] video failed: {e}")

            return AgentResult(
                success=True,
                data={"output": final_output, "file_size_mb": round(fs / 1024 / 1024, 1),
                       "duration_sec": dur, "clips_count": len(clips)},
                duration_ms=int((time.time() - start) * 1000))

        except subprocess.TimeoutExpired:
            self._cleanup_temp_files(*temp_files)
            return AgentResult(success=False, error="合成超时")
        except subprocess.CalledProcessError as e:
            self._cleanup_temp_files(*temp_files)
            return AgentResult(success=False, error=f"FFmpeg失败: {str(e.stderr)[:300] if e.stderr else str(e)}")
        except Exception as e:
            self._cleanup_temp_files(*temp_files)
            return AgentResult(success=False, error=f"合成异常: {e}")

    def _run_ffmpeg(self, cmd: list, desc: str = "FFmpeg", timeout: int = 300):
        logger.info(f"{desc}: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr[:500] if result.stderr else "?"
            logger.error(f"{desc}失败: {stderr}")
            raise subprocess.CalledProcessError(result.returncode, cmd, stderr)

    def _get_media_duration(self, filepath: str) -> float:
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", filepath],
                capture_output=True, text=True, timeout=10)
            return round(float(r.stdout.strip()), 1) if r.stdout.strip() else 0.0
        except:
            return 0.0

    def run(self, action: str = "composite", **kwargs) -> AgentResult:
        if action in ("composite", "merge"):
            return self.composite(kwargs.get("clips", []), kwargs.get("output_path", ""),
                                  kwargs.get("workdir", "/tmp"), recipe=kwargs)
        return AgentResult(success=False, error=f"未知动作: {action}")

    def execute(self, clips: list, output_path: str = "", workdir: str = "/tmp", **kwargs):
        """唯一入口：视频合成"""
        return self.composite(clips, output_path=output_path, workdir=workdir, recipe=kwargs)