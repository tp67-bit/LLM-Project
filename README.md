# Insider Trading Analysis Pipeline — Small-Cap Screen (≤ $2B Market Cap)

## Purpose
Analyzes SEC Form 4 insider trading filings cross-referenced with Yahoo Finance price
data to answer: **do insider trades at small-cap companies predict forward stock returns?**

## Data Sources
| Source | What it provides | Access method |
|---|---|---|
| SEC EDGAR (Form 4) | Every officer/director/10%-owner transaction, filed within 2 business days of trade | Free, no API key — `browse-edgar` atom feed + raw XML filings |
| Yahoo Finance (`yfinance`) | Market cap, historical daily closes for forward-return calc | Free, no API key |

No paid API (e.g. sec-api.io, EODHD) is required — the pipeline uses SEC's own public
endpoints, which is important since it's free and has no rate-limit costs for a class project.

## Pipeline Steps (`insider_pipeline.py`)
1. **Ticker → CIK resolution** via SEC's `company_tickers.json`.
2. **Market cap filter**: pulls `fast_info.marketCap` from yfinance; skips any ticker over $2B.
3. **Filing retrieval**: lists recent Form 4 filings per CIK (2-year lookback), fetches each raw XML.
4. **Transaction parsing**: extracts owner name, title, transaction code (P=purchase, S=sale, F=tax withholding, A=award/grant), shares, price, and post-transaction share balance.
5. **CEO flag**: regex-matches officer title against "chief executive officer" / "CEO" to separate CEO trades from other insiders.
6. **Allocation %**: computes the transaction's shares as a percentage of the insider's prior holdings — this measures how large the trade is *relative to their existing stake* (not just $ size).
7. **One-off flag**: any single transaction ≥ $1,000,000 in value is flagged `is_one_off = True` (the "XYZ CEO bought $2M of shares" case).
8. **Signal (rule-based, as scoped)**:
   - `STRONG_BUY` — open-market purchase (code P) ≥ $1M
   - `NOTABLE_BUY` — open-market purchase < $1M
   - `SELL` — open-market sale (code S)
   - `NEUTRAL` — grants, option exercises, tax-withholding dispositions (code F), gifts
9. **Price outlook**: for each transaction date, pulls the closing price on/near that date and computes forward returns at +3 months, +1 year, +2 years.

## Output
`insider_trades.csv` — one row per transaction, with all fields above. Test run on BigBear.ai (BBAI) shows the pipeline correctly:
- Identified CEO Kevin McAleenan's tax-withholding disposition separately from CFO/General Counsel transactions
- Computed allocation % changes relative to prior holdings
- Filtered to sub-$2B market cap only (BBAI: ~$1.57B)

## Known Limitations / Next Steps for You & Your Partner
- **Universe generation**: currently you pass a ticker list manually. For a full screen, pull a small-cap universe (e.g., Russell 2000 constituents or a screener export) and feed it in bulk — expect SEC to rate-limit at high volume, so add delays (already has 0.15s/filing) or consider the SEC daily bulk `.zip` index files referenced in the docs.
- **Derivative transactions** (options, RSUs vesting) are not yet parsed — only `nonDerivativeTransaction` blocks. Add a `derivativeTransaction` parser if you want option-based signals too.
- **10b5-1 plan detection**: footnotes sometimes disclose pre-scheduled trading plans (which carry less signal than discretionary buys). Worth flagging these separately — the footnote text is already being fetched, just not parsed yet.
- **Signal backtesting**: the `TBD` signal is currently a simple rule. Your next step could be backtesting `STRONG_BUY` events against the `return_3m_pct` / `return_1y_pct` / `return_2y_pct` columns to see if the rule actually predicts outperformance — this is the natural "part 2" of the project.
- **CEO title matching**: some filings list "President and CEO" or "Co-CEO" — the current regex handles these, but co-founder/executive chairman edge cases may need manual review.

## How to Run
```bash
python insider_pipeline.py --tickers BBAI,WOLF,SOUN
```
Swap in any tickers under $2B market cap; the script auto-skips anything larger.
# LLM-Project
