"""智能体9：成片合成智能体 v3 — 自动处理data URL + FFmpeg"""
import json, time, logging, os, subprocess, base64, uuid, shutil
from typing import Optional, Dict, List
from .agent_base_legacy import BaseAgent, AgentResult
import httpx
from services.ai_providers import BailianVideoProvider
from utils.storage_path import final_path, tts_path, local_to_url
from app_config import BASE_URL

from services.balance_manager import record_cost
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
        # [bugfix] composite 签名无 kwargs，改用 recipe 取 pipeline_id/user_id
        pipeline_id = recipe.get("pipeline_id", "")
        project_id = recipe.get("project_id", "") or ""
        user_id = recipe.get("user_id", 0)
        final_output, final_url = final_path(project_id)
        # 文件名带 pipeline_id，便于 /status 接口按 pipeline_id glob 扫描兜底
        safe_pid = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(pipeline_id))[:32]
        final_name = f"final_{ts}_{safe_pid}.mp4" if safe_pid else f"final_{ts}.mp4"
        final_output = output_path or final_output

        temp_files = [concat_file, video_merged, audio_merged]

        try:
            # 处理data URL
            processed = []
            for clip in clips:
                p = {}
                for k in ("video", "audio", "bgm", "subtitle"):
                    v = clip.get(k, "")
                    if v:
                        # 根据URL后缀决定扩展名
                        ext = ".tmp"
                        for _e in (".wav", ".mp3", ".aac", ".mp4", ".srt"):
                            if v.endswith(_e):
                                ext = _e
                                break
                        if ext == ".tmp":
                            ext_map_default = {"video": ".mp4", "audio": ".wav", "bgm": ".mp3", "subtitle": ".srt"}
                            ext = ext_map_default.get(k, ".tmp")
                        local_path = self._save_data_url(v, ext)
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
                # 跳过空音频和无效文件
                if a and os.path.exists(a) and os.path.getsize(a) > 100:
                    delay_ms = int(cum_duration * 1000)
                    audio_inputs.extend(["-i", a])
                    filter_parts.append(f"[{mix_count}:a]adelay={delay_ms}|{delay_ms}[a{mix_count}]")
                    mix_count += 1
                # 从原始clips获取分镜时长（用实际视频时长累加）
                shot_dur = float(clips[i].get("duration_sec", clips[i].get("duration", 5)))
                cum_duration += shot_dur
                logger.info(f"[Composite] clip[{i}] dur={shot_dur}s cum={cum_duration}s audio={'Y' if (a and os.path.exists(a) and os.path.getsize(a)>100) else 'N'}")

            # BGM：只用第一个，stream_loop -1 循环播放
            seen_bgm = set()
            for clip in processed:
                b = clip.get("bgm", "")
                if b and os.path.exists(b) and b not in seen_bgm:
                    bgm_paths.append(b)
                    seen_bgm.add(b)
            if bgm_paths:
                bgm_path = bgm_paths[0]  # 只用第一个
                audio_inputs.extend(["-i", bgm_path])
                mix_count += 1
            bgm_count = 1 if bgm_paths else 0

            if mix_count > 0:
                inputs = ["ffmpeg", "-y"] + audio_inputs
                if mix_count == 1 and bgm_count == 0:
                    filter_str = f"{filter_parts[0].split('adelay')[0]}adelay=0|0[out]"
                else:
                    # 把所有带延迟的音频流喂给 amix
                    delayed = [f"[a{i}]" for i in range(mix_count - bgm_count)]
                    for i in range(bgm_count):
                        delayed.append(f"[{mix_count - bgm_count + i}:a]")
                    gain_str = ",".join(["1.5"] * (mix_count - bgm_count) + ["0.1"] * bgm_count) if bgm_count > 0 else ",".join(["1.5"] * mix_count)
                    filter_str = (";".join(filter_parts))
                    filter_str += (";" if filter_parts else "") + "".join(delayed)
                    filter_str += f"amix=inputs={mix_count}:duration=first:dropout_transition=0:weights={gain_str.replace(",", "\\,")}"
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
            # ── 按镜头口型同步 (per-shot VideoRetalk) ──
            # 只对"有人脸（近景/中景/特写）+ 有台词"的镜头做
            retalked_clips = []
            retalk_success = 0
            for i, clip in enumerate(processed):
                v = clip.get("video", "")
                a = clip.get("audio", "")
                shot_type = clips[i].get("shot_type", "") if i < len(clips) else ""

                needs_retalk = (
                    a and os.path.exists(a) and os.path.getsize(a) > 100
                    and v and os.path.exists(v)
                    and shot_type in ("近景", "特写", "大特写", "中景")
                )

                if needs_retalk:
                    logger.info(f"[Composite] shot[{i}] 口型开始 shot_type={shot_type}")
                    try:
                        vdur = self._get_media_duration(v)
                        adur = self._get_media_duration(a)
                        if vdur < 2 or vdur > 120:
                            logger.warning(f"[Composite] shot[{i}] 视频{vdur}s超限")
                            retalked_clips.append(v)
                            continue
                        if adur < 2:
                            padded = os.path.join(workdir, f"pad_{i}_{ts}.wav")
                            self._run_ffmpeg(["ffmpeg","-y","-i",a,"-af","apad","-t","2",padded],
                                             desc=f"pad shot[{i}]", timeout=30)
                            a = padded
                        # 音频转wav（百炼VideoRetalk的MoviePy读不了某些mp3）
                        wav_audio = os.path.join(workdir, f"retalk_audio_{i}_{ts}.wav")
                        self._run_ffmpeg(["ffmpeg", "-y", "-i", a, "-ac", "1", "-ar", "16000", wav_audio],
                                         desc=f"retalk转wav shot[{i}]", timeout=30)
                        # 音视频必须复制到storage下，百炼才能通过URL下载
                        # /tmp文件nginx访问不了
                        accessible_v = os.path.join(workdir, f"retalk_v_{i}_{ts}{os.path.splitext(v)[1]}")
                        accessible_a = os.path.join(workdir, f"retalk_a_{i}_{ts}.wav")
                        shutil.copy2(v, accessible_v)
                        if os.path.exists(wav_audio):
                            shutil.copy2(wav_audio, accessible_a)
                        else:
                            shutil.copy2(a, accessible_a)
                        # 复制到storage/projects下让nginx能访问
                        from utils.storage_path import tts_path
                        storage_v, video_public = tts_path(project_id, f"retalk_v_{i}_{ts}.mp4") if False else (None, "")
                        # 直接用storage目录
                        retalk_dir = f"/www/wwwroot/storage/projects/{project_id}/retalk/"
                        os.makedirs(retalk_dir, exist_ok=True)
                        shutil.copy2(accessible_v, f"{retalk_dir}v_{i}_{ts}{os.path.splitext(v)[1]}")
                        shutil.copy2(accessible_a, f"{retalk_dir}a_{i}_{ts}.wav")
                        video_public = f"https://ai.mzsh.top/storage/projects/{project_id}/retalk/v_{i}_{ts}{os.path.splitext(v)[1]}"
                        audio_public = f"https://ai.mzsh.top/storage/projects/{project_id}/retalk/a_{i}_{ts}.wav"
                        provider = BailianVideoProvider()
                        task_id = provider.submit_videoretalk(video_url=video_public, audio_url=audio_public)
                        if task_id:
                            logger.info(f"[Composite] shot[{i}] retalk提交: {task_id}")
                            retalked_url = provider.poll_videoretalk(task_id, max_wait=300)
                            if retalked_url:
                                rp = os.path.join(workdir, f"retalk_{i}_{ts}.mp4")
                                r = httpx.get(retalked_url, timeout=120, follow_redirects=True)
                                r.raise_for_status()
                                with open(rp, "wb") as f: f.write(r.content)
                                retalked_clips.append(rp)
                                retalk_success += 1
                                logger.info(f"[Composite] shot[{i}] 口型成功")
                                try:
                                    record_cost(kwargs.get("user_id", 0), kwargs.get("project_id", ""), "videoretalk", 1)
                                except Exception: pass
                            else:
                                logger.warning(f"[Composite] shot[{i}] retalk空URL")
                                retalked_clips.append(v)
                        else:
                            logger.warning(f"[Composite] shot[{i}] retalk提交失败")
                            retalked_clips.append(v)
                    except Exception as e:
                        logger.warning(f"[Composite] shot[{i}] 口型异常: {e}")
                        retalked_clips.append(v)
                else:
                    reason = "无音频" if not a else f"景别={shot_type}" if shot_type else "其他"
                    if a and v:
                        logger.info(f"[Composite] shot[{i}] 跳过口型: {reason}")
                    retalked_clips.append(v)

            logger.info(f"[Composite] 口型完成: {retalk_success}/{sum(1 for ci,c in enumerate(clips) if c.get('audio') and c.get('shot_type','') in ('近景','特写','大特写','中景'))} 成功")

            # 用口型后的视频重新拼接
            if retalk_success > 0:
                with open(concat_file, "w") as f:
                    for vp in retalked_clips:
                        if vp and os.path.exists(vp):
                            f.write(f"file '{vp}'\n")
                self._run_ffmpeg(["ffmpeg","-y","-f","concat","-safe","0","-i",concat_file,"-c","copy",video_merged],
                                 desc="拼接视频轨(含口型)")
                logger.info(f"[Composite] 重新拼接 {retalk_success} 个口型镜头")
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

    def _run_ffmpeg(self, cmd: list, desc: str = "FFmpeg", timeout: int = 600):
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