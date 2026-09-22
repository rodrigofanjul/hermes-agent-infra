#!/usr/bin/env python3
"""Monthly ARCA (ex-AFIP) Factura C generation via the RCEL portal.

Replaces the "ARCA - Facturas C mensuales" cron job, which ran in AGENT
mode with the real CUIT and password embedded in plaintext inside the
job's prompt — sent to whatever LLM provider was resolved on every run
(confirmed to have reached a third-party provider at least once), and
never actually completed a real invoice end-to-end. This script follows
the same pattern as galicia_sync.py/bna_sync.py: a single --no-agent
script, credentials read only from os.environ, Playwright for the real
browser interaction.

STATUS: login only, confirmed real (see login() below — selectors
verified live against the real site on 2026-09-22). The RCEL
invoice-generation flow (selecting punto de venta, the 4-step form —
Datos de emisión / Receptor / Operación / Resumen — for each invoice)
has NOT been reconnoitered yet: nothing past login is implemented, and
nothing here has ever submitted a real invoice. Do NOT deploy this to
the cron job or run it unattended until that reconnaissance is done and
reviewed by a human — see docs/superpowers/specs/ for the design
process used for galicia_sync.py/bna_sync.py, which this should follow
given the significantly higher stakes of a real tax document versus a
personal backup.

Prints nothing on success (silent = OK for hermes's --no-agent watchdog
mode). Prints a short line and exits non-zero on login failure or a
partial section failure — that stdout is what hermes delivers to
WhatsApp via --deliver origin.
"""
import os
import sys

from playwright.sync_api import sync_playwright, Page

LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"


def login(page: Page) -> bool:
    """Logs into ARCA/AFIP (a 2-step login: CUIT/CUIL, then Clave Fiscal
    on the same URL after a server-side page transition — confirmed
    real live on 2026-09-22, both fields load correctly with a normal
    Playwright click + the page's own navigation wait, no special
    handling needed for the JSF/ViewState-based form). Returns True on
    success, False if the login failed (still on the login URL after
    submit, or a required credential env var is missing). Never prints
    or returns the credentials themselves.
    """
    try:
        cuit = os.environ["ARCA_CUIT"]
        password = os.environ["ARCA_PASSWORD"]
    except KeyError as e:
        print(f"ARCA: falta la variable de entorno {e.args[0]}")
        return False

    page.goto(LOGIN_URL)
    page.locator("#F1\\:username").type(cuit, delay=30)
    page.locator("#F1\\:btnSiguiente").click()
    page.wait_for_load_state("networkidle")

    page.locator("#F1\\:password").type(password, delay=30)
    page.get_by_role("button", name="Ingresar").click()
    page.wait_for_load_state("networkidle")

    return "login.xhtml" not in page.url


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        try:
            if not login(page):
                print("ARCA: no se pudo iniciar sesión, revisar manualmente")
                return 1

            print(
                "ARCA: login confirmado, pero la generación de facturas todavía "
                "no está implementada — falta reconocer en vivo el flujo del "
                "RCEL (selección de punto de venta y los 4 pasos del "
                "formulario). No se emitió ninguna factura."
            )
            return 1
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
