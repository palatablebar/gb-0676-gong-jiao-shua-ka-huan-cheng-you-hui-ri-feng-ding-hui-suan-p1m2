from decimal import Decimal
from app.values import number,instant,money
from app.errors import DomainError
from app import passes
from app.config import RULE_VERSION,LEGACY_RULE_VERSION
ZERO=Decimal("0")


def _parameters(context):
    params=(context or {}).get("parameters",{}) or {}
    cap=number(params.get("daily_cap","6.00"));off=number(params.get("transfer_discount","1.00"));window=number(params.get("transfer_minutes","60"))
    if cap<0 or off<0 or window<0:raise DomainError("BAD_DISCOUNT","优惠参数不能为负")
    return params,cap,off,window


def _minutes(later,earlier):
    return Decimal(str((instant(later)-instant(earlier)).total_seconds()))/60


def _legacy(rows,cap,off,window):
    """旧版口径：只有实扣大于零的行程才会成为换乘锚点。

    月票核验失败时原样回退到旧版结果，保证复核口径可追溯。
    """
    spent={};anchor={};good=[]
    for row in sorted(rows,key=lambda r:(r["card"],instant(r["at"]),r["event_id"])):
        key=(row["card"],row["business_day"]);base=number(row["base_fare"]);previous=anchor.get(key);eligible=False
        if previous:
            delta=_minutes(row["at"],previous["at"])
            eligible=previous["route"]!=row["route"] and ZERO<=delta<window
        transfer=min(base,off) if eligible else ZERO
        after_transfer=base-transfer;remaining=max(ZERO,cap-spent.get(key,ZERO));charge=min(after_transfer,remaining)
        cap_discount=after_transfer-charge;spent[key]=spent.get(key,ZERO)+charge
        is_pass=passes.pass_covered(row,min(after_transfer,remaining))
        if is_pass:kind="月票"
        elif charge>0:kind="现金"
        elif cap_discount>0:kind="封顶"
        elif transfer>0:kind="换乘"
        else:kind="零价"
        good.append({**row,"transfer_anchor":previous["event_id"] if eligible else "—",
                     "transfer_anchor_route":previous["route"] if eligible else "—",
                     "transfer_anchor_at":previous["at"] if eligible else "—",
                     "transfer_anchor_kind":"现金" if eligible else "—",
                     "transfer_discount":money(transfer),"cap_discount":money(cap_discount),
                     "pass_covered":money(base if is_pass else ZERO),"ticket_kind":kind,
                     "expected":money(charge),"day_paid":money(spent[key]),
                     "rule_mode":"legacy_fallback"})
        if charge>0:anchor[key]=row
    return {"rows":good,"quarantine":[]}


def _summaries(rows):
    from app.passes import card_day_totals
    return card_day_totals(rows)


def _dedupe(rows):
    """同一批重跑（同一事件重复入账）不得重复占用换乘/封顶额度。

    先按卡号与时间排序，同一 event_id 只保留首条，保证结果与输入顺序无关。
    """
    ordered=sorted((r for r in rows if isinstance(r,dict)),
                   key=lambda r:(str(r.get("card","")),str(r.get("at","")),str(r.get("event_id",""))))
    seen=set();out=[]
    for row in ordered:
        event=row.get("event_id")
        if event not in (None,""):
            key=(str(row.get("card","")),str(row.get("business_day","")),str(event))
            if key in seen:continue
            seen.add(key)
        out.append(row)
    return out


def evaluate(rows,context):
    params,cap,off,window=_parameters(context)
    prior_run=context.get("previous_discounts") if isinstance(context,dict) else None
    rows=_dedupe(rows)
    verified=passes.verified(params) and all(passes.row_verified(row) for row in rows)
    if not verified:
        result=_legacy(rows,cap,off,window)
        summaries=_summaries(result["rows"])
        result["metrics"]={
            "expected_total":money(sum((number(r["expected"]) for r in result["rows"]),ZERO)),
            "daily_caps":len(summaries),
            "card_days":len(summaries),
            "transfer_hits":sum(r["transfer_anchor"]!="—" for r in result["rows"]),
            "cap_hits":sum(number(r["cap_discount"])>0 for r in result["rows"]),
            "pass_journeys":sum(passes.zero_deduction(r) for r in result["rows"]),
            "rule_mode":"legacy_fallback",
            "pass_verification":"failed",
            "effective_rule_version":LEGACY_RULE_VERSION,
            "fallback_from":RULE_VERSION,
        }
        result["card_day_totals"]=summaries
        result["fallback"]=_fallback_summary(prior_run,result)
        return result

    spent={};clock={};precursor={};good=[]
    for row in sorted(rows,key=lambda r:(r["card"],instant(r["at"]),r["event_id"])):
        key=(row["card"],row["business_day"]);base=number(row["base_fare"])
        remaining=max(ZERO,cap-spent.get(key,ZERO))
        previous=precursor.get(key);window_from=clock.get(key)
        eligible=False;transfer=ZERO
        # 换乘窗口始终从上一次实际付费行程起算；月票行程只作为线路比较的前序，
        # 不会把窗口往后推，因此连续刷月票不会反复续期优惠。
        if previous is not None and window_from is not None:
            delta=_minutes(row["at"],window_from)
            eligible=previous["route"]!=row["route"] and ZERO<=delta<window
            if eligible:transfer=min(base,off)
        after_transfer=base-transfer
        normal_charge=min(after_transfer,remaining)
        # 只有“本应扣费而实扣为零”的行程才是月票；已被换乘或封顶优惠降到零的是现金行程。
        is_pass=passes.pass_covered(row,normal_charge)
        if is_pass:
            transfer=ZERO;eligible=False;charge=ZERO;cap_discount=ZERO;pass_covered=base
        else:
            charge=normal_charge;cap_discount=after_transfer-charge;pass_covered=ZERO
        spent[key]=spent.get(key,ZERO)+charge
        if is_pass:kind="月票"
        elif charge>0:kind="现金"
        elif cap_discount>0:kind="封顶"
        elif transfer>0:kind="换乘"
        else:kind="零价"
        record={**row,
                     "transfer_anchor":previous["event_id"] if eligible else "—",
                     "transfer_anchor_route":previous["route"] if eligible else "—",
                     "transfer_anchor_at":previous["at"] if eligible else "—",
                     "transfer_anchor_kind":previous.get("ticket_kind","现金") if eligible else "—",
                     "transfer_discount":money(transfer),"cap_discount":money(cap_discount),
                     "pass_covered":money(pass_covered),"ticket_kind":kind,
                     "expected":money(charge),"day_paid":money(spent[key]),
                     "rule_mode":"pass_precursor"}
        good.append(record)
        # 只有实际付费行程推进换乘时钟；当日首条行程仅在其为月票时作为最初基准，
        # 之后的月票连续刷卡不会反复续期。零价非月票行程不建立锚点，沿用旧口径。
        if not is_pass and charge>0:clock[key]=row["at"]
        elif key not in clock and is_pass:clock[key]=row["at"]
        # 前序只由实际付费行程或月票行程刷新；封顶吸收到零的行程保持旧前序，
        # 与旧版“charge>0 才作锚点”的口径一致，避免同线路判断漂移。
        if is_pass or charge>0:precursor[key]=record

    summaries=_summaries(good)
    result={"rows":good,"quarantine":[],"card_day_totals":summaries,"metrics":{
        "expected_total":money(sum((number(r["expected"]) for r in good),ZERO)),
        "daily_caps":len(summaries),
        "card_days":len(summaries),
        "transfer_hits":sum(r["transfer_anchor"]!="—" for r in good),
        "cap_hits":sum(number(r["cap_discount"])>0 for r in good),
        "pass_journeys":sum(r.get("ticket_kind")=="月票" for r in good),
        "rule_mode":"pass_precursor",
        "pass_verification":"ok",
        "effective_rule_version":RULE_VERSION,
    }}
    result["fallback"]={"used":False,"reason":"","kept_run_id":""}
    return result


def _fallback_summary(previous,result):
    kept=None
    if isinstance(previous,dict):
        kept=str(previous.get("run_id","")) or None
    return {"used":True,"reason":"月票状态核验失败","kept_run_id":kept,
            "rule_mode":result["metrics"]["rule_mode"],
            "effective_rule_version":result["metrics"]["effective_rule_version"]}
