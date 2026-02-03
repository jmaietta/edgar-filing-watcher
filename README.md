# edgar-filing-watcher

A local Python tool that scans the SEC EDGAR daily index for specific tickers and generates a clean HTML summary report.

## Features

- **Targeted Scanning:** Filters daily indices for a user-defined list of tickers
- **Form Support:** Defaults to `8-K`, `8-K/A`, `DEF 14A`, and `DEFA14A`
- **Smart Extraction:** Automatically extracts and highlights specific Item sections for 8-K filings (proxy filings are listed without snippets)

## Setup

### 1. Install Dependencies

Requires Python 3.
```bash
python3 -m pip install -r requirements.txt
```

### 2. Configure SEC User-Agent (Required)

To comply with SEC automated access guidelines, you must identify your script. Set the `SEC_USER_AGENT` environment variable before running:
```bash
export SEC_USER_AGENT="edgar-filing-watcher (your-email@example.com)"
```

### 3. Create Ticker List

Create a CSV file (e.g., `tickers.csv`) with a single column named `Ticker`:
```csv
Ticker
AAPL
MSFT
DLTR
```

## Usage

**Basic Run** — Scans the most recent available day (with automatic look-back):
```bash
python3 sec_notifications.py --tickers-csv tickers.csv
```

**Specific Date:**
```bash
python3 sec_notifications.py --tickers-csv tickers.csv --date 2026-02-01
```

**Filter by Form Type:**
```bash
python3 sec_notifications.py --tickers-csv tickers.csv --forms "8-K,8-K/A"
```

**View All Options:**
```bash
python3 sec_notifications.py -h
```

## Output

The script generates a local HTML file (e.g., `sec_report_YYYY-MM-DD.html`).

| Platform      | Command                              |
|---------------|--------------------------------------|
| macOS         | `open sec_report_2026-02-01.html`    |
| Linux         | `xdg-open sec_report_2026-02-01.html`|
| Windows       | Double-click the file                |

## Disclaimer

- Item extraction is heuristic and may vary based on filing formatting
- Please respect SEC request rate limits
- This tool is not affiliated with the U.S. Securities and Exchange Commission

## License

MIT License
