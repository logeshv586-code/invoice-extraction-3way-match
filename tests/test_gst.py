from datetime import date

from extractor.gst import gstin_check_digit, indian_financial_year, normalize_invoice_number, valid_gst_rate, valid_gstin


def test_gstin_checksum_valid_and_invalid():
    assert gstin_check_digit("27AAACR5055K1Z") == "7"
    assert valid_gstin("27AAACR5055K1Z7")
    assert not valid_gstin("27AAACR5055K1Z8")


def test_gst_rate_slab_change():
    assert valid_gst_rate(12, date(2025, 9, 21))
    assert not valid_gst_rate(12, date(2025, 9, 22))
    assert not valid_gst_rate(40, date(2025, 9, 21))
    assert valid_gst_rate(40, date(2025, 9, 22))
    assert valid_gst_rate(0.25, date(2026, 1, 1))
    assert valid_gst_rate(3, date(2024, 1, 1))


def test_financial_year_boundary_and_invoice_normalization():
    assert indian_financial_year(date(2026, 3, 31)) == (2025, 2026)
    assert indian_financial_year(date(2026, 4, 1)) == (2026, 2027)
    assert normalize_invoice_number("inv / 042") == "INV/042"
    assert normalize_invoice_number(" INV/042 ") == "INV/042"
