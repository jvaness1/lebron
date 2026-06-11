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
- [ ] **P0b — Alternative regime signals.** Test BTC>200d-MA and BTC 30d-return>0 as
      the regime gate instead of universe breadth; do they match/beat breadth, and are
      they less correlated to the book? Does any regime gate also rescue the daily-frame
      variant (P1)?
- [ ] **P1 — Validate the daily-frame variant properly.** A test-set search hinted
      daily-rebalance + 14d lookback + K3 → +179% / Sharpe 1.42 OOS, BUT that was
      in-sample-selected. Do it right: pick the best (lookback, K) on TRAIN only,
      evaluate on TEST; run 5-slice walk-forward; map the parameter neighbourhood
      (lookback 7–21, K 2–4, daily). Verdict: is a daily-frame bot deployable? If
      yes, write state/strategy.xsmom_daily.yaml + note how to run it.
- [ ] **P2 — Volatility-scaled sizing.** Replace fixed per-name weight with
      inverse-vol (or vol-target) weights. Does it raise Sharpe / cut the ~50% DD?
- [ ] **P3 — Regime filter.** Gate the book on a market-breadth / BTC-trend signal
      (e.g. only deploy when >50% of universe is above its 50d MA). Does it remove
      the losing walk-forward slices without killing total return?
- [ ] **P4 — Wider universe (40–60 coins).** Does more breadth improve the
      cross-sectional spread, or just add illiquid noise? Report with a volume floor.
- [ ] **P5 — Skip-period momentum.** Add a 3–7d skip (rank on return excluding the
      most recent days) to dodge short-term reversal. Better OOS?
- [ ] **P6 — Longer history for majors.** Pull max KuCoin daily for the longest-lived
      coins; re-test on a longer, multi-regime window to shrink sample-size risk.
- [x] **P7 — Long-only spot viability.** DONE (LOG 2026-06-11): long-only (no gate)
      is real (+47% vs benchmark -31%) but mediocre (Sharpe ~0.8, ~46% DD); the breadth
      gate HURTS long-only (it's long-short-specific). Spot real-money path is viable
      but weak and needs its own risk control. Follow-up P7a below.
- [ ] **P7a — Long-only risk control + funding cost.** Design a long-only-appropriate
      risk control (trailing stop / vol-target / BTC-trend gate) to tame the ~46% DD,
      and quantify perp FUNDING cost so we know if live long-short net-of-funding still
      beats spot long-only (decides the real-money execution path: perps vs spot).
- [ ] **P8 — Cost/turnover sensitivity.** Re-run the live config at maker vs taker
      fees; how fragile is the edge to slippage assumptions?
- [~] **P9 — Live-vs-backtest drift tracker.** TOOLING BUILT (scripts/drift_tracker.py
      + the engine now logs state/equity_history.jsonl each rebalance). Compares live
      realised equity to a backtest over the same dates and flags divergence >3pp.
      OPEN part: needs ≥3 live rebalances (a few weeks) before it can conclude — re-run
      it periodically and record the tracking error in LOG.md once data accrues.

## Done
(findings recorded in LOG.md)
