"""Tests for the customer-side reference implementation."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import ingest_side  # noqa: E402


def test_pseudonym_is_stable_and_not_reversible():
    key = b"secret"
    a = ingest_side.pseudonymize("member-0001", key)
    assert a == ingest_side.pseudonymize("member-0001", key)
    assert a != ingest_side.pseudonymize("member-0002", key)
    assert "member" not in a and len(a) == 32
    assert a != ingest_side.pseudonymize("member-0001", b"other-key")


def test_classify_maps_to_codes_and_never_returns_text():
    assert ingest_side.classify("商品が壊れていました") == ("complaint", "")
    assert ingest_side.classify("返品したい") == ("request", "")
    assert ingest_side.classify("配送はいつですか") == ("question", "")


def test_other_gets_a_short_masked_note():
    cat, note = ingest_side.classify("山田様 090-1234-5678 の件で相談があります。折り返しください。")
    assert cat == "other"
    assert len(note) <= ingest_side.NOTE_MAX
    assert "090" not in note and "山田" not in note


def test_withdraw_row_shape():
    row = ingest_side.withdraw_row("m" * 32, "2026-11-22T12:00:00+09:00")
    assert row[1] == "" and row[2] == "withdraw" and len(row) == 5
