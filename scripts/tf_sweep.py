"""Backtest sweep across timeframes × two configs. Read-only; writes no state."""
import asyncio
import copy

from hermes_trading import adapters, metrics
from hermes_trading.loop import PaperEngine, GOAL_FILE, load_strategy
import yaml

TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h"]
BARS = 5000
SPLIT = 0.7


def replay(strategy, asset, candles, risk_cfg):
    eng = PaperEngine(strategy, asset, risk_cfg=risk_cfg)
    closed = []
    for c in candles:
        t = eng.on_bar(c[4], c[0] / 1000.0, bar=c)
        if t:
            closed.append(t)
    return closed


async def main():
    base = load_strategy()
    goal = yaml.safe_load(GOAL_FILE.read_text())
    asset = goal.get("asset", "SOL/USDT")
    risk_cfg = goal.get("risk")

    # Config A: exactly as deployed. Config B: RSI-only with a timeframe-appropriate
    # stop (the 0.6% stop is nonsense on a 4h bar). None => use deployed stop.
    cfg_deployed = copy.deepcopy(base)
    cfg_rsi = copy.deepcopy(base)
    cfg_rsi["entry"].pop("min_bull_count", None)
    cfg_rsi["entry"].pop("min_adx", None)
    configs = {"as-deployed": cfg_deployed, "rsi+tf-stop": cfg_rsi}
    STOP_BY_TF = {"1m": 0.6, "5m": 1.5, "15m": 2.5, "1h": 4.0, "4h": 8.0}

    # Fetch every timeframe concurrently.
    fetched = await asyncio.gather(*[
        adapters.price.fetch_history(asset, timeframe=tf, total=BARS)
        for tf in TIMEFRAMES
    ], return_exceptions=True)

    costs = base.get("costs", {})
    print(f"\nAsset {asset} · costs {costs.get('fees_bps',0)}bps fee + "
          f"{costs.get('slippage_bps',0)}bps slip per side · split {SPLIT:.0%} train\n")
    hdr = f"{'config':<12} {'tf':>4} {'bars':>5} {'trTR':>5} {'teTR':>5} " \
          f"{'test net%':>9} {'PF':>5} {'shrp':>6} {'win%':>5}  verdict"
    print(hdr); print("-" * len(hdr))

    for cfg_name, strat in configs.items():
        for tf, hist in zip(TIMEFRAMES, fetched):
            if isinstance(hist, Exception):
                print(f"{cfg_name:<12} {tf:>4}  fetch failed: {hist}")
                continue
            candles = hist["candles"]
            run_strat = copy.deepcopy(strat)
            if cfg_name == "rsi+tf-stop":
                run_strat["stop_loss_pct"] = STOP_BY_TF.get(tf, run_strat["stop_loss_pct"])
            cut = int(len(candles) * SPLIT)
            tr = replay(run_strat, asset, candles[:cut], risk_cfg)
            te = replay(run_strat, asset, candles[cut:], risk_cfg)
            s = metrics.summary(te)
            n_te = s.get("n", 0)
            if n_te == 0:
                print(f"{cfg_name:<12} {tf:>4} {len(candles):>5} {len(tr):>5} "
                      f"{0:>5} {'--':>9} {'--':>5} {'--':>6} {'--':>5}  no test trades")
                continue
            pf = s["profit_factor"]
            pf_s = "inf" if pf == float("inf") else f"{pf:.2f}"
            verdict = ("EDGE?" if (s["net_return"] > 0 and pf > 1.1 and s["sharpe"] > 0.5
                                   and n_te >= 30)
                       else "thin" if n_te < 30 else "no edge")
            print(f"{cfg_name:<12} {tf:>4} {len(candles):>5} {len(tr):>5} "
                  f"{n_te:>5} {s['net_return']*100:>+8.2f}% {pf_s:>5} "
                  f"{s['sharpe']:>6.2f} {s['win_rate']*100:>4.0f}%  {verdict}")
        print()


asyncio.run(main())
