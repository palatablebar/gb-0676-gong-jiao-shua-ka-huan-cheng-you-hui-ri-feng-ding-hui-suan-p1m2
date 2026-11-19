from decimal import Decimal
from app.reasons import sources
from app.errors import DomainError
from app.passes import card_day_totals


def evaluate(rows,context):
    intake=context.get("intake")
    bad=context.get("quarantine",[])
    accepted=set(x for row in rows for x in sources(row))
    rejected=set(x for row in bad for x in sources(row))
    if accepted & rejected: raise DomainError("LINEAGE_OVERLAP","同一来源行被重复裁决")
    m={"accepted_inputs":len(accepted),"quarantine_inputs":len(rejected),"output_records":len(rows)}
    if intake:
        excluded=intake["duplicate"]+intake["blank"]+intake["repeated_headers"]
        m.update(input=intake["input"],excluded=excluded,balanced=intake["input"]==len(accepted)+len(rejected)+excluded)
    else:m.update(input=len(accepted)+len(rejected),excluded=0,balanced=True)
    output=[]
    for row in rows:
        expected=Decimal(str(row.get("expected","0")));reported=Decimal(str(row.get("reported","0")))
        output.append({**row,"difference":str(expected-reported),"status":"通过" if expected==reported else "差异"})
    m.update(expected_total=str(sum((Decimal(str(r.get("expected","0"))) for r in rows),Decimal(0))),reported_total=str(sum((Decimal(str(r.get("reported","0"))) for r in rows),Decimal(0))),differences=sum(r["status"]=="差异" for r in output))
    # 每卡每业务日只保留一份换乘与封顶应收；差异也按卡日归并，供日结与页面复核。
    totals=card_day_totals(output)
    card_day_expected=sum((Decimal(t["expected"]) for t in totals),Decimal(0))
    total_expected=Decimal(m["expected_total"]);total_reported=Decimal(m["reported_total"])
    total_difference=sum((Decimal(r["difference"]) for r in output),Decimal(0))
    conserved=card_day_expected==total_expected and total_difference==total_expected-total_reported
    # 复核员可通过参数显式标记对账未平（例如上游申报尚待确认）。
    explicit=(context.get("parameters") or {}).get("reconciled")
    if explicit is None:flagged=True
    elif isinstance(explicit,bool):flagged=explicit
    else:flagged=str(explicit).strip().lower() not in {"false","0","no","unreconciled","未平"}
    m.update(card_days=len(totals),card_day_differences=sum(Decimal(t["difference"])!=0 for t in totals),
             card_day_expected_total=str(card_day_expected),
             reconciled=bool(m.get("balanced",True)) and conserved and flagged)
    return {"rows":output,"card_day_totals":totals,"quarantine":[],"metrics":m}
