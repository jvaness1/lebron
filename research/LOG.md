# Hermes research log

Newest entries on top. Each entry: date · backlog item · what was tested · result ·
honest verdict · any follow-up added to BACKLOG.md. Be skeptical of your own wins.

---

## 2026-06-11 · P0a · REAL walk-forward of the regime gate (interactive, LOCAL data)
Redone on real KuCoin daily (the cloud agent could only use SYNTHETIC data — its
sandbox blocks all exchange APIs with 403 "host not in allowlist"). 5 sequential
~239d OOS slices, FIXED gate config (each slice genuine OOS):
  s1 gate -2.5%/Sh-0.26 vs base +22.5%/1.45 → base
  s2 gate +11.5%/0.61   vs base +40.6%/1.24 → base
  s3 gate +43.8%/1.47   vs base +76.5%/1.87 → base
  s4 gate +86.3%/3.78   vs base +97.5%/2.98 → gate
  s5 gate  -2.5%/-0.07  vs base -31.9%/-1.33 → gate (crash protection)
  → gate beats baseline only 2/5 slices.
Full gate: +513.8% / Sharpe 1.65 / maxDD 19.2% (71/167 in-market).
ACTIVE-PERIOD Sharpe 2.75 (cash periods removed) → momentum edge is REAL, NOT a
cash-variance artifact (settles that question).
VERDICT (CORRECTS the rosy single-split P0): the regime gate is DRAWDOWN PROTECTION,
not a return booster. It wins decisively only in bad regimes (s5: -32%→-2.5%) and
LAGS the ungated book in bull regimes (sits in cash, misses gains). The P0 "0.31→1.61"
was flattered because that one split included the crash the gate dodged. It's a
risk/return CHOICE: gate = much smaller worst-case DD, lower return in rallies.
Cloud agent's synthetic "3-4/5" was optimistic vs the real 2/5 — proof that synthetic
validation can't be trusted and the research must run locally on real data.
DECISION: gate stays live (DD control suits the real-money path) but flagged as a
tradeoff, not an unambiguous upgrade.

## 2026-06-11 · P7a (partial) · perp funding cost magnitude (interactive session)
OKX perp funding, 9 liquid coins, ~200 intervals each: avg ≈ +0.0017%/8h ≈ +1.9%/yr
ONE-SIDED — small. On a DOLLAR-NEUTRAL long-short book the uniform funding component
cancels (longs pay, shorts receive); only the weight×funding correlation leaves a
residual, expected to be low-single-digit %/yr — negligible vs the strategy's ~100%+
gross. CONCLUSION: funding is NOT a dealbreaker for the perp long-short version.
(Self-correction: an earlier ad-hoc "337%/yr spread" figure was a BAD calc — annualised
a cross-sectional std by ×1095; ignore it.) STILL OPEN in P7a: a proper per-rebalance
funding sim (apply each held coin's funding over the holding window) for the exact drag.

## 2026-06-11 · P7 · long-only spot viability + gate interaction (interactive session)
OOS ~479d, 24 coins, K5/R7/LB30, 15bps/side, breadth ALIGNED to test window.
  - long-short + gate (LIVE config): +123.9% / Sharpe 2.22 / maxDD 16.8%
  - long-short, no gate:             +154.7% / 1.67 / 42.7%
  - long-only + gate (spot):          -10.3% / -0.04 / 31.0%   ← gate HURTS long-only
  - long-only, no gate (spot):        +47.3% / 0.81 / 46.4%    (benchmark -31.5%)
VERDICT: the breadth regime gate is LONG-SHORT-SPECIFIC — big help to L/S (Sharpe↑,
DD↓), but it HURTS long-only (cash periods miss the oversold-long rebounds). Real-money
implications: the strong strategy needs PERPS (it's long-short). The no-perps spot path
is long-only — real (beats benchmark) but mediocre (Sharpe ~0.8, ~46% DD) and must NOT
use the breadth gate; it needs its own risk control. "Start real money on spot long-only"
is possible but materially weaker than the live paper strategy. CAVEATS: one ~16mo OOS
window, survivors-only, ~23 active rebalances.
FOLLOW-UPS (P7a): design a long-only-appropriate risk control (trailing stop / vol-target)
and re-test; quantify perp FUNDING cost to check live long-short net-of-funding still beats
spot long-only.

## 2026-06-11 · P0 · indicator/regime overlay on xsmom (interactive session)
Tested baseline pure momentum vs (a) per-coin bull-score confirmation filter,
(b) conviction tilt by bull-score, (c) market-breadth regime gate. KuCoin daily,
24 full-history coins, K5/R7/LB30, costs 15bps/side, train→test 60/40, OOS ~479d
= 68 weekly periods. Any threshold picked on TRAIN only. Script: scripts/test_overlay.py.
RESULT:
  - baseline:            OOS Sharpe 0.31, net -0.5%, maxDD 48%
  - (a) confirmation:    WORSE — Sharpe 0.30, net -13.9%
  - (b) conviction tilt: negligible — Sharpe 0.36
  - (c) BREADTH REGIME GATE (hold book only when ≥thr of universe bullish, else cash):
        STRONG — OOS Sharpe 1.61, net +73.8%, maxDD 9.3%. Robust across thr 0.3–0.7
        (Sharpe 1.25–1.61, DD 4–11%), NOT a knife-edge. In-market only ~34% of the time.
VERDICT: per-position indicator filtering does NOT help; a market-breadth REGIME GATE
does, materially (higher return + far lower DD) on a sound mechanism (momentum crashes
in bear regimes). Worth deploying to live xsmom — conservative (goes to cash when the
tape is broadly bearish). CAVEATS: Sharpe partly flattered by zero-return flat periods;
one ~16mo OOS window; ~23 active rebalances; survivors-only universe.
FOLLOW-UPS added: walk-forward the gate across multiple windows; test BTC>200d-MA as an
alternative regime signal; does the gate also rescue the daily-frame variant (P1)?

## 2026-06-11 · seed · baseline established by the interactive session
- Live strategy: cross-sectional momentum, 24-coin universe, 30d lookback, weekly
  rebalance, long top-5 / short bottom-5, dollar-neutral, size 0.3. Backtest
  (survivorship-mitigated, ~480d OOS): +143% / Sharpe ~1.3 while market −32%.
  Robust across K3/5/8 and the parameter neighbourhood. Deployed to Railway paper.
- Data depth used: ~1,200 daily bars (~3.3y) × ~24 coins; ~68 weekly OOS periods
  (modest sample — main known weakness alongside survivorship).
- Rebalance-frequency test (30d signal): weekly Sharpe 1.67 ≫ daily 0.53. BUT a
  later daily-rebalance lookback sweep hinted 14d/K3/daily → +179%/1.42 OOS
  (IN-SAMPLE-SELECTED — unvalidated; see backlog P1).
- Prior dead ends (do not revisit without a new angle): RSI dip-buy (no edge after
  costs); Donchian 1h breakout (worked on SOL, did NOT generalise — 43% of coins
  positive, equal-weight ~flat); all intraday/HF strategies (killed by taker fees;
  only viable at maker ≤~3bps/side).
