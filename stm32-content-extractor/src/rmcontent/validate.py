"""Reconcile extracted sections against the manual's own Contents pages.

The Contents is the exact analog of the List of tables: ST's own
statement of what the document contains, printed in the document. Every
number reported here is a comparison against that, not a self-consistency
check.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from .exporter import OVERSIZED_CHARS, section_sort_key
from .registers import REGISTER_BITS, check_bit_coverage, declared_width


@dataclass
class ValidationReport:
    missing_sections: list = field(default_factory=list)  # in Contents, not extracted
    extra_sections: list = field(default_factory=list)  # extracted, not in Contents
    contents_section_count: int = 0
    contents_chapter_count: int = 0
    extracted_count: int = 0
    subsection_count: int = 0
    chapters_resolved: int = 0
    sections_without_chapter_title: list = field(default_factory=list)
    register_description_count: int = 0
    field_count: int = 0
    named_field_count: int = 0
    # Fields do not partition bits 31..0 -- the check §5 asks for, on the
    # full 32-bit word: (section, register, problem).
    coverage_errors: list = field(default_factory=list)
    # The subset of those that ST itself declares narrower than 32 bits
    # (a 16-bit `Reset value: 0x0000`) and that partition their own width
    # exactly: expected, not a defect, and documented with the reason.
    # (section, register, width, reason)
    narrow_registers: list = field(default_factory=list)
    # The subset that is a genuine gap/overlap within the register's own
    # declared width -- these are parse bugs: (section, register, problem)
    real_coverage_errors: list = field(default_factory=list)
    oversized: list = field(default_factory=list)  # (section, chars)
    noise_summary: list = field(default_factory=list)
    uncaptioned_regions: int = 0
    empty_sections: int = 0
    recovered_headings: list = field(default_factory=list)
    rejected_headings: list = field(default_factory=list)
    chapters_without_sections: list = field(default_factory=list)
    chapter_records: list = field(default_factory=list)
    chapters_without_own_record: list = field(default_factory=list)
    rejected_chapters: list = field(default_factory=list)
    multi_register_sections: list = field(default_factory=list)

    def is_clean(self) -> bool:
        return not (
            self.missing_sections
            or self.extra_sections
            or self.sections_without_chapter_title
            or self.chapters_without_own_record
            or self.real_coverage_errors
        )

    def summary(self) -> str:
        lines = [
            f"records: {self.extracted_count} total "
            f"({self.subsection_count} numbered sections + "
            f"{len(self.chapter_records)} chapters)",
            f"sections: {self.subsection_count} extracted vs "
            f"{self.contents_section_count} listed in Contents",
            f"missing (in Contents, not extracted): {len(self.missing_sections)} "
            f"{self.missing_sections[:20]}",
            f"extra (extracted, not in Contents): {len(self.extra_sections)} "
            f"{self.extra_sections[:20]}",
            f"chapters resolved: {self.chapters_resolved}/{self.contents_chapter_count}",
            f"chapter-level records emitted: {len(self.chapter_records)}",
            f"Contents chapters with NO record at all: "
            f"{len(self.chapters_without_sections)} {self.chapters_without_sections}",
            f"Contents chapters with no chapter-level record: "
            f"{len(self.chapters_without_own_record)} {self.chapters_without_own_record}",
            f"chapter heading candidates rejected: {len(self.rejected_chapters)} "
            f"{self.rejected_chapters[:10]}",
            f"sections with empty chapter_title: {len(self.sections_without_chapter_title)} "
            f"{self.sections_without_chapter_title[:20]}",
            f"headings recovered via the Contents: {len(self.recovered_headings)} "
            f"{self.recovered_headings[:10]}",
            f"heading candidates rejected (implausible chapter): "
            f"{len(self.rejected_headings)} {self.rejected_headings[:10]}",
            f"register descriptions: {self.register_description_count}",
            f"sections documenting several registers (left generic): "
            f"{len(self.multi_register_sections)} {self.multi_register_sections[:10]}",
            f"fields extracted: {self.field_count} "
            f"({self.named_field_count} named, "
            f"{self.field_count - self.named_field_count} reserved)",
            f"registers failing coverage at their own declared width: "
            f"{len(self.real_coverage_errors)}",
        ]
        for section, register, problem in self.real_coverage_errors[:40]:
            lines.append(f"    {section} {register or '(unnamed)'}: {problem}")
        if len(self.real_coverage_errors) > 40:
            lines.append(f"    ... and {len(self.real_coverage_errors) - 40} more")
        # Context, not failures: ST documents these registers as narrower
        # than a full word, and each covers its own width exactly.
        widths = Counter(width for _, _, width, _ in self.narrow_registers)
        lines.append(
            f"registers ST declares narrower than {REGISTER_BITS} bits: "
            f"{len(self.narrow_registers)} "
            f"({', '.join(f'{n} x {w}-bit' for w, n in sorted(widths.items()))})"
            if self.narrow_registers else
            f"registers ST declares narrower than {REGISTER_BITS} bits: 0"
        )
        lines.append(
            f"  (informational) registers not covering the full 31..0 word: "
            f"{len(self.coverage_errors)}"
        )
        for section, register, _, reason in self.narrow_registers[:10]:
            lines.append(f"    {section} {register or '(unnamed)'}: {reason}")
        if len(self.narrow_registers) > 10:
            lines.append(f"    ... and {len(self.narrow_registers) - 10} more")
        lines.append(f"sections over {OVERSIZED_CHARS} characters: {len(self.oversized)}")
        for section, chars in self.oversized:
            lines.append(f"  {section}: {chars} chars")
        lines.append(f"sections with empty section_content: {self.empty_sections}")
        lines.append(f"uncaptioned table regions (no marker emitted): {self.uncaptioned_regions}")
        lines.extend(self.noise_summary)
        return "\n".join(lines)


def validate(doc: dict, contents, scanner) -> ValidationReport:
    report = ValidationReport()
    records = doc["sections"]
    # Chapter-level records reconcile against the Contents' CHAPTER list,
    # not its section list, so they are excluded here -- otherwise every
    # chapter would be reported as an "extra" section.
    extracted = {r["section"] for r in records if r["level"] > 1}
    listed = set(contents.sections)

    report.extracted_count = len(records)
    report.subsection_count = len(extracted)
    report.contents_section_count = len(listed)
    report.contents_chapter_count = len(contents.chapters)
    report.missing_sections = sorted(listed - extracted, key=section_sort_key)
    report.extra_sections = sorted(extracted - listed, key=section_sort_key)

    report.chapters_resolved = len({
        r["chapter"] for r in records if r["chapter_title"]
    })
    report.sections_without_chapter_title = [
        r["section"] for r in records if not r["chapter_title"]
    ]
    # Every chapter in the Contents must now have at least one record --
    # its own chapter-level record, if nothing else. A chapter with none
    # means its level-1 heading was never recognized in the body, and
    # everything it contains is missing from the output.
    seen_chapters = {r["chapter"] for r in records}
    report.chapters_without_sections = sorted(
        (c for c in contents.chapters if c not in seen_chapters), key=int
    )
    report.chapter_records = sorted(
        (r["section"] for r in records if r["level"] == 1), key=section_sort_key
    )
    report.chapters_without_own_record = sorted(
        (c for c in contents.chapters if c not in set(report.chapter_records)), key=int
    )

    for r in records:
        if not r["section_content"].strip():
            report.empty_sections += 1
        if r["chars"] > OVERSIZED_CHARS:
            report.oversized.append((r["section"], r["chars"]))
        semantic = r["semantic"]
        if r["semantic_type"] != "register_description":
            if r["section_content"].count("Address offset:") > 1:
                report.multi_register_sections.append(r["section"])
            continue
        report.register_description_count += 1
        fields = semantic.get("fields") or []
        report.field_count += len(fields)
        report.named_field_count += sum(1 for f in fields if f["name"] not in ("", "Res."))

        # Coverage is checked against the register's OWN width, which is
        # what ST states in its `Reset value:`. A hard-coded 32 is simply
        # the wrong invariant for the 42 RM0486 / 66 RM0490 registers ST
        # documents as 16-bit: each prints a single `15 14 ... 0` strip
        # with no `31 ... 16` row above it, and its fields partition
        # 15..0 exactly. The full-word result is still computed, and
        # reported as context, but it is not a failure on its own.
        register = semantic.get("register")
        full_word = check_bit_coverage(semantic)
        if not full_word:
            continue
        report.coverage_errors.append((r["section"], register, full_word))
        width = declared_width(semantic)
        own_width = check_bit_coverage(semantic, width)
        if width < REGISTER_BITS and not own_width:
            report.narrow_registers.append((
                r["section"], register, width,
                f"{width}-bit register (Reset value: "
                f"{semantic.get('reset_value') or '(absent)'}); "
                f"fields partition {width - 1}..0 exactly",
            ))
        else:
            report.real_coverage_errors.append((r["section"], register, own_width))

    report.oversized.sort(key=lambda x: -x[1])
    report.noise_summary = scanner.noise.summary_lines()
    report.uncaptioned_regions = scanner.uncaptioned_regions
    report.recovered_headings = list(scanner.recovered_headings)
    report.rejected_headings = list(scanner.rejected_headings)
    report.rejected_chapters = list(scanner.rejected_chapters)
    return report
