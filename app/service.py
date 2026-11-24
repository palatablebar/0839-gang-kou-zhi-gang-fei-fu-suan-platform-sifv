import hashlib
from decimal import Decimal
from pathlib import Path

from app.catalog import SPEC
from app.domain.calculate import calculate_rows
from app.domain.decision import decide_rows
from app.domain.prepare import prepare_rows
from app.domain.trip import trip_id_of
from app.intake.service import load
from app.reconcile.ledger import build_ledger
from app.tickets.service import (empty_tickets, load_tickets, reconcile_tickets,
                                 tickets_from_serialized, tickets_from_rows)


def jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items() if not str(key).startswith("_")}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def _stage(name):
    return SPEC["stages"].index(name) + 1


def _ticket_source(profile, ticket_path, tickets):
    if tickets is not None:
        if isinstance(tickets, dict):
            return tickets_from_serialized(tickets.get("tickets") or []) if tickets.get("enabled") else empty_tickets()
        if not tickets:
            # 显式提交空票据清单表示本批次不做票据对账。
            return empty_tickets()
        return tickets_from_rows(tickets)
    return load_tickets(profile, ticket_path)


def pipeline(profile="dirty", path=None, upto="证据交付", tickets=None, ticket_path=None):
    intake = load(profile, path)
    prepared, prepare_issues, results = [], [], []
    ledger = reconciliation = None
    if _stage(upto) >= 2:
        prepared, prepare_issues = prepare_rows(intake["rows"])
    if _stage(upto) >= 3:
        results = calculate_rows(prepared)
    if _stage(upto) >= 4:
        results = decide_rows(results)
    if _stage(upto) >= 5:
        ticket_data = _ticket_source(profile, ticket_path, tickets)
        reconciliation = reconcile_tickets(results, ticket_data)
        _apply_tickets(results, reconciliation)
        ledger = build_ledger(intake, prepared, prepare_issues, results, reconciliation)
    intake["counts"]["prepared"] = len(prepared)
    intake["counts"]["computed"] = len(results)
    issues = intake["issues"] + prepare_issues
    if reconciliation is not None:
        issues = issues + reconciliation.get("issues", [])
    summary = {"PASS": 0, "REVIEW": 0, "REJECT": 0}
    for item in results:
        summary[item.get("verdict", "REVIEW")] = summary.get(item.get("verdict", "REVIEW"), 0) + 1
    chart_field = {"0840": "rolling", "0849": "mean", "0850": "settlement_cents"}.get(SPEC["code"], "metric")
    chart = [{"label": item.get("label", item.get(SPEC["key"], "")),
              "value": float(item.get(chart_field) or 0),
              "limit": float(item.get("limit") or 0)}
             for item in results[:12]]
    rows = []
    raw_stage = _stage(upto) <= 2
    if raw_stage:
        stage_rows = intake["rows"] if _stage(upto) == 1 else prepared
        rows = [{key: item.get(key) for key in SPEC["columns"]} for item in stage_rows[:18]]
    else:
        for item in results[:18]:
            rows.append({key: value for key, value in item.items()
                         if key in {"label", "verdict", "reason", "metric", "limit", "status", "state",
                                    "breakdown", "trip_id", "ticket_id", "ticket_cents",
                                    "ticket_diff_cents", "ticket_status", "ticket_reason"}
                         or key in SPEC["columns"] or key in SPEC["result_columns"]})
    source = {"profile": profile, "filename": Path(intake["path"]).name, "sha256": intake["sha256"]}
    payload = {
        "project": SPEC["code"] + " " + SPEC["name"],
        "rule": SPEC["rule_short"],
        "profile": profile,
        "source": source,
        "counts": intake["counts"],
        "issues": issues[:60],
        "rows": rows,
        "row_kind": "source" if raw_stage else "result",
        "chart": chart,
        "summary": summary,
        "ledger": ledger,
        "tickets": reconciliation,
        "stages": [{"id": "intake", "name": SPEC["stages"][0]},
                   {"id": "prepare", "name": SPEC["stages"][1]},
                   {"id": "calculate", "name": SPEC["stages"][2]},
                   {"id": "decision", "name": SPEC["stages"][3]},
                   {"id": "reconcile", "name": SPEC["stages"][4]}],
        "run_key": "",
    }
    fingerprint = (reconciliation or {}).get("fingerprint", "")
    # 批次键 = 来源 + 规则 + 票据归属指纹：同源同归属幂等，票据改归属即生成新批次。
    payload["run_key"] = hashlib.sha256(
        (SPEC["code"] + profile + intake["sha256"] + SPEC["rule"] + fingerprint).encode("utf-8")).hexdigest()[:24]
    if reconciliation is not None:
        payload["ticket_source"] = {
            "enabled": reconciliation["enabled"],
            "sha256": (reconciliation.get("source") or {}).get("sha256", ""),
            "filename": (reconciliation.get("source") or {}).get("filename", ""),
            "fingerprint": fingerprint,
        }
    return jsonable(payload)


def _apply_tickets(results, reconciliation):
    from app.tickets.effects import apply_verdict
    by_id = {row["trip_id"]: row for row in reconciliation.get("trips", [])}
    for item in results:
        row = by_id.get(trip_id_of(item))
        if row is not None:
            apply_verdict(item, row, bool(reconciliation.get("enabled")))
    return results
