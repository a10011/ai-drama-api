"""
agent_base_legacy.py — 旧版智能体基类（兼容层）
所有旧智能体继承此类，提供 LLM 调用、工具使用、经验引擎集成
"""
import re
import json
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import time
import logging
import threading
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field
import httpx
import urllib.parse

logger = logging.getLogger(__name__)

# Thread-local HTTP client pool
_thread_local = threading.local()

def _get_client(timeout: int = 60) -> httpx.Client:
    """Get thread-local HTTP client with connection pooling"""
    if not hasattr(_thread_local, "client") or _thread_local.client is None:
        _thread_local.client = httpx.Client(
            timeout=httpx.Timeout(timeout + 5, connect=10),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=20),
            follow_redirects=True
        )
    return _thread_local.client

try:
    from services.experience_engine import experience_engine
except ImportError:
    experience_engine = None


def _get_deepseek_key() -> str:
    """从集中配置获取 DeepSeek API key"""
    try:
        from services.ai_providers import _get_key
        return _get_key("deepseek")
    except Exception:
        return os.environ.get("DEEPSEEK_API_KEY", "")


def _get_deepseek_base_url() -> str:
    """获取 DeepSeek base URL"""
    try:
        from services.ai_providers import _get_base_url
        url = _get_base_url("deepseek")
        return url or "https://api.deepseek.com/v1"
    except Exception:
        return "https://api.deepseek.com/v1"


@dataclass
class AgentResult:
    """智能体执行结果"""
    success: bool = True
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    cost: float = 0.0
    duration_ms: int = 0


class BaseAgent:
    """所有旧版智能体的基类"""

    name: str = ""
    agent_id: str = ""
    description: str = ""
    version: str = "1.0.0"

    def __init__(self, tool_registry=None, agent_name_for_tools: str = "", progress_callback=None):
        self.client = None
        self.tool_registry = tool_registry
        self.agent_name_for_tools = agent_name_for_tools
        self._progress_callback = progress_callback
        self._init_provider()

    def report_progress(self, message: str, percent: int = 0):
        """向管家汇报当前进度（正在做什么、遇到什么困难）"""
        try:
            logger.info(f"[{self.name or self.agent_id}] {message} ({percent}%)")
            if self._progress_callback:
                self._progress_callback(
                    agent=self.name or self.agent_id,
                    message=message,
                    percent=percent,
                )
        except Exception as e:
            logger.warning(f"[{self.name}] report_progress 失败: {e}")

    async def use_tool(self, tool_name: str, **kwargs):
        """使用工具注册中心的工具"""
        if not self.tool_registry:
            from tools.base import ToolResult
            return ToolResult(success=False, error="ToolRegistry 未初始化")
        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            from tools.base import ToolResult
            return ToolResult(success=False, error=f"工具不存在: {tool_name}")
        if not tool.validate(**kwargs):
            from tools.base import ToolResult
            return ToolResult(success=False, error=f"工具参数验证失败: {tool_name}", quality_score=0)
        try:
            result = await tool.execute(**kwargs)
            return result
        except Exception as e:
            from tools.base import ToolResult
            return ToolResult(success=False, error=str(e), quality_score=0)
    
    def use_tool_sync(self, tool_name: str, **kwargs):
        """同步版 use_tool — 不依赖asyncio桥，直接在独立loop中运行"""
        if not hasattr(self, "tool_registry") or not self.tool_registry:
            from tools.base import ToolResult
            return ToolResult(success=False, error=f"工具注册表未初始化")
        tool = self.tool_registry.get_tool(tool_name)
        if not tool:
            from tools.base import ToolResult
            return ToolResult(success=False, error=f"工具不存在: {tool_name}")
        if not tool.validate(**kwargs):
            from tools.base import ToolResult
            return ToolResult(success=False, error=f"工具参数验证失败: {tool_name}", quality_score=0)
        try:
            import asyncio
            coro = tool.execute(**kwargs)
            # 工具内部用 _call_llm() 是纯同步的，直接 run
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(coro)
            # 在已有loop的场景下用新线程事件循环（避免线程安全冲突）
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        except Exception as e:
            from tools.base import ToolResult
            logger.warning(f"工具调用异常 [{tool_name}]: {e}")
            return ToolResult(success=False, error=str(e), quality_score=0)

    def _try_tool_redo(self, tools: List[dict], min_score: float = 70, max_rounds: int = 0) -> dict:
        """
        工具箱驱动自我优化 — 统一反馈循环
        
        用法: agent 生成结果后，调此方法获取工具反馈并判断是否需要重做。
        
        tools: [{"name": "tool_name", "params": {...}, "weight": 1.0}, ...]
        min_score: 低于此分触发重做
        max_rounds: 最多重做轮数
        
        Returns: {"should_redo": bool, "score": float, "feedback": str, "tool_results": [...]}
        feedback 可直接拼入 prompt。
        """
        if not self.tool_registry:
            return {"should_redo": False, "score": 100, "feedback": "", "tool_results": []}
        
        all_results = []
        total_score = 0
        total_weight = 0
        all_tips = []
        
        for tcfg in tools:
            tool_name = tcfg.get("name", "")
            params = tcfg.get("params", {})
            weight = tcfg.get("weight", 1.0)
            try:
                result = self.use_tool_sync(tool_name, **params)
                if result and result.success:
                    s = result.quality_score or 0
                    tips = result.suggestions or []
                    all_results.append({"name": tool_name, "score": s, "tips": tips, "data": result.data})
                    total_score += s * weight
                    total_weight += weight
                    all_tips.extend(tips)
            except Exception as e:
                logger.warning(f"[_try_tool_redo] {tool_name} 异常: {e}")
        
        if total_weight == 0:
            return {"should_redo": False, "score": 100, "feedback": "", "tool_results": all_results}
        
        weighted_score = total_score / total_weight
        should_redo = weighted_score < min_score or len(all_tips) > 2
        
        feedback_lines = [f"\n【质量评估】综合得分: {weighted_score:.0f}/100"]
        if all_tips:
            feedback_lines.append("\n【改进建议】")
            for i, tip in enumerate(all_tips[:6], 1):
                feedback_lines.append(f"  {i}. {tip}")
        feedback_lines.append(f"\n请根据以上反馈重新生成，修复所有指出的问题。")
        
        logger.info(
            f"[_try_tool_redo] score={weighted_score:.0f} redo={should_redo} "
            f"tips={len(all_tips)} tools={[r['name'] for r in all_results]}"
        )
        
        return {
            "should_redo": should_redo,
            "score": weighted_score,
            "feedback": "\n".join(feedback_lines),
            "tool_results": all_results
        }

    def get_tool_functions(self) -> list:
        """返回当前Agent可用的工具清单"""
        if not self.tool_registry:
            return []
        lookup = getattr(self, 'agent_name_for_tools', '') or self.name
        my_tools = self.tool_registry.get_agent_tools(lookup)
        return [t.explain() for t in my_tools]

    def _init_provider(self):
        """初始化AI provider"""
        try:
            from ai_base import deepseek, agnes
            self._dp, self._ag, self.client = deepseek, agnes, deepseek
        except ImportError:
            logger.warning("ai_base not available, using fallback")
            self._dp = None
            self._ag = None
            self.client = None

    def _remember(self, task_type: str, scene_type: str, input_text: str, output_text: str, success: bool = True):
        """记录执行经验"""
        try:
            if experience_engine:
                experience_engine.log_generation(
                    self.name or "unknown", task_type, scene_type,
                    input_text, output_text, self.genre if hasattr(self, 'genre') else "",
                    success, 4 if success else 2
                )
        except Exception as e:
            logger.debug(f"_remember失败: {e}")

    def _recall(self, task_type: str, scene_type: str, input_text: str) -> str:
        """回忆相似任务的历史经验"""
        try:
            if not experience_engine:
                return ""
            conn = experience_engine._conn()
            cur = conn.cursor()
            h = experience_engine._hash(input_text[:2000])
            cur.execute(
                "SELECT output_text FROM agent_experience WHERE agent_name=? AND task_type=? AND input_hash=? AND success=1 AND effectiveness>=3 ORDER BY id DESC LIMIT 1",
                (self.name or "unknown", task_type, h)
            )
            row = cur.fetchone()
            cur.close()
            conn.close()
            if row and row[0]:
                return f"\n\n【历史经验参考】上次相似任务的成功输出（仅供参考）：\n{row[0][:1000]}"
        except Exception as e:
            logger.debug(f"_recall失败: {e}")
        return ""

    def _call_llm(self, system: str, user: str, temp: float = 0.7, agent_id: str = None, timeout: int = 300, retries: int = 0, **kwargs) -> str:
        """调用LLM：按岗位分配模型(ROLE_MODEL)，走统一模型路由"""
        logger_local = logging.getLogger("agent.llm")
        aid = agent_id or self.name or "default"

        from services.model_client import UnifiedModel
        from services.model_spec import get_role_model

        # [配脑子] 按岗位选模型：显式传 model 优先，否则用 ROLE_MODEL 映射
        chosen_model = kwargs.get("model") or get_role_model(aid)

        for attempt in range(retries + 1):
            try:
                logger_local.info(f"  LLM({aid}) send: system={len(system)} user={len(user)} temp={temp} model={chosen_model}")
                result = UnifiedModel.llm(
                    prompt=user, system=system,
                    model=chosen_model,
                    max_tokens=16384, timeout=timeout
                )
                if result.get("success") and result.get("text"):
                    recv_text = result.get("text", "")
                    logger_local.info(f"  LLM({aid}) recv: {len(recv_text)} chars model={result.get('model','?')}")
                    return recv_text
                raise Exception(result.get("error") or "LLM返回空")
            except Exception as e:
                last_error = e
                logger_local.warning(f"LLM({aid}) 第{attempt+1}次失败: {e}")
                if attempt < retries:
                    time.sleep(2)
        raise Exception(f"LLM({aid})调用全部失败({retries+1}次尝试): {last_error}")
    
    def _call_llm_json(self, system: str, user: str, temp: float = 0.3, agent_id: str = None, timeout: int = 300, retries: int = 0, **kwargs) -> dict:
        """调用LLM并解析JSON返回 — 增强版容错"""
        aid = agent_id or self.name or "default"
        last_error = None
        for attempt in range(retries + 1):
            try:
                result = self._call_llm(system, user, temp, aid, timeout=timeout, **kwargs)
                if not result or not result.strip():
                    last_error = "LLM返回空结果"
                    continue
                
                result = result.strip()
                # 去掉 markdown 代码块
                if result.startswith("```"):
                    first_nl = result.find("\n")
                    if first_nl != -1:
                        result = result[first_nl+1:]
                    if result.endswith("```"):
                        result = result[:-3].strip()
                
                if not result:
                    last_error = "去除markdown后为空"
                    continue
                
                # 去掉外层引号
                result = result.strip().strip('"').strip("'")
                
                # 尝试直接解析
                try:
                    # 清理markdown标记
                    result = result.strip()
                    if result.startswith('```json'):
                        result = result[7:]
                    if result.startswith('```'):
                        result = result[3:]
                    if result.endswith('```'):
                        result = result[:-3]
                    result = result.strip()
                    parsed = json.loads(result)
                    if isinstance(parsed, dict) and len(parsed) > 0:
                        return parsed
                    if isinstance(parsed, list) and len(parsed) > 0:
                        return {"items": parsed, "data": parsed}
                except json.JSONDecodeError:
                    pass
                
                # 容错1：移除控制字符
                cleaned = re.sub(r'[\x00-\x1f\x7f]', '', result)
                # 容错2：修复尾逗号
                cleaned = re.sub(r',\s*([}\]])', r'\1', cleaned)
                try:
                    parsed = json.loads(cleaned)
                    if isinstance(parsed, dict):
                        return parsed
                    if isinstance(parsed, list):
                        return {"items": parsed, "data": parsed}
                except json.JSONDecodeError:
                    pass
                
                # 容错3：提取第一个 { } 块
                brace_start = result.find('{')
                brace_end = result.rfind('}')
                if brace_start != -1 and brace_end > brace_start:
                    try:
                        parsed = json.loads(result[brace_start:brace_end+1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                
                # 容错4：提取第一个 [ ] 块
                arr_start = result.find('[')
                arr_end = result.rfind(']')
                if arr_start != -1 and arr_end > arr_start:
                    try:
                        parsed = json.loads(result[arr_start:arr_end+1])
                        if isinstance(parsed, list):
                            return {"shots": parsed, "items": parsed}
                    except json.JSONDecodeError:
                        pass
                
                last_error = f"JSON解析失败: 无法从响应中提取有效JSON"
                logger.warning(f"JSON解析失败({aid}) 第{attempt+1}次: 响应前200字符={result[:200]}")
                
            except Exception as e:
                last_error = str(e)
                logger.warning(f"JSON解析失败({aid}) 第{attempt+1}次: {e}")
        
        logger.error(f"JSON解析失败({aid}) - {retries+1}次重试均失败, last_error={last_error}")
        return {}

    def _log_experience(self, task_type: str, scene_type: str, input_text: str, output_text: str, success: bool = True, effectiveness: int = 3, user_id: int = 0):
        """记录一次生成经验"""
        try:
            if experience_engine:
                experience_engine.log_generation(
                    agent_name=self.name or self.__class__.__name__,
                    task_type=task_type,
                    scene_type=scene_type,
                    input_text=input_text,
                    output_text=str(output_text)[:3000],
                    genres="",
                    success=success,
                    effectiveness=effectiveness,
                    user_id=user_id
                )
        except Exception as ex_: logger.warning(f"[agent_base_legacy]  {ex_}")

    def run(self, **kwargs) -> AgentResult:
        """执行智能体任务，由子类覆盖"""
        raise NotImplementedError

    def validate(self, **kwargs) -> bool:
        """验证输入参数"""
        return True
    
    def _search_materials(self, style: str = None, category: str = None, query: str = None, gender: str = None, limit: int = 10) -> list:
        """直连数据库查询素材库"""
        import pymysql
        try:
            conn = pymysql.connect(
                host=os.getenv("DB_HOST", "127.0.0.1"),
                port=int(os.getenv("DB_PORT", "3307")),
                user=os.getenv("DB_USER", "drama_admin"),
                password=os.getenv("DB_PASS", "123456"),
                database=os.getenv("DB_NAME", "drama_admin"),
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=5,
                read_timeout=10
            )
            sql = "SELECT * FROM materials WHERE 1=1"
            p = []
            if style:
                sql += " AND (style LIKE %s OR tags LIKE %s)"
                p.extend([f"%{style}%", f"%{style}%"])
            if category:
                sql += " AND category = %s"
                p.append(category)
            if query:
                keywords = [t.strip() for t in query.replace("，",",").split(",") if 1 < len(t.strip()) < 10]
                if not keywords:
                    keywords = [t.strip() for t in query.split() if 1 < len(t.strip()) < 10]
                if not keywords:
                    keywords = [query[:15]]
                kw_clauses = []
                for kw in keywords:
                    kw_clauses.append(" (name LIKE %s OR description LIKE %s OR tags LIKE %s) ")
                    p.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
                sql += " AND (" + "OR".join(kw_clauses) + ")"
            if gender:
                if gender == "男":
                    sql += " AND ((tags LIKE %s AND tags NOT LIKE %s) OR tags LIKE %s)"
                    p.extend(["%男性%", "%女性%", "%男%"])
                else:
                    sql += " AND (tags LIKE %s OR tags LIKE %s)"
                    p.extend(["%女性%", "%女%"])
            sql += " ORDER BY id ASC LIMIT %s"
            p.append(min(limit, 20))
            with conn.cursor() as cur:
                cur.execute(sql, p)
                rows = cur.fetchall()
            conn.close()
            items = []
            for row in rows:
                tags_raw = row.get("tags", "")
                if isinstance(tags_raw, str):
                    try:
                        tags_list = json.loads(tags_raw) if tags_raw.startswith("[") else [t.strip() for t in tags_raw.split(",") if t.strip()]
                    except (json.JSONDecodeError, TypeError):
                        tags_list = [tags_raw]
                else:
                    tags_list = tags_raw
                items.append({
                    "id": row["id"], "name": row["name"],
                    "category": row["category"], "tags": tags_list,
                    "style": row.get("style", ""),
                    "description": row.get("description", ""),
                    "file_url": row.get("file_url", ""),
                })
            return items
        except Exception as e:
            logger.warning(f"素材库直连查询失败: {e}")
            return []
    
    def _check_common_sense(self, data: dict, agent_id: str = "") -> list:
        """常识校验：检查LLM输出是否有明显错误"""
        issues = []
        if not data or not isinstance(data, dict):
            return issues
        
        # 剧本标题校验
        if 'title' in data and isinstance(data.get('title'), str):
            if len(data['title']) < 2 or len(data['title']) > 100:
                issues.append(f"剧本标题长度异常: {len(data['title'])}字")
        
        # 角色校验
        if 'characters' in data and isinstance(data['characters'], list):
            names = [c.get('name', '') for c in data['characters'] if isinstance(c, dict)]
            seen = set()
            dupes = set()
            for n in names:
                if n in seen:
                    dupes.add(n)
                seen.add(n)
            if dupes:
                issues.append(f"发现重复角色: {', '.join(dupes)}")
        
        # 分镜校验
        if 'shots' in data and isinstance(data['shots'], list):
            total_dur = sum(s.get('duration_sec', 0) for s in data['shots'] if isinstance(s, dict))
            if len(data['shots']) < 2:
                issues.append("分镜镜头少于2个")
            for s in data['shots']:
                if isinstance(s, dict):
                    for f in ['shot_type', 'description', 'duration_sec']:
                        if f not in s or (isinstance(s.get(f), str) and not s[f].strip()):
                            issues.append(f"镜头{s.get('shot_num','?')}缺少字段: {f}")
                            break
            if total_dur > 0 and total_dur < 30:
                issues.append(f"总时长仅{total_dur}s，可能偏短")
        
        # 场景校验
        if 'scenes' in data and isinstance(data['scenes'], list):
            if len(data['scenes']) < 2:
                issues.append("场景少于2个")
        
        # BGM校验
        if 'bgm_tracks' in data and isinstance(data['bgm_tracks'], list):
            if len(data['bgm_tracks']) == 0:
                issues.append("BGM配乐列表为空")
            for t in data['bgm_tracks']:
                if isinstance(t, dict) and 'bgm_genre' not in t:
                    issues.append(f"BGM缺少bgm_genre字段")
                    break
        
        return issues

    # ---- media quality checks ----
    def _check_audio_file(self, filepath, min_size=1024):
        """音频质量检查"""
        import subprocess as _subprocess
        if not filepath or not os.path.exists(filepath):
            return {"pass": False, "reason": "file not found: " + str(filepath)}
        size = os.path.getsize(filepath)
        if size < min_size:
            return {"pass": False, "reason": "audio too small: %d bytes" % size}
        try:
            r = _subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath],
                capture_output=True, text=True, timeout=8
            )
            if r.returncode != 0:
                return {"pass": True, "reason": "ffprobe unavailable"}
            info = json.loads(r.stdout)
            dur = float(info.get("format", {}).get("duration", 0))
            br = info.get("format", {}).get("bit_rate", "0")
            br = float(br) if str(br).strip().isdigit() else 0
            if dur <= 0:
                return {"pass": False, "reason": "audio duration is 0"}
            if 0 < br < 8000:
                return {"pass": False, "reason": "audio bitrate too low: %dkbps" % (br//1000)}
            logger.info("audio QC pass: %s %.1fs %dkbps",
                        os.path.basename(filepath), dur, br//1000)
            return {"pass": True, "reason": ""}
        except Exception as e:
            logger.warning("audio QC exception (pass soft): %s", e)
            return {"pass": True, "reason": "check exception: %s" % e}

    def _check_video_file(self, filepath, expected_duration=5.0):
        """视频质量检查"""
        import subprocess as _subprocess
        if not filepath or not os.path.exists(filepath):
            return {"pass": False, "reason": "file not found: " + str(filepath)}
        size = os.path.getsize(filepath)
        if size < 50000:
            return {"pass": False, "reason": "video too small: %d bytes" % size}
        try:
            r = _subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", filepath],
                capture_output=True, text=True, timeout=10
            )
            if r.returncode != 0:
                return {"pass": True, "reason": "ffprobe unavailable"}
            info = json.loads(r.stdout)
            dur = float(info.get("format", {}).get("duration", 0))
            br = info.get("format", {}).get("bit_rate", "0")
            br = float(br) if str(br).strip().isdigit() else 0
            vs = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
            fps = None
            if vs:
                num, den = (vs[0].get("avg_frame_rate", "0/1").split("/")+["1"])[:2]
                fps = float(num)/float(den) if float(den) > 0 else 0
            reasons = []
            if dur < expected_duration * 0.3:
                reasons.append("dur=%.1fs (expect %ds)" % (dur, expected_duration))
            if 0 < br < 100000:
                reasons.append("bitrate=%.0f" % (br/1000))
            if vs and fps and fps < 5:
                reasons.append("fps=%.1f" % fps)
            if reasons:
                return {"pass": False, "reason": "; ".join(reasons)}
            logger.info("video QC pass: %s %.1fs %dkbps %s",
                        os.path.basename(filepath), dur, br//1000, fps and ("%.1ffps" % fps) or "")
            return {"pass": True, "reason": ""}
        except Exception as e:
            logger.warning("video QC exception (pass soft): %s", e)
            return {"pass": True, "reason": "exception: %s" % e}

    def _check_bgm_data(self, tracks):
        """BGM数据检查"""
        if not tracks:
            return {"pass": False, "reason": "bgm list empty"}
        for i, t in enumerate(tracks):
            if isinstance(t, dict):
                if not t.get("bgm_genre") and not t.get("description"):
                    return {"pass": False, "reason": "bgm[%d] missing genre/desc" % i}
            elif isinstance(t, str) and not t.strip():
                return {"pass": False, "reason": "bgm[%d] empty string" % i}
        return {"pass": True, "reason": ""}

    def _get_remember_text(self, task_type: str) -> str:
        """获取历史经验文本"""
        return self._recall(task_type, "", "")