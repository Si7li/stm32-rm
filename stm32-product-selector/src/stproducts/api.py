"""ST product-selector API: the grid endpoint and per-part detail.

URL shape, read out of ``product-selector.min.js``::

    composeServiceURL: name => "/bin/st/selectors/cxst/" + language + "." + name + ".html/"

so the two endpoints used here are::

    /bin/st/selectors/cxst/en.cxst-ps-grid.html/{levelId}.json
    /bin/st/selectors/cxst/en.cxst-rpn-info.html/{productId}.json

Column headers
--------------
The grid returns column *metadata*, not the rendered header text. The header
ST's own export writes is composed from that metadata::

    name [ " (<symbol>)" ] [ " (<conditional>)" ] [ " <qualifier>" ]

and when a column carries an ``aggregation`` key, that string becomes a
merged group header on the header row with the composed label beneath it as
a sub-header -- which is where ``Number of Channels typ`` under
``A/D Converters 12-bit`` comes from.

This rule was checked against all three distinct workbook shapes
(STM32F2 series, STM8 8-bit MCUs, STM32 Arm Cortex MPUs) and reproduces
every one of their headers exactly, with no misses either way.
"""

from __future__ import annotations

import base64
import html
import json
import re
from dataclasses import dataclass, field, replace

from .net import ST_ROOT, Fetcher

GRID_URL = ST_ROOT + "/bin/st/selectors/cxst/en.cxst-ps-grid.html/{level_id}.json"
RPN_URL = ST_ROOT + "/bin/st/selectors/cxst/en.cxst-rpn-info.html/{product_id}.json"

#: What ST's "Export to Excel" button POSTs to, with a ``requestData`` form
#: field carrying the base64 JSON of :func:`excel_export_payload`. Read out of
#: ``product-selector.min.js`` (``onDownloadBtnClick`` /
#: ``getDownloadInfo``) and ``commons.min.js`` (``FileDownloader``).
EXCEL_DOWNLOAD_URL = ST_ROOT + "/bin/st/selectors/cxst/products-excel-download"

PART_NUMBER_COLUMN_ID = "1"

#: Three columns ST's export always opens with, in that order.
FIXED_EXPORT_COLUMNS = ("1", "4", "163")

#: ST joins repeated values with this inside a single cell.
MULTI_SEP = "||"

#: One component of a row's hierarchy path. ``SS`` (series), ``SC``
#: (catalogue) and ``LN`` (product line) are served by the grid endpoint;
#: ``CL`` and ``FM`` are bookkeeping and answer HTTP 400.
LEVEL_SEGMENT = re.compile(r"((?:FM|CL|SC|SS|LN|PF))\d{3,6}")

#: The level families a grid will actually serve.
GRID_LEVELS = ("SC", "SS", "LN")


@dataclass(frozen=True)
class Column:
    """One selector column, with the header text ST would render for it."""

    id: str
    name: str
    order: int
    show: bool
    type: str
    identifier: str
    symbol: str | None = None
    conditional: str | None = None
    qualifier: str | None = None
    aggregation: str | None = None
    #: True when some row actually carries several values in this column.
    #: Learned from the data, because the declared type does not say so:
    #: ``Package`` is typed ``string`` yet holds ``LQFP 64 ...||WLCSP 66 ...``,
    #: and the workbook renders that as a comma-separated list. Without this
    #: the two sides tokenise differently and every multi-package part reads
    #: as a spurious change.
    multivalued: bool = False

    @property
    def label(self) -> str:
        """The composed label -- the sub-header when grouped, else the header."""
        text = self.name
        if self.symbol:
            text += f" ({self.symbol})"
        if self.conditional:
            text += f" ({self.conditional})"
        if self.qualifier:
            text += f" {self.qualifier}"
        return text

    @property
    def key(self) -> str:
        """Stable identity of a column across API and workbook.

        Grouped columns need the group in the key: three different columns
        are all called ``Number of A/D Converters typ`` and are told apart
        only by their 12-/14-/16-bit aggregation.
        """
        return f"{self.aggregation} | {self.label}" if self.aggregation else self.label

    @property
    def is_numeric(self) -> bool:
        return self.type in ("integer", "float")

    @property
    def is_list(self) -> bool:
        return self.type == "multi" or self.multivalued


@dataclass
class Grid:
    """A parsed ``cxst-ps-grid`` response."""

    level_id: str
    level_title: str
    columns: list[Column]
    rows: list[dict] = field(default_factory=list)
    #: ``Microcontrollers & microprocessors/STM32 .../STM32F2 series``.
    breadcrumb: str = ""

    @property
    def part_numbers(self) -> list[str]:
        return [r["part_number"] for r in self.rows]

    def level_ids(self) -> dict[str, set[str]]:
        """Every level id named in the rows' hierarchy paths, by family.

        Each row carries its full position in ST's product tree::

            /etc/prmis/products/FM141/CL1734/SC2154/SS1577/LN1938/PF262719
                                family class  catal.  series line   product

        so one catalogue-level grid names every series and product line
        beneath it. That is the whole discovery mechanism, and it needs no
        HTML: reading these paths avoids the trap that a sub-family *page*
        embeds its parent's ``SS`` id, which makes scraping resolve
        STM32F2x5 to the 38-row STM32F2 series grid, silently.
        """
        found: dict[str, set[str]] = {}
        for row in self.rows:
            for segment in (row.get("path") or "").split("/"):
                match = LEVEL_SEGMENT.fullmatch(segment)
                if match:
                    found.setdefault(match.group(1), set()).add(segment)
        return found

    def by_key(self) -> dict[str, Column]:
        return {c.key: c for c in self.columns}

    def rows_by_part(self) -> dict[str, dict]:
        return {r["part_number"]: r for r in self.rows}


def _unescape(value: str | None) -> str | None:
    return html.unescape(value) if value else value


def parse_grid(level_id: str, payload: dict) -> Grid:
    columns = [
        Column(
            id=str(c["id"]),
            name=_unescape(c.get("name")) or "",
            order=int(c.get("order", 0)),
            show=bool(c.get("show", True)),
            type=c.get("type", "string"),
            identifier=c.get("identifier", ""),
            symbol=_unescape(c.get("symbol")),
            conditional=_unescape(c.get("conditional")),
            qualifier=c.get("qualifier"),
            aggregation=_unescape(c.get("aggregation")),
        )
        for c in payload.get("columns", [])
    ]
    columns.sort(key=lambda c: c.order)

    rows = []
    for r in payload.get("rows", []):
        cells = {str(c["columnId"]): c.get("value") for c in r.get("cells", [])}
        part = cells.get(PART_NUMBER_COLUMN_ID)
        if not part:
            continue
        rows.append(
            {
                "part_number": str(part).strip(),
                "product_id": r.get("productId"),
                "product_folder_url": r.get("productFolderUrl"),
                "path": r.get("path") or "",
                "cells": cells,
            }
        )
    # Second pass: mark the columns that actually hold repeated values.
    multi_ids = {
        column_id
        for row in rows
        for column_id, value in row["cells"].items()
        if value and MULTI_SEP in str(value)
    }
    if multi_ids:
        columns = [
            replace(c, multivalued=True) if c.id in multi_ids else c for c in columns
        ]

    return Grid(
        level_id=level_id,
        level_title=payload.get("levelTitle") or "",
        columns=columns,
        rows=rows,
        breadcrumb=payload.get("breadcrumb") or "",
    )


def fetch_grid(fetcher: Fetcher, level_id: str, *, referer: str | None = None) -> Grid:
    payload = fetcher.get_json(
        GRID_URL.format(level_id=level_id),
        referer=referer or ST_ROOT + "/en/microcontrollers-microprocessors.html",
        xhr=True,
    )
    return parse_grid(level_id, payload)


def excel_export_payload(grid: Grid) -> dict:
    """The ``downloadInfo`` object ST's export endpoint expects.

    Mirrors ``getDownloadInfo`` in ST's ``product-selector.min.js``: the
    fixed leading columns, then every visible column in its own ``order``,
    all product ids, and one row object per product. ``rows`` is sent for
    fidelity; the server rebuilds rows from ``productIds``, which is why a
    row keyed by column id answers the same way the JS's does.

    One deliberate difference from the JS: every ``rows`` entry carries **all**
    column ids, filling absent cells with ``-``. The JS only sends the cells
    the grid returned, and ST's server drops a row cell whose key is absent
    and writes the rest positionally -- so a sparse part's values slide left
    under the wrong headers. A dense row (verified for LN2519) keeps ST's own
    export aligned, which is the whole point of downloading it.
    """
    visibility_ordered = sorted(
        (c for c in grid.columns if c.show), key=lambda c: c.order
    )
    column_ids: list[str] = ["1"]
    if any(c.id == "4" for c in grid.columns):
        column_ids.append("4")
    column_ids.append("163")
    column_ids.extend(c.id for c in visibility_ordered if c.id not in FIXED_EXPORT_COLUMNS)

    super_attribute_ids = [
        c.id
        for c in visibility_ordered
        if c.qualifier and c.id not in FIXED_EXPORT_COLUMNS
    ]

    product_ids = [r["product_id"] for r in grid.rows]
    exponent_pf = next(
        (r["product_id"] for r in grid.rows if grid.level_id in (r.get("path") or "")),
        product_ids[-1] if product_ids else None,
    )
    rows = []
    for row in grid.rows:
        cells = {column_id: "-" for column_id in column_ids}
        cells.update(
            {cid: value for cid, value in row["cells"].items() if cid in column_ids}
        )
        rows.append(cells)
    return {
        "exponentPF": exponent_pf,
        "rootProductId": grid.level_id,
        "productIds": product_ids,
        "columnIds": column_ids,
        "superAttributesColumnIds": super_attribute_ids,
        "rows": rows,
    }


def download_workbook(fetcher: Fetcher, grid: Grid, *, referer: str | None = None) -> bytes:
    """POST ``products-excel-download`` and return the raw XLSX bytes.

    The response is a genuine ST export -- the same file the site's Export to
    Excel button hands to a browser -- so a selector with no local workbook
    can still get a real diff against ST's own original.
    """
    payload = json.dumps(excel_export_payload(grid))
    request_data = base64.b64encode(payload.encode("utf-8")).decode("ascii")
    return fetcher.post_form_bytes(
        EXCEL_DOWNLOAD_URL,
        data={"requestData": request_data},
        referer=referer or ST_ROOT + "/en/microcontrollers-microprocessors.html",
    )


def fetch_datasheet_url(fetcher: Fetcher, product_id: str, *, referer: str | None = None) -> str | None:
    """``cxst-rpn-info`` takes ST's internal productId (``PF250192``), not the
    part number, and answers with the datasheet path."""
    payload = fetcher.get_json(
        RPN_URL.format(product_id=product_id),
        referer=referer or ST_ROOT + "/en/microcontrollers-microprocessors.html",
        xhr=True,
    )
    path = payload.get("downloadURL")
    if not path:
        return None
    return path if path.startswith("http") else ST_ROOT + path
