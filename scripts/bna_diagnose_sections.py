#!/usr/bin/env python3
"""One-shot structural diagnostic for BNA cards, statements, and loans."""
import sys

from playwright.sync_api import sync_playwright

from bna_diagnose_accounts import sanitized_path
from bna_sync import (
    discover_cards_from_html,
    discover_loans_from_html,
    list_card_statements,
    login,
    logout,
)


def settle(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(1500)


def click_section(page, name: str) -> None:
    page.get_by_role("link", name=name, exact=True).click()
    settle(page)


def has_known_empty_state(page, section: str) -> bool:
    patterns = {
        "movements": [
            "No tenés movimientos",
            "No hay movimientos",
            "Todavía no tenés movimientos",
        ],
        "statements": [
            "No tenés resúmenes",
            "No hay resúmenes",
            "Todavía no tenés resúmenes",
        ],
        "loans": [
            "No tenés préstamos",
            "No hay préstamos",
            "Todavía no tenés préstamos",
        ],
    }
    return page.evaluate(
        "(phrases) => { const text = document.body?.innerText || ''; "
        "return phrases.some(phrase => text.includes(phrase)); }",
        patterns[section],
    )


def inspect_cards(page) -> None:
    click_section(page, "Tarjetas")
    page.wait_for_selector('[id^="card-"]', timeout=30000)
    cards = discover_cards_from_html(page.content())
    credit = sum(card["kind"] == "credit" for card in cards)
    debit = sum(card["kind"] == "debit" for card in cards)
    print(
        f"CARDS count={len(cards)} credit={credit} debit={debit} "
        f"path={sanitized_path(page.url)}"
    )

    for ordinal, card in enumerate(cards):
        try:
            click_section(page, "Tarjetas")
            page.locator(f"#card-{card['index']}").click()
            settle(page)
            movements_tab = page.get_by_text("Movimientos", exact=True).count()
            statements_tab = page.get_by_text("Resúmenes", exact=True).count()
            print(
                f"CARD ordinal={ordinal} kind={card['kind']} open=ok "
                f"movements_tab={movements_tab} statements_tab={statements_tab} "
                f"path={sanitized_path(page.url)}"
            )

            if movements_tab:
                page.get_by_text("Movimientos", exact=True).click()
                settle(page)
                print(
                    f"CARD_MOVEMENTS ordinal={ordinal} "
                    f"table={page.locator('table tbody').count()} "
                    f"rows={page.locator('table tbody tr').count()} "
                    f"known_empty={has_known_empty_state(page, 'movements')}"
                )

            if statements_tab:
                click_section(page, "Tarjetas")
                page.locator(f"#card-{card['index']}").click()
                settle(page)
                page.get_by_text("Resúmenes", exact=True).click()
                settle(page)
                print(
                    f"CARD_STATEMENTS ordinal={ordinal} "
                    f"items={len(list_card_statements(page.content()))} "
                    f"buttons={page.get_by_role('button', name='Descargar', exact=True).count()} "
                    f"known_empty={has_known_empty_state(page, 'statements')}"
                )
        except Exception as exc:
            print(f"CARD ordinal={ordinal} error={type(exc).__name__}")


def inspect_loans(page) -> None:
    click_section(page, "Préstamos")
    loans = discover_loans_from_html(page.content())
    candidates = page.locator('a[href^="/loans/"]').count()
    print(
        f"LOANS count={len(loans)} candidates={candidates} "
        f"known_empty={has_known_empty_state(page, 'loans')} "
        f"path={sanitized_path(page.url)}"
    )

    for ordinal, loan in enumerate(loans):
        try:
            click_section(page, "Préstamos")
            page.locator(f'a[href="{loan["url"]}"]').click()
            settle(page)
            all_installments = page.get_by_role(
                "radio", name="Todas las cuotas", exact=True
            ).count()
            all_short = page.get_by_role("radio", name="Todas", exact=True).count()
            all_text = page.get_by_text("Todas", exact=True).count()
            print(
                f"LOAN ordinal={ordinal} open=ok "
                f"radio_all_installments={all_installments} "
                f"radio_all={all_short} text_all={all_text} "
                f"path={sanitized_path(page.url)}"
            )
            if all_installments:
                page.get_by_role(
                    "radio", name="Todas las cuotas", exact=True
                ).click()
            elif all_short:
                page.get_by_role("radio", name="Todas", exact=True).click()
            elif all_text:
                page.get_by_text("Todas", exact=True).click()
            settle(page)
            print(
                f"LOAN_INSTALLMENTS ordinal={ordinal} "
                f"table={page.locator('table tbody').count()} "
                f"rows={page.locator('table tbody tr').count()}"
            )
        except Exception as exc:
            print(f"LOAN ordinal={ordinal} error={type(exc).__name__}")


def should_inspect_loans(arguments: list[str]) -> bool:
    return "--cards-only" not in arguments


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()

        def record_response(response) -> None:
            if response.request.resource_type not in {"fetch", "xhr"}:
                return
            if "/api/v1/execute/" not in response.url:
                return
            print(
                "HTTP"
                f" method={response.request.method}"
                f" status={response.status}"
                f" path={sanitized_path(response.url)}"
            )

        page.on("response", record_response)
        try:
            if not login(page):
                print("RESULT login_failed")
                return 1
            print(f"RESULT login_ok path={sanitized_path(page.url)}")
            inspect_cards(page)
            if should_inspect_loans(sys.argv[1:]):
                inspect_loans(page)
            print("RESULT diagnostic_complete")
            return 0
        finally:
            logout(page)
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
