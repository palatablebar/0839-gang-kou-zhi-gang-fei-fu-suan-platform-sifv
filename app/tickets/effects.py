"""票据对账对逐趟判定的影响。

判定优先级：票据硬冲突（同一趟被多张票认领、同一票重复入账）为阻断；
票据金额不符或缺少票据为人工复核；其余保留原有判定。
"""
SEVERITY = {"PASS": 0, "REVIEW": 1, "REJECT": 2}

TICKET_VERDICT = {
    "MATCHED": ("PASS", "TICKET_MATCH"),
    "AMOUNT_MISMATCH": ("REVIEW", "TICKET_AMOUNT_MISMATCH"),
    "NO_TICKET": ("REVIEW", "TICKET_MISSING"),
    "CONFLICT": ("REJECT", "TICKET_MULTIPLE_CLAIMS"),
}


def apply_verdict(item, trip, enabled):
    item["ticket_id"] = trip.get("ticket_id")
    item["ticket_cents"] = trip.get("ticket_cents")
    item["ticket_diff_cents"] = trip.get("diff_cents")
    item["ticket_status"] = trip.get("status")
    if not enabled:
        item["ticket_reason"] = "TICKETS_DISABLED"
        return item
    item["ticket_reason"] = trip.get("reason")
    verdict, reason = TICKET_VERDICT.get(trip.get("status"), (None, None))
    if verdict is None:
        return item
    # 缺票只提示，不掩盖更严重的既有判定；金额不符与冲突会抬高判定级别。
    if verdict == "PASS":
        return item
    if SEVERITY[verdict] > SEVERITY.get(item.get("verdict", "PASS"), 0):
        item["verdict"] = verdict
        item["reason"] = reason
    elif item.get("verdict") == "PASS":
        item["verdict"] = verdict
        item["reason"] = reason
    item.setdefault("ticket_flags", []).append(trip.get("reason"))
    return item
