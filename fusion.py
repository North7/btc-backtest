"""
S1（4H 突破，N20+N30 各半、寬度≤0.5、波動目標、只做多）與「日線趨勢＋動能＋波動率目標」策略（以下稱 D，
資料來自 strategy_daily.csv / strategy_summary.md）能否融合。
1) 用本專案的 BTC 日線重算 D 的目標倉位，和 CSV 核對（確認規則理解正確）
2) 兩者的相關性、曝險重疊
3) 資金分配組合（兩個子帳戶，每月初再平衡回目標比例）
4) 訊號融合：把 S1 的 200MA 濾網換成 D 的方向分數（延後一天），BTC / OKX / ETH 都測
執行：python fusion.py [D 的 CSV 路徑，預設 ~/Downloads/strategy_daily.csv]
"""
import warnings; warnings.filterwarnings("ignore")
import sys, os
import numpy as np, pandas as pd
from backtest import *

START, SPLIT, IS_END, END = "2019-01-01", "2024-01-01", "2023-12-31", "2026-09-30"
D_PATH = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/Downloads/strategy_daily.csv")
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)

def stats(r, per_year):
    eq = (1 + r).cumprod()
    return {"Sharpe": r.mean() / r.std() * np.sqrt(per_year), "CAGR%": (eq.iloc[-1] ** (per_year / len(r)) - 1) * 100,
            "MaxDD%": (eq / eq.cummax() - 1).min() * 100}

def width_ok(k, n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    return (((hi - lo) / k.close.shift(1)).rolling(6 * 180).rank(pct=True) <= 0.5).values

def vol_target(k, ret, pos, target=0.4, cap=1.5):
    rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
    s = (target / rv).clip(upper=cap).shift(1)
    act = (pos != 0) | (ret != 0)
    e = act & (pos.shift(1, fill_value=0) == 0)
    return ret * s.where(e).ffill().where(act, 0.0).fillna(0.0), s.where(e).ffill().where(pos != 0, 0.0).fillna(0.0)

def d_score(close_daily):
    """D 的方向分數（0–1）與目標倉位；輸入為 UTC 日收盤，輸出標籤 = 計算當天（尚未延後）"""
    c = close_daily
    m50, m140, m200 = (c.rolling(n).mean() for n in (50, 140, 200))
    trend = ((c > m200).astype(int) + (m50 > m200).astype(int) + (c > m140).astype(int)) / 3
    mom = sum((c / c.shift(n) - 1 > 0).astype(int) for n in (30, 90, 180)) / 3
    score = 0.5 * trend + 0.5 * mom
    vol = c.pct_change().rolling(30).std() * np.sqrt(365)
    return score, (score * (0.4 / vol).clip(upper=1)).clip(0, 1)

def s1(k, n, regime_ok):
    """S1 只做多；regime_ok 取代日線 200MA 濾網（None = 用原本的 200MA）"""
    ok = width_ok(k, n) if regime_ok is None else width_ok(k, n) & regime_ok
    r, p, _ = donchian(k, n, 3.0, use_filter=regime_ok is None, entry_ok=ok, trade_from=START)
    return vol_target(k, r, p)

def s1_combo(k, regime_ok=None):
    (r20, x20), (r30, x30) = s1(k, 20, regime_ok), s1(k, 30, regime_ok)
    return (r20 + r30) / 2, (x20 + x30) / 2

# ---------------------------------------------------------------- 1) 核對 D
D = pd.read_csv(D_PATH, parse_dates=["date"]).set_index("date")
D.index = D.index.tz_localize("UTC")
k = load()
cd = k.close.resample("1D").last()
score, tgt = d_score(cd)
both = pd.concat([D.target, tgt.rename("重算")], axis=1).dropna()[START:END]
print("== 1) 用 Binance 日線重算 D 的目標倉位 vs CSV ==")
print(f"相關係數 {both.corr().iloc[0,1]:.3f}，平均絕對差 {(both.iloc[:,0]-both.iloc[:,1]).abs().mean():.3f}，"
      f"差 >0.1 的天數比例 {((both.iloc[:,0]-both.iloc[:,1]).abs() > 0.1).mean():.1%}")
print(f"D 收盤價 vs Binance：中位差 {(D.close / cd.reindex(D.index) - 1)[START:END].median():+.3%}")

# ---------------------------------------------------------------- 2) 相關性與曝險
rS, xS = s1_combo(k)
dS = (1 + rS).resample("1D").prod() - 1                      # S1 日報酬
xS_d = xS.resample("1D").mean()                              # S1 當日平均名目曝險
dD = D.strategy_return
idx = dS[START:END].index.intersection(dD.index)
dS, dD, dB = dS[idx], dD[idx], D.btc_return[idx]
m = lambda r: (1 + r).resample("ME").prod() - 1
print("\n== 2) 相關性（2019-01 至 2026-09）==")
print(f"日報酬相關：S1 vs D {dS.corr(dD):.2f}、S1 vs BTC {dS.corr(dB):.2f}、D vs BTC {dD.corr(dB):.2f}")
print(f"月報酬相關：S1 vs D {m(dS).corr(m(dD)):.2f}、S1 vs BTC {m(dS).corr(m(dB)):.2f}、D vs BTC {m(dD).corr(m(dB)):.2f}")
w = D.actual_btc_weight[idx]; inS = xS_d[idx] > 0
print(f"S1 在場的日子：D 的平均 BTC 比例 {w[inS].mean():.0%}；S1 空手的日子：{w[~inS].mean():.0%}")
print(f"S1 在場時 D 也 ≥50% 持倉的比例 {(w[inS] >= 0.5).mean():.0%}；D 空倉（<5%）時 S1 在場的天數 {(inS & (w < 0.05)).sum()} 天")
yr = lambda r: (r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1) * 100).round(0)
print(pd.DataFrame({"S1": yr(dS), "D": yr(dD), "BTC": yr(dB)}).T.to_string())

# ---------------------------------------------------------------- 3) 資金分配（每月初再平衡）
def blend(wS, rs, rd):
    eq, out = 1.0, []
    a, b = wS, 1 - wS
    for month, g in pd.concat([rs, rd], axis=1).groupby(pd.Grouper(freq="MS")):
        for _, (x, y) in g.iterrows():
            a *= 1 + x; b *= 1 + y
            tot = a + b; out.append(tot / eq - 1); eq = tot
        a, b = eq * wS, eq * (1 - wS)
    return pd.Series(out, rs.index)
print("\n== 3) 兩個子帳戶分資金，每月初再平衡（日報酬、√365 年化）==")
rows = []
for wS in [0, 0.25, 0.5, 0.75, 1.0]:
    r = blend(wS, dS, dD)
    rows.append({"S1 比例": f"{wS:.0%}", **{f"全期{k_}": v for k_, v in stats(r, 365).items()},
                 "IS Sharpe": stats(r[START:IS_END], 365)["Sharpe"], "OOS Sharpe": stats(r[SPLIT:], 365)["Sharpe"],
                 "2025–26%": ((1 + r["2025":]).prod() - 1) * 100})
r = dD + 0.5 * dS
rows.append({"S1 比例": "D 全額＋S1 半份疊加（總曝險最高 175%）", **{f"全期{k_}": v for k_, v in stats(r, 365).items()},
             "IS Sharpe": stats(r[START:IS_END], 365)["Sharpe"], "OOS Sharpe": stats(r[SPLIT:], 365)["Sharpe"],
             "2025–26%": ((1 + r["2025":]).prod() - 1) * 100})
print(pd.DataFrame(rows).round(2).to_string(index=False))

# ---------------------------------------------------------------- 4) 訊號融合：D 的方向分數當 S1 的趨勢濾網
print("\n== 4) S1 的 200MA 濾網換成 D 的方向分數（日線、延後一天）==")
rows = []
for ex, kk in {"BTC": k, "OKX": load("data/okx_btc_4h.csv", "data/okx_funding_spliced.csv"),
               "ETH": load("data/eth_4h.csv", "data/eth_funding.csv")}.items():
    sc, _ = d_score(kk.close.resample("1D").last())
    sc4 = sc.shift(1).reindex(kk.index, method="ffill")
    for lab, ok in [("原本 200MA", None), ("分數 >0", (sc4 > 0).values), ("分數 ≥0.5", (sc4 >= 0.5).values),
                    ("分數 ≥0.83", (sc4 >= 0.8).values), ("分數 =1", (sc4 >= 0.99).values)]:
        r, _ = s1_combo(kk, ok)
        rr = r[START:]
        rows.append({"資料": ex, "濾網": lab, "IS": stats(r[START:IS_END], BARS_PER_YEAR)["Sharpe"],
                     "OOS": stats(r[SPLIT:], BARS_PER_YEAR)["Sharpe"], **stats(rr, BARS_PER_YEAR),
                     "2025–26%": ((1 + r["2025":]).prod() - 1) * 100})
print(pd.DataFrame(rows).round(2).to_string(index=False))
