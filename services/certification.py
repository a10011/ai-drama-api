"""
创作原创证明服务
记录每步创作过程，生成可验证的创作证书
"""
import hashlib, json, time, sqlite3
from datetime import datetime, timezone
from typing import Optional

DB_PATH = "/www/wwwroot/api.mzsh.top/data/drama.db"

def _hash(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def log_step(project_id: str, user_id: int, stage: str,
             input_content: str = "", output_content: str = "",
             model_used: str = "", duration_ms: int = 0):
    input_hash = _hash(input_content) if input_content else ""
    output_hash = _hash(output_content) if output_content else ""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            "INSERT INTO creation_logs (project_id,user_id,stage,input_hash,output_hash,input_content,output_content,model_used,duration_ms) VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, user_id, stage, input_hash, output_hash, input_content[:5000], output_content[:5000], model_used, duration_ms)
        )
        conn.commit()
    finally:
        conn.close()

def generate_certificate(project_id: str, user_id: int, title: str = "", genre: str = "", video_url: str = "") -> dict:
    conn = sqlite3.connect(DB_PATH)
    try:
        # Check if certificate already exists
        existing = conn.execute(
            "SELECT id, created_at FROM creation_certificates WHERE project_id=?", (project_id,)
        ).fetchone()

        # Fetch logs
        rows = conn.execute(
            "SELECT stage, output_hash, model_used, duration_ms, created_at FROM creation_logs WHERE project_id=? ORDER BY id",
            (project_id,)
        ).fetchall()

        stages = [{"stage": r[0], "output_hash": r[1] or "", "model_used": r[2] or "", "duration_ms": r[3] or 0, "created_at": r[4] or ""} for r in rows]
        all_hashes = [r[1] for r in rows if r[1]]

        video_hash = _hash(video_url) if video_url else ""
        if video_hash:
            all_hashes.append(video_hash)

        master_hash = _hash("|".join(all_hashes)) if all_hashes else _hash(f"{project_id}:{int(time.time())}")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        if existing:
            conn.execute(
                "UPDATE creation_certificates SET title=?,genre=?,cert_hash=?,stages_json=?,video_hash=?,created_at=? WHERE project_id=?",
                (title, genre, master_hash, json.dumps(stages, ensure_ascii=False), video_hash, now, project_id)
            )
        else:
            conn.execute(
                "INSERT INTO creation_certificates (project_id,user_id,title,genre,cert_hash,stages_json,video_hash,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (project_id, user_id, title, genre, master_hash, json.dumps(stages, ensure_ascii=False), video_hash, now)
            )
        conn.commit()

        created_at = existing[1] if existing else now
        return {
            "project_id": project_id, "title": title, "genre": genre,
            "cert_hash": master_hash, "stages_count": len(stages),
            "video_hash": video_hash, "created_at": created_at
        }
    finally:
        conn.close()

def get_certificate(project_id: str) -> Optional[dict]:
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT id, project_id, user_id, title, genre, cert_hash, stages_json, video_hash, created_at FROM creation_certificates WHERE project_id=?",
            (project_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "id": row[0], "project_id": row[1], "user_id": row[2],
            "title": row[3] or "", "genre": row[4] or "",
            "cert_hash": row[5], "stages": json.loads(row[6] or "[]"),
            "video_hash": row[7] or "", "created_at": row[8] or ""
        }
    finally:
        conn.close()

def get_project_logs(project_id: str) -> list:
    conn = sqlite3.connect(DB_PATH)
    try:
        rows = conn.execute(
            "SELECT id, stage, input_hash, output_hash, model_used, duration_ms, created_at FROM creation_logs WHERE project_id=? ORDER BY id",
            (project_id,)
        ).fetchall()
        return [{"id": r[0], "stage": r[1], "input_hash": r[2] or "", "output_hash": r[3] or "",
                 "model_used": r[4] or "", "duration_ms": r[5] or 0, "created_at": r[6] or ""} for r in rows]
    finally:
        conn.close()
