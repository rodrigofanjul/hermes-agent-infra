#!/usr/bin/env python3
"""Fast, repeatable tests for bna_sync.py's HTML parsing functions, run
against fixture files (no live login needed)."""
import os
from unittest.mock import MagicMock, Mock, mock_open, patch

from bna_diagnose_accounts import sanitized_path
from bna_sync import (
    BNADataUnavailableError,
    classify_accounts_html,
    discover_cards_from_html,
    discover_loans_from_html,
    download_statement_pdf,
    discover_accounts_from_html,
    get_card_movements_html,
    get_loan_installments_html,
    get_account_detail_html,
    navigate_to_cards,
    navigate_to_loans,
    navigate_to_accounts,
    open_card_detail,
    list_card_statements,
    parse_account_balance,
    parse_bank_table,
    validate_account_detail,
    sync_card_movements_csv,
    sync_card_statements,
    sync_csv,
    sync_loan_installments_csv,
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
        validate_account_detail("<table><tbody><tr><td>movimiento</td></tr></tbody></table>", "")
    except BNADataUnavailableError as exc:
        assert "saldo" in str(exc)
    else:
        raise AssertionError("a missing balance must fail closed")


def test_validate_account_detail_allows_rendered_empty_movements_table():
    validate_account_detail("<table><tbody></tbody></table>", "1.000,00")


def test_validate_account_detail_rejects_missing_movements_table():
    try:
        validate_account_detail("<html><body>Cargando...</body></html>", "1.000,00")
    except BNADataUnavailableError as exc:
        assert "tabla de movimientos" in str(exc)
    else:
        raise AssertionError("a missing movement table must fail closed")


def test_navigate_to_accounts_uses_internal_link_and_semantic_wait():
    page = Mock()
    link = page.get_by_role.return_value

    navigate_to_accounts(page)

    page.get_by_role.assert_called_once_with("link", name="Cuentas", exact=True)
    link.click.assert_called_once_with()
    page.wait_for_function.assert_called_once()
    expression = page.wait_for_function.call_args.args[0]
    assert "account_card_number_" in expression
    assert "Mis Cuentas (0)" in expression


def test_get_account_detail_navigates_back_through_spa():
    page = Mock()
    page.content.return_value = "<html></html>"

    get_account_detail_html(page, {"index": "0"})

    page.goto.assert_not_called()
    page.get_by_role.assert_called_once_with("link", name="Cuentas", exact=True)
    page.locator.assert_called_once_with("#account_card_number_0")


def test_sanitized_path_removes_queries_and_long_identifiers():
    url = "https://digital.bna.com.ar/api/v1/accounts/abcdefghijklmnopqrstuvwxyz123456?token=secret"
    assert sanitized_path(url) == "/api/v1/accounts/<id>"


def test_discover_cards_from_fixture():
    cards = discover_cards_from_html(_read("bna_cards_sample.html"))
    assert cards == [
        {"index": "0", "kind": "credit", "last4": "1234"},
        {"index": "1", "kind": "debit", "last4": "5678"},
    ]


def test_parse_card_movements_from_fixture():
    movements = parse_bank_table(
        _read("bna_card_movements_sample.html"),
        ["description", "date", "amount"],
    )
    assert movements == [
        {"description": "SU PAGO", "date": "02/09/2026", "amount": "- $ 1.000,00"},
        {"description": "ALMACEN DON JOSE", "date": "15/08/2026", "amount": "$ 500,00"},
    ]


def test_navigate_to_cards_uses_internal_link_and_semantic_wait():
    page = Mock()

    navigate_to_cards(page)

    page.get_by_role.assert_called_once_with("link", name="Tarjetas", exact=True)
    page.get_by_role.return_value.click.assert_called_once_with()
    expression = page.wait_for_function.call_args.args[0]
    assert "card-" in expression


def test_open_card_detail_never_hard_navigates():
    page = Mock()

    open_card_detail(page, {"index": "0"})

    page.goto.assert_not_called()
    page.locator.assert_called_once_with("#card-0")


def test_get_card_movements_html_returns_rendered_page():
    page = Mock()
    page.content.return_value = "<html><table><tbody></tbody></table></html>"
    with patch("bna_sync.open_card_detail") as mocked_open:
        html = get_card_movements_html(page, {"index": "0"})

    mocked_open.assert_called_once_with(page, {"index": "0"})
    page.get_by_text.assert_called_once_with("Movimientos", exact=True)
    assert html == page.content.return_value


def test_get_card_movements_html_rejects_missing_table_after_tab_opens():
    page = Mock()
    page.wait_for_selector.side_effect = RuntimeError("table timeout")
    with patch("bna_sync.open_card_detail"):
        try:
            get_card_movements_html(page, {"index": "0"})
        except BNADataUnavailableError as exc:
            assert "tabla de movimientos" in str(exc)
        else:
            raise AssertionError("an opened movements tab without a table must fail")


def test_sync_card_movements_csv_uses_last_four_digits():
    movements = [{"description": "COMPRA", "date": "01/09/2026", "amount": "$ 1,00"}]
    with patch("bna_sync.sync_csv") as mocked_sync:
        sync_card_movements_csv("1234", movements, "folder-id")

    mocked_sync.assert_called_once_with(
        "tarjeta_1234.csv",
        ["description", "date", "amount"],
        movements,
        "folder-id",
    )


def test_list_card_statements_ignores_unrelated_lists():
    labels = list_card_statements(_read("bna_card_statements_sample.html"))
    assert labels == ["Agosto 2026", "Julio 2026"]


def test_download_statement_pdf_saves_successful_download():
    page = MagicMock()
    download_info = MagicMock()
    page.expect_download.return_value.__enter__.return_value = download_info

    result = download_statement_pdf(
        page,
        {"index": "0", "last4": "1234"},
        1,
        "/tmp/statement.pdf",
        attempts=1,
    )

    assert result is True
    page.get_by_role.assert_called_once_with("button", name="Descargar", exact=True)
    page.get_by_role.return_value.nth.assert_called_once_with(1)
    download_info.value.save_as.assert_called_once_with("/tmp/statement.pdf")


def test_download_statement_pdf_retries_are_bounded():
    page = Mock()
    page.expect_download.side_effect = RuntimeError("download failed")
    card = {"index": "0", "last4": "1234"}

    with patch("bna_sync.open_card_detail") as mocked_open:
        result = download_statement_pdf(page, card, 0, "/tmp/statement.pdf", attempts=3)

    assert result is False
    assert mocked_open.call_count == 2


def test_sync_card_statements_accepts_card_without_summaries_tab():
    page = Mock()
    page.get_by_text.return_value.click.side_effect = RuntimeError("tab absent")
    with patch("bna_sync.open_card_detail"):
        result = sync_card_statements(page, {"index": "1", "last4": "5678"}, "folder-id")

    assert result == []


def test_sync_card_statements_rejects_loaded_tab_without_statement_rows():
    page = Mock()
    page.content.return_value = "<html><body>Resúmenes</body></html>"
    with patch("bna_sync.open_card_detail"):
        try:
            sync_card_statements(page, {"index": "0", "last4": "1234"}, "folder-id")
        except BNADataUnavailableError as exc:
            assert "lista de resúmenes" in str(exc)
        else:
            raise AssertionError("a loaded summaries tab without rows must fail")


def test_sync_card_statements_skips_files_already_in_drive():
    page = Mock()
    page.content.return_value = _read("bna_card_statements_sample.html")
    card = {"index": "0", "last4": "1234"}
    with (
        patch("bna_sync.open_card_detail"),
        patch("bna_sync.drive_find_file", return_value="existing-id") as mocked_find,
        patch("bna_sync.download_statement_pdf") as mocked_download,
    ):
        result = sync_card_statements(page, card, "folder-id")

    assert result == []
    assert [call.args[0] for call in mocked_find.call_args_list] == [
        "1234_agosto_2026.pdf",
        "1234_julio_2026.pdf",
    ]
    mocked_download.assert_not_called()


def test_sync_card_statements_uploads_new_pdf():
    page = Mock()
    page.content.return_value = "<ul><li><p>Agosto 2026</p><button>Descargar</button></li></ul>"
    card = {"index": "0", "last4": "1234"}
    with (
        patch("bna_sync.open_card_detail"),
        patch("bna_sync.drive_find_file", return_value=None),
        patch("bna_sync.download_statement_pdf", return_value=True),
        patch("bna_sync.subprocess.run") as mocked_run,
        patch("bna_sync.os.path.exists", return_value=True),
        patch("bna_sync.os.remove") as mocked_remove,
    ):
        result = sync_card_statements(page, card, "folder-id")

    assert result == []
    assert mocked_run.call_args.args[0][-4:] == [
        "--name", "1234_agosto_2026.pdf", "--parent", "folder-id"
    ]
    mocked_remove.assert_called_once_with("/tmp/1234_agosto_2026.pdf")


def test_sync_card_statements_reports_failed_month():
    page = Mock()
    page.content.return_value = "<ul><li><p>Agosto 2026</p><button>Descargar</button></li></ul>"
    card = {"index": "0", "last4": "1234"}
    with (
        patch("bna_sync.open_card_detail"),
        patch("bna_sync.drive_find_file", return_value=None),
        patch("bna_sync.download_statement_pdf", return_value=False),
        patch("bna_sync.subprocess.run") as mocked_run,
    ):
        result = sync_card_statements(page, card, "folder-id")

    assert result == ["Agosto 2026"]
    mocked_run.assert_not_called()


def test_discover_loans_from_real_shaped_links():
    html = (
        '<a href="/loans/abc123">Préstamo - 0014682194</a>'
        '<a href="/loans/list">Préstamos</a>'
    )
    loans = discover_loans_from_html(html)
    assert loans == [{"url": "/loans/abc123", "number": "0014682194"}]


def test_parse_loan_installments_from_fixture():
    installments = parse_bank_table(
        _read("bna_loan_installments_sample.html"),
        ["installment", "due_date", "status", "amount"],
    )
    assert installments == [
        {
            "installment": "1",
            "due_date": "10/02/2022",
            "status": "Paga",
            "amount": "$ 50.000,00",
        },
        {
            "installment": "56",
            "due_date": "10/09/2026",
            "status": "A vencer",
            "amount": "$ 630.000,00",
        },
    ]


def test_navigate_to_loans_uses_internal_link():
    page = Mock()

    navigate_to_loans(page)

    page.get_by_role.assert_called_once_with("link", name="Préstamos", exact=True)
    page.get_by_role.return_value.click.assert_called_once_with()
    page.goto.assert_not_called()


def test_get_loan_installments_uses_spa_link_and_all_filter():
    page = Mock()
    page.content.return_value = "<html><table><tbody></tbody></table></html>"
    loan = {"url": "/loans/abc123", "number": "0014682194"}
    with patch("bna_sync.navigate_to_loans") as mocked_navigate:
        html = get_loan_installments_html(page, loan)

    mocked_navigate.assert_called_once_with(page)
    page.locator.assert_called_once_with('a[href="/loans/abc123"]')
    page.get_by_role.assert_called_once_with("radio", name="Todas las cuotas")
    page.goto.assert_not_called()
    assert html == page.content.return_value


def test_sync_loan_installments_csv_uses_loan_number():
    installments = [
        {"installment": "1", "due_date": "10/02/2022", "status": "Paga", "amount": "$ 1,00"}
    ]
    with patch("bna_sync.sync_csv") as mocked_sync:
        sync_loan_installments_csv("0014682194", installments, "folder-id")

    mocked_sync.assert_called_once_with(
        "prestamo_0014682194.csv",
        ["installment", "due_date", "status", "amount"],
        installments,
        "folder-id",
    )


def test_sync_csv_uploads_replacement_before_deleting_existing_file():
    commands = []

    def record_command(command, **kwargs):
        commands.append(command)
        result = Mock()
        result.stdout = "[]"
        return result

    with (
        patch("bna_sync.drive_find_file", return_value="old-file-id"),
        patch("bna_sync.subprocess.run", side_effect=record_command),
        patch("builtins.open", mock_open(read_data="date,balance\n")),
        patch("bna_sync.os.remove"),
    ):
        sync_csv(
            "saldo_cuenta.csv",
            ["date", "balance"],
            [{"date": "2026-09-09", "balance": "1.000,00"}],
            "folder-id",
        )

    operations = [command[3] for command in commands]
    assert operations == ["download", "upload", "delete"]


if __name__ == "__main__":
    test_discover_accounts_from_fixture()
    test_parse_account_movements_from_fixture()
    test_parse_account_balance_from_fixture()
    test_classify_accounts_html_returns_real_accounts()
    test_classify_accounts_html_rejects_explicit_zero_accounts()
    test_classify_accounts_html_rejects_unrecognized_page()
    test_validate_account_detail_rejects_missing_balance()
    test_validate_account_detail_allows_rendered_empty_movements_table()
    test_validate_account_detail_rejects_missing_movements_table()
    test_navigate_to_accounts_uses_internal_link_and_semantic_wait()
    test_get_account_detail_navigates_back_through_spa()
    test_sanitized_path_removes_queries_and_long_identifiers()
    test_discover_cards_from_fixture()
    test_parse_card_movements_from_fixture()
    test_navigate_to_cards_uses_internal_link_and_semantic_wait()
    test_open_card_detail_never_hard_navigates()
    test_get_card_movements_html_returns_rendered_page()
    test_get_card_movements_html_rejects_missing_table_after_tab_opens()
    test_sync_card_movements_csv_uses_last_four_digits()
    test_list_card_statements_ignores_unrelated_lists()
    test_download_statement_pdf_saves_successful_download()
    test_download_statement_pdf_retries_are_bounded()
    test_sync_card_statements_accepts_card_without_summaries_tab()
    test_sync_card_statements_rejects_loaded_tab_without_statement_rows()
    test_sync_card_statements_skips_files_already_in_drive()
    test_sync_card_statements_uploads_new_pdf()
    test_sync_card_statements_reports_failed_month()
    test_discover_loans_from_real_shaped_links()
    test_parse_loan_installments_from_fixture()
    test_navigate_to_loans_uses_internal_link()
    test_get_loan_installments_uses_spa_link_and_all_filter()
    test_sync_loan_installments_csv_uses_loan_number()
    test_sync_csv_uploads_replacement_before_deleting_existing_file()
