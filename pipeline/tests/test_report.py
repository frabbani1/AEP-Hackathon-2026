import re

import pytest

from aep_pipeline.pipeline import _busiest_week_share, _count_nonconforming
from aep_pipeline.report import known_gaps, n, pct, render_report
import pandas as pd


def context(**overrides):
    base = {
        "damage": {"has_time_component": False, "busiest_week_share": 0.5},
        "switch_in_topology": {"total": 900, "in_topology": 0},
        "crosswalk": {"nonconforming_circuit_aliases": 0, "nonconforming_substation_aliases": 0},
        "placeholder_rates": {"poles": 0.5, "primary lines": 0.8, "secondary lines": 0.6},
    }
    base.update(overrides)
    return base


def test_four_required_gaps_are_always_stated():
    text = "\n".join(known_gaps(context()))
    assert "No weather or storm characteristics are provided" in text
    assert "no time of day" in text
    assert "no outage identifier and no damage quantity" in text
    assert "Switches are absent from the topology file (0 of 900" in text


def test_time_statement_is_checked_against_the_data():
    text = "\n".join(known_gaps(context(damage={"has_time_component": True, "busiest_week_share": 0.1})))
    assert "no time of day" not in text and "carry a time of day" in text


def test_alias_gap_appears_only_when_there_are_nonconforming_values():
    assert not any("anonymized" in gap for gap in known_gaps(context()))
    gaps = known_gaps(context(crosswalk={"nonconforming_circuit_aliases": 6, "nonconforming_substation_aliases": 0}))
    assert "6 circuit alias value(s)" in gaps[0] and "server artifacts only" in gaps[0]


def test_alias_count_helper_counts_distinct_values_not_rows():
    assert _count_nonconforming({"CIR1", "CIR20", "PENN FOREST", "7-1 NETWORK"}, r"CIR\d+") == 2
    assert _count_nonconforming(set(), r"CIR\d+") == 0


def test_busiest_week_share_uses_a_seven_day_window():
    dates = pd.Series(pd.to_datetime(["2025-02-13"] * 5 + ["2025-02-19"] * 2 + ["2025-02-20"] + ["2025-04-01"] * 2))
    assert _busiest_week_share(dates) == pytest.approx(7 / 10)  # 13th to 19th inclusive
    assert _busiest_week_share(pd.Series([], dtype="datetime64[ns]")) == 0.0


def test_numbers_are_formatted_so_none_looks_like_a_raw_identifier():
    assert n(117599) == "117,599" and n(1234567) == "1,234,567" and n(None) == "n/a"
    assert pct(0.9996) == "99.96%" and pct(None) == "n/a"
    assert not re.search(r"(?<![\w,.])\d{7,9}(?![\w,.])", n(123456789))
