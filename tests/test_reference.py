from ingest.reference import parse_broker_list


def test_parse_broker_list_derives_parent():
    # TWSE broker codes are 4 chars: a 3-char firm prefix + a branch slot.
    # The head office fills slot "0" (1020), branches fill 1..9 / A.. (102A).
    # parent = <prefix>0; the head office is its own parent.
    rows = [["1020", "合作金庫", "..."], ["102A", "合庫-復興", "..."],
            ["1440", "美林", "..."]]
    out = parse_broker_list(rows)
    d = {b[0]: b for b in out}
    assert d["102A"][2] == "1020"                 # parent id
    assert d["102A"][3] == "合作金庫"              # parent name
    assert d["1020"][2] == "1020"                 # a head office is its own parent


def test_parse_broker_list_unknown_parent_falls_back_to_own_name():
    # Foreign / IB brokers appear as branches with no head-office row present.
    rows = [["9B2H", "港商麥格理-復興", "..."]]
    out = parse_broker_list(rows)
    branch_id, branch_name, parent_id, parent_name = out[0]
    assert parent_id == "9B20"
    assert parent_name == "港商麥格理-復興"        # no 9B20 row -> own name


def test_parse_broker_list_dedupes_repeated_codes():
    rows = [["1440", "美林", "x"], ["1440", "美林", "y"]]
    assert len(parse_broker_list(rows)) == 1


import pytest  # noqa: E402

from ingest.reference import refresh_reference_db  # noqa: E402


@pytest.mark.integration
def test_refresh_reference_db_live(tmp_path):
    n_c, n_b = refresh_reference_db(str(tmp_path / "r.db"))
    # TWSE listed (~1094) + TPEx main-board equities (~887) -> ~1981.
    # Brokers: TWSE OpenData_BRK02 branches (~814) + head offices (~64) -> ~878.
    # (Brief's brokerService/brokerList?response=json JSON feed is dead; see
    # module docstring. 900 -> 850 to match the OpenData_BRK02 universe.)
    assert n_c > 1500
    assert n_b > 850
