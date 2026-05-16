# Review Mode — AlphaAbsolute

Load this context when: fact-checking outputs, auditing reports, post-mortem analysis, performance review, improving the framework.

## Active Agents in Review Mode
- Agent 16 (Fact-Check & Auditor) — primary
- Agent 12 (Risk Devil's Advocate) — risk review
- Agent 13 (Portfolio Performance) — attribution
- Agent 14 (Learning & Memory) — lessons

## Fact-Check Protocol
Every claim must pass:
1. **Number check**: Does it match the source data?
2. **Date check**: Is this the most recent available data?
3. **Logic check**: Does the conclusion follow from the data?
4. **Fabrication check**: Is this real or hallucinated?

Flag as ⚠️ UNVERIFIED if any check cannot be confirmed.
Flag as ❌ HALLUCINATION if the number appears invented.

## Post-Mortem Template
```
TRADE: [TICKER] [BUY/SELL DATE] → [EXIT DATE]
Result: [+X% / -X%]
Thesis at entry: [1-2 sentences]
What went right: [specific]
What went wrong: [specific]
NRGC phase at entry: [X] | at exit: [X]
Health check at entry: [X/8]
Rule violated (if any): [specify]
Framework update needed: [YES/NO — what change]
```

## Audit Stamp
All audited reports must end with:
```
AUDIT STAMP — Agent 16
Date: YYYY-MM-DD
Verified claims: N
Unverified: N (see flags)
Hallucinations detected: N
Overall confidence: HIGH / MEDIUM / LOW
```
