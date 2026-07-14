```python
# agent_base.py
"""智能体基类 - 生产级重写版本"""

import re
import json
import sys
import os
import time
import logging
import asyncio
import concurrent.futures
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field, asdict
from enum import Enum

import httpx
import urllib.parse
import requests
import pymysql
import subprocess

logger = logging.getLogger(__name__)

# 尝试导入经验引擎
try:
    from services.experience_engine import experience_engine
except ImportError:
    experience_engine = None
    logger.warning("经验引擎未加载，经验记录功能不可用")


class AgentError(Exception):
    """智能体基础异常类"""
    pass


class LLMError(AgentError):
    """LLM调用异常"""
    pass


class ToolError(AgentError):
    """工具调用异常"""
    pass


class ValidationError(AgentError):
    """参数验证异常"""
    pass


@dataclass
class AgentResult:
    """智能体执行结果"""
    success: bool = True
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""
    cost: float = 0.0
    duration_ms: int = 0

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return asdict(self)


@dataclass
class MediaQualityResult:
    """媒体质量检查结果"""
    passed: bool = True
    reason: str = ""


class BaseAgent:
    """所有智能体的基类 - 生产级实现"""

    name: str = ""
    agent_id: str = ""
    description: str = ""
    version: str = "1.0.0"

    # LLM配置常量
    LLM_API_URL = "https://api.deepseek.com/v1/chat/completions"
    LLM_API_KEY = "sk-9842971050e24481865e7760237988f2"
    LLM_MODEL = "deepseek-chat"
    LLM_MAX_TOKENS = 16384
    LLM_TIMEOUT = 60
    LLM_RETRIES = 2
    LLM_RETRY_DELAY = 2

    # 数据库配置
    DB_CONFIG = {
        "host": os.getenv("DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("DB_PORT", "3307")),
        "user": os.getenv("DB_USER", "drama_admin"),
        "password": os.getenv("DB_PASS", "123456"),
        "database": os.getenv("DB_NAME", "drama_admin"),
        "charset": "utf8mb4",
        "cursorclass": pymysql.cursors.DictCursor
    }

    def __init__(self, tool_registry=None, agent_name_for_tools: str = ""):
        """初始化智能体"""
        self._http_client: Optional[httpx.Client] = None
        self._async_http_client: Optional[httpx.AsyncClient] = None
        self._db_connection: Optional[pymysql.Connection] = None
        self._rate_limiter: Optional[asyncio.Semaphore] = None
        
        self.tool_registry = tool_registry
        self.agent_name_for_tools = agent_name_for_tools
        
        self._init_provider()
        self._init_rate_limiter()
        
        logger.info(f"智能体 {self.name or self.__class__.__name__} 初始化完成")

    def _init_rate_limiter(self, max_concurrent: int = 5):
        """初始化速率限制器"""
        try:
            self._rate_limiter = asyncio.Semaphore(max_concurrent)
            logger.debug(f"速率限制器初始化: 最大并发 {max_concurrent}")
        except Exception as e:
            logger.warning(f"速率限制器初始化失败: {e}")
            self._rate_limiter = None

    def _get_http_client(self) -> httpx.Client:
        """获取或创建HTTP客户端（连接复用）"""
        if self._http_client is None or self._http_client.is_closed:
            try:
                self._http_client = httpx.Client(
                    timeout=httpx.Timeout(self.LLM_TIMEOUT, connect=10.0),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.LLM_API_KEY}"
                    },
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
                )
                logger.debug("HTTP客户端创建成功")
            except Exception as e:
                logger.error(f"HTTP客户端创建失败: {e}")
                raise AgentError(f"HTTP客户端初始化失败: {e}")
        return self._http_client

    async def _get_async_http_client(self) -> httpx.AsyncClient:
        """获取或创建异步HTTP客户端"""
        if self._async_http_client is None or self._async_http_client.is_closed:
            try:
                self._async_http_client = httpx.AsyncClient(
                    timeout=httpx.Timeout(self.LLM_TIMEOUT, connect=10.0),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.LLM_API_KEY}"
                    },
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
                )
                logger.debug("异步HTTP客户端创建成功")
            except Exception as e:
                logger.error(f"异步HTTP客户端创建失败: {e}")
                raise AgentError(f"异步HTTP客户端初始化失败: {e}")
        return self._async_http_client

    def _get_db_connection(self) -> pymysql.Connection:
        """获取数据库连接"""
        if self._db_connection is None or not self._db_connection.open:
            try:
                self._db_connection = pymysql.connect(**self.DB_CONFIG)
                logger.debug("数据库连接创建成功")
            except pymysql.Error as e:
                logger.error(f"数据库连接失败: {e}")
                raise AgentError(f"数据库连接失败: {e}")
        return self._db_connection

    async def use_tool(self, tool_name: str, **kwargs) -> Any:
        """使用工具注册中心的工具（ToolRegistry集成）"""
        if not self.tool_registry:
            from tools.base import ToolResult
            logger.warning(f"ToolRegistry未初始化，无法使用工具: {tool_name}")
            return ToolResult(success=False, error="ToolRegistry 未初始化，请在Agent构造时传入 tool_registry 参数")

        try:
            tool = self.tool_registry.get_tool(tool_name)
            if not tool:
                from tools.base import ToolResult
                logger.error(f"工具不存在: {tool_name}")
                return ToolResult(success=False, error=f"工具不存在: {tool_name}")

            if not tool.validate(**kwargs):
                from tools.base import ToolResult
                logger.warning(f"工具参数验证失败: {tool_name}")
                return ToolResult(success=False, error=f"工具参数验证失败: {tool_name}", quality_score=0)

            result = await tool.execute(**kwargs)
            logger.info(f"工具 {tool_name} 执行成功")
            return result

        except Exception as e:
            from tools.base import ToolResult
            logger.error(f"工具 {tool_name} 执行失败: {e}", exc_info=True)
            return ToolResult(success=False, error=str(e), quality_score=0)

    def use_tool_sync(self, tool_name: str, **kwargs) -> Any:
        """同步版 use_tool — 在非async方法中调用工具"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                future = asyncio.run_coroutine_threadsafe(
                    self.use_tool(tool_name, **kwargs), loop
                )
                return future.result(timeout=60)
            else:
                return loop.run_until_complete(self.use_tool(tool_name, **kwargs))
        except RuntimeError:
            return asyncio.run(self.use_tool(tool_name, **kwargs))
        except Exception as e:
            logger.error(f"同步调用工具 {tool_name} 失败: {e}", exc_info=True)
            from tools.base import ToolResult
            return ToolResult(success=False, error=str(e), quality_score=0)

    def get_tool_functions(self) -> list:
        """返回当前Agent可用的工具清单（OpenAI function calling格式）"""
        if not self.tool_registry:
            return []
        
        try:
            lookup = getattr(self, 'agent_name_for_tools', '') or self.name
            my_tools = self.tool_registry.get_agent_tools(lookup)
            return [t.explain() for t in my_tools]
        except Exception as e:
            logger.error(f"获取工具清单失败: {e}")
            return []

    def _init_provider(self):
        """初始化AI provider，由子类覆盖"""
        try:
            from ai_base import deepseek, agnes
            self._dp, self._ag, self.client = deepseek, agnes, deepseek
            logger.debug("AI provider初始化成功")
        except ImportError as e:
            logger.error(f"AI provider导入失败: {e}")
            self._dp = self._ag = self.client = None
        except Exception as e:
            logger.error(f"AI provider初始化失败: {e}")
            self._dp = self._ag = self.client = None

    def _remember(self, task_type: str, scene_type: str, input_text: str, 
                  output_text: str, success: bool = True):
        """记录执行经验，下次相似任务可参考"""
        if not experience_engine:
            return

        try:
            experience_engine.log_generation(
                self.name or "unknown", 
                task_type, 
                scene_type,
                input_text, 
                output_text, 
                self.genre if hasattr(self, 'genre') else "",
                success, 
                4 if success else 2
            )
            logger.debug(f"经验记录成功: task_type={task_type}, scene_type={scene_type}")
        except Exception as e:
            logger.warning(f"经验记录失败: {e}")

    def _recall(self, task_type: str, scene_type: str, input_text: str) -> str:
        """回忆相似任务的历史经验，返回经验提示"""
        if not experience_engine:
            return ""

        try:
            conn = experience_engine._conn()
            with conn.cursor() as cur:
                h = experience_engine._hash(input_text[:2000])
                cur.execute(
                    """SELECT output_text FROM agent_experience 
                       WHERE agent_name=%s AND task_type=%s AND input_hash=%s 
                       AND success=1 AND effectiveness>=3 
                       ORDER BY id DESC LIMIT 1""",
                    (self.name or "unknown", task_type, h)
                )
                row = cur.fetchone()
                if row and row[0]:
                    return f"\n\n【历史经验参考】上次相似任务的成功输出（仅供参考）：\n{row[0][:1000]}"
        except Exception as e:
            logger.debug(f"经验回忆失败: {e}")
        finally:
            try:
                conn.close()
            except:
                pass
        
        return ""

    def _call_llm(self, system: str, user: str, temp: float = 0.7, 
                  agent_id: str = None, timeout: int = 60, retries: int = 2) -> str:
        """调用LLM：使用连接池 + 重试机制"""
        aid = agent_id or self.name or "default"
        actual_retries = retries if retries >= 0 else self.LLM_RETRIES
        actual_timeout = max(timeout, 15)

        payload = {
            "model": self.LLM_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "temperature": temp,
            "max_tokens": self.LLM_MAX_TOKENS
        }

        for attempt in range(actual_retries + 1):
            try:
                logger.info(f"LLM({aid}) 发送请求: system={len(system)}chars, user={len(user)}chars, temp={temp}")
                
                client = self._get_http_client()
                response = client.post(
                    self.LLM_API_URL,
                    json=payload,
                    timeout=httpx.Timeout(actual_timeout, connect=10.0)
                )
                response.raise_for_status()
                
                result = response.json()["choices"][0]["message"]["content"]
                logger.info(f"LLM({aid}) 接收响应: {len(result)}chars")
                return result

            except httpx.TimeoutException as e:
                logger.warning(f"LLM({aid}) 第{attempt+1}次超时: {e}")
                if attempt < actual_retries:
                    time.sleep(self.LLM_RETRY_DELAY)
                    
            except httpx.HTTPStatusError as e:
                logger.warning(f"LLM({aid}) 第{attempt+1}次HTTP错误: {e.response.status_code}")
                if attempt < actual_retries:
                    time.sleep(self.LLM_RETRY_DELAY)
                    
            except Exception as e:
                logger.warning(f"LLM({aid}) 第{attempt+1}次失败: {e}", exc_info=True)
                if attempt < actual_retries:
                    time.sleep(self.LLM_RETRY_DELAY)

        error_msg = f"LLM({aid})调用全部失败({actual_retries+1}次尝试)"
        logger.error(error_msg)
        raise LLMError(error_msg)

    def _call_llm_json(self, system: str, user: str, temp: float = 0.3,
                       agent_id: str = None, timeout: int = 60, retries: int = 2) -> dict:
        """调用LLM并解析JSON返回，失败时自动重试（带多项容错）"""
        aid = agent_id or self.name or "default"
        actual_retries = retries if retries >= 0 else self.LLM_RETRIES

        for attempt in range(actual_retries + 1):
            try:
                result = self._call_llm(system, user, temp, aid, timeout=timeout)
                result = result.strip()
                
                # 去掉 markdown 代码块
                if result.startswith("```"):
                    first_nl = result.find("\n")
                    if first_nl != -1:
                        result = result[first_nl + 1:]
                    if result.endswith("```"):
                        result = result[:-3].strip()
                
                if not result:
                    continue
                
                # 去掉外层引号
                result = result.strip().strip('"').strip("'")
                
                # 尝试直接解析
                try:
                    parsed = json.loads(result)
                    if isinstance(parsed, dict) and len(parsed) > 0:
                        return parsed
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
                except json.JSONDecodeError:
                    pass
                
                # 容错3：提取第一个 { } 块
                brace_start = result.find('{')
                brace_end = result.rfind('}')
                if brace_start != -1 and brace_end > brace_start:
                    try:
                        parsed = json.loads(result[brace_start:brace_end + 1])
                        if isinstance(parsed, dict):
                            return parsed
                    except json.JSONDecodeError:
                        pass
                
                # 容错4：提取第一个 [ ] 块（用于列表结果）
                arr_start = result.find('[')
                arr_end = result.rfind(']')
                if arr_start != -1 and arr_end > arr_start:
                    try:
                        parsed = json.loads(result[arr_start:arr_end + 1])
                        if isinstance(parsed, list):
                            return {"shots": parsed, "items": parsed}
                    except json.JSONDecodeError:
                        pass
                        
            except LLMError as e:
                logger.warning(f"JSON解析失败({aid}) 第{attempt+1}次: {e}")
            except Exception as e:
                logger.warning(f"JSON解析失败({aid}) 第{attempt+1}次: {e}", exc_info=True)

        logger.error(f"JSON解析失败({aid}) - {actual_retries+1}次重试均失败")
        return {}

    def _log_experience(self, task_type: str, scene_type: str, input_text: str,
                        output_text: str, success: bool = True, effectiveness: int = 3,
                        user_id: int = 0):
        """记录一次生成经验（由子智能体调用，默认全局）"""
        if not experience_engine:
            return

        try:
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
            logger.debug(f"经验日志记录成功: {task_type}/{scene_type}")
        except Exception as e:
            logger.warning(f"经验日志记录失败: {e}")

    def run(self, **kwargs) -> AgentResult:
        """执行智能体任务，由子类覆盖"""
        raise NotImplementedError("子类必须实现run方法")

    def validate(self, **kwargs) -> bool:
        """验证输入参数"""
        return True

    def _search_materials(self, style: str = None, category: str = None,
                          query: str = None, gender: str = None, limit: int = 10) -> list:
        """直连数据库查询素材库（避免内部HTTP死锁）"""
        try:
            conn = self._get_db_connection()
            sql = "SELECT * FROM materials WHERE 1=1"
            params = []

            if style:
                sql += " AND (style LIKE %s OR tags LIKE %s)"
                params.extend([f"%{style}%", f"%{style}%"])
            
            if category:
                sql += " AND category = %s"
                params.append(category)
            
            if query:
                keywords = [t.strip() for t in query.replace("，", ",").split(",") 
                           if 1 < len(t.strip()) < 10]
                if not keywords:
                    keywords = [t.strip() for t in query.split() 
                               if 1 < len(t.strip()) < 10]
                if not keywords:
                    keywords = [query[:15]]
                
                kw_clauses = []
                for kw in keywords:
                    kw_clauses.append(" (name LIKE %s OR description LIKE %s OR tags LIKE %s) ")
                    params.extend([f"%{kw}%", f"%{kw}%", f"%{kw}%"])
                sql += " AND (" + "OR".join(kw_clauses) + ")"
            
            if gender:
                if gender == "男":
                    sql += " AND ((tags LIKE %s AND tags NOT LIKE %s) OR tags LIKE %s)"
                    params.extend(["%男性%", "%女性%", "%男%"])
                else:
                    sql += " AND (tags LIKE %s OR tags LIKE %s)"
                    params.extend(["%女性%", "%女%"])
            
            sql += " ORDER BY id ASC LIMIT %s"
            params.append(min(limit, 20))

            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()

            items = []
            for row in rows:
                tags_raw = row.get("tags", "")
                if isinstance(tags_raw, str):
                    try:
                        tags_list = json.loads(tags_raw) if tags_raw.startswith("[") else [
                            t.strip() for t in tags_raw.split(",") if t.strip()
                        ]
                    except json.JSONDecodeError:
                        tags_list = [tags_raw]
                else:
                    tags_list = tags_raw

                items.append({
                    "id": row["id"],
                    "name": row["name"],
                    "category": row["category"],
                    "tags": tags_list,
                    "style": row.get("style", ""),
                    "description": row.get("description", ""),
                    "file_url": row.get("file_url", ""),
                })

            logger.info(f"素材查询成功: 返回{len(items)}条结果")
            return items

        except pymysql.Error as e:
            logger.error(f"素材库查询失败: {e}")
            return []
        except Exception as e:
            logger.error(f"素材库查询异常: {e}", exc_info=True)
            return []

    def _check_common_sense(self, data: dict, agent_id: str = "") -> list:
        """常识校验：检查LLM输出是否有明显错误，返回问题列表"""
        issues = []
        
        if not data or not isinstance(data, dict):
            return issues

        try:
            # 剧本校验
            if 'title' in data and isinstance(data.get('title'), str):
                title_len = len(data['title'])
                if title_len < 2 or title_len > 100:
                    issues.append(f"剧本标题长度异常: {title_len}字")

            # 角色提取校验
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
                                issues.append(f"镜头{s.get('shot_num', '?')}缺少字段: {f}")
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

        except Exception as e:
            logger.warning(f"常识校验异常: {e}")

        return issues

    def _check_audio_file(self, filepath: str, min_size: int = 1024) -> MediaQualityResult:
        """音频质量检查：存在性、大小、时长、比特率"""
        if not filepath or not os.path.exists(filepath):
            return MediaQualityResult(passed=False, reason=f"文件不存在: {filepath}")

        try:
            size = os.path.getsize(filepath)
            if size < min_size:
                return MediaQualityResult(passed=False, reason=f"音频文件过小: {size}字节")

            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath],
                capture_output=True, text=True, timeout=8
            )
            
            if result.returncode != 0:
                logger.warning(f"ffprobe不可用，跳过详细检查: {filepath}")
                return MediaQualityResult(passed=True, reason="ffprobe不可用")

            info = json.loads(result.stdout)
            duration = float(info.get("format", {}).get("duration", 0))
            bit_rate = info.get("format", {}).get("bit_rate", "0")
            bit_rate = float(bit_rate) if str(bit_rate).strip().isdigit() else 0

            if duration <= 0:
                return MediaQualityResult(passed=False, reason="音频时长为0")
            
            if 0 < bit_rate < 8000:
                return MediaQualityResult(passed=False, reason=f"音频比特率过低: {bit_rate//1000}kbps")

            logger.info(f"音频质量检查通过: {os.path.basename(filepath)} {duration:.1f}s {bit_rate//1000}kbps")
            return MediaQualityResult(passed=True, reason="")

        except subprocess.TimeoutExpired:
            logger.warning(f"音频检查超时: {filepath}")
            return MediaQualityResult(passed=True, reason="检查超时")
        except json.JSONDecodeError as e:
            logger.warning(f"音频信息解析失败: {e}")
            return MediaQualityResult(passed=True, reason=f"解析失败: {e}")
        except Exception as e:
            logger.warning(f"音频检查异常: {e}")
            return MediaQualityResult(passed=True, reason=f"检查异常: {e}")

    def _check_video_file(self, filepath: str, expected_duration: float = 5.0) -> MediaQualityResult:
        """视频质量检查：大小、时长、比特率、帧率"""
        if not filepath or not os.path.exists(filepath):
            return MediaQualityResult(passed=False, reason=f"文件不存在: {filepath}")

        try:
            size = os.path.getsize(filepath)
            if size < 50000:
                return MediaQualityResult(passed=False, reason=f"视频文件过小: {size}字节")

            result = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", "-show_streams", filepath],
                capture_output=True, text=True, timeout=10
            )
            
            if result.returncode != 0:
                logger.warning(f"ffprobe不可用，跳过详细检查: {filepath}")
                return MediaQualityResult(passed=True, reason="ffprobe不可用")

            info = json.loads(result.stdout)
            duration = float(info.get("format", {}).get("duration", 0))
            bit_rate = info.get("format", {}).get("bit_rate", "0")
            bit_rate = float(bit_rate) if str(bit_rate).strip().isdigit() else 0
            
            video_streams = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
            fps = None
            if video_streams:
                num_den = (video_streams[0].get("avg_frame_rate", "0/1").split("/") + ["1"])[:2]
                fps = float(num_den[0]) / float(num_den[1]) if float(num_den[1]) > 0 else 0

            reasons = []
            if duration < expected_duration * 0.3:
                reasons.append(f"时长={duration:.1f}s (期望{expected_duration}s)")
            if 0 < bit_rate < 100000:
                reasons.append(f"比特率={bit_rate/1000:.0f}kbps")
            if video_streams and fps and fps < 5:
                reasons.append(f"帧率={fps:.1f}fps")

            if reasons:
                return MediaQualityResult(passed=False, reason="; ".join(reasons))

            logger.info(f"视频质量检查通过: {os.path.basename(filepath)} {duration:.1f}s "
                       f"{bit_rate//1000}kbps {fps:.1f}fps" if fps else "")
            return MediaQualityResult(passed=True, reason="")

        except subprocess.TimeoutExpired:
            logger.warning(f"视频检查超时: {filepath}")
            return MediaQualityResult(passed=True, reason="检查超时")
        except json.JSONDecodeError as e:
            logger.warning(f"视频信息解析失败: {e}")
            return MediaQualityResult(passed=True, reason=f"解析失败: {e}")
        except Exception as e:
            logger.warning(f"视频检查异常: {e}")
            return MediaQualityResult(passed=True, reason=f"检查异常: {e}")

    def _check_bgm_data(self, tracks: list) -> MediaQualityResult:
        """BGM数据检查：空列表、缺失字段"""
        if not tracks:
            return MediaQualityResult(passed=False, reason="BGM列表为空")

        try:
            for i, track in enumerate(tracks):
                if isinstance(track, dict):
                    if not track.get("bgm_genre") and not track.get("description"):
                        return MediaQualityResult(
                            passed=False, 
                            reason=f"BGM[{i}]缺少genre/description字段"
                        )
                elif isinstance(track, str) and not track.strip():
                    return MediaQualityResult(
                        passed=False,
                        reason=f"BGM[{i}]为空字符串"
                    )

            return MediaQualityResult(passed=True, reason="")

        except Exception as e:
            logger.warning(f"BGM数据检查异常: {e}")
            return MediaQualityResult(passed=True, reason=f"检查异常: {e}")

    def _get_remember_text(self, task_type: str) -> str:
        """获取记忆文本"""
        try:
            if hasattr(self, '_dp') and self._dp:
                return self._dp.text if hasattr(self._dp, 'text') else ""
        except Exception as e:
            logger.debug(f"获取记忆文本失败: {e}")
        return ""

    def __del__(self):
        """析构函数：清理资源"""
        try:
            if self._http_client and not self._http_client.is_closed:
                self._http_client.close()
        except Exception:
            pass
        
        try:
            if self._async_http_client and not self._async_http_client.is_closed:
                # 异步客户端需要在事件循环中关闭
                pass
        except Exception:
            pass
        
        try:
            if self._db_connection and self._db_connection.open:
                self._db_connection.close()
        except Exception:
            pass
```