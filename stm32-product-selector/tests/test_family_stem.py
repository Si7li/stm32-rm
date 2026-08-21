"""Family-stem resolution: single-digit families must find their own PDF.

``LocalIndex.find`` falls back to the filename family stem when neither the
exact name nor the cover index answers. ``STM32U3B5JI`` used to stem to
itself -- the ``\\d{2,3}`` regex cannot see past U3's single digit -- so the
fallback silently never fired and every U3B5/U3C5 part fell back to the API
even though ``stm32u3b5cg.pdf`` sat on disk, its device-summary table
carrying a column for each of them.
"""

from stproducts.datasheets import family_stem


def test_single_digit_families_bucket_with_their_own_pdf():
    assert family_stem("STM32U3B5JI") == "STM32U3B"
    assert family_stem("stm32u3b5cg") == "STM32U3B"
    # ...and NOT with the neighbouring U375/U385 datasheets:
    assert family_stem("STM32U375CE") == "STM32U375C"
    assert family_stem("STM32U385CG") == "STM32U385C"


def test_two_digit_families_keep_their_buckets():
    assert family_stem("STM32F205RB") == "STM32F205R"
    assert family_stem("STM32F207IG") == "STM32F207I"
    # different families, different buckets -- a candidates[0] fallback must
    # never hand an F207 part an F205 file (or vice versa).
    assert family_stem("STM32F205RB") != family_stem("STM32F207IG")


def test_three_digit_and_stm8_families_unchanged():
    assert family_stem("STM32H733VG") == "STM32H733V"
    assert family_stem("STM32L431CB") == "STM32L431C"
    assert family_stem("STM8AF5268") == "STM8AF52"
