# /alpha-pulse — Produce Alpha Pulse Daily Morning Brief

## What it does

Runs the full QuanTA 5-stage pipeline and produces the Alpha Pulse Daily — a professional Thai-language institutional morning note for BLS Wealth / QuanTA clients.

## How to use

```
/alpha-pulse
```

No arguments needed. The `pulsedaily` agent orchestrates everything automatically.

## What gets produced

1. Full Alpha Pulse Daily brief in Thai (800–1,200 words)
2. Saved to `output/alpha_pulse_[YYYYMMDD].md`
3. Printed in conversation (LINE-ready)

## Pipeline that runs

```
Stage 1 (parallel): pulsedaily-data + quanta-macro
Stage 2 (parallel): quanta-industry + pulsedaily-strategy + quanta-equity
Stage 3 (gate):     pulsedaily-review → CLEAR or REVISE
Stage 4 (write):    pulsedaily-editor → final brief
```

## Output sections

- Market Narrative (1-2 paragraphs, narrative-led)
- Key Highlights (5-7 bullets)
- What Changed (3 bullets, จาก...เป็น... framing)
- Macro & Policy Pulse (Actual/Consensus/Previous)
- Earnings & Corporate Pulse
- Thailand Focus (SET + flows + sectors + FX)
- Factors To Watch: Next 7 Days (7-12 items with dates/times)
- Verification Notes

## Status field

- **Final**: all critical numbers confirmed from 2+ sources
- **Draft with verification gaps**: some numbers single-source or unconfirmed

## Invoke
Invoke the `pulsedaily` agent to run.
