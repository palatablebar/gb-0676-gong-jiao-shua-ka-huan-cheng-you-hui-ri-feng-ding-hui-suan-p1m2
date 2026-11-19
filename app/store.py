import sqlite3,json
from pathlib import Path
from app.config import DATA_DIR
from app.errors import DomainError
def connect():
    DATA_DIR.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(DATA_DIR/"records.db",timeout=20)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY,payload TEXT NOT NULL,version INTEGER NOT NULL DEFAULT 1,decision TEXT NOT NULL DEFAULT '待复核')")
    db.execute("CREATE TABLE IF NOT EXISTS attempts (id INTEGER PRIMARY KEY AUTOINCREMENT,run_id TEXT NOT NULL,kept_run_id TEXT,status TEXT NOT NULL,payload TEXT NOT NULL)")
    return db
def save(run_id,payload):
    with connect() as db:
        db.execute("INSERT OR IGNORE INTO runs(id,payload) VALUES (?,?)",(run_id,json.dumps(payload,ensure_ascii=False)))
    return get(run_id)
def save_preserving(run_id,payload,previous_id):
    """月票核验失败：保留旧版结果，新计算只作为差异记录，不写入新批次。

    同一份输入重跑会再次落到同一旧批次上，因此不会重复占额，也不会改变历史；
    失败尝试只追加到 attempts 审计表。
    """
    kept=get(previous_id)
    incoming={"incoming_run_id":run_id,"attempted_rule_version":payload.get("manifest",{}).get("rule_version",""),
              "fallback":payload.get("fallback",{}),"input_sha256":payload.get("manifest",{}).get("input_sha256","")}
    with connect() as db:
        db.execute("INSERT INTO attempts(run_id,kept_run_id,status,payload) VALUES (?,?,?,?)",
                   (run_id,previous_id,"pass_verification_failed",json.dumps(payload,ensure_ascii=False)))
    return {**kept,"fallback":payload.get("fallback",{}),"preserved":True,"incoming":incoming}
def get(run_id):
    with connect() as db: row=db.execute("SELECT payload,version,decision FROM runs WHERE id=?",(run_id,)).fetchone()
    if not row: raise DomainError("NOT_FOUND","批次不存在",404)
    return {**json.loads(row[0]),"version":row[1],"decision":row[2]}
def latest():
    with connect() as db: row=db.execute("SELECT id FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
    return get(row[0]) if row else None
def latest_good(input_sha256):
    """最近一次月票核验通过且输入摘要相同的批次，用于核验失败时保留旧版结果。"""
    with connect() as db: rows=db.execute("SELECT payload FROM runs ORDER BY rowid DESC").fetchall()
    for (payload,) in rows:
        try: run=json.loads(payload)
        except ValueError: continue
        if run.get("manifest",{}).get("input_sha256")!=input_sha256: continue
        stages=run.get("stages") or []
        if len(stages)>4 and stages[4].get("metrics",{}).get("pass_verification")=="ok":
            return run.get("run_id")
    return None
def history():
    with connect() as db: return [{"run_id":i,"version":v,"decision":d} for i,v,d in db.execute("SELECT id,version,decision FROM runs ORDER BY rowid DESC")]
def review(run_id,version,decision):
    if decision not in {"已复核","退回复核"}: raise DomainError("BAD_DECISION","复核状态无效")
    with connect() as db:
        cursor=db.execute("UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?",(decision,run_id,version))
        if cursor.rowcount!=1:
            if not db.execute("SELECT 1 FROM runs WHERE id=?",(run_id,)).fetchone(): raise DomainError("NOT_FOUND","批次不存在",404)
            raise DomainError("STALE_VERSION","版本已更新，请刷新",409)
    return get(run_id)
