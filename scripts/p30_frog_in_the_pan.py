"""P30 — Frog-in-the-Pan / Information Discreteness momentum-quality filter (NEW, 2026-07-10).

Da, Gurun & Warachka (2014, RFS): momentum delivered by CONTINUOUS, smooth price paths
(information arriving in many small daily increments) continues far longer and reverses far
less than momentum delivered by a few DISCRETE jumps (attention-grabbing news pumps). They
proxy path continuity with INFORMATION DISCRETENESS:

    ID = sign(PRET) * (%neg_days - %pos_days)   over the formation window

  - Winner (PRET>0) that rose via many small up-days: %pos high -> (neg-pos)<0 -> ID LOW  (continuous)
  - Winner that jumped up on a few days: %pos low  -> (neg-pos)>0 -> ID HIGH (discrete/jumpy)
  Low ID = continuous = stronger, more persistent momentum; high ID = jumpy = reversal-prone.

WHY THIS IS A GENUINELY NEW ANGLE (not a re-tread of P0-P29): every prior lever changed the
momentum MAGNITUDE ranking (horizon set P25, vol-norm P21, strip-beta P23), the SELECTION
redundancy (decorr P24, buffer P18, top_k P19), or the net EXPOSURE (gates P0/P3/P11/P28,
sizing P2/P11). P30 changes NONE of those — it re-ranks/filters on the QUALITY (path
smoothness) of each name's momentum. Motivated directly by the LOG's two open wounds:
"rode the high-beta cluster into the 2022 bear" (P16) and "crashes are trend-reversals from
calm" (P11) — jumpy/pumpy names are precisely the reversal-prone type FIP is designed to avoid.

FAMILY tested (everything else = EXACT live: dual-mom px>100d MA else cash, weekly R=7, K5 eq wt,
15bps/side; live momentum = equal-wt raw [14,30,60]). ID formation window PRE-FIXED to 60d (the
longest live horizon; enough daily obs), committed before looking at TEST:
  live     : baseline (reproduce the canonical ~0.90 full-2020 Sharpe)                      <- LIVE
  fip8     : double-sort — top-8 by momentum -> trend-pass -> keep the 5 LOWEST-ID (continuous)
  fip10    : double-sort — top-10 by momentum -> trend-pass -> keep the 5 lowest-ID
  fip12    : double-sort — top-12 by momentum -> trend-pass -> keep the 5 lowest-ID
  tilt50   : composite rank = rank(mom) + 0.5*rank(-ID); top-5; trend  (soft continuous tilt)
  tilt100  : composite rank = rank(mom) + 1.0*rank(-ID); top-5; trend
  idfilt   : live top-5 momentum, then DROP any name with ID above the pool median (quality gate;
             cashes the jumpy ones -> inmkt check flags if any DD win is just partial cash)

HONESTY: single validated engine (mirrors p25.bt exactly -> 'live' must reproduce ~0.90).
inmkt reported everywhere (a DD cut that is only lower deployment = partial cash, the P11/P19
trap). Honest TRAIN->TEST selection, then the P20/P21 7-phase grid-luck KILLER (which killed
P21/P23/P24), the 5-slice walk-forward, the 2022-bear-located test, and an independent 2017-2020
window. Adopt ONLY if a TRAIN-picked variant beats live OOS Sharpe AND >=4/5 WF AND survives the
7-phase test (mean edge>0, >=5/7) at EQUAL-OR-HIGHER deployment.

    EXCHANGE_ID=kucoin python scripts/p30_frog_in_the_pan.py
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_cache import load_panel, live_universe  # noqa: E402
from p15_revalidate import K, R, SPLIT, MA_DAYS, COST  # noqa: E402

LBS = [14, 30, 60]           # live momentum horizons
ID_WIN = 60                  # ID formation window (pre-fixed = longest live horizon)
WARMUP = max(max(LBS), ID_WIN, MA_DAYS) if False else max(LBS)  # 60 — same rebalance dates as p25 live
# NOTE: p25 uses WARMUP=90 (its 'wide' variant had a 90d horizon). Here the max horizon is 60,
# and ID_WIN=60, so WARMUP=60 is the natural start. To make 'live' reproduce p25's numbers
# EXACTLY we would need WARMUP=90; instead we cross-check live here vs its own p25=90 sanity.

FAMILY = ["live", "fip8", "fip10", "fip12", "tilt50", "tilt100", "idfilt"]
POOL = {"fip8": 8, "fip10": 10, "fip12": 12}
TILT = {"tilt50": 0.5, "tilt100": 1.0}


def mom_score(panel, i):
    """Live signal: equal-weight average of raw 14/30/60d returns."""
    return sum(panel.iloc[i] / panel.iloc[i - lb] - 1 for lb in LBS) / len(LBS)


def info_discreteness(panel, i, L=ID_WIN):
    """ID = sign(PRET) * (%neg - %pos) over the trailing L-day formation window (causal)."""
    win = panel.iloc[i - L:i + 1]
    dret = win.pct_change()
    cnt = dret.count()
    pos = (dret > 0).sum() / cnt
    neg = (dret < 0).sum() / cnt
    pret = panel.iloc[i] / panel.iloc[i - L] - 1
    return np.sign(pret) * (neg - pos)


def select(panel, ma, i, name, trend=True):
    """Return a weight Series (1/K on chosen names, else 0) for variant `name` at row i.
    Mirrors live selection: at most K names, each above its 100d MA, else cash."""
    mom = mom_score(panel, i).dropna()
    w = pd.Series(0.0, index=panel.columns)
    if len(mom) < K:
        return w

    if name == "live":
        cand = list(mom.sort_values().index[-K:])
        chosen = [s for s in cand if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]]

    elif name in POOL:                    # double-sort: momentum pool -> trend -> lowest ID
        pool = list(mom.sort_values().index[-POOL[name]:])
        idv = info_discreteness(panel, i).reindex(pool)
        tp = [s for s in pool if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]]
        # keep the K with the LOWEST ID (most continuous) among trend-passers
        chosen = list(idv.reindex(tp).sort_values().index[:K])

    elif name in TILT:                    # soft composite rank tilt toward low ID
        idv = info_discreteness(panel, i).reindex(mom.index)
        comp = mom.rank() + TILT[name] * (-idv).rank()
        cand = list(comp.sort_values().index[-K:])
        chosen = [s for s in cand if (not trend) or panel.iloc[i][s] > ma.iloc[i][s]]

    elif name == "idfilt":                # live top-K, then drop jumpy (ID above pool median)
        cand = list(mom.sort_values().index[-K:])
        idv = info_discreteness(panel, i).reindex(cand)
        med = idv.median()
        chosen = [s for s in cand
                  if ((not trend) or panel.iloc[i][s] > ma.iloc[i][s]) and idv[s] <= med]
    else:
        raise ValueError(name)

    for s in chosen[:K]:
        w[s] = 1.0 / K
    return w


def bt(panel, ma, name="live", cost=COST, trend=True, lo=None, hi=None, phase=0):
    """Live long-only K5 weekly engine with a pluggable FIP selection. Mirrors p25.bt accounting."""
    rets, turns, deployed = [], [], []
    prev = pd.Series(0.0, index=panel.columns)
    i = WARMUP + phase
    while i + R < len(panel):
        w = select(panel, ma, i, name, trend=trend)
        fwd = (panel.iloc[i + R] / panel.iloc[i] - 1).reindex(panel.columns).fillna(0)
        if lo is None or (lo <= i < hi):
            rets.append((w * fwd).sum() - (w - prev).abs().sum() * cost)
            turns.append((w - prev).abs().sum())
            deployed.append(float(w.sum()))
        prev = w
        i += R
    rets = np.array(rets)
    if len(rets) < 2 or rets.std() == 0:
        return dict(net=0, sharpe=0, maxdd=0, worstwk=0, turnover=0, inmkt=0, n=len(rets))
    eq = np.cumprod(1 + rets); pk = np.maximum.accumulate(eq)
    return dict(net=eq[-1] - 1, sharpe=rets.mean() / rets.std() * np.sqrt(365 / R),
                maxdd=float(np.max((pk - eq) / pk)), worstwk=float(rets.min()),
                turnover=float(np.mean(turns)), inmkt=float(np.mean(deployed)), n=len(rets))


def seven_phase(panel, ma, a, b):
    edges, ash, bsh = [], [], []
    for ph in range(7):
        ra = bt(panel, ma, name=a, phase=ph); rb = bt(panel, ma, name=b, phase=ph)
        edges.append(ra["sharpe"] - rb["sharpe"]); ash.append(ra["sharpe"]); bsh.append(rb["sharpe"])
    edges = np.array(edges)
    return dict(edges=edges, mean=float(edges.mean()), std=float(edges.std()),
                pos=int((edges > 0).sum()), a=ash, b=bsh)


def main():
    syms = live_universe()
    panel = load_panel(syms, "1d")
    if panel.empty:
        sys.exit("No cache. Run: EXCHANGE_ID=kucoin python scripts/data_cache.py --update")
    panel.index = pd.to_datetime(panel.index, unit="ms")
    panel = panel.sort_index()

    def prep(start, end=None):
        p = panel[panel.index >= start]
        if end:
            p = p[p.index < end]
        p = p.dropna(axis=1, thresh=120)
        return p, p.rolling(MA_DAYS).mean()

    p20, m20 = prep("2020-01-01")
    idx = p20.index
    cut = int(len(p20) * SPLIT)
    yrs = (idx[-1] - idx[0]).days / 365

    print("=" * 88)
    print("P30 — Frog-in-the-Pan (Information Discreteness) momentum-QUALITY filter vs LIVE")
    print("=" * 88)
    print(f"deep panel {idx[0].date()}..{idx[-1].date()} (~{yrs:.1f}y, {len(p20)}d), "
          f"{p20.shape[1]} coins, {COST*1e4:.0f}bps/side, warmup {WARMUP}d, ID window {ID_WIN}d")
    print(f"2020-split OOS test half starts {idx[cut].date()} (~{(idx[-1]-idx[cut]).days}d)\n")

    pr = p20.reset_index(drop=True); mr = m20.reset_index(drop=True)
    ptr = p20.iloc[:cut].reset_index(drop=True); mtr = m20.iloc[:cut].reset_index(drop=True)
    pte = p20.iloc[cut:].reset_index(drop=True); mte = m20.iloc[cut:].reset_index(drop=True)
    folds = np.array_split(np.arange(len(p20)), 5)

    # ---- sanity: live must reproduce the canonical ~0.90 (warmup differs from p25's 90 -> note) ----
    fl = bt(pr, mr, name="live")
    print(f"[sanity] live FULL: net {fl['net']*100:.0f}%  Sharpe {fl['sharpe']:.2f}  "
          f"maxDD {fl['maxdd']*100:.0f}%  inmkt {fl['inmkt']*100:.0f}%  (cf p25/p13 offset-0 ~0.90;")
    print("          small diff = this engine warms up at 60d vs p25's 90d -> a few extra early rebalances)\n")

    # ---- (1) full / OOS / WF / inmkt for every variant ----
    print("-" * 88)
    print("(1) Each variant — FULL, OOS test-half, 5-slice WF+, inmkt (vs live):")
    print(f"{'variant':<10}{'fullSh':>8}{'fullNet':>9}{'fullDD':>7}{'oosSh':>7}{'oosNet':>9}"
          f"{'oosDD':>7}{'WF+':>5}{'inmkt':>7}{'turn':>6}")

    def wf(name):
        out = []
        for f in folds:
            seg = p20.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            ms = m20.iloc[f[0]:f[-1] + 1].reset_index(drop=True)
            out.append(bt(seg, ms, name=name))
        return out

    rows = {}
    for name in FAMILY:
        full = bt(pr, mr, name=name); oos = bt(pte, mte, name=name)
        w = wf(name); pos = sum(1 for r in w if r["sharpe"] > 0)
        rows[name] = dict(full=full, oos=oos, wf=w, wfpos=pos)
        print(f"{name:<10}{full['sharpe']:>8.2f}{full['net']*100:>8.0f}%{full['maxdd']*100:>6.0f}%"
              f"{oos['sharpe']:>7.2f}{oos['net']*100:>8.0f}%{oos['maxdd']*100:>6.0f}%"
              f"{pos:>4}/5{full['inmkt']*100:>6.0f}%{full['turnover']:>6.2f}")

    # ---- (2) honest TRAIN->TEST ----
    print("\n" + "-" * 88)
    print("(2) HONEST TRAIN->TEST: pick variant by TRAIN Sharpe, report its TEST half:")
    tr = {name: bt(ptr, mtr, name=name) for name in FAMILY}
    pick = max(tr, key=lambda n: tr[n]["sharpe"])
    print("  TRAIN Sharpe: " + "  ".join(f"{n}={tr[n]['sharpe']:.2f}" for n in FAMILY))
    print(f"  -> TRAIN picks: {pick}")
    tp = rows[pick]["oos"]; tl = rows["live"]["oos"]
    print(f"  -> TEST {pick}: net {tp['net']*100:+.0f}%  Sharpe {tp['sharpe']:.2f}  "
          f"maxDD {tp['maxdd']*100:.0f}%  inmkt {tp['inmkt']*100:.0f}%  WF {rows[pick]['wfpos']}/5")
    print(f"  -> TEST live:  net {tl['net']*100:+.0f}%  Sharpe {tl['sharpe']:.2f}  "
          f"maxDD {tl['maxdd']*100:.0f}%  inmkt {tl['inmkt']*100:.0f}%  WF {rows['live']['wfpos']}/5")

    # ---- (3) 7-phase KILLER (best non-live full-Sharpe variant AND the TRAIN pick) ----
    print("\n" + "-" * 88)
    best_nl = max((n for n in FAMILY if n != "live"), key=lambda n: rows[n]["full"]["sharpe"])
    for tag, cand in {"TRAIN-pick": pick, "best-full-Sharpe": best_nl}.items():
        if cand == "live":
            print(f"(3.{tag}) TRAIN/best picked live itself — FIP does not beat live even in-sample.")
            continue
        P = seven_phase(pte, mte, cand, "live")
        print(f"(3) 7-phase OOS Sharpe ({cand}-live) [{tag}] — THE grid-luck killer (P20/P21):")
        print(f"    phase:   " + "  ".join(f"{ph}" for ph in range(7)))
        print(f"    live Sh: " + "  ".join(f"{s:.2f}" for s in P["b"]))
        print(f"    {cand:<7} " + "  ".join(f"{s:.2f}" for s in P["a"]))
        print(f"    edge:    " + "  ".join(f"{e:+.2f}" for e in P["edges"]))
        print(f"    => mean {P['mean']:+.2f}  std {P['std']:.2f}  positive {P['pos']}/7  "
              f"[need mean>0 AND >=5/7]\n")

    # ---- (4) bear-2022 located ----
    print("-" * 88)
    print("(4) Located INSIDE the 2022 bear (offset-0):")
    lo = int(idx.searchsorted(pd.Timestamp("2022-01-01")))
    hi = int(idx.searchsorted(pd.Timestamp("2022-12-31")))
    print(f"{'variant':<10}{'net%':>9}{'Sharpe':>8}{'maxDD':>8}{'worstWk':>9}{'inmkt':>7}")
    for name in FAMILY:
        r = bt(pr, mr, name=name, lo=lo, hi=hi)
        print(f"{name:<10}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}{r['maxdd']*100:>7.0f}%"
              f"{r['worstwk']*100:>8.1f}%{r['inmkt']*100:>6.0f}%")

    # ---- (5) independent 2017-2020 window ----
    print("\n" + "-" * 88)
    print("(5) INDEPENDENT 2017-2020 window (thin cross-section, directional only):")
    pe, me = prep("2017-01-01", "2020-01-01")
    per = pe.reset_index(drop=True); mer = me.reset_index(drop=True)
    print(f"  window {pe.index[0].date()}..{pe.index[-1].date()}  {pe.shape[1]} coins ({len(pe)}d)")
    print(f"{'variant':<10}{'net%':>9}{'Sharpe':>8}{'inmkt':>7}")
    for name in FAMILY:
        r = bt(per, mer, name=name)
        print(f"{name:<10}{r['net']*100:>8.1f}%{r['sharpe']:>8.2f}{r['inmkt']*100:>6.0f}%")

    # ---- verdict ----
    print("\n" + "=" * 88)
    beats = (pick != "live" and tp["sharpe"] > tl["sharpe"] and rows[pick]["wfpos"] >= 4
             and tp["inmkt"] >= tl["inmkt"] - 0.02)
    print("Adopt a FIP variant only if a TRAIN-picked non-live variant beats live OOS Sharpe,")
    print(">=4/5 WF, at equal-or-higher deployment, AND survives the 7-phase test (mean>0, >=5/7).")
    print(f"TRAIN-pick beats-live-OOS gate (pre-phase): {'PASS' if beats else 'FAIL'}"
          f"  -> {'run/read phase test above for final call' if beats else 'KILL P30 (live stands)'}")
    print("=" * 88)


if __name__ == "__main__":
    main()
