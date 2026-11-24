import pytest

from app.errors import DomainIssue
from app.tickets.service import load_tickets, tickets_from_rows


def write(tmp_path, body, name="tickets.csv", header="ticket_id,container_id,gate_in,gate_out,amount_cents"):
    path = tmp_path / name
    path.write_text(header + "\n" + body, encoding="utf-8")
    return path


def test_ticket_file_can_use_port_aliases(tmp_path):
    path = write(tmp_path, "T1,C1,2026-11-02,2026-11-10,52000\n",
                 header="票号,箱号,进港日期,出港日期,金额分")
    data = load_tickets(None, path)
    assert data["counts"]["accepted"] == 1
    assert data["tickets"][0]["container_id"] == "C1"


def test_ticket_header_must_map_all_columns(tmp_path):
    path = write(tmp_path, "T1,C1,52000\n", header="ticket_id,container_id,amount_cents")
    with pytest.raises(DomainIssue) as error:
        load_tickets(None, path)
    assert error.value.code == "HEADER_MISMATCH"


def test_ticket_row_with_wrong_width_is_isolated(tmp_path):
    path = write(tmp_path, "T1,C1,2026-11-02,2026-11-10,52000,extra\n")
    data = load_tickets(None, path)
    assert data["counts"]["isolated"] == 1
    assert any(issue["code"] == "TICKET_COLUMN_COUNT" for issue in data["issues"])


def test_ticket_dates_must_be_complete_or_absent(tmp_path):
    path = write(tmp_path, "T1,C1,2026-11-02,,52000\n")
    data = load_tickets(None, path)
    assert data["counts"]["isolated"] == 1
    assert any(issue["code"] == "TICKET_PARTIAL_DATES" for issue in data["issues"])


def test_ticket_amount_can_be_given_in_yuan():
    data = tickets_from_rows([{"ticket_id": "T1", "container_id": "C1", "amount": "520.00"}])
    assert data["tickets"][0]["amount_cents"] == 52000


def test_ticket_leading_zero_is_preserved():
    data = tickets_from_rows([{"ticket_id": "0007", "container_id": "0001", "amount_cents": "0"}])
    assert data["tickets"][0]["ticket_id"] == "0007"
    assert data["tickets"][0]["container_id"] == "0001"


def test_blank_ticket_file_is_rejected(tmp_path):
    path = tmp_path / "blank.csv"
    path.write_text("", encoding="utf-8")
    with pytest.raises(DomainIssue) as error:
        load_tickets(None, path)
    assert error.value.code == "EMPTY_FILE"


def test_missing_default_ticket_source_is_simply_disabled():
    assert load_tickets("no-such-profile")["enabled"] is False
