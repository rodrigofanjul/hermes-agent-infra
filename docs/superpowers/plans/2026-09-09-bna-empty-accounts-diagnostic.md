# BNA+ Empty Accounts Diagnostic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make an empty BNA+ accounts page fail safely and use one sanitized live run to isolate whether internal SPA navigation, client state, or the backend causes the discrepancy.

**Architecture:** Keep production parsing in `scripts/bna_sync.py`, but separate account-page classification from browser navigation so it is testable without a bank session. Add a temporary standalone diagnostic runner that imports the production login/navigation functions, records metadata only for `fetch`/XHR responses, performs no Drive writes, and always logs out.

**Tech Stack:** Python 3, Playwright sync API, BeautifulSoup, standard-library `unittest.mock`, existing fixture-style tests.

---

### Task 1: Fail closed on an empty or incomplete accounts page

**Files:**
- Modify: `scripts/test_bna_parsing.py`
- Modify: `scripts/bna_sync.py`

- [ ] **Step 1: Write failing classification tests**

Add these imports and tests to `scripts/test_bna_parsing.py`:

```python
from bna_sync import (
    BNADataUnavailableError,
    classify_accounts_html,
    discover_accounts_from_html,
    parse_account_balance,
    parse_bank_table,
    validate_account_detail,
)


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
```

Import `validate_account_detail` and call the five new tests in the file's
`if __name__ == "__main__":` block.

- [ ] **Step 2: Run the tests and verify the new import fails**

Run:

```powershell
python scripts/test_bna_parsing.py
```

Expected: import failure because `BNADataUnavailableError` and `classify_accounts_html` do not exist.

- [ ] **Step 3: Implement the minimal classifier**

Add this directly above `discover_accounts_from_html` in `scripts/bna_sync.py`:

```python
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


def validate_account_detail(movements: list[dict], balance: str) -> None:
    if not balance:
        raise BNADataUnavailableError("BNA no proporcionó el saldo de la cuenta")
    if not movements:
        raise BNADataUnavailableError("BNA no proporcionó movimientos de la cuenta")
```

Change `discover_accounts(page)` to return `classify_accounts_html(page.content())` instead of calling `discover_accounts_from_html` directly.
Replace the account-detail body in `main()` with this ordering so validation
happens before any Drive mutation:

```python
                        html = get_account_detail_html(page, account)
                        movements = parse_bank_table(html, ["date", "receipt", "description", "amount"])
                        balance = parse_account_balance(html)
                        validate_account_detail(movements, balance)
                        sync_account_movements_csv(account["name"], movements, CUENTAS_FOLDER_ID)
                        sync_account_balance_csv(account["name"], balance, CUENTAS_FOLDER_ID)
```

- [ ] **Step 4: Run the parsing tests**

Run:

```powershell
python scripts/test_bna_parsing.py
```

Expected: all eight test paths complete and the process exits `0`.

- [ ] **Step 5: Remove the disproven User-Agent override**

In `main()`, replace the temporary `default_page`/`real_ua`/custom-context block with:

```python
        page = browser.new_page()
```

This also removes the comment claiming the override restored account data.

- [ ] **Step 6: Verify syntax and diff hygiene**

Run:

```powershell
python -m py_compile scripts/bna_sync.py scripts/test_bna_parsing.py
git diff --check
```

Expected: both commands exit `0`.

- [ ] **Step 7: Commit the fail-closed behavior**

```powershell
git add scripts/bna_sync.py scripts/test_bna_parsing.py
git commit -m "Fail safely when BNA account data is unavailable"
```

---

### Task 2: Reproduce the successful SPA navigation path

**Files:**
- Modify: `scripts/test_bna_parsing.py`
- Modify: `scripts/bna_sync.py`

- [ ] **Step 1: Write a failing navigation contract test**

Add `from unittest.mock import Mock` and this test:

```python
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
```

Import `navigate_to_accounts` from `bna_sync` and call this test in the script entry point.

- [ ] **Step 2: Run the test and verify it fails**

Run:

```powershell
python scripts/test_bna_parsing.py
```

Expected: import failure because `navigate_to_accounts` does not exist.

- [ ] **Step 3: Implement internal navigation and semantic waiting**

Add this function below `ACCOUNTS_URL`:

```python
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
```

Change `discover_accounts(page)` to call `navigate_to_accounts(page)` instead of `page.goto(ACCOUNTS_URL)` and `wait_for_load_state("networkidle")`.

- [ ] **Step 4: Run all offline tests**

Run:

```powershell
python scripts/test_bna_parsing.py
python -m py_compile scripts/bna_sync.py scripts/test_bna_parsing.py
```

Expected: every test passes and compilation exits `0`.

- [ ] **Step 5: Commit the navigation repair**

```powershell
git add scripts/bna_sync.py scripts/test_bna_parsing.py
git commit -m "Navigate to BNA accounts through the authenticated SPA"
```

---

### Task 3: Build the no-data diagnostic runner

**Files:**
- Create: `scripts/bna_diagnose_accounts.py`

- [ ] **Step 1: Create the sanitized diagnostic runner**

Create `scripts/bna_diagnose_accounts.py` with:

```python
#!/usr/bin/env python3
"""One-shot BNA account-loading diagnostic. Never reads or prints response bodies."""
import re
import sys
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from bna_sync import (
    BNADataUnavailableError,
    classify_accounts_html,
    login,
    logout,
    navigate_to_accounts,
)


def sanitized_path(url: str) -> str:
    path = urlsplit(url).path
    return re.sub(r"(?<=/)[A-Za-z0-9_-]{24,}(?=/|$)", "<id>", path)


def main() -> int:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()

        def record_response(response) -> None:
            if response.request.resource_type not in {"fetch", "xhr"}:
                return
            print(
                "HTTP"
                f" type={response.request.resource_type}"
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
            navigate_to_accounts(page)
            try:
                accounts = classify_accounts_html(page.content())
            except BNADataUnavailableError as exc:
                print(f"RESULT accounts_unavailable reason={exc}")
                return 2
            print(f"RESULT accounts_loaded count={len(accounts)}")
            return 0
        finally:
            logout(page)
            browser.close()


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Verify it contains no response-body access**

Run:

```powershell
rg -n "response\.(body|text|json)|Authorization|headers|post_data" scripts/bna_diagnose_accounts.py
```

Expected: no matches.

- [ ] **Step 3: Verify syntax and existing tests**

Run:

```powershell
python -m py_compile scripts/bna_diagnose_accounts.py
python scripts/test_bna_parsing.py
git diff --check
```

Expected: all commands exit `0`.

- [ ] **Step 4: Commit the diagnostic runner**

```powershell
git add scripts/bna_diagnose_accounts.py
git commit -m "Add sanitized BNA accounts diagnostic"
```

---

### Task 4: Execute exactly one controlled live diagnostic

**Files:**
- No repository changes during execution

- [ ] **Step 1: Resolve the current Hermes container without printing its environment**

Run:

```powershell
ssh opc@oracle-us-west "sudo docker ps --format '{{.Names}} {{.Status}}'"
```

Select the single healthy container whose name begins with `hermes-` and assign
its exact name to `$bnaContainer`. Print that value and verify it matches the
listed healthy container before continuing. Do not run `docker inspect`, `env`,
or any command that displays environment variables.

- [ ] **Step 2: Copy only the production module and diagnostic runner to temporary paths**

Run with the verified `$bnaContainer` value:

```powershell
scp scripts/bna_sync.py opc@oracle-us-west:/tmp/bna_sync.py
scp scripts/bna_diagnose_accounts.py opc@oracle-us-west:/tmp/bna_diagnose_accounts.py
ssh opc@oracle-us-west "sudo docker cp /tmp/bna_sync.py ${bnaContainer}:/tmp/bna_sync.py"
ssh opc@oracle-us-west "sudo docker cp /tmp/bna_diagnose_accounts.py ${bnaContainer}:/tmp/bna_diagnose_accounts.py"
```

- [ ] **Step 3: Run the diagnostic exactly once**

Run:

```powershell
ssh opc@oracle-us-west "sudo docker exec $bnaContainer /opt/hermes/.venv/bin/python /tmp/bna_diagnose_accounts.py"
```

Expected: one `RESULT login_ok`, sanitized `HTTP` lines, and exactly one terminal result: `accounts_loaded`, `accounts_unavailable`, or `login_failed`. Do not rerun regardless of the result.

- [ ] **Step 4: Remove both temporary copies from the container and server**

Run with explicit paths and the verified container name:

```powershell
ssh opc@oracle-us-west "sudo docker exec $bnaContainer rm -f /tmp/bna_sync.py /tmp/bna_diagnose_accounts.py"
ssh opc@oracle-us-west "rm -f /tmp/bna_sync.py /tmp/bna_diagnose_accounts.py"
```

- [ ] **Step 5: Classify the result without another login**

Use this decision table:

| Evidence | Conclusion | Next action |
|---|---|---|
| `accounts_loaded` | Hard navigation was the cause | Keep SPA navigation and resume Task 4's live Drive verification in a separately approved run |
| An account-data request has HTTP 4xx/5xx | Backend/session boundary failed | Report endpoint/status and investigate that boundary without bypass techniques |
| Account-data requests are HTTP 2xx but result is explicitly empty | Server accepted the session but returned no accounts in Oracle | Keep fail-closed behavior; recommend an approved local/residential execution environment |
| No account-data fetch/XHR occurs | Client navigation/state failed | Inspect the link result and SPA route offline from captured metadata; do not relogin |
| `login_failed` | Authentication failed | Stop and report; do not consume another attempt |

The diagnostic outcome is the deliverable of this plan. Any environment migration or endpoint-specific repair requires a new, evidence-based design before further live authentication.

---

### Task 5: Final verification and handoff

**Files:**
- No expected modifications

- [ ] **Step 1: Run the complete offline verification**

```powershell
python scripts/test_bna_parsing.py
python -m py_compile scripts/bna_sync.py scripts/bna_diagnose_accounts.py
git diff --check
git status --short
```

Expected: tests and compilation pass. `git status --short` may show only the pre-existing `scripts/__pycache__/` directory; it must not show temporary diagnostics containing live output.

- [ ] **Step 2: Report evidence**

Report the terminal `RESULT`, the sanitized endpoint/status sequence, the conclusion selected from Task 4's table, test output, commit SHAs, and confirmation that temporary remote files were removed. Do not include response bodies or financial data.
