import csv
import io
import pytest
from app import store
from app.config import ROOT
from app.errors import DomainError
from app.modules.m7_settlement.rules import evaluate as settle
from app.pipeline import calculate, execute, load_sample
from app.store import save_preserving


def test_monthly_sample_uses_pass_precursor(client):
    payload = client.post("/api/runs", json={"sample": "monthly.csv"}).get_json()
    assert payload["fallback"]["used"] is False
    discounts = payload["stages"][4]
    assert discounts["metrics"]["rule_mode"] == "pass_precursor"
    assert discounts["metrics"]["pass_verification"] == "ok"
    by_id = {r["event_id"]: r for r in discounts["rows"]}
    # 月票后的现金换乘取得优惠，前序指向月票事件。
    assert by_id["C001"]["transfer_anchor"] == "P001"
    assert by_id["C001"]["transfer_anchor_kind"] == "月票"
    assert by_id["C001"]["expected"] == "1.50"
    # 连续刷月票不续期：P003 未推进时钟，C002 在首个行程 80 分钟后出窗。
    assert by_id["C002"]["transfer_anchor"] == "—"
    assert by_id["C002"]["expected"] == "2.50"


def test_monthly_sample_card_days_unique(client):
    payload = client.post("/api/runs", json={"sample": "monthly.csv"}).get_json()
    totals = payload["card_day_totals"]
    keys = [(d["card"], d["business_day"]) for d in totals]
    assert len(keys) == len(set(keys))
    assert len(totals) == 3
    # 每卡日应收 = 该卡日所有行程应扣之和，不重复占额。
    for day in totals:
        rows = [r for r in payload["stages"][4]["rows"]
                if (r["card"], r["business_day"]) == (day["card"], day["business_day"])]
        assert day["expected"] == format(sum(float(r["expected"]) for r in rows), ".2f")


def test_page_shows_transfer_precursor(client):
    client.post("/api/runs", json={"sample": "monthly.csv"})
    html = client.get("/discounts").data.decode()
    assert "每卡日唯一应收" in html
    assert "前序事件" in html and "前序类型" in html
    assert "P001" in html and "月票" in html


def test_pass_verification_failure_keeps_previous(client):
    first = client.post("/api/runs", json={"sample": "monthly.csv"}).get_json()
    second = client.post("/api/runs", json={"sample": "monthly.csv",
                                            "parameters": {"pass_verification": "failed"}}).get_json()
    assert second["preserved"] is True
    assert second["fallback"]["used"] is True
    assert second["run_id"] == first["run_id"]
    assert [r["run_id"] for r in store.history()] == [first["run_id"]]
    # 失败尝试不会覆盖旧版结果，旧批次仍是新口径。
    assert store.get(first["run_id"])["stages"][4]["metrics"]["rule_mode"] == "pass_precursor"


def test_rerun_same_batch_does_not_double_charge(client):
    first = client.post("/api/runs", json={"sample": "monthly.csv"}).get_json()
    second = client.post("/api/runs", json={"sample": "monthly.csv"}).get_json()
    assert first["run_id"] == second["run_id"]
    assert len(store.history()) == 1
    assert first["stages"][4]["metrics"] == second["stages"][4]["metrics"]


def test_duplicate_events_in_payload_not_double_counted():
    import app.modules.m5_discounts.rules as discounts
    sample = [
        {"event_id": "P", "card": "0001", "business_day": "2026-11-16",
         "at": "2026-11-16T00:00:00+00:00", "route": "01", "base_fare": "2.50", "reported": "0"},
        {"event_id": "C", "card": "0001", "business_day": "2026-11-16",
         "at": "2026-11-16T00:15:00+00:00", "route": "02", "base_fare": "2.50", "reported": "1.50"},
    ]
    once = discounts.evaluate(sample, {})
    twice = discounts.evaluate(sample + sample, {})
    assert once["rows"] == twice["rows"]
    assert once["card_day_totals"] == twice["card_day_totals"]
    assert once["metrics"]["expected_total"] == twice["metrics"]["expected_total"] == "1.50"


def test_unreconciled_cannot_seal():
    rows = [{"event_id": "1", "expected": "1.00", "reported": "2.00", "status": "差异"}]
    with pytest.raises(DomainError) as error:
        settle(rows, {"clearing": {"metrics": {"balanced": True, "reconciled": False}}})
    assert error.value.code == "UNRECONCILED"


def test_balanced_reconcile_can_seal():
    rows = [{"event_id": "1", "expected": "1.00", "reported": "1.00", "status": "通过"}]
    result = settle(rows, {"clearing": {"metrics": {"balanced": True, "reconciled": True}}})
    assert result["metrics"]["ready"] is True
    assert len(result["card_day_totals"]) == 1


def test_export_contains_card_day_evidence(client):
    payload = client.post("/api/runs", json={"sample": "monthly.csv"}).get_json()
    response = client.get("/api/runs/" + payload["run_id"] + "/archive")
    rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
    header = rows[0]
    assert "input_sha256" in header and "kind" in header
    assert any(row[3] == "accepted" for row in rows[1:])
