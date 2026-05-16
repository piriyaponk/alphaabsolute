# Risk Rules — AlphaAbsolute

Always loaded. See Risk.md in project root for full detail.

## Quick Reference (memorize these)

- Max position: **15%** — absolute ceiling
- Base 0 max: **5%** | Base 1 max: **8%** | Bottom Fish pre-Stage 2: **4%**
- Stop loss trigger: **-8% from entry** → mandatory review
- Earnings within 5 days: **NO new entry**
- Stage 3 or 4: **NO BUY, no exceptions**
- Theme concentration: **50% max**
- ADTV: position ≤ **20% of 6M ADTV**

## Agent Enforcement

Every agent MUST include this block in any BUY recommendation:

```
RISK CHECK:
- Position size requested: X% (limit: Y%)
- ADTV check: $Xm daily (20% = $Ym max position)
- Stage/Wyckoff gate: GREEN / YELLOW / RED
- Earnings in next 5 days: YES / NO
- Risk.md compliant: YES / NO
```

If any check fails → recommendation is BLOCKED until resolved.
