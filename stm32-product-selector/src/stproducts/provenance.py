"""Where each written value came from.

Every cell in a datasheet-first workbook carries exactly one of these
tokens, recorded in a parallel ``Provenance`` sheet so the data sheet itself
stays clean and usable.

``DATASHEET``    read from the device-summary table or the cover page.
``DERIVED``      computed from datasheet values by a rule stated in the code
                 and the README (see :mod:`stproducts.extract`).
``AMBIGUOUS``    the datasheet carries related information but does not say
                 which value ST publishes; the API value is written and the
                 datasheet's evidence is recorded in the ``Conditions`` sheet.
``API``          the datasheet makes no such assertion, so the API supplies
                 the value. Includes "API via absence": a datasheet that does
                 not mention a feature is not evidence the part lacks it.
``UNAVAILABLE``  neither source has a value.

``DATASHEET`` and ``DERIVED`` both require a non-empty ``source`` naming the
table the value was read from -- :func:`check_invariants` enforces it, so a
cell cannot be labelled as read from a PDF unless something actually read it.

Naming the table is necessary but **not sufficient**, and assuming otherwise
shipped 26 wrong cells. ``CAN (2.0)`` and ``CAN (FD)`` were both configured to
read the summary table's ``Comm. interfaces | CAN`` row, so every STM32F2 part
asserted ``CAN (FD) = 2`` at ``DATASHEET`` provenance for silicon that has no
CAN FD at all. The invariant passed the whole time: a source table *was*
named. What was missing is that the named **row** must support the specific
field being filled. :attr:`Reading.row` records the row label so
:mod:`stproducts.fieldmap` can require or forbid tokens in it.
"""

from __future__ import annotations

from dataclasses import dataclass

DATASHEET = "DATASHEET"
DERIVED = "DERIVED"
AMBIGUOUS = "AMBIGUOUS"
API = "API"
UNAVAILABLE = "UNAVAILABLE"

TOKENS = (DATASHEET, DERIVED, AMBIGUOUS, API, UNAVAILABLE)

#: Tokens whose value came out of a PDF and therefore must name their source.
FROM_PDF = (DATASHEET, DERIVED)


@dataclass(frozen=True)
class Reading:
    """One field's worth of evidence recovered from a datasheet."""

    token: str
    value: str | None = None
    #: Caption of the table (or "cover page") the value was read from.
    source: str = ""
    #: What the datasheet does say, when it cannot settle the value.
    conditions: str = ""
    #: Label of the specific row the value came from, when it came from a row.
    #: Checked against the target column's declared row tokens -- naming the
    #: table is not enough to justify filling a field. Empty for readers that
    #: do not read a labelled row (the cover page, composed sets).
    row: str = ""

    def __post_init__(self) -> None:
        if self.token not in TOKENS:
            raise ValueError(f"unknown provenance token: {self.token!r}")
        if self.token in FROM_PDF and not self.source:
            raise ValueError(f"{self.token} reading must name its source table")
        if self.token in FROM_PDF and self.value is None:
            raise ValueError(f"{self.token} reading must carry a value")


def check_invariants(tokens: list[str], sources: dict[str, str]) -> list[str]:
    """Return a list of violations; empty means the sheet is well-formed."""
    problems = []
    for index, token in enumerate(tokens):
        if token not in TOKENS:
            problems.append(f"cell {index}: unknown token {token!r}")
        elif token in FROM_PDF and not sources.get(str(index)):
            problems.append(f"cell {index}: {token} with no source table")
    return problems
