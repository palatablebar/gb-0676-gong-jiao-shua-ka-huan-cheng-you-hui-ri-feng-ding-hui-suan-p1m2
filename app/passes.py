"""零扣费月票行程的判定与月票状态核验。

裁决口径（见 docs/DEPTH.md TRAP-27）：实扣为零的行程视为月票覆盖。它在现金换乘中
承担“前序”角色——后续现金行程可据此比较线路并取得换乘优惠；但它不移动换乘窗口的
起算时钟，也不占用日封顶额度。换乘窗口始终由最近一次实际付费行程起算，若当日尚无
付费行程，则由当日首个行程（必须是月票行程）起算；连续刷月票不会反复续期优惠。

月票状态核验失败时，调用方回退到旧版口径（只有实扣大于零的行程才能作为锚点），
同时保留旧版结果不覆盖（见 app/pipeline.py、app/store.py）。
"""

FAILURE_WORDS = {
    "failed", "failure", "error", "unverified", "invalid", "rejected",
    "fail", "核验失败", "失败", "未核验", "无效", "异常",
}
TRUTHY_WORDS = {"1", "true", "yes", "y", "是", "月票", "pass", "monthly"}
FALSY_WORDS = {"0", "false", "no", "n", "否", "现金", "cash"}
PASS_WORDS = {
    "monthly_pass", "monthlypass", "monthly", "pass", "mpass",
    "月票", "月卡", "p", "定期票",
}
VERIFICATION_KEYS = (
    "pass_verification", "pass_status", "pass_check", "month_pass_status",
    "monthly_pass_status", "passes_verified", "pass_verified",
    "monthly_pass_verified",
)
PASS_FLAG_KEYS = ("pass", "is_pass", "monthly_pass", "is_monthly_pass")
PASS_TYPE_KEYS = ("ticket_type", "fare_type", "product", "pass_type", "ticket")
STATUS_KEYS = ("pass_status", "pass_verification", "ticket_status", "monthly_pass_status")


def _text(value):
    return "" if value is None else str(value).strip().lower()


def verification_value(params):
    """从运行参数中取出月票核验状态，缺省视为通过。"""
    params = params or {}
    for key in VERIFICATION_KEYS:
        if key in params:
            return params[key]
    nested = params.get("pass")
    if isinstance(nested, dict):
        for key in ("verification", "status", "verified"):
            if key in nested:
                return nested[key]
    return "ok"


def verified(params):
    value = verification_value(params)
    if isinstance(value, bool):
        return value
    return _text(value) not in FAILURE_WORDS


def row_verified(row):
    if not isinstance(row, dict):
        return True
    for key in STATUS_KEYS:
        if key in row and _text(row[key]) in FAILURE_WORDS:
            return False
    for key in PASS_FLAG_KEYS:
        if key in row and _text(row[key]) in FAILURE_WORDS:
            return False
    return True


def _flag(row, key):
    if key not in row:
        return None
    value = row[key]
    if isinstance(value, bool):
        return value
    text = _text(value)
    if text in TRUTHY_WORDS:
        return True
    if text in FALSY_WORDS:
        return False
    return None


def explicit_pass(row):
    """显式月票标记（用于带 ticket_type/pass 列或直接调用模块的场景）。"""
    if not isinstance(row, dict):
        return False
    for key in PASS_FLAG_KEYS:
        flag = _flag(row, key)
        if flag is not None:
            return flag
    for key in PASS_TYPE_KEYS:
        if _text(row.get(key)) in PASS_WORDS:
            return True
    return False


def zero_deduction(row):
    """实扣为零即为零扣费行程；缺失实扣时不能当作零。"""
    if not isinstance(row, dict):
        return False
    if row.get("reported") in (None, ""):
        return False
    try:
        from app.values import number
        return number(row["reported"]) == 0
    except Exception:
        return False


def pass_covered(row, normal_charge=None):
    """判断该行程是否由月票覆盖。

    显式 ticket_type/pass 标记优先；否则以“实扣为零，且按现金规则本应扣费大于零”
    为准。换乘优惠或日封顶已把应扣降到零的行程，是现金优惠行程，不是月票。
    normal_charge 为按当月价与优惠参数算出的现金应扣（未考虑月票）。
    """
    if not isinstance(row, dict):
        return False
    if explicit_pass(row):
        return True
    if not zero_deduction(row):
        return False
    if normal_charge is None:
        return True
    try:
        from app.values import number
        return number(normal_charge) > 0
    except Exception:
        return True


def _amount(row, field):
    value = row.get(field)
    if value in (None, ""):
        return None
    try:
        from app.values import number
        return number(value)
    except Exception:
        return None


def card_day_totals(rows):
    """按 (card, business_day) 汇总，得到每张卡当日唯一的一份换乘与封顶应收。

    返回列表按 (card, business_day) 排序，键唯一；同一卡日无论有多少条行程
    （含重跑产生的重复行）都只归并到一条汇总，避免重复占额。
    """
    from decimal import Decimal
    from app.values import money

    zero = Decimal("0")
    groups = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("card", "")), str(row.get("business_day", "")))
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "card": key[0], "business_day": key[1],
                "journeys": 0, "transfer_hits": 0, "cap_hits": 0,
                "pass_journeys": 0, "anchors": [],
                "_transfer": zero, "_cap": zero, "_pass": zero,
                "_expected": zero, "_reported": zero,
            }
        group["journeys"] += 1
        transfer = _amount(row, "transfer_discount")
        cap = _amount(row, "cap_discount")
        covered = _amount(row, "pass_covered")
        expected = _amount(row, "expected")
        reported = _amount(row, "reported")
        if transfer is not None:
            group["_transfer"] += transfer
            if transfer > 0:
                group["transfer_hits"] += 1
        if cap is not None:
            group["_cap"] += cap
            if cap > 0:
                group["cap_hits"] += 1
        if covered is not None:
            group["_pass"] += covered
        if expected is not None:
            group["_expected"] += expected
        if reported is not None:
            group["_reported"] += reported
        if row.get("ticket_kind")=="月票" or (row.get("ticket_kind") is None and zero_deduction(row)):
            group["pass_journeys"] += 1
        anchor = row.get("transfer_anchor")
        if anchor not in (None, "", "—"):
            group["anchors"].append({
                "event_id": str(anchor),
                "route": str(row.get("transfer_anchor_route", "—")),
                "at": str(row.get("transfer_anchor_at", "—")),
                "kind": str(row.get("transfer_anchor_kind", "—")),
            })

    result = []
    for key in sorted(groups):
        group = groups[key]
        result.append({
            "card": group["card"], "business_day": group["business_day"],
            "journeys": group["journeys"],
            "transfer_hits": group["transfer_hits"],
            "cap_hits": group["cap_hits"],
            "pass_journeys": group["pass_journeys"],
            "transfer_discount": money(group["_transfer"]),
            "cap_discount": money(group["_cap"]),
            "pass_covered": money(group["_pass"]),
            "expected": money(group["_expected"]),
            "reported": money(group["_reported"]),
            "difference": money(group["_expected"] - group["_reported"]),
            "anchors": group["anchors"],
        })
    return result


def card_day_keys(rows):
    return [str(r.get("card", "")) + "|" + str(r.get("business_day", "")) for r in rows]


