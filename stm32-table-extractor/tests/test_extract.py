"""Merged-cell (rowspan/colspan) grid explosion (RECOVERY_TASK.md fix 3).

pdfplumber reports a spanning cell once, at its rectangle's top-left "home"
position; every other grid position it visually covers comes back as
`None`. `build_grid` fills each drawn cell's text into every grid position
its rectangle overlaps, instead of leaving those covered positions null.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import pdfplumber

from rmtables.extract import TABLE_SETTINGS, build_grid

HERE = os.path.dirname(__file__)
PDF = os.path.join(
    HERE, "..", "..", "usermanuel",
    "rm0490-stm32c0-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf",
)


def test_table_16_mass_erase_overview_merged_cells_are_filled():
    """Table 16 "Mass erase overview" (page 62): SEC_PROT/PCROP_RDP/CPU bus
    error each span 4 rows; Comment/WRPERR span 3 rows; the bottom row's
    "x" spans 3 columns (PCROP/WRP/PCROP_RDP). Every covered position must
    repeat the spanning value, never come back as None."""
    with pdfplumber.open(PDF) as pdf:
        page = pdf.pages[61]  # page 62
        chars = page.chars
        tables = page.find_tables(table_settings=TABLE_SETTINGS)
        assert len(tables) == 1
        grid = build_grid(tables[0], chars)
        page.flush_cache()

    assert grid == [
        ["SEC_PROT", "PCROP", "WRP", "PCROP_RDP", "Comment", "WRPERR", "CPU bus error"],
        ["0", "No", "No", "x", "Memory is erased", "No", "No"],
        ["0", "No", "Yes", "x", "Erase aborted (no erase started)", "Yes", "No"],
        ["0", "Yes", "No", "x", "Erase aborted (no erase started)", "Yes", "No"],
        ["0", "Yes", "Yes", "x", "Erase aborted (no erase started)", "Yes", "No"],
        ["1", "x", "x", "x", "Erase aborted (no erase started)", "No", "Yes"],
    ]


def test_build_grid_leaves_genuinely_empty_cells_as_empty_string_not_none():
    with pdfplumber.open(PDF) as pdf:
        page = pdf.pages[61]
        chars = page.chars
        table = page.find_tables(table_settings=TABLE_SETTINGS)[0]
        grid = build_grid(table, chars)
        page.flush_cache()

    assert all(cell is not None for row in grid for cell in row)
