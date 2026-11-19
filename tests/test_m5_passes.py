import pytest
from decimal import Decimal
from app.modules.m5_discounts.rules import evaluate
from app.errors import DomainError


def ride(event_id, minute, route="01", reported="2.50", base="2.50", card="0001", day="2026-11-16"):
    return {"event_id": str(event_id), "card": card, "business_day": day,
            "at": f"2026-11-16T{8 + minute // 60:02d}:{minute % 60:02d}:00Z",
            "route": route, "base_fare": base, "reported": reported}


def rows_of(rows, context=None):
    return {r["event_id"]: r for r in evaluate(rows, context or {})["rows"]}


def test_pass_is_transfer_precursor():
    "TRAP-27 零扣费月票充当现金换乘前序"
    rows = rows_of([ride("P", 0, "01", "0"), ride("C", 15, "02")])
    assert rows["C"]["transfer_anchor"] == "P"
    assert rows["C"]["transfer_anchor_kind"] == "月票"
    assert rows["C"]["transfer_discount"] == "1.00"
    assert rows["C"]["expected"] == "1.50"


def test_pass_itself_charges_nothing():
    rows = rows_of([ride("P", 0, "01", "0")])
    assert rows["P"]["expected"] == "0.00"
    assert rows["P"]["pass_covered"] == "2.50"
    assert rows["P"]["transfer_discount"] == "0.00"


def test_consecutive_passes_do_not_renew_window():
    "短时间连续刷月票不得反复续期优惠"
    rows = rows_of([ride("P1", 0, "01", "0"), ride("P2", 40, "02", "0"), ride("C", 70, "03")])
    # 窗口从当日首个行程起算，70 分钟后已出窗；第二条月票不续期。
    assert rows["C"]["transfer_anchor"] == "—"
    assert rows["C"]["expected"] == "2.50"


def test_pass_after_paid_does_not_move_clock():
    rows = rows_of([ride("A", 0, "01"), ride("P", 30, "02", "0"), ride("C", 65, "01")])
    assert rows["C"]["transfer_anchor"] == "—"
    assert rows["C"]["expected"] == "2.50"


def test_pass_between_paid_keeps_paid_clock():
    rows = rows_of([ride("A", 0, "01"), ride("P", 20, "02", "0"), ride("C", 55, "01")])
    # 55 分钟仍在 A 的窗口内，前序按线路比较取最近一条（月票 P，线路 02）。
    assert rows["C"]["transfer_anchor"] == "P"
    assert rows["C"]["expected"] == "1.50"


def test_same_route_pass_gives_no_transfer():
    rows = rows_of([ride("P", 0, "01", "0"), ride("C", 15, "01")])
    assert rows["C"]["expected"] == "2.50"


def test_cap_zero_row_is_not_pass():
    "封顶已用尽的零扣费是现金行程，不是月票"
    rows = rows_of([ride("A", 0, "01"), ride("B", 10, "02"), ride("C", 20, "03"),
                    ride("D", 30, "01"), ride("E", 40, "02")])
    assert rows["D"]["expected"] == "0.50"
    assert rows["D"]["ticket_kind"] == "现金"
    # E 与 D 同窗但线路不同，仍可享受换乘；应扣 1.50 全部被剩余封顶 0.50 吸收。
    assert rows["E"]["expected"] == "0.00"
    assert rows["E"]["ticket_kind"] == "封顶"
    assert rows["E"]["pass_covered"] == "0.00"


def test_card_day_unique_total():
    "每张卡当日只有一份换乘与封顶应收"
    result = evaluate([ride("P", 0, "01", "0"), ride("C1", 15, "02"), ride("C2", 30, "03")], {})
    totals = result["card_day_totals"]
    assert len(totals) == 1
    day = totals[0]
    assert day["transfer_discount"] == "2.00"
    assert day["expected"] == "3.00"
    assert [a["event_id"] for a in day["anchors"]] == ["P", "C1"]


def test_rerun_same_events_do_not_double_count():
    rows = [ride("P", 0, "01", "0"), ride("C", 15, "02")]
    first = evaluate(rows, {})
    second = evaluate(rows + rows, {})
    assert first["rows"] == second["rows"]
    assert first["card_day_totals"] == second["card_day_totals"]


def test_pass_verification_failure_uses_legacy():
    "月票核验失败时保留旧版结果"
    result = evaluate([ride("P", 0, "01", "0"), ride("C", 15, "02")],
                      {"parameters": {"pass_verification": "failed"}})
    by_id = {r["event_id"]: r for r in result["rows"]}
    assert by_id["C"]["transfer_anchor"] == "P"
    assert by_id["C"]["expected"] == "1.50"
    assert result["metrics"]["pass_verification"] == "failed"
    assert result["fallback"]["used"] is True


def test_row_level_verification_failure_falls_back():
    row = {**ride("P", 0, "01", "0"), "pass_status": "核验失败"}
    result = evaluate([row, ride("C", 15, "02")], {})
    assert result["metrics"]["rule_mode"] == "legacy_fallback"


def test_fallback_flag_from_row_level_failure():
    row = {**ride("P", 0, "01", "0"), "pass_status": "核验失败"}
    result = evaluate([row, ride("C", 15, "02")], {})
    assert result["fallback"]["used"] is True


def test_negative_parameter_still_rejected():
    with pytest.raises(DomainError):
        evaluate([], {"parameters": {"pass_verification": "failed", "daily_cap": "-1"}})
