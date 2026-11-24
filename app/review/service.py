import json

from app.errors import DomainIssue
from app.review.policy import approval_allowed


def _base_key(payload):
    source = payload.get("source") or {}
    return "{}:{}:{}:{}".format(payload.get("project", ""), payload.get("profile", ""),
                                source.get("filename", ""), source.get("sha256", ""))


def _ticket_fingerprint(payload):
    tickets = payload.get("tickets") or {}
    return tickets.get("fingerprint", "") if tickets.get("enabled") else ""


def create_run(store, payload, tickets=None):
    base_key = _base_key(payload)
    fingerprint = _ticket_fingerprint(payload)
    row = store.create(payload["run_key"], payload["profile"], payload, base_key=base_key,
                       ticket_fingerprint=fingerprint)
    result = serialize(row)
    result["superseded_runs"] = row.get("superseded_runs", [])
    return result


def approve(store, run_id, decision, comment, expected_revision):
    row = store.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if row.get("invalidated"):
        raise DomainIssue("STALE_REVIEW", 409, detail="票据归属已更新，旧复核意见失效，请基于最新批次重新复核")
    payload = json.loads(row["payload"])
    if decision == "APPROVED" and not approval_allowed(payload):
        raise DomainIssue("GATE_BLOCKED", 409)
    if decision not in {"APPROVED", "HELD"}:
        raise DomainIssue("BAD_REQUEST")
    row, problem = store.review(run_id, decision, comment.strip(), expected_revision)
    if problem == "missing":
        raise DomainIssue("RUN_NOT_FOUND", 404)
    if problem == "invalidated":
        raise DomainIssue("STALE_REVIEW", 409, detail="票据归属已更新，旧复核意见失效，请基于最新批次重新复核")
    if problem == "stale":
        raise DomainIssue("STALE_REVIEW", 409)
    return serialize(row)


def serialize(row):
    if row is None:
        return None
    payload = json.loads(row["payload"])
    payload.update(run_id=row["id"], status=row["status"], revision=row["revision"],
                   comment=row["comment"], created_at=row["created_at"],
                   invalidated=bool(row.get("invalidated")),
                   invalidated_reason=row.get("invalidated_reason", ""),
                   superseded_by=row.get("superseded_by", ""),
                   base_key=row.get("base_key", ""),
                   ticket_fingerprint=row.get("ticket_fingerprint", ""))
    return payload


def _as_rows(payload):
    """把批次里已采信的票据还原成可再次整理的原始行。"""
    rows = []
    for index, item in enumerate(((payload.get("tickets") or {}).get("tickets") or []), start=1):
        rows.append({"ticket_id": item.get("ticket_id"), "container_id": item.get("container_id"),
                     "gate_in": item.get("gate_in") or "", "gate_out": item.get("gate_out") or "",
                     "amount_cents": item.get("amount_cents"), "_line": item.get("line") or index})
    return rows


def merge_tickets(existing, late):
    """晚到票据按票号覆盖旧记录，未提及的旧票保留；无票号的行原样追加待隔离。"""
    merged = {}
    order = []
    orphans = []
    for row in list(existing) + list(late):
        key = str(row.get("ticket_id") or "")
        if not key:
            orphans.append(row)
            continue
        if key not in merged:
            order.append(key)
        merged[key] = row
    return [merged[key] for key in order] + orphans


def reattach_tickets(store, run_id, tickets, mode="merge"):
    """把晚到票据并入既有批次：生成新批次并使旧批次的复核失效。

    `merge` 表示这是港方补发的记录，按票号覆盖同号旧记录；
    `replace` 表示这是完整重发的票据清单。
    """
    from app.service import pipeline
    row = store.get(run_id)
    if row is None:
        raise DomainIssue("RUN_NOT_FOUND", 404)
    payload = json.loads(row["payload"])
    if mode == "merge":
        tickets = merge_tickets(_as_rows(payload), tickets)
    elif mode != "replace":
        raise DomainIssue("BAD_REQUEST", detail="mode 只能是 merge 或 replace")
    fresh = pipeline(payload["profile"], tickets=tickets, upto="证据交付")
    return create_run(store, fresh)
