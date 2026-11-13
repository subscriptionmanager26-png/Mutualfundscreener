from datetime import datetime

from mfscreener.amfi.parser import parse_navall, parse_plan_option


def test_parse_plan_option():
    assert parse_plan_option("Direct", "Growth Option") == ("direct", "growth")
    assert parse_plan_option("Regular", "IDCW") == ("regular", "idcw")


def test_parse_navall_minimal():
    sample = """Open Ended Schemes(Equity Scheme - Large Cap Fund)

Foo Mutual Fund
100001;INFTEST00101;;Test Large Cap Fund;Direct;Growth;123.45;09-Nov-2026
"""
    rows, latest = parse_navall(sample)
    assert len(rows) == 1
    assert rows[0].scheme_code == 100001
    assert rows[0].nav == 123.45
    assert latest == datetime(2026, 11, 9)
