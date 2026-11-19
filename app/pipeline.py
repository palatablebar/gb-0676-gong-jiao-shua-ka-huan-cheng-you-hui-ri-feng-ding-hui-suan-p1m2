import time
from app.config import ROOT,MODULES
from app.errors import DomainError
from app.values import scalar
from app.manifest import digest,manifest
from app import store
from app.modules.m1_taps.rules import evaluate as stage1
from app.modules.m2_routes.rules import evaluate as stage2
from app.modules.m3_fares.rules import evaluate as stage3
from app.modules.m4_journeys.rules import evaluate as stage4
from app.modules.m5_discounts.rules import evaluate as stage5
from app.modules.m6_clearing.rules import evaluate as stage6
from app.modules.m7_settlement.rules import evaluate as stage7
STAGES=[stage1,stage2,stage3,stage4,stage5,stage6,stage7]


def load_sample(name):
    if name not in {"dirty.csv","clean.csv","legacy.csv","monthly.csv"}: raise DomainError("BAD_SAMPLE","样例不存在",404)
    return (ROOT/"sample_data"/name).read_bytes()


def _previous_run(params,raw=None):
    """取同批次最近一次成功批次，供月票核验失败时保留旧版结果。

    显式传入 previous_run 时优先使用；否则按输入摘要查找上次核验通过的批次。
    """
    run_id=params.get("previous_run") if isinstance(params,dict) else None
    if run_id:
        try: return store.get(str(run_id))
        except DomainError: return None
    if raw is None: return None
    import hashlib
    found=store.latest_good(hashlib.sha256(raw).hexdigest())
    if found: return store.get(found)
    return None


def calculate(raw,params=None,stop=6):
    params=params or {};started=time.monotonic();context={"parameters":params,"raw":raw,"quarantine":[]};stages=[];rows=[]
    previous=_previous_run(params,raw)
    if previous is not None: context["previous_run"]=previous
    for index,stage in enumerate(STAGES[:stop+1]):
        if index==4 and previous is not None:
            context["previous_discounts"]=previous
        result=stage(raw if index==0 else rows,context)
        result=scalar(result);rows=result["rows"]
        context["quarantine"].extend(result.get("quarantine",[]))
        if index==0: context["intake"]=result["metrics"]
        result["cumulative_quarantine"]=list(context["quarantine"])
        context[MODULES[index][0]]=result
        stages.append(result)
    run={"run_id":digest(raw,params)[:20],"stages":stages,"manifest":manifest(raw,params,started),"parameters":params}
    run["card_day_totals"]=_card_day_totals(stages)
    run["fallback"]=_fallback(stages)
    return run


def _card_day_totals(stages):
    discounts=stages[4] if len(stages)>4 else None
    if isinstance(discounts,dict) and isinstance(discounts.get("card_day_totals"),list):
        return discounts["card_day_totals"]
    return []


def _fallback(stages):
    discounts=stages[4] if len(stages)>4 else None
    if isinstance(discounts,dict) and isinstance(discounts.get("fallback"),dict):
        return discounts["fallback"]
    return {"used":False,"reason":"","kept_run_id":""}


def execute(raw,params=None):
    result=calculate(raw,params)
    previous=result.get("fallback",{}).get("kept_run_id")
    if previous:
        # 月票核验失败：保留旧版结果，新批次只作为差异记录，不覆盖旧批次。
        result["preserved_run_id"]=previous
        return store.save_preserving(result["run_id"],result,previous)
    return store.save(result["run_id"],result)


def stage_preview(index,payload):
    if "rows" in payload and index>0:
        if not isinstance(payload["rows"],list) or any(not isinstance(row,dict) for row in payload["rows"]): raise DomainError("BAD_ROWS","记录需要对象列表")
        if not isinstance(payload.get("context",{}),dict): raise DomainError("BAD_CONTEXT","上下文需要对象")
        return scalar(STAGES[index](payload["rows"],payload.get("context",{"parameters":{},"quarantine":[]})))
    raw=payload.get("csv")
    raw=raw.encode() if isinstance(raw,str) else load_sample(payload.get("sample","dirty.csv"))
    return calculate(raw,payload.get("parameters",{}),index)["stages"][-1]
