#!/usr/bin/env python3
"""One-shot BNA account-loading diagnostic. Never reads response bodies."""
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
    """Return a query-free path with long opaque segments redacted."""
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
