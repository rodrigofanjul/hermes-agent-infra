# Banco Galicia Daily Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `galicia_sync.py`, a standalone Python script that logs into Banco Galicia's online banking, extracts balances/movements/card statements, and backs them up to Google Drive — run daily by hermes as a `--no-agent` cron job, with the LLM never touching real credentials or running the extraction logic itself.

**Architecture:** A single linear script (login → discover accounts/cards → extract+dedupe+upload each → logout), using Playwright for the login and any HTML-only pages, `requests` (with cookies extracted from the Playwright session) for the one confirmed JSON API (card statements), and `beautifulsoup4` for HTML parsing. Drive I/O goes through the existing `google_api.py` skill via subprocess, never a new library. Two extraction mechanisms (account movements, card movements) were only ever observed through Playwright's accessibility-tree snapshot during reconnaissance, not raw HTML — this plan investigates the real HTML for each live, in the same task that implements the parser, rather than guessing selectors in advance.

**Tech Stack:** Python (already in the hermes-agent venv), Playwright + Chromium (added in a prior plan, already deployed), `beautifulsoup4` (added in the same prior plan), `requests` (already present), `google_api.py` (existing hermes skill, invoked via subprocess).

**Reference:** Design doc at `docs/superpowers/specs/2026-09-08-galicia-sync-design.md` — read it first for the full reconnaissance findings (confirmed field IDs, confirmed API endpoints, Drive folder structure, dedup strategy) and the prerequisite chain (Playwright/Chromium plan, already completed and deployed).

**Credentials for testing:** All live-testing tasks in this plan require real Galicia credentials set as environment variables on the server (`GALICIA_DNI`, `GALICIA_USER`, `GALICIA_PASSWORD`) before the container can run the script against the real site. These must already be set in Coolify's Environment Variables for the `hermes-agent-infra` resource before Task 2 — if they aren't set yet, STOP and ask the human to set them (do not ask for the values themselves; only confirm the env vars exist).

---

### Task 1: Script skeleton, login, and logout

**Files:**
- Create: `E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py`

- [ ] **Step 1: Write the script skeleton with login and logout**

```python
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
    login failed (still on /login after submit, or an error is shown).
    Never prints or returns the credentials themselves.
    """
    dni = os.environ["GALICIA_DNI"]
    user = os.environ["GALICIA_USER"]
    password = os.environ["GALICIA_PASSWORD"]

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
```

- [ ] **Step 2: Commit**

```bash
cd E:\Repositorios\hermes-agent-infra
git add scripts/galicia_sync.py
git commit -m "Add galicia_sync.py skeleton with login/logout"
```

## Context

The three field IDs used here (`#DocumentNumber`, `#UserName`, `#Password`) and the button role/name (`role="button"`, accessible name `"iniciar sesión"`) are confirmed exact values from live DOM inspection during reconnaissance (see the design doc's "Reconocimiento previo" section) — not guessed. `page.locator("#Password").fill(...)` triggers real keyboard input events, which is required because the site encrypts the password client-side via a JS listener on those events (confirmed: a hidden `EncriptedPassword` field gets populated as you type) — if this task's live test in Task 2 shows login failing where it worked in manual reconnaissance, the first thing to suspect is that `.fill()` doesn't fire the right events and `.type()` (character-by-character) is needed instead; don't guess, check by testing.

The failure-detection logic (`"/login" not in page.url`) matches what reconnaissance observed: a successful login redirects to `/inicio`, a failed one stays on `/login`.

## Before You Begin

If you have questions about the requirements, ask them now.

## Your Job

1. Create the file exactly as specified.
2. Commit with the exact message given.
3. Self-review (see below).
4. Report back.

Work from: `E:\Repositorios\hermes-agent-infra`

## Self-Review

Confirm the file is syntactically valid Python (`python -m py_compile scripts/galicia_sync.py`) and that the commit succeeded.

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- What you did
- Commit SHA
- Any issues

---

### Task 2: Live test of login and logout

**Files:** none (live test against the real site, inside the real hermes container)

- [ ] **Step 1: Confirm `GALICIA_DNI`, `GALICIA_USER`, `GALICIA_PASSWORD` are set in the container**

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> printenv | grep -c GALICIA_"
```

(Get `<hermes-container-name>` from `sudo docker ps --format '{{.Names}}' | grep '^hermes-e'`.) Expected: `3`. If it's less than 3, STOP — these must be set in Coolify's Environment Variables for the `hermes-agent-infra` resource first (as Secret) — do not proceed, and do not ask for the values, just report which ones are missing.

- [ ] **Step 2: Copy the script into the container and run it**

```bash
scp E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py opc@oracle-us-west:/tmp/galicia_sync_test.py
ssh opc@oracle-us-west "sudo docker cp /tmp/galicia_sync_test.py <hermes-container-name>:/tmp/galicia_sync_test.py"
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/python /tmp/galicia_sync_test.py; echo EXIT_CODE:\$?"
```

Expected: `EXIT_CODE:0` and no stdout printed (silent success — login worked, logout ran, nothing failed). If it prints `Galicia: no se pudo iniciar sesión, revisar manualmente` and exits 1, the login didn't work — see the debugging note below, don't just retry blindly.

- [ ] **Step 2a (only if Step 2 failed): debug the login**

Add temporary debug output (do not commit this) to see what's happening — SSH in, open a Python shell inside the container, and run the login steps interactively:

```bash
ssh opc@oracle-us-west "sudo docker exec -it <hermes-container-name> /opt/hermes/.venv/bin/python3"
```

Inside that Python shell:
```python
import os
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
browser = p.chromium.launch(args=["--no-sandbox"])
page = browser.new_page()
page.goto("https://onlinebanking.bancogalicia.com.ar/login")
page.locator("#DocumentNumber").fill(os.environ["GALICIA_DNI"])
page.locator("#UserName").fill(os.environ["GALICIA_USER"])
page.locator("#Password").fill(os.environ["GALICIA_PASSWORD"])
page.get_by_role("button", name="iniciar sesión").click()
page.wait_for_load_state("networkidle")
print(page.url)
print(page.content()[:2000])
```

Read the printed URL and HTML snippet to understand what actually happened (an error message shown on the page, a different URL than expected, etc.) — report the exact finding rather than guessing at a fix. Common suspects: `.fill()` not triggering the client-side encryption listener (try `.type()` instead, character by character, as a fix if this is the cause), or a session/cookie issue from a previous test run still being logged in (try `page.context.clear_cookies()` before navigating, or use `browser.new_context()` for a fresh session).

- [ ] **Step 3: Clean up the test file**

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> rm -f /tmp/galicia_sync_test.py; rm -f /tmp/galicia_sync_test.py"
```

## Context

This is the first live test against the real banking site from the actual script code (not the interactive Playwright MCP session used during reconnaissance). It is read-only (login + immediate logout, no data extraction yet) so the risk is low, but it IS a real login attempt against a real account with real credentials — if Step 2a's debugging reveals something unexpected (a CAPTCHA, a security challenge, anything not seen during reconnaissance), STOP and report BLOCKED rather than trying multiple login attempts in a row (repeated failed/probing logins could trigger the bank's fraud detection).

## Before You Begin

If you have questions, ask them now.

## Your Job

1. Confirm env vars are set (Step 1) — stop if not.
2. Run the live test (Step 2).
3. If it fails, debug once (Step 2a) — report findings, don't loop retrying.
4. Clean up.
5. Report back.

## When You're in Over Your Head

STOP and escalate (BLOCKED) if: env vars aren't set, the login fails for a reason you can't diagnose from one debug pass, or anything suggests the bank flagged this as suspicious activity (unexpected security prompt, account lock message, etc.).

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- Evidence (exit code, stdout, and if debugging was needed, what you found)
- Any issues or concerns

---

### Task 3: Investigate and implement account movements extraction

**Files:**
- Modify: `E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py`
- Create: `E:\Repositorios\hermes-agent-infra\scripts\fixtures\cuentas_movimientos_sample.html` (a real, anonymized-if-needed HTML capture used for a fast repeatable test — see Step 3)

- [ ] **Step 1: Capture the real HTML of the accounts overview and one account's movements page**

Using the same live Python shell approach as Task 2 Step 2a (or extend the test script temporarily), after a successful login:

```python
page.goto("https://cuentas.bancogalicia.com.ar/cuentas/inicio")
page.wait_for_load_state("networkidle")
accounts_html = page.content()
with open("/tmp/cuentas_inicio.html", "w", encoding="utf-8") as f:
    f.write(accounts_html)

# Click into the first account (adapt the selector based on what accounts_html
# shows — reconnaissance found these as elements with an aria-label starting
# "Acceso a Caja Ahorro", but the real underlying tag/class needs confirming
# from this captured HTML, not assumed)
```

Pull `/tmp/cuentas_inicio.html` back with `scp` (via `docker cp` out of the container first) and read it locally to find: (a) the real selector for each account's clickable element (tag name, class, or a stable `data-*` attribute — not just the `aria-label` text, which is fragile), and (b) once navigated into an account's movements page, capture that page's HTML the same way and find the real selector/structure for each movement row (what tag wraps a single movement, and how date/description/amount are nested inside it).

- [ ] **Step 2: Save one real, representative account-movements HTML page as a test fixture**

```bash
mkdir -p E:\Repositorios\hermes-agent-infra\scripts\fixtures
```

Copy the captured movements-page HTML (from Step 1) to
`E:\Repositorios\hermes-agent-infra\scripts\fixtures\cuentas_movimientos_sample.html`. If the real page contains the user's actual account number/balance/transaction data, that is expected and fine to keep as a local fixture — but confirm this file is covered by `.gitignore` before committing anything else in this task (check `E:\Repositorios\hermes-agent-infra\.gitignore` for a `scripts/fixtures/` or similar exclusion; if none exists, add one — real account data must never be committed to this repo).

- [ ] **Step 3: Write the discovery + parsing functions based on what Step 1 found**

Using the REAL selectors/structure found in Step 1 (not the ones shown below, which are illustrative placeholders for the shape of the function — replace them with what you actually observed), add to `galicia_sync.py`:

```python
from bs4 import BeautifulSoup


def discover_accounts(page: Page) -> list[dict]:
    """Returns a list of {"name": str, "number": str, "url": str} for
    each account found on the accounts overview page. `url` is the
    direct link to that account's movements page if one exists in the
    HTML, otherwise None (meaning the caller must click through instead
    of navigating directly — determine which is true from what Step 1
    actually found)."""
    page.goto("https://cuentas.bancogalicia.com.ar/cuentas/inicio")
    page.wait_for_load_state("networkidle")
    soup = BeautifulSoup(page.content(), "html.parser")
    # Replace this with the real selector found in Step 1.
    raise NotImplementedError("Fill in with the real selector from Step 1's HTML capture")


def parse_movements(html: str) -> list[dict]:
    """Returns a list of {"date": str, "description": str, "amount": str}
    for each movement found in an account's movements-page HTML."""
    soup = BeautifulSoup(html, "html.parser")
    # Replace this with the real selector found in Step 1.
    raise NotImplementedError("Fill in with the real selector from Step 1's HTML capture")


def get_account_movements_html(page: Page, account: dict) -> str:
    """Navigates to the given account's movements page (using account["url"]
    if discover_accounts found a direct link, otherwise by clicking through
    from the accounts overview page — determine which applies from what
    Step 1 actually found) and returns the page's HTML, ready for
    parse_movements()."""
    if account.get("url"):
        page.goto(account["url"])
    else:
        # Replace this with the real click-through logic found in Step 1
        # (e.g. page.get_by_text(account["name"]).click() — confirm the
        # real selector, don't assume this one works without checking).
        raise NotImplementedError("Fill in with the real click-through found in Step 1")
    page.wait_for_load_state("networkidle")
    return page.content()
```

Replace all three `raise NotImplementedError(...)` bodies with real logic once Step 1's findings are in hand — do not leave them as stubs when this task is reported DONE. `get_account_movements_html` is the function Task 9's `main()` calls per account; make sure its signature (`(page, account) -> str`) matches exactly.

- [ ] **Step 4: Write a fast, repeatable test against the saved fixture (no live login needed)**

```python
def test_parse_movements_from_fixture():
    with open("scripts/fixtures/cuentas_movimientos_sample.html", encoding="utf-8") as f:
        html = f.read()
    movements = parse_movements(html)
    assert len(movements) > 0, "Expected at least one movement in the fixture"
    for m in movements:
        assert m["date"], "Every movement needs a non-empty date"
        assert m["description"], "Every movement needs a non-empty description"
        assert m["amount"], "Every movement needs a non-empty amount"
    print(f"OK: parsed {len(movements)} movements from fixture")


if __name__ == "__main__":
    test_parse_movements_from_fixture()
```

Put this in a new file `E:\Repositorios\hermes-agent-infra\scripts\test_galicia_parsing.py` that imports `parse_movements` from `galicia_sync.py`.

- [ ] **Step 5: Run the test**

```bash
cd E:\Repositorios\hermes-agent-infra
python scripts/test_galicia_parsing.py
```

Expected: `OK: parsed N movements from fixture` with N > 0. If `parse_movements` raises or returns an empty list, the selector from Step 1 was wrong — go back and re-examine the captured HTML, don't guess a second selector without looking again.

- [ ] **Step 6: Commit**

```bash
git add scripts/galicia_sync.py scripts/test_galicia_parsing.py scripts/fixtures/.gitignore
git add -f scripts/fixtures/cuentas_movimientos_sample.html  # only if you added a fixtures-specific gitignore rule that would otherwise exclude it — otherwise plain `git add`
git commit -m "Implement account discovery and movements parsing, with fixture test"
```

## Context

This is the task where reconnaissance's limitation matters most: everything we know about the movements page came from Playwright's accessibility-tree snapshot (ARIA roles and accessible names), not the real underlying HTML tags/classes. That abstraction is reliable for a human clicking through a page, but not precise enough to write a BeautifulSoup selector against — BeautifulSoup parses raw HTML, and needs to know real tag names and classes, which the accessibility snapshot doesn't expose. That's why this task starts with a real HTML capture instead of jumping straight to writing the parser.

## Before You Begin

If you have questions, ask them now — especially if the login from Task 2 needs to be redone to get a fresh authenticated session for the HTML capture in Step 1 (sessions may expire between tasks).

## Your Job

1. Get a fresh authenticated session (redo login if needed — same env vars, same script pattern as Task 2).
2. Capture real HTML (Step 1), save a fixture (Step 2).
3. Write real parsing code based on what you found (Step 3) — no stubs left behind.
4. Write and run the fixture test (Steps 4-5).
5. Commit (Step 6), making sure the fixture with real account data is excluded from git or explicitly reviewed for what it contains before committing.
6. Report back.

## When You're in Over Your Head

STOP and escalate (BLOCKED) if: the accounts page structure is fundamentally different from what reconnaissance described (e.g. no accessible list of accounts at all, requiring a totally different discovery approach), or if you're not confident the parser is correct after two attempts.

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- What the real HTML structure turned out to be (brief — the actual tag/class pattern found)
- Test output
- Commit SHA
- Confirmation that the fixture file doesn't leak sensitive data into git (or that it's gitignored)
- Any issues

---

### Task 4: Drive dedup and upload for account movements

**Files:**
- Modify: `E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py`

- [ ] **Step 1: Confirm the exact google_api.py invocation and output format**

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/python /opt/hermes/skills/productivity/google-workspace/scripts/google_api.py drive search 'name contains \"test\"' --raw-query --max 3"
```

Read the actual output format (is it JSON? plain text? one result per line?) — this determines how the code in Step 2 parses the response. Do not assume a format without seeing real output.

- [ ] **Step 2: Write the dedup + upload function, using the real output format from Step 1**

```python
import csv
import io
import re
import subprocess

GOOGLE_API_SCRIPT = "/opt/hermes/skills/productivity/google-workspace/scripts/google_api.py"
VENV_PYTHON = "/opt/hermes/.venv/bin/python"


def slugify(name: str) -> str:
    """caja_ahorro_pesos, mastercard_3652, etc."""
    s = name.lower()
    s = (s.replace("á", "a").replace("é", "e").replace("í", "i")
           .replace("ó", "o").replace("ú", "u"))
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


def drive_find_file(name: str, parent_folder_id: str) -> str | None:
    """Returns the file_id if a file with this exact name exists directly
    under parent_folder_id, else None."""
    query = f"name = '{name}' and '{parent_folder_id}' in parents and trashed = false"
    result = subprocess.run(
        [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "search", query, "--raw-query"],
        capture_output=True, text=True, check=True,
    )
    # Replace this with real parsing of Step 1's confirmed output format.
    raise NotImplementedError("Fill in based on the real output format from Step 1")


def sync_account_movements_csv(account_name: str, new_movements: list[dict], parent_folder_id: str) -> None:
    """Downloads the existing CSV for this account (if any), merges in
    only movements not already present (deduped by date+description+amount),
    and re-uploads."""
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

    new_rows = [
        m for m in new_movements
        if (m["date"], m["description"], m["amount"]) not in existing_rows
    ]

    if not new_rows and existing_file_id:
        return  # nothing new, nothing to upload

    with open(local_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "description", "amount"])
        writer.writeheader()
        for row in sorted(existing_rows | {(m["date"], m["description"], m["amount"]) for m in new_movements}):
            writer.writerow({"date": row[0], "description": row[1], "amount": row[2]})

    subprocess.run(
        [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "upload", local_path,
         "--name", filename, "--parent", parent_folder_id],
        capture_output=True, text=True, check=True,
    )
    os.remove(local_path)
```

Adjust `drive_find_file`'s parsing to match Step 1's real output — and confirm separately whether `drive upload` creates a duplicate when a file with the same name already exists in that folder, or genuinely requires deleting the old one first (test this empirically in Task 5, not assumed here — if it creates a duplicate, this function needs an added `drive delete <existing_file_id>` call before the upload).

- [ ] **Step 3: Commit**

```bash
git add scripts/galicia_sync.py
git commit -m "Add Drive dedup/upload for account movements CSVs"
```

## Context

This depends on Task 3's `parse_movements` output shape (`{"date", "description", "amount"}` dicts) and on knowing exactly how `google_api.py drive search/download/upload` behave — Step 1 of this task exists specifically to stop guessing about that CLI's output format, the same discipline used throughout this project (verify empirically, don't assume). The Drive folder structure is `Bancos/Galicia/Cuentas/<account>.csv` per the design doc — you'll need the sub-folder IDs for `Cuentas` and (in a later task) `Tarjetas`; if they don't exist yet under the `Bancos` folder (ID `1wNYrxgVJo6MPN4kc7aZK3q8qi9s9ZlJS`), create them with `google_api.py drive create-folder` and record the resulting IDs as constants in the script.

## Before You Begin

If you have questions, ask them now.

## Your Job

1. Confirm the real CLI output format (Step 1).
2. Implement dedup/upload using that real format (Step 2) — no stubs left in `drive_find_file`.
3. Commit.
4. Report back.

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- The real output format found in Step 1
- Commit SHA
- Any issues

---

### Task 5: End-to-end test of account sync against the real site and real Drive

**Files:** none (live test)

- [ ] **Step 1: Ensure the `Galicia/Cuentas` Drive subfolder exists**

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/python /opt/hermes/skills/productivity/google-workspace/scripts/google_api.py drive create-folder Galicia --parent 1wNYrxgVJo6MPN4kc7aZK3q8qi9s9ZlJS"
```

Record the returned folder ID, then create `Cuentas` inside it the same way, recording that ID too. Update the constants in `galicia_sync.py` (add `CUENTAS_FOLDER_ID = "..."` near `DRIVE_ROOT_FOLDER_ID`) with the real IDs — do not hardcode a guessed ID.

- [ ] **Step 2: Wire discovery + parsing + Drive sync into a temporary test run**

Add a temporary block to the bottom of `galicia_sync.py`'s `main()` (after the login check, before `return 0`) that calls `discover_accounts`, then for each account, `parse_movements` on that account's page, then `sync_account_movements_csv`. Run it:

```bash
scp E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py opc@oracle-us-west:/tmp/galicia_sync_test.py
ssh opc@oracle-us-west "sudo docker cp /tmp/galicia_sync_test.py <hermes-container-name>:/tmp/galicia_sync_test.py"
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/python /tmp/galicia_sync_test.py; echo EXIT_CODE:\$?"
```

Expected: `EXIT_CODE:0`, no stdout (silent success).

- [ ] **Step 3: Verify in Drive**

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/python /opt/hermes/skills/productivity/google-workspace/scripts/google_api.py drive search \"'<CUENTAS_FOLDER_ID>' in parents\" --raw-query"
```

Expected: one `.csv` file per discovered account, with real data.

- [ ] **Step 4: Run it a SECOND time immediately and confirm no duplicates**

Repeat Step 2's run command exactly. Then repeat Step 3's search — expected: the SAME number of files (not doubled), and if you download one and inspect it, the same rows (not duplicated). If `drive upload` created a second file with the same name instead of replacing the first, go back to Task 4 and add a `drive delete` of the old file_id before uploading — this is the empirical confirmation that task's Step 2 flagged as needing a test.

- [ ] **Step 5: Clean up test artifacts on the server**

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> rm -f /tmp/galicia_sync_test.py; rm -f /tmp/galicia_sync_test.py"
```

## Context

This is the first true end-to-end test of the account-sync path — real login, real parsing, real Drive writes. Do it once, verify carefully (especially the no-duplicates check in Step 4), rather than assuming Task 3 and 4's unit-level correctness transfers automatically to the integrated flow.

## Your Job

1. Set up the Drive folder structure with real IDs (Step 1).
2. Run the integrated flow twice (Steps 2 and 4), verifying Drive state after each (Steps 3 and 4).
3. Fix `sync_account_movements_csv` in `galicia_sync.py` if duplicates appear (this means committing a fix, not just noting it).
4. Clean up.
5. Report back.

## When You're in Over Your Head

STOP and escalate if login fails unexpectedly (see Task 2's guidance on not retrying logins in a loop) or if Drive writes behave in a way you can't explain.

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- Evidence for each step
- Whether a duplicate-handling fix was needed, and if so, the commit SHA for it
- Any issues

---

### Task 6: Card statement (PDF) sync via the confirmed API

**Files:**
- Modify: `E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py`

- [ ] **Step 1: Add the card discovery, cookie-extraction, and resumen-sync functions**

```python
import requests


def discover_cards(page: Page) -> list[dict]:
    """Returns a list of {"brand": str, "last4": str} for each card
    found on the cards overview page."""
    page.goto("https://onlinebanking.bancogalicia.com.ar/navigation/menulink/390")
    page.wait_for_load_state("networkidle")
    soup = BeautifulSoup(page.content(), "html.parser")
    cards = []
    for p_tag in soup.find_all("p"):
        text = p_tag.get_text(strip=True)
        if text.startswith("****") and text.count("*") >= 12:
            last4 = text.replace("*", "").replace(" ", "")
            cards.append({"brand": "unknown", "last4": last4})
    return cards


def session_from_page(page: Page) -> requests.Session:
    """Builds a requests.Session carrying the Playwright page's current
    cookies, for hitting the JSON API endpoints directly."""
    session = requests.Session()
    for cookie in page.context.cookies():
        session.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"])
    return session


def sync_card_statements(session: requests.Session, last4: str, tarjeta_folder_id: str, from_date: str, end_date: str) -> None:
    list_url = f"https://tarjetas.bancogalicia.com.ar/api/resumen/list?from={from_date}&end={end_date}"
    resp = session.get(list_url)
    resp.raise_for_status()
    statements = resp.json()  # confirm this is really a JSON list/dict — check in Task 7's live test, adjust if not

    for statement in statements:
        # The exact field names in each statement dict aren't confirmed —
        # this is filled in based on what Task 7's live test actually
        # returns, not guessed here.
        pass
```

Leave the loop body as a comment for now — Task 7 fills it in with the real field names once the actual JSON shape is seen live.

- [ ] **Step 2: Commit**

```bash
git add scripts/galicia_sync.py
git commit -m "Add card discovery and cookie-session scaffolding for resumen sync"
```

## Context

`discover_cards`'s parsing (`text.startswith("****")`) is based on what reconnaissance's accessibility snapshot showed (`"**** **** **** 8477"` as a paragraph's text content) — this one specific case is likely safe to write ahead of a live HTML capture because it's matching on literal visible text content, not a CSS class/tag structure, so it doesn't have the same "invented precision" risk as the movements parser in Task 3. Still confirm it works for real in Task 7's live test rather than assuming.

The two confirmed real API endpoints (from reconnaissance) are:
- `GET https://tarjetas.bancogalicia.com.ar/api/resumen/list?from=YYYY-MM-DD&end=YYYY-MM-DD`
- `POST https://tarjetas.bancogalicia.com.ar/api/resumen/getresumen`

Reconnaissance confirmed these work with the session's cookies and that a real PDF was successfully downloaded through them (via browser UI, not yet via raw `requests` — Task 7 confirms the raw HTTP version works too).

## Before You Begin

If you have questions, ask them now.

## Your Job

1. Add the scaffolding functions as specified (the incomplete loop body in `sync_card_statements` is intentional — leave it, do not invent field names).
2. Commit.
3. Report back.

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- Commit SHA
- Any issues

---

### Task 7: Investigate the real resumen/list response shape and complete the sync

**Files:**
- Modify: `E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py`

- [ ] **Step 1: Get a fresh authenticated session and call the real API**

Using the same live-Python-shell pattern as Task 2 Step 2a:

```python
import os, json
from playwright.sync_api import sync_playwright
p = sync_playwright().start()
browser = p.chromium.launch(args=["--no-sandbox"])
page = browser.new_page()
page.goto("https://onlinebanking.bancogalicia.com.ar/login")
page.locator("#DocumentNumber").fill(os.environ["GALICIA_DNI"])
page.locator("#UserName").fill(os.environ["GALICIA_USER"])
page.locator("#Password").fill(os.environ["GALICIA_PASSWORD"])
page.get_by_role("button", name="iniciar sesión").click()
page.wait_for_load_state("networkidle")

import requests
session = requests.Session()
for c in page.context.cookies():
    session.cookies.set(c["name"], c["value"], domain=c["domain"])

resp = session.get("https://tarjetas.bancogalicia.com.ar/api/resumen/list?from=2026-04-08&end=2026-09-08")
print(resp.status_code)
print(json.dumps(resp.json(), indent=2)[:3000])
```

Read the real field names in the response (e.g. is the statement date under `"fecha"`, `"date"`, something else? Is there a statement ID field needed for the `getresumen` call?).

- [ ] **Step 2: Confirm the `getresumen` POST body shape**

Try calling it with what Step 1's list response suggests as the identifying field(s) for one statement:

```python
resp2 = session.post("https://tarjetas.bancogalicia.com.ar/api/resumen/getresumen", json={"<field-from-step-1>": "<value-from-step-1>"})
print(resp2.status_code, resp2.headers.get("content-type"), len(resp2.content))
```

Expected: a `200` with `content-type` indicating a PDF (`application/pdf` or similar) and non-trivial byte length. If it 400s/404s, the body shape guessed above is wrong — inspect the network request more carefully (re-run the UI-driven download from reconnaissance if needed, this time capturing the real request body via Playwright's request/response interception, not just the URL).

- [ ] **Step 3: Complete `sync_card_statements` in galicia_sync.py with the real field names**

Fill in the loop body from Task 6, e.g. (adjust field names to what Step 1 actually showed):

```python
def sync_card_statements(session: requests.Session, last4: str, tarjeta_folder_id: str, from_date: str, end_date: str) -> None:
    list_url = f"https://tarjetas.bancogalicia.com.ar/api/resumen/list?from={from_date}&end={end_date}"
    resp = session.get(list_url)
    resp.raise_for_status()
    statements = resp.json()

    for statement in statements:
        filename = f"RESUMEN_{last4}_{statement['<month-field>']}_{statement['<year-field>']}.pdf"
        if drive_find_file(filename, tarjeta_folder_id):
            continue  # already synced

        pdf_resp = session.post(
            "https://tarjetas.bancogalicia.com.ar/api/resumen/getresumen",
            json={"<field-from-step-1>": statement["<field-from-step-1>"]},
        )
        pdf_resp.raise_for_status()
        local_path = f"/tmp/{filename}"
        with open(local_path, "wb") as f:
            f.write(pdf_resp.content)

        subprocess.run(
            [VENV_PYTHON, GOOGLE_API_SCRIPT, "drive", "upload", local_path,
             "--name", filename, "--parent", tarjeta_folder_id],
            capture_output=True, text=True, check=True,
        )
        os.remove(local_path)
```

- [ ] **Step 4: Test it live**

```bash
scp E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py opc@oracle-us-west:/tmp/galicia_sync_test.py
ssh opc@oracle-us-west "sudo docker cp /tmp/galicia_sync_test.py <hermes-container-name>:/tmp/galicia_sync_test.py"
```

Add a temporary call to `sync_card_statements` in `main()` for one discovered card (using a `Tarjetas/<last4>` Drive subfolder created the same way as Task 5 Step 1), run it, and confirm a real PDF lands in Drive. Run it a second time and confirm it does NOT re-download (the `drive_find_file` check should skip it).

- [ ] **Step 5: Commit**

```bash
git add scripts/galicia_sync.py
git commit -m "Complete card statement sync with confirmed API response fields"
```

## Context

This task exists because Task 6 deliberately left the statement-processing loop empty — the real JSON field names from `/api/resumen/list` were never captured during reconnaissance (only that the endpoint exists and returns 200). Find them for real here rather than guessing.

## Before You Begin

If you have questions, ask them now — including if a fresh login is needed (sessions may have expired since Task 6).

## Your Job

1. Investigate the real API response (Steps 1-2).
2. Complete the implementation with real field names (Step 3) — no placeholder field names left in committed code.
3. Test live, twice, confirming no re-download on the second run (Step 4).
4. Commit (Step 5).
5. Report back.

## When You're in Over Your Head

STOP and escalate if the API behaves in a way that doesn't match reconnaissance's findings at all (e.g. requires a different auth mechanism than cookies), or if you can't get a valid PDF back after two attempts at the request body shape.

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- The real field names found in the API response
- Test evidence (first run downloads, second run skips)
- Commit SHA
- Any issues

---

### Task 8: Investigate and implement card movements extraction

**Files:**
- Modify: `E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py`
- Create: `E:\Repositorios\hermes-agent-infra\scripts\fixtures\tarjeta_movimientos_sample.html` (if the investigation finds this needs HTML parsing; skip if it finds a clean API instead)

- [ ] **Step 1: Investigate whether card movements have a clean API, same method as Task 7**

Get a fresh session (same pattern as Task 7 Step 1), navigate to a card's overview page
(`https://onlinebanking.bancogalicia.com.ar/navigation/menulink/390`, then whichever
URL a card's own page turns out to be — follow the same navigation reconnaissance
did originally), and inspect network requests during that navigation and while
clicking "Mostrar más" on the movements list. If Playwright's `page.on("request", ...)` /
`page.on("response", ...)` event handlers are available in this Python environment, use
them to log request URLs during that interaction — this is the equivalent of the
`browser_network_requests` tool used during interactive reconnaissance, now done
from a plain script.

- [ ] **Step 2: Branch based on what Step 1 found**

**If a clean API exists** (similar to the resumen one): implement `sync_card_movements` following the same pattern as `sync_card_statements` in Task 7 — a `requests` call, real field names confirmed empirically, dedup by (date, description, amount) same as account movements, write to `Tarjetas/<last4>/movimientos.csv`.

**If no API exists** (HTML-rendered like account movements): capture real HTML the same way as Task 3 Step 1, save a fixture at `scripts/fixtures/tarjeta_movimientos_sample.html`, write `parse_card_movements(html: str) -> list[dict]` using BeautifulSoup against the REAL structure found (not guessed), and handle the "Mostrar más" pagination by clicking it in a loop with Playwright until it's no longer present or no new content loads, capturing each page's HTML and parsing all of them.

- [ ] **Step 3: Write a fixture test (only if the HTML-parsing branch was taken)**

Same pattern as Task 3 Step 4 — add a test function to `scripts/test_galicia_parsing.py` that parses the saved fixture and asserts non-empty, well-formed results.

- [ ] **Step 4: Test live end-to-end, dedup CSV sync included**

Wire `sync_card_movements` (or the API+dedup flow, whichever branch) into a temporary test run the same way as prior live-test tasks, run it twice, confirm no duplicates in the resulting `movimientos.csv` per card.

- [ ] **Step 5: Commit**

```bash
git add scripts/galicia_sync.py scripts/test_galicia_parsing.py scripts/fixtures/tarjeta_movimientos_sample.html
git commit -m "Implement card movements extraction (API or HTML, per investigation) with dedup"
```

(Omit the fixture file from this commit if the API branch was taken instead — there's nothing to save in that case.)

## Context

This is the one piece of the design the spec explicitly flagged as unconfirmed ("no se investigó a fondo" in the design doc's reconnaissance section) — you are doing the investigation this plan always intended to happen before implementation, not skipping a step that was already done.

## Before You Begin

If you have questions, ask them now.

## Your Job

1. Investigate (Step 1), branch appropriately (Step 2).
2. Implement based on real findings — no guessed selectors or field names.
3. Test (Steps 3-4), confirming dedup works on a second run.
4. Commit (Step 5).
5. Report back, including which branch was taken and why.

## When You're in Over Your Head

STOP and escalate if the investigation is inconclusive after a reasonable attempt, or if the pagination ("Mostrar más") behaves in a way that risks an infinite loop or excessive requests against the real site — cap any pagination loop at a small fixed number of iterations (e.g. 20) and report if that cap is hit, rather than looping unbounded.

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- Which branch was taken (API vs HTML) and the evidence for that decision
- Test results (first run vs second run, confirming dedup)
- Commit SHA
- Any issues

---

### Task 9: Wire the full flow together with per-section error handling

**Files:**
- Modify: `E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py`

- [ ] **Step 1: Replace the temporary test-only wiring with the real main() flow**

```python
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
                        html = get_account_movements_html(page, account)  # from Task 3
                        movements = parse_movements(html)
                        sync_account_movements_csv(account["name"], movements, CUENTAS_FOLDER_ID)
                    except Exception as e:
                        failures.append(f"cuenta {account['name']}: {e}")
            except Exception as e:
                failures.append(f"descubrimiento de cuentas: {e}")

            try:
                cards = discover_cards(page)
                session = session_from_page(page)
                from_date, end_date = "2026-04-08", "2026-09-08"  # replace with a real rolling 6-month window computed from today's date
                for card in cards:
                    tarjeta_folder_id = ensure_tarjeta_folder(card["last4"])  # create-folder if missing, same pattern as Task 5 Step 1 but at runtime
                    try:
                        sync_card_statements(session, card["last4"], tarjeta_folder_id, from_date, end_date)
                    except Exception as e:
                        failures.append(f"resumen tarjeta {card['last4']}: {e}")
                    try:
                        sync_card_movements(page, session, card, tarjeta_folder_id)  # from Task 8, whichever branch
                    except Exception as e:
                        failures.append(f"movimientos tarjeta {card['last4']}: {e}")
            except Exception as e:
                failures.append(f"descubrimiento de tarjetas: {e}")

            if failures:
                print("Galicia: sync parcial, falló: " + "; ".join(failures))
                return 1

            return 0
        finally:
            logout(page)
            browser.close()
```

Add a real `ensure_tarjeta_folder(last4: str) -> str` helper following the same `drive create-folder` pattern used manually in Tasks 5 and 7, and a real `from_date`/`end_date` computed via `datetime` for a rolling 6-month window instead of the hardcoded example dates shown above.

- [ ] **Step 2: Run the full script live, end to end, twice**

```bash
scp E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py opc@oracle-us-west:/tmp/galicia_sync_test.py
ssh opc@oracle-us-west "sudo docker cp /tmp/galicia_sync_test.py <hermes-container-name>:/tmp/galicia_sync_test.py"
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/python /tmp/galicia_sync_test.py; echo EXIT_CODE:\$?"
```

Expected first run: `EXIT_CODE:0`, silent (assuming everything from prior tasks works together). Run it again immediately — expected: still `EXIT_CODE:0`, silent, and Drive state unchanged (full idempotency check across the whole flow, not just individual pieces).

- [ ] **Step 3: Commit**

```bash
git add scripts/galicia_sync.py
git commit -m "Wire full Galicia sync flow with per-section error isolation"
```

## Context

This integrates everything from Tasks 1-8 into the real, final `main()`. Per-section `try/except` means a failure extracting one account or one card doesn't stop the others — matching the design doc's explicit requirement that partial failures still let the rest of the sync complete, with a summary printed at the end (which becomes a WhatsApp message via hermes's `--deliver origin`, since non-empty stdout is not silent).

## Before You Begin

If you have questions, ask them now.

## Your Job

1. Wire the full flow (Step 1), filling in `ensure_tarjeta_folder` and a real rolling date window.
2. Test twice end-to-end (Step 2), confirming full-flow idempotency.
3. Commit (Step 3).
4. Report back.

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- Evidence from both runs
- Commit SHA
- Any issues

---

### Task 10: Deploy the script to the persistent scripts directory and register the cron job

**Files:** none (deployment + cron registration)

- [ ] **Step 1: Copy the final script to the persistent volume path**

```bash
ssh opc@oracle-us-west "sudo docker cp E:\Repositorios\hermes-agent-infra\scripts\galicia_sync.py <hermes-container-name>:/opt/data/scripts/galicia_sync.py"
```

(Create `/opt/data/scripts/` first if it doesn't exist: `ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> mkdir -p /opt/data/scripts"`.)

- [ ] **Step 2: Run it once from its real location to confirm the path works**

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/python /opt/data/scripts/galicia_sync.py; echo EXIT_CODE:\$?"
```

Expected: `EXIT_CODE:0`, silent.

- [ ] **Step 3: Register the cron job**

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/hermes cron create '0 9 * * *' --name 'Sync Galicia' --script galicia_sync.py --no-agent --deliver origin"
```

Note: `0 9 * * *` is UTC in most cron implementations, but confirm what timezone hermes's scheduler uses (check `hermes cron doctor` or the job's listed next-run time after creation) — the requirement is 9am **Argentina time** (UTC-3), so the cron expression may need to be `0 12 * * *` (UTC) instead if hermes's scheduler runs in UTC. Verify with:

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/hermes cron list"
```

Check the displayed next-run time against what 9am Argentina time should be right now, and adjust the cron expression if it's off by the UTC offset.

- [ ] **Step 4: Note this in the README**

Add a brief mention to `E:\Repositorios\hermes-agent-infra\README.md` (near wherever cron jobs or scripts are otherwise documented, or in a new short section if none exists) that `scripts/galicia_sync.py` exists in this repo for review, deploys to `/opt/data/scripts/` (persistent volume, survives redeploys — this is NOT copied automatically by any deploy step, it must be re-copied manually if `hermes-data` is ever recreated), and runs daily via the `Sync Galicia` cron job.

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "Document galicia_sync.py deployment and cron registration"
```

## Context

This is the final task — everything built and tested in Tasks 1-9 gets deployed to its real running location and scheduled. `/opt/data/scripts/` lives in the `hermes-data` volume (not `hermes-agent-src`), which is NOT wiped on hermes-agent version upgrades (unlike `hermes-agent-src`) — but it's also not populated by any Dockerfile/build step, so a full volume loss (not expected in normal operation) would require re-running Step 1 of this task manually.

## Before You Begin

If you have questions, ask them now.

## Your Job

1. Deploy the script to its real path (Step 1), confirm it runs from there (Step 2).
2. Register the cron job with the correct timezone-adjusted schedule (Step 3).
3. Document the deployment (Step 4).
4. Commit the doc update (Step 5).
5. Report back.

## Report Format

Report:
- **Status:** DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT
- Confirmation the script runs from `/opt/data/scripts/`
- The final cron schedule expression used, and why (UTC vs local time reasoning)
- Commit SHA
- Any issues

---

## Rollback

If the cron job misbehaves after being registered:

```bash
ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> /opt/hermes/.venv/bin/hermes cron remove 'Sync Galicia'"
```

To remove the script entirely: `ssh opc@oracle-us-west "sudo docker exec <hermes-container-name> rm /opt/data/scripts/galicia_sync.py"`. Neither affects Mnemosyne, Playwright, or any other cron job — this is fully isolated.
