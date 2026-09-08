#!/usr/bin/env python3
"""Fast, repeatable tests for galicia_sync.py's HTML parsing functions,
run against fixture files (no live login needed)."""
import os

from galicia_sync import parse_movements

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def test_parse_movements_from_fixture():
    with open(os.path.join(FIXTURES_DIR, "cuentas_movimientos_sample.html"), encoding="utf-8") as f:
        html = f.read()
    movements = parse_movements(html)
    assert len(movements) > 0, "Expected at least one movement in the fixture"
    for m in movements:
        assert m["date"], "Every movement needs a non-empty date"
        assert m["description"], "Every movement needs a non-empty description"
        assert m["amount"], "Every movement needs a non-empty amount"
    print(f"OK: parsed {len(movements)} movements from fixture")


if __name__ == "__main__":
    test_parse_movements_from_fixture()
