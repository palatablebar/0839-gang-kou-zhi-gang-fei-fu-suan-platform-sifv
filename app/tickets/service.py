"""港方票据的采信与配对。

票据由港方单独提供，与箱单来源相互独立。票据身份是 `ticket_id`；
归属由 `container_id` 加可选的进出港日期确定。

配对顺序固定，避免用金额反推归属：

1. 写了进出港日期的票据只能认领日期完全一致的趟次，优先于只写箱号的旧票。
2. 只写箱号的旧票，仅当该箱在本批次只有一趟时才能唯一配对并入账；
   同箱多趟时保持未认领状态，即使金额恰好等于某一趟的复算费用。
3. 一张票最多入账一次；一趟最多由一张票入账。

票据文件可以晚到并带来同一 `ticket_id` 的修订版本。修订以文件中后出现的
记录为准，并保留 `TICKET_REVISED` 问题记录；归属因此变化时，旧复核失效。
"""
import csv
import hashlib
import io
import json
from pathlib import Path

from app.catalog import SPEC
from app.config import MAX_FILE, SAMPLES
from app.domain.helpers import DAY, I
from app.domain.trip import date_key, trip_key, trip_id_of
from app.errors import DomainIssue
from app.intake.normalize import canonical
from app.intake.reader import decode


POSTED = "POSTED"
ORPHAN = "ORPHAN"
AMBIGUOUS = "AMBIGUOUS"
SUPERSEDED = "SUPERSEDED"
CONFLICT = "CONFLICT"

MATCHED = "MATCHED"
AMOUNT_MISMATCH = "AMOUNT_MISMATCH"
NO_TICKET = "NO_TICKET"
TRIP_CONFLICT = "CONFLICT"
NOT_CHECKED = "NOT_CHECKED"

DATE_FIELDS = ("gate_in", "gate_out")

# 港方票据常见列名；文件表头与 API 行都会先映射到规范列。
ALIASES = {
    "ticket_id": ("ticket_id", "ticket_no", "ticket", "invoice_id", "invoice_no", "bill_id", "bill_no", "票据号", "票号"),
    "container_id": ("container_id", "container", "container_no", "ctn_no", "ctn", "箱号"),
    "gate_in": ("gate_in", "gate_in_date", "in_date", "entry_date", "进港日期", "进港日"),
    "gate_out": ("gate_out", "gate_out_date", "out_date", "exit_date", "出港日期", "出港日"),
    "amount_cents": ("amount_cents", "ticket_cents", "invoice_cents", "fee_cents", "cents", "金额分", "票据分"),
    "amount": ("amount", "ticket_amount", "invoice_amount", "金额"),
}
ALIAS_LOOKUP = {alias: canonical_name for canonical_name, aliases in ALIASES.items() for alias in aliases}


def _canonical_row(raw):
    """把任意键名的票据行映射到规范列；无法识别的金额保留为空。"""
    mapped = {}
    for key, value in (raw or {}).items():
        name = ALIAS_LOOKUP.get(canonical(key))
        if name:
            mapped[name] = value
    return mapped


def _row_cells(raw):
    mapped = _canonical_row(raw)
    cells = []
    for column in SPEC["ticket_columns"]:
        value = mapped.get(column)
        if column == "amount_cents" and value in (None, "") and mapped.get("amount") not in (None, ""):
            value = _yuan_to_cents(mapped.get("amount"))
        cells.append(canonical(value))
    return cells


def _yuan_to_cents(value):
    try:
        from decimal import Decimal, ROUND_HALF_UP
        return int((Decimal(canonical(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:
        return value


def validate_ticket_header(cells):
    """票据表头允许港方列名，但必须能完整映射到规范列。"""
    mapped = [ALIAS_LOOKUP.get(canonical(cell)) for cell in cells]
    if sorted(item for item in mapped if item) != sorted(SPEC["ticket_columns"]) or None in mapped:
        raise DomainIssue("HEADER_MISMATCH", detail="票据表头无法映射到票号、箱号、进出港日期与金额")
    return mapped


def _reorder(cells, header):
    """按表头把票据行重排为规范列顺序。"""
    values = dict(zip(header, cells))
    return [canonical(values.get(column, "")) for column in SPEC["ticket_columns"]]



def _safe_name(profile) -> str:
    name = Path(str(profile or "")).name
    if not name or name != str(profile) or name.startswith("."):
        raise DomainIssue("SAMPLE_UNKNOWN")
    return name


def ticket_path(profile) -> Path:
    return SAMPLES / f"tickets_{_safe_name(profile)}.csv"


def _issue(code, line=0, ticket_id="", detail=""):
    item = {"line": line, "key": ticket_id, "code": code, "source": "ticket"}
    if detail:
        item["detail"] = detail
    return item


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()


def _signature(ticket):
    return (ticket["ticket_id"], ticket["container_id"], date_key(ticket.get("gate_in")),
            date_key(ticket.get("gate_out")), int(ticket["amount_cents"]))


def _parse_ticket(cells, line):
    raw = dict(zip(SPEC["ticket_columns"], cells))
    ticket_id = canonical(raw.get("ticket_id"))
    container_id = canonical(raw.get("container_id"))
    if not ticket_id:
        raise ValueError("TICKET_KEY_MISSING")
    if not container_id:
        raise ValueError("TICKET_CONTAINER_MISSING")
    amount = I(raw.get("amount_cents"), "INVALID_AMOUNT_CENTS")
    if amount < 0:
        raise ValueError("INVALID_AMOUNT_CENTS")
    dates = {}
    for field in DATE_FIELDS:
        text = canonical(raw.get(field))
        dates[field] = DAY(text) if text else None
    if (dates["gate_in"] is None) != (dates["gate_out"] is None):
        raise ValueError("TICKET_PARTIAL_DATES")
    if dates["gate_in"] and dates["gate_out"] and dates["gate_out"] < dates["gate_in"]:
        raise ValueError("TICKET_GATE_ORDER")
    return {"ticket_id": ticket_id, "container_id": container_id,
            "gate_in": dates["gate_in"], "gate_out": dates["gate_out"],
            "amount_cents": amount, "_line": line}


def normalize_tickets(records):
    """把票据行（已按文件顺序）整理成唯一票据。"""
    issues = []
    counts = {"input": len(records), "accepted": 0, "isolated": 0, "duplicates": 0, "revised": 0}
    valid = []
    for line, cells in records:
        try:
            valid.append(_parse_ticket(cells, line))
        except ValueError as exc:
            counts["isolated"] += 1
            issues.append(_issue(str(exc), line, canonical(cells[0]) if cells else ""))
    grouped, order = {}, []
    for ticket in valid:
        key = ticket["ticket_id"]
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(ticket)
    accepted = []
    for key in order:
        versions = grouped[key]
        ticket = versions[-1]
        signatures = {_signature(item) for item in versions}
        if len(versions) > 1:
            if len(signatures) == 1:
                counts["duplicates"] += len(versions) - 1
                for item in versions[:-1]:
                    issues.append(_issue("TICKET_EXACT_DUPLICATE", item["_line"], key))
            else:
                counts["revised"] += len(versions) - 1
                issues.append(_issue("TICKET_REVISED", ticket["_line"], key,
                                     detail="同一票据晚到记录更新了归属或金额，以最后一条为准"))
        accepted.append(ticket)
    counts["accepted"] = len(accepted)
    accepted.sort(key=lambda item: item["_line"])
    return accepted, issues, counts


def _source(records, tickets, source):
    rows = [[item["ticket_id"], item["container_id"], date_key(item["gate_in"]),
             date_key(item["gate_out"]), int(item["amount_cents"])] for item in tickets]
    return {"enabled": True, "source": source, "tickets": tickets, "issues": [],
            "counts": {"input": len(records), "accepted": 0, "isolated": 0, "duplicates": 0, "revised": 0},
            "sha256": _digest(records), "rows": records, "fingerprint": _digest(rows)}


def empty_tickets():
    return {"enabled": False, "source": {}, "tickets": [], "issues": [],
            "counts": {"input": 0, "accepted": 0, "isolated": 0, "duplicates": 0, "revised": 0},
            "sha256": "", "rows": [], "fingerprint": ""}


def tickets_from_rows(rows):
    """直接从字典行构造票据集合，供 API 内联提交、晚到票据与测试使用。"""
    records = []
    for index, row in enumerate(rows or [], start=1):
        if not isinstance(row, dict):
            raise DomainIssue("BAD_REQUEST", detail="票据行必须是对象")
        cells = _row_cells(row)
        records.append((int(row.get("_line") or index), cells))
    tickets, issues, counts = normalize_tickets(records)
    payload = _source(records, tickets, {"inline": True})
    payload["issues"] = issues
    payload["counts"] = counts
    return payload


def tickets_from_serialized(tickets):
    """把已保存的票据（JSON 化后的 ISO 文本）还原成可再次整理的票据集合。"""
    rows = [{"ticket_id": item.get("ticket_id"), "container_id": item.get("container_id"),
             "gate_in": item.get("gate_in"), "gate_out": item.get("gate_out"),
             "amount_cents": item.get("amount_cents"), "_line": item.get("line") or index}
            for index, item in enumerate(tickets or [], start=1)]
    if not rows:
        return empty_tickets()
    return tickets_from_rows(rows)


def load_tickets(profile=None, path=None):
    """读取票据来源。

    显式给出路径时文件必须存在；按样例名查找时找不到票据文件视为未启用票据对账。
    """
    if path is not None:
        sample = Path(path)
        if not sample.is_file():
            raise DomainIssue("SAMPLE_UNKNOWN")
    else:
        if not profile:
            return empty_tickets()
        sample = ticket_path(profile)
        if not sample.is_file():
            return empty_tickets()
    raw = sample.read_bytes()
    if len(raw) > MAX_FILE:
        raise DomainIssue("FILE_TOO_LARGE", 413)
    text = decode(raw)
    if not text.strip():
        raise DomainIssue("BLANK_FILE")
    try:
        parsed = list(csv.reader(io.StringIO(text, newline=""), strict=True))
    except csv.Error:
        raise DomainIssue("UNCLOSED_QUOTE")
    if not parsed:
        raise DomainIssue("EMPTY_FILE")
    header = validate_ticket_header(parsed[0])
    width = len(SPEC["ticket_columns"])
    records, issues = [], []
    counts = {"input": 0, "accepted": 0, "isolated": 0, "duplicates": 0, "revised": 0}
    usable = []
    for raw_row in parsed[1:]:
        cells = [canonical(cell) for cell in raw_row]
        if not any(cells) or cells == header:
            continue
        line = len(records) + 2
        records.append((line, cells))
        if len(cells) != width:
            issues.append(_issue("TICKET_COLUMN_COUNT", line, cells[0] if cells else ""))
        else:
            usable.append((line, _reorder(cells, header)))
    tickets, more_issues, more_counts = normalize_tickets(usable)
    counts.update({field: more_counts[field] for field in ("accepted", "isolated", "duplicates", "revised")})
    counts["input"] = len(records)
    # 无法解析或列数不符的票据行都记入隔离，保证来源行 = 采信 + 隔离 + 重复 + 修订。
    counts["isolated"] = counts["input"] - counts["accepted"] - counts["duplicates"] - counts["revised"]
    issues.extend(more_issues)
    source = {"profile": profile, "filename": sample.name,
              "sha256": hashlib.sha256(raw).hexdigest()}
    payload = _source(records, tickets, source)
    payload["issues"] = issues
    payload["counts"] = counts
    payload["sha256"] = source["sha256"]
    return payload


def _index_trips(trips):
    by_key, by_container = {}, {}
    for trip in trips:
        key = trip_key(trip.get("container_id"), trip.get("gate_in"), trip.get("gate_out"))
        by_key[key] = trip
        by_container.setdefault(canonical(trip.get("container_id")), []).append(trip)
    return by_key, by_container


def _trip_row(trip, enabled):
    return {
        "trip_id": trip_id_of(trip),
        "label": trip.get("label") or canonical(trip.get("container_id")),
        "container_id": canonical(trip.get("container_id")),
        "gate_in": date_key(trip.get("gate_in")),
        "gate_out": date_key(trip.get("gate_out")),
        "free_days": trip.get("free_days"),
        "invoice_cents": int(trip.get("invoice_cents") or 0),
        "fee_cents": int(trip.get("fee_cents") or 0),
        "free_end": trip.get("free_end") or "",
        "charge_days": trip.get("charge_days"),
        "base_verdict": trip.get("base_verdict") or trip.get("verdict") or "PASS",
        "base_reason": trip.get("base_reason") or trip.get("reason") or "OK",
        "ticket_id": None,
        "ticket_cents": None,
        "diff_cents": None,
        "status": NO_TICKET if enabled else NOT_CHECKED,
        "reason": "TICKET_MISSING" if enabled else "TICKETS_DISABLED",
        "_key": trip_key(trip.get("container_id"), trip.get("gate_in"), trip.get("gate_out")),
    }


def _ticket_row(ticket, status, reason, trip=None):
    return {"ticket_id": ticket["ticket_id"], "container_id": ticket["container_id"],
            "gate_in": date_key(ticket.get("gate_in")), "gate_out": date_key(ticket.get("gate_out")),
            "amount_cents": int(ticket["amount_cents"]), "line": ticket.get("_line", 0),
            "status": status, "reason": reason,
            "trip_id": trip_id_of(trip) if trip else None,
            "candidates": [trip_id_of(item) for item in ticket.get("_candidates", [])]}


def _attribution_fingerprint(enabled, postings, conflict_trips, conflict_tickets):
    """只用会影响入账归属与签发的字段计算指纹。

    新增一张未入账的票据、票据行顺序变化、或只是金额说明不同，都不改变归属，
    不应让旧复核失效；票据改挂到别的趟次、入账金额变化、或出现同一趟多票 /
    同一票重复入账的硬冲突，则必须让旧复核失效。
    """
    return _digest({
        "enabled": bool(enabled),
        "postings": sorted([trip_id, ticket_id, int(amount)] for trip_id, ticket_id, amount in postings),
        "conflict_trips": sorted(conflict_trips),
        "conflict_tickets": sorted(conflict_tickets),
    })


def reconcile_tickets(trips, ticket_data=None):
    """把每趟复算费用与港方票据逐趟对账。

    返回逐趟结果、逐票结果与归属指纹；指纹只随票据归属或入账金额变化，
    用于判定晚到票据是否让旧复核失效。
    """
    if isinstance(ticket_data, list):
        ticket_data = tickets_from_rows(ticket_data)
    if ticket_data is None:
        ticket_data = empty_tickets()
    enabled = bool(ticket_data.get("enabled"))
    tickets = list(ticket_data.get("tickets") or [])
    by_key, by_container = _index_trips(trips)

    claims, trip_rows, order = {}, {}, []
    for trip in trips:
        row = _trip_row(trip, enabled)
        key = row["_key"]
        order.append(key)
        trip_rows[key] = row
        claims.setdefault(key, [])

    ticket_rows = []

    # 第一轮：写了进出港日期的票据优先认领对应趟次。
    dated = set()
    for ticket in tickets:
        if ticket.get("gate_in") and ticket.get("gate_out"):
            key = trip_key(ticket["container_id"], ticket["gate_in"], ticket["gate_out"])
            if key in by_key:
                claims[key].append(ticket)
                dated.add(key)
            else:
                ticket_rows.append(_ticket_row(ticket, ORPHAN, "TICKET_NO_TRIP"))

    # 第二轮：只写箱号的旧票，仅在同箱唯一一趟且未被日期票认领时入账。
    for ticket in tickets:
        if ticket.get("gate_in") and ticket.get("gate_out"):
            continue
        candidates = by_container.get(ticket["container_id"], [])
        if not candidates:
            ticket_rows.append(_ticket_row(ticket, ORPHAN, "TICKET_NO_TRIP"))
        elif len(candidates) > 1:
            candidate_keys = [trip_key(item.get("container_id"), item.get("gate_in"), item.get("gate_out"))
                              for item in candidates]
            if all(key in dated for key in candidate_keys):
                # 该箱每一趟都已被日期票认领，旧票不可能再唯一归属。
                ticket_rows.append(_ticket_row(ticket, SUPERSEDED, "TICKET_DATED_PRIORITY"))
            else:
                # 同箱多趟：旧票不认领；金额相同也不例外。
                ticket["_candidates"] = candidates
                ticket_rows.append(_ticket_row(ticket, AMBIGUOUS, "TICKET_AMBIGUOUS_CONTAINER"))
        else:
            key = trip_key(candidates[0].get("container_id"),
                           candidates[0].get("gate_in"), candidates[0].get("gate_out"))
            if key in dated:
                ticket_rows.append(_ticket_row(ticket, SUPERSEDED, "TICKET_DATED_PRIORITY", candidates[0]))
            else:
                claims[key].append(ticket)

    # 回合裁决：每趟最多一张票，每票最多入账一次。
    postings = []
    for key in order:
        row = trip_rows[key]
        items = claims[key]
        if not items:
            continue
        if len(items) > 1:
            row["status"], row["reason"] = TRIP_CONFLICT, "TICKET_MULTIPLE_CLAIMS"
            for ticket in items:
                ticket_rows.append(_ticket_row(ticket, CONFLICT, "TICKET_MULTIPLE_CLAIMS"))
            continue
        ticket = items[0]
        row["ticket_id"] = ticket["ticket_id"]
        row["ticket_cents"] = int(ticket["amount_cents"])
        row["diff_cents"] = row["ticket_cents"] - row["fee_cents"]
        if row["diff_cents"] == 0:
            row["status"], row["reason"] = MATCHED, "TICKET_MATCH"
        else:
            row["status"], row["reason"] = AMOUNT_MISMATCH, "TICKET_AMOUNT_MISMATCH"
        postings.append((row["trip_id"], ticket["ticket_id"], row["ticket_cents"]))
        ticket_rows.append(_ticket_row(ticket, POSTED, row["reason"]))

    # 同一张票只能入账一次：重复入账视为硬冲突，全部撤销。
    seen, duplicated = set(), set()
    for _trip_id, ticket_id, _amount in postings:
        if ticket_id in seen:
            duplicated.add(ticket_id)
        seen.add(ticket_id)
    if duplicated:
        for row in trip_rows.values():
            if row["ticket_id"] in duplicated:
                row["status"], row["reason"] = TRIP_CONFLICT, "TICKET_POSTED_TWICE"
                row["ticket_id"], row["ticket_cents"], row["diff_cents"] = None, None, None
        for row in ticket_rows:
            if row["ticket_id"] in duplicated:
                row["status"], row["reason"] = CONFLICT, "TICKET_POSTED_TWICE"
        postings = [item for item in postings if item[1] not in duplicated]

    ticket_rows.sort(key=lambda item: item.get("line") or 0)
    trips_out = []
    for key in order:
        row = trip_rows[key]
        row.pop("_key", None)
        trips_out.append(row)

    counts = {
        "trips": len(trips_out),
        "matched": sum(1 for row in trips_out if row["status"] == MATCHED),
        "mismatched": sum(1 for row in trips_out if row["status"] == AMOUNT_MISMATCH),
        "missing": sum(1 for row in trips_out if row["status"] == NO_TICKET),
        "conflicts": sum(1 for row in trips_out if row["status"] == TRIP_CONFLICT),
        "tickets_input": int((ticket_data.get("counts") or {}).get("input", 0)) if enabled else 0,
        "tickets_accepted": len(tickets),
        "tickets_isolated": int((ticket_data.get("counts") or {}).get("isolated", 0)) if enabled else 0,
        "posted": sum(1 for row in ticket_rows if row["status"] == POSTED),
        "unmatched": sum(1 for row in ticket_rows if row["status"] == ORPHAN),
        "ambiguous": sum(1 for row in ticket_rows if row["status"] == AMBIGUOUS),
        "superseded": sum(1 for row in ticket_rows if row["status"] == SUPERSEDED),
        "ticket_conflicts": sum(1 for row in ticket_rows if row["status"] == CONFLICT),
    }
    checks = []
    if enabled:
        checks = [
            {"id": "ticket_posting", "label": "每趟票据唯一", "ok": counts["conflicts"] == 0},
            {"id": "ticket_once", "label": "同一票据只入账一次", "ok": counts["ticket_conflicts"] == 0},
            {"id": "ticket_coverage", "label": "每趟均有票据", "ok": counts["missing"] == 0},
            {"id": "ticket_accounting", "label": "票据全部入账或说明", "ok": counts["ambiguous"] + counts["unmatched"] == 0},
            {"id": "ticket_source", "label": "票据来源行守恒", "ok": _ticket_balanced(ticket_data)},
        ]
    reconciliation = {
        "enabled": enabled,
        "source": ticket_data.get("source") or {},
        "counts": counts,
        "trips": trips_out,
        "tickets": ticket_rows,
        "issues": list(ticket_data.get("issues") or []),
        "attribution": {ticket_id: trip_id for trip_id, ticket_id, _amount in postings},
        "hard_conflicts": counts["conflicts"] + counts["ticket_conflicts"],
        "checks": checks,
        "all_checks": all(item["ok"] for item in checks),
    }
    reconciliation["fingerprint"] = _attribution_fingerprint(
        enabled, postings,
        [row["trip_id"] for row in trips_out if row["status"] == TRIP_CONFLICT],
        [row["ticket_id"] for row in ticket_rows if row["status"] == CONFLICT])
    return reconciliation


def _ticket_balanced(ticket_data):
    counts = ticket_data.get("counts") or {}
    if not ticket_data.get("enabled"):
        return True
    return int(counts.get("input", 0)) == (int(counts.get("accepted", 0)) + int(counts.get("isolated", 0))
                                           + int(counts.get("duplicates", 0)) + int(counts.get("revised", 0)))
