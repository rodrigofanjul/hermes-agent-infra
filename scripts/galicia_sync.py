#!/usr/bin/env python3
"""Daily Banco Galicia sync: backs up balances, movements, and card
statements to Google Drive. Run by hermes as a --no-agent cron job —
see docs/superpowers/specs/2026-09-08-galicia-sync-design.md.

Prints nothing on success (silent = OK for hermes's --no-agent watchdog
mode). Prints a short line and exits non-zero on login failure or a
partial section failure — that stdout is what hermes delivers to
WhatsApp via --deliver origin.
"""
import csv
import json
import os
import re
import subprocess
import sys
from datetime import date

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page

LOGIN_URL = "https://onlinebanking.bancogalicia.com.ar/login"
ACCOUNTS_URL = "https://onlinebanking.bancogalicia.com.ar/navigation/menulink/2"
DRIVE_ROOT_FOLDER_ID = "1wNYrxgVJo6MPN4kc7aZK3q8qi9s9ZlJS"  # "Bancos" folder
GALICIA_FOLDER_ID = "1R5nLrV-q8Y85pPSKLNJshs9dtAqr1EQm"  # "Bancos/Galicia"
CUENTAS_FOLDER_ID = "1y1bRpau2km4YTdQx3Rel7dnlaCkYZgko"  # "Bancos/Galicia/Cuentas"
TARJETAS_FOLDER_ID = "1xiVDZqs_KrTj0XnFcPa_lsoYNLWDz2l5"  # "Bancos/Galicia/Tarjetas"
RESUMENES_FOLDER_ID = "1IbVWTMfhcLPwuA30sRCgb3IVhJIVy6_h"  # "Bancos/Galicia/Tarjetas/Resumenes"

GOOGLE_API_SCRIPT = "/opt/hermes/skills/productivity/google-workspace/scripts/google_api.py"
VENV_PYTHON = "/opt/hermes/.venv/bin/python"


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
    page.locator("#DocumentNumber").type(dni, delay=30)
    page.locator("#UserName").type(user, delay=30)
    page.locator("#Password").type(password, delay=30)
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


def discover_accounts(page: Page) -> list[dict]:
    """Returns a list of {"name": str, "indice": str, "balance": str} for
    each account found on the accounts overview page. There's no direct
    per-account URL — each account is a clickable div on this same page,
    selected by its stable data-indice attribute (see
    get_account_movements_html). The balance is parsed from the same
    aria-label used for the name (confirmed real format: "Acceso a Caja
    Ahorro Pesos número ... saldo 0,00") — this page shows it directly,
    no need to open the account to read it.
    """
    page.goto(ACCOUNTS_URL)
    page.wait_for_load_state("networkidle")
    soup = BeautifulSoup(page.content(), "html.parser")

    accounts = []
    for el in soup.find_all("div", attrs={"role": "button", "data-indice": True, "data-tipo": True}):
        aria_label = el.get("aria-label", "")
        name_match = re.search(r"Acceso a (.+?) número", aria_label)
        name = name_match.group(1) if name_match else el["data-tipo"]
        balance_match = re.search(r"saldo ([\d.,]+)", aria_label)
        balance = balance_match.group(1) if balance_match else ""
        accounts.append({"name": name, "indice": el["data-indice"], "balance": balance})
    return accounts


def get_account_movements_html(page: Page, account: dict) -> str:
    """Navigates to the accounts overview page, clicks the given account
    (identified by its data-indice), waits for its movements to actually
    load (this section renders via AJAX after the initial page load, so
    networkidle alone isn't a reliable signal — the extra waits below for
    document.body and then a DD/MM/YYYY date pattern in the page text were
    confirmed necessary via live testing), and returns the resulting
    page's HTML.
    """
    page.goto(ACCOUNTS_URL)
    page.wait_for_load_state("networkidle")
    page.evaluate(
        """(indice) => {
            const el = document.querySelector(`div[role="button"][data-indice="${indice}"]`);
            if (el) el.click();
        }""",
        account["indice"],
    )
    page.wait_for_function("() => !!document.body", timeout=15000)
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_function(
            r"() => document.body && /\d{2}\/\d{2}\/\d{4}/.test(document.body.innerText)",
            timeout=20000,
        )
    except Exception:
        pass  # best-effort: parse_movements below returns [] if nothing loaded
    return page.content()


def parse_movements(html: str) -> list[dict]:
    """Returns a list of {"date": str, "description": str, "amount": str}
    for each movement found in an account's movements-page HTML.

    Confirmed real structure (from a live capture): each movement is a
    top-level child of #ContenedorMovimientos, a div[role="button"] whose
    first two ".detalle-movimiento" children are the date and description;
    the signed peso amount (e.g. "-0,07 pesos") is in a ".sr-only" span
    elsewhere in the same row (more reliable than parsing the "$"-formatted
    visible span, which omits the sign for positive amounts inside a
    nested <label>).
    """
    soup = BeautifulSoup(html, "html.parser")
    container = soup.find(id="ContenedorMovimientos")
    if not container:
        return []

    movements = []
    for row in container.find_all("div", attrs={"role": "button"}, recursive=False):
        detail_divs = row.find_all("div", class_="detalle-movimiento")
        if len(detail_divs) < 2:
            continue
        date = detail_divs[0].get_text(strip=True)
        description = detail_divs[1].get_text(strip=True)
        sr_only = row.find("span", class_="sr-only")
        amount = sr_only.get_text(strip=True) if sr_only else ""
        movements.append({"date": date, "description": description, "amount": amount})
    return movements


def slugify(name: str) -> str:
    """"Caja Ahorro Pesos" -> "caja_ahorro_pesos"."""
    s = name.lower()
    for accented, plain in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        s = s.replace(accented, plain)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def drive_find_file(name: str, parent_folder_id: str) -> str | None:
    """Returns the file_id if a file with this exact name exists directly
    under parent_folder_id, else None. `google_api.py drive search` returns
    a JSON array of objects with at least "id" and "name" (confirmed via
    live invocation)."""
    query = f"name = '{name}' and '{parent_folder_id}' in parents and trashed = false"
    result = subprocess.run(
        [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "search", query, "--raw-query"],
        capture_output=True, text=True, check=True,
    )
    matches = json.loads(result.stdout)
    return matches[0]["id"] if matches else None


def sync_csv(filename: str, fieldnames: list[str], new_records: list[dict], parent_folder_id: str) -> None:
    """Downloads the existing CSV at this name under parent_folder_id (if
    any), merges in only records not already present (deduped by the full
    row tuple), and re-uploads. Deletes the old Drive file first, then
    uploads the merged version — `drive upload` has no in-place overwrite
    mode, only create, so this delete-then-upload approach avoids leaving
    duplicate same-named files (behavior confirmed empirically in Task 5's
    live end-to-end test for account movements).
    """
    existing_file_id = drive_find_file(filename, parent_folder_id)

    existing_rows: set[tuple] = set()
    local_path = f"/tmp/{filename}"

    if existing_file_id:
        subprocess.run(
            [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "download", existing_file_id, "--output", local_path],
            capture_output=True, text=True, check=True,
        )
        with open(local_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_rows.add(tuple(row[k] for k in fieldnames))

    new_rows = {tuple(r[k] for k in fieldnames) for r in new_records}
    all_rows = existing_rows | new_rows

    if all_rows == existing_rows and existing_file_id:
        return  # nothing new, nothing to upload

    with open(local_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in sorted(all_rows):
            writer.writerow(dict(zip(fieldnames, row)))

    if existing_file_id:
        subprocess.run(
            [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "delete", existing_file_id, "--permanent"],
            capture_output=True, text=True, check=True,
        )

    subprocess.run(
        [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "upload", local_path,
         "--name", filename, "--parent", parent_folder_id],
        capture_output=True, text=True, check=True,
    )
    os.remove(local_path)


def sync_account_movements_csv(account_name: str, new_movements: list[dict], parent_folder_id: str) -> None:
    sync_csv(f"{slugify(account_name)}.csv", ["date", "description", "amount"], new_movements, parent_folder_id)


def sync_account_balance_csv(account_name: str, balance: str, parent_folder_id: str) -> None:
    """Appends today's balance snapshot to saldo_<account>.csv — one row
    per day, so re-running the same day is a no-op (sync_csv's row-tuple
    dedup) but a balance change on a later day adds a new row instead of
    overwriting history."""
    today = date.today().isoformat()
    sync_csv(
        f"saldo_{slugify(account_name)}.csv",
        ["date", "balance"],
        [{"date": today, "balance": balance}],
        parent_folder_id,
    )


CARDS_URL = "https://onlinebanking.bancogalicia.com.ar/navigation/menulink/390"


def discover_cards(page: Page) -> list[dict]:
    """Returns a list of {"index": int, "last4": str} for each card found
    on the cards overview page, in carousel-slide order. Confirmed real
    structure: the cards overview renders each card as a slide in a slick
    carousel (three dots at `.slick-dots li button`, text "0"/"1"/"2");
    each slide has its own div.card-number-container showing
    "**** **** **** NNNN". "index" is the slide index to pass to
    switch_active_card to bring that card's movements/statements into
    view — the page only ever shows one card's MOVIMIENTOS/Resumen data
    at a time, for whichever slide is active.
    """
    page.goto(CARDS_URL)
    page.wait_for_load_state("networkidle")
    soup = BeautifulSoup(page.content(), "html.parser")

    cards = []
    for i, container in enumerate(soup.find_all("div", class_="card-number-container")):
        digits = container.get_text(strip=True).replace("*", "").strip()
        if digits:
            cards.append({"index": i, "last4": digits})
    return cards


def switch_active_card(page: Page, index: int) -> None:
    """Clicks the given slide's dot in the cards carousel so its
    movements/statements become the ones shown on the page. Must be
    called (with a page already on CARDS_URL) before reading movements or
    statements for any card other than the default (index 0) one."""
    page.evaluate(
        """(i) => {
            const dots = document.querySelectorAll('.slick-dots li button');
            if (dots[i]) dots[i].click();
        }""",
        index,
    )
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)  # let the movements/resumen panel re-render for the new slide


def get_card_movements_html(page: Page, card_index: int) -> str:
    """Navigates to the cards overview page, switches to the given card's
    carousel slide, then clicks "Mostrar más" (a pure client-side route
    change, confirmed via live network-request logging to fire no API
    call) repeatedly, up to a small bounded number of iterations, to load
    the full movements table before returning the page's HTML.
    """
    page.goto(CARDS_URL)
    page.wait_for_load_state("networkidle")
    switch_active_card(page, card_index)
    for _ in range(20):
        clicked = page.evaluate(
            """() => {
                const link = Array.from(document.querySelectorAll('a'))
                    .find(a => a.textContent.trim() === 'Mostrar más');
                if (link) { link.click(); return true; }
                return false;
            }"""
        )
        if not clicked:
            break
        page.wait_for_timeout(800)
    return page.content()


def parse_card_movements(html: str) -> list[dict]:
    """Returns a list of {"date", "card", "description", "installments",
    "amount_ars", "amount_usd"} for each row in the card movements table.

    Confirmed real structure: a div.react-bootstrap-table wraps a <table>
    whose <tbody><tr> rows have 6 <td> cells matching the header order
    Fecha/Tarjeta/Descripción/Cuotas/Importe en pesos/Importe en dólares.
    """
    soup = BeautifulSoup(html, "html.parser")
    wrapper = soup.find("div", class_="react-bootstrap-table")
    if not wrapper or not wrapper.table or not wrapper.table.tbody:
        return []

    movements = []
    for row in wrapper.table.tbody.find_all("tr", recursive=False):
        cells = row.find_all("td", recursive=False)
        if len(cells) < 6:
            continue
        values = [c.get_text(strip=True) for c in cells]
        movements.append({
            "date": values[0],
            "card": values[1],
            "description": values[2],
            "installments": values[3],
            "amount_ars": values[4],
            "amount_usd": values[5],
        })
    return movements


def sync_card_movements_csv(last4: str, new_movements: list[dict], parent_folder_id: str) -> None:
    sync_csv(
        f"tarjeta_{last4}.csv",
        ["date", "card", "description", "installments", "amount_ars", "amount_usd"],
        new_movements,
        parent_folder_id,
    )


def get_statement_rows_html(page: Page, card_index: int) -> str:
    """Navigates to the cards overview page, switches to the given card's
    carousel slide, then its Resumen tab and Resúmenes mensuales sub-tab,
    waiting for each to actually render (confirmed via live testing that a
    fixed networkidle wait isn't enough — the tab content, including the
    buttons this clicks, renders slightly after that), and returns the
    resulting HTML once the statements table has real rows.

    Returns "" if no "Resumen" tab ever appears — confirmed live for a
    card with no recent activity, which apparently doesn't get a separate
    statements section at all. That's treated as "zero statements to
    sync", not an error.
    """
    page.goto(CARDS_URL)
    page.wait_for_load_state("networkidle")
    switch_active_card(page, card_index)

    try:
        page.wait_for_function(
            """() => Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('Resumen'))""",
            timeout=15000,
        )
    except Exception:
        return ""
    page.evaluate(
        """() => {
            const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Resumen'));
            if (btn) btn.click();
        }"""
    )
    page.wait_for_load_state("networkidle")

    page.wait_for_function(
        """() => Array.from(document.querySelectorAll('button')).some(b => b.textContent.includes('Mostrar resúmenes mensuales'))""",
        timeout=15000,
    )
    page.evaluate(
        """() => {
            const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Mostrar resúmenes mensuales'));
            if (btn) btn.click();
        }"""
    )
    page.wait_for_load_state("networkidle")
    page.wait_for_selector("table tbody tr", timeout=15000)
    return page.content()


def parse_statement_rows(html: str) -> list[dict]:
    """Returns a list of {"index": int, "date": str, "month": str,
    "year": str} for each monthly statement row, in table order —
    "index" matches the row's position for download_statement_pdf.
    Confirmed real structure:
    same div.react-bootstrap-table wrapper as the movements table, with
    columns Fecha/Mes/Año/(download menu)."""
    soup = BeautifulSoup(html, "html.parser")
    wrapper = soup.find("div", class_="react-bootstrap-table")
    if not wrapper or not wrapper.table or not wrapper.table.tbody:
        return []

    rows = []
    for i, tr in enumerate(wrapper.table.tbody.find_all("tr", recursive=False)):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 3:
            continue
        rows.append({
            "index": i,
            "date": cells[0].get_text(strip=True),
            "month": cells[1].get_text(strip=True),
            "year": cells[2].get_text(strip=True),
        })
    return rows


def download_statement_pdf(page: Page, row_index: int, local_path: str) -> None:
    """Downloads one monthly statement PDF via its row's dropdown menu
    "Descargar resumen" link.

    Confirmed real structure: that menu item is an <a title="Descargar
    resumen"> already present in the DOM for every row (not lazily
    rendered on menu-open), so clicking it directly via JS — bypassing
    Playwright's visibility-actionability checks, which failed in earlier
    testing because this framework doesn't toggle a standard "open"
    attribute on the menu wrapper when its "..." button is clicked — is
    the confirmed-working approach. This replaces the original plan of
    calling /api/resumen/list + /api/resumen/getresumen directly with
    `requests`: that returned an HTTP 500 (likely missing browser-set
    headers) when tried with the session's cookies during reconnaissance.
    """
    with page.expect_download(timeout=30000) as download_info:
        page.evaluate(
            """(rowIndex) => {
                const rows = document.querySelectorAll('.react-bootstrap-table table tbody tr');
                const row = rows[rowIndex];
                if (!row) return;
                const link = Array.from(row.querySelectorAll('a')).find(a => (a.title || '').includes('Descargar'));
                if (link) link.click();
            }""",
            row_index,
        )
    download = download_info.value
    download.save_as(local_path)


def sync_card_statements(page: Page, card_index: int, last4: str, resumenes_folder_id: str) -> None:
    """Syncs every monthly statement PDF for the given card to
    resumenes_folder_id, skipping any whose filename already exists there
    (statements don't change once issued, so name-based dedup is enough —
    no need to download-and-compare like the movements CSVs)."""
    html = get_statement_rows_html(page, card_index)
    for row in parse_statement_rows(html):
        # Full date (not just month/year) avoids filename collisions when
        # two statements land in the same month — confirmed to happen for
        # real (two distinct "Julio 2026" rows with different day-of-month
        # dates were found live for one card).
        date_slug = row["date"].replace("/", "-")
        filename = f"{last4}_{date_slug}_{slugify(row['month'])}.pdf"
        if drive_find_file(filename, resumenes_folder_id):
            continue

        local_path = f"/tmp/{filename}"
        download_statement_pdf(page, row["index"], local_path)
        subprocess.run(
            [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "upload", local_path,
             "--name", filename, "--parent", resumenes_folder_id],
            capture_output=True, text=True, check=True,
        )
        os.remove(local_path)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        try:
            if not login(page):
                print("Galicia: no se pudo iniciar sesión, revisar manualmente")
                return 1

            failures = []

            try:
                accounts = discover_accounts(page)
                for account in accounts:
                    try:
                        html = get_account_movements_html(page, account)
                        movements = parse_movements(html)
                        sync_account_movements_csv(account["name"], movements, CUENTAS_FOLDER_ID)
                        sync_account_balance_csv(account["name"], account["balance"], CUENTAS_FOLDER_ID)
                    except Exception as e:
                        failures.append(f"cuenta {account['name']}: {e}")
            except Exception as e:
                failures.append(f"descubrimiento de cuentas: {e}")

            try:
                cards = discover_cards(page)
                for card in cards:
                    index, last4 = card["index"], card["last4"]
                    try:
                        movements_html = get_card_movements_html(page, index)
                        movements = parse_card_movements(movements_html)
                        sync_card_movements_csv(last4, movements, TARJETAS_FOLDER_ID)
                    except Exception as e:
                        failures.append(f"movimientos tarjeta {last4}: {e}")
                    try:
                        sync_card_statements(page, index, last4, RESUMENES_FOLDER_ID)
                    except Exception as e:
                        failures.append(f"resumenes tarjeta {last4}: {e}")
            except Exception as e:
                failures.append(f"descubrimiento de tarjetas: {e}")

            if failures:
                print("Galicia: sync parcial, falló: " + "; ".join(failures))
                return 1

            return 0
        finally:
            logout(page)
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
