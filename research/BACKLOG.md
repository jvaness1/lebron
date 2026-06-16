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
- [~] **P6 — Longer history for majors.** BLOCKED (LOG 2026-06-15): deep multi-coin fetch rate-limits KuCoin → unreliable; needs throttled fetch + local cache or a historical-data provider. Sample-size caveat STANDS. orig: Pull max KuCoin daily for the longest-lived
      coins; re-test on a longer, multi-regime window to shrink sample-size risk.
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
- [ ] **P8a — Per-coin slippage realism (follow-up).** P8 modeled a UNIFORM per-side
      cost. The universe holds memecoins (SHIB/PEPE) with worse real slippage. Re-run
      P8 with name-specific costs (e.g. majors 15bps, memecoins 40-60bps) to check the
      edge still holds when the cost is concentrated in the illiquid names actually held.
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
- [ ] **P11 — Drawdown smoothing for the LIVE long-only config.** For income, DD depth and
      DURATION matter more than peak return. P2 (vol-sizing) was tested on long-SHORT —
      re-examine specifically for the live long-only 36-coin trend-filtered config: book-level
      vol-targeting, portfolio trailing stop, per-name weight caps, partial-cash in high-vol
      regimes. Goal: cut the ~22% DD and time-to-recovery without killing Sharpe. Honest OOS.
      NB (from P10): a per-name weight cap / stop also directly bounds the survivorship
      "held-death" tail (~-20pp single-week book hit when a held coin delists) — test that.
- [ ] **P12 — Signal diversification (reduce single-factor risk).** Momentum has multi-month
      droughts — the main threat to "consistent." Add a low-correlation second sleeve (e.g.
      short-horizon mean-reversion on the same universe, or a low-vol/quality tilt) and blend.
      Does a 2-sleeve blend raise the WORST quarter/year (consistency) even at some cost to
      peak return? Report correlation of sleeves + blended walk-forward.
- [ ] **P13 — Longer, multi-cycle data (unblock P6, foundational).** Build the throttled-fetch
      + local-cache (parquet) pipeline so backtests span MORE than one ~3yr window. Shrinks
      the #1 caveat (sample size) and raises the trustworthiness of every other finding.
- [ ] **P14 — Income/withdrawal & sequence-risk model.** Simulate steady monthly withdrawals
      against the equity curve (incl. the survivorship-haircut from P10). What withdrawal rate
      survives the DD profile without ruin? Defines what passive income this realistically
      supports per $ of capital — and at what point scaling capital is justified.
      NB (from P10): use the HAIRCUT curve (~+53% @20% deaths) and the negative-tail seeds as
      the realistic input, NOT the optimistic survivors-only +76% curve.

## Done
(findings recorded in LOG.md)
