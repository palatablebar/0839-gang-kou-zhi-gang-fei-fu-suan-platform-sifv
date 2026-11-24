"""趟次身份。

同一箱号在免堆周期内可能多次进出港，每次进出港是一条独立趟次。
趟次由 `container_id + gate_in + gate_out` 共同确定；只有箱号不足以区分趟次。
"""
from datetime import date, datetime

from app.intake.normalize import canonical


def date_key(value) -> str:
    """把日期值归一成 ISO 文本，便于跨层比较。

    采信阶段的原始文本可能带全角字符或未补零，统一按日期解析，
    避免同一趟因写法不同被当成两趟。
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = canonical(value)
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return text


def trip_key(container_id, gate_in, gate_out) -> str:
    return "{}|{}|{}".format(canonical(container_id), date_key(gate_in), date_key(gate_out))


def trip_key_of(item) -> str:
    return trip_key(item.get("container_id"), item.get("gate_in"), item.get("gate_out"))


def trip_id_of(item) -> str:
    """可读且稳定的趟次编号。"""
    container = canonical(item.get("container_id"))
    gate_in = date_key(item.get("gate_in"))
    gate_out = date_key(item.get("gate_out"))
    return "{}@{}~{}".format(container, gate_in, gate_out)
