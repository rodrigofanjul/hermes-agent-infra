#!/usr/bin/env python3
"""Daily card-movements -> Google Sheet ledger sync.

Reads the tarjeta_*.csv files that galicia_sync.py and bna_sync.py already
drop into Drive (Bancos/Galicia/Tarjetas and Bancos/BNA/Tarjetas), normalizes
every row into a common schema, and appends only the rows not already present
in the "Hoja 1" ledger tab of Rodrigo's gastos Sheet (dedup by full row
tuple, same delete-then-upload-free approach as the bank syncs but for
Sheets: read-all, diff, append-only — never rewrites existing rows).

Run by hermes as a --no-agent cron job, scheduled AFTER both bank syncs
(which run ~23:00/23:05 ART) and BEFORE the daily expense summary
(~23:55 ART) — see the "Resumen diario de gastos" cron job.

Prints nothing on success (silent = OK for hermes's --no-agent watchdog
mode). Prints a short line and exits non-zero on failure — that stdout is
what hermes delivers via --deliver origin.
"""
import json
import re
import subprocess
import sys

GOOGLE_API_SCRIPT = "/opt/hermes/skills/productivity/google-workspace/scripts/google_api.py"
VENV_PYTHON = "/opt/hermes/.venv/bin/python"

GASTOS_SHEET_ID = "1kV9t1VLw7CJeYp5j-TWewePzw2aZ2TaEIkLjEmBbwcA"
LEDGER_TAB = "Hoja 1"

GALICIA_TARJETAS_FOLDER_ID = "1xiVDZqs_KrTj0XnFcPa_lsoYNLWDz2l5"
BNA_TARJETAS_FOLDER_ID = "1OcoYvPEslra2fOmVyBAQVG0cHkIg-J7N"

HEADER = ["Fecha", "Banco", "Tarjeta", "Descripcion", "Cuotas", "Monto ARS", "Monto USD"]


def run_gapi(*args: str) -> str:
    try:
        result = subprocess.run(
            [VENV_PYTHON, GOOGLE_API_SCRIPT, *args],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        # The bare CalledProcessError (str(e)) only shows the command and
        # exit code, discarding google_api.py's actual stderr/traceback —
        # every failure this job has reported so far said only "returned
        # non-zero exit status 1" with no way to tell auth-expired from a
        # transient network blip from a real bug. Surface the last part of
        # stderr (where a Python traceback's exception message lives) so
        # the next real failure is actually diagnosable.
        stderr_tail = (e.stderr or "").strip()[-800:]
        raise RuntimeError(f"google_api.py {args[0]} failed: {stderr_tail or '(no stderr captured)'}") from e
    return result.stdout


def list_csv_files(folder_id: str) -> list[dict]:
    query = f"'{folder_id}' in parents and trashed = false and name contains 'tarjeta_'"
    return json.loads(run_gapi("drive", "search", query, "--raw-query"))


def download_csv(file_id: str, local_path: str) -> None:
    run_gapi("drive", "download", file_id, "--output", local_path)


def parse_amount(raw: str) -> float:
    """'- $ 135.506,62' / '$ 2.777,66' / '' -> float (0.0 for empty)."""
    raw = (raw or "").strip()
    if not raw:
        return 0.0
    negative = raw.startswith("-")
    digits = re.sub(r"[^\d,]", "", raw)
    digits = digits.replace(".", "").replace(",", ".")
    if not digits:
        return 0.0
    value = float(digits)
    return -value if negative else value


def to_iso_date(ddmmyyyy: str) -> str:
    d, m, y = ddmmyyyy.strip().split("/")
    # Leading apostrophe forces Sheets (valueInputOption=USER_ENTERED) to
    # store this as literal text instead of auto-parsing it into a date
    # serial number — without it, some rows silently became integers like
    # 46269, breaking any string-based "date == today" filter downstream.
    return f"'{y}-{m}-{d}"


def force_text(value: str) -> str:
    """Prefix with an apostrophe so Sheets (USER_ENTERED) never guesses a
    date/number type for free-text fields like '8 de 9' (installments),
    which Sheets otherwise silently mangles into a date serial number."""
    value = value or ""
    return f"'{value}" if value else value


def parse_galicia_csv(path: str, last4_from_filename: str) -> list[list]:
    import csv
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            card = r.get("card") or f"Visa/Master {last4_from_filename}"
            rows.append([
                to_iso_date(r["date"]),
                "Galicia",
                card,
                r.get("description", ""),
                force_text(r.get("installments", "")),
                parse_amount(r.get("amount_ars", "")),
                parse_amount(r.get("amount_usd", "")),
            ])
    return rows


def parse_bna_csv(path: str, last4_from_filename: str) -> list[list]:
    import csv
    rows = []
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([
                to_iso_date(r["date"]),
                "Nacion",
                force_text(last4_from_filename),
                r.get("description", ""),
                "",
                parse_amount(r.get("amount", "")),
                0.0,
            ])
    return rows


def extract_last4(filename: str) -> str:
    m = re.search(r"tarjeta_(\w+)\.csv", filename)
    return m.group(1) if m else filename


def collect_new_rows() -> list[list]:
    rows: list[list] = []
    for f in list_csv_files(GALICIA_TARJETAS_FOLDER_ID):
        local_path = f"/tmp/{f['name']}"
        download_csv(f["id"], local_path)
        rows.extend(parse_galicia_csv(local_path, extract_last4(f["name"])))
    for f in list_csv_files(BNA_TARJETAS_FOLDER_ID):
        local_path = f"/tmp/{f['name']}"
        download_csv(f["id"], local_path)
        rows.extend(parse_bna_csv(local_path, extract_last4(f["name"])))
    return rows


def get_existing_rows() -> list[list]:
    raw = run_gapi("sheets", "get", GASTOS_SHEET_ID, f"'{LEDGER_TAB}'!A2:G100000")
    data = json.loads(raw)
    return data or []


def ensure_header() -> None:
    raw = run_gapi("sheets", "get", GASTOS_SHEET_ID, f"'{LEDGER_TAB}'!A1:G1")
    data = json.loads(raw)
    if not data or data[0] != HEADER:
        run_gapi(
            "sheets", "update", GASTOS_SHEET_ID, f"'{LEDGER_TAB}'!A1:G1",
            "--values", json.dumps([HEADER]),
        )


def row_key(row) -> tuple:
    # Sheets returns already-persisted amounts as locale strings (e.g.
    # "-135506,62", comma decimal, no thousands separator) while freshly
    # parsed CSV amounts are Python floats (e.g. -135506.62). A naive
    # float(v) on the comma-string raises and silently normalized to 0.0,
    # making every existing row look different from its own re-parsed
    # version and re-appending everything on every run. Route both through
    # the same comma-aware parser used when building new rows.
    def norm_amount(v):
        if isinstance(v, (int, float)):
            return round(float(v), 2)
        return round(parse_amount(str(v)), 2)
    return (
        str(row[0]).lstrip("'") if len(row) > 0 else "",
        str(row[1]) if len(row) > 1 else "",
        str(row[2]).lstrip("'") if len(row) > 2 else "",
        str(row[3]) if len(row) > 3 else "",
        str(row[4]).lstrip("'") if len(row) > 4 else "",
        norm_amount(row[5]) if len(row) > 5 else 0.0,
        norm_amount(row[6]) if len(row) > 6 else 0.0,
    )


def main() -> int:
    try:
        ensure_header()
        existing = get_existing_rows()
        existing_keys = {row_key(r) for r in existing}

        new_rows = collect_new_rows()
        to_append = [r for r in new_rows if row_key(r) not in existing_keys]

        # De-dup within this run's own batch too (a row could legitimately
        # repeat across files only if identical in every field, which the
        # key already treats as the same movement).
        seen = set()
        deduped = []
        for r in to_append:
            k = row_key(r)
            if k in seen:
                continue
            seen.add(k)
            deduped.append(r)

        if deduped:
            run_gapi(
                "sheets", "append", GASTOS_SHEET_ID, f"'{LEDGER_TAB}'!A:G",
                "--values", json.dumps(deduped),
            )
        return 0
    except Exception as e:
        print(f"Sync movimientos->Sheet: fallo — {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
