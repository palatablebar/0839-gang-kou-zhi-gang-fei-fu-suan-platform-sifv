def approval_allowed(payload):
    if int(payload["summary"].get("REJECT", 0)) != 0:
        return False
    tickets = payload.get("tickets") or {}
    if tickets.get("enabled") and int(tickets.get("hard_conflicts") or 0) > 0:
        return False
    return True
