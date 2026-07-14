"""
模型管家智能体 — 动态管理所有模型的健康、路由、规格

职责：
1. 实时监控：记录每次 API 调用的成功/失败/延迟/错误码
2. 健康检查：定时探测每个模型端点
3. 智能路由：根据实时健康状态自动选择最优模型
4. 故障切换：模型异常时自动标记、降级、恢复
5. 数据面板：提供模型运行状态 API
6. 规格管理：model_spec 的运行时修改接口

调用方式：
    agent = AgentModelManager()
    result = agent.run(action="health", model="wanxiang")  # 单模型健康检查
    result = agent.run(action="status")                      # 全部模型状态
    result = agent.run(action="route", type="image")         # 获取当前最佳路由
    result = agent.run(action="metrics")                     # 近期调用统计
"""

import json
import time
import logging
import threading
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger("agent.model_manager")

# ── 导入统一规格 ──
try:
    from services.model_spec import (SPEC, get_chain, get_rate, PRICING, get_price,
                                       calc_cost, CURRENT_ECOSYSTEM, ECOSYSTEM_CHAINS)
except ImportError:
    SPEC = {}
    get_chain = lambda c: []
    get_rate = lambda m: None
    CURRENT_ECOSYSTEM = "deepseek"
    ECOSYSTEM_CHAINS = {}

# ── 模型知识库（自动加载）──
try:
    from services.model_knowledge import (
        MODEL_KNOWLEDGE, get_model_info, get_all_models,
        get_ecosystem_info, get_balances, get_bailian_apis,
        ECOSYSTEM_INFO, BALANCES, BAILIAN_IMAGE_APIS
    )
except ImportError:
    MODEL_KNOWLEDGE = {}
    get_model_info = lambda m: {"error": "knowledge module not loaded"}
    get_all_models = lambda: {}
    get_ecosystem_info = lambda e=None: {}
    get_balances = lambda: {}
    get_bailian_apis = lambda: {}
    ECOSYSTEM_INFO = {}
    BALANCES = {}
    BAILIAN_IMAGE_APIS = {}

# ── 数据库路径 ──
DB_DIR = "/www/wwwroot/api.mzsh.top/data"
DB_PATH = os.path.join(DB_DIR, "model_metrics.db")


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

class _AgentResult:
    """兼容 Agent 框架的返回对象"""
    def __init__(self, success=True, data=None, error="", duration_ms=0):
        self.success = success
        self.data = data
        self.error = error
        self.duration_ms = duration_ms

@dataclass
class ModelHealth:
    """模型实时健康状态"""
    model_name: str
    status: str = "unknown"     # healthy | degraded | down | disabled
    success_rate: float = 1.0   # 最近 100 次成功率
    avg_latency: float = 0.0    # 平均延迟(秒)
    last_error: str = ""        # 最后错误信息
    last_error_time: float = 0
    consecutive_fails: int = 0
    total_calls: int = 0
    total_fails: int = 0
    throttle_count: int = 0     # 429 次数
    checked_at: float = 0


@dataclass
class CallRecord:
    """单次调用记录"""
    model_name: str
    action: str                 # image | video | llm | tts
    success: bool
    latency: float
    error: str = ""
    error_code: int = 0
    timestamp: float = field(default_factory=time.time)


# ═══════════════════════════════════════════════════════════════
# 模型管家 Agent
# ═══════════════════════════════════════════════════════════════

class AgentModelManager:
    """模型管家 — 负责所有模型的健康监控和智能路由"""

    def __init__(self, tool_registry=None, agent_name_for_tools="model_manager"):
        self.tool_registry = tool_registry
        self.agent_name = agent_name_for_tools
        self._health: Dict[str, ModelHealth] = {}
        self._lock = threading.Lock()
        self._init_db()
        self._load_health()

    # ── 数据库 ──

    def _init_db(self):
        os.makedirs(DB_DIR, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_name TEXT NOT NULL,
                action TEXT NOT NULL,
                success INTEGER NOT NULL,
                latency REAL NOT NULL,
                error TEXT DEFAULT '',
                error_code INTEGER DEFAULT 0,
                timestamp REAL NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_model_calls_time
            ON model_calls(model_name, timestamp)
        """)
        # 迁移: 加 cost 列
        try:
            conn.execute("ALTER TABLE model_calls ADD COLUMN cost REAL DEFAULT 0.0")
        except Exception:
            pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS model_health (
                model_name TEXT PRIMARY KEY,
                status TEXT DEFAULT 'healthy',
                consecutive_fails INTEGER DEFAULT 0,
                last_error TEXT DEFAULT '',
                last_error_time REAL DEFAULT 0,
                throttle_count INTEGER DEFAULT 0,
                updated_at REAL NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def _load_health(self):
        """从数据库恢复健康状态"""
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            rows = conn.execute("SELECT * FROM model_health").fetchall()
            for row in rows:
                name, status, cons_fails, last_err, last_err_time, throttle, _ = row
                mh = ModelHealth(
                    model_name=name, status=status,
                    consecutive_fails=cons_fails or 0,
                    last_error=last_err or "",
                    last_error_time=last_err_time or 0,
                    throttle_count=throttle or 0,
                )
                self._health[name] = mh
            conn.close()
        except Exception as e:
            logger.warning(f"加载健康状态失败: {e}")

    def _save_health(self, model_name: str):
        """持久化单个模型健康状态"""
        try:
            mh = self._health.get(model_name)
            if not mh:
                return
            conn = sqlite3.connect(DB_PATH, timeout=5)
            conn.execute("""
                INSERT OR REPLACE INTO model_health
                (model_name, status, consecutive_fails, last_error, last_error_time, throttle_count, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (model_name, mh.status, mh.consecutive_fails, mh.last_error,
                  mh.last_error_time, mh.throttle_count, time.time()))
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"保存健康状态失败: {e}")

    # ── 调用记录（由 model_client 调用） ──

    def record_call(self, model_name: str, action: str, success: bool,
                    latency: float, error: str = "", error_code: int = 0):
        """记录一次模型调用（异步写 DB）"""
        now = time.time()

        # 更新内存状态
        with self._lock:
            if model_name not in self._health:
                self._health[model_name] = ModelHealth(model_name=model_name)
            mh = self._health[model_name]
            mh.total_calls += 1
            if success:
                mh.consecutive_fails = 0
                if mh.status == "down":
                    mh.status = "degraded"
                    logger.info(f"[ModelManager] 🔄 {model_name} 恢复: down → degraded")
            else:
                mh.total_fails += 1
                mh.consecutive_fails += 1
                mh.last_error = error[:200]
                mh.last_error_time = now
                if error_code == 429:
                    mh.throttle_count += 1
                # 连续失败 ≥3 → 标记 down
                if mh.consecutive_fails >= 3:
                    if mh.status != "down":
                        mh.status = "down"
                        logger.warning(f"[ModelManager] 🔴 {model_name} 连续{mh.consecutive_fails}次失败 → down")
                elif mh.consecutive_fails >= 1:
                    mh.status = "degraded"
            mh.checked_at = now

        # 写 DB（异步，不阻塞调用链）
        try:
            conn = sqlite3.connect(DB_PATH, timeout=2)
            conn.execute("""
                INSERT INTO model_calls (model_name, action, cost, success, latency, error, error_code, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (model_name, action, cost, 1 if success else 0, latency, error[:500], error_code, now))
            conn.commit()
            conn.close()
        except Exception:
            pass

        # 持久化健康状态（每 10 次写一次，减少 IO）
        if mh.total_calls % 10 == 0:
            self._save_health(model_name)

    # ── 智能路由 ──

    def get_best_route(self, category: str, exclude_down: bool = True) -> List[str]:
        """
        获取当前最优路由
        - 排除 down 状态的模型
        - 优先 healthy，其次 degraded
        """
        base_chain = get_chain(category)
        if not base_chain:
            return []

        healthy = []
        degraded = []
        down = []

        for model in base_chain:
            mh = self._health.get(model)
            if not mh or mh.status == "unknown":
                healthy.append(model)  # 未知状态视为健康
            elif mh.status == "healthy":
                healthy.append(model)
            elif mh.status == "degraded":
                degraded.append(model)
            elif mh.status == "down":
                down.append(model)
            else:
                healthy.append(model)

        result = healthy + degraded
        if not exclude_down:
            result += down

        if result:
            logger.info(f"[ModelManager] 🧭 路由 {category}: {result[0]} "
                       f"(healthy={len(healthy)} degraded={len(degraded)} down={len(down)})")
        return result

    def is_model_available(self, model_name: str) -> bool:
        """检查模型是否可用"""
        mh = self._health.get(model_name)
        if not mh:
            return True  # 未知状态，假设可用
        return mh.status != "down"

    # ── 健康检查 ──

    def check_health(self, model_name: str = None) -> Dict:
        """
        健康检查
        - model_name 指定 → 单模型检查
        - model_name=None → 全量检查
        """
        if model_name:
            return self._check_single(model_name)
        return self._check_all()

    def _check_single(self, model_name: str) -> Dict:
        """单模型健康报告"""
        mh = self._health.get(model_name)
        if not mh:
            return {"model": model_name, "status": "unknown", "reason": "无调用记录"}

        # 计算近期指标
        recent = self._get_recent_metrics(model_name, minutes=10)
        return {
            "model": model_name,
            "status": mh.status,
            "consecutive_fails": mh.consecutive_fails,
            "last_error": mh.last_error,
            "last_error_time": mh.last_error_time,
            "throttle_count": mh.throttle_count,
            "total_calls": mh.total_calls,
            "total_fails": mh.total_fails,
            "recent": recent,
            "checked_at": mh.checked_at,
        }

    def _check_all(self) -> Dict:
        """全量模型状态"""
        results = {}
        all_healthy = True
        for model_name in SPEC:
            if SPEC[model_name].static:
                continue
            info = self._check_single(model_name)
            results[model_name] = {
                "status": info.get("status", "unknown"),
                "consecutive_fails": info.get("consecutive_fails", 0),
                "last_error": (info.get("last_error") or "")[:80],
            }
            if info["status"] == "down":
                all_healthy = False

        return {
            "ecosystem": CURRENT_ECOSYSTEM,
            "all_healthy": all_healthy,
            "total_models": len(results),
            "healthy": sum(1 for r in results.values() if r["status"] == "healthy"),
            "degraded": sum(1 for r in results.values() if r["status"] == "degraded"),
            "down": sum(1 for r in results.values() if r["status"] == "down"),
            "models": results,
            "time": time.time(),
        }

    def _get_recent_metrics(self, model_name: str, minutes: int = 10) -> Dict:
        """获取近期调用指标"""
        try:
            cutoff = time.time() - minutes * 60
            conn = sqlite3.connect(DB_PATH, timeout=5)
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) as success_count,
                       AVG(latency) as avg_latency
                FROM model_calls
                WHERE model_name=? AND timestamp > ?
            """, (model_name, cutoff)).fetchone()
            conn.close()
            total = row[0] or 0
            success = row[1] or 0
            avg_lat = row[2] or 0
            return {
                "total_calls": total,
                "success_count": success,
                "fail_count": total - success,
                "success_rate": success / total if total > 0 else 1.0,
                "avg_latency": round(avg_lat, 2),
                "window_minutes": minutes,
            }
        except Exception:
            return {"total_calls": 0, "success_count": 0, "fail_count": 0, "success_rate": 1.0, "avg_latency": 0}

    # ── 指标查询 ──

    def get_metrics(self, model_name: str = None, hours: int = 1) -> Dict:
        """获取详细调用指标"""
        try:
            cutoff = time.time() - hours * 3600
            conn = sqlite3.connect(DB_PATH, timeout=5)
            if model_name:
                rows = conn.execute("""
                    SELECT model_name, action, success, latency, error, error_code, timestamp
                    FROM model_calls
                    WHERE model_name=? AND timestamp > ?
                    ORDER BY timestamp DESC LIMIT 200
                """, (model_name, cutoff)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT model_name, action, success, latency, error, error_code, timestamp
                    FROM model_calls
                    WHERE timestamp > ?
                    ORDER BY timestamp DESC LIMIT 500
                """, (cutoff,)).fetchall()
            conn.close()

            calls = []
            by_model = {}
            for r in rows:
                name, action, success, lat, err, code, ts = r
                calls.append({
                    "model": name, "action": action, "success": bool(success),
                    "latency": round(lat, 2), "error": err[:100] if err else "",
                    "error_code": code or 0, "time": ts,
                })
                if name not in by_model:
                    by_model[name] = {"total": 0, "success": 0, "fail": 0, "avg_latency": 0, "latencies": []}
                by_model[name]["total"] += 1
                if success:
                    by_model[name]["success"] += 1
                else:
                    by_model[name]["fail"] += 1
                by_model[name]["latencies"].append(lat)

            # 计算平均延迟
            summary = {}
            for name, stats in by_model.items():
                lats = stats["latencies"]
                summary[name] = {
                    "total": stats["total"],
                    "success": stats["success"],
                    "fail": stats["fail"],
                    "success_rate": round(stats["success"] / stats["total"] * 100, 1) if stats["total"] > 0 else 0,
                    "avg_latency": round(sum(lats) / len(lats), 2) if lats else 0,
                }

            return {
                "window_hours": hours,
                "total_calls": len(calls),
                "summary": summary,
                "recent_calls": calls[:50],  # 只返回最近 50 条
            }
        except Exception as e:
            return {"error": str(e)}

    # ── 主动修复 ──

    def reset_model(self, model_name: str) -> Dict:
        """手动重置模型状态（从 down 恢复到 healthy）"""
        with self._lock:
            self._health[model_name] = ModelHealth(
                model_name=model_name, status="healthy"
            )
            self._save_health(model_name)
        logger.info(f"[ModelManager] 🔄 {model_name} 手动重置为 healthy")
        return {"model": model_name, "status": "healthy", "action": "reset"}

    def enable_model(self, model_name: str) -> Dict:
        """启用模型（从 disabled → healthy）"""
        return self.reset_model(model_name)

    def disable_model(self, model_name: str, reason: str = "") -> Dict:
        """禁用模型（遇不可恢复错误时手动操作）"""
        with self._lock:
            self._health[model_name] = ModelHealth(
                model_name=model_name, status="disabled",
                last_error=reason, last_error_time=time.time()
            )
            self._save_health(model_name)
        logger.warning(f"[ModelManager] 🔒 {model_name} 手动禁用: {reason}")
        return {"model": model_name, "status": "disabled", "reason": reason}

    # ── 规格查询 ──

    def get_spec(self, model_name: str = None) -> Dict:
        """查询模型规格"""
        if model_name:
            spec = SPEC.get(model_name)
            if not spec:
                return {"error": f"未知模型: {model_name}"}
            result = {
                "model": model_name,
                "provider": spec.provider,
                "service": spec.service,
                "model_id": spec.model_id,
                "type": spec.type,
                "size": spec.size,
                "timeout": spec.timeout,
                "mode": spec.mode,
                "resolution": spec.resolution,
                "duration": spec.duration,
            }
            return result

        # 全量
        return {
            "ecosystem": CURRENT_ECOSYSTEM,
            "chains": {
                eco: {
                    "llm": getattr(ch, "llm", []),
                    "image": getattr(ch, "image", []),
                    "video": getattr(ch, "video", []),
                    "tts": getattr(ch, "tts", []),
                }
                for eco, ch in ECOSYSTEM_CHAINS.items()
            },
            "models": {
                name: {
                    "provider": s.provider,
                    "model_id": s.model_id,
                    "type": s.type,
                    "size": s.size,
                    "timeout": s.timeout,
                }
                for name, s in SPEC.items()
                if not s.static
            }
        }

    # ── Agent 统一入口 ──



    def set_rate(self, model: str, concurrency: int = None, rpm: int = None) -> Dict:
        """运行时调整模型限流参数"""
        try:
            from services.model_spec import RATE, RateSpec, PRICING
            if model not in RATE:
                return {"error": f"未知模型: {model}"}
            old = RATE[model]
            new_c = concurrency if concurrency is not None else old.concurrency
            new_r = rpm if rpm is not None else old.rpm
            RATE[model] = RateSpec(concurrency=new_c, rpm=new_r, acquire_timeout=old.acquire_timeout)
            logger.info(f"[ModelManager] RATE {model}: {old.concurrency}/{old.rpm} -> {new_c}/{new_r}")
            return {
                "model": model,
                "old": {"concurrency": old.concurrency, "rpm": old.rpm},
                "new": {"concurrency": new_c, "rpm": new_r}
            }
        except Exception as e:
            return {"error": str(e)}

    def get_rate(self, model: str = None) -> Dict:
        """查询当前限流配置"""
        from services.model_spec import RATE
        if model:
            r = RATE.get(model)
            return {"model": model, "concurrency": r.concurrency, "rpm": r.rpm} if r else {"error": f"未知: {model}"}
        return {
            m: {"concurrency": r.concurrency, "rpm": r.rpm}
            for m, r in RATE.items() if not m.startswith("_")
        }

    def set_price(self, model: str, price: float) -> Dict:
        """运行时调整模型单价"""
        try:
            from services.model_spec import PRICING
            if model not in PRICING:
                return {"error": f"未知模型: {model}"}
            old = PRICING[model]["price"]
            PRICING[model]["price"] = price
            logger.info(f"[ModelManager] PRICE {model}: {old} -> {price}")
            return {"model": model, "old_price": old, "new_price": price}
        except Exception as e:
            return {"error": str(e)}

    def get_costs(self, hours: int = 24) -> Dict:
        """查询近期费用：按模型/小时汇总"""
        try:
            with self._lock:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                since = time.time() - hours * 3600
                cursor.execute(
                    "SELECT model_name, SUM(cost) as total, COUNT(*) as calls "
                    "FROM model_calls WHERE timestamp >= ? AND cost > 0 "
                    "GROUP BY model_name ORDER BY total DESC",
                    (since,)
                )
                rows = cursor.fetchall()
                total = sum(r[1] or 0 for r in rows)
                result = {
                    "period_hours": hours,
                    "total_cost": round(total, 4),
                    "models": [
                        {"model": r[0], "cost": round(r[1] or 0, 4), "calls": r[2]}
                        for r in rows
                    ]
                }
                conn.close()
                return result
        except Exception as e:
            logger.error(f"[ModelManager] get_costs 失败: {e}")
            return {"error": str(e)}

    def run(self, action: str, **kwargs) -> Any:
        """统一调用入口，返回兼容 AgentResult 接口的对象"""
        actions = {
            "health": lambda: self.check_health(kwargs.get("model")),
            "status": lambda: self.check_health(),
            "route": lambda: self.get_best_route(kwargs.get("type", "image")),
            "metrics": lambda: self.get_metrics(
                kwargs.get("model"), kwargs.get("hours", 1)
            ),
            "reset": lambda: self.reset_model(kwargs.get("model", "")),
            "enable": lambda: self.enable_model(kwargs.get("model", "")),
            "disable": lambda: self.disable_model(
                kwargs.get("model", ""), kwargs.get("reason", "")
            ),
            "costs": lambda: self.get_costs(kwargs.get("hours", 24)),
            "set_rate": lambda: self.set_rate(kwargs.get("model"), kwargs.get("concurrency"), kwargs.get("rpm")),
            "get_rate": lambda: self.get_rate(kwargs.get("model")),
            "set_price": lambda: self.set_price(kwargs.get("model"), kwargs.get("price", 0)),
            "spec": lambda: self.get_spec(kwargs.get("model")),
            # ── 模型知识库操作 ──
            "info": lambda: get_model_info(kwargs.get("model", "")),
            "models": lambda: get_all_models(),
            "knowledge": lambda: MODEL_KNOWLEDGE,
            "ecosystem": lambda: get_ecosystem_info(kwargs.get("eco")),
            "balances": lambda: get_balances(),
            "bailian": lambda: get_bailian_apis(),
            "all": lambda: {
                "ecosystem": get_ecosystem_info(),
                "models": MODEL_KNOWLEDGE,
                "balances": get_balances(),
                "bailian_apis": get_bailian_apis(),
                "health": self.check_health(),
                "rates": self.get_rate(),
            },
        }

        func = actions.get(action)
        if not func:
            return _AgentResult(success=False, error=f"未知操作: {action}，支持: {list(actions.keys())}")

        try:
            result = func()
            return _AgentResult(success=True, data=result)
        except Exception as e:
            logger.error(f"[ModelManager] {action} 失败: {e}")
            return _AgentResult(success=False, error=str(e))


# ── 全局单例 ──
_model_manager: Optional[AgentModelManager] = None
_lock_singleton = threading.Lock()

def get_model_manager() -> AgentModelManager:
    """获取全局模型管家单例"""
    global _model_manager
    if _model_manager is None:
        with _lock_singleton:
            if _model_manager is None:
                _model_manager = AgentModelManager()
    return _model_manager
