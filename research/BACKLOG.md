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

- [x] **P15 — Re-validate prior findings on the less-bull-flattered 2020→ window.** DONE (LOG
      2026-06-20, scripts/p15_revalidate.py): the whole stack HOLDS qualitatively on the 2020→ AND
      2021→ multi-cycle panels (full 2022 bear in the input). P8 cost-robust (break-even ~3.6–4.3%/
      side, +ve at 60bps 4/5 WF); P10 survivorship haircut SMALLER than the prior ~1/3 (~17–25% at
      30% death, Sharpe barely moves — broad universe dilutes deaths; trend filter ~2× protection
      re-confirmed); P11 market-DD gate still a return-killer (WF 2–3/5 vs base 4–5/5), weight cap
      still the only Sharpe-invariant DD lever; P14 SWR still entirely a forward-return bet (halve
      mean → SWR 0). NO config change. Only correction = P13's already-known maxDD ~80% (not ~46%)
      + Sharpe ~0.8–0.9. Caveat → P16.

- [x] **P16 — Bear-LOCATED OOS test (sharpen P15's caveat).** DONE (LOG 2026-06-21,
      scripts/p16_bear_oos.py). Continuous warmup-correct engine (validated: reproduces P13's
      full +3349%/0.90/83% exactly), measured ONLY the rebalances dated inside the 2022 bear.
      THREE findings: (1) live edge in the 2022 bear is WORSE than P13's per-year implied —
      −68%/Sharpe −1.94/maxDD 65%/worstWk −28% (not −49.5%; P13's per-year slice-and-reset was
      warmup-contaminated → optimistic). 100d-MA filter is a LAGGING top-detector (rode the
      late-2021 book into the crash, 47% deployed through the bear). (2) P10 survivorship haircut
      placed INSIDE the bear is TINY (~2pp at 30% death) — the OPPOSITE of P15's worry — because
      the dual-mom filter excludes crashing/dying coins (held-to-death ~0.7). So the bull-located
      haircut was NOT an understatement; survivorship risk doesn't concentrate in the bear. (3) a
      bull-optimizer would TURN OFF the trend filter (drag in bulls) and lose ~15pp MORE in the
      bear (−84%) → trend-ON is bear-justified insurance. NO config change. Caveat: one bear,
      ~52 rebals. Feeds P17.
- [x] **P18 — Rank-buffer (hysteresis) rebalancing.** DONE (LOG 2026-06-22,
      scripts/p18_buffer.py): KILLED. Textbook momentum buffering (Novy-Marx & Velikov / AQR) —
      ENTER on strict top-K=5 but KEEP a held name while it stays in a wider top-N_hold band.
      Genuinely new lever (exit/hold threshold, not trend-MA/skip/gate/vol-sizing). Sanity:
      N_hold=5 reproduces live exactly. Mechanism CONFIRMED (turnover falls 0.43→0.25 across the
      band) but USELESS: P8 already showed the edge is cost-robust (break-even ~3.6%/side), so a
      40% turnover cut saves nothing, while the wider band DILUTES selection — OOS test-half
      Sharpe/return DECREASE monotonically (1.22→0.90, 413%→168%). Honest TRAIN→TEST picks
      N_hold=6 and it underperforms live OOS (1.19 vs 1.22, +385% vs +413%). N_hold=7 clears the
      LITERAL ≥4/5 WF bar but loses the OOS test half (−25% return) — same metric artifact as P17.
      Bear 2022 barely moves (−66→−64% at N_hold=12). Live "strict top-5" is the optimum for this
      lever. NO config change.

- [x] **P17 — Faster top-brake to cut the bear ENTRY.** DONE (LOG 2026-06-22,
      scripts/p17_top_brake.py): KILLED. Pursued with the genuinely-new PER-COIN angle (not the
      dead market-wide gate): (A) symmetric shorter trend MA 30–200d, (B) novel ASYMMETRIC
      slow-in(100d)/fast-out(30–75d). PREMISE FAILS — a faster MA does NOT brake the 2022 bear:
      sym 50/50 = −74.6% (vs live −66.1%) and MORE deployed (52% vs 46%); asym 100/30 ~ties
      (−67.5%). A faster MA re-enters falling coins on dead-cat bounces and multi-horizon momentum
      keeps re-selecting them, so net exposure is equal/higher. Honest TRAIN→TEST picks sym 30/30
      and it underperforms live OOS (Sh 0.81 vs 1.22, +136% vs +413%). One config (sym 50/50)
      clears the LITERAL ≥4/5 WF-Sharpe bar but is a metric artifact (OOS test-half +115%/0.77,
      gives up ~300pp + makes the bear worse). Confirms the P0/P3/P11 prior now per-coin; the live
      100d MA is the practical optimum for this lever. NO config change.
- [x] **P14 — Income/withdrawal & sequence-risk model.** DONE (LOG 2026-06-19,
      scripts/p14_income_model.py): block-bootstrap sequence-risk sim on the P10-stressed
      weekly returns w/ the P11 partial-cash dial. On the (bull-flattered) ~75%/yr stressed
      returns, SWR≤5%-5yr-ruin = 15%/yr (f=1.0), 20%/yr (f=0.5 — partial cash RAISES the safe
      rate, ruin is vol-driven). BUT halving the mean to a still-positive ~+15%/yr collapses SWR
      to ~0 → the headline SWR is entirely a forward-RETURN bet. VERDICT: treat as GROWTH
      capital, not an income annuity; withdraw conservatively (≤5–10%/yr) from accrued buffer,
      percent-of-equity not fixed-nominal. No config change. Re-run on multi-cycle data once P13
      lands. orig NBs: P10 haircut curve as input (done); partial cash = only DD dial (confirmed).

- [x] **P19 — Holding count (top_k) re-validation on the live 36-coin config.** DONE (LOG
      2026-06-23, scripts/p19_topk.py): KILLED. The improve_sweep K=8 hint does NOT replicate
      honestly — wider K MONOTONICALLY worsens the walk-forward (K≥6 → 3/5,2/5,2/5,2/5) and OOS
      return (709%→140%); only K=4 clears ≥4/5 WF but its OOS Sharpe (1.21) is below live's (1.22).
      Honest TRAIN→TEST picks K=7 (bull-train-optimal) and UNDERPERFORMS live K=5 on BOTH return
      (+283% vs +413%) and Sharpe (1.14 vs 1.22). Mechanism: a wider K cuts bear-2022 DD only by
      being less deployed (inmkt 56%→31%, Sharpe identically ~−1.66) and gives up bull return 1:1
      — the SAME consistency dial as P11 partial-cash/weight-cap, not free diversification. Live
      strict top-5 is the optimum for this lever. NO config change.

- [x] **P20 — Rebalance-PHASE (weekday) timing-luck robustness.** DONE (LOG 2026-06-23,
      scripts/p20_rebalance_phase.py): ROBUST + honesty fix. Every prior backtest uses ONE fixed
      rebalance grid (i starts at max(LBS)=60, steps R=7 → one weekday). Re-ran the exact live
      engine at all 7 weekly start offsets on the 2020→ panel. Edge is NOT a grid artifact: 7/7
      phases positive (full + OOS); Sharpe phase-robust (full 0.87–1.25, std 0.13). BUT cumulative
      RETURN is wildly phase-sensitive (+1032%…+7393% for the SAME strategy, CV 0.57) → quote
      Sharpe + a band, NOT point returns (P13's "+3349%" is one phase's draw). offset-0 (all prior
      BTs) is a CONSERVATIVE full-window draw (pctile 29), so the prior numbers are honest/under-
      stated, not cherry-picked. 2022 bear phase-STABLE (−56%…−68%, std 4.8%). NO config change
      (phase unknowable ex-ante). Caveat: 7 overlapping phases of one series — grid-alignment luck,
      not regime luck.

- [x] **P21 — Risk-adjusted MULTI-horizon momentum ranking.** DONE (LOG 2026-06-24,
      scripts/p21_riskadj_momentum.py): NOT ROBUST (soft dead-end). ra_pooled (raw_multi/60d-vol)
      BEATS live OOS at offset-0 (Sharpe 1.41 vs 1.22, +599% vs +413%, lower maxDD) and TRAIN
      honestly picks it — BUT the P20 phase test kills it: the Sharpe edge across all 7 weekly
      offsets is mean +0.05, std 0.08, positive only 4/7; offset-0 (the grid all prior BTs use)
      is the SINGLE MOST FAVORABLE phase. Edge also reverses below baseline at 90-120d vol
      windows. Bear-2022 only marginally better. Direction weakly favorable everywhere but within
      noise phase-averaged -> NO config change. Live raw multi-horizon stands.

- [x] **P22 — Phase-TRANCHED (overlapping) rebalancing to diversify away timing luck.** DONE
      (LOG 2026-06-24, scripts/p22_tranched.py): FREE CONSISTENCY TWEAK, NOT A SHARPE EDGE → NO
      config change. Built the Jegadeesh-Titman overlapping construction P20 left untested (hold
      all 7 weekly phases at once = rebalance 1/7 of book daily). NO free parameter. Daily-marked
      accounting validated (single-phase offset-0 Sharpe 0.89 ≈ P20/P13 weekly ~0.90). But the 7
      phases correlate at ρ=0.95 (same book offset by days) → theory Sharpe lift only ~2%, and
      that's all it delivers: dSharpe vs honest phase-mean +0.02 full/+0.03 OOS, maxDD −1/−4pp,
      WF 5/5 but magnitudes +0.01..+0.05 (noise). It does NOT beat live on risk-adjusted terms.
      What it genuinely buys is eliminating the unrewarded weekday-luck DISPERSION (cumulative-net
      CV 0.59→0, locks in ~phase-mean deterministically) — a real outcome-uncertainty cut (P20's
      worry) but not a backtest-improvable Sharpe/return metric. Optional operational choice (2-
      tranche {0,4} ≈ all the benefit at 2 rebal days/wk). Closes P20's open door.

- [x] **P23 — Residual (beta-adjusted) momentum ranking.** DONE (LOG 2026-06-25,
      scripts/p23_residual_momentum.py): NOT ROBUST (soft dead-end). NEW hypothesis grounded in
      P12 (book "dominated by common market beta") + P16 (rode high-beta into the 2022 bear); the
      Blitz/Huij/Martens Residual Momentum anomaly ranks by momentum AFTER stripping market beta
      (genuinely new vs P21's total-vol normalisation). Full-window Sharpe looks better (1.04-1.12
      vs 0.91) but lives in the 2020-23 TRAIN half: honest TRAIN->TEST picks resid_t_ew and it
      UNDERPERFORMS live OOS (Sh 0.97 vs 1.22, +207% vs +413%). The 7-phase test (P21's killer)
      kills it — OOS Sharpe edge vs raw mean -0.21..-0.24, positive 0-1/7. HEADLINE claim FAILS:
      2022 bear barely moves (-64.4% vs -66.1%, identical worstWk -27.3%) because the trend filter
      already gates the bear and long-only crypto names all crash together regardless of beta-
      adjusted ranking. 2021-> window favorable but 2020-> not -> window luck. Live raw multi-
      horizon stands. NO config change. (May still help a long-short book; live is long-only -> moot.)

- [x] **P24 — Correlation-aware (cluster-decorrelated) selection within the momentum book.**
      DONE (LOG 2026-06-25, scripts/p24_decorrelated_selection.py): KILLED. Honest TRAIN->TEST picks
      tau=0.8 (TRAIN Sh 1.06 >> raw 0.77) but it UNDERPERFORMS live OOS (Sh 1.11 vs 1.22, +323% vs
      +413%); the 7-phase test is decisive — decorr's OOS Sharpe edge vs raw is mean -0.13, std 0.06,
      positive 0/7 (loses at EVERY weekly offset). At EQUAL inmkt (80%, NOT de-levering) the corr cap
      forces skipping the top-momentum name when it correlates with a held one, and that sacrificed
      momentum costs more than diversification buys (trendpool-backfill alone also dilutes, 1.14 OOS,
      same as P18/P19). Bear-2022 is the only place the mechanism shows (-61% vs -66% at equal deploy)
      but worstWk identical (-27.3%, coincident market crash) and it doesn't generalise. Confirms P12
      ("market-beta-dominated, can't cheaply diversify") on the SELECTION axis. Live strict top-5 is
      the optimum. NO config change. (May help a long-short book; live is long-only -> moot.)
      NEW hypothesis (added 2026-06-25). Grounded in the LOG's strongest recurring theme: the
      book is "dominated by common market beta" (P12), "rode the high-beta cluster into the 2022
      bear" (P16), and EVERY consistency dial so far (P11 weight-cap, P19 wider-K, P12 sleeves)
      reduces to PARTIAL CASH — cut net exposure 1:1 with return. Decorrelation is a genuinely
      DIFFERENT axis: keep all 5 slots fully invested, but among trend-passing momentum candidates
      greedily pick names whose trailing-60d return correlation to already-selected names is below a
      cap τ (skip the redundant, take the next-highest momentum). Hypothesis: holding 5 LESS-
      redundant names cuts drawdown WITHOUT the 1:1 return cost (deployment held equal via backfill).
      Honesty: decompose raw(live) vs trendpool(backfill, τ=1) vs decorr(τ<1) so the decorrelation
      effect is isolated from the backfill effect; report inmkt everywhere (a DD cut that only comes
      from lower deployment is just partial cash again — the P11/P19 trap). Pick τ on TRAIN; report
      TEST + 5-slice WF + the P20/P21 7-phase robustness killer + bear-2022 located. scripts/p24_*.py

- [x] **P25 — Momentum-horizon COMPOSITION robustness (the core-alpha parameter).** DONE
      (LOG 2026-06-29, scripts/p25_horizon_composition.py): STRONG LEAD, no config change. The live
      equal-weight-[14,30,60] blend is NOT TRAIN-optimal (TRAIN Sharpe 0.89, 3rd-LOWEST of 10
      variants) because averaging RAW returns secretly over-weights the slowest horizon (60d returns
      ~2-4x the magnitude of 14d). A CLUSTER of shorter / contribution-equalized variants beats it on
      TRAIN AND on 3 windows (2020-split OOS, 2021-> OOS, AND an INDEPENDENT 2017-2020 window); adding
      90d HURTS. The P20/P21 phase killer SEPARATES the cluster: config-expressible horizon-set swaps
      (fast[7,14,30], single30) are GRID-LUCK (phase-avg dSharpe ~0.00), but CONTRIBUTION-EQUALIZATION
      (rank-avg / inverse-horizon wt) is genuinely phase-robust (rankavg full-window +0.13 7/7, wshort
      +0.11 6/7) — unlike P21/P23 it survives. BUT the robust fix needs an ENGINE change (rank/weight
      in portfolio.py, out of research scope) and the config-only shortcut isn't robust, so NO deploy.
      Real, phase-robust, TRAIN-supported -> feeds P26.
- [ ] **P26 — Pre-registered contribution-equalized momentum (the P25 lead, done right).** P25 found
      the live equal-weight-of-RAW-returns [14,30,60] blend is implicitly a slow-momentum signal and is
      beaten on TRAIN+OOS+phase by equalizing each horizon's CONTRIBUTION (rank-average per horizon, or
      inverse-horizon weighting). This run could not deploy it: (a) it needs a small ENGINE feature (a
      `momentum_combine: rank|invwt` option in the xsmom signal, human+tests own engine/live behavior),
      and (b) P25 found it by a 10-variant sweep — must be RE-TESTED as a SINGLE pre-registered ex-ante
      variant to avoid multiple-comparisons bias. Plan: (1) propose the engine option to a human (NOT a
      research task — flag it); (2) pre-register rankavg as the one hypothesis; (3) deep-validate on the
      2017-> panel as more coins gain history, 5-slice WF + 7-phase + bear-located; (4) ONLY if it holds
      ex-ante, write the candidate. Until the engine supports it, this is BLOCKED on a human engine change.

- [x] **P27 — Rebalance INTERVAL (holding period) re-validation.** DONE (LOG 2026-07-07,
      scripts/p27_rebalance_interval.py): KILLED. Live rebalances weekly (R=7); the only prior
      frequency test (P1) went FASTER (daily, worse), so this tests SLOWER (R∈{10,14,21,28}) — a
      genuinely new, config-expressible lever (rebalance_days). Sanity: R=7 reproduces live (2020→
      Sharpe 0.907/+1220%/DD81%). Slower is monotonically worse phase-averaged (phaseMean Sharpe R7
      1.06 > R10 1.03 > R14 0.97 > R21/28 0.84); R14/21/28 fail WF (0–1/5) and worsen the 2022 bear.
      R=10 is an OFFSET-0 MIRAGE: default-grid full Sharpe 1.09/+3461% and TRAIN even picks it, but
      it fails all three honest gates — OOS test-half Sharpe 1.07<1.22, WF 3/5, and the P20/P21
      PHASE killer shows phase-mean Sharpe 1.03<1.06 (dSharpe −0.03, +ve only 3/7). The +3461% was
      one lucky weekday draw (P20 phase-sensitivity), same class as P21/P25 config-only shortcuts.
      Both frequency directions now closed (P1 faster, P27 slower). Live weekly R=7 stands. NO config
      change.


## Done
(findings recorded in LOG.md)
