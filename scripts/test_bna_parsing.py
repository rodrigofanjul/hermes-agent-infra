#!/usr/bin/env python3
"""Fast, repeatable tests for bna_sync.py's HTML parsing functions, run
against fixture files (no live login needed)."""
import os

from bna_sync import discover_accounts_from_html, parse_bank_table, parse_account_balance

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES_DIR, name), encoding="utf-8") as f:
        return f.read()


def test_discover_accounts_from_fixture():
    accounts = discover_accounts_from_html(_read("bna_accounts_sample.html"))
    assert accounts == [
        {"index": "0", "name": "CUENTA SUELDO"},
        {"index": "1", "name": "CAJA DE AHORRO EN PESOS"},
    ]
    print(f"OK: discovered {len(accounts)} accounts from fixture")


def test_parse_account_movements_from_fixture():
    html = _read("bna_account_detail_sample.html")
    movements = parse_bank_table(html, ["date", "receipt", "description", "amount"])
    assert len(movements) == 2
    assert movements[0] == {
        "date": "05/09/2026", "receipt": "0",
        "description": "RENDIMIENTO DIARIO PESOS", "amount": "+ $ 10,00",
    }
    print(f"OK: parsed {len(movements)} account movements from fixture")


def test_parse_account_balance_from_fixture():
    balance = parse_account_balance(_read("bna_account_detail_sample.html"))
    assert balance == "100.000,00"
    print(f"OK: parsed balance {balance!r} from fixture")


if __name__ == "__main__":
    test_discover_accounts_from_fixture()
    test_parse_account_movements_from_fixture()
    test_parse_account_balance_from_fixture()
