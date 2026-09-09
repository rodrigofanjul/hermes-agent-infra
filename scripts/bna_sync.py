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
import csv
import json
import os
import re
import subprocess
import sys
from datetime import date

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, Page

GOOGLE_API_SCRIPT = "/opt/hermes/skills/productivity/google-workspace/scripts/google_api.py"
VENV_PYTHON = "/opt/hermes/.venv/bin/python"

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


ACCOUNTS_URL = "https://digital.bna.com.ar/accounts/myaccounts"


def navigate_to_accounts(page: Page) -> None:
    """Open Cuentas through the authenticated SPA and wait for a final state."""
    page.get_by_role("link", name="Cuentas", exact=True).click()
    page.wait_for_function(
        """() => {
            const text = document.body?.innerText || '';
            return !!document.querySelector('[id^="account_card_number_"]')
                || text.includes('Mis Cuentas (0)')
                || text.includes('ninguna cuenta abierta')
                || location.pathname.includes('/error');
        }""",
        timeout=20000,
    )


class BNADataUnavailableError(RuntimeError):
    """BNA authenticated the session but did not provide usable account data."""


def classify_accounts_html(html: str) -> list[dict]:
    accounts = discover_accounts_from_html(html)
    if accounts:
        return accounts

    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    if "Mis Cuentas (0)" in text or "ninguna cuenta abierta" in text:
        raise BNADataUnavailableError("BNA respondió con cero cuentas")
    raise BNADataUnavailableError("la sección Cuentas no terminó de cargar")


def discover_accounts_from_html(html: str) -> list[dict]:
    """Pure HTML-parsing half of discover_accounts — see its docstring
    for the confirmed real structure. Split out so it's testable
    against a fixture without a live Page."""
    soup = BeautifulSoup(html, "html.parser")
    accounts = []
    for button in soup.find_all("button", id=re.compile(r"^account_card_number_\d+$")):
        index = button["id"].rsplit("_", 1)[1]
        label_span = button.find("span", attrs={"aria-label": True})
        name = label_span["aria-label"] if label_span else button.get_text(strip=True)
        accounts.append({"index": index, "name": name})
    return accounts


def discover_accounts(page: Page) -> list[dict]:
    """Returns a list of {"index": str, "name": str} for each account
    on the accounts overview page. Confirmed real structure: each
    account is a <button id="account_card_number_{i}" role="link">
    containing a labelled <span aria-label="..."> with the account's
    name (e.g. "CUENTA SUELDO"). The account's real per-account URL
    (/accounts/<hash>) isn't visible until after clicking, so this
    returns the stable numeric index instead (see
    get_account_detail_html)."""
    navigate_to_accounts(page)
    return classify_accounts_html(page.content())


def get_account_detail_html(page: Page, account: dict) -> str:
    """Navigates to the accounts overview page, clicks the given
    account (by its stable numeric index), and waits for its
    movements table to render (a fixed networkidle wait isn't fully
    reliable for this SPA — confirmed via live testing that the table
    can still be empty right after networkidle fires) before returning
    the resulting page's HTML."""
    navigate_to_accounts(page)
    page.locator(f"#account_card_number_{account['index']}").click()
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_selector("table tbody tr", timeout=15000)
    except Exception:
        pass  # best-effort: parse_bank_table below returns [] if nothing loaded
    return page.content()


def parse_bank_table(html: str, columns: list[str]) -> list[dict]:
    """Returns a list of dicts, one per <tr> in the page's first
    <table><tbody>, with keys taken from `columns` in cell order.
    Confirmed real structure: account movements, card movements, and
    loan installments all render through the exact same table
    component (<table><thead><tbody><tr><td>) across this entire site
    — this one parser covers all three, just with a different
    `columns` list per call site. Extra trailing columns (e.g. an
    empty header for a per-row action button) are ignored — only the
    first len(columns) cells of each row are read."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table or not table.tbody:
        return []

    rows = []
    for tr in table.tbody.find_all("tr", recursive=False):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < len(columns):
            continue
        rows.append({col: cells[i].get_text(strip=True) for i, col in enumerate(columns)})
    return rows


def parse_account_balance(html: str) -> str:
    """Returns the account's current balance as shown in its detail
    page header (e.g. "66.346,91"), or "" if not found. Confirmed real
    structure: every amount on this site (movement amounts, the
    accounts-overview consolidated balance) renders as <span role="img"
    aria-roledescription="Monto" aria-label="N Pesos argentinos ">. The
    header balance is the FIRST such element in the page (it appears
    before the movements table in DOM order)."""
    soup = BeautifulSoup(html, "html.parser")
    el = soup.find(attrs={"role": "img", "aria-roledescription": "Monto"})
    if not el:
        return ""
    match = re.match(r"([\d.,]+)", el.get("aria-label", "").strip())
    return match.group(1) if match else ""


def validate_account_detail(html: str, balance: str) -> None:
    if not balance:
        raise BNADataUnavailableError("BNA no proporcionó el saldo de la cuenta")
    soup = BeautifulSoup(html, "html.parser")
    if not soup.select_one("table tbody"):
        raise BNADataUnavailableError("BNA no proporcionó la tabla de movimientos")


def slugify(name: str) -> str:
    """"Caja Ahorro Pesos" -> "caja_ahorro_pesos"."""
    s = name.lower()
    for accented, plain in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u")):
        s = s.replace(accented, plain)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def drive_find_file(name: str, parent_folder_id: str) -> str | None:
    """Returns the file_id if a file with this exact name exists
    directly under parent_folder_id, else None."""
    query = f"name = '{name}' and '{parent_folder_id}' in parents and trashed = false"
    result = subprocess.run(
        [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "search", query, "--raw-query"],
        capture_output=True, text=True, check=True,
    )
    matches = json.loads(result.stdout)
    return matches[0]["id"] if matches else None


def sync_csv(filename: str, fieldnames: list[str], new_records: list[dict], parent_folder_id: str) -> None:
    """Downloads the existing CSV at this name under parent_folder_id
    (if any), merges in only records not already present (deduped by
    the full row tuple), and re-uploads. Delete-then-upload since
    `drive upload` has no in-place overwrite mode — same pattern
    already proven in galicia_sync.py's live end-to-end tests."""
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

    subprocess.run(
        [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "upload", local_path,
         "--name", filename, "--parent", parent_folder_id],
        capture_output=True, text=True, check=True,
    )

    if existing_file_id:
        subprocess.run(
            [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "delete", existing_file_id, "--permanent"],
            capture_output=True, text=True, check=True,
        )

    os.remove(local_path)


def sync_account_movements_csv(account_name: str, movements: list[dict], parent_folder_id: str) -> None:
    sync_csv(f"{slugify(account_name)}.csv", ["date", "receipt", "description", "amount"], movements, parent_folder_id)


def sync_account_balance_csv(account_name: str, balance: str, parent_folder_id: str) -> None:
    sync_csv(
        f"saldo_{slugify(account_name)}.csv",
        ["date", "balance"],
        [{"date": date.today().isoformat(), "balance": balance}],
        parent_folder_id,
    )


def navigate_to_cards(page: Page) -> None:
    """Open Tarjetas through the authenticated SPA and wait for a final state."""
    page.get_by_role("link", name="Tarjetas", exact=True).click()
    page.wait_for_function(
        """() => {
            const text = document.body?.innerText || '';
            return !!document.querySelector('[id^="card-"]')
                || text.includes('ninguna tarjeta')
                || location.pathname.includes('/error');
        }""",
        timeout=20000,
    )


def discover_cards_from_html(html: str) -> list[dict]:
    """Parse the stable card buttons rendered on the cards overview."""
    soup = BeautifulSoup(html, "html.parser")
    cards = []
    for button in soup.find_all("button", id=re.compile(r"^card-\d+$")):
        index = button["id"].split("-")[1]
        card_text = button.get_text(" ", strip=True)
        last4_match = re.search(r"(\d{4})\s*$", card_text)
        kind = "credit" if card_text.casefold().startswith("crédito") else "debit"
        cards.append({
            "index": index,
            "kind": kind,
            "last4": last4_match.group(1) if last4_match else "",
        })
    return cards


def discover_cards(page: Page) -> list[dict]:
    navigate_to_cards(page)
    cards = discover_cards_from_html(page.content())
    if not cards:
        raise BNADataUnavailableError("BNA no proporcionó tarjetas")
    return cards


def open_card_detail(page: Page, card: dict) -> None:
    """Open a card detail through the authenticated SPA."""
    navigate_to_cards(page)
    page.locator(f"#card-{card['index']}").click()
    page.wait_for_url(
        re.compile(r"/cards/(?:creditCards|debitCards)/detail/"),
        timeout=20000,
    )
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass


def get_card_movements_html(page: Page, card: dict) -> str:
    """Return a card's rendered current-period movements page."""
    open_card_detail(page, card)
    try:
        page.get_by_text("Movimientos", exact=True).click(timeout=5000)
    except Exception:
        return ""
    page.wait_for_load_state("networkidle")
    try:
        page.wait_for_selector("table tbody", timeout=15000)
    except Exception:
        return ""
    return page.content()


def sync_card_movements_csv(last4: str, movements: list[dict], parent_folder_id: str) -> None:
    sync_csv(
        f"tarjeta_{last4}.csv",
        ["description", "date", "amount"],
        movements,
        parent_folder_id,
    )


def list_card_statements(html: str) -> list[str]:
    """Return statement labels from list items that pair a label with a
    download button.

    Confirmed real structure: each statement row is
    <li><p>Mes Año</p><button>...</button></li>, where the download
    button's accessible name ("Descargar") comes from a child icon —
    the button itself has no literal text content, so matching on
    `button.string == "Descargar"` (the previous approach) never found
    a match against the real site, even though it matched a fixture
    that had guessed literal button text. Matching on structure
    (a <li> pairing a <p> label with a <button>, with nothing else on
    this page shaped that way) is robust to the icon's exact markup.
    """
    soup = BeautifulSoup(html, "html.parser")
    labels = []
    for item in soup.select("li"):
        button = item.find("button")
        label = item.find("p")
        if button and label:
            labels.append(label.get_text(strip=True))
    return labels


def statements_page_is_empty(html: str) -> bool:
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).casefold()
    return any(
        phrase in text
        for phrase in (
            "no tenés resúmenes",
            "no hay resúmenes",
            "todavía no tenés resúmenes",
        )
    )


def download_statement_pdf(
    page: Page,
    card: dict,
    index: int,
    local_path: str,
    attempts: int = 3,
) -> bool:
    """Download one statement with bounded retries and no raised failure."""
    for attempt in range(attempts):
        try:
            with page.expect_download(timeout=15000) as download_info:
                page.get_by_role(
                    "button",
                    name="Descargar",
                    exact=True,
                ).nth(index).click()
            download_info.value.save_as(local_path)
            return True
        except Exception:
            if attempt < attempts - 1:
                open_card_detail(page, card)
                try:
                    page.get_by_text("Resúmenes", exact=True).click(timeout=5000)
                    page.wait_for_load_state("networkidle")
                except Exception:
                    return False
    return False


def sync_card_statements(page: Page, card: dict, resumenes_folder_id: str) -> list[str]:
    """Upload missing card statements and return labels that could not download.

    Confirmed real structure: the "Descargar" button's accessible name
    comes from a child icon, not literal button text — checking
    `button.innerText.trim() === 'Descargar'` in raw JS (the previous
    approach) never matched the real site, so this always fell through
    to a 30s timeout in production. Playwright's own accessible-name-
    aware locator (already used elsewhere in this file, e.g.
    download_statement_pdf) doesn't have that problem.
    """
    open_card_detail(page, card)
    try:
        page.get_by_text("Resúmenes", exact=True).click(timeout=5000)
    except Exception:
        return []
    page.wait_for_load_state("networkidle")
    try:
        page.get_by_role("button", name="Descargar", exact=True).first.wait_for(timeout=30000)
    except Exception:
        if "/error" in page.url:
            raise BNADataUnavailableError("BNA no proporcionó la lista de resúmenes")
        # No download button ever appeared — fall through to check for
        # the known "sin resúmenes" empty state below rather than
        # assuming failure.
    statements_html = page.content()
    labels = list_card_statements(statements_html)
    if not labels and statements_page_is_empty(statements_html):
        return []
    if not labels:
        raise BNADataUnavailableError("BNA no proporcionó la lista de resúmenes")

    failed = []
    for index, label in enumerate(labels):
        filename = f"{card['last4']}_{slugify(label)}.pdf"
        if drive_find_file(filename, resumenes_folder_id):
            continue

        local_path = f"/tmp/{filename}"
        if not download_statement_pdf(page, card, index, local_path):
            failed.append(label)
            continue

        try:
            subprocess.run(
                [
                    VENV_PYTHON,
                    GOOGLE_API_SCRIPT,
                    "drive",
                    "upload",
                    local_path,
                    "--name",
                    filename,
                    "--parent",
                    resumenes_folder_id,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)
    return failed


def navigate_to_loans(page: Page) -> None:
    """Open Préstamos through the authenticated SPA and wait for its list."""
    page.get_by_role("link", name="Préstamos", exact=True).click()
    page.wait_for_function(
        r"""() => {
            const links = [...document.querySelectorAll('a[href^="/loans/"]')];
            return links.some(link => /Préstamo\s*-/.test(link.innerText))
                || location.pathname.includes('/error');
        }""",
        timeout=20000,
    )


def discover_loans_from_html(html: str) -> list[dict]:
    """Parse real loan detail links and their displayed loan numbers."""
    soup = BeautifulSoup(html, "html.parser")
    loans = []
    for link in soup.find_all("a", href=re.compile(r"^/loans/[0-9a-f]+$")):
        match = re.search(r"Préstamo\s*-\s*(\d+)", link.get_text(" ", strip=True))
        if match:
            loans.append({"url": link["href"], "number": match.group(1)})
    return loans


def discover_loans(page: Page) -> list[dict]:
    navigate_to_loans(page)
    loans = discover_loans_from_html(page.content())
    if not loans:
        raise BNADataUnavailableError("BNA no proporcionó préstamos")
    return loans


def get_loan_installments_html(page: Page, loan: dict) -> str:
    """Open a loan through the SPA and render its complete installment history.

    Confirmed real structure: the "Todas las cuotas" filter is the
    ACCESSIBLE NAME of the radio (from an aria-label), not its visible
    text — the rendered label users see is just "Todas". Waiting for
    that string inside document.body.innerText (the previous approach)
    can never match, since aria-label content isn't part of innerText —
    that wait_for_function silently timed out every time in production,
    which is why loan sync failed even though a manual diagnostic that
    clicked the same radio via Playwright's own accessible-name-aware
    locator succeeded. Using the locator directly (not a raw innerText
    check) avoids that class of bug entirely.
    """
    navigate_to_loans(page)
    page.locator(f'a[href="{loan["url"]}"]').click()
    all_installments_radio = page.get_by_role("radio", name="Todas las cuotas", exact=True)
    try:
        all_installments_radio.wait_for(timeout=20000)
    except Exception:
        if "/error" not in page.url:
            raise
        return page.content()
    all_installments_radio.click()
    page.wait_for_function(
        """() => new Promise(resolve => {
            let lastCount = -1;
            let stableSince = Date.now();
            const poll = () => {
                const count = document.querySelectorAll('table tbody tr').length;
                if (count > 0) {
                    if (count !== lastCount) {
                        lastCount = count;
                        stableSince = Date.now();
                    } else if (Date.now() - stableSince >= 1500) {
                        resolve(true);
                        return;
                    }
                }
                setTimeout(poll, 100);
            };
            poll();
        })""",
        timeout=20000,
    )
    return page.content()


def sync_loan_installments_csv(
    loan_number: str,
    installments: list[dict],
    parent_folder_id: str,
) -> None:
    sync_csv(
        f"prestamo_{loan_number}.csv",
        ["installment", "due_date", "status", "amount"],
        installments,
        parent_folder_id,
    )


def sync_authenticated(page: Page) -> list[str]:
    """Sync authenticated sections, leaving fragile PDF downloads until last."""
    failures = []

    try:
        accounts = discover_accounts(page)
        for account in accounts:
            try:
                html = get_account_detail_html(page, account)
                movements = parse_bank_table(html, ["date", "receipt", "description", "amount"])
                balance = parse_account_balance(html)
                validate_account_detail(html, balance)
                if movements:
                    sync_account_movements_csv(account["name"], movements, CUENTAS_FOLDER_ID)
                sync_account_balance_csv(account["name"], balance, CUENTAS_FOLDER_ID)
            except Exception as e:
                failures.append("cuentas")
    except Exception as e:
        failures.append("cuentas")

    cards = []
    try:
        cards = discover_cards(page)
        for card in cards:
            try:
                movements_html = get_card_movements_html(page, card)
                movements = parse_bank_table(
                    movements_html,
                    ["description", "date", "amount"],
                )
                if movements:
                    sync_card_movements_csv(
                        card["last4"],
                        movements,
                        TARJETAS_FOLDER_ID,
                    )
            except Exception as e:
                failures.append("movimientos de tarjetas")
    except Exception as e:
        failures.append("movimientos de tarjetas")

    try:
        loans = discover_loans(page)
        for loan in loans:
            try:
                installments_html = get_loan_installments_html(page, loan)
                installments = parse_bank_table(
                    installments_html,
                    ["installment", "due_date", "status", "amount"],
                )
                if not installments:
                    raise BNADataUnavailableError(
                        "BNA no proporcionó cuotas del préstamo"
                    )
                sync_loan_installments_csv(
                    loan["number"],
                    installments,
                    PRESTAMOS_FOLDER_ID,
                )
            except Exception as e:
                failures.append("préstamos")
    except Exception as e:
        failures.append("préstamos")

    for card in cards:
        try:
            failed_statements = sync_card_statements(
                page,
                card,
                RESUMENES_FOLDER_ID,
            )
            if failed_statements:
                failures.append("resúmenes de tarjetas")
        except Exception as e:
            failures.append("resúmenes de tarjetas")

    return list(dict.fromkeys(failures))


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        try:
            if not login(page):
                print("BNA: no se pudo iniciar sesión, revisar manualmente")
                return 1

            failures = sync_authenticated(page)
            if failures:
                print("BNA: sync parcial, falló: " + "; ".join(failures))
                return 1
            return 0
        finally:
            logout(page)
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
