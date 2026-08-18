"""Grid parsing and the column-label rule.

The labels here are the exact header strings the shipped workbooks use, so
this test is what pins the rule that lets API columns and workbook columns
be matched on a single string.
"""

from __future__ import annotations

from stproducts.api import parse_grid


def col(**kw):
    base = {"id": "1", "name": "X", "order": "1", "show": True, "type": "string",
            "identifier": "I"}
    base.update(kw)
    return base


def test_label_composition_matches_shipped_headers():
    payload = {
        "levelTitle": "T",
        "columns": [
            col(id="1", name="Part Number", order="1"),
            col(id="2", name="Flash Size", symbol="kB", conditional="Prog", order="2"),
            col(id="3", name="Timers", conditional="16-bit", qualifier="typ", order="3"),
            col(id="4", name="Supply Current", symbol="&#181;A",
                conditional="@ Lowest Power", qualifier="typ", order="4"),
            col(id="5", name="Operating Temperature", symbol="&#176;C",
                qualifier="min", order="5"),
            col(id="6", name="I2C", qualifier="typ", order="6"),
            col(id="7", name="Operating Frequency", symbol="MHz", order="7"),
        ],
        "rows": [],
    }
    labels = [c.label for c in parse_grid("X", payload).columns]
    assert labels == [
        "Part Number",
        "Flash Size (kB) (Prog)",
        "Timers (16-bit) typ",
        "Supply Current (µA) (@ Lowest Power) typ",
        "Operating Temperature (°C) min",
        "I2C typ",
        "Operating Frequency (MHz)",
    ]


def test_aggregation_becomes_a_grouped_key():
    """Three columns share the label 'Number of A/D Converters typ' and are
    told apart only by their group."""
    payload = {
        "levelTitle": "T",
        "columns": [
            col(id="2", name="Number of A/D Converters", qualifier="typ", order="2",
                aggregation="A/D Converters 12-bit"),
            col(id="3", name="Number of Channels", qualifier="typ", order="3",
                aggregation="A/D Converters 12-bit"),
            col(id="4", name="Number of A/D Converters", qualifier="typ", order="4",
                aggregation="A/D Converters 16-bit"),
        ],
        "rows": [],
    }
    keys = [c.key for c in parse_grid("X", payload).columns]
    assert keys == [
        "A/D Converters 12-bit | Number of A/D Converters typ",
        "A/D Converters 12-bit | Number of Channels typ",
        "A/D Converters 16-bit | Number of A/D Converters typ",
    ]
    assert len(set(keys)) == 3


def test_multivalued_is_learned_from_the_rows():
    """`Package` is typed string but holds lists; only the data reveals it."""
    payload = {
        "levelTitle": "T",
        "columns": [
            col(id="1", name="Part Number", order="1"),
            col(id="2", name="Package", order="2"),
            col(id="3", name="Core", order="3"),
        ],
        "rows": [
            {"productId": "PF1", "cells": [
                {"columnId": "1", "value": "STM32F205RB"},
                {"columnId": "2", "value": "LQFP 64||WLCSP 66"},
                {"columnId": "3", "value": "Arm Cortex-M3"},
            ]},
        ],
    }
    grid = parse_grid("X", payload)
    by_key = grid.by_key()
    assert by_key["Package"].multivalued is True
    assert by_key["Core"].multivalued is False


def test_rows_are_keyed_by_part_number():
    payload = {
        "levelTitle": "STM32F2 series",
        "columns": [col(id="1", name="Part Number", order="1")],
        "rows": [
            {"productId": "PF250192",
             "productFolderUrl": "/en/microcontrollers-microprocessors/stm32f205rb.html",
             "cells": [{"columnId": "1", "value": " STM32F205RB "}]},
            {"productId": "PF2", "cells": []},  # no part number -> dropped
        ],
    }
    grid = parse_grid("SS1575", payload)
    assert grid.part_numbers == ["STM32F205RB"]
    assert grid.rows[0]["product_id"] == "PF250192"
    assert grid.level_title == "STM32F2 series"
