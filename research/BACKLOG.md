# Hermes strategy research backlog

The background research agent works ONE item per run (top unchecked first), appends
findings to LOG.md, then checks the item off (or adds follow-ups). Methodology is
non-negotiable: KuCoin data, costs from strategy.yaml, **train→test selection**
(never pick params on the test set), report out-of-sample only, be honest about
survivorship + sample size. A finding only "counts" if it survives an honest OOS test.

Tools already in repo: scripts/xsmom.py, scripts/multi_asset_regime.py,
scripts/strategy_search.py, scripts/multi_asset_backtest.py. Reuse/extend them.

## Open (priority order)

- [x] **P0 — Indicator/regime overlay on the live xsmom strategy.** DONE (see LOG
      2026-06-11): per-coin confirmation filter & conviction tilt do NOT help; a
      MARKET-BREADTH REGIME GATE does (OOS Sharpe 0.31→1.61, maxDD 48%→9%, robust
      across thresholds). Recommended for deployment. Follow-ups below (P0a/P0b).
- [x] **P0a — Walk-forward the breadth regime gate.** DONE on REAL KuCoin data (LOG
      2026-06-11; scripts/walkforward_p0a.py). Gate beats baseline only 2/5 OOS slices;
      it's DRAWDOWN PROTECTION (wins in crashes, lags in rallies), NOT a return booster.
      Active-period Sharpe 2.75 → momentum edge is real (not a cash artifact). Gate kept
      live as a risk/return tradeoff. The single-split P0 overstated it.
- [x] **P0b — Alternative regime signals.** DONE: BTC-trend gate ~ties breadth (1.63 vs 1.62); kept breadth. orig: Test BTC>200d-MA and BTC 30d-return>0 as
      the regime gate instead of universe breadth; do they match/beat breadth, and are
      they less correlated to the book? Does any regime gate also rescue the daily-frame
      variant (P1)?
- [x] **P1 — Daily-frame variant.** DONE (LOG): honest train→test = WORSE than weekly (Sharpe 0.45 vs 1.62, DD 38% vs 19%). The +179% was in-sample luck. KILLED. orig: A test-set search hinted
      daily-rebalance + 14d lookback + K3 → +179% / Sharpe 1.42 OOS, BUT that was
      in-sample-selected. Do it right: pick the best (lookback, K) on TRAIN only,
      evaluate on TEST; run 5-slice walk-forward; map the parameter neighbourhood
      (lookback 7–21, K 2–4, daily). Verdict: is a daily-frame bot deployable? If
      yes, write state/strategy.xsmom_daily.yaml + note how to run it.
- [x] **P2 — Volatility-scaled sizing.** DONE (LOG): WORSE than equal-weight (Sharpe 1.62→1.22). Skipped. orig: Replace fixed per-name weight with
      inverse-vol (or vol-target) weights. Does it raise Sharpe / cut the ~50% DD?
- [x] **P3 — Regime filter.** DONE (LOG 2026-06-12, CORRECTED vs cloud-bot's mismatched-window claim): gate is pure drawdown insurance with a steep return premium (drag -188pp in winning slices vs +34pp crash rescue; aggregate gate +248% < no-gate +925%), NOT a return booster. Live is long-only (gate off) so no change. orig: Gate the book on a market-breadth / BTC-trend signal
      (e.g. only deploy when >50% of universe is above its 50d MA). Does it remove
      the losing walk-forward slices without killing total return?
- [x] **P4 — Wider universe.** DONE (LOG 2026-06-15): expanded to 36 Coinbase-tradeable coins; robustly beats the old 24 (4/5 walk-forward, Sharpe up). Deployed v06. orig: INCONCLUSIVE: only 23 of 40 had history (overlaps P6). orig: Does more breadth improve the
      cross-sectional spread, or just add illiquid noise? Report with a volume floor.
- [x] **P5 — Skip-period momentum.** DONE: skip 7d WORSE (1.62→0.79). Skipped. orig: Add a 3–7d skip (rank on return excluding the
      most recent days) to dodge short-term reversal. Better OOS?
- [x] **P6 — Longer history for majors.** RESOLVED via P13 (LOG 2026-06-20): the throttled
      cache unblocked the deep fetch; the live config now tested on 2017-2026 (~8.7y). Edge
      persists (7/9 positive years, +3349%) but the recent window was BULL-FLATTERED (Sharpe
      1.33→0.91, true maxDD ~80% not ~46%). Sample-size caveat materially shrunk for survivors.
      orig: BLOCKED — deep multi-coin fetch rate-limits KuCoin; needed throttled fetch + cache.
- [x] **P7 — Long-only spot viability.** DONE (LOG 2026-06-11): long-only (no gate)
      is real (+47% vs benchmark -31%) but mediocre (Sharpe ~0.8, ~46% DD); the breadth
      gate HURTS long-only (it's long-short-specific). Spot real-money path is viable
      but weak and needs its own risk control. Follow-up P7a below.
- [x] **P7a — Long-only risk control.** DONE (LOG): per-coin TREND filter (px>100d MA, dual momentum) halves long-only DD 43%→22%, kept Sharpe ~1.5. DEPLOYED as live US-spot strategy v04. Funding moot for spot. orig: Design a long-only-appropriate
      risk control (trailing stop / vol-target / BTC-trend gate) to tame the ~46% DD,
      and quantify perp FUNDING cost so we know if live long-short net-of-funding still
      beats spot long-only (decides the real-money execution path: perps vs spot).
- [x] **P8 — Cost/turnover sensitivity.** DONE (LOG 2026-06-15): COST-ROBUST. Live
      config turnover is only ~0.42x/week (low); net 78%@15bps → 59%@60bps (Coinbase-
      real), Sharpe 1.04→0.91, positive 5/5 walk-forward at BOTH levels. Analytic
      break-even ~3.6%/side (~24x the backtest assumption). Edge is NOT a cost artifact;
      raises confidence in the real-money Coinbase deploy. scripts/p8_cost_sensitivity.py.
- [x] **P8a — Per-coin slippage realism (follow-up).** DONE (LOG 2026-06-17,
      scripts/p8a_percoin_slippage.py): EDGE ROBUST. Per-coin tiered cost (majors 15 /
      mid 25 / thin-meme 45-65bps total) → tiered-realistic 130% vs uniform-15 138%; the
      crux is that illiquid tier-C names take only ~31% of the book's turnover (plurality
      43.6% is liquid mid-caps), so concentrating slippage there barely moves it — tier-C
      slippage would need ~8%/side to erase the edge. Per-coin realism CONFIRMS cost-safety.
      Noted: rolling data window shifted the most-recent OOS slice negative (~-25%) — feeds P11.
- [~] **P9 — Live-vs-backtest drift tracker.** TOOLING BUILT (scripts/drift_tracker.py
      + the engine now logs state/equity_history.jsonl each rebalance). Compares live
      realised equity to a backtest over the same dates and flags divergence >3pp.
      OPEN part: needs ≥3 live rebalances (a few weeks) before it can conclude — re-run
      it periodically and record the tracking error in LOG.md once data accrues.

### Consistency & robustness agenda (added 2026-06-15) — the path to steadier real-money returns

The live goal is CONSISTENT income, so prioritize robustness, drawdown depth+duration,
and honesty over peak backtest return. Highest-value first.

- [x] **P10 — Survivorship-bias stress test (HIGHEST PRIORITY, honesty).** DONE (LOG
      2026-06-16, scripts/p10_survivorship.py). Random-dropout proxy on the real survivor
      panel (truncate real prices → crash to 2% → delisted-flat), 200 seeds, exact live
      config. EDGE SURVIVES with a ~1/3 HAIRCUT: baseline +76%/Sh1.08 → @20% 3-yr death rate
      +53%/0.90 → @30% +30%/0.68; positive 5/5 walk-forward. The live dual-momentum trend
      filter PROTECTS (≈doubles stressed return vs filter-off, DD 54→41%) — survivorship
      insurance, not just crash insurance. Discount forward expectations ~1/3; edge is real.
      Caveat: uniform-timing proxy understates bear-regime clustering; tail (held-death) is
      a ~-20pp single-week book hit → motivates P11 weight cap/stop. Feeds P14.
- [x] **P11 — Drawdown smoothing for the LIVE long-only config.** DONE (LOG 2026-06-18,
      scripts/p11_dd_smoothing.py): NO ROBUST WIN. Vol-targeting = no-op (DDs aren't preceded
      by high vol). Market-DD gate looks great single-split (Sharpe 1.05->1.30, DD 49->24%) but
      walk-forward exposes it as the known DD-insurance/whipsaw tradeoff (3/5+ vs base 4/5+;
      helps the crash slice, tanks the recovery/grind slices) — same flattering-by-included-
      crash artifact as the original P0; echoes P7 (gate hurts long-only). Per-name weight cap
      = pure linear de-leverage (Sharpe-INVARIANT 1.05); it bounds maxDD + the P10 held-death
      tail PROPORTIONALLY (50% inv -> DD 26%, worst wk -22%->-11%) at 1:1 return cost. Only
      honest DD lever = PARTIAL CASH. No config change. Feeds P14.
- [x] **P12 — Signal diversification (reduce single-factor risk).** DONE (LOG 2026-06-19,
      scripts/p12_diversification.py): NO HONEST DIVERSIFIER, KILLED. Short-term reversal (the
      textbook momentum complement) is POSITIVELY correlated (~0.6-0.7 train) with the momentum
      book — in a long-only crypto universe both sleeves are dominated by common market beta.
      Low-vol has low corr (TEST 0.16) but near-zero standalone return, so blending only de-risks
      monotonically down the risk/return line (worstQ -34%->-17% at 60% sleeve, but Sharpe falls
      the whole way and worstWk is flat — coincident market crash hits both). TRAIN-selected blend
      = 100% momentum (no blend raises Sharpe). Same in character as P11's partial-cash lever, not
      free consistency. The consistency dial remains partial-cash (P11/P14). orig: Momentum has
      multi-month droughts. Add a low-correlation second sleeve and blend.
- [x] **P13 — Longer, multi-cycle data (unblock P6, foundational).** DONE (LOG 2026-06-20,
      scripts/data_cache.py + scripts/p13_longer_history.py): built a SERIAL, throttled, retry+
      backoff, INCREMENTAL OHLCV cache (data/ohlcv/, CSV — venv has no pyarrow; gitignored/
      regenerable). Cached all 36 live coins back to 2017-10 (~8.7y vs prior ~3.3y). Re-ran the
      live config across cycles: edge PERSISTS (7/9 positive years, +3349%) but the recent ~3yr
      basis was BULL-FLATTERED (Sharpe 1.33→0.91; true maxDD ~80%, ~2x what prior P-numbers
      showed). Foundational: `from data_cache import load_panel` now backs cheap longer-window
      re-tests. No config change (honesty correction, not a beat). Feeds P14/P15.

- [ ] **P15 — Re-validate prior findings on the less-bull-flattered 2020→ window.** Now that
      the cache exists, cheaply re-run P8 (cost), P10 (survivorship), P11 (DD smoothing) and
      P14 (income/SWR) on the 2020→ multi-cycle panel instead of the bull-flattered 2023→ window.
      P13 showed Sharpe ~0.9 / maxDD ~80% on the longer basis — does the ~1/3 P10 haircut, the
      partial-cash DD dial, and the SWR all hold up when the input distribution includes the full
      2022 bear? Highest-value because it firms up (or corrects) the whole findings stack at once.
- [x] **P14 — Income/withdrawal & sequence-risk model.** DONE (LOG 2026-06-19,
      scripts/p14_income_model.py): block-bootstrap sequence-risk sim on the P10-stressed
      weekly returns w/ the P11 partial-cash dial. On the (bull-flattered) ~75%/yr stressed
      returns, SWR≤5%-5yr-ruin = 15%/yr (f=1.0), 20%/yr (f=0.5 — partial cash RAISES the safe
      rate, ruin is vol-driven). BUT halving the mean to a still-positive ~+15%/yr collapses SWR
      to ~0 → the headline SWR is entirely a forward-RETURN bet. VERDICT: treat as GROWTH
      capital, not an income annuity; withdraw conservatively (≤5–10%/yr) from accrued buffer,
      percent-of-equity not fixed-nominal. No config change. Re-run on multi-cycle data once P13
      lands. orig NBs: P10 haircut curve as input (done); partial cash = only DD dial (confirmed).

## Done
(findings recorded in LOG.md)
