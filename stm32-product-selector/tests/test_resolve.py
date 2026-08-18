"""Candidate discovery, and the ambiguity that makes verification necessary."""

from __future__ import annotations

from stproducts.resolve import ids_from_page, slugify

# The real markup: a sub-family page whose hierarchy leads with its own id
# and then names its parents.
SUBFAMILY_PAGE = """
  window.productHierarchy = "LN1433-SS1575-SC2154-CL1734-FM141".split('-');
  ... links to SC2154 ... SS1575 ... SS1575 ... SS1575 ...
"""

# The parent series page: same parents, no LN of its own.
SERIES_PAGE = """
  window.productHierarchy = "SS1575-SC2154-CL1734-FM141".split('-');
  ... SS1575 SS1575 SS1575 SC2154 ...
"""


def test_slugify_matches_st_page_slugs():
    assert slugify("STM32F2 series") == "stm32f2-series"
    assert slugify("STM32F2x5") == "stm32f2x5"
    assert slugify("STM8AF series ") == "stm8af-series"
    assert slugify("STM32 high performance MCUs") == "stm32-high-performance-mcus"
    assert slugify("STM8 8-bit MCUs") == "stm8-8-bit-mcus"
    assert slugify("STM32 Arm Cortex MPUs") == "stm32-arm-cortex-mpus"


def test_hierarchy_leads_with_the_pages_own_id():
    ids = ids_from_page(SUBFAMILY_PAGE)
    assert ids[0] == ("LN1433", "productHierarchy")
    assert [i for i, _ in ids][:3] == ["LN1433", "SS1575", "SC2154"]


def test_sub_family_page_also_carries_its_parent():
    """Why scraping alone is not enough: the parent's id is all over the
    sub-family page, so a naive scrape resolves STM32F2x5 to STM32F2 series."""
    assert "SS1575" in [i for i, _ in ids_from_page(SUBFAMILY_PAGE)]


def test_non_grid_hierarchy_levels_are_ignored():
    """CL and FM are hierarchy bookkeeping; the grid answers 400 for them."""
    ids = [i for i, _ in ids_from_page(SUBFAMILY_PAGE)]
    assert "CL1734" not in ids
    assert "FM141" not in ids


def test_series_page_has_no_line_id():
    assert [i for i, _ in ids_from_page(SERIES_PAGE)][0] == "SS1575"


def test_scrape_fallback_orders_by_frequency():
    """With no hierarchy, the page's own grid is the most-mentioned id."""
    page = "SC9999 SS1111 SS1111 SS1111 SC9999x"
    assert [i for i, _ in ids_from_page(page)][0] == "SS1111"
