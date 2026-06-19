# Hermes research log

Newest entries on top. Each entry: date · backlog item · what was tested · result ·
honest verdict · any follow-up added to BACKLOG.md. Be skeptical of your own wins.

---

## 2026-06-19 · P14 · income / withdrawal & sequence-of-returns risk model — SWR is entirely a forward-return bet; treat as GROWTH capital, not income
Why this matters: the live goal is consistent income. This asks the decisive question —
what fixed monthly withdrawal survives the drawdown profile without ruin (a Safe Withdrawal
Rate for a crypto-momentum book)? Built scripts/p14_income_model.py. Honest inputs per the
P10/P11 NBs: return distribution = the P10 survivorship-STRESSED weekly net returns (live
config + random deaths @20% 3-yr rate, 60 seeds pooled → 9,720 weekly samples, fat left
tail, worstWk -39.5%), NOT the optimistic survivors-only curve; consistency dial = PARTIAL
CASH (P11's per-name cap = uniform de-leverage, invested f∈{1.0,0.75,0.50}, cash@0%).
SEQUENCE RISK via block bootstrap (B=8w preserves DD clustering) → 4,000 synthetic paths
per cell, fixed monthly withdrawals, ruin = can't fund next withdrawal.

RESULTS (5yr horizon, monthly withdrawals):
  - Pooled returns: survivors-only ~+88%/yr → P10-stressed ~+75%/yr (the ~1/3 P10 haircut).
  - On the P10-stressed (still bull-flattered) returns, SWR (≤5% 5yr-ruin):
      f=1.00 → 15%/yr · f=0.75 → 15%/yr · f=0.50 → 20%/yr.
    Partial cash RAISES the safe rate (ruin is vol-driven at these withdrawal levels, so
    de-leveraging cuts ruin faster than it cuts the median) — a genuine sequence-risk lever,
    consistent with P11's "partial cash is the only honest DD dial".
  - Ruin rises with horizon (sequence risk compounds): 20%/yr withdraw → 0% @1yr, 4% @3yr,
    9% @5yr, 13% @10yr. Block length barely matters (9–11% across B=1..16w) → autocorrelation
    assumptions aren't driving it; the MEAN is.
  - ★ RETURN-STRESS (the punchline): halve the pooled mean to ~+15%/yr (still POSITIVE, still
    arguably generous for forward crypto) → SWR collapses to 0%/yr; even a 5% withdrawal carries
    ~6% 5yr-ruin, 15% carries 27%. The entire "15% SWR" rests on assuming the backtest window's
    ~75%/yr return persists. It is a forward-RETURN bet, not a withdrawal-rule guarantee.
VERDICT: No config change (this is an allocation/income-policy finding, not a strategy edit).
Honest takeaway for the real-money plan: this is GROWTH capital, not an income annuity. Do NOT
size a fixed monthly draw off the backtest SWR. Prudent policy = withdraw conservatively
(≤5–10%/yr) and only from a buffer that has actually accrued (percent-of-current-equity, not
fixed-nominal, dodges the worst sequence risk); partial-cash de-leverage modestly improves
ruin-safety per unit of income if/when drawing. CAVEATS: one ~3.3yr survivor window (one bull +
one bear) → the bootstrap inherits a bull-flattered mean even after the P10 haircut; bootstrap
breaks true regime persistence beyond the block; fixed-nominal withdrawals are the worst case
(percent-of-equity is safer but doesn't give steady $). The stressed row, not the headline SWR,
is the number to plan against. Feeds: when P13 lands multi-cycle data, re-run on a less
bull-flattered window to firm up the forward-return input.

## 2026-06-19 · P12 · signal diversification (2nd sleeve blend) — NO HONEST DIVERSIFIER on long-only crypto; KILLED
Why this matters: momentum has multi-month droughts — the chief threat to "consistent
income". Tested whether blending the LIVE long-only momentum book with a low-correlation
second long-only sleeve on the SAME 36-coin universe raises the WORST quarter (consistency)
honestly OOS. Built scripts/p12_diversification.py: each sleeve = top-5, equal weight, weekly,
15bps/side; honest train->test (60/40) + 5-slice walk-forward; sleeve params & blend weight
(alpha = MOM capital share) SELECTED ON TRAIN ONLY. "worstQ" = min compounded return over
rolling 13-rebalance (~quarter) windows.
Candidate second sleeves (standalone TRAIN Sharpe / TRAIN corr-to-MOM):
  SHORT-TERM REVERSAL (long the K most-oversold, lb 3-10d, +/- 100d-MA gate):
    best REV lb3 no-trend Sharpe 0.96 — but corr-to-MOM 0.72 (all REV variants corr 0.56-0.74)
  LOW-VOL (long K lowest 30/60d realized-vol): vw60 Sharpe 0.43, corr 0.55 (best of the LV set)
KEY MECHANISM: short-term reversal — the TEXTBOOK momentum complement — is POSITIVELY
correlated (~0.6-0.7) with the momentum book here, because in a long-only crypto universe
BOTH sleeves are dominated by the common market beta (everything co-moves), so reversal does
NOT diversify. Low-vol has the lowest correlation (TEST 0.16) but a near-zero standalone
return (Sharpe 0.43 << MOM 1.88), so blending it only DILUTES.
RESULT: the TRAIN-selected blend is alpha=1.0 (= MOM-ONLY) — NO blend raises TRAIN Sharpe.
On TEST, adding the low-vol sleeve walks the book monotonically DOWN the risk/return line:
  alpha 1.0 (mom only): net +129% Sharpe 1.35 maxDD 38.0% worstQ -34.0% worstWk -15.5%
  alpha 0.6 (60/40):    net  +70% Sharpe 1.21 maxDD 25.7% worstQ -21.9% worstWk -15.3%
  alpha 0.4 (40/60):    net  +41% Sharpe 0.97 maxDD 19.8% worstQ -16.9% worstWk -15.4%
  alpha 0.0 (sleeve):   net  -11% Sharpe -0.09 maxDD 40.2% worstQ -40.2% worstWk -15.5%
worstQ does improve (-34%->-17%) but ONLY by giving up proportional return, and Sharpe falls
the whole way — this is identical in character to holding partial cash / a lower weight, the
exact lever P11 already identified as the ONLY honest DD dial. It is NOT free consistency from
low-correlation alpha. Note worstWk is ~flat (-15.5% -> -15.4%) at every alpha: the worst week
is a market-wide coincident crash that hits the low-vol sleeve too, so the 2nd sleeve gives no
tail protection either. 5-slice walk-forward: MOM-only and the train-picked blend are identical
(alpha=1.0). VERDICT: NEGATIVE — no second sleeve on this universe is a Sharpe-positive
diversifier; reversal co-moves with momentum, low-vol only de-risks like partial cash. The
"consistency" lever remains partial-cash (P11/P14), not factor diversification. No config
change. KILLED. CAVEATS: long-only spot only (a long-SHORT reversal sleeve could be market-
neutral and genuinely uncorrelated, but the live US path can't short — moot); survivors-only
36-coin / one ~3yr window (P13 still the binding caveat); did not test cross-asset (BTC-vs-alt
rotation) or an off-crypto sleeve.

## 2026-06-18 · P11 · drawdown smoothing for the LIVE long-only config — NO ROBUST WIN; only honest lever is partial-cash
Why this matters: for steady income, DD depth + recovery time beat peak return, and P10
flagged a ~-20pp single-week "held-death" tail. P2 (vol-sizing) was tested on long-SHORT;
re-examined the DD levers specifically for the LIVE long-only 36-coin trend-filtered config.
Built scripts/p11_dd_smoothing.py (extends the P8 harness): EXACT live config (multi-horizon
14/30/60, K5, weekly, 100d-MA trend), 36/36 coins, 15bps/side, honest train->test (60/40,
report OOS only) + 5-slice walk-forward. Three overlays, each param SELECTED ON TRAIN:
  BASELINE OOS: net +83.4% · Sharpe 1.05 · maxDD 48.7% · worst week -22.0%
  [1] BOOK VOL-TARGETING (scale exposure by clip(target/trailing_book_vol,0,1), cap 1.0):
      train picks target=1.00 (the only target with DD<=baseline) -> the scalar essentially
      never binds (crypto book vol > target) -> OOS = +92%/1.11/45.5%, i.e. a NO-OP. Lower
      targets CUT train Sharpe and did NOT cut DD (DD rose to 42-44% at targets 0.5-0.8).
      MECHANISM: the big drawdowns are NOT preceded by elevated trailing vol (momentum
      crashes are trend-reversals from calm), so a backward-looking vol-target can't dodge
      them. NEGATIVE — vol-targeting does not smooth this strategy's DD.
  [2] MARKET-DRAWDOWN GATE (cash when equal-weight market index >X% off its running peak;
      causal; re-enters when it recovers — a clean fix for a naive book trailing-stop, which
      is invalid: once in cash the book's equity is frozen below peak so it can NEVER re-arm):
      train picks gate=30%. SINGLE-SPLIT OOS looks great: +99.3%/1.30/maxDD 24.1% — beats
      baseline on all three. BUT the 5-slice walk-forward exposes it as the SAME drawdown-
      insurance/whipsaw tradeoff already documented in P0a/P3/P7, NOT a robust Sharpe win:
        slice Sharpe  base [0.22, 1.49, 2.83, 1.81, -0.37] (4/5+)
                      gate [-0.15,1.50, 3.30, 1.81, -2.64] (3/5+)
        slice maxDD%  base [35, 31, 26, 16, 26] -> gate [22, 31, 11, 16, 15]
      The gate reliably CUTS maxDD every slice, HELPS in the crash slice (s3 +192->+240,
      dodged the drop) — but WHIPSAWS in choppy recoveries (s1 -0.5->-6.3) and tanks the
      worst slice's Sharpe (s5 -0.37->-2.64: cash through a grind-down then re-entered into
      more pain). The single-split +1.30 was FLATTERED because the OOS test half happens to
      contain the crash the gate dodged — the exact artifact that flattered the original P0
      single-split (0.31->1.61) before P0a's walk-forward corrected it. Verdict: it's DD
      insurance with a whipsaw cost, not robust risk-adjusted alpha; for long-only this echoes
      P7 (the breadth gate HURTS long-only). NOT deployable as a clear upgrade.
  [3] PER-NAME WEIGHT CAP / PARTIAL CASH (cap each name <20%, remainder cash):
      Sharpe is INVARIANT at 1.05 across caps 0.20/0.15/0.12/0.10 — confirmed empirically;
      for equal-weight K5 a cap is a uniform linear de-leverage (all weights, turnover, gross
      scale by the same factor -> Sharpe unchanged). It trades return for DD ~1:1 and cleanly
      bounds the P10 held-death tail:
        cap 0.15 (75% inv): net 67% · DD 38.1% · worst wk -16.5%
        cap 0.12 (60% inv): net 55% · DD 31.1% · worst wk -13.2%
        cap 0.10 (50% inv): net 47% · DD 26.3% · worst wk -11.0%  (tail halved vs -22%)
OVERALL VERDICT (NEGATIVE for "free" DD smoothing): no overlay improves OOS risk-adjusted
return. Vol-targeting is a no-op (DDs aren't vol-predictable here); the market-DD gate is the
already-known DD-insurance-with-whipsaw tradeoff (not robust for long-only); a per-name cap is
pure linear de-leverage (Sharpe-flat). So DD can only be bought with return — either linearly
(hold cash) or via a regime gate that costs robustness. The ONLY honest lever is the PARTIAL-
CASH / per-name-cap dial: it bounds both maxDD and the held-death tail PROPORTIONALLY with
Sharpe preserved. That is a capital-allocation choice, not an edge gain. NO config change -> no
candidate written (live config stays). P11 RESOLVED (negative, with one usable dial).
HONEST CAVEATS: (1) DD measured weekly-sampled (true intra-week DD is deeper) — consistent with
prior P-numbers for comparability. (2) WINDOW-ROLL INSTABILITY (flagged in P8a): the SAME
baseline's single-split OOS reads net/DD = 78%/31% (P8, 06-15), 138%/38% (P8a, 06-17), 83%/49%
(today) as total=1200 rolls the window a few days each run — a large swing that is itself why
the single OOS split can't be trusted and the walk-forward is the real test (it also explains
why the gate's single-split win is unreliable). (3) Same survivors-only ~3yr / 5-slice basis
(sample-size caveat from P6/P13 stands).
FOLLOW-UP: feeds P14 — use the PARTIAL-CASH dial (e.g. 50-75% invested -> DD ~26-38%, worst
week -11 to -16%, Sharpe ~1.05) as the realistic DD/tail input to the withdrawal/sequence-risk
model, NOT vol-targeting or a stop. P11 closes the DD-smoothing question: there is no robust
Sharpe-improving overlay; consistency for income comes from de-leveraging (cash), not cleverness.

## 2026-06-17 · P8a · per-coin slippage realism — EDGE ROBUST; illiquid churn is only ~31%
Why this matters: P8 modeled a UNIFORM per-side cost, but the live 36-coin universe mixes
deep majors (BTC/ETH) with memecoins (SHIB/PEPE) and thin newer alts (TAO/SEI/WLD/ENA/ONDO/
STRK/ETHFI/STG). Because momentum SELECTS recent pumpers, the worry is the book concentrates
turnover into exactly the illiquid names, so a uniform cost understates the real drag. Built
scripts/p8a_percoin_slippage.py: assigns each coin a liquidity TIER with its own per-side
cost (10bps fee + tier slippage), recomputes cost as a PER-COIN turnover·cost dot-product (not
uniform), measures turnover SHARE by tier, sweeps tier-C slippage to break-even, and walk-
forwards. EXACT live config (multi-horizon 14/30/60, K5, weekly, 100d-MA trend), 36/36 coins,
honest OOS (test half ~479d) + 5-slice walk-forward.
  Tier A majors (slip 5/5bps): ADA AVAX BCH BTC DOGE DOT ETH LINK LTC SOL XRP
  Tier B mid   (slip 15/20bps): AAVE ALGO APT ARB ATOM DASH FET HBAR ICP INJ NEAR SUI UNI XLM ZEC
  Tier C thin  (slip 35/55bps): ENA ETHFI ONDO PEPE SEI SHIB STG STRK TAO WLD
  TURNOVER SHARE by tier: A 25.6% · B 43.6% · C 30.8%  (avg round-trip 0.451x/rebal)
  scenario                 net%   Sharpe  maxDD
  uniform 15bps (backtest) 138.0%  1.33   38.0%
  uniform 60bps (P8 pessim)111.0%  1.19   39.3%
  TIERED realistic         129.6%  1.29   38.4%
  TIERED pessimistic       124.5%  1.26   38.7%
  break-even (raise ONLY tier-C slip): +100% net even at 200bps, +22% net at 800bps(!) tier-C
  walk-forward tiered-realistic: Sharpe [0.45,1.35,2.17,1.25,-0.59] positive 4/5 | net [5,43,170,39,-26]
  walk-forward tiered-pessim:    Sharpe [0.44,1.33,2.15,1.21,-0.61] positive 4/5 | net [5,42,167,37,-26]
VERDICT (positive): the edge is NOT a uniform-cost artifact. The crux is turnover SHARE —
the illiquid tier-C names (incl. memecoins) take only ~31% of the book's churn; the plurality
(43.6%) is liquid mid-caps and 25.6% is majors. So concentrating realistic-to-pessimistic
slippage in tier C barely moves the result (138%→129.6%→124.5%). Tier-C slippage would have to
reach ~8%/side to erase the edge (tier C is too small a share of turnover to dominate). Notably
the TIERED-realistic result (129.6%) BEATS the uniform-60 pessimistic (111%) because uniform-60
wrongly charged majors memecoin rates — i.e. P8's uniform-60 was already conservative. Per-coin
realism confirms the real-money Coinbase deploy is cost-safe. NO config change (live config
already survives) → no candidate written. P8a RESOLVED (positive).
HONEST CAVEATS: (1) The tiers and their bps are a JUDGMENT heuristic, not measured order-book
depth; the conclusion rests on the turnover-SHARE mechanism (robust to the exact bps), not the
point estimate. (2) DATA-WINDOW SHIFT noted: re-running P8 today gives net 138%@15bps and
walk-forward 4/5 (slice 5 now -25%), vs the 2026-06-15 log's 78% and 5/5. total=1200 fetches
the MOST RECENT bars, so the rolling OOS window moved and the latest slice flipped negative —
a recent-regime drawdown, NOT a harness bug (P8a uniform numbers match P8 today exactly). This
is itself a mild honesty flag: the most-recent OOS slice is currently the worst (~-25%) at all
cost levels — worth watching live, and motivates the DD-smoothing work in P11. (3) Fixed
slippage regardless of order SIZE; at $20-25 orders real slip is tiny, but it grows with size as
the book scales — the break-even sweep (edge survives even 8%/side tier-C) covers the scale risk.
(4) Same survivors-only ~3yr / one-OOS-window basis (sample-size caveat from P6 stands; see P13).

## 2026-06-16 · P10 · survivorship-bias stress test — EDGE SURVIVES, ~1/3 HAIRCUT, TREND FILTER PROTECTS
Why this matters (highest-priority honesty item): the backtest universe is TODAY's
survivors — coins that pumped then died (delisted / →0) are absent. A momentum strategy
SELECTS high-momentum names, so it would have bought some of those pumpers right before
they collapsed — losses the survivors-only backtest never sees. This sizes the gap between
backtest and what real money will actually earn. We can't fetch delisted coins from the
live KuCoin API, so I used the backlog-sanctioned RANDOM-DROPOUT PROXY on the real survivor
panel (scripts/p10_survivorship.py): for a random subset of coins, keep REAL prices up to a
random death date (so momentum selects them exactly like real coins), then crash to 2% of
pre-death price over 5d and hold flat (delisted near-zero). Non-NaN floor → a coin HELD
through death realizes the full ~-98% loss (a NaN would wrongly net to 0). After death its
momentum is ~0 and it's below its 100d MA → never re-selected (realistic). Captures BOTH
channels: (1) selection of doomed pumpers, (2) the live trend filter's protection. BTC/ETH
exempt (won't delist). EXACT live config (multi-horizon 14/30/60, K5, weekly, 100d-MA trend),
36-coin universe, 15bps/side, honest OOS test half (~479d), 200 Monte-Carlo seeds/row.
  BASELINE (survivors-only, optimistic): net +76.2% · Sharpe 1.08 · maxDD 41.7%
  death rate   median net% [p10,p90]    Sharpe   held-death/run
     10%        +67.6% [+30,+90]          1.07        0.33
     20%        +52.6% [ -7,+89]          0.90        0.63
     30%        +30.3% [-27,+76]          0.68        1.08
  Trend filter protection @20% deaths (median): ON (live) +52.6%/0.90/DD41.3% vs
     OFF +23.9%/0.62/DD54.3% — the live dual-momentum filter ~doubles stressed return
     and cuts stressed DD 54→41%.
  Walk-forward @20% deaths (5 slices, median): deaths-Sharpe [0.61,1.16,1.56,1.00,0.25]
     vs baseline [0.97,1.67,1.81,1.64,0.46]; positive 5/5 (slice 5 marginal: net -3.5%).
VERDICT: the live edge is REAL but the survivors-only backtest OVERSTATES it. At a plausible
~20% 3-yr death rate among non-major alts, expect roughly a ONE-THIRD haircut to return
(+76%→+53% OOS) and Sharpe ~1.1→0.9; at a harsh 30%, ~+30% and Sharpe 0.68. The edge does
NOT vanish and stays positive across all 5 walk-forward regimes. Mechanism: median ~0.6
held-deaths per OOS window at 20%, each costing ~-20pp on the book (one of 5 names → -98% in
a week). KEY DEPLOY-RELEVANT FINDING: the live config's dual-momentum trend filter is doing
real SURVIVORSHIP protection (dying coins lose their uptrend and get dropped), not just crash
protection — it roughly doubles stressed return vs plain momentum. This raises confidence in
the LIVE config specifically. NO config change (live already includes the protective filter;
nothing better to deploy) → no candidate written. P10 RESOLVED.
HONEST CAVEATS: (1) PROXY, not real delistings. Real deaths CLUSTER in bear regimes (you lose
on the death AND on everything else at once), so the regime-agnostic uniform-timing model
likely UNDERSTATES tail damage — partly visible as slice 5 flipping negative, but clustering
is not fully modeled. (2) Severity fixed at -98% (2% floor); a true total loss (0) or a
partial recovery would shift it — 2% is a middle assumption. Death RATE was swept (10-30%);
20-30% over 3yr is realistic-to-harsh for alt-heavy tails, not conservative. (3) Median maxDD
is ~flat (41%) but that MASKS the tail: an unlucky single-week held-death is a ~-20pp book hit
(p10 net turns negative at 20-30%) — a per-name weight cap / stop (P11) would bound this.
(4) Doesn't add the EXTRA breadth a true point-in-time universe would have had. (5) Same
~3yr / one-OOS-window basis (sample-size caveat from P6 stands).
FOLLOW-UP: feeds P11 (a per-name stop/weight cap directly bounds the held-death tail) and P14
(use the ~1/3 return haircut + the negative-tail seeds as the realistic input to the
withdrawal/sequence-risk model, not the optimistic survivors-only curve).

## 2026-06-15 · P8 · cost/turnover sensitivity of the LIVE config — EDGE IS COST-ROBUST
Why this matters: strategy.yaml assumes 15bps/side, but the real Coinbase deploy cost
~1.14% (entry-only ≈ ~57bps/side) — ~4x the backtest assumption. So the live question is
whether the edge is an artifact of an optimistic cost model. Tested the EXACT live config
(multi-horizon 14/30/60, K5, weekly, long-only, 100d-MA trend filter) on the EXACT live
36-coin universe (all 36 had history). scripts/p8_cost_sensitivity.py. Honest OOS (test
half, ~479d) + 5-slice walk-forward. Key lever measured directly: TURNOVER.
  cost/side:   0bp -> 15bp -> 60bp -> 80bp
  net%:      84.8%   78.0%   59.0%   51.2%
  Sharpe:     1.09    1.04    0.91    0.85
  maxDD:     30.5%   31.0%   32.8%   33.5%
  avg turnover/rebalance: 0.424x (cost-independent; ~1 of 5 names churns per week)
  mean gross return / rebalance: 1.523%
  ANALYTIC break-even cost/side (mean net per-rebal = 0): ~359 bps/side (3.6%/side)
  walk-forward @15bps: Sharpe [0.61,1.97,2.21,1.51,1.31] positive 5/5
  walk-forward @60bps: Sharpe [0.46,1.87,2.09,1.33,1.17] positive 5/5
VERDICT: the edge is NOT fragile to the cost assumption. Mechanism: weekly multi-horizon
momentum is LOW-TURNOVER (~0.42x round-trip/week — names persist in the top-5), so even
at 4x the assumed cost the drag is small. Break-even is ~3.6%/side — costs would have to
be ~24x the backtest assumption (or ~6x the observed Coinbase retail rate) to erase the
edge. Survives 5/5 walk-forward slices at both 15bps and a pessimistic 60bps. This
materially raises confidence in the REAL-MONEY Coinbase deployment: the ~1.14% observed
deploy cost is well inside the safety margin. NO config change (live config already
survives) → no candidate written. P8 RESOLVED (positive).
CAVEATS: (1) break-even is mean-based (mean net per-rebal=0); the walk-forward 5/5 is the
robustness proof, not the point estimate. (2) Same survivors-only ~3yr / one-OOS-window
basis (sample-size caveat from P6 stands). (3) memecoins (SHIB/PEPE) in the universe carry
worse real slippage than the uniform 60bps models — at $20-25/order this is modest but is
the main place the cost model could understate reality; turnover into those specific names
is the thing to watch live. (4) Coinbase Advanced-Trade fees DROP with volume, so 60bps is
conservative as the book scales.
FOLLOW-UP added: P8a — per-coin slippage realism (model memecoin names at higher cost).

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

## 2026-06-15 · HEALTHCHECK (deterministic, automated)

Live config: # above their 100d MA (dual momentum), else cash. Weekly rebalance. ~halves drawdown vs   indicator: xsmom   lookback_days: 30   momentum_lookbacks: [14, 30, 60]   top_k: 5   trend_ma_days: 100 rebalance_days: 7 risk: 

```
### drift: live realised vs backtest (same dates)

Live equity points logged: 1
Not enough live history yet (need ≥3 rebalances). The bot logs one per
weekly rebalance — check back after a few weeks of live paper running.
Latest: equity=0.9988 regime=invested

### edge persistence: xsmom walk-forward
WFV P0a · xsmom + breadth-regime gate
panel 22x1200 1199d

-- fold results --
fold  thr    trainSH   OOS SH    maxDD     ret%  active%
0     0.3       0.16     2.84     2.6%    +15.0%      59%
1     0.3       0.77     2.04     0.4%     +1.1%      24%
2     0.3       0.74     0.42     4.8%     +1.1%      65%
3     0.3       0.71     1.95     0.1%     +1.6%      18%
4     0.3       0.74     1.68     0.9%     +3.8%      41%

-- walk-forward aggregate --
  OOS Sharpe: 1.55
  maxDD:      4.8%
  total ret:  +24.0%
  active ~    41% of the time
  folds:      5

wrote /Users/jamesvaness/hermes-trading/research/walk_forward_p0a.json
```

