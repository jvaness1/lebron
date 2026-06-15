# Hermes research log

Newest entries on top. Each entry: date · backlog item · what was tested · result ·
honest verdict · any follow-up added to BACKLOG.md. Be skeptical of your own wins.

---

## 2026-06-15 · P4 + Coinbase universe — EXPANDED 24→36, DEPLOYED (interactive)
User is US → will trade on Coinbase, so universe MUST be Coinbase-listed. Built Coinbase
universe via ccxt (coinbase lists 400 spot bases); 23/24 current coins are on Coinbase
(only TRX isn't; BNB also dropped — not Coinbase-US retail). Expanded to liquid Coinbase
coins with history (memecoins excluded for slippage). scripts/coinbase_universe.py.
EXPANSION HELPS ROBUSTLY (multi-horizon long-only, OOS): 17 coins Sharpe 0.32 → 20 coins
0.98 → 33 coins 1.17; walk-forward expanded beats current in 4/5 slices (higher Sharpe
nearly every window). Mechanism sound: more breadth → genuinely stronger top-5 in cross-
sectional momentum. DEPLOYED strategy.yaml v06: 36 Coinbase-tradeable coins. Resolves P4
(wider universe — earlier "inconclusive" was KuCoin-history-limited; the Coinbase set has
breadth). NOTE for real trading: switch pairs to /USD + EXCHANGE_ID=coinbase and verify
each coin is enabled in the user's Coinbase account. Caveat: memecoins (SHIB/PEPE) kept as
liquid Coinbase names but carry higher slippage; expansion benefit partly window-dependent
but robust across walk-forward.

## 2026-06-15 · P6 · longer-history test — DATA-PIPELINE BLOCKED (interactive)
Attempted to validate the live long-only config on a longer multi-cycle window
(scripts/p6_longer_history.py). BLOCKED: fetching total=3000 daily × 60 coins rate-
limits KuCoin (180+ paginated calls) → most coins return partial/failed data, leaving
only BTC/ETH full. So the deep fetch is unreliable and the test produced all-zeros (too
few coins to form a 5-name book). The "only 2 coins have long history" reading is a
RATE-LIMIT ARTIFACT, not truth (at total=1200 ~24 coins reliably return ~1199 days).
VERDICT: a longer multi-cycle test is NOT cleanly achievable with the current live-API
setup — needs a throttled fetch + local data cache, or a proper historical-data provider
(point-in-time universe). The ~3yr / ~20-24 coin window remains the reliable validation
basis; the SAMPLE-SIZE CAVEAT STANDS (cannot be resolved without a better data pipeline).
Cloud bot also could not do this (no exchange access at all). No faked result recorded.

## 2026-06-12 · IMPROVEMENT FOUND — multi-horizon momentum (interactive, LOCAL data)
Tested alternative SELECTION signals for the long-only strategy (scripts/factor_research.py),
not just param tweaks. Single 60/40 OOS + 5-slice walk-forward, long-only K5 trend-filtered:
  raw 30d (live):        +52% / Sharpe 0.88 / DD 38.5%  (walk-fwd baseline)
  risk-adjusted mom:     +70% / 1.06 / 40.6%  (beats raw 2/5 slices)
  MULTI-HORIZON 14/30/60:+92% / 1.17 / 26.9%  (beats raw 4/5 slices)  ← WINNER
  momentum + low-vol:    +24% / 0.71 / 17.3%  (beats raw 0/5 — bad)
Multi-horizon (rank by AVERAGE of 14/30/60d returns) beats raw single-30d in 4/5
walk-forward slices, higher Sharpe AND lower DD, positive in EVERY slice incl. the
worst (1.05 vs 0.31). Mechanism sound (averaging timeframes picks consistent momentum,
ignores single-window noise — documented robustness gain). DEPLOYED: strategy.yaml v05,
entry.momentum_lookbacks [14,30,60]; engine momentum_multi(). First robust improvement
since the regime gate. CAVEAT: same ~3yr survivors data; real test is live forward.

## 2026-06-12 · P3 · regime-gate rescue/drag decomposition (CORRECTED) (interactive)
Cloud bot proposed a P3 finding but its headline ("higher aggregate return WITH gate,
514% vs 321%") compared MISMATCHED windows (full-period gate vs test-half no-gate) —
WRONG. Reproduced on real KuCoin, matched windows (scripts/p3_regime_rescue.py),
long-short, 5 slices:
  drag in the 4 winning slices: -188.5pp total (gate underperforms no-gate every one)
  rescue in the 1 crash slice (s5): +33.9pp (nogate -48.6% → gate -14.7%)
  AGGREGATE compounded (matched): gate +248% vs NO-GATE +925% → gate is LOWER.
VERDICT: the regime gate REDUCES both return and Sharpe; its ONLY benefit is lower
drawdown (crash avoidance). It is pure insurance with a STEEP return premium, NOT a
return booster. (This also corrects the rosy single-split P0 0.31→1.61, which was
flattered by that split's baseline eating the crash.) Consistent with improve_sweep
(no-gate test-half +321%/Sharpe2.03 > gated +78%/1.62). P3 RESOLVED.
IMPLICATION: live strategy is LONG-ONLY (gate OFF, trend filter instead) so this
doesn't change the deployment. For the long-short RESEARCH book, the gate is optional
insurance — keep only if you value the drawdown cut more than the large return give-up.
NOTE: cloud bot can meta-analyse logged results without market data, but it made a
window-matching error AND still can't push — its output must be verified before landing.

## 2026-06-11 · LONG-ONLY US-spot strategy — dual momentum (interactive, LOCAL data)
User is US (no perps → can't short), so designed the spot-tradeable long-only version.
Plain long-only momentum: real but ~43% DD. Fixes tested (scripts/longonly_design.py,
OOS ~479d, 23 coins, weekly K5):
  plain long-only:            +178% / CAGR 130% / Sharpe 1.42 / DD 43.5%
  +abs-mom (own ret>0):       +210% / Sharpe 1.56 / DD 30.0%
  +TREND filter (px>100d MA): +169% / Sharpe 1.53 / DD 21.8%  ← CHOSEN
  +BTC-regime gate:           +59%  / Sharpe 0.94 / DD 19.7% (too conservative)
  benchmarks: equal-weight hold -27%, BTC hold -33% (so all beat just-holding).
Walk-forward (trend vs plain, 5 slices): trend cut DD in 3/5; huge help in worst slice
(s5 DD 42%→22%, Sharpe -1.81→-0.60); slightly lags plain in calm bull slices. So the
trend filter is CRASH PROTECTION — halves full-window DD (43%→22%) for a small upside
cost. DEPLOYED as LIVE (strategy.yaml v04: long-only, trend_filter px>100d MA, K5/30d/
weekly, size 1.0). Long-short kept at strategy.xsmom.yaml for research. This is the only
version a US user could actually trade (spot). CAVEATS: CAGRs window-inflated (one ~16mo
OOS, survivors-only), regime-dependent, needs live forward proof. Resolves P7a (long-only
risk control); funding cost moot for spot (no perps).

## 2026-06-11 · P1 · daily-frame variant, done HONESTLY (interactive, LOCAL data)
Train-selected best daily config = lookback7/K2/gate (train Sharpe only 0.49 — weak
even in-sample). TEST OOS: +14.0% / Sharpe 0.45 / maxDD 38.2% (472 daily rebalances)
vs live WEEKLY +78.0% / Sharpe 1.62 / maxDD 19.2%. Walk-forward: daily beats weekly
3/5 slices but wildly inconsistent (-29%, +0.4%, +98%, +35%, -15.5%) at ~2x the DD.
VERDICT: daily-frame is WORSE — lower Sharpe, lower return, higher DD. The earlier
"+179%/1.42" was pure IN-SAMPLE SELECTION (test-set fitting). KILLED. Weekly wins
decisively. scripts/p1_daily.py.
=> With P1 killed, the IMPROVEMENT SEARCH IS EXHAUSTED. Live config (xsmom 30d/K5/
weekly/breadth-gate/size0.3) is the best findable on ~3yr data. Nothing left to deploy;
binding constraint is now live forward-proof (TIME).

## 2026-06-11 · P2/P4/P5/P0b · improvement sweep (interactive, LOCAL data)
OOS ~479d single split, 23 coins (only 23 of top-40 had 600+d history). Baseline
(live: 30d/K5/weekly/breadth-gate) = +78% / Sharpe 1.62 / DD 19.2%. Each variant
changes ONE thing (scripts/improve_sweep.py):
  +vol-scaled sizing (P2): +41%/1.22 — WORSE
  +skip 7d (P5):           +26%/0.79 — WORSE
  +wider universe 40 (P4): identical (only 23 coins had history) — INCONCLUSIVE
  +BTC-trend gate (P0b):   +116%/1.63/20.4% — Sharpe tie, more return; marginal, single-window
  +lookback 60:            -12%/-0.17 — WORSE (30d better)
  +K=8:                    +52%/1.74/13.6% — higher Sharpe + lower DD, lower return; tradeoff
  +no gate (ref):          +321%/2.03/30.3% — higher this window but crash-exposed (walk-forward s5)
VERDICT: NO variant robustly beats live. vol-sizing/skip/longer-lookback hurt;
BTC-gate & K=8 are within-noise tradeoffs not clear wins; wider universe inconclusive
(data-limited, overlaps P6). The live config is at the practical optimum findable on
~3yr of data. DEPLOY NOTHING — more single-window tweaking = overfitting. IMPROVEMENT
SEARCH EXHAUSTED; binding constraint is now live forward-proof (TIME), not backtesting.

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
