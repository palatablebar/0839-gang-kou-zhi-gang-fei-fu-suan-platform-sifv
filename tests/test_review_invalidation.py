"""晚到票据改归属时，旧版本复核意见失效。"""
import json

from app.service import pipeline

TICKETS = [
    {"ticket_id": "T1", "container_id": "C1", "gate_in": "2026-11-02", "gate_out": "2026-11-10", "amount_cents": "52000"},
    {"ticket_id": "T2", "container_id": "C2", "gate_in": "2026-11-06", "gate_out": "2026-11-11", "amount_cents": "16000"},
    {"ticket_id": "T3", "container_id": "C3", "gate_in": "2026-09-29", "gate_out": "2026-10-12", "amount_cents": "24000"},
    {"ticket_id": "T4", "container_id": "C4", "gate_in": "2026-11-02", "gate_out": "2026-11-10", "amount_cents": "0"},
]


def create(client, tickets=None, profile="clean"):
    body = {"profile": profile}
    if tickets is not None:
        body["tickets"] = tickets
    response = client.post("/api/runs", json=body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def approve(client, run, comment="已核验"):
    return client.post(f"/api/runs/{run['run_id']}/review",
                       json={"decision": "APPROVED", "comment": comment,
                             "expected_revision": run["revision"]})


def revise(client, run, tickets, mode=None):
    body = {"tickets": tickets}
    if mode:
        body["mode"] = mode
    return client.post(f"/api/runs/{run['run_id']}/tickets", json=body)


def test_late_ticket_reassignment_invalidates_previous_review(client):
    run = create(client, tickets=TICKETS)
    assert approve(client, run).status_code == 200
    assert client.get(f"/api/runs/{run['run_id']}/evidence").status_code == 200
    late = [dict(TICKETS[3], amount_cents="1000")]
    response = revise(client, run, late)
    assert response.status_code == 200
    fresh = response.get_json()
    assert run["run_id"] in fresh["superseded_runs"]
    old = client.get(f"/api/runs/{run['run_id']}").get_json()
    assert old["invalidated"] is True
    assert old["invalidated_reason"] == "TICKET_ATTRIBUTION_REVISED"
    assert old["superseded_by"] == fresh["run_id"]


def test_late_ticket_can_move_from_one_trip_to_another(client):
    run = create(client, tickets=TICKETS[1:2])
    assert approve(client, run).status_code == 200
    assert run["tickets"]["attribution"]["T2"] == "C2@2026-11-06~2026-11-11"
    # 港方更正：C2 的票据其实属于 C1 的那一趟，并补上了进出港日期。
    corrected = [dict(TICKETS[1], container_id="C1", gate_in="2026-11-02", gate_out="2026-11-10")]
    fresh = revise(client, run, corrected).get_json()
    assert fresh["tickets"]["attribution"]["T2"] == "C1@2026-11-02~2026-11-10"
    assert client.get(f"/api/runs/{run['run_id']}").get_json()["invalidated"] is True


def test_late_ticket_replace_mode_recomputes_whole_source(client):
    run = create(client, tickets=TICKETS)
    assert approve(client, run).status_code == 200
    fresh = revise(client, run, TICKETS[:2], mode="replace").get_json()
    assert fresh["tickets"]["counts"]["tickets_accepted"] == 2
    assert fresh["run_id"] in client.get(f"/api/runs/{run['run_id']}").get_json()["superseded_by"] \
        or fresh["superseded_runs"] == [run["run_id"]]


def test_bad_late_ticket_mode_is_rejected(client):
    run = create(client, tickets=TICKETS)
    response = revise(client, run, TICKETS, mode="guess")
    assert response.status_code == 400


def test_invalidated_run_cannot_be_approved_again(client):
    run = create(client, tickets=TICKETS)
    assert approve(client, run).status_code == 200
    revise(client, run, [dict(TICKETS[3], amount_cents="7")])
    response = client.post(f"/api/runs/{run['run_id']}/review",
                           json={"decision": "APPROVED", "comment": "旧意见", "expected_revision": 1})
    assert response.status_code == 409
    assert response.get_json()["code"] == "STALE_REVIEW"
    assert "失效" in response.get_json()["error"]


def test_invalidated_run_cannot_issue_evidence(client):
    run = create(client, tickets=TICKETS)
    assert approve(client, run).status_code == 200
    revise(client, run, [dict(TICKETS[3], amount_cents="7")])
    response = client.get(f"/api/runs/{run['run_id']}/evidence")
    assert response.status_code == 409
    assert response.get_json()["code"] == "STALE_REVIEW"


def test_fresh_run_can_be_approved_after_revision(client):
    run = create(client, tickets=TICKETS)
    assert approve(client, run).status_code == 200
    fresh = revise(client, run, [dict(TICKETS[3], amount_cents="7")]).get_json()
    assert fresh["summary"]["REVIEW"] >= 1
    response = approve(client, fresh, "按晚到票据重新复核")
    assert response.status_code == 200
    assert response.get_json()["status"] == "APPROVED"
    assert client.get(f"/api/runs/{fresh['run_id']}/evidence").status_code == 200


def test_unrelated_ticket_addition_does_not_invalidate_review(client):
    run = create(client, tickets=TICKETS)
    assert approve(client, run).status_code == 200
    # 新增一张没有对应趟次的票：入账归属没变，旧复核仍然有效。
    fresh = revise(client, run, TICKETS + [
        {"ticket_id": "T9", "container_id": "C9", "amount_cents": "100"}]).get_json()
    old = client.get(f"/api/runs/{run['run_id']}").get_json()
    assert old["invalidated"] is False
    assert old["superseded_by"] == ""
    # 归属未变，批次保持幂等，不生成新版本。
    assert fresh["run_id"] == run["run_id"]
    assert client.get(f"/api/runs/{run['run_id']}/evidence").status_code == 200


def test_same_attribution_is_idempotent(client):
    run = create(client, tickets=TICKETS)
    again = create(client, tickets=TICKETS)
    assert again["run_id"] == run["run_id"]


def test_evidence_records_ticket_source(client):
    run = create(client, tickets=TICKETS)
    assert approve(client, run).status_code == 200
    artifact = client.get(f"/api/runs/{run['run_id']}/evidence").get_json()
    assert artifact["ticket_source"]["enabled"] is True
    assert artifact["ticket_source"]["fingerprint"]
    assert artifact["ledger"]["tickets"]["counts"]["posted"] == 4
    assert "tickets" in artifact["ledger"]


def test_run_payload_reflects_ticket_reconciliation(client):
    run = create(client, tickets=[dict(item, amount_cents="1") for item in TICKETS[:1]] + TICKETS[1:])
    assert run["tickets"]["counts"]["mismatched"] == 1
    assert run["summary"]["REVIEW"] >= 1


def test_missing_ticket_source_keeps_old_behaviour(client):
    run = create(client, profile="clean")
    assert run["tickets"]["enabled"] is True  # clean 样例自带票据文件
    bare = pipeline("clean", tickets=[])
    assert bare["tickets"]["enabled"] is False
    assert bare["summary"]["REJECT"] == 0
