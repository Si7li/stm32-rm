import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.metadata import (
    CORE_RE,
    FAMILY_RE,
    FREQUENCY_RE,
    NAME_RE,
    REV_RE,
    _derive_core,
    _derive_references,
    _normalize_device_token,
    _validate_frequency,
    _validate_rev,
)


def test_normalize_device_token_forces_stm32_and_family_letter_uppercase():
    assert _normalize_device_token("stm32f101xx") == "STM32F101xx"
    assert _normalize_device_token("STM32F101xx") == "STM32F101xx"
    assert _normalize_device_token("stm32c0") == "STM32C0"


def test_derive_references_collects_all_devices_from_cover_title():
    # Verified RM0008 artifact: the previous derivation took only the FIRST
    # STM32... match instead of all five devices this manual applies to.
    cover_title = "RM0008\nReference manual\nSTM32F101xx, STM32F102xx, STM32F103xx, STM32F105xx and\nSTM32F107xx advanced Arm-based 32-bit MCUs"
    filename = "rm0008-stm32f101xx-stm32f102xx-stm32f103xx-stm32f105xx-and-stm32f107xx-advanced-armbased-32bit-mcus-stmicroelectronics.pdf"
    references = _derive_references(cover_title, filename)
    assert references == "STM32F101xx, STM32F102xx, STM32F103xx, STM32F105xx, STM32F107xx"


def test_derive_references_dedupes_cover_and_filename_tokens():
    # The filename slug repeats every device the cover title already named;
    # each must appear exactly once in the result, in first-seen order.
    cover_title = "RM0008\nSTM32F101xx, STM32F102xx"
    filename = "rm0008-stm32f101xx-stm32f102xx-stmicroelectronics.pdf"
    assert _derive_references(cover_title, filename) == "STM32F101xx, STM32F102xx"


def test_derive_references_series_only_manual_keeps_series_string():
    cover_title = "RM0490\nReference manual\nSTM32C0 series advanced Arm-based 32-bit MCUs"
    filename = "rm0490-stm32c0-series-advanced-armbased-32bit-mcus-stmicroelectronics.pdf"
    assert _derive_references(cover_title, filename) == "STM32C0"


def test_derive_references_finds_device_only_in_filename():
    cover_title = "RM9999\nReference manual"
    filename = "rm9999-stm32z9xx-stmicroelectronics.pdf"
    assert _derive_references(cover_title, filename) == "STM32Z9xx"


# --------------------------------------------------------------- CORE_RE
# CORE_REGEX_FIX.md: the old `M(\d\+?)` used a single-digit class, so every
# two-digit Arm core was truncated (Cortex-M55 -> "M5", Cortex-M33 -> "M3").

def _core_match(text):
    m = CORE_RE.search(text)
    assert m, f"CORE_RE did not match {text!r}"
    return m.group(1) + m.group(2)


def test_core_re_matches_two_digit_cores_without_truncation():
    assert _core_match("the Arm® Cortex®-M55 core") == "55"
    assert _core_match("the Arm® Cortex®-M33 core") == "33"
    assert _core_match("STM32 Cortex-M85 MCUs") == "85"


def test_core_re_still_matches_single_digit_and_m0_plus():
    assert _core_match("STM32F10xxx Cortex®-M3") == "3"
    assert _core_match("Cortex-M7 core") == "7"
    assert _core_match("Cortex®-M0+ core") == "0+"


def test_core_re_tolerates_stray_whitespace_and_registered_mark():
    assert _core_match("Cortex ® - M 55") == "55"
    assert _core_match("Cortex™-M33") == "33"
    assert _core_match("Cortex-M 4") == "4"


def test_derive_core_prefers_first_match_and_builds_expected_string():
    text = "the Arm® Cortex®-M55 core, ref\n... STM32 Cortex®-M55 MCUs prog"
    assert _derive_core(text) == "Arm 32-bit Cortex-M55 CPU"


def test_derive_core_keeps_m0_plus_suffix():
    text = "the Arm® Cortex®-M0+ core"
    assert _derive_core(text) == "Arm 32-bit Cortex-M0+ CPU"


def test_derive_core_returns_empty_when_no_match():
    assert _derive_core("no Arm core mentioned here") == ""


def test_derive_core_prefers_first_match_and_logs_all_distinct_cores(caplog):
    # An H7-class manual mentioning both an M7 and an M4 core must not
    # silently collapse to one -- the first match wins the value, but every
    # distinct number found is logged at DEBUG.
    text = "dual-core Cortex-M7 and Cortex-M4 device"
    with caplog.at_level(logging.DEBUG, logger="rmtables.metadata"):
        core = _derive_core(text)
    assert core == "Arm 32-bit Cortex-M7 CPU"
    assert "7" in caplog.text and "4" in caplog.text


def test_derive_core_warns_on_unrecognized_core_number(caplog):
    with caplog.at_level(logging.WARNING, logger="rmtables.metadata"):
        core = _derive_core("Cortex-M99 core")
    assert core == "Arm 32-bit Cortex-M99 CPU"
    assert "unrecognized" in caplog.text.lower()


def test_derive_core_warns_when_maximal_digit_run_is_violated(caplog):
    # A hypothetical 4-digit run: the 1-3 digit cap stops at "550", but a
    # "0" sits immediately after it in the source text -- that's the
    # truncation signature the bug class produces, so it must WARN.
    with caplog.at_level(logging.WARNING, logger="rmtables.metadata"):
        _derive_core("Cortex-M5500 core")
    assert "may be truncated" in caplog.text.lower()


def test_maximal_digit_run_holds_for_every_real_core_example():
    # The captured core number must not be immediately followed by another
    # digit in the source text (CORE_REGEX_FIX.md validation requirement).
    examples = [
        "the Arm® Cortex®-M55 core, ref",
        "the Arm® Cortex®-M33 core, ref",
        "STM32 Cortex-M85 MCUs prog",
        "STM32F10xxx Cortex®-M3 programming",
        "the Arm® Cortex®-M0+ core, ref",
    ]
    for text in examples:
        m = CORE_RE.search(text)
        end = m.end(1)
        assert not (end < len(text) and text[end].isdigit()), text


# -------------------------------------------------------------- NAME_RE

def test_name_re_matches_four_digit_rm_numbers():
    assert NAME_RE.search("RM0486\nReference manual").group(1) == "RM0486"
    assert NAME_RE.search("RM0522\nReference manual").group(1) == "RM0522"


def test_name_re_does_not_truncate_a_future_five_digit_rm_number():
    assert NAME_RE.search("RM12345\nReference manual").group(1) == "RM12345"


# ------------------------------------------------------------ FAMILY_RE

def test_family_re_handles_plain_two_char_families():
    assert FAMILY_RE.search("STM32C0 series advanced").group(1) == "C0"
    assert FAMILY_RE.search("STM32F101xx").group(1) == "F1"
    assert FAMILY_RE.search("STM32U5 series").group(1) == "U5"
    assert FAMILY_RE.search("STM32N6x5x7xx").group(1) == "N6"


def test_family_re_captures_trailing_letter_for_sub_line_variants():
    # STM32H7Rx/7Sx: the plain STM32([A-Z]\d) form would give "H7" for both
    # the R and S sub-lines, losing the distinction between them.
    assert FAMILY_RE.search("STM32H7Rx/7Sx Arm-based 32-bit MCUs").group(1) == "H7R"


# --------------------------------------------------------------- REV_RE

def test_rev_re_matches_multi_digit_revision():
    # RM0008 must give "Rev 21", not "Rev 2" -- \d+ is already
    # variable-length, but this pins the behavior down explicitly.
    m = REV_RE.search("Reference manual\nRev 21\n")
    assert m.group(1) == "21"


# --------------------------------------------------------- FREQUENCY_RE

def test_frequency_re_matches_three_digit_mhz_value():
    m = FREQUENCY_RE.search("CPU frequency up to 600 MHz")
    assert m.group(1) == "up to 600 MHz"


def test_frequency_re_matches_two_digit_mhz_value():
    m = FREQUENCY_RE.search("CPU frequency up to 72 MHz")
    assert m.group(1) == "up to 72 MHz"


# ----------------------------------------------------- sanity validation

def test_validate_rev_warns_on_empty(caplog):
    with caplog.at_level(logging.WARNING, logger="rmtables.metadata"):
        _validate_rev("")
    assert "could not be derived" in caplog.text.lower()


def test_validate_rev_warns_on_more_than_three_digits(caplog):
    with caplog.at_level(logging.WARNING, logger="rmtables.metadata"):
        _validate_rev("Rev 12345")
    assert "more than 3 digits" in caplog.text.lower()


def test_validate_rev_silent_for_normal_values(caplog):
    with caplog.at_level(logging.WARNING, logger="rmtables.metadata"):
        _validate_rev("Rev 21")
    assert caplog.text == ""


def test_validate_frequency_warns_when_out_of_range(caplog):
    with caplog.at_level(logging.WARNING, logger="rmtables.metadata"):
        _validate_frequency("up to 5000 MHz")
    assert "outside the expected" in caplog.text.lower()


def test_validate_frequency_silent_for_plausible_values(caplog):
    with caplog.at_level(logging.WARNING, logger="rmtables.metadata"):
        _validate_frequency("up to 600 MHz")
    assert caplog.text == ""
