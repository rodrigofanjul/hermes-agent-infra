#!/usr/bin/env python3
"""Daily Banco Nación (BNA+) sync: backs up account balances/movements,
card movements/statements, and loan installment history to Google
Drive. Run by hermes as a --no-agent cron job — see
docs/superpowers/specs/2026-09-09-bna-sync-design.md.

Prints nothing on success (silent = OK for hermes's --no-agent watchdog
mode). Prints a short line and exits non-zero on login failure or a
partial section failure — that stdout is what hermes delivers to
WhatsApp via --deliver origin.
"""
import os
import sys

from playwright.sync_api import sync_playwright, Page

LOGIN_STEP1_URL = "https://digital.bna.com.ar/loginStep1"
DRIVE_ROOT_FOLDER_ID = "1wNYrxgVJo6MPN4kc7aZK3q8qi9s9ZlJS"  # "Bancos" folder
BNA_FOLDER_ID = "1Pt0FPSeV8xxSg3-rcJ33ImGmIF6tV45O"  # "Bancos/BNA"
CUENTAS_FOLDER_ID = "1iNtZBCxBfA54eQF9GB78tEOeO3jQZdnn"  # "Bancos/BNA/Cuentas"
TARJETAS_FOLDER_ID = "1OcoYvPEslra2fOmVyBAQVG0cHkIg-J7N"  # "Bancos/BNA/Tarjetas"
PRESTAMOS_FOLDER_ID = "1lZCMmiBgFI1EpLOP9IZSaaEznrpkE03a"  # "Bancos/BNA/Prestamos"
RESUMENES_FOLDER_ID = "1-L1eTzrqwDofB_adSUt1pfXro_rZ5K2A"  # "Bancos/BNA/Tarjetas/Resumenes"


def login(page: Page) -> bool:
    """Logs into BNA+ (a 2-step login, confirmed real: DNI+Usuario on
    /loginStep1, then Password on /loginStep2 — unlike Galicia's single
    step). Returns True on success, False if the login failed (still
    on either login step's URL after submit, or a required credential
    env var is missing). Never prints or returns the credentials
    themselves.
    """
    try:
        dni = os.environ["BNA_DNI"]
        user = os.environ["BNA_USER"]
        password = os.environ["BNA_PASSWORD"]
    except KeyError as e:
        print(f"BNA: falta la variable de entorno {e.args[0]}")
        return False

    page.goto(LOGIN_STEP1_URL)
    page.locator("#document").type(dni, delay=30)
    page.locator("#username").type(user, delay=30)
    page.locator("#global\\.continue").click()
    page.wait_for_load_state("networkidle")

    page.locator("#password").type(password, delay=30)
    page.locator("#global\\.continue").click()
    page.wait_for_load_state("networkidle")
    try:
        # Confirmed via live testing: networkidle fires before the SPA's
        # client-side navigation away from /loginStep2 actually completes
        # (about 1s later) — checking page.url right after networkidle
        # alone caught a false "still on loginStep2" a real successful
        # login as a failure.
        page.wait_for_function("() => !location.pathname.includes('loginStep')", timeout=15000)
    except Exception:
        pass  # genuinely still on a login step — return False below

    return "loginStep" not in page.url


def logout(page: Page) -> None:
    """Logs out. Best-effort — swallow errors, this always runs in a
    finally block and must never raise past it. Confirmed real
    structure: the user's display name is a <button> in the top nav
    banner that opens a menu containing a "Salir" button — selected
    generically (the only <button> in that banner besides notifications
    and help, both of which are <link> elements, not buttons) so this
    doesn't hardcode the account holder's name.
    """
    try:
        page.get_by_role("banner", name="Barra de navegación principal").get_by_role("button").click()
        page.get_by_role("button", name="Salir").click()
        page.wait_for_load_state("networkidle")
    except Exception:
        pass


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        try:
            if not login(page):
                print("BNA: no se pudo iniciar sesión, revisar manualmente")
                return 1

            # Extraction steps land here in later tasks of this plan.

            return 0
        finally:
            logout(page)
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
