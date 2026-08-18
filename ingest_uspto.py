"""
ingest_uspto.py — Downloads USPTO trademark bulk-data XML and builds/updates
a local SQLite database (uspto_marks.db) that MarkScout's app.py queries
instead of making a live network call to USPTO on every search.

WHY THIS EXISTS
----------------
USPTO's live systems aren't built for per-request search-by-name queries at
app scale, and free hosting can't hold the full multi-GB historical
register in memory. So instead: periodically download USPTO's official
bulk XML data, parse it, and store a compact local index. app.py then
searches that local file instantly, with no live network dependency.

TWO MODES
---------
1. Backfill (--mode backfill): run this ONCE, by hand, with an explicit
   --url pointing at USPTO's Annual/date-range bulk dataset, to seed the
   database with broad historical coverage. This file is large - expect
   it to take a while.

2. Incremental (--mode incremental): run this on a schedule (see the
   GitHub Actions workflow alongside this file). If you don't pass --url,
   it automatically builds a date-range URL covering everything since the
   last successful run through today, using the same dataset endpoint.

WHERE TO GET THE BACKFILL URL / WHY AUTH MIGHT BE NEEDED
------------------------------------------------------------
USPTO's bulk-data file listings live behind a JavaScript-rendered page, so
there isn't one fixed URL I can hardcode with certainty. Get it yourself:
  1. Go to https://data.uspto.gov/bulkdata/datasets/trtyrap
  2. Sign in with your USPTO.gov account if prompted
  3. Pick your date range and find the direct download link
  4. Pass it via --url (backfill), or set USPTO_BULK_URL as an
     environment variable / GitHub Actions secret

Since June 2026, USPTO requires a signed-in account for the Open Data
Portal. If a request comes back as an HTML login page instead of a zip
file, you'll need --auth-header (or the USPTO_AUTH_HEADER environment
variable) with a token/cookie from your own signed-in browser session -
this script can't obtain that for you, since it's tied to your personal
USPTO.gov account.

USAGE
-----
    python ingest_uspto.py --mode backfill --url "<annual bulk zip URL>"
    python ingest_uspto.py --mode incremental
    python ingest_uspto.py --mode incremental --auth-header "Authorization: Bearer xyz"

Requires: requests (already in requirements.txt). Uses only the standard
library otherwise (sqlite3, zipfile, xml.etree.ElementTree).
"""

import argparse
import os
import sqlite3
import sys
import zipfile
import io
import re
from datetime import datetime, timezone, timedelta, date
from xml.etree import ElementTree as ET

import requests

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uspto_marks.db")
REQUEST_TIMEOUT = 300  # bulk files are large; give it real time

# Confirmed, current (as of Aug 2026) dataset base URL that accepts a date
# range - used to auto-build the incremental-mode URL instead of needing a
# constantly-changing daily filename. See this file's docstring for how to
# find it yourself if USPTO changes this again.
ANNUAL_RANGE_DATASET_URL = "https://data.uspto.gov/bulkdata/datasets/trtyrap"


def normalize(text: str) -> str:
    """Lowercase, strip punctuation/whitespace - used for fast matching."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def init_db(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS marks (
            serial_number TEXT PRIMARY KEY,
            mark_literal TEXT,
            mark_normalized TEXT,
            status_code TEXT,
            status_date TEXT,
            filing_date TEXT,
            owner_name TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mark_normalized ON marks (mark_normalized)
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ingest_log (
            run_at TEXT,
            mode TEXT,
            source_url TEXT,
            records_processed INTEGER
        )
    """)
    conn.commit()


def download_bulk_zip(url: str, auth_header: str = None) -> bytes:
    print(f"Downloading bulk data from: {url}")
    headers = {"User-Agent": "MarkScout/1.0 (trademark risk screening tool)"}
    if auth_header:
        # Format: "HeaderName: value" - e.g. "Authorization: Bearer abc123"
        # or "Cookie: session=abc123". Set via --auth-header or the
        # USPTO_AUTH_HEADER environment variable / GitHub Actions secret.
        # This exists because data.uspto.gov now requires a signed-in
        # USPTO.gov account to access bulk data (added June 2026) - a
        # plain unauthenticated request may get redirected to a login
        # page instead of the actual file. Log into data.uspto.gov
        # yourself, find the auth token/cookie your browser sends, and
        # pass it through here. I can't obtain this for you - it's tied
        # to your personal USPTO.gov account.
        name, _, value = auth_header.partition(":")
        headers[name.strip()] = value.strip()

    resp = requests.get(url, timeout=REQUEST_TIMEOUT, stream=True, headers=headers)
    resp.raise_for_status()
    if resp.headers.get("Content-Type", "").startswith("text/html"):
        raise RuntimeError(
            "Got an HTML page back instead of a zip file - this usually "
            "means the request was redirected to a USPTO login page. "
            "You likely need to pass --auth-header (see this function's "
            "comments above) with a valid token/cookie from your own "
            "signed-in USPTO.gov session."
        )
    chunks = []
    downloaded = 0
    for chunk in resp.iter_content(chunk_size=1024 * 1024):
        chunks.append(chunk)
        downloaded += len(chunk)
        print(f"\r  {downloaded / (1024*1024):.1f} MB downloaded", end="", flush=True)
    print()
    return b"".join(chunks)


def iter_case_files_from_zip(zip_bytes: bytes):
    """
    Yields parsed <case-file> elements from every XML file inside the zip.
    USPTO's trademark daily/annual XML bundles contain one or more large
    XML files, each holding many <case-file> records. This uses iterparse
    to avoid loading the whole XML tree into memory at once, since these
    files can be very large.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if not xml_names:
            print("WARNING: no .xml files found inside the downloaded zip.")
        for name in xml_names:
            print(f"Parsing {name} ...")
            with zf.open(name) as f:
                context = ET.iterparse(f, events=("end",))
                for event, elem in context:
                    if elem.tag == "case-file":
                        yield elem
                        elem.clear()


def extract_record(case_file_elem):
    """
    Pulls the fields MarkScout actually needs out of one <case-file>
    element, using USPTO's standard trademark case-file XML schema.
    """
    def find_text(path):
        node = case_file_elem.find(path)
        return node.text.strip() if node is not None and node.text else None

    serial_number = find_text("serial-number")
    mark_literal = find_text("case-file-header/mark-identification")
    status_code = find_text("case-file-header/status-code")
    status_date = find_text("case-file-header/status-date")
    filing_date = find_text("case-file-header/filing-date")

    owner_name = None
    owner_node = case_file_elem.find("case-file-owners/case-file-owner/party-name")
    if owner_node is not None and owner_node.text:
        owner_name = owner_node.text.strip()

    if not serial_number or not mark_literal:
        return None

    return {
        "serial_number": serial_number,
        "mark_literal": mark_literal,
        "mark_normalized": normalize(mark_literal),
        "status_code": status_code,
        "status_date": status_date,
        "filing_date": filing_date,
        "owner_name": owner_name,
    }


def get_last_run_date(conn) -> date:
    """Returns the date of the last successful ingest, or a 30-day lookback
    default if this is the first run (no ingest_log rows yet)."""
    row = conn.execute("SELECT run_at FROM ingest_log ORDER BY run_at DESC LIMIT 1").fetchone()
    if row:
        return datetime.fromisoformat(row[0]).date()
    return (datetime.now(timezone.utc) - timedelta(days=30)).date()


def build_incremental_url(conn) -> str:
    """
    Builds the date-range dataset URL automatically, from the last
    successful ingest date through today. This avoids needing to hunt
    down a new daily filename every single run - see ANNUAL_RANGE_DATASET_URL
    above for where this pattern was confirmed.
    """
    from_date = get_last_run_date(conn)
    to_date = datetime.now(timezone.utc).date()
    url = (
        f"{ANNUAL_RANGE_DATASET_URL}"
        f"?fileDataFromDate={from_date.isoformat()}"
        f"&fileDataToDate={to_date.isoformat()}"
    )
    print(f"Auto-built incremental URL for range {from_date} to {to_date}: {url}")
    return url


def ingest(url: str, mode: str, auth_header: str = None, local_file: str = None):
    conn = sqlite3.connect(DB_PATH)
    init_db(conn)

    if local_file:
        print(f"Reading local file: {local_file}")
        with open(local_file, "rb") as f:
            zip_bytes = f.read()
        source_label = f"local file: {local_file}"
    else:
        if not url and mode == "incremental":
            url = build_incremental_url(conn)
        zip_bytes = download_bulk_zip(url, auth_header=auth_header)
        source_label = url

    now = datetime.now(timezone.utc).isoformat()

    count = 0
    batch = []
    for case_file in iter_case_files_from_zip(zip_bytes):
        record = extract_record(case_file)
        if record is None:
            continue
        batch.append((
            record["serial_number"], record["mark_literal"], record["mark_normalized"],
            record["status_code"], record["status_date"], record["filing_date"],
            record["owner_name"], now,
        ))
        count += 1
        if len(batch) >= 5000:
            _flush_batch(conn, batch)
            batch = []
            print(f"  ...{count} records processed so far")

    if batch:
        _flush_batch(conn, batch)

    conn.execute(
        "INSERT INTO ingest_log (run_at, mode, source_url, records_processed) VALUES (?, ?, ?, ?)",
        (now, mode, source_label, count),
    )
    conn.commit()
    conn.close()

    print(f"\nDone. {count} records processed in this run ({mode} mode).")
    print(f"Database at: {DB_PATH}")


def _flush_batch(conn, batch):
    conn.executemany("""
        INSERT INTO marks (serial_number, mark_literal, mark_normalized, status_code,
                            status_date, filing_date, owner_name, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(serial_number) DO UPDATE SET
            mark_literal=excluded.mark_literal,
            mark_normalized=excluded.mark_normalized,
            status_code=excluded.status_code,
            status_date=excluded.status_date,
            filing_date=excluded.filing_date,
            owner_name=excluded.owner_name,
            updated_at=excluded.updated_at
    """, batch)
    conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest USPTO bulk trademark data into a local SQLite index.")
    parser.add_argument("--mode", choices=["backfill", "incremental"], required=True,
                         help="backfill = run once by hand, using --file or --url covering "
                              "whatever historical range you want to seed. "
                              "incremental = run on a schedule; auto-builds a date-range URL "
                              "from the last successful run to today if --url isn't given.")
    parser.add_argument("--file",
                         help="Path to a zip file you already downloaded yourself (e.g. by "
                              "clicking 'Download' on USPTO's Bulk Data Directory page in your "
                              "browser, while signed in). This is the easiest option, since "
                              "USPTO's portal is a JavaScript app and the download link often "
                              "isn't something you can just copy - clicking it in your browser "
                              "and pointing this script at the saved file sidesteps that.")
    parser.add_argument("--url", default=os.environ.get("USPTO_BULK_URL"),
                         help="Direct download URL for the bulk zip file, if you have one that "
                              "actually works when fetched programmatically. Optional for "
                              "incremental mode (auto-built if omitted). Falls back to the "
                              "USPTO_BULK_URL environment variable. Ignored if --file is given.")
    parser.add_argument("--auth-header", default=os.environ.get("USPTO_AUTH_HEADER"),
                         help="Optional 'HeaderName: value' string (e.g. 'Authorization: Bearer "
                              "...' or 'Cookie: session=...') if data.uspto.gov requires a "
                              "signed-in session for bulk downloads via --url. Not needed with "
                              "--file. Falls back to the USPTO_AUTH_HEADER environment variable.")
    args = parser.parse_args()

    if not args.file and not args.url and args.mode == "backfill":
        print("ERROR: backfill mode needs either --file (a zip you already downloaded) or --url.")
        print("Easiest path: go to https://data.uspto.gov/bulkdata/datasets/trtdxfap while")
        print("signed into your USPTO.gov account, pick a date range, click Download, and pass")
        print("the saved file's path with --file.")
        sys.exit(1)

    ingest(args.url, args.mode, auth_header=args.auth_header, local_file=args.file)
