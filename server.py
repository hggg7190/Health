import os
import json
import sqlite3
from datetime import datetime, timedelta
from contextlib import contextmanager

from fastapi import FastAPI, Request, HTTPException
from fastmcp import FastMCP

# ---------- 配置 ----------
DB_PATH = os.environ.get("DB_PATH", "/data/health.db")
API_TOKEN = os.environ.get("API_TOKEN", "")

# ---------- 数据库 ----------
@contextmanager
def get_db():
 conn = sqlite3.connect(DB_PATH)
 conn.row_factory = sqlite3.Row
 try:
 yield conn
 finally:
 conn.close()

def init_db():
 os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
 with get_db() as conn:
 conn.execute("""
 CREATE TABLE IF NOT EXISTS health_data (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 metric TEXT NOT NULL,
 value REAL NOT NULL,
 unit TEXT DEFAULT '',
 recorded_at TEXT NOT NULL,
 created_at TEXT DEFAULT (datetime('now'))
 )
 """)
 conn.execute("""
 CREATE INDEX IF NOT EXISTS idx_metric_date
 ON health_data (metric, recorded_at)
 """)
 conn.commit()

init_db()

# ---------- FastAPI ----------
api = FastAPI()

@api.post("/upload")
async def upload(request: Request):
 auth = request.headers.get("Authorization", "")
 if API_TOKEN and auth != f"Bearer {API_TOKEN}":
 raise HTTPException(status_code=401, detail="Unauthorized")

 body = await request.json()
 items = body.get("data", [body]) if "data" in body else [body]

 with get_db() as conn:
 for item in items:
 conn.execute(
 "INSERT INTO health_data (metric, value, unit, recorded_at) VALUES (?, ?, ?, ?)",
 (item["metric"], item["value"], item.get("unit", ""), item["recorded_at"])
 )
 conn.commit()

 return {"ok": True, "inserted": len(items)}

# ---------- FastMCP ----------
mcp = FastMCP("HealthData", stateless_http=True)

@mcp.tool()
def query_day(date: str, metric: str = "") -> str:
 """查某一天的健康数据。date 格式 YYYY-MM-DD，metric 留空查全部指标。"""
 with get_db() as conn:
 if metric:
 rows = conn.execute(
 "SELECT metric, value, unit, recorded_at FROM health_data WHERE recorded_at = ? AND metric = ?",
 (date, metric)
 ).fetchall()
 else:
 rows = conn.execute(
 "SELECT metric, value, unit, recorded_at FROM health_data WHERE recorded_at = ?",
 (date,)
 ).fetchall()
 if not rows:
 return f"{date} 没有{'该指标的' if metric else ''}数据"
 return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)

@mcp.tool()
def query_range(start: str, end: str, metric: str = "") -> str:
 """查日期范围内的数据。start/end 格式 YYYY-MM-DD。"""
 with get_db() as conn:
 if metric:
 rows = conn.execute(
 "SELECT metric, value, unit, recorded_at FROM health_data WHERE recorded_at BETWEEN ? AND ? AND metric = ? ORDER BY recorded_at",
 (start, end, metric)
 ).fetchall()
 else:
 rows = conn.execute(
 "SELECT metric, value, unit, recorded_at FROM health_data WHERE recorded_at BETWEEN ? AND ? ORDER BY recorded_at",
 (start, end)
 ).fetchall()
 if not rows:
 return "该范围内没有数据"
 return json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)

@mcp.tool()
def query_week_summary(metric: str, end_date: str = "") -> str:
 """查某指标最近7天的汇总。end_date 留空默认今天。"""
 if not end_date:
 end_date = datetime.now().strftime("%Y-%m-%d")
 start_date = (datetime.strptime(end_date, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
 with get_db() as conn:
 row = conn.execute(
 "SELECT COUNT(*) as days, AVG(value) as avg, MAX(value) as max, MIN(value) as min, SUM(value) as total FROM health_data WHERE metric = ? AND recorded_at BETWEEN ? AND ?",
 (metric, start_date, end_date)
 ).fetchone()
 if not row or row["days"] == 0:
 return f"{metric} 在 {start_date}~{end_date} 没有数据"
 return json.dumps(dict(row), ensure_ascii=False, indent=2)

@mcp.tool()
def compare_weeks(metric: str, end_date: str = "") -> str:
 """对比本周 vs 上周某个指标。"""
 if not end_date:
 end_date = datetime.now().strftime("%Y-%m-%d")
 end_dt = datetime.strptime(end_date, "%Y-%m-%d")
 this_start = (end_dt - timedelta(days=6)).strftime("%Y-%m-%d")
 last_end = (end_dt - timedelta(days=7)).strftime("%Y-%m-%d")
 last_start = (end_dt - timedelta(days=13)).strftime("%Y-%m-%d")

 with get_db() as conn:
 def week_stats(s, e):
 return dict(conn.execute(
 "SELECT COUNT(*) as days, AVG(value) as avg, SUM(value) as total FROM health_data WHERE metric = ? AND recorded_at BETWEEN ? AND ?",
 (metric, s, e)
 ).fetchone())
 this_week = week_stats(this_start, end_date)
 last_week = week_stats(last_start, last_end)

 return json.dumps({"this_week": this_week, "last_week": last_week}, ensure_ascii=False, indent=2)

@mcp.tool()
def list_metrics() -> str:
 """列出数据库里已有的所有指标名。"""
 with get_db() as conn:
 rows = conn.execute("SELECT DISTINCT metric FROM health_data ORDER BY metric").fetchall()
 return json.dumps([r["metric"] for r in rows], ensure_ascii=False)

# ---------- 挂载 ----------
api.mount("/mcp", mcp.streamable_http_app())

if __name__ == "__main__":
 import uvicorn
 uvicorn.run(api, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
