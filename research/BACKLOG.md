# Hermes strategy research backlog

The background research agent works ONE item per run (top unchecked first), appends
findings to LOG.md, then checks the item off (or adds follow-ups). Methodology is
non-negotiable: KuCoin data, costs from strategy.yaml, **train→test selection**
(never pick params on the test set), report out-of-sample only, be honest about
survivorship + sample size. A finding only "counts" if it survives an honest OOS test.

Tools already in repo: scripts/xsmom.py, scripts/multi_asset_regime.py,
scripts/strategy_search.py, scripts/multi_asset_backtest.py. Reuse/extend them.

## Open (priority order)

- [ ] **P0 — Indicator/regime overlay on the live xsmom strategy.** The "TradingView
      MAX" 21-indicator × multi-timeframe matrix is just analytics the bot already
      computes (hermes_trading/features.py). Test honestly whether using it on the
      cross-sectional momentum legs beats the pure-momentum baseline OOS: (a) only
      go long momentum-leaders that ALSO pass a multi-TF bull/trend screen and only
      short laggards that fail it; (b) weight tilt by bull_count (conviction sizing);
      (c) a market-breadth regime gate (e.g. only deploy when >50% of the universe is
      above its 50d MA). Build it on KuCoin daily data, strict train→test selection,
      OOS only, compare Sharpe/return/maxDD against the current 30d/weekly baseline.
      Deploy (write state/strategy.xsmom_filtered.yaml) ONLY if it beats baseline OOS.
      NOTE: do NOT try to read TradingView's desktop UI as a data source — it adds no
      information ccxt doesn't already give and is fragile; compute the indicators in
      Python from the same KuCoin data the live bot uses.

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
- [ ] **P7 — Long-only spot viability.** How much edge survives without shorting
      (no perps needed)? Is long-only top-K good enough to run on spot for real?
- [ ] **P8 — Cost/turnover sensitivity.** Re-run the live config at maker vs taker
      fees; how fragile is the edge to slippage assumptions?
- [ ] **P9 — Live-vs-backtest drift tracker.** Compare the live paper equity curve
      (state/portfolio.json on Railway) to backtest expectation; flag divergence.

## Done
(findings recorded in LOG.md)
