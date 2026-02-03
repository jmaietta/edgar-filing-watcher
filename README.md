
````md
# edgar-filing-watcher

Local Python tool that scans the SEC EDGAR daily index for a list of tickers and generates a local HTML summary report.

## What filings does it pull?

By default, this script filters the SEC daily index for these form types:

- 8-K
- 8-K/A
- DEF 14A
- DEFA14A

**Note:** The script extracts and highlights **Item sections only for 8-K / 8-K/A**.  
Proxy filings (DEF 14A / DEFA14A) will still appear in the report, but without item snippets.

## Requirements

- Python 3
- Internet connection
- `requests` (installed via `requirements.txt`)

## Install

Run:

```bash
python3 -m pip install -r requirements.txt
````

## SEC User-Agent (recommended)

The U.S. Securities and Exchange Commission requests automated scripts identify themselves with contact info in the `User-Agent` header.

Before running, set this in your terminal:

```bash
export SEC_USER_AGENT="edgar-filing-watcher (your-email@example.com)"
```

This is **not stored in the repo** — each user sets it locally on their own machine.

## Create your ticker list

Create a CSV file (example name: `tickers.csv`) with a `Ticker` column:

```csv
Ticker
AAPL
MSFT
DLTR
```

## Run

Basic run (uses the most recent available day, and looks back a few days if needed):

```bash
python3 sec_notifications.py --tickers-csv tickers.csv
```

Run a specific date:

```bash
python3 sec_notifications.py --tickers-csv tickers.csv --date 2026-02-01
```

Only scan 8-K / 8-K/A:

```bash
python3 sec_notifications.py --tickers-csv tickers.csv --forms "8-K,8-K/A"
```

See all options:

```bash
python3 sec_notifications.py -h
```

## Output

The script generates an HTML file named like:

* `sec_report_YYYY-MM-DD.html`

Open it (Linux):

```bash
xdg-open sec_report_YYYY-MM-DD.html
```

## Notes

* Item extraction is heuristic and may miss some items depending on filing formatting.
* Please be respectful with request volume when querying SEC endpoints.
* Not affiliated with the SEC.

## License

MIT License (see `LICENSE`).

```
::contentReference[oaicite:0]{index=0}
```
