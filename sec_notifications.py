#!/usr/bin/env python3
"""
SEC Notifications
-----------------
Fetches the SEC EDGAR daily index, filters filings by your tickers + form types,
extracts 8‑K Item sections (with a short context snippet), and generates a clean HTML report.

Quick start:
    python3 sec_notifications.py --tickers-csv tickers.csv

SEC requires a descriptive User-Agent with contact info. Set one of:
    export SEC_USER_AGENT="sec-notifications (you@example.com)"
or pass:
    --user-agent "sec-notifications (you@example.com)"

Notes:
- This script only depends on `requests`.
- It HTML-escapes all extracted snippets so broken markup (e.g. partial XBRL tags)
  can't break your report links.
"""

from __future__ import annotations

import argparse
import csv
import html as html_lib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import requests


# -----------------------------
# Configuration
# -----------------------------

SEC_TICKERS_JSON_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives"
DEFAULT_FORMS = {"8-K", "8-K/A", "DEF 14A", "DEFA14A"}

# 8‑K Item descriptions (partial list; extend as you like)
ITEM_DESCRIPTIONS: Dict[str, str] = {
    "1.01": "Entry into Material Agreement",
    "1.02": "Termination of Material Agreement",
    "1.03": "Bankruptcy or Receivership",
    "2.01": "Acquisition/Disposition of Assets",
    "2.02": "Results of Operations (Earnings)",
    "2.03": "Creation of Direct Financial Obligation",
    "2.04": "Triggering Events (Acceleration)",
    "2.05": "Exit/Disposal Activities (Restructuring)",
    "2.06": "Material Impairments",
    "3.01": "Delisting or Transfer Notice",
    "3.02": "Unregistered Sales of Equity",
    "3.03": "Material Modification of Rights",
    "4.01": "Change in Accountant",
    "4.02": "Non-Reliance on Financial Statements",
    "5.01": "Change in Control",
    "5.02": "Departure/Appointment of Directors or Officers",
    "5.03": "Amendments to Articles/Bylaws",
    "5.04": "Temporary Suspension of Trading",
    "5.05": "Amendments to Code of Ethics",
    "5.06": "Change in Shell Company Status",
    "5.07": "Shareholder Vote Submission",
    "5.08": "Shareholder Nominations",
    "7.01": "Regulation FD Disclosure",
    "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}

# Items to highlight as "priority"
PRIORITY_ITEMS = {"5.02", "5.01", "1.01", "2.05"}


# -----------------------------
# Data models
# -----------------------------

@dataclass
class FilingItem:
    item: str
    description: str
    context: str
    is_priority: bool


@dataclass
class Filing:
    cik: str
    company_name: str
    form_type: str
    date_filed: str
    filename: str
    accession: str
    url: str      # best browser URL (prefer primary doc, else index)
    raw_url: str  # raw filing URL used to fetch content

    # enriched fields
    ticker: str = "???"
    items: Optional[List[FilingItem]] = None


# -----------------------------
# Helpers
# -----------------------------

def esc(value: object) -> str:
    """HTML-escape anything we inject into the report."""
    return html_lib.escape(str(value or ""), quote=True)


def build_session(user_agent: str, timeout: int = 30) -> Tuple[requests.Session, int]:
    """
    Returns a requests.Session and a default timeout value.
    Keeping timeout separate avoids repeating it on every call.
    """
    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})
    return session, timeout


def load_tickers_from_csv(csv_path: Path, column_name: str = "Ticker") -> Set[str]:
    """Load tickers from a CSV with a column like 'Ticker'."""
    tickers: Set[str] = set()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            return tickers

        # fall back if column_name isn't present
        col = column_name if column_name in reader.fieldnames else reader.fieldnames[0]

        for row in reader:
            t = (row.get(col) or "").strip().upper()
            if t:
                tickers.add(t)

    return tickers


def get_ticker_to_cik_mapping(session: requests.Session, timeout: int) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Fetch SEC ticker → CIK mapping (and reverse)."""
    resp = session.get(SEC_TICKERS_JSON_URL, timeout=timeout)
    resp.raise_for_status()

    data = resp.json()
    ticker_to_cik: Dict[str, str] = {}
    cik_to_ticker: Dict[str, str] = {}

    for entry in data.values():
        ticker = str(entry.get("ticker", "")).upper()
        cik = str(entry.get("cik_str", "")).strip()
        if ticker and cik:
            ticker_to_cik[ticker] = cik
            cik_to_ticker[cik] = ticker

    return ticker_to_cik, cik_to_ticker


def get_daily_index_url(date: datetime) -> str:
    """Build URL for SEC daily index file."""
    quarter = (date.month - 1) // 3 + 1
    return f"{SEC_ARCHIVES_BASE}/edgar/daily-index/{date.year}/QTR{quarter}/master.{date.strftime('%Y%m%d')}.idx"


def download_and_parse_index(session: requests.Session, url: str, timeout: int) -> List[Filing]:
    """Download and parse the master index file."""
    resp = session.get(url, timeout=timeout)
    if resp.status_code in (403, 404):
        return []
    resp.raise_for_status()

    filings: List[Filing] = []
    lines = resp.text.splitlines()

    for line in lines:
        if "|" not in line:
            continue
        parts = line.split("|")
        if len(parts) < 5:
            continue

        cik, company_name, form_type, date_filed, filename = parts[:5]
        accession = filename.split("/")[-1].replace(".txt", "")

        accession_no_dashes = accession.replace("-", "")
        folder_url = f"{SEC_ARCHIVES_BASE}/edgar/data/{cik}/{accession_no_dashes}/"
        # Prefer the filing's HTML index page over the bare directory listing.
        # Example: .../0001628280-26-004393-index.html
        index_url = f"{folder_url}{accession}-index.html"
        raw_url = f"{SEC_ARCHIVES_BASE}/{filename}"

        filings.append(
            Filing(
                cik=cik,
                company_name=company_name,
                form_type=form_type,
                date_filed=date_filed,
                filename=filename,
                accession=accession,
                url=index_url,
                raw_url=raw_url,
            )
        )

    return filings


def filter_filings(filings: Sequence[Filing], target_ciks: Set[str], target_forms: Set[str]) -> List[Filing]:
    """Filter filings to our CIKs and forms."""
    return [f for f in filings if f.form_type in target_forms and f.cik in target_ciks]


def fetch_filing_content(session: requests.Session, raw_url: str, timeout: int) -> str:
    """Fetch raw filing content."""
    resp = session.get(raw_url, timeout=timeout)
    if resp.status_code in (403, 404):
        return ""
    resp.raise_for_status()
    return resp.text


def extract_8k_items(content: str) -> List[FilingItem]:
    """Extract Item sections from 8‑K filing content."""
    if not content:
        return []

    items_found: List[FilingItem] = []
    seen: Set[str] = set()

    # Match "Item 5.02:" etc (many filings vary in punctuation / spacing)
    item_pattern = re.compile(r"item\s*(\d+\.\d+)[:\s\-—]+([^\n]+)?", re.IGNORECASE)

    for match in item_pattern.finditer(content):
        item_num = match.group(1)

        if item_num in seen:
            continue

        # Grab context after the item heading
        start_pos = match.end()
        end_pos = min(start_pos + 500, len(content))
        context = content[start_pos:end_pos]

        # Remove complete HTML tags
        context = re.sub(r"<[^>]+>", " ", context)
        # Remove trailing partial "<tag ..." fragments that got cut off mid-tag
        context = re.sub(r"<[^>]*$", " ", context)
        # Normalize whitespace
        context = re.sub(r"\s+", " ", context).strip()
        if len(context) > 300:
            context = context[:300] + "..."

        desc = ITEM_DESCRIPTIONS.get(item_num, "Other Item")
        is_priority = item_num in PRIORITY_ITEMS

        items_found.append(FilingItem(item=item_num, description=desc, context=context, is_priority=is_priority))
        seen.add(item_num)

    # sort by item number
    items_found.sort(key=lambda x: float(x.item))
    return items_found


def extract_primary_document_filename(submission_text: str, form_type: str) -> Optional[str]:
    """Find the main filing document filename inside the full submission text.

    We already fetch the full submission text file (.txt). It usually contains several
    <DOCUMENT> blocks with <TYPE> and <FILENAME>. This lets us link directly to the
    primary HTML filing document (e.g., the 8‑K itself) instead of the directory.
    """
    if not submission_text:
        return None

    desired = {form_type.upper()}
    if form_type.upper().endswith("/A"):
        desired.add(form_type.upper().replace("/A", ""))

    # Split on closing DOCUMENT tags (simple but effective).
    blocks = re.split(r"</DOCUMENT>", submission_text, flags=re.IGNORECASE)

    candidates = []
    for block in blocks:
        t = re.search(r"<TYPE>\s*([^\n<]+)", block, flags=re.IGNORECASE)
        fn = re.search(r"<FILENAME>\s*([^\n<]+)", block, flags=re.IGNORECASE)
        if not t or not fn:
            continue

        doc_type = t.group(1).strip().upper()
        filename = fn.group(1).strip()

        seq_m = re.search(r"<SEQUENCE>\s*(\d+)", block, flags=re.IGNORECASE)
        seq = int(seq_m.group(1)) if seq_m else 9999

        is_html = filename.lower().endswith((".htm", ".html"))
        candidates.append((doc_type, filename, seq, is_html))

    if not candidates:
        return None

    # Prefer: TYPE matches desired, then HTML, then sequence order.
    def rank(c):
        doc_type, filename, seq, is_html = c
        type_match = 0 if doc_type in desired else 1
        html_rank = 0 if is_html else 1
        return (type_match, html_rank, seq)

    candidates.sort(key=rank)
    return candidates[0][1]


def copy_assets(assets_dir: Optional[Path], output_path: Path) -> Optional[Path]:
    """
    Copy asset files (favicons/logos) next to the generated report.
    Returns the relative assets folder name (e.g. 'assets') if copied.
    """
    if not assets_dir:
        return None
    if not assets_dir.exists() or not assets_dir.is_dir():
        return None

    dest_dir = output_path.parent / assets_dir.name
    dest_dir.mkdir(parents=True, exist_ok=True)

    for p in assets_dir.glob("*"):
        if p.is_file():
            shutil.copy2(p, dest_dir / p.name)

    return dest_dir.name


def generate_filing_html(filing: Filing, is_priority: bool = False) -> str:
    priority_class = " priority" if is_priority else ""

    html_out = f"""
        <div class="filing{priority_class}">
            <div class="filing-header">
                <div class="company-info">
                    <h3><span class="ticker">{esc(filing.ticker)}</span> {esc(filing.company_name)}</h3>
                    <div class="cik">CIK: {esc(filing.cik)} · Filed: {esc(filing.date_filed)}</div>
                </div>
                <span class="form-type">{esc(filing.form_type)}</span>
            </div>
            <div class="items">
"""

    items = filing.items or []
    if items:
        for item in items:
            item_priority = " priority" if item.is_priority else ""
            html_out += f"""
                <div class="item{item_priority}">
                    <div class="item-header">Item {esc(item.item)}: {esc(item.description)}</div>
                    <div class="item-context">{esc(item.context)}</div>
                </div>
"""
    else:
        html_out += '                <p class="no-items">Could not extract item details</p>\n'

    html_out += f"""
            </div>
            <a class="filing-link" href="{esc(filing.url)}" target="_blank" rel="noopener">View Full Filing →</a>
            <a class="filing-link" href="{esc(f"{SEC_ARCHIVES_BASE}/edgar/data/{filing.cik}/{filing.accession.replace('-', '')}/{filing.accession}-index.html")}" target="_blank" rel="noopener">All documents</a>
        </div>
"""
    return html_out


def generate_html_report(
    filings: Sequence[Filing],
    report_date: str,
    output_file: Path,
    assets_dir: Optional[Path] = None,
    title: str = "SEC Filing Summary Report",
) -> None:
    """Generate a styled HTML report."""
    assets_rel = copy_assets(assets_dir, output_file)

    # Use logo in header if present
    logo_src = ""
    if assets_rel:
        candidate = Path(assets_rel) / "android-chrome-192x192.png"
        logo_src = str(candidate)

    priority_filings = [f for f in filings if any((it.is_priority for it in (f.items or [])))]
    other_filings = [f for f in filings if f not in priority_filings]

    forms_present = ", ".join(sorted({f.form_type for f in filings})) if filings else ""

    html_doc = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{esc(title)} - {esc(report_date)}</title>
"""

    if assets_rel:
        html_doc += f"""
    <link rel="icon" type="image/png" sizes="32x32" href="{esc(assets_rel)}/favicon-32x32.png">
    <link rel="icon" type="image/png" sizes="192x192" href="{esc(assets_rel)}/android-chrome-192x192.png">
    <link rel="apple-touch-icon" sizes="180x180" href="{esc(assets_rel)}/android-chrome-192x192.png">
"""

    html_doc += f"""
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 950px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
            color: #333;
        }}
        .header {{
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 10px;
        }}
        .logo {{
            width: 40px;
            height: 40px;
            border-radius: 10px;
            background: #fff;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #1a365d;
            border-bottom: 3px solid #2563eb;
            padding-bottom: 10px;
            margin: 0;
        }}
        .summary {{
            background: #fff;
            padding: 15px 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.1);
        }}
        .summary strong {{ color: #2563eb; }}
        .priority-section {{ margin-bottom: 30px; }}
        .priority-section h2 {{
            color: #dc2626;
            display: flex;
            align-items: center;
            gap: 8px;
        }}
        .priority-badge {{
            background: #dc2626;
            color: white;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
        }}

        .other-section { margin-bottom: 30px; }
        .other-section h2 {
            color: #6b7280;
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .other-badge {
            background: #6b7280;
            color: white;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
        }
        .filing {{
            background: #fff;
            margin-bottom: 16px;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 1px 3px rgba(0,0,0,0.08);
            border-left: 4px solid #2563eb;
        }}
        .filing.priority {{
            border-left-color: #dc2626;
        }}
        .filing-header {{
            padding: 14px 16px;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 12px;
            border-bottom: 1px solid #e5e7eb;
        }}
        .company-info h3 {{
            margin: 0 0 4px 0;
            color: #1a365d;
            font-size: 18px;
        }}
        .ticker {{
            display: inline-block;
            background: #2563eb;
            color: white;
            padding: 2px 8px;
            border-radius: 6px;
            font-weight: 700;
            font-size: 13px;
        }}
        .cik {{
            color: #6b7280;
            font-size: 12px;
        }}
        .form-type {{
            background: #e5e7eb;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            white-space: nowrap;
        }}
        .items {{
            padding: 14px 16px;
        }}
        .item {{
            margin-bottom: 10px;
            padding: 10px 12px;
            background: #f9fafb;
            border-radius: 8px;
            border-left: 3px solid #e5e7eb;
        }}
        .item.priority {{
            border-left-color: #dc2626;
            background: #fef2f2;
        }}
        .item-header {{
            font-weight: 700;
            color: #1f2937;
            margin-bottom: 6px;
        }}
        .item-context {{
            color: #374151;
            font-size: 14px;
            line-height: 1.4;
        }}
        .filing-link {{
            display: block;
            padding: 12px 16px;
            background: #f3f4f6;
            text-decoration: none;
            color: #2563eb;
            font-weight: 700;
            border-top: 1px solid #e5e7eb;
        }}
        .filing-link:hover {{
            background: #e5e7eb;
        }}
        .no-filings {{
            text-align: center;
            color: #6b7280;
            padding: 30px;
        }}
    </style>
</head>
<body>
    <div class="header">
        {"<img class='logo' src='" + esc(logo_src) + "' alt='logo'>" if logo_src else ""}
        <h1>{esc(title)}</h1>
    </div>
    <div class="summary">
        <p><strong>Date:</strong> {esc(report_date)}</p>
        <p><strong>Total Filings:</strong> {len(filings)}</p>
        <p><strong>Forms (present):</strong> {esc(forms_present) if forms_present else '—'}</p>
        <p><strong>Priority 8-K Filings:</strong> {len(priority_filings)} (8-K Items: {", ".join(sorted(PRIORITY_ITEMS))})</p>
    </div>
"""

    if priority_filings:
        html_doc += f"""
    <div class="priority-section">
        <h2>Priority 8-K Filings <span class="priority-badge">{len(priority_filings)}</span></h2>
"""
        for f in priority_filings:
            html_doc += generate_filing_html(f, is_priority=True)
        html_doc += "    </div>\n"
    if other_filings:
        html_doc += f"""
    <div class=\"other-section\">
        <h2>Other Filings <span class=\"other-badge\">{len(other_filings)}</span></h2>
"""
        for f in other_filings:
            html_doc += generate_filing_html(f, is_priority=False)
        html_doc += "    </div>\n"
    else:
        if not priority_filings:
            html_doc += '<div class="no-filings">No filings found for your criteria.</div>\n'

    html_doc += """
</body>
</html>
"""

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(html_doc, encoding="utf-8")


# -----------------------------
# CLI
# -----------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SEC filing watcher + HTML report")
    parser.add_argument("--tickers-csv", default=os.getenv("TICKERS_CSV", "tickers.csv"),
                        help="Path to a CSV with a Ticker column (default: tickers.csv)")
    parser.add_argument("--ticker-column", default="Ticker", help="CSV column name that contains tickers (default: Ticker)")
    parser.add_argument("--forms", default=",".join(sorted(DEFAULT_FORMS)),
                        help="Comma-separated form types to include (default: 8-K,8-K/A,DEF 14A,DEFA14A)")
    parser.add_argument("--date", default="",
                        help="Report date YYYY-MM-DD. If omitted, searches backward from today.")
    parser.add_argument("--lookback-days", type=int, default=7,
                        help="How many days back to search when --date is not set (default: 7)")
    parser.add_argument("--include-weekends", action="store_true", help="Also check Sat/Sun (default: off)")
    parser.add_argument("--output", default="",
                        help="Output HTML file. Default: sec_report_YYYY-MM-DD.html in current dir")
    parser.add_argument("--assets-dir", default="assets",
                        help="Folder with logo/favicon files to copy next to the report (default: assets)")
    parser.add_argument("--title", default="SEC Filing Summary Report", help="HTML report title")
    parser.add_argument("--user-agent", default=os.getenv("SEC_USER_AGENT", ""),
                        help="SEC-compliant User-Agent string (recommended)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    user_agent = args.user_agent.strip()
    if not user_agent:
        # Keep running, but warn loudly. SEC wants contact info in UA.
        user_agent = "sec-notifications (set SEC_USER_AGENT with your email)"
        print("WARNING: No SEC_USER_AGENT set. You should set it to include contact info.")
        print('Example: export SEC_USER_AGENT="sec-notifications (you@example.com)"\n')

    session, timeout = build_session(user_agent=user_agent)

    tickers_csv = Path(args.tickers_csv)
    if not tickers_csv.exists():
        print(f"Ticker CSV not found: {tickers_csv}")
        print("Tip: create a CSV with a 'Ticker' column (e.g. AAPL, MSFT, ...)")
        return 2

    tickers = load_tickers_from_csv(tickers_csv, column_name=args.ticker_column)
    if not tickers:
        print(f"No tickers found in {tickers_csv}. Check the column name.")
        return 2

    forms = {f.strip() for f in args.forms.split(",") if f.strip()}
    print(f"Loaded {len(tickers)} tickers · Forms: {sorted(forms)}")

    print("Fetching SEC ticker→CIK mapping...")
    ticker_to_cik, cik_to_ticker = get_ticker_to_cik_mapping(session, timeout)
    ciks: Set[str] = set()
    missing: List[str] = []
    for t in sorted(tickers):
        cik = ticker_to_cik.get(t)
        if cik:
            ciks.add(cik)
        else:
            missing.append(t)

    print(f"Mapped {len(ciks)} tickers to CIKs")
    if missing:
        print(f"Warning: could not find CIKs for (showing up to 10): {missing[:10]}")

    # Determine which date(s) to check
    filings: List[Filing] = []
    report_date: Optional[str] = None

    if args.date:
        try:
            dt = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print("Invalid --date. Use YYYY-MM-DD.")
            return 2

        url = get_daily_index_url(dt)
        print(f"Downloading index: {url}")
        filings = download_and_parse_index(session, url, timeout)
        report_date = dt.strftime("%Y-%m-%d")
    else:
        for days_ago in range(args.lookback_days):
            dt = datetime.now() - timedelta(days=days_ago)
            if (dt.weekday() >= 5) and (not args.include_weekends):
                continue

            url = get_daily_index_url(dt)
            print(f"Checking {dt.strftime('%Y-%m-%d')} ...")
            filings = download_and_parse_index(session, url, timeout)
            if filings:
                report_date = dt.strftime("%Y-%m-%d")
                print(f"Found filings for {report_date}")
                break

    if not filings or not report_date:
        print("No filings found in the requested window.")
        return 0

    matches = filter_filings(filings, ciks, forms)
    print(f"Matched {len(matches)} filings for your tickers/forms")

    # Enrich tickers and extract items for 8‑K
    for f in matches:
        f.ticker = cik_to_ticker.get(f.cik, "???")

    print(f"Fetching content for {len(matches)} filings (8‑K only for Item extraction)...")
    for i, f in enumerate(matches, start=1):
        if "8-K" in f.form_type:
            print(f"  [{i}/{len(matches)}] {f.ticker} - {f.company_name[:40]}")
            content = fetch_filing_content(session, f.raw_url, timeout)
            f.items = extract_8k_items(content)

            # Update the browser link to point to the actual filing document when possible
            # (instead of the directory listing).
            folder_url = f"{SEC_ARCHIVES_BASE}/edgar/data/{f.cik}/{f.accession.replace('-', '')}/"
            index_url = f"{folder_url}{f.accession}-index.html"
            primary_filename = extract_primary_document_filename(content, f.form_type)
            f.url = f"{folder_url}{primary_filename}" if primary_filename else index_url
        else:
            f.items = []

    # Output
    out = Path(args.output) if args.output else Path(f"sec_report_{report_date}.html")
    assets_dir = Path(args.assets_dir) if args.assets_dir else None
    generate_html_report(matches, report_date, out, assets_dir=assets_dir, title=args.title)

    priority_count = sum(1 for f in matches if any(it.is_priority for it in (f.items or [])))
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total filings: {len(matches)}")
    print(f"Priority filings (Items {', '.join(sorted(PRIORITY_ITEMS))}): {priority_count}")
    print(f"Report saved to: {out}")
    print(f"Open in browser: file://{out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
