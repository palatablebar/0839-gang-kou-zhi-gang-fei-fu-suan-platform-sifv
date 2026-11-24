from decimal import Decimal

import pytest

from app.domain.calculate import calculate_rows
from app.domain.decision import decide_rows
from app.domain.prepare import prepare_rows
from app.intake.service import load
from app.service import pipeline
from app.tickets.service import reconcile_tickets, tickets_from_rows

C1_A = {"container_id": "C1", "gate_in": "2026-11-02", "gate_out": "2026-11-10",
        "free_days": "3", "invoice_cents": "52000"}
C1_B = {"container_id": "C1", "gate_in": "2026-11-15", "gate_out": "2026-11-20",
        "free_days": "3", "invoice_cents": "16000"}
C2 = {"container_id": "C2", "gate_in": "2026-11-06", "gate_out": "2026-11-11",
      "free_days": "1", "invoice_cents": "16000"}


def trips(*rows):
    items, issues = prepare_rows([dict(row, **{"_line": index + 2}) for index, row in enumerate(rows)])
    assert issues == []
    return decide_rows(calculate_rows(items))


def ticket(ticket_id, container_id, amount, gate_in=None, gate_out=None):
    return {"ticket_id": ticket_id, "container_id": container_id,
            "gate_in": gate_in, "gate_out": gate_out, "amount_cents": str(amount)}


def by_trip(reconciliation):
    return {row["trip_id"]: row for row in reconciliation["trips"]}


def by_ticket(reconciliation):
    return {row["ticket_id"]: row for row in reconciliation["tickets"]}


# --- 同箱多趟 -------------------------------------------------------------

def test_same_container_multiple_trips_are_not_a_conflict(tmp_path):
    path = tmp_path / "multi.csv"
    path.write_text(
        "container_id,gate_in,gate_out,free_days,invoice_cents\n"
        "C1,2026-11-02,2026-11-10,3,52000\n"
        "C1,2026-11-15,2026-11-20,3,16000\n",
        encoding="utf-8")
    data = load("clean", str(path))
    assert data["counts"]["accepted"] == 2
    assert data["counts"]["isolated"] == 0
    assert not any(issue["code"] == "CONFLICT_DUPLICATE" for issue in data["issues"])


def test_same_container_and_same_dates_but_different_content_is_conflict(tmp_path):
    path = tmp_path / "conflict.csv"
    path.write_text(
        "container_id,gate_in,gate_out,free_days,invoice_cents\n"
        "C1,2026-11-02,2026-11-10,3,52000\n"
        "C1,2026-11-02,2026-11-10,3,51000\n",
        encoding="utf-8")
    data = load("clean", str(path))
    assert data["counts"]["accepted"] == 0
    assert any(issue["code"] == "CONFLICT_DUPLICATE" for issue in data["issues"])


def test_each_trip_gets_its_own_fee():
    rows = trips(C1_A, C1_B)
    assert len(rows) == 2
    assert len({row["trip_id"] for row in rows}) == 2
    assert len({row["label"] for row in rows}) == 2
    assert rows[0]["fee_cents"] != rows[1]["fee_cents"]


# --- 只写箱号的旧票 -------------------------------------------------------

def test_container_only_ticket_posts_when_single_trip():
    rows = trips(C1_A)
    result = reconcile_tickets(rows, tickets_from_rows([ticket("T1", "C1", 52000)]))
    assert result["counts"]["posted"] == 1
    assert by_ticket(result)["T1"]["status"] == "POSTED"
    assert by_trip(result)[rows[0]["trip_id"]]["status"] == "MATCHED"


def test_container_only_ticket_is_ambiguous_for_multi_trip_container_even_if_amount_matches():
    rows = trips(C1_A, C1_B)
    result = reconcile_tickets(rows, tickets_from_rows([ticket("T1", "C1", 52000)]))
    assert result["counts"]["posted"] == 0
    row = by_ticket(result)["T1"]
    assert row["status"] == "AMBIGUOUS"
    assert row["reason"] == "TICKET_AMBIGUOUS_CONTAINER"
    assert len(row["candidates"]) == 2
    assert all(item["ticket_id"] is None for item in result["trips"])


def test_ambiguous_ticket_is_flagged_without_blocking_signing(tmp_path):
    path = tmp_path / "multi.csv"
    path.write_text(
        "container_id,gate_in,gate_out,free_days,invoice_cents\n"
        "C1,2026-11-02,2026-11-10,3,52000\n"
        "C1,2026-11-15,2026-11-20,3,16000\n"
        "C2,2026-11-06,2026-11-11,1,16000\n",
        encoding="utf-8")
    payload = pipeline("clean", path=str(path), tickets=[
        ticket("T1", "C1", 52000),
        ticket("T2", "C2", 16000, "2026-11-06", "2026-11-11"),
    ])
    assert payload["tickets"]["counts"]["ambiguous"] == 1
    assert payload["tickets"]["hard_conflicts"] == 0
    assert payload["summary"]["REJECT"] == 0


# --- 日期票优先 -----------------------------------------------------------

def test_dated_ticket_claims_matching_trip_over_container_only_ticket():
    rows = trips(C1_A)
    result = reconcile_tickets(rows, tickets_from_rows([
        ticket("OLD", "C1", 52000),
        ticket("NEW", "C1", 52000, "2026-11-02", "2026-11-10"),
    ]))
    ticket_rows = by_ticket(result)
    assert ticket_rows["NEW"]["status"] == "POSTED"
    assert ticket_rows["OLD"]["status"] == "SUPERSEDED"
    assert ticket_rows["OLD"]["reason"] == "TICKET_DATED_PRIORITY"
    assert result["counts"]["superseded"] == 1


def test_container_only_ticket_stays_ambiguous_when_a_dated_ticket_only_claims_one_of_several_trips():
    rows = trips(C1_A, C1_B)
    result = reconcile_tickets(rows, tickets_from_rows([
        ticket("OLD", "C1", 52000),
        ticket("NEW", "C1", 52000, "2026-11-02", "2026-11-10"),
    ]))
    ticket_rows = by_ticket(result)
    assert ticket_rows["NEW"]["status"] == "POSTED"
    assert ticket_rows["OLD"]["status"] == "AMBIGUOUS"


def test_container_only_ticket_is_superseded_when_all_trips_of_the_box_are_dated():
    rows = trips(C1_A, C1_B)
    result = reconcile_tickets(rows, tickets_from_rows([
        ticket("OLD", "C1", 52000),
        ticket("NEW", "C1", 52000, "2026-11-02", "2026-11-10"),
        ticket("NEW2", "C1", 16000, "2026-11-15", "2026-11-20"),
    ]))
    assert by_ticket(result)["OLD"]["status"] == "SUPERSEDED"
    assert result["counts"]["posted"] == 2


def test_dated_ticket_with_wrong_dates_is_orphan():
    rows = trips(C1_A)
    result = reconcile_tickets(rows, tickets_from_rows([
        ticket("T1", "C1", 52000, "2026-11-03", "2026-11-10")]))
    assert by_ticket(result)["T1"]["status"] == "ORPHAN"
    assert by_ticket(result)["T1"]["reason"] == "TICKET_NO_TRIP"


def test_dated_ticket_cannot_claim_a_different_containers_trip():
    rows = trips(C1_A)
    result = reconcile_tickets(rows, tickets_from_rows([
        ticket("T1", "C2", 52000, "2026-11-02", "2026-11-10")]))
    assert by_ticket(result)["T1"]["status"] == "ORPHAN"


# --- 一张票只入账一次 / 一趟只入账一张票 -----------------------------------

def test_two_dated_tickets_for_same_trip_conflict_and_block():
    rows = trips(C1_A)
    result = reconcile_tickets(rows, tickets_from_rows([
        ticket("T1", "C1", 52000, "2026-11-02", "2026-11-10"),
        ticket("T2", "C1", 52000, "2026-11-02", "2026-11-10"),
    ]))
    assert by_trip(result)[rows[0]["trip_id"]]["status"] == "CONFLICT"
    assert result["hard_conflicts"] == 1 + 2  # 一趟归属冲突，两张票各自硬冲突
    assert result["counts"]["posted"] == 0


def test_same_ticket_id_is_normalized_to_one_version():
    rows = trips(C1_A)
    result = reconcile_tickets(rows, tickets_from_rows([
        ticket("T1", "C1", 52000, "2026-11-02", "2026-11-10"),
        ticket("T1", "C1", 52000, "2026-11-02", "2026-11-10"),
    ]))
    assert result["counts"]["posted"] == 1
    assert result["counts"]["ticket_conflicts"] == 0


def test_duplicate_dated_ticket_cannot_claim_second_trip():
    rows = trips(C1_A, C2)
    result = reconcile_tickets(rows, tickets_from_rows([
        ticket("T1", "C1", 52000, "2026-11-02", "2026-11-10"),
        {"ticket_id": "T1", "container_id": "C2", "gate_in": "2026-11-06",
         "gate_out": "2026-11-11", "amount_cents": "16000"},
    ]))
    # 同一票号出现在两个趟次：后一条是修订版本，只能归到一个趟次。
    posted = [row for row in result["tickets"] if row["status"] == "POSTED"]
    assert len(posted) == 1
    assert result["counts"]["posted"] <= 1


def test_ticket_revision_keeps_last_record_and_reports_issue():
    rows = trips(C1_A)
    data = tickets_from_rows([
        ticket("T1", "C1", 52000, "2026-11-02", "2026-11-10"),
        ticket("T1", "C1", 51000, "2026-11-02", "2026-11-10"),
    ])
    assert data["counts"]["revised"] == 1
    assert any(issue["code"] == "TICKET_REVISED" for issue in data["issues"])
    result = reconcile_tickets(rows, data)
    assert by_trip(result)[rows[0]["trip_id"]]["ticket_cents"] == 51000
    assert by_trip(result)[rows[0]["trip_id"]]["status"] == "AMOUNT_MISMATCH"


# --- 判定与守恒 -----------------------------------------------------------

def test_amount_mismatch_raises_to_review_and_keeps_ticket():
    item = C1_A
    payload = pipeline("clean", tickets=[
        ticket("T1", "C1", 53000),
        ticket("T2", "C2", 16000, "2026-11-06", "2026-11-11"),
        ticket("T3", "C3", 24000),
        ticket("T4", "C4", 0),
    ])
    row = next(row for row in payload["rows"] if row["container_id"] == "C1")
    assert row["ticket_status"] == "AMOUNT_MISMATCH"
    assert row["verdict"] == "REVIEW"
    assert row["ticket_diff_cents"] == 1000


def test_missing_ticket_is_reported_as_review():
    payload = pipeline("clean", tickets=[ticket("T1", "C1", 52000)])
    assert payload["tickets"]["counts"]["missing"] == 3
    assert payload["summary"]["REVIEW"] == 3


def test_ticket_source_rows_are_conserved():
    payload = pipeline("dirty")
    counts = payload["tickets"]["counts"]
    ticket_ledger = payload["ledger"]["tickets"]["counts"]
    assert ticket_ledger["tickets_input"] == 6
    assert ticket_ledger["tickets_accepted"] == counts["tickets_accepted"]
    checks = {item["id"]: item["ok"] for item in payload["ledger"]["checks"]}
    assert checks["ticket_source"] is True


def test_pipeline_is_deterministic_with_tickets():
    first = pipeline("clean")
    second = pipeline("clean")
    assert first["ticket_source"] == second["ticket_source"]
    assert first["summary"] == second["summary"]
    assert first["tickets"]["counts"] == second["tickets"]["counts"]
