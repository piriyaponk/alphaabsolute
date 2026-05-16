# AlphaAbsolute Obsidian Vault — Setup Guide

## 1. Install Obsidian (free)
Download from: https://obsidian.md

## 2. Open This Folder as Vault
File -> Open Vault -> Open folder as vault -> Select this `obsidian-vault/` folder

## 3. Install Required Community Plugins
Settings -> Community Plugins -> Browse -> search and install:

| Plugin | Purpose |
|--------|---------|
| **Local REST API** | Allows Python scripts to write notes automatically |
| **Dataview** | SQL queries across all notes (live dashboards) |
| **Smart Connections** | Local AI semantic search (replaces NotebookLM for lookups) |
| **Obsidian Git** | Auto-backup vault to GitHub |
| **Templater** | Dynamic templates with user prompts |
| **QuickAdd** | Hotkey capture for new ideas/trades |
| **Excalidraw** | Value chain and thematic diagrams |

## 4. Configure Local REST API
- Enable the plugin
- Note the API Key shown in settings
- Add to `.env` in AlphaAbsolute project root:
  ```
  OBSIDIAN_API_KEY=your_key_here
  OBSIDIAN_URL=https://127.0.0.1:27124
  ```

## 5. Configure Templater
- Settings -> Templater -> Template folder location: `templates`
- Enable "Trigger Templater on new file creation"

## 6. Configure QuickAdd
Add these macros (Settings -> QuickAdd):

| Name | Hotkey | Template | Action |
|------|--------|----------|--------|
| New Ticker | Ctrl+Shift+T | templates/ticker_analysis.md | Create in tickers/ |
| New Trade | Ctrl+Shift+N | templates/trade_log.md | Create in paper_trades/ |
| New Earnings | Ctrl+Shift+E | templates/earnings_note.md | Create in research/earnings/ |

## 7. Configure Obsidian Git
- Settings -> Obsidian Git -> Repository: auto-detected (this is inside AlphaAbsolute)
- Auto-commit: every 30 minutes
- Auto-push: YES

## 8. Dataview Dashboards (paste into any note)

### Active Positions Dashboard
```dataview
TABLE emls_score, nrgc_phase, setup, entry, stop, size_pct
FROM "paper_trades"
WHERE status = "open"
SORT emls_score DESC
```

### Top EMLS Watchlist
```dataview
TABLE emls_score, nrgc_phase, theme, last_updated
FROM "tickers"
WHERE emls_score >= 70
SORT emls_score DESC
```

### Recent Earnings
```dataview
TABLE ticker, quarter, eps_beat_miss, guidance, tone_score, nrgc_signal
FROM "research/earnings"
SORT report_date DESC
LIMIT 10
```

## Vault Structure
```
obsidian-vault/
  tickers/        <- One note per ticker (auto-written by weekly_runner.py)
  themes/         <- One note per theme (auto-updated with edge signals)
  paper_trades/   <- One note per trade (create via QuickAdd)
  daily/          <- Weekly brief (auto-written by weekly_runner.py)
  research/
    earnings/     <- Earnings notes per company
    macro/        <- FOMC, PMI, macro regime notes
  templates/      <- Templater templates (do not edit filenames)
```

## NotebookLM vs Obsidian — When to Use What

| Use Case | NotebookLM | Obsidian |
|----------|-----------|---------|
| Long-form research reports | YES | NO |
| Deep thematic deep dives | YES | NO |
| Methodology / framework docs | YES | NO |
| AI chat Q&A about research | YES | NO (use Smart Connections) |
| Individual ticker tracking | NO | YES |
| Trade logs | NO | YES |
| Earnings notes | NO | YES |
| Auto-updated by Python | NO | YES |
| Queryable with Dataview | NO | YES |
| Local semantic search | NO | YES (Smart Connections) |
