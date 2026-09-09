#!/usr/bin/env python3
"""Fast, repeatable tests for bna_sync.py's HTML parsing functions, run
against fixture files (no live login needed)."""
import os

from bna_sync import (
    BNADataUnavailableError,
    classify_accounts_html,
    discover_accounts_from_html,
    parse_account_balance,
    parse_bank_table,
    validate_account_detail,
)

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


def test_classify_accounts_html_returns_real_accounts():
    accounts = classify_accounts_html(_read("bna_accounts_sample.html"))
    assert len(accounts) == 2


def test_classify_accounts_html_rejects_explicit_zero_accounts():
    html = "<html><body><h2>Mis Cuentas (0)</h2><p>Aun no tenés ninguna cuenta abierta con nosotros.</p></body></html>"
    try:
        classify_accounts_html(html)
    except BNADataUnavailableError as exc:
        assert "cero cuentas" in str(exc)
    else:
        raise AssertionError("an explicit zero-account response must fail closed")


def test_classify_accounts_html_rejects_unrecognized_page():
    try:
        classify_accounts_html("<html><body>Cargando...</body></html>")
    except BNADataUnavailableError as exc:
        assert "no terminó de cargar" in str(exc)
    else:
        raise AssertionError("an unrecognized page must fail closed")


def test_validate_account_detail_rejects_missing_balance():
    try:
        validate_account_detail([{"date": "01/01/2026"}], "")
    except BNADataUnavailableError as exc:
        assert "saldo" in str(exc)
    else:
        raise AssertionError("a missing balance must fail closed")


def test_validate_account_detail_rejects_empty_movements():
    try:
        validate_account_detail([], "1.000,00")
    except BNADataUnavailableError as exc:
        assert "movimientos" in str(exc)
    else:
        raise AssertionError("an unexpectedly empty movement table must fail closed")


if __name__ == "__main__":
    test_discover_accounts_from_fixture()
    test_parse_account_movements_from_fixture()
    test_parse_account_balance_from_fixture()
    test_classify_accounts_html_returns_real_accounts()
    test_classify_accounts_html_rejects_explicit_zero_accounts()
    test_classify_accounts_html_rejects_unrecognized_page()
    test_validate_account_detail_rejects_missing_balance()
    test_validate_account_detail_rejects_empty_movements()
