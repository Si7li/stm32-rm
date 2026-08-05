"""Section boundaries, chapter recognition, and in-page assembly."""

from __future__ import annotations

from rmcontent.sections import Section, SectionScanner

PAGE_HEIGHT = 842.0
LINE_STEP = 20.0
BODY_SIZE = 10.0


class FakePage:
    """A page whose lines are laid out top-to-bottom, with no tables.

    `gaps` optionally gives the extra vertical space before a line, keyed
    by its index -- needed wherever `rmtables.notes.notes_below` is in
    play, since it distinguishes a wrapped continuation from a paragraph
    break by the gap relative to the previous line's height.

    `sizes` optionally gives a line's font size in points, keyed by its
    index; everything else is body-sized. Figure artwork is separated
    from prose by font size, so a line's `chars` carry a real `size`.
    """

    def __init__(self, texts, height=PAGE_HEIGHT, start_top=100.0, gaps=None,
                 sizes=None, body_size=BODY_SIZE, rotated=()):
        self._texts = texts
        self.height = height
        self._start_top = start_top
        self._gaps = gaps or {}
        self._sizes = sizes or {}
        self._body_size = body_size
        self._rotated = set(rotated)

    def extract_text_lines(self, **kwargs):
        lines = []
        top = self._start_top
        for i, text in enumerate(self._texts):
            top += self._gaps.get(i, 0)
            size = self._sizes.get(i, self._body_size)
            lines.append({
                "text": text,
                "top": top,
                "bottom": top + 10,
                "x0": 67.0,
                "x1": 528.0,
                "chars": [{"size": size, "upright": i not in self._rotated}
                          for _ in text or " "],
            })
            top += LINE_STEP
        return lines

    def find_tables(self, table_settings=None):
        return []

    @property
    def chars(self):
        return []

    def flush_cache(self):
        pass


def scan(pages, document="RM0490", chapters=None, section_titles=None,
         body_font_size=BODY_SIZE):
    scanner = SectionScanner(document, chapters or {}, section_titles or {},
                             body_font_size=body_font_size)
    for i, page in enumerate(pages, start=1):
        scanner.scan_page(page, i)
    return scanner


def by_number(sections):
    return {s.number: s for s in sections}


def test_section_runs_to_the_next_heading_of_any_level():
    """A parent keeps only its own preamble and never repeats a child."""
    page = FakePage([
        "4.7 FLASH registers",
        "The registers are described below.",
        "4.7.1 FLASH access control register (FLASH_ACR)",
        "Address offset: 0x000",
        "4.7.2 FLASH key register (FLASH_KEYR)",
        "Address offset: 0x008",
    ])
    sections = by_number(scan([page]).finalize())
    assert sections["4.7"].content == "The registers are described below."
    assert sections["4.7.1"].content == "Address offset: 0x000"
    assert sections["4.7.2"].content == "Address offset: 0x008"


def test_section_levels_and_parents():
    page = FakePage([
        "4.7 FLASH registers",
        "4.7.1 FLASH access control register (FLASH_ACR)",
    ])
    sections = by_number(scan([page]).finalize())
    assert (sections["4.7"].level, sections["4.7"].parent) == (2, None)
    assert (sections["4.7.1"].level, sections["4.7.1"].parent) == (3, "4.7")
    assert sections["4.7.1"].chapter == "4"


def test_section_spans_pages_and_records_page_end():
    pages = [
        FakePage(["4.7.1 FLASH access control register (FLASH_ACR)", "Address offset: 0x000"]),
        FakePage(["Bit 8 PRFTEN: CPU Prefetch enable"]),
        FakePage(["4.7.2 FLASH key register (FLASH_KEYR)"]),
    ]
    sections = by_number(scan(pages).finalize())
    assert sections["4.7.1"].page == 1
    assert sections["4.7.1"].page_end == 2
    assert sections["4.7.2"].page == 3


def test_chapter_heading_closes_the_previous_section_and_opens_its_own():
    """HEADING_RE cannot see a level-1 heading, so the Contents supplies
    the authority for recognizing one."""
    pages = [
        FakePage(["3.1.4 Boot pin", "Some boot prose."]),
        FakePage([
            "4 Embedded flash memory (FLASH)",
            "This chapter describes the flash memory.",
            "4.1 FLASH main features",
        ]),
    ]
    scanner = scan(pages, chapters={"3": "Boot modes", "4": "Embedded flash memory (FLASH)"})
    sections = by_number(scanner.finalize())
    assert sections["3.1.4"].content == "Some boot prose."
    assert sections["4"].content == "This chapter describes the flash memory."
    assert sections["4.1"].content == ""


def test_a_chapter_record_is_level_1_with_no_parent():
    page = FakePage(["5 OTP mapping (OTP)", "The OTP area is described below."])
    section = by_number(scan([page], chapters={"5": "OTP mapping (OTP)"}).finalize())["5"]
    assert (section.level, section.parent, section.chapter) == (1, None, "5")


def test_a_chapter_with_no_subsections_keeps_its_whole_body():
    """RM0486 chapter 5 and RM0490 chapter 21 have no numbered
    subsections, so before chapter records existed their entire content
    was silently dropped."""
    pages = [
        FakePage([
            "5 OTP mapping (OTP)",
            "The OTP area holds 2048 bits.",
            "Each word is programmable once.",
        ]),
        FakePage(["6 Power control (PWR)", "6.1 Introduction"]),
    ]
    scanner = scan(pages, chapters={"5": "OTP mapping (OTP)", "6": "Power control (PWR)"})
    sections = by_number(scanner.finalize())
    assert sections["5"].content == (
        "The OTP area holds 2048 bits.\nEach word is programmable once."
    )
    assert sections["6"].content == ""


def test_a_chapter_with_subsections_keeps_only_its_preamble():
    """Exactly the rule levels 2 and 3 already follow -- no duplication."""
    page = FakePage([
        "4 Embedded flash memory (FLASH)",
        "Preamble only.",
        "4.1 FLASH main features",
        "The features are listed below.",
    ])
    sections = by_number(
        scan([page], chapters={"4": "Embedded flash memory (FLASH)"}).finalize()
    )
    assert sections["4"].content == "Preamble only."
    assert "The features are listed below." not in sections["4"].content
    assert sections["4.1"].content == "The features are listed below."


def test_a_chapter_title_that_wrapped_still_matches():
    """ST wraps a long chapter title, so only its first line reaches the
    check; titles are compared on a 30-character prefix."""
    page = FakePage(["21 Chrom-ART Accelerator controller", "Body."])
    sections = by_number(scan(
        [page], chapters={"21": "Chrom-ART Accelerator controller (DMA2D)"},
    ).finalize())
    assert sections["21"].content == "Body."


def test_a_body_line_reusing_a_seen_chapter_number_is_rejected():
    """"5 Some numbered item" after chapter 5 has passed is not a
    chapter -- a false chapter record would truncate the section it
    interrupted."""
    page = FakePage([
        "5 OTP mapping (OTP)",
        "5.1 OTP introduction",
        "5 OTP mapping (OTP) is described above.",
        "More prose.",
    ])
    scanner = scan([page], chapters={"5": "OTP mapping (OTP)"})
    sections = by_number(scanner.finalize())
    assert len(scanner.rejected_chapters) == 1
    assert sections["5.1"].content == (
        "5 OTP mapping (OTP) is described above.\nMore prose."
    )


def test_a_chapter_number_out_of_order_is_rejected():
    pages = [
        FakePage(["40 Some later chapter", "Body."]),
        FakePage(["1 Documentation conventions", "Not a chapter here."]),
    ]
    scanner = scan(pages, chapters={
        "1": "Documentation conventions", "40": "Some later chapter",
    })
    sections = by_number(scanner.finalize())
    assert "1" not in sections
    assert scanner.rejected_chapters == ["1"]
    # A rejected candidate is body text, not a deletion.
    assert sections["40"].content == (
        "Body.\n1 Documentation conventions\nNot a chapter here."
    )


def test_a_chapter_number_with_the_wrong_title_is_not_a_chapter():
    page = FakePage(["5 Some numbered item in a list", "More prose."])
    scanner = scan([page], chapters={"5": "OTP mapping (OTP)"})
    assert scanner.finalize() == []


def test_a_short_fragment_does_not_match_a_chapter_title():
    """"5 OTP" shares three characters with "OTP mapping (OTP)"; the
    minimum comparison length rejects it."""
    page = FakePage(["4.1 Features", "5 OTP", "More prose."])
    scanner = scan([page], chapters={"4": "Flash", "5": "OTP mapping (OTP)"})
    sections = by_number(scanner.finalize())
    assert "5" not in sections
    assert sections["4.1"].content == "5 OTP\nMore prose."


def test_a_digit_led_body_line_is_not_mistaken_for_a_chapter():
    """The number alone is not enough: the title must match the Contents
    too, or a line like "16 Bit timer mode" would orphan the section."""
    page = FakePage([
        "4.1 FLASH main features",
        "16 Bit timer mode is not supported here.",
        "More prose.",
    ])
    scanner = scan([page], chapters={
        "4": "Embedded flash memory (FLASH)", "16": "Advanced-control timer (TIM1)",
    })
    sections = by_number(scanner.finalize())
    assert sections["4.1"].content == (
        "16 Bit timer mode is not supported here.\nMore prose."
    )


def test_prose_beyond_the_last_chapter_is_not_a_heading():
    """RM0522: "61.44 MHz from the clock controller of the circuit. In
    the example above we" satisfies HEADING_RE completely, in a manual
    whose last chapter is 52."""
    page = FakePage([
        "43.5.7 I2S full-duplex mode",
        "61.44 MHz from the clock controller of the circuit. In the example above we",
        "assume the codec is already programmed.",
    ])
    scanner = scan([page], chapters={str(i): f"Chapter {i}" for i in range(1, 53)})
    sections = by_number(scanner.finalize())
    assert "61.44" not in sections
    assert scanner.rejected_headings == ["61.44"]
    assert "61.44 MHz from the clock" in sections["43.5.7"].content


def test_a_chapter_the_contents_parser_missed_does_not_delete_its_sections():
    """The bound is an upper limit, not exact membership: an exact test
    would silently drop every section of a chapter the Contents parse
    happened to lose."""
    page = FakePage(["7.1 CRS introduction", "Prose."])
    scanner = scan([page], chapters={"1": "A", "52": "B"})  # no chapter 7
    assert by_number(scanner.finalize())["7.1"].content == "Prose."
    assert scanner.rejected_headings == []


def test_no_contents_means_no_chapter_bound():
    page = FakePage(["61.44 Something", "Prose."])
    scanner = scan([page], chapters={})
    assert "61.44" in by_number(scanner.finalize())


def test_heading_rejected_by_rmtables_is_recovered_from_the_contents():
    """RM0490 17.3.19's title starts with a digit, which
    `parse_heading`'s uppercase-initial guard refuses."""
    page = FakePage(["17.3.19 6-step PWM generation", "Prose about PWM."])
    scanner = scan(
        [page],
        chapters={"17": "Advanced-control timer (TIM1)"},
        section_titles={"17.3.19": "6-step PWM generation"},
    )
    sections = by_number(scanner.finalize())
    assert sections["17.3.19"].title == "6-step PWM generation"
    assert sections["17.3.19"].content == "Prose about PWM."
    assert scanner.recovered_headings == ["17.3.19"]


def test_recovery_requires_the_title_to_match_the_contents_too():
    page = FakePage(["17.3.19 6-step PWM generation", "Prose."])
    scanner = scan(
        [page],
        chapters={"17": "Advanced-control timer (TIM1)"},
        section_titles={"17.3.19": "Something else entirely"},
    )
    assert scanner.finalize() == []


def test_multi_parenthetical_heading_continuation_is_folded_in():
    """RM0490 18.4.11 wraps as "(TIMx_CCER)(x = 2 to 3)", which
    BARE_PAREN_RE alone rejects."""
    page = FakePage([
        "18.4.11 TIMx capture/compare enable register",
        "(TIMx_CCER)(x = 2 to 3)",
        "Address offset: 0x20",
    ])
    section = by_number(scan([page]).finalize())["18.4.11"]
    assert section.title == "TIMx capture/compare enable register (TIMx_CCER)(x = 2 to 3)"
    assert section.content == "Address offset: 0x20"


def test_wrapped_heading_paren_is_folded_into_the_title_same_page():
    page = FakePage([
        "6.4.17 RCC AHB peripheral clock enable in Sleep/Stop mode register",
        "(RCC_AHBSMENR)",
        "Address offset: 0x048",
    ])
    sections = by_number(scan([page]).finalize())
    section = sections["6.4.17"]
    assert section.title.endswith("(RCC_AHBSMENR)")
    assert section.content == "Address offset: 0x048"


def test_wrapped_heading_paren_is_folded_across_a_page_break():
    pages = [
        FakePage(["6.4.17 RCC AHB peripheral clock enable in Sleep/Stop mode register"]),
        FakePage(["(RCC_AHBSMENR)", "Address offset: 0x048"]),
    ]
    sections = by_number(scan(pages).finalize())
    section = sections["6.4.17"]
    assert section.title.endswith("(RCC_AHBSMENR)")
    assert section.content == "Address offset: 0x048"


def test_noise_is_filtered_and_counted():
    page = FakePage([
        "RM0490 Embedded flash memory (FLASH)",
        "4.7.1 FLASH access control register (FLASH_ACR)",
        "Address offset: 0x000",
        "s",
        "31 30 29 28 27 26 25 24 23 22 21 20 19 18 17 16",
        "Bit 18 DBG_SWEN: Debug access software enable",
    ], start_top=59.76)
    scanner = scan([page])
    section = by_number(scanner.finalize())["4.7.1"]
    assert section.content == (
        "Address offset: 0x000\nBit 18 DBG_SWEN: Debug access software enable"
    )
    assert scanner.noise.headers_footers == 1
    assert scanner.noise.stray_glyphs == 1
    assert scanner.noise.bit_layout_rows == 1


def test_contents_pages_are_skipped_wholesale():
    page = FakePage([
        "Contents RM0490",
        "4.7.1 FLASH access control register (FLASH_ACR) . . . . . . 77",
        "4.7.2 FLASH key register (FLASH_KEYR) . . . . . . 78",
    ])
    scanner = scan([page])
    assert scanner.finalize() == []
    assert scanner.noise.contents_pages == 1
    assert scanner.noise.contents_page_lines == 3


def test_front_matter_before_the_first_heading_is_dropped():
    page = FakePage(["Some cover prose.", "4.1 FLASH main features", "Body."])
    sections = by_number(scan([page]).finalize())
    assert list(sections) == ["4.1"]
    assert sections["4.1"].content == "Body."


def test_empty_section_is_still_emitted():
    """31 RM0490 sections have no prose of their own; completeness is
    provable only if they are emitted anyway."""
    page = FakePage(["4.7 FLASH registers", "4.7.1 FLASH access control register (FLASH_ACR)"])
    sections = by_number(scan([page]).finalize())
    assert sections["4.7"].content == ""
    assert len(sections) == 2


def test_section_dataclass_derives_level_chapter_parent():
    s = Section(number="12.6.1", title="T", page=1, page_end=1)
    assert (s.level, s.chapter, s.parent) == (3, "12", "12.6")


def test_heading_split_across_two_lines_with_the_number_second():
    """RM0486 page 1456 renders "31.4.1 DLYB diagram" as two pdfplumber
    lines, "DLYB" then "31.4.1 diagram" -- the mirror image of the
    "(REGNAME)"-on-the-next-line wrap."""
    page = FakePage([
        "31.4 DLYB functional description",
        "DLYB",
        "31.4.1 diagram",
        "The delay block includes the following sub-blocks.",
    ])
    scanner = scan(
        [page],
        chapters={"31": "Delay block (DLYB)"},
        section_titles={"31.4": "DLYB functional description", "31.4.1": "DLYB diagram"},
    )
    sections = by_number(scanner.finalize())
    assert sections["31.4.1"].title == "DLYB diagram"
    assert sections["31.4.1"].content == "The delay block includes the following sub-blocks."
    assert sections["31.4"].content == ""
    assert scanner.recovered_headings == ["31.4.1"]


def test_split_heading_requires_an_exact_contents_match():
    """Without it, any two adjacent lines could be merged into a heading."""
    page = FakePage(["Some prose ending here", "31.4.1 diagram", "More."])
    scanner = scan(
        [page],
        chapters={"31": "Delay block (DLYB)"},
        section_titles={"31.4.1": "DLYB diagram"},
    )
    assert scanner.finalize() == []


class TabledPage(FakePage):
    """A page with one ruled region, so caption/footnote suppression and
    bbox exclusion can be exercised end to end."""

    def __init__(self, texts, bbox, **kw):
        super().__init__(texts, **kw)
        self._bbox = bbox

    def find_tables(self, table_settings=None):
        page = self

        class _Row:
            def __init__(self, bbox):
                self.bbox = bbox
                self.cells = [(bbox[0], bbox[1], bbox[2], bbox[3])]

        class _Table:
            bbox = page._bbox
            rows = [_Row(page._bbox)]

        return [_Table()]


def test_caption_line_is_dropped_and_the_marker_kept():
    """The caption is printed above the grid, outside the bbox."""
    page = TabledPage(
        [
            "10.3.1 Internal SRAMs",
            "Table 33. Internal SRAM features",
            "row text inside the grid",
        ],
        bbox=(67.0, 135.0, 528.0, 155.0),
    )
    section = by_number(scan([page]).finalize())["10.3.1"]
    assert section.content == "[Table 33. Internal SRAM features]"


def test_a_prose_cross_reference_survives_next_to_its_caption():
    """RM0486 10.3.1: "Table33 summarizes..." differs from the caption
    only by position and a missing space."""
    page = TabledPage(
        [
            "10.3.1 Internal SRAMs",
            "Table33 summarizes the features supported by each internal SRAM.",
            "Table 33. Internal SRAM features",
            "row text inside the grid",
        ],
        bbox=(67.0, 155.0, 528.0, 175.0),
    )
    section = by_number(scan([page]).finalize())["10.3.1"]
    assert section.content == (
        "Table33 summarizes the features supported by each internal SRAM.\n"
        "[Table 33. Internal SRAM features]"
    )


def test_table_footnote_after_a_marker_is_dropped():
    page = TabledPage(
        [
            "13.4 RAMCFG interrupts",
            "Table 37. RAMCFG interrupt requests",
            "row text inside the grid",
            "1. All these bits are in RAMCFG_BKPSRAMISR.",
        ],
        bbox=(67.0, 135.0, 528.0, 155.0),
    )
    section = by_number(scan([page]).finalize())["13.4"]
    assert section.content == "[Table 37. RAMCFG interrupt requests]"


def test_a_genuine_numbered_list_in_prose_survives():
    """Nothing is matched by shape: a section's own numbered list is not
    below a table bbox, so `notes_below` never reports it."""
    page = FakePage([
        "6.2.1 Configuration procedure",
        "1. Enable the peripheral clock.",
        "2. Configure the prescaler.",
        "3. Set the enable bit.",
    ])
    section = by_number(scan([page]).finalize())["6.2.1"]
    assert section.content == (
        "1. Enable the peripheral clock.\n"
        "2. Configure the prescaler.\n"
        "3. Set the enable bit."
    )


def test_footer_and_bit_row_do_not_reach_the_body():
    page = FakePage([
        "16.9 Data truncation",
        "[Figure 53. 20-bit to 16-bit result truncation]",
        "19 15 11 7 3 0",
        "160/4669",
        "RM0486 Rev 4",
        "Raw 20-bit data",
    ])
    scanner = SectionScanner("RM0486", {}, {}, page_count=4669)
    scanner.scan_page(page, 1)
    section = by_number(scanner.finalize())["16.9"]
    assert section.content == (
        "[Figure 53. 20-bit to 16-bit result truncation]\nRaw 20-bit data"
    )
    assert scanner.noise.bit_layout_rows == 1
    assert scanner.noise.headers_footers == 2


def test_table_footnotes_continue_onto_the_next_page():
    """RM0490 Table 104: the grid fills page 660, so ST prints its notes
    at the top of page 661 where no region exists."""
    pages = [
        TabledPage(
            ["24.5 RTC interrupts", "Table 104. Interrupt requests", "grid row"],
            bbox=(67.0, 135.0, 528.0, 155.0),
        ),
        FakePage(
            [
                "1. The event flags are in the RTC_SR register.",
                "2. The interrupt masked flags are in RTC_MISR.",
                "The RTC provides the following interrupts.",
            ],
            gaps={2: 40.0},  # a paragraph break, not a wrapped note
        ),
    ]
    section = by_number(scan(pages).finalize())["24.5"]
    assert section.content == (
        "[Table 104. Interrupt requests]\n"
        "The RTC provides the following interrupts."
    )


def test_a_page_opening_with_prose_continues_nothing():
    """`notes_below` is the only decision-maker: it returns nothing
    unless the very first line of the page is a numbered note."""
    pages = [
        TabledPage(
            ["24.5 RTC interrupts", "Table 104. Interrupt requests", "grid row"],
            bbox=(67.0, 135.0, 528.0, 155.0),
        ),
        FakePage([
            "The RTC provides the following interrupts.",
            "1. This is a genuine numbered list in prose.",
            "2. It must survive.",
        ]),
    ]
    section = by_number(scan(pages).finalize())["24.5"]
    assert section.content == (
        "[Table 104. Interrupt requests]\n"
        "The RTC provides the following interrupts.\n"
        "1. This is a genuine numbered list in prose.\n"
        "2. It must survive."
    )


def test_continuation_is_not_armed_when_content_follows_the_table():
    """Prose after the grid means the table is finished, so a numbered
    list opening the next page belongs to the prose."""
    pages = [
        TabledPage(
            [
                "24.5 RTC interrupts",
                "Table 104. Interrupt requests",
                "grid row",
                "Configure the RTC as follows.",
            ],
            bbox=(67.0, 135.0, 528.0, 155.0),
        ),
        FakePage(["1. Enable the peripheral clock.", "2. Configure the prescaler."]),
    ]
    section = by_number(scan(pages).finalize())["24.5"]
    assert section.content.endswith(
        "Configure the RTC as follows.\n"
        "1. Enable the peripheral clock.\n"
        "2. Configure the prescaler."
    )


def test_a_wrapped_chapter_title_is_taken_whole_from_the_contents():
    """RM0486 chapter 7's heading wraps; before this the remainder line
    opened the chapter's body instead of completing its title."""
    page = FakePage([
        "7 Resource isolation slave unit for address space",
        "protection (full version) (RISAF)",
        "The RISAF protects a slave address space.",
    ])
    sections = by_number(scan([page], chapters={
        "7": "Resource isolation slave unit for address space protection (full version) (RISAF)",
    }).finalize())
    assert sections["7"].title == (
        "Resource isolation slave unit for address space protection "
        "(full version) (RISAF)"
    )
    assert sections["7"].content == "The RISAF protects a slave address space."


def test_a_superscript_lifted_out_of_a_chapter_title_still_matches():
    """pdfplumber lifts the (R) onto a line of its own, leaving a gap:
    RM0486 chapter 75 reads "USB Type-C /USB Power Delivery..." in the
    body against "USB Type-C(R)/USB Power Delivery..." in the Contents."""
    page = FakePage([
        "75 USB Type-C /USB Power Delivery interface (UCPD)",
        "75.1 UCPD introduction",
    ])
    sections = by_number(scan([page], chapters={
        "75": "USB Type-C®/USB Power Delivery interface (UCPD)",
    }).finalize())
    assert sections["75"].title == "USB Type-C®/USB Power Delivery interface (UCPD)"


def test_an_ordinary_first_line_is_not_swallowed_as_a_title_remainder():
    page = FakePage([
        "5 OTP mapping (OTP)",
        "The OTP area holds 2048 bits.",
    ])
    sections = by_number(scan([page], chapters={"5": "OTP mapping (OTP)"}).finalize())
    assert sections["5"].content == "The OTP area holds 2048 bits."


# -- figure artwork (FIGURE_ARTWORK_FIX) ------------------------------------


def test_figure_artwork_is_dropped_and_prose_kept():
    """RM0486 2.1.2's figure pages are 2-3 pt labels around a 10 pt
    caption. There is no bbox to exclude: page 159 reports zero grids,
    0 images, 3 curves, 4 rects, yet 1,401 chars of artwork."""
    page = FakePage(
        [
            "2.1.2 Bus architecture",
            "The bus architecture is divided in two domains.",
            "Figure 1. Interconnect top view - STM32N6x7 devices",
            "CK_ICN_M_NPU",
            "elabaehcaC UPN sHSALF ot ciffart",
            "AHB2AXI",
            "The GPDMA1_P has a dedicated access to APB1/2/4 peripherals.",
        ],
        sizes={3: 2.0, 4: 2.0, 5: 3.0},
    )
    scanner = scan([page])
    section = by_number(scanner.finalize())["2.1.2"]
    assert section.content == (
        "The bus architecture is divided in two domains.\n"
        "[Figure 1. Interconnect top view - STM32N6x7 devices]\n"
        "The GPDMA1_P has a dedicated access to APB1/2/4 peripherals."
    )
    assert scanner.noise.figure_artwork == 3


def test_a_body_sized_caption_still_emits_its_marker():
    """Captions are body-sized, so the filter must not reach them."""
    page = FakePage([
        "11.4.1 DMA block diagram",
        "Figure 21. DMA block diagram",
        "DMA_CH0",
    ], sizes={2: 3.0})
    section = by_number(scan([page]).finalize())["11.4.1"]
    assert section.content == "[Figure 21. DMA block diagram]"


def test_an_eight_point_figure_footnote_is_kept():
    """A deliberate choice: it is readable prose explaining the figure."""
    page = FakePage([
        "2.1.2 Bus architecture",
        "Figure 1. Interconnect top view",
        "CK_ICN_M_NPU",
        "1. The high-performance domain is shown in pink.",
    ], sizes={2: 2.0, 3: 8.0})
    section = by_number(scan([page]).finalize())["2.1.2"]
    assert section.content == (
        "[Figure 1. Interconnect top view]\n"
        "1. The high-performance domain is shown in pink."
    )


def test_a_standalone_asset_id_is_dropped_even_at_body_size():
    page = FakePage([
        "2.1.2 Bus architecture",
        "Prose stays.",
        "MS70497V3",
        "MSv40234",
        "MSc12345b2",
    ])
    scanner = scan([page])
    assert by_number(scanner.finalize())["2.1.2"].content == "Prose stays."
    assert scanner.noise.figure_artwork == 3


def test_an_asset_id_inside_a_sentence_is_not_dropped():
    page = FakePage(["2.1.2 Bus architecture", "The drawing MS70497V3 is referenced here."])
    section = by_number(scan([page]).finalize())["2.1.2"]
    assert section.content == "The drawing MS70497V3 is referenced here."


def test_the_threshold_is_derived_not_hardcoded():
    """A manual set at 20 pt must drop its 11 pt artwork (11 < 12) while
    a 10 pt manual keeps the very same line."""
    texts = ["2.1.2 Bus architecture", "Body prose.", "ARTWORK_LABEL"]
    big = FakePage(texts, sizes={1: 20.0, 2: 11.0}, body_size=20.0)
    assert by_number(scan([big], body_font_size=20.0).finalize())["2.1.2"].content == (
        "Body prose."
    )
    small = FakePage(texts, sizes={1: 10.0, 2: 11.0})
    assert by_number(scan([small]).finalize())["2.1.2"].content == (
        "Body prose.\nARTWORK_LABEL"
    )


def test_the_six_point_ambiguous_band_is_kept():
    """0.6 x 10 = 6.0 and the test is strict, so 6 pt stays -- the
    conservative direction, since that band holds real content."""
    page = FakePage(["2.1.2 Bus architecture", "Ambiguous six point line."], sizes={1: 6.0})
    assert by_number(scan([page]).finalize())["2.1.2"].content == "Ambiguous six point line."


def test_a_line_with_no_measurable_size_is_kept():
    """Absence of evidence is not evidence of a diagram label."""
    page = FakePage(["2.1.2 Bus architecture", "Prose."])
    lines = page.extract_text_lines()
    for line in lines:
        line["chars"] = []
    page.extract_text_lines = lambda **kw: lines
    assert by_number(scan([page]).finalize())["2.1.2"].content == "Prose."


def test_a_section_with_no_figures_is_byte_identical():
    """The filter must be inert where there is no artwork."""
    texts = [
        "4.7.1 FLASH access control register (FLASH_ACR)",
        "Address offset: 0x000",
        "Reset value: 0x0000 0000",
        "Bits 31:1 Reserved, must be kept at reset value.",
        "Bit 0 CEN: Counter enable",
    ]
    with_filter = by_number(scan([FakePage(texts)]).finalize())["4.7.1"]
    # A threshold of 0 disables the size rule entirely.
    without = by_number(scan([FakePage(texts)], body_font_size=0.0).finalize())["4.7.1"]
    assert with_filter.content == without.content
    assert with_filter.content == "\n".join(texts[1:])


# -- one marker per logical table (MULTIPAGE_TABLE_MARKER_FIX) ---------------


class MultiGridPage(TabledPage):
    """A page carrying several ruled regions."""

    def __init__(self, texts, bboxes, **kw):
        FakePage.__init__(self, texts, **kw)
        self._bboxes = bboxes

    def find_tables(self, table_settings=None):
        page = self

        class _Row:
            def __init__(self, bbox):
                self.bbox = bbox
                self.cells = [bbox]

        class _Table:
            def __init__(self, bbox):
                self.bbox = bbox
                self.rows = [_Row(bbox)]

        return [_Table(b) for b in page._bboxes]


def test_a_table_continued_onto_the_next_page_emits_one_marker():
    """RM0486 4.3.2: p202 and p203 both carry a grid for Table 9, and the
    prose cross-reference above them must survive untouched."""
    pages = [
        TabledPage(
            [
                "4.3.2 BSEC internal signals",
                "Table9 describes the user relevant internal signals interfacing the "
                "BSEC peripheral.",
                "Table 9. BSEC internal input/output signals",
                "grid row",
            ],
            bbox=(67.0, 155.0, 528.0, 175.0),
        ),
        TabledPage(
            ["Table 9. BSEC internal input/output signals (continued)", "grid row"],
            bbox=(67.0, 115.0, 528.0, 135.0),
        ),
    ]
    section = by_number(scan(pages).finalize())["4.3.2"]
    assert section.content == (
        "Table9 describes the user relevant internal signals interfacing the "
        "BSEC peripheral.\n"
        "[Table 9. BSEC internal input/output signals]"
    )


def test_a_three_page_table_emits_one_marker():
    pages = [
        TabledPage(
            ["4.3.2 BSEC internal signals", "Table 9. BSEC signals", "grid row"],
            bbox=(67.0, 135.0, 528.0, 155.0),
        ),
        TabledPage(["Table 9. BSEC signals (continued)", "grid row"],
                   bbox=(67.0, 115.0, 528.0, 135.0)),
        TabledPage(["Table 9. BSEC signals (continued)", "grid row"],
                   bbox=(67.0, 115.0, 528.0, 135.0)),
    ]
    section = by_number(scan(pages).finalize())["4.3.2"]
    assert section.content == "[Table 9. BSEC signals]"


def test_two_grids_of_one_table_on_the_same_page_emit_one_marker():
    page = MultiGridPage(
        [
            "4.3.2 BSEC internal signals",
            "Table 9. BSEC signals",
            "first half",
            "second half",
        ],
        bboxes=[(67.0, 135.0, 528.0, 145.0), (67.0, 155.0, 528.0, 165.0)],
    )
    section = by_number(scan([page]).finalize())["4.3.2"]
    assert section.content == "[Table 9. BSEC signals]"


def test_two_different_tables_on_one_page_emit_two_markers():
    page = MultiGridPage(
        [
            "4.3.2 BSEC internal signals",
            "Table 9. BSEC signals",
            "grid row",
            "Table 10. BSEC other signals",
            "grid row",
        ],
        bboxes=[(67.0, 135.0, 528.0, 145.0), (67.0, 175.0, 528.0, 185.0)],
    )
    section = by_number(scan([page]).finalize())["4.3.2"]
    assert section.content == (
        "[Table 9. BSEC signals]\n[Table 10. BSEC other signals]"
    )


def test_the_same_table_referenced_twice_in_prose_keeps_both_lines():
    """Prose cross-references are never considered for deduplication."""
    page = FakePage([
        "4.3.2 BSEC internal signals",
        "Table9 describes the user relevant internal signals.",
        "Some intervening prose.",
        "As already shown in Table9, the signals are grouped.",
    ])
    section = by_number(scan([page]).finalize())["4.3.2"]
    assert section.content == (
        "Table9 describes the user relevant internal signals.\n"
        "Some intervening prose.\n"
        "As already shown in Table9, the signals are grouped."
    )


def test_a_table_reappearing_far_later_emits_again():
    pages = [
        TabledPage(["4.3.2 BSEC internal signals", "Table 9. BSEC signals", "grid row"],
                   bbox=(67.0, 135.0, 528.0, 155.0)),
    ] + [FakePage(["filler prose."]) for _ in range(4)] + [
        TabledPage(["Table 9. BSEC signals", "grid row"], bbox=(67.0, 115.0, 528.0, 135.0)),
    ]
    section = by_number(scan(pages).finalize())["4.3.2"]
    assert section.content.count("[Table 9. BSEC signals]") == 2


def test_a_continuation_in_the_next_section_is_still_marked():
    """Resetting per section means a table is never hidden from a section
    that contains it."""
    pages = [
        TabledPage(["4.3.2 BSEC internal signals", "Table 9. BSEC signals", "grid row"],
                   bbox=(67.0, 135.0, 528.0, 155.0)),
        TabledPage(["4.3.3 BSEC registers", "Table 9. BSEC signals (continued)", "grid row"],
                   bbox=(67.0, 135.0, 528.0, 155.0)),
    ]
    sections = by_number(scan(pages).finalize())
    assert sections["4.3.2"].content == "[Table 9. BSEC signals]"
    assert sections["4.3.3"].content == "[Table 9. BSEC signals]"


# -- artwork bands, bounded by ST's asset ID (FIGURE_ASSET_ID_FIX) -----------

LISTED = {n: f"Filler figure {n}" for n in range(100, 130)}


def banded(pages, figures=None, **kw):
    listed = {**LISTED, **(figures or {})}
    scanner = SectionScanner("RM0490", {}, {}, listed_figures=listed, **kw)
    for i, page in enumerate(pages, start=1):
        scanner.scan_page(page, i)
    scanner.finalize()
    return scanner


def test_a_band_drops_caption_artwork_and_the_asset_id():
    """Caption opens, asset ID closes inclusive, everything between goes;
    the body after the ID is kept."""
    page = FakePage([
        "2.1 System architecture",
        "The main system consists of:",
        "Figure 1. System architecture",
        "GPIO Ports Flash memory",
        "Cortex®-M0+ System bus Bus matrix",
        "DMA1/DMAMUX",
        "MSv66119V2",
        "System bus (S-bus)",
    ], sizes={3: 8.0, 4: 8.0, 5: 6.5, 6: 8.0})
    scanner = banded([page], figures={1: "System architecture"})
    section = by_number(scanner.finalize())["2.1"]
    assert section.content == (
        "The main system consists of:\n"
        "[Figure 1. System architecture]\n"
        "System bus (S-bus)"
    )
    assert (scanner.band.opened, scanner.band.closed) == (1, 1)


def test_artwork_at_eight_point_is_dropped_by_the_band_not_by_size():
    """RM0490 p43 sets artwork at 8.0 and 6.5 pt against a 9.96 pt body,
    so no threshold can reach it without destroying 9.0 pt register
    prose. The band has no size dependency."""
    page = FakePage([
        "2.1 System architecture",
        "Figure 1. System architecture",
        "IOPORT",
        "MSv66119V2",
        "Bits 15:0 BSy: Port x set I/O y",
    ], sizes={2: 8.0, 3: 8.0, 4: 9.0})
    section = by_number(banded([page], figures={1: "System architecture"})
                        .finalize())["2.1"]
    assert section.content == (
        "[Figure 1. System architecture]\nBits 15:0 BSy: Port x set I/O y"
    )


def test_a_band_with_no_asset_id_drops_nothing():
    """Fail-safe by construction: uncertainty costs a leak, never a
    deletion."""
    pages = [
        FakePage([
            "2.1 System architecture",
            "Figure 1. System architecture",
            "GPIO Ports Flash memory",
            "Real prose that must survive.",
        ], sizes={2: 8.0}),
        FakePage(["More real prose."]),
        FakePage(["Yet more prose."]),
        FakePage(["Past the hard bound."]),
    ]
    scanner = banded(pages, figures={1: "System architecture"})
    section = by_number(scanner.finalize())["2.1"]
    assert "GPIO Ports Flash memory" in section.content
    assert "Real prose that must survive." in section.content
    assert "Past the hard bound." in section.content
    assert scanner.band.opened == 1
    assert scanner.band.closed == 0
    assert [c for c, _ in scanner.band.abandoned] == ["[Figure 1. System architecture]"]


def test_a_band_may_not_cross_a_section_boundary():
    pages = [
        FakePage(["2.1 System architecture", "Figure 1. System architecture", "artwork"],
                 sizes={2: 8.0}),
        FakePage(["2.2 Memory organization", "Register prose here.", "MSv66119V2"]),
    ]
    scanner = banded(pages, figures={1: "System architecture"})
    sections = by_number(scanner.finalize())
    assert sections["2.1"].content.endswith("artwork")
    assert sections["2.2"].content == "Register prose here."
    assert [c for c, _ in scanner.band.abandoned] == ["[Figure 1. System architecture]"]


def test_a_band_may_not_run_more_than_two_pages():
    pages = [
        FakePage(["2.1 System architecture", "Figure 1. System architecture", "artwork"],
                 sizes={2: 8.0}),
        FakePage(["page two prose"]),
        FakePage(["page three prose"]),
        FakePage(["page four prose", "MSv66119V2"]),
    ]
    scanner = banded(pages, figures={1: "System architecture"})
    section = by_number(scanner.finalize())["2.1"]
    assert "artwork" in section.content
    assert "page four prose" in section.content
    assert scanner.band.closed == 0


def test_a_cross_reference_produces_no_marker_and_no_band():
    """RM0486 12.4.3: the sentence stays prose, and nothing is deleted."""
    page = FakePage([
        "12.4.3 CACHEAXI memories",
        "Figure 14. shows the functional view of TAG and data memories.",
        "Real prose that must survive.",
        "Figure 14. CACHEAXI TAG and data memories functional view",
        "artwork label",
        "MSv45319V2",
    ], sizes={4: 8.0})
    scanner = banded([page],
                     figures={14: "CACHEAXI TAG and data memories functional view"})
    section = by_number(scanner.finalize())["12.4.3"]
    assert section.content == (
        "Figure 14. shows the functional view of TAG and data memories.\n"
        "Real prose that must survive.\n"
        "[Figure 14. CACHEAXI TAG and data memories functional view]"
    )
    assert scanner.band.opened == 1
    assert len(scanner.figures.rejected) == 1


def test_two_figures_on_one_page_pair_in_order():
    page = FakePage([
        "2.1 System architecture",
        "Figure 1. System architecture",
        "first artwork",
        "MSv66119V2",
        "Between the figures.",
        "Figure 2. Memory map",
        "second artwork",
        "MSv66120V1",
        "After both.",
    ], sizes={2: 8.0, 6: 8.0})
    scanner = banded([page], figures={1: "System architecture", 2: "Memory map"})
    section = by_number(scanner.finalize())["2.1"]
    assert section.content == (
        "[Figure 1. System architecture]\n"
        "Between the figures.\n"
        "[Figure 2. Memory map]\n"
        "After both."
    )
    assert (scanner.band.opened, scanner.band.closed) == (2, 2)


def test_an_asset_id_split_by_kerning_still_closes_the_band():
    page = FakePage([
        "2.1 System architecture",
        "Figure 1. System architecture",
        "artwork label",
        "MS v 66119 V2",
        "After the figure.",
    ], sizes={2: 8.0})
    scanner = banded([page], figures={1: "System architecture"})
    section = by_number(scanner.finalize())["2.1"]
    assert section.content == (
        "[Figure 1. System architecture]\nAfter the figure."
    )
    assert scanner.band.closed == 1


def test_a_section_with_no_figure_is_byte_identical():
    texts = [
        "4.7.1 FLASH access control register (FLASH_ACR)",
        "Address offset: 0x000",
        "Bits 31:1 Reserved, must be kept at reset value.",
        "Bit 0 CEN: Counter enable",
        "0: Debugger disabled",
    ]
    with_band = by_number(banded([FakePage(texts)]).finalize())["4.7.1"]
    assert with_band.content == "\n".join(texts[1:])


def test_an_unlisted_figure_number_keeps_its_marker_without_a_band():
    page = FakePage([
        "2.1 System architecture",
        "Figure 999. Some unlisted figure",
        "would-be artwork",
        "MSv66119V2",
    ], sizes={2: 8.0})
    scanner = banded([page])
    section = by_number(scanner.finalize())["2.1"]
    assert "[Figure 999. Some unlisted figure]" in section.content
    assert scanner.band.opened == 0
    # The size backstop still reaches the 8 pt label only if it is small
    # enough; the standalone id line is dropped by the id backstop.
    assert "MSv66119V2" not in section.content


def test_a_rotated_running_head_does_not_reach_the_prose():
    """RM0490 2.1 carried standalone 'Memory', 'and', 'bus',
    'architecture', 'RM0490' off a landscape figure page."""
    page = FakePage(
        [
            "2.1 System architecture",
            "The main system consists of:",
            "Memory",
            "and bus",
            "architecture",
            "RM0490",
            "System bus (S-bus)",
        ],
        # Body-sized, so the artwork size floor cannot reach them and the
        # rotation flag is the only thing that distinguishes them.
        rotated={2, 3, 4, 5},
    )
    scanner = scan([page])
    section = by_number(scanner.finalize())["2.1"]
    assert section.content == (
        "The main system consists of:\nSystem bus (S-bus)"
    )
    assert scanner.noise.rotated_lines == 4


def test_an_upright_caption_on_a_landscape_page_still_emits_its_marker():
    page = FakePage(
        [
            "2.1 System architecture",
            "Figure 1. System architecture",
            "Memory",
            "MSv66119V2",
        ],
        rotated={2},
    )
    scanner = scan([page])
    section = by_number(scanner.finalize())["2.1"]
    assert section.content == "[Figure 1. System architecture]"
    assert scanner.noise.rotated_lines == 1


def test_a_section_with_no_rotated_text_is_untouched():
    texts = [
        "4.7.1 FLASH access control register (FLASH_ACR)",
        "Address offset: 0x000",
        "Bits 31:1 Reserved, must be kept at reset value.",
        "Bit 0 CEN: Counter enable",
    ]
    scanner = scan([FakePage(texts)])
    assert by_number(scanner.finalize())["4.7.1"].content == "\n".join(texts[1:])
    assert scanner.noise.rotated_lines == 0
