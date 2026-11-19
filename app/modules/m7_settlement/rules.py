import hashlib,json
from decimal import Decimal
from app.errors import DomainError
from app.reasons import sources
from app.passes import card_day_totals


def _reconcile(context):
    found=None
    for value in context.values():
        if isinstance(value,dict) and isinstance(value.get("metrics"),dict):
            if "balanced" in value["metrics"] or "card_day_differences" in value["metrics"]:
                found=value
    return found


def evaluate(rows,context):
    stages=[v for v in context.values() if isinstance(v,dict) and "metrics" in v]
    balances=[s["metrics"]["balanced"] for s in stages if "balanced" in s["metrics"]]
    if balances and not all(balances): raise DomainError("UNBALANCED","来源行未对平，不能归档")
    # 对账未平（来源行不平，或被显式标记为未平）不能封存日结。
    reconcile=_reconcile(context)
    if reconcile is not None:
        metrics=reconcile["metrics"]
        if metrics.get("balanced") is False:
            raise DomainError("UNBALANCED","来源行未对平，不能归档")
        if metrics.get("reconciled") is False:
            raise DomainError("UNRECONCILED","对账未平，不能封存日结")
    ids=[str(r.get("event_id",r.get("_line",""))) for r in rows]
    if len(ids)!=len(set(ids)): raise DomainError("ARCHIVE_DUPLICATE","归档业务键重复")
    encoded=json.dumps(rows,ensure_ascii=False,sort_keys=True,default=str).encode()
    totals=card_day_totals(rows)
    return {"rows":rows,"card_day_totals":totals,"quarantine":[],"metrics":{
        "records":len(rows),
        "source_lines":len(set(x for row in rows for x in sources(row))),
        "sha256":hashlib.sha256(encoded).hexdigest(),
        "ready":True,
        "differences":sum(r.get("status")=="差异" for r in rows),
        "card_days":len(totals),
        "card_day_differences":sum(Decimal(t["difference"])!=0 for t in totals),
        "card_day_expected_total":str(sum((Decimal(t["expected"]) for t in totals),Decimal(0))),
    }}
