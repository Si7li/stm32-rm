"""The normalisation rules -- the part that decides whether the diff is
believable. Each case here is a rendering difference that showed up in the
real workbooks and must not be reported as a wrong value."""

from __future__ import annotations

import pytest

from stproducts.api import Column
from stproducts.values import canon, is_blank, render


def column(**kw) -> Column:
    base = dict(id="1", name="X", order=1, show=True, type="string", identifier="")
    base.update(kw)
    return Column(**base)


NUMERIC = column(type="float", name="Flash Size")
BOOLEAN = column(type="boolean", name="Dual-bank Flash")
MULTI = column(type="multi", name="Other timer functions")
LISTY = column(type="string", name="Package", multivalued=True)
TEMP_MAX = column(type="float", name="Operating Temperature", qualifier="max")
TEMP_MIN = column(type="float", name="Operating Temperature", qualifier="min")


@pytest.mark.parametrize(
    "left,right,col",
    [
        ("120", "120.0", NUMERIC),          # numeric formatting
        ("120", " 120 ", NUMERIC),          # padding
        ("-", "", None),                    # the workbook's blank vs the API's
        ("—", "", None),
        ("A  B", "A B", None),              # collapsed whitespace
        ("No", "false", BOOLEAN),           # boolean spelling
        ("Yes", "true", BOOLEAN),
        ("No", "", BOOLEAN),                # absent boolean means No
        ("No", "false||true", BOOLEAN),     # mixed across variants
        ("Dual Watchdog, RTC", "Dual Watchdog||RTC", MULTI),
        ("RTC, Dual Watchdog", "Dual Watchdog||RTC", MULTI),  # order is rendering
        ("LQFP 64 x, WLCSP 66 y", "LQFP 64 x||WLCSP 66 y", LISTY),
        ("105", "105||85", TEMP_MAX),       # qualifier picks the extreme
        ("85", "105||85", TEMP_MIN),
    ],
)
def test_equivalent_renderings_are_not_changes(left, right, col):
    assert canon(left, col) == canon(right, col)


@pytest.mark.parametrize(
    "left,right,col",
    [
        ("Ethenet, SD/MMC", "Ethernet, SD/MMC", MULTI),  # the real typo
        ("Intenal", "Internal", None),
        ("2", "3", NUMERIC),
        ("Yes", "false", BOOLEAN),
        ("105", "125||85", TEMP_MAX),
        ("A, B", "A, B, C", MULTI),
    ],
)
def test_genuine_differences_are_reported(left, right, col):
    assert canon(left, col) != canon(right, col)


def test_description_commas_are_not_a_list():
    """A prose column must not be tokenised, or reordered prose would pass."""
    prose = column(type="string", name="General Description")
    assert canon("a, b, c", prose) != canon("c, b, a", prose)


def test_render_reproduces_export_conventions():
    assert render("false", BOOLEAN) == "No"
    assert render("true", BOOLEAN) == "Yes"
    assert render("false||true", BOOLEAN) == "No"
    assert render(None, BOOLEAN) == "No"
    assert render(None, NUMERIC) == "-"
    assert render("A||B", MULTI) == "A, B"
    assert render("105||85", TEMP_MAX) == "105"
    assert render("&#181;A", None) == "µA"


def test_is_blank():
    assert is_blank(None) and is_blank("") and is_blank(" - ") and is_blank("—")
    assert not is_blank("0")
