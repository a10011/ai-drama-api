# -*- coding: utf-8 -*-
"""
hermes_db.py — Hermes 样本库持久化（SQLite）
正负样本自动入库，重启不丢，越用越强
"""

import sqlite3
import json
import logging
import os
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "hermes_samples.db")


def _get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    """初始化表结构"""
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS positive_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                era TEXT NOT NULL,
                genre TEXT NOT NULL,
                config_json TEXT NOT NULL,
                outline TEXT NOT NULL,
                script_excerpt TEXT,
                warning_defects TEXT DEFAULT '[]',
                highlights TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS negative_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                era TEXT NOT NULL,
                genre TEXT NOT NULL,
                defect_json TEXT NOT NULL,
                position TEXT DEFAULT '',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS output_scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                era TEXT, genre TEXT, audience TEXT,
                outline TEXT,
                script TEXT,
                retry_count INTEGER DEFAULT 0,
                defects_json TEXT DEFAULT '[]',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_pos_era_genre ON positive_samples(era, genre);
            CREATE INDEX IF NOT EXISTS idx_neg_era_genre ON negative_samples(era, genre);
        """)
    logger.info("Hermes DB initialized")


def save_positive(era: str, genre: str, config: dict,
                  outline: str, script: str,
                  warnings: list, highlights: str = ""):
    """归档 A+ 优质剧本"""
    try:
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO positive_samples (era, genre, config_json, outline,
                   script_excerpt, warning_defects, highlights)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (era, genre, json.dumps(config, ensure_ascii=False),
                 outline[:500], script[:500],
                 json.dumps(warnings, ensure_ascii=False), highlights)
            )
        logger.info(f"Hermes: 正向样本入库 [{era}/{genre}]")
    except Exception as e:
        logger.warning(f"Hermes: 正向样本入库失败: {e}")


def save_negative(era: str, genre: str, defects: list):
    """归档缺陷样本"""
    try:
        with _get_conn() as conn:
            for d in defects:
                conn.execute(
                    """INSERT INTO negative_samples (era, genre, defect_json, position)
                       VALUES (?, ?, ?, ?)""",
                    (era, genre, json.dumps(d, ensure_ascii=False),
                     d.get("position", ""))
                )
        logger.info(f"Hermes: 反向样本入库 [{era}/{genre}] {len(defects)}条")
    except Exception as e:
        logger.warning(f"Hermes: 反向样本入库失败: {e}")


def save_output(session_id: str, era: str, genre: str, audience: str,
                outline: str, script: str, retry_count: int, defects: list):
    """保存完整输出记录"""
    try:
        with _get_conn() as conn:
            conn.execute(
                """INSERT INTO output_scripts (session_id, era, genre, audience,
                   outline, script, retry_count, defects_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, era, genre, audience,
                 outline[:500], script,
                 retry_count, json.dumps(defects, ensure_ascii=False))
            )
    except Exception as e:
        logger.warning(f"Hermes: 输出记录入库失败: {e}")


def get_similar_positive(era: str, genre: str, limit: int = 3) -> List[Dict]:
    """获取同类目优质样本，辅助创意复用"""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM positive_samples
                   WHERE era = ? AND genre = ?
                   ORDER BY id DESC LIMIT ?""",
                (era, genre, limit)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"Hermes: 查询优质样本失败: {e}")
        return []


def get_common_defects(era: str, genre: str, limit: int = 5) -> List[str]:
    """获取同类目常见缺陷，提示避免"""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                """SELECT defect_json FROM negative_samples
                   WHERE era = ? AND genre = ?
                   ORDER BY id DESC LIMIT ?""",
                (era, genre, limit)
            ).fetchall()
            defects = set()
            for r in rows:
                d = json.loads(r["defect_json"])
                defects.add(d.get("desc", ""))
            return list(defects)
    except Exception as e:
        logger.warning(f"Hermes: 查询缺陷样本失败: {e}")
        return []


# 启动时初始化
init_db()
