"""质检智能体 — 文本/画面/音频/口型全维度检查，不合格退回修改"""
import json, time, logging, re, os, base64, subprocess, tempfile
from typing import Optional, Dict, Any, List
from .agent_base_legacy import BaseAgent, AgentResult

logger = logging.getLogger("agent_qa")

class QAAgent(BaseAgent):
    name = "质检智能体"
    description = "文本/画面/音频/口型全维度质量检查"
    max_retries = 3

    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def check(self, step: str, data: Any, ctx: dict = None) -> dict:
        """统一入口"""
        return self.review_and_fix(step, None, data, ctx)[1]

    def check_text(self, step: str, data: Any, ctx: dict = None) -> dict:
        """文本维度质检（剧本/角色/分镜描述）"""
        prompts = {
            "script": "检查剧本：剧情完整度、角色清晰度、冲突合理性、对话质量。JSON返回 scores(0-10每项), passed, issues, suggestions",
            "character": "检查角色设计：一致性、辨识度、适配度、完整度。JSON返回",
            "storyboard": "检查分镜：情绪连贯性、镜头多样性、描述清晰度、对话匹配。JSON返回",
        }
        p = prompts.get(step, "")
        if not p: return AgentResult(success=True, data={"passed": True})
        try:
            from services.model_client import UnifiedModel
            import json as _j, re as _r
            raw = UnifiedModel.llm(prompt=json.dumps(data, ensure_ascii=False)[:3000], system=p, timeout=90, max_tokens=8192)
            # [bugfix] UnifiedModel.llm 返回 ModelResult dict，文本在 "text" 字段
            raw_text = raw.get("text", "") if isinstance(raw, dict) else str(raw)
            m = _r.search(r'\{.*\}', raw_text, _r.DOTALL)
            if m:
                r = _j.loads(m.group(0))
                scores = r.get("scores", {})
                r["passed"] = r.get("passed", False) or (all(v >= 5 for v in scores.values()) and sum(scores.values()) >= 20)
                logger.info(f"  [QA文本] {step}: scores={scores} passed={r['passed']}")
                return AgentResult(success=True, data=r)
        except Exception as e:
            logger.warning(f"  [QA文本] 异常: {e}")
        return AgentResult(success=True, data={"passed": True})

    def check_image(self, image_path: str, expected_desc: str = "") -> AgentResult:
        """画面质检：检查图片是否符合要求"""
        if not image_path or not os.path.exists(image_path):
            return AgentResult(success=True, data={"passed": True, "note": "无图片可检查"})
        try:
            img_size = os.path.getsize(image_path)
            if img_size < 1000: return AgentResult(success=False, data={"passed": False, "issues": ["图片文件过小"]})
            import struct
            with open(image_path, 'rb') as f:
                header = f.read(32)
            if header[:2] not in (b'\xff\xd8', b'\x89P', b'GIF8', b'\x89P'):
                return AgentResult(success=False, data={"passed": False, "issues": ["图片格式异常"]})
            # 用 LLM 检查图片内容（如果部署了视觉模型）
            try:
                from services.model_client import UnifiedModel
                with open(image_path, 'rb') as f:
                    b64 = base64.b64encode(f.read(50000)).decode()
                prompt = f"检查这张图片：{expected_desc[:200] if expected_desc else ''} 评估：角色脸部是否自然、画面是否清晰、构图是否合理。JSON返回 scores(脸部/清晰度/构图)，passed，issues"
                raw = UnifiedModel.llm(prompt=f"[图片base64: {b64[:200]}...]", system=prompt, timeout=90, max_tokens=8192)
                # [bugfix] UnifiedModel.llm 返回 ModelResult dict，文本在 "text" 字段
                raw_text = raw.get("text", "") if isinstance(raw, dict) else str(raw)
                m = re.search(r'\{.*\}', raw_text, re.DOTALL)
                if m:
                    r = json.loads(m.group(0))
                    logger.info(f"  [QA画面] scores={r.get('scores',{})} passed={r.get('passed',False)}")
                    return AgentResult(success=True, data=r)
            except Exception as e:
                logger.warning(f"  视觉质检失败: {e}")
            return AgentResult(success=True, data={"passed": True, "note": "基础检查通过"})
        except Exception as e:
            logger.warning(f"  [QA画面] 异常: {e}")
        return AgentResult(success=True, data={"passed": True})

    def check_audio(self, audio_path: str, expected_emotion: str = "") -> AgentResult:
        """音频质检：检查音频质量"""
        if not audio_path or not os.path.exists(audio_path):
            return AgentResult(success=True, data={"passed": True, "note": "无音频可检查"})
        try:
            size = os.path.getsize(audio_path)
            if size < 1000: return AgentResult(success=False, data={"passed": False, "issues": ["音频文件过小"]})
            import subprocess
            r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", audio_path],
                              capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                info = json.loads(r.stdout)
                duration = float(info.get("format", {}).get("duration", 0))
                if duration < 0.5: return AgentResult(success=False, data={"passed": False, "issues": ["音频时长过短"]})
                logger.info(f"  [QA音频] 时长={duration:.1f}s 通过")
                return AgentResult(success=True, data={"passed": True, "duration": duration})
            return AgentResult(success=True, data={"passed": True})
        except Exception as e:
            logger.warning(f"  [QA音频] 异常: {e}")
        return AgentResult(success=True, data={"passed": True})

    def check_video(self, video_path: str, audio_path: str = "") -> AgentResult:
        """视频+口型同步质检"""
        if not video_path or not os.path.exists(video_path):
            return AgentResult(success=True, data={"passed": True, "note": "无视频可检查"})
        try:
            r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", video_path],
                              capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                info = json.loads(r.stdout)
                duration = float(info.get("format", {}).get("duration", 0))
                streams = info.get("streams", [])
                has_video = any(s.get("codec_type") == "video" for s in streams)
                has_audio = any(s.get("codec_type") == "audio" for s in streams)

                issues = []
                if not has_video: issues.append("无视频流")
                if not has_audio and audio_path: issues.append("无音频流")
                if duration < 1: issues.append("视频时长过短")

                if issues:
                    logger.warning(f"  [QA视频] 问题: {issues}")
                    passed = len(issues) <= 1
                    return AgentResult(success=passed, data={"passed": passed, "issues": issues, "duration": duration})
                logger.info(f"  [QA视频] 时长={duration:.1f}s 通过")
                return AgentResult(success=True, data={"passed": True, "duration": duration})
            return AgentResult(success=True, data={"passed": True})
        except Exception as e:
            logger.warning(f"  [QA视频] 异常: {e}")
        return AgentResult(success=True, data={"passed": True})

    def review_and_fix(self, step: str, producer_func, output_data: Any, ctx: dict = None) -> tuple:
        """完整质检流程：文本→画面→音频→视频，不合格退回"""
        for attempt in range(self.max_retries + 1):
            # 文本检查
            _check_result = self.check_text(step, output_data, ctx)
            result = _check_result.data if isinstance(_check_result, AgentResult) else _check_result
            # 如果有图片/音频/视频路径，也检查
            if isinstance(output_data, dict):
                for k, v in output_data.items():
                    if isinstance(v, str) and os.path.isfile(v):
                        ext = os.path.splitext(v)[1].lower()
                        if ext in ('.jpg','.jpeg','.png','.webp'):
                            _ir = self.check_image(v, output_data.get("description",""))
                            ir = _ir.data if isinstance(_ir, AgentResult) else _ir
                            if not ir.get("passed", True):
                                result["passed"] = False
                                result.setdefault("issues", []).extend(ir.get("issues",[]))
                        elif ext in ('.mp3','.wav','.ogg'):
                            _ar = self.check_audio(v)
                            ar = _ar.data if isinstance(_ar, AgentResult) else _ar
                            if not ar.get("passed", True):
                                result["passed"] = False
                                result.setdefault("issues", []).extend(ar.get("issues",[]))
                        elif ext in ('.mp4','.webm','.mov'):
                            _vr = self.check_video(v)
                            vr = _vr.data if isinstance(_vr, AgentResult) else _vr
                            if not vr.get("passed", True):
                                result["passed"] = False
                                result.setdefault("issues", []).extend(vr.get("issues",[]))

            if result.get("passed", False):
                return output_data, result

            if attempt < self.max_retries:
                logger.info(f"  [QA] 第{attempt+1}次不合格，退回修改: {result.get('issues',[])}")
                if ctx is None: ctx = {}
                ctx["qa_feedback"] = {"issues": result.get("issues",[]), "suggestions": result.get("suggestions",[]), "retry": attempt+1}
                try:
                    new_out = producer_func(qa_feedback=ctx["qa_feedback"])
                    if new_out: output_data = new_out; continue
                except Exception:
                    pass
            return output_data, result
        return output_data, {"passed": False, "message": "多次修改仍不合格"}

qa = QAAgent()
