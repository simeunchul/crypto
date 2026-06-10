"""B 검증 — 종목별 일관성. cooldown_1봉 이 18종 다 baseline 이기는가?"""
from __future__ import annotations
import argparse, sys
import numpy as np, pandas as pd
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except: pass
from autotrader.backtest.crypto_strategies import BacktestConfig, backtest, strat_trend
from autotrader.data.crypto_bars import fetch_crypto_bars

SYMBOLS = [
    ("BTC/USDT:USDT","BTCUSDT"),("ETH/USDT:USDT","ETHUSDT"),
    ("SOL/USDT:USDT","SOLUSDT"),("AVAX/USDT:USDT","AVAXUSDT"),
    ("BNB/USDT:USDT","BNBUSDT"),("DOGE/USDT:USDT","DOGEUSDT"),
    ("ADA/USDT:USDT","ADAUSDT"),("XRP/USDT:USDT","XRPUSDT"),
    ("DOT/USDT:USDT","DOTUSDT"),("LINK/USDT:USDT","LINKUSDT"),
    ("LTC/USDT:USDT","LTCUSDT"),("BCH/USDT:USDT","BCHUSDT"),
    ("ARB/USDT:USDT","ARBUSDT"),("OP/USDT:USDT","OPUSDT"),
    ("SUI/USDT:USDT","SUIUSDT"),("INJ/USDT:USDT","INJUSDT"),
    ("NEAR/USDT:USDT","NEARUSDT"),("ATOM/USDT:USDT","ATOMUSDT"),
]
BARS_PER_YEAR_4H = 2190
BARS_PER_DAY_4H = 6


def overlay(df, base, policy, cooldown_bars=0, trail=0.02):
    out = np.zeros(len(df), dtype=float)
    state=0; entry=None; hw=lw=None; blocked=0; stop_bar=-1
    closes=df["close"].values; bps=base.fillna(0).astype(int).values
    for i in range(len(df)):
        p=float(closes[i]); bp=int(bps[i])
        stopped=False
        if state!=0 and entry is not None:
            if state==1:
                hw=max(hw or p, p)
                if p < hw*(1-trail): stopped=True
            else:
                lw=min(lw or p, p)
                if p > lw*(1+trail): stopped=True
        if stopped:
            blocked=state; stop_bar=i; state=0; entry=None; hw=lw=None
        if blocked!=0:
            if policy=="cooldown" and i-stop_bar >= cooldown_bars: blocked=0
            elif policy=="signal_change" and bp!=blocked and bp!=0: blocked=0
        if state==0 and bp!=0 and bp!=blocked:
            state=bp; entry=p
            hw=p if bp==1 else None; lw=p if bp==-1 else None
        out[i]=state
    return pd.Series(out, index=df.index)


def wmetric(sub):
    rets=sub["net_ret"]
    if len(sub)<2 or rets.std()==0: return None
    eq=(1+rets).cumprod()
    return {
        "return": float(eq.iloc[-1]-1)*100,
        "sharpe": float(rets.mean()/rets.std()*np.sqrt(BARS_PER_YEAR_4H)),
        "max_dd": float(((eq/eq.cummax())-1).min())*100,
        "flips": int((sub["position"].diff().abs()>0).sum()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--fast", type=int, default=12)
    ap.add_argument("--slow", type=int, default=48)
    ap.add_argument("--cost-bps", type=float, default=10.0,
                     help="현실적 비용 (Binance taker + slip)")
    args = ap.parse_args()

    print(f"=== B 검증: 종목별 baseline vs cooldown_1봉 (cost {args.cost_bps} bps) ===")
    cfg = BacktestConfig(fast_ma=args.fast, slow_ma=args.slow,
                         cost_bps_per_side=args.cost_bps, bars_per_year=BARS_PER_YEAR_4H)
    window_bars = args.window_days * BARS_PER_DAY_4H

    rows = []
    for ccxt, sym in SYMBOLS:
        try: df = fetch_crypto_bars(ccxt, timeframe="4h", days=args.days)
        except: continue
        if df.empty or len(df) < args.slow + window_bars: continue
        base_pos = strat_trend(df, cfg)
        pos_A = overlay(df, base_pos, "signal_change")
        pos_C = overlay(df, base_pos, "cooldown", cooldown_bars=1)
        out_A = backtest(df, pos_A, cfg)
        out_C = backtest(df, pos_C, cfg)
        n_windows = len(out_A) // window_bars
        win_count = 0; total = 0; A_sharpes=[]; C_sharpes=[]; A_rets=[]; C_rets=[]
        for w in range(n_windows):
            sub_A = out_A.iloc[w*window_bars:(w+1)*window_bars]
            sub_C = out_C.iloc[w*window_bars:(w+1)*window_bars]
            mA = wmetric(sub_A); mC = wmetric(sub_C)
            if not mA or not mC: continue
            A_sharpes.append(mA["sharpe"]); C_sharpes.append(mC["sharpe"])
            A_rets.append(mA["return"]); C_rets.append(mC["return"])
            if mC["sharpe"] > mA["sharpe"]: win_count += 1
            total += 1
        if total == 0: continue
        rows.append({
            "symbol": sym,
            "n": total,
            "A_sharpe": float(np.mean(A_sharpes)),
            "C_sharpe": float(np.mean(C_sharpes)),
            "delta_sh": float(np.mean(C_sharpes)) - float(np.mean(A_sharpes)),
            "A_return": float(np.mean(A_rets)),
            "C_return": float(np.mean(C_rets)),
            "delta_ret": float(np.mean(C_rets)) - float(np.mean(A_rets)),
            "win_rate": win_count / total * 100,
        })

    df_res = pd.DataFrame(rows).sort_values("delta_sh", ascending=False)
    print(f"\n=== 종목별 (Δ sharpe 내림차순) ===")
    print(f"  {'sym':<10s} {'win%':>6s} {'A_sh':>7s} {'C_sh':>7s} {'Δsh':>7s} {'A_ret%':>8s} {'C_ret%':>8s} {'Δret%':>8s}")
    for _, r in df_res.iterrows():
        marker = "  " if r['delta_sh'] > 0 else "❌"
        print(f"  {r['symbol']:<10s} {r['win_rate']:>5.1f}% {r['A_sharpe']:>+7.3f} {r['C_sharpe']:>+7.3f} "
              f"{r['delta_sh']:>+7.3f} {r['A_return']:>+7.2f} {r['C_return']:>+7.2f} {r['delta_ret']:>+7.2f} {marker}")

    print(f"\n=== 요약 ===")
    print(f"  18종 중 {(df_res['delta_sh']>0).sum()}종이 cooldown 우세")
    print(f"  평균 Δ sharpe: {df_res['delta_sh'].mean():+.3f}")
    print(f"  최저 Δ sharpe: {df_res['delta_sh'].min():+.3f} ({df_res.loc[df_res['delta_sh'].idxmin(),'symbol']})")
    print(f"  최고 Δ sharpe: {df_res['delta_sh'].max():+.3f} ({df_res.loc[df_res['delta_sh'].idxmax(),'symbol']})")


if __name__ == "__main__":
    main()
