import json, logging, os, sqlite3
from datetime import datetime

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "usage.db")

def get_daily_report(date_str: str = None):
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")
    
    if not os.path.exists(DB_PATH):
        return {"date": date_str, "total_cost": 0, "total_calls": 0, "by_model": []}
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute("""SELECT model_name, model_type, provider,
                  COUNT(*) as calls,
                  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success,
                  SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as failed,
                  ROUND(SUM(cost), 4) as cost,
                  SUM(total_tokens) as tokens,
                  SUM(image_count) as images,
                  SUM(char_count) as chars,
                  SUM(video_duration) as video_sec
           FROM model_usage_logs WHERE date(created_at)=?
           GROUP BY model_name ORDER BY cost DESC""", (date_str,))
    by_model = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT ROUND(SUM(cost),4) as total_cost, COUNT(*) as total_calls FROM model_usage_logs WHERE date(created_at)=?", (date_str,))
    total = dict(c.fetchone())
    
    c.execute("SELECT ROUND(SUM(cost),4) as total_cost, COUNT(*) as total_calls FROM model_usage_logs")
    all_time = dict(c.fetchone())
    
    conn.close()
    
    return {
        "date": date_str,
        "total_cost": total.get("total_cost") or 0,
        "total_calls": total.get("total_calls") or 0,
        "all_time_cost": all_time.get("total_cost") or 0,
        "all_time_calls": all_time.get("total_calls") or 0,
        "by_model": by_model,
    }

def get_monthly_report(year: int = None, month: int = None):
    now = datetime.now()
    if not year: year = now.year
    if not month: month = now.month
    
    month_str = f"{year}-{month:02d}"
    
    if not os.path.exists(DB_PATH):
        return {"month": month_str, "total_cost": 0, "total_calls": 0, "by_model": []}
    
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute("""SELECT model_name, model_type, provider,
                  COUNT(*) as calls,
                  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success,
                  SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as failed,
                  ROUND(SUM(cost), 4) as cost
           FROM model_usage_logs WHERE strftime('%Y-%m', created_at)=?
           GROUP BY model_name ORDER BY cost DESC""", (month_str,))
    by_model = [dict(r) for r in c.fetchall()]
    
    c.execute("SELECT ROUND(SUM(cost),4) as total_cost, COUNT(*) as total_calls FROM model_usage_logs WHERE strftime('%Y-%m', created_at)=?", (month_str,))
    total = dict(c.fetchone())
    
    conn.close()
    
    return {
        "month": month_str,
        "total_cost": total.get("total_cost") or 0,
        "total_calls": total.get("total_calls") or 0,
        "by_model": by_model,
    }

def format_wechat_report(report: dict) -> str:
    lines = [f"Daily Bill {report['date']}"]
    lines.append("=" * 20)
    lines.append(f"Cost: ${report['total_cost']:.4f}")
    lines.append(f"Calls: {report['total_calls']}")
    lines.append("")
    
    for m in report.get("by_model", [])[:10]:
        cost = m.get("cost") or 0
        calls = m.get("calls", 0)
        name = m.get("model_name", "")
        succ = m.get("success", 0)
        fail = m.get("failed", 0)
        st = f"OK:{succ}" if succ else ""
        if fail: st += f" ERR:{fail}"
        lines.append(f"  {name}: ${cost:.4f} ({calls}x {st})")
    
    lines.append("=" * 20)
    lines.append(f"Total: ${report.get('all_time_cost', 0):.2f} / {report.get('all_time_calls', 0)}x")
    return "\n".join(lines)