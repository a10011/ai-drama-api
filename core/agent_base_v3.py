"""
Agent V3 基类 — 进化版
MQ 消费 + 多层记忆 + 反思学习 + 限流
"""
import time
import json
import logging
import threading
from abc import ABC, abstractmethod
from typing import Optional

from .mq_client import mq, AGENT_QUEUES, COMPLETED_TOPIC
from .agent_memory import AgentMemory
from .rate_limiter import rate_limiter
from .pipeline_ids import register_asset

logger = logging.getLogger(__name__)


class AgentV3(ABC):
    name: str = ""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.memory = AgentMemory(user_id, self.name)
        self.knowledge = self._load_knowledge()

    def _load_knowledge(self) -> str:
        try:
            import glob, os
            kdir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "agents_v2", "knowledge")
            parts = []
            for fp in sorted(glob.glob(os.path.join(kdir, "*.md"))):
                with open(fp, "r", encoding="utf-8") as f:
                    txt = f.read()[:2000]  # 每个文件取前2000字
                parts.append(chr(91) + os.path.basename(fp).replace(chr(46)+"md","") + chr(93) + txt)
        except Exception:
            return 

    @abstractmethod
    def execute(self, task: dict) -> dict:
        """子类实现：核心业务逻辑"""
        raise NotImplementedError

    def run(self, task: dict, timeout: int = 7200) -> dict:  # 2小时，视频生成需要时间
        """进化版执行入口：超时保护 + 保证 publish"""
        import concurrent.futures as _cf
        pipeline_id = task.get("pipeline_id", "")
        self.pipeline_id = pipeline_id
        
        def _do_execute():
            evolution_tips = self._evolution_check(task)
            if evolution_tips:
                task["_evolution_tips"] = evolution_tips
            if self.knowledge and len(self.knowledge) > 50:
                task["_knowledge"] = self.knowledge[:8000]
            cached = self._check_memory(task)
            if cached:
                if evolution_tips:
                    cached["_evolution_tips"] = evolution_tips
                    cached["pipeline_id"] = pipeline_id
                return cached
            similar = self._find_similar_memory(task)
            if similar:
                task["_similar_memories"] = similar
            if evolution_tips:
                task["_evolution_tips"] = evolution_tips
            model = task.get("model", "doubao-pro-256k")
            rpm = task.get("rpm", 100)
            raw = rate_limiter.execute(self.user_id, model, rpm, self.execute, task)
            result = raw if isinstance(raw, dict) else {"data": raw}
            logger.info("[Agent:" + self.name + "] result: " + str({k: str(v)[:100] for k, v in result.items()}))
            if result.get("success"):
                self._save_memory(task, result)
            self._write_reflection(task, result)
            result["pipeline_id"] = pipeline_id
            result.setdefault("user_id", task.get("user_id", 0))
            return result

        try:
            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                _fut = _ex.submit(_do_execute)
                try:
                    result = _fut.result(timeout=timeout)
                except _cf.TimeoutError:
                    result = {"success": False, "error": "超时(" + str(timeout) + "s)", "pipeline_id": pipeline_id, "user_id": task.get("user_id", 0)}
                    logger.error("[Agent:" + self.name + "] timeout " + str(pipeline_id))
        except Exception as e:
            result = {"success": False, "error": str(e)[:200], "pipeline_id": pipeline_id, "user_id": task.get("user_id", 0)}
            logger.error("[Agent:" + self.name + "] crash: " + str(e)[:200])

        # Merge upstream data so downstream agents get full context
        upstream_data = task.get("data", {})
        # V2 agent 返回平铺结构 {success, pipeline_id, characters, ...}，直接用 result 本身
        # V1 agent 返回嵌套结构 {success, data: {...}}，取 data
        result_data = result.get("data", {})
        if not result_data and isinstance(result, dict):
            # 平铺结构：排除元字段后就是业务数据
            meta_keys = {"success", "error", "pipeline_id", "user_id", "data"}
            result_data = {k: v for k, v in result.items() if k not in meta_keys}
        if isinstance(result_data, dict) and isinstance(upstream_data, dict):
            merged = {}
            merged.update(upstream_data)
            merged.update(result_data)
            result["data"] = merged
            logger.info("[Agent:" + self.name + "] merged data keys: " + str(list(merged.keys())[:10]))

        logger.info("[Agent:" + self.name + "] done, success=" + str(result.get("success")) + " pid=" + str(pipeline_id))
        if result.get("success"):
            self.publish_complete(result)
        else:
            self.publish_failed(pipeline_id, result.get("error", "unknown"))
        return result

    def call_model(self, model: str, rpm: float, func, *args, **kwargs):
        """子类调模型 — 自动限流 + 注入pipeline_id做防重复"""
        # 自动注入 pipeline_id + request_id（全链路追踪）
        pid = getattr(self, "_pipeline_id", "") or kwargs.get("pipeline_id", "")
        if pid:
            kwargs["pipeline_id"] = pid
            # 生成唯一 request_id，模型返回时带回来
            if not kwargs.get("request_id"):
                import uuid, time
                kwargs["request_id"] = pid + "-" + self.name + "-" + str(int(time.time()*1000))[-6:]
        # 过滤 func 不接受的参数，避免 TypeError
        import inspect
        sig = inspect.signature(func)
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
        return rate_limiter.execute(
            self.user_id, model, rpm, func, *args, **filtered_kwargs
        )

    def call_with_safety_retry(self, model: str, rpm: float, func, *args,
                                max_retries: int = 2, **kwargs) -> dict:
        """调模型，遇风控自动改写 prompt 重试。自动注入pipeline_id"""
        # 兼容: 如果kwargs里也有model且与位置参数相同, 移除kwargs里的避免重复
        kwargs.pop("model", None)
        pid = getattr(self, "_pipeline_id", "") or kwargs.get("pipeline_id", "")
        if pid:
            kwargs["pipeline_id"] = pid
        for attempt in range(max_retries + 1):
            result = self.call_model(model, rpm, func, *args, **kwargs)
            if result.get("success"):
                return result

            error = result.get("error", "") or str(result.get("data", {}).get("error", ""))
            if "block" not in error.lower() and "safety" not in error.lower() and "violation" not in error.lower():
                return result

            if attempt >= max_retries:
                logger.warning(f"[{self.name}] 风控重试 {max_retries} 次均失败")
                return result

            prompt = kwargs.get("prompt") or (args[0] if args else "")
            if not prompt:
                return result

            rewritten = self._rewrite_blocked_prompt(prompt, error)
            if not rewritten or rewritten == prompt:
                logger.info(f"[{self.name}] 改写无变化")
                return result

            logger.info(f"[{self.name}] 风控改写 prompt (try {attempt+2})")
            if "prompt" in kwargs:
                kwargs["prompt"] = rewritten
            elif args:
                args = list(args)
                args[0] = rewritten
                args = tuple(args)
            time.sleep(2 * (attempt + 1))

        return result

    def _rewrite_blocked_prompt(self, prompt: str, error: str = "") -> str:
        """用 LLM 改写被风控的 prompt + 审计日志入 safety_block_logs"""
        try:
            if not prompt:
                return ""
            from services.model_client import UnifiedModel
            from .safety_audit import log_event
            sys_p = "你是一个安全内容改写助手。用户提供的描述被AI风控拦截，请用同样语义但更安全的方式改写。保持核心创意不变，替换冲突性、暴力、成人或敏感词汇为中性或隐喻表达。只返回改写结果，不要解释。"
            user_msg = "原始描述：" + str(prompt)[:500] + "\n\n错误信息：" + str(error)[:200] + "\n\n请改写："
            r = UnifiedModel.llm(
                prompt=user_msg,
                system=sys_p,
                model="doubao-lite-pro",
                max_tokens=512,
                timeout=15,
            )
            if isinstance(r, dict):
                text = r.get("data", "")
            else:
                text = getattr(r, "data", "")
            rewritten = text.strip().strip('"').strip("'").strip() or ""
            if rewritten:
                log_event(
                    content_type="agent",
                    action="rewrite",
                    provider="doubao",
                    model="doubao-lite-pro",
                    original=prompt[:500],
                    replaced=rewritten[:500],
                    reason="agent_safety_retry",
                    error_msg=error[:200],
                    pipeline_id=getattr(self, "pipeline_id", ""),
                    agent=self.name,
                    user_id=self.user_id,
                )
                logger.info(f"[{self.name}] 风控改写已审计到 safety_block_logs")
            return rewritten
        except Exception as e:
            logger.warning(f"[{self.name}] 改写失败: {e}")
            return ""

    # ── 记忆：精确匹配 ──
    def _check_memory(self, task: dict) -> Optional[dict]:
        return None

    # ── 记忆：模糊搜索（子类重写） ──
    def _find_similar_memory(self, task: dict) -> list:
        return []

    # ── 记忆：保存（子类重写） ──
    def _save_memory(self, task: dict, result: dict):
        pass

    # ── 进化引擎 ──
    def _evolution_check(self, task: dict) -> list:
        """查历史反思，返回进化建议"""
        # 子类可重写：按用户+题材查过去的成功/失败记录
        try:
            genre = task.get("data", {}).get("genre", "")
            if not genre:
                return []
            
            # 查相似记忆中的失败记录
            similars = self.memory.find_similar(genre, limit=5)
            tips = []
            for s in similars:
                val = s.get("value", {})
                if isinstance(val, dict) and not val.get("success", True):
                    tips.append("上次同类失败: " + str(val.get("error", "?")))
            
            # 查反思日志
            try:
                conn = self.memory._get_db()
                rows = conn.execute(
                    "SELECT content FROM agent_reflections WHERE user_id=? AND agent_type=? ORDER BY id DESC LIMIT 10",
                    (self.user_id, self.name)
                ).fetchall()
                conn.close()
                for r in rows:
                    content = r["content"] or ""
                    if "失败" in content and genre in content:
                        tip = content.replace("[失败] ", "").replace("[成功] ", "")
                        if tip not in tips:
                            tips.append(tip)
            except:
                pass
            
            return tips
        except Exception:
            return []

    def _write_reflection(self, task: dict, result: dict):
        """进化反思：记成功经验+失败教训，越跑越聪明"""
        try:
            data = task.get("data", {})
            genre = data.get("genre", "通用")
            ok = result.get("success", False)
            pipeline_id = task.get("pipeline_id", "?")

            if ok:
                # 成功：记下产出量，下次同类可直接复用模板
                outputs = {}
                for k in ["characters","scenes","scene_images","audio_files","video_segments"]:
                    v = result.get(k)
                    if v:
                        outputs[k] = len(v) if isinstance(v, (list, dict)) else str(v)[:100]
                tip = "完成" + genre + "题材,产出:" + str(outputs)
                score = 1.0
            else:
                err = str(result.get("error", "?"))[:200]
                tip = "失败原因:" + err + "。下次" + genre + "题材需注意规避。"
                score = -1.0

            self.memory.save(
                {"tip": tip, "genre": genre, "ok": ok, "pipe": pipeline_id[:20]},
                "reflection", str(int(time.time())),
                tags=self.name + "," + genre,
                score=score
            )
            
            # 写反思日志到 agent_reflections 表
            try:
                self.memory.reflect(
                    self.name + "_" + genre,
                    f"[{'成功' if ok else '失败'}] {tip}"
                )
            except:
                pass
                
            logger.info("[" + self.name + "] 反思: " + tip[:60])
        except Exception:
            pass

    
    def log_asset(self, asset_type: str, url: str = "", file_path: str = "", meta: dict = None):
        pipeline_id = meta.get("pipeline_id", "") if meta else ""
        return register_asset(pipeline_id, self.name, asset_type, url, file_path, meta or {})

    def publish_complete(self, result: dict):
        mq.publish(COMPLETED_TOPIC[self.name], {
            "pipeline_id": result.get("pipeline_id", ""), "stage": self.name,
            "result": result, "success": result.get("success", False),
        })
        # 自动注册 asset
        try:
            pid = result.get("pipeline_id", "")
            if pid:
                meta_keys = {"success", "error", "pipeline_id", "user_id", "data"}
                # 按阶段注册对应 asset
                asset_type_map = {
                    "script": "script",
                    "director": "director_analysis",
                    "character": "characters",
                    "storyboard": "storyboard",
                    "scene": "scene_images",
                    "video": "video_clips",
                    "composite": "composite",
                }
                asset_type = asset_type_map.get(self.name, self.name)
                business_data = {k: v for k, v in result.items() if k not in meta_keys}
                if business_data:
                    import json
                    json_str = json.dumps(business_data, ensure_ascii=False)
                    if len(json_str) > 50000:
                        import os, hashlib
                        os.makedirs('/www/wwwroot/storage/assets', exist_ok=True)
                        fname = f'{asset_type}_{pid[:12]}_{hashlib.md5(json_str.encode()).hexdigest()[:8]}.json'
                        fpath = f'/www/wwwroot/storage/assets/{fname}'
                        with open(fpath, 'w', encoding='utf-8') as f:
                            f.write(json_str)
                        self.log_asset(asset_type, file_path=fpath, meta={"pipeline_id": pid, "size": len(json_str)})
                    else:
                        self.log_asset(asset_type, url=json_str, meta={"pipeline_id": pid, "size": len(json_str)})
        except Exception as e:
            logger.warning(f"[Worker:{self.name}] asset register failed: {e}")
        # push next stage
        try:
            pid = result.get("pipeline_id", "")
            from core.scheduler import get_next_stage, _set_status
            ns = get_next_stage(self.name)
            if ns:
                _set_status(pid, "running:" + ns)
                mq.push(AGENT_QUEUES[ns], {"pipeline_id": result.get("pipeline_id", ""), "user_id": result.get("user_id", 0), "stage": ns, "data": result.get("data", result.get("result", {}))})
                logger.info("[Worker:" + self.name + "] -> " + ns)
        except Exception as e:
            logger.warning("[Worker:" + self.name + "] push next failed: " + str(e))

    def publish_failed(self, pipeline_id: str, error: str):
        # 标记失败但保留数据，支持断点续跑
        # 超时不标记为失败——让管道继续
        if "超时" in str(error) or "timeout" in str(error).lower():
            logger.warning(f"[{self.name}] 超时但继续: {error[:80]}")
            # 超时不标记失败，尝试发布到下一阶段
            try:
                from core.scheduler import get_next_stage, _set_status
                ns = get_next_stage(self.name)
                if ns:
                    _set_status(pipeline_id, "running:" + ns)
            except:
                pass
            return
        
        try:
            from core.scheduler import _set_status
            _set_status(pipeline_id, "failed:" + self.name + ":" + error[:50])
        except:
            pass
        mq.publish(COMPLETED_TOPIC[self.name], {
            "pipeline_id": pipeline_id, "stage": self.name,
            "result": {"error": error}, "success": False,
        })
        try:
            from core.scheduler import get_next_stage, _set_status
            _set_status(pipeline_id, "failed:" + self.name)
            ns = get_next_stage(self.name)
            if ns:
                mq.push(AGENT_QUEUES[ns], {"pipeline_id": pipeline_id, "user_id": 0, "stage": ns, "data": {"error": error}})
        except Exception:
            pass


def start_worker(agent_cls):
    name = agent_cls.name
    queue = AGENT_QUEUES[name]

    def _loop():
        logger.info(f"[Worker:{name}] 启动，监听 {queue}")
        while True:
            try:
                task = mq.pop(queue, timeout=0)
                if task is None:
                    time.sleep(2)
                    continue
                user_id = task.get("user_id", 0)
                agent = agent_cls(user_id)
                agent.run(task, timeout=600)
            except Exception as e:
                logger.error(f"[Worker:{name}] 异常: {e}", exc_info=True)
                time.sleep(5)

    t = threading.Thread(target=_loop, daemon=True, name=f"w-{name}")
    t.start()
    return t
