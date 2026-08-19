"""Grid parsing and the column-label rule.

The labels here are the exact header strings the shipped workbooks use, so
this test is what pins the rule that lets API columns and workbook columns
be matched on a single string.
"""

from __future__ import annotations

import base64
import json

from stproducts.api import EXCEL_DOWNLOAD_URL, download_workbook, excel_export_payload, parse_grid


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


def test_excel_export_payload_mirrors_get_download_info():
    """The ``downloadInfo`` POSTed to products-excel-download: fixed leading
    columns, then every visible column in its own order, all product ids."""
    payload = {
        "levelTitle": "T",
        "columns": [
            # id "4" here is a column ST happens to call "General Description";
            # the point is the export always opens 1, 4, 163 in that order.
            col(id="1", name="Part Number", order="1"),
            col(id="4", name="General Description", order="2"),
            col(id="163", name="Marketing Status", order="3"),
            col(id="10", name="Flash Size", order="4"),
            col(id="11", name="Operating Temperature", qualifier="max", order="5"),
            col(id="12", name="Not shown", order="6", show=False),
        ],
        "rows": [
            {"productId": "PF1", "path": "/etc/prmis/products/LN0001/PF1", "cells": [
                {"columnId": "1", "value": "A"},
                {"columnId": "10", "value": "100"},
            ]},
            {"productId": "PF2", "cells": [
                {"columnId": "1", "value": "B"},
                {"columnId": "11", "value": "85"},
            ]},
        ],
    }
    grid = parse_grid("LN0001", payload)
    out = excel_export_payload(grid)
    assert out["rootProductId"] == "LN0001"
    # 1, 4, 163 always lead; hidden columns are never exported.
    assert out["columnIds"] == ["1", "4", "163", "10", "11"]
    assert out["superAttributesColumnIds"] == ["11"]
    assert out["productIds"] == ["PF1", "PF2"]
    assert out["exponentPF"] == "PF1"  # its path contains the root id
    # Rows are dense: every column id present, "-" for blank cells. ST's
    # server drops a missing key and shifts the row, so a sparse part would
    # otherwise land its values under the wrong headers.
    assert out["rows"][0] == {"1": "A", "4": "-", "163": "-", "10": "100", "11": "-"}
    assert out["rows"][1] == {"1": "B", "4": "-", "163": "-", "10": "-", "11": "85"}


def test_excel_export_payload_exponent_falls_back_to_last_product():
    payload = {
        "levelTitle": "T",
        "columns": [col(id="1", name="Part Number", order="1")],
        "rows": [
            {"productId": "PF1", "path": "", "cells": [{"columnId": "1", "value": "A"}]},
            {"productId": "PF2", "path": "", "cells": [{"columnId": "1", "value": "B"}]},
        ],
    }
    grid = parse_grid("LN0001", payload)
    assert excel_export_payload(grid)["exponentPF"] == "PF2"


def test_download_workbook_posts_base64_request_data():
    payload = {
        "levelTitle": "T",
        "columns": [col(id="1", name="Part Number", order="1")],
        "rows": [
            {"productId": "PF1", "path": "/LN0001/PF1",
             "cells": [{"columnId": "1", "value": "A"}]},
        ],
    }
    grid = parse_grid("LN0001", payload)

    class StubFetcher:
        def __init__(self):
            self.calls = []

        def post_form_bytes(self, url, data, *, referer=None):
            self.calls.append((url, data, referer))
            return b"PK\x03\x04 fake xlsx"

    stub = StubFetcher()
    raw = download_workbook(stub, grid)
    assert raw == b"PK\x03\x04 fake xlsx"
    (url, data, referer) = stub.calls[0]
    assert url == EXCEL_DOWNLOAD_URL
    assert list(data) == ["requestData"]
    decoded = json.loads(base64.b64decode(data["requestData"]).decode("utf-8"))
    assert decoded["rootProductId"] == "LN0001"
    assert decoded["productIds"] == ["PF1"]
