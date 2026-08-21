"""Multi-type sub-rows aggregate; multi-value cells are never truncated.

Regression cover for the two extraction bugs that wrote wrong values at
full provenance:

* a parameter split into typed sub-rows (GPIOs by I/O type, timers by
  counter width) was answered from the FIRST matching row only -- F334 wrote
  26 GPIOs where ST sums the type rows to 51, and G431 missed the low-power
  timer, 9 against ST's 10;
* ``_sole_number`` took the head before the slash, so a cell carrying
  ``2/16`` (converters/channels) filled a column with ``2`` -- a value the
  cell never states alone -- producing corrections that should not happen.

The expected values below were read off the printed datasheet tables and
cross-checked against ST's own workbooks (see SUM_SUBTYPES_FIX.md).
"""

from __future__ import annotations

import pathlib

import pytest

from stproducts.extract import (
    Document,
    Fragment,
    _gpio_options,
    _sole_number,
    _width_pairs,
    READERS,
)
from stproducts.fieldmap import spec_for
from stproducts.provenance import AMBIGUOUS, DATASHEET, DERIVED


def _doc(rows, column=2):
    """A one-fragment document whose part column is pinned."""
    fragment = Fragment(
        caption="Table 2. STM32xx features and peripheral counts",
        page=14,
        rows=[["Peripherals", "Peripherals", "STM32xxRx"], *rows],
        family_columns=[2],
        column=column,
    )
    return Document(
        part="STM32XXRX", path=pathlib.Path(__file__), fragments=[fragment]
    )


# --------------------------------------------------------------------------
# Cell parsing
# --------------------------------------------------------------------------


class TestWidthPairs:
    @pytest.mark.parametrize(
        "cell,expected",
        [
            ("2 (16-bit)", [(2, 16)]),
            ("2 (16 bits)", [(2, 16)]),
            ("1/(8-bit)", [(1, 8)]),
            ("5 (16-bit)\n1 (32-bit)", [(5, 16), (1, 32)]),
            ("2 (32 bits) and 8 (16 bits)", [(2, 32), (8, 16)]),
            # A footnote marker on the count must not swallow the pair.
            ("6 (16 bits)(1)", [(6, 16)]),
            ("SysTick = 2 (24 bits)", [(2, 24)]),
            ("Yes", []),
            ("", []),
            (None, []),
        ],
    )
    def test_shapes(self, cell, expected):
        assert _width_pairs(cell) == expected


class TestSoleNumber:
    def test_a_breakdown_keeps_its_leading_number(self):
        assert _sole_number("64 (48+16)") == "64"
        assert _sole_number("3/(2)(2)") == "3"

    def test_bare_alternatives_stay_unread(self):
        assert _sole_number("512 1024 2048") is None

    def test_slash_values_are_never_truncated(self):
        """``2/16`` says two things; neither of them is "2 alone"."""
        assert _sole_number("2/16") is None
        assert _sole_number("5/5/1") is None

    def test_a_single_number_survives(self):
        assert _sole_number("51") == "51"
        assert _sole_number("120 MHz") == "120"


class TestGpioOptions:
    def test_one_value(self):
        assert _gpio_options("26") == [26]

    def test_supply_alternatives(self):
        assert _gpio_options("80/ 78") == [80, 78]

    def test_na_marks_an_absent_option(self):
        assert _gpio_options("NA/ 78") == [None, 78]

    def test_non_numeric_is_unread(self):
        assert _gpio_options("yes") is None


# --------------------------------------------------------------------------
# Timers: sum every width-annotated row
# --------------------------------------------------------------------------


def _timer_doc():
    return _doc([
        ["Timers", "Advanced motor control", "2 (16-bit)"],
        ["Timers", "General purpose", "5 (16-bit)\n1 (32-bit)"],
        ["Timers", "Basic", "2 (16-bit)"],
        ["Timers", "Low power", "1 (16-bit)"],
        ["Timers", "SysTick timer", "1"],
        ["Timers", "Watchdog timers (independent, window)", "2"],
    ])


class TestTimerWidthSum:
    def test_the_low_power_timer_is_no_longer_missed(self):
        """G431: 2+5+2+1 = 10 x 16-bit. The first-row read said 9."""
        reading = READERS["derived_timer_width"](_timer_doc(), 16)
        assert reading.token == DERIVED
        assert reading.value == "10"
        assert "low power" in reading.conditions

    def test_the_32_bit_count_comes_from_the_same_rows(self):
        assert READERS["derived_timer_width"](_timer_doc(), 32).value == "1"

    def test_systick_and_watchdogs_stay_out_of_the_sum(self):
        doc = _doc([
            ["Timers", "SysTick timer", "2 (24 bits)"],
            ["Timers", "Watchdog timers", "2 (16 bits)"],
        ])
        assert READERS["derived_timer_width"](doc, 16) is None

    def test_no_32_bit_annotation_is_a_real_zero(self):
        doc = _doc([["Timers", "General purpose", "3 (16-bit)"]])
        reading = READERS["derived_timer_width"](doc, 32)
        assert reading.token == DERIVED
        assert reading.value == "0"

    def test_unannotated_tables_fall_back_to_the_inventory_join(self):
        """The F2 shape has class counts but no widths: nothing to sum."""
        doc = _doc([
            ["Timers", "General-purpose", "10"],
            ["Timers", "Advanced-control", "2"],
            ["Timers", "Basic", "2"],
        ])
        reading = READERS["derived_timer_width"](doc, 16)
        # No comparison table in this fabricated document -> the join has
        # nothing to work with and declines.
        assert reading is None or reading.token == AMBIGUOUS

    def test_partially_annotated_tables_assert_nothing(self):
        """F301 marks Advanced and General but leaves Basic bare: the
        annotated sum is known-incomplete, so it must not be written."""
        doc = _doc([
            ["Timers", "Advanced control", "1 (16-bit)"],
            ["Timers", "General purpose", "3 (16-bit)/1 (32 bit)"],
            ["Timers", "Basic", "1"],
        ])
        assert READERS["derived_timer_width"](doc, 16) is None

    def test_columns_disagreeing_leave_the_family_unread(self):
        fragment = Fragment(
            caption="Table 2. features and peripheral counts",
            page=14,
            rows=[
                ["Peripherals", "Peripherals", "STM32xxR8", "STM32xxM8"],
                ["Timers", "Low power", "1 (16-bit)", "2 (16-bit)"],
            ],
            family_columns=[2, 3],
            column=None,
        )
        doc = Document(part="X", path=pathlib.Path(__file__), fragments=[fragment])
        assert READERS["derived_timer_width"](doc, 16) is None


# --------------------------------------------------------------------------
# GPIOs: type rows sum, supply alternatives union
# --------------------------------------------------------------------------


class TestGpioTotal:
    def test_type_sub_rows_are_summed(self):
        """F334C8: Normal 20 + 5V-tolerant 17 = the 37 ST publishes."""
        doc = _doc([
            ["GPIOs", "Normal I/Os (TC, TTa)", "20"],
            ["GPIOs", "5-Volt tolerant I/Os (FT, FT1)", "17"],
        ])
        reading = READERS["summary_gpio_total"](doc)
        assert reading.token == DATASHEET
        assert reading.value == "37"

    def test_footnoted_cells_still_sum(self):
        doc = _doc([
            ["GPIOs", "GPIOs", "54(1)"],
        ])
        assert READERS["summary_gpio_total"](doc).value == "54"

    def test_supply_alternatives_union_instead_of_summing(self):
        """H563VI: LDO 80 / SMPS 78 -- ST publishes both, never 158."""
        doc = _doc([
            ["GPIOs", "Legacy", "80"],
            ["GPIOs", "SMPS", "78"],
        ])
        reading = READERS["summary_gpio_total"](doc)
        assert reading.value == "78, 80"

    def test_slash_cells_are_alternatives_too(self):
        doc = _doc([["GPIOs", "GPIOs (LDO / SMPS)", "80/ 78"]])
        assert READERS["summary_gpio_total"](doc).value == "78, 80"

    def test_wakeup_pins_and_vddio2_subsets_stay_out(self):
        """H5E4ZJ: ST publishes 110, 111, 112, 113 -- not +10, not +7."""
        doc = _doc([
            ["GPIOs", "GPIOs (LDO / SMPS)", "112/ 110"],
            ["GPIOs", "GPIOs (LDO / SMPS)", "113/ 111"],
            ["GPIOs", "GPIOs supplied by VDDIO2 (LDO / SMPS)", "10/9"],
            ["GPIOs", "Wake-up pins", "7"],
        ])
        assert READERS["summary_gpio_total"](doc).value == "110, 111, 112, 113"

    def test_a_single_plain_row_passes_through(self):
        doc = _doc([["GPIOs", "GPIOs", "51"]])
        assert READERS["summary_gpio_total"](doc).value == "51"

    def test_package_qualified_options_sum_positionally(self):
        """F301K8: Normal 9 (UFQFPN32) / 10 (LQFP32) beside a flat 15 gives
        24 or 25 -- exactly the ``24||25`` ST publishes."""
        doc = _doc([
            ["GPIOs", "Normal I/Os (TC, TTa)", "9 (UFQFPN32)/10 (LQFP32)"],
            ["GPIOs", "5-Volt tolerant I/Os (FT, FT1)", "15"],
        ])
        reading = READERS["summary_gpio_total"](doc)
        assert reading.value == "24, 25"


# --------------------------------------------------------------------------
# ADC / DAC: packed cells map positionally
# --------------------------------------------------------------------------


class TestAdcPair:
    def test_packed_cell_maps_positionally(self):
        """F105: "2\\n16" is two converters with sixteen channels."""
        doc = _doc([["12-bit ADC Number of channels", "12-bit ADC Number of channels", "2\n16"]])
        conv = READERS["summary_adc_pair"](doc, "12-bit", "converters")
        chan = READERS["summary_adc_pair"](doc, "12-bit", "channels")
        assert conv.value == "2"
        assert chan.value == "16"

    def test_slash_form_matches_too(self):
        doc = _doc([["12-bit ADC Number of channels", "", "2/16"]])
        assert READERS["summary_adc_pair"](doc, "12-bit", "converters").value == "2"

    def test_f2_stacked_rows_converters_then_channels(self):
        doc = _doc([
            ["12-bit ADC \nNumber of channels", "12-bit ADC \nNumber of channels", "3"],
            ["12-bit ADC \nNumber of channels", "12-bit ADC \nNumber of channels", "16"],
        ])
        assert READERS["summary_adc_pair"](doc, "12-bit", "converters").value == "3"
        assert READERS["summary_adc_pair"](doc, "12-bit", "channels").value == "16"

    def test_per_converter_channel_pairs_fill_nothing(self):
        """H7's "9/8" fast/slow cells answer per converter, not in total;
        the dedicated "Number of ADCs" row fills the converter column only."""
        doc = _doc([
            ["16-bit ADCs", "Number of ADCs", "2"],
            ["16-bit ADCs", "Number of direct channelsADC1/ADC2", "2/2"],
            ["16-bit ADCs", "Number of slow channelsADC1/ADC2", "9/8"],
        ])
        assert READERS["summary_adc_pair"](doc, "16-bit", "converters").value == "2"
        assert READERS["summary_adc_pair"](doc, "16-bit", "channels") is None

    def test_other_width_groups_do_not_leak(self):
        doc = _doc([["12-bit ADC Number of channels", "", "2\n16"]])
        assert READERS["summary_adc_pair"](doc, "16-bit", "converters") is None


class TestDac:
    def test_presence_plus_channels_takes_the_channel_count(self):
        doc = _doc([["12-bit DAC Number of channels", "", "Yes\n2"]])
        assert READERS["summary_dac"](doc).value == "2"

    def test_a_dedicated_channel_row_answers(self):
        doc = _doc([
            ["12-bit DAC", "Present in IC", "yes"],
            ["12-bit DAC", "Number of channels", "2"],
            ["12-bit DAC", "Comparators", "2"],
        ])
        assert READERS["summary_dac"](doc).value == "2"

    def test_a_bare_number_on_another_dac_row_is_not_this_fact(self):
        doc = _doc([["12-bit DAC", "Comparators", "2"]])
        assert READERS["summary_dac"](doc) is None

    def test_a_controller_count_row_wins_over_channels(self):
        """H523: controller = 1, channels = 2 -- ST publishes 1."""
        doc = _doc([
            ["DAC", "12-bit DAC controller", "1"],
            ["DAC", "Number of channels", "2"],
        ])
        assert READERS["summary_dac"](doc).value == "1"


# --------------------------------------------------------------------------
# USART/UART: only assert what the row actually separates
# --------------------------------------------------------------------------


class TestUsartUart:
    def test_a_row_that_splits_the_interfaces_maps_both(self):
        from stproducts.extract import _usart_uart
        doc = _doc([["Comm. interfaces", "USART \nUART", "4\n2"]])
        usart, uart, note = _usart_uart(doc)
        assert (usart, uart) == ("4", "2")
        assert note is None

    def test_a_lumped_number_asserts_nothing(self):
        """F105 reads USART = 5 where ST publishes 3 + 2."""
        from stproducts.extract import _usart_uart
        doc = _doc([["Comm. interfaces", "USART", "5"]])
        usart, uart, note = _usart_uart(doc)
        assert usart is None and uart is None
        assert "combined count" in note

    def test_a_lumped_number_with_a_separate_uart_row_is_usarts_alone(self):
        from stproducts.extract import _usart_uart
        doc = _doc([
            ["Comm. interfaces", "USART", "3"],
            ["Comm. interfaces", "UART", "2"],
        ])
        usart, uart, note = _usart_uart(doc)
        assert (usart, uart, note) == ("3", None, None)

    def test_triple_cells_assert_usart_only(self):
        """Whether ST counts the LPUART into UART typ varies by family."""
        from stproducts.extract import _usart_uart
        doc = _doc([["Communication interfaces", "USART/UART/LPUART", "5/5/1"]])
        usart, uart, note = _usart_uart(doc)
        assert usart == "5"
        assert uart is None


# --------------------------------------------------------------------------
# Field map wiring
# --------------------------------------------------------------------------


class TestFieldMap:
    def test_timer_columns_read_distinct_widths(self):
        for key, width in [
            ("Timers (8-bit) typ", 8),
            ("Timers (16-bit) typ", 16),
            ("Timers (32-bit) typ", 32),
        ]:
            spec = spec_for(key)
            assert spec.tier == DERIVED, key
            assert spec.reader == "derived_timer_width"
            assert spec.args == (width,), key

    def test_gpio_column_reads_the_aggregating_reader(self):
        spec = spec_for("I/Os (High Current)")
        assert spec.tier == DATASHEET
        assert spec.reader == "summary_gpio_total"

    def test_spi_column_reads_the_packed_pair_aware_reader(self):
        assert spec_for("SPI typ").reader == "summary_spi"
