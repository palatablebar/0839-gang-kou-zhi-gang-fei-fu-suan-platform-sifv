from datetime import date, timedelta
from app.catalog import SPEC
from app.domain.helpers import *  # noqa: F401,F403
from app.domain.trip import trip_id_of


def calculate_rows(items):
    out = []
    rows = items
    holidays = {date(2026, 10, d) for d in range(1, 8)} | {date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)}
    for item in items:
        deadline = business_days_after(item["gate_in"], item["free_days"], holidays)
        charge_days = max(0, (item["gate_out"] - deadline).days)
        fee = tier_fee(charge_days)
        item["free_end"] = deadline.isoformat()
        item["charge_days"] = charge_days
        item["fee_cents"] = fee
        item["metric"] = D(str(fee), "CALC")
        item["limit"] = D("60000", "LIMIT")
        item["trip_id"] = trip_id_of(item)
        out.append(item)
    # 同箱多趟时箱号不再唯一，用趟次编号区分展示与证据条目。
    container_counts = {}
    for item in out:
        container_counts[item.get(SPEC["key"])] = container_counts.get(item.get(SPEC["key"]), 0) + 1
    for item in out:
        container = item.get(SPEC["key"])
        if container_counts.get(container, 0) > 1:
            item["label"] = item["trip_id"]
        else:
            item.setdefault("label", str(container or ""))
    return out
