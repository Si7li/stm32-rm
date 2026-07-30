import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from rmtables.cells import _char_text, cell_text, fix_symbols


def pua(byte: int) -> str:
    """The Symbol-font PUA codepoint pdfplumber emits for `byte`."""
    return chr(0xF000 + byte)


def test_fix_symbols_maps_bullet():
    assert fix_symbols(pua(0xB7) + " Item") == "• Item"


def test_fix_symbols_maps_lessequal():
    text = "2.4 V {0} V {0} V".format(pua(0xA3))
    assert fix_symbols(text) == "2.4 V ≤ V ≤ V"


def test_fix_symbols_maps_arrow():
    assert fix_symbols("A {0} B".format(pua(0xE0))) == "A → B"


def test_fix_symbols_maps_multiply_and_greaterequal():
    assert fix_symbols(pua(0xB4)) == "×"
    assert fix_symbols(pua(0xB3)) == "≥"


def test_fix_symbols_maps_space_verified_in_rm0008():
    # RM0008's ADC pins table renders "2.4V<=V<=3.6V" with the whole thing,
    # including the ordinary word-space, through SymbolMT.
    text = "2.4V{sp}{le}{sp}V{sp}{le}{sp}3.6V".format(sp=pua(0x20), le=pua(0xA3))
    assert fix_symbols(text) == "2.4V ≤ V ≤ 3.6V"


def test_fix_symbols_drops_unmapped_pua_codepoint():
    # A never-confirmed Symbol-font slot must be dropped, not left as
    # garbage and not silently corrupted into some guessed character.
    assert fix_symbols("a" + chr(0xF0FF) + "b") == "ab"


def test_fix_symbols_keeps_legitimate_unicode_untouched():
    # micro, registered, trademark, multiply, en dash, curly quotes, degree
    # are all real Unicode -- outside the U+F000..U+F0FF PUA range -- and
    # must pass through unchanged.
    text = "µA, Reg®, Trade™, 3×4, 10–20, “quoted”, 25°C"
    assert fix_symbols(text) == text


def test_fix_symbols_ascii_identical_low_block():
    # The Symbol font's low block (parens, greater-than) renders identically
    # to ASCII -- verified in RM0008 (0xF028/0xF029/0xF03E actually occur).
    assert fix_symbols(pua(0x28) + pua(0x29)) == "()"
    assert fix_symbols(pua(0x3E)) == ">"


def test_char_text_belt_and_suspenders_symbol_fontname():
    # A char whose own text isn't PUA-shifted but whose font is a Symbol
    # variant, and whose raw ordinal directly matches a Symbol-encoding
    # slot, still gets remapped.
    assert _char_text(chr(0xB7), "SymbolMT") == "•"
    assert _char_text(chr(0xB7), "Arial") == chr(0xB7)  # non-Symbol font: untouched


def test_cell_text_remaps_symbol_font_glyph_in_a_table_cell():
    chars = [
        {"text": pua(0xA3), "x0": 10, "x1": 16, "top": 100, "bottom": 110, "upright": True, "size": 9.0},
    ]
    assert cell_text(chars, (0, 90, 30, 120)) == "≤"
