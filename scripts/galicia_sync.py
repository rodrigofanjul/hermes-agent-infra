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

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page

LOGIN_URL = "https://onlinebanking.bancogalicia.com.ar/login"
ACCOUNTS_URL = "https://onlinebanking.bancogalicia.com.ar/navigation/menulink/2"
DRIVE_ROOT_FOLDER_ID = "1wNYrxgVJo6MPN4kc7aZK3q8qi9s9ZlJS"  # "Bancos" folder

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
    """Returns a list of {"name": str, "indice": str} for each account
    found on the accounts overview page. There's no direct per-account
    URL — each account is a clickable div on this same page, selected
    by its stable data-indice attribute (see get_account_movements_html).
    """
    page.goto(ACCOUNTS_URL)
    page.wait_for_load_state("networkidle")
    soup = BeautifulSoup(page.content(), "html.parser")

    accounts = []
    for el in soup.find_all("div", attrs={"role": "button", "data-indice": True, "data-tipo": True}):
        aria_label = el.get("aria-label", "")
        match = re.search(r"Acceso a (.+?) número", aria_label)
        name = match.group(1) if match else el["data-tipo"]
        accounts.append({"name": name, "indice": el["data-indice"]})
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


def sync_account_movements_csv(account_name: str, new_movements: list[dict], parent_folder_id: str) -> None:
    """Downloads the existing CSV for this account (if any), merges in
    only movements not already present (deduped by date+description+amount),
    and re-uploads. Deletes the old Drive file first, then uploads the
    merged version — `drive upload` has no in-place overwrite mode, only
    create, so this delete-then-upload approach avoids leaving duplicate
    same-named files (behavior confirmed empirically in Task 5's live
    end-to-end test).
    """
    filename = f"{slugify(account_name)}.csv"
    existing_file_id = drive_find_file(filename, parent_folder_id)

    existing_rows: set[tuple[str, str, str]] = set()
    local_path = f"/tmp/{filename}"

    if existing_file_id:
        subprocess.run(
            [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "download", existing_file_id, "--output", local_path],
            capture_output=True, text=True, check=True,
        )
        with open(local_path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_rows.add((row["date"], row["description"], row["amount"]))

    new_rows = {(m["date"], m["description"], m["amount"]) for m in new_movements}
    all_rows = existing_rows | new_rows

    if all_rows == existing_rows and existing_file_id:
        return  # nothing new, nothing to upload

    with open(local_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "description", "amount"])
        writer.writeheader()
        for row in sorted(all_rows):
            writer.writerow({"date": row[0], "description": row[1], "amount": row[2]})

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
