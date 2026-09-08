#!/usr/bin/env python3
"""Daily Banco Galicia sync: backs up balances, movements, and card
statements to Google Drive. Run by hermes as a --no-agent cron job —
see docs/superpowers/specs/2026-09-08-galicia-sync-design.md.

Prints nothing on success (silent = OK for hermes's --no-agent watchdog
mode). Prints a short line and exits non-zero on login failure or a
partial section failure — that stdout is what hermes delivers to
WhatsApp via --deliver origin.
"""
import os
import sys

from playwright.sync_api import sync_playwright, Page

LOGIN_URL = "https://onlinebanking.bancogalicia.com.ar/login"
DRIVE_ROOT_FOLDER_ID = "1wNYrxgVJo6MPN4kc7aZK3q8qi9s9ZlJS"  # "Bancos" folder


def login(page: Page) -> bool:
    """Logs into Banco Galicia. Returns True on success, False if the
    login failed (still on /login after submit, an error is shown, or
    a required credential env var is missing). Never prints or returns
    the credentials themselves.
    """
    try:
        dni = os.environ["GALICIA_DNI"]
        user = os.environ["GALICIA_USER"]
        password = os.environ["GALICIA_PASSWORD"]
    except KeyError as e:
        print(f"Galicia: falta la variable de entorno {e.args[0]}")
        return False

    page.goto(LOGIN_URL)
    page.locator("#DocumentNumber").fill(dni)
    page.locator("#UserName").fill(user)
    page.locator("#Password").fill(password)
    page.get_by_role("button", name="iniciar sesión").click()
    page.wait_for_load_state("networkidle")

    return "/login" not in page.url


def logout(page: Page) -> None:
    """Logs out. Best-effort — swallow errors, this always runs in a
    finally block and must never raise past it."""
    try:
        page.get_by_role("link", name="Cerrar Sesión").click()
        page.wait_for_load_state("networkidle")
    except Exception:
        pass


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        try:
            if not login(page):
                print("Galicia: no se pudo iniciar sesión, revisar manualmente")
                return 1

            # Extraction steps land here in later tasks of this plan.

            return 0
        finally:
            logout(page)
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
