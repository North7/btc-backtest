"""
S1 多空版：在目前的只做多版本（寬度濾網 ≤0.5、波動目標化、N20+N30 各半）上加入對稱的空單，不另外調參數。
- 空單：收盤跌破前 N 根最低價、日線收盤在 200MA 下方（延後一天）、寬度百分位 ≤0.5 → 下一根開盤放空
- 停損：進場價 + 3×ATR，只往下移
- 倉位：多單 min(1.5, 40%/實現波動)；空單 min(1.0, 40%/實現波動)（空單不加槓桿）
- 空單收取正的資金費（引擎中 ret -= pos × funding）
另外：只看空單那一條腿，並和「200MA 下方時隨機放空、相同停損」比較
執行：python longshort.py [隨機次數，預設 300]
"""
import warnings; warnings.filterwarnings("ignore")
import sys
import numpy as np, pandas as pd
from backtest import *

START, SPLIT, IS_END = "2019-01-01", "2024-01-01", "2023-12-31"
sh = lambda r: r.mean() / r.std() * np.sqrt(BARS_PER_YEAR) if r.std() > 0 else np.nan
cg = lambda r: (1 + r).prod() ** (BARS_PER_YEAR / len(r)) - 1
mdd = lambda r: ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()
N_SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
DATA = {"BTC": load(), "OKX": load("data/okx_btc_4h.csv", "data/okx_funding_spliced.csv"),
        "ETH": load("data/eth_4h.csv", "data/eth_funding.csv")}
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)

def width_ok(k, n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    return (((hi - lo) / k.close.shift(1)).rolling(6 * 180).rank(pct=True) <= 0.5).values

def vol_target(k, ret, pos, target=0.4, cap_long=1.5, cap_short=1.0):
    rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
    base = (target / rv).shift(1)
    sig = pd.Series(np.where(pos > 0, base.clip(upper=cap_long), base.clip(upper=cap_short)), k.index)
    prev = pos.shift(1, fill_value=0)
    active = (pos != 0) | (ret != 0)
    entry = active & ((prev == 0) | ((pos != 0) & (pos != prev)))   # 新開倉或反手
    # 進場當根就停損（pos 已歸 0）：方向看不到，以空單上限保守處理
    return ret * sig.where(entry).ffill().where(active, 0.0).fillna(0.0)

def leg(k, n, side):
    ok = width_ok(k, n); none = np.zeros(len(k), bool)
    if side == "long":
        r, p, t = donchian(k, n, 3.0, entry_ok=ok, trade_from=START)
    elif side == "short":
        r, p, t = donchian(k, n, 3.0, allow_short=True, entry_ok=none, short_ok=ok, trade_from=START)
    else:
        r, p, t = donchian(k, n, 3.0, allow_short=True, entry_ok=ok, short_ok=ok, trade_from=START)
    return vol_target(k, r, p), p, t

def stats_row(r, t=None):
    d = {"IS": sh(r[START:IS_END]), "OOS": sh(r[SPLIT:]), "全期": sh(r[START:]),
         "CAGR%": cg(r[START:]) * 100, "MaxDD%": mdd(r[START:]) * 100}
    if t is not None: d["交易"] = len(t)
    return d

yearly = lambda r: (r[START:].groupby(r[START:].index.year).apply(lambda x: (1 + x).prod() - 1) * 100).round(1)

# ---------------------------------------------------------------- 1) 只做多 vs 多空 vs 只做空
rows, yr = [], {}
for ex, k in DATA.items():
    combo = {}
    for side, lab in [("long", "只做多"), ("both", "多空"), ("short", "只做空（拆解）")]:
        rs, ts = [], 0
        for n in [20, 30]:
            r, p, t = leg(k, n, side); rs.append(r); ts += len(t)
            rows.append({"資料": ex, "版本": lab, "N": n, **stats_row(r, t)})
        combo[lab] = (rs[0] + rs[1]) / 2
        rows.append({"資料": ex, "版本": lab, "N": "20+30", **stats_row(combo[lab]), "交易": ts})
        yr[f"{ex} {lab}"] = yearly(combo[lab])
    yr[f"{ex} 現貨漲跌"] = yearly(k.close.pct_change().fillna(0))
df = pd.DataFrame(rows)
print("== 1) 寬度≤0.5＋波動目標（空單上限 1 倍）；樣本內 2019–23 / 樣本外 2024–26/9 ==")
print(df.round(2).to_string(index=False))
print("\n== 逐年報酬 %（N20+N30 各半）==")
print(pd.DataFrame(yr).T.to_string())

# ---------------------------------------------------------------- 2) 空單 vs 200MA 下方的隨機放空
print(f"\n== 2) 空單訊號 vs「200MA 下方隨機放空」（相同 3×ATR 停損、不加波動目標，{N_SIMS} 次）==")
for ex, k in DATA.items():
    dn = daily_filter(k)[1].values
    for n in [20, 30]:
        none = np.zeros(len(k), bool)
        r0, _, t0 = donchian(k, n, 3.0, allow_short=True, entry_ok=none, short_ok=width_ok(k, n), trade_from=START)
        idx = np.flatnonzero(dn & (k.index >= pd.Timestamp(START, tz="UTC")))
        rng = np.random.default_rng(9); res = []
        for _ in range(N_SIMS):
            m = np.zeros(len(k), bool)
            m[rng.choice(idx, size=min(len(idx), len(t0) * 3), replace=False)] = True
            r, _, t = donchian(k, n, 3.0, short_mask=m, trade_from=START)
            res.append((sh(r[START:]), r[START:].sum(), len(t)))
        res = np.array(res)
        print(f"{ex} N{n}: 空單 {len(t0)} 筆、全期 Sharpe {sh(r0[START:]):.2f}、累計報酬 {r0[START:].sum():+.1%}；"
              f"隨機放空 {int(np.median(res[:,2]))} 筆、Sharpe 中位 {np.median(res[:,0]):.2f}；空單勝過隨機 {np.mean(res[:,0] < sh(r0[START:])):.0%}")
