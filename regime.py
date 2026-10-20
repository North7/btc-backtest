"""
「只在大趨勢確定時才做」：把現在的 200MA 濾網換成更嚴格的趨勢確認，看能否
(a) 減少多頭年份裡的小虧空單、(b) 保留空頭年份的空單收益。
事先定好 3 種確認規則（全部用日線、延後一天），多空對稱，不另調參數：
  R0 現行：收盤在 200MA 上（下）
  R1 均線斜率：R0 且 200MA 比 20 天前高（低）
  R2 均線排列：R0 且 50MA 在 200MA 上（下）
  R3 持續性：過去 20 天每天收盤都在 200MA 上（下）
其餘規則同現版：寬度≤0.5、3×ATR 移動停損、波動目標（多單上限 1.5x、空單 1x）、N20+N30 各半。
2018-07 至 2018-12 是空頭、且沒用在任何設計中，另列為空單的額外檢驗。
執行：python regime.py [隨機次數，預設 300]
"""
import warnings; warnings.filterwarnings("ignore")
import sys
import numpy as np, pandas as pd
from backtest import *

START, SPLIT, IS_END = "2019-01-01", "2024-01-01", "2023-12-31"
sh = lambda r: r.mean() / r.std() * np.sqrt(BARS_PER_YEAR) if r.std() > 0 else np.nan
cg = lambda r: (1 + r).prod() ** (BARS_PER_YEAR / len(r)) - 1
mdd = lambda r: ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()
DATA = {"BTC": load(), "OKX": load("data/okx_btc_4h.csv", "data/okx_funding_spliced.csv"),
        "ETH": load("data/eth_4h.csv", "data/eth_funding.csv")}

def width_ok(k, n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    return (((hi - lo) / k.close.shift(1)).rolling(6 * 180).rank(pct=True) <= 0.5).values

def vol_target(k, ret, pos, target=0.4, cap_long=1.5, cap_short=1.0):
    """同 longshort.py：進場時定倉，多單上限 1.5x、空單 1x"""
    rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
    base = (target / rv).shift(1)
    sig = pd.Series(np.where(pos > 0, base.clip(upper=cap_long), base.clip(upper=cap_short)), k.index)
    prev = pos.shift(1, fill_value=0)
    active = (pos != 0) | (ret != 0)
    entry = active & ((prev == 0) | ((pos != 0) & (pos != prev)))
    return ret * sig.where(entry).ffill().where(active, 0.0).fillna(0.0)

N_SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)

def regimes(k):
    d = k.close.resample("1D").last()
    m200, m50 = d.rolling(200).mean(), d.rolling(50).mean()
    up0, dn0 = d > m200, d < m200
    rules = {"R0 現行": (up0, dn0),
             "R1 均線斜率": (up0 & (m200 > m200.shift(20)), dn0 & (m200 < m200.shift(20))),
             "R2 均線排列": (up0 & (m50 > m200), dn0 & (m50 < m200)),
             "R3 持續20天": (up0.rolling(20).min() == 1, dn0.rolling(20).min() == 1)}
    f = lambda s: s.astype(float).shift(1).reindex(k.index, method="ffill").fillna(0).astype(bool).values
    return {name: (f(u), f(dn)) for name, (u, dn) in rules.items()}

def run(k, n, up, dn, short):
    w = width_ok(k, n); none = np.zeros(len(k), bool)
    r, p, t = donchian(k, n, 3.0, allow_short=short, use_filter=False,
                       entry_ok=w & up, short_ok=w & dn, trade_from=START)
    return vol_target(k, r, p), t

def short_leg(k, n, dn, start=START):
    w = width_ok(k, n); none = np.zeros(len(k), bool)
    r, p, t = donchian(k, n, 3.0, allow_short=True, use_filter=False, entry_ok=none, short_ok=w & dn, trade_from=start)
    return r, t

yr = lambda r: (r[START:].groupby(r[START:].index.year).apply(lambda x: (1 + x).prod() - 1) * 100).round(1)

rows, years = [], {}
for ex, k in DATA.items():
    R = regimes(k)
    for name, (up, dn) in R.items():
        for short, lab in [(False, "只做多"), (True, "多空")]:
            rs, nt = [], 0
            for n in [20, 30]:
                r, t = run(k, n, up, dn, short); rs.append(r); nt += len(t)
            r = (rs[0] + rs[1]) / 2
            rows.append({"資料": ex, "趨勢規則": name, "版本": lab, "IS": sh(r[START:IS_END]), "OOS": sh(r[SPLIT:]),
                         "全期": sh(r[START:]), "CAGR%": cg(r[START:]) * 100, "MaxDD%": mdd(r[START:]) * 100, "交易": nt})
            if ex == "BTC": years[f"{name} {lab}"] = yr(r)
print("== 1) 趨勢確認規則 × 只做多／多空（N20+N30 各半）==")
print(pd.DataFrame(rows).round(2).to_string(index=False))
print("\n== BTC 逐年報酬 % ==")
print(pd.DataFrame(years).T.to_string())

# ---------------------------------------------------------------- 空單這條腿：逐年、2018 下半年、隨機對照
print("\n== 2) 只看空單（N20+N30 各半，固定 1 倍、不加波動目標）==")
srows = []
for ex, k in DATA.items():
    R = regimes(k)
    for name, (_, dn) in R.items():
        rs, ts = [], 0
        for n in [20, 30]:
            r, t = short_leg(k, n, dn, start="2018-07-01"); rs.append(r); ts += len(t)
        r = (rs[0] + rs[1]) / 2
        y = (r["2018-07":].groupby(r["2018-07":].index.year).apply(lambda x: (1 + x).prod() - 1) * 100).round(1)
        # 隨機對照：在同一個空頭狀態裡隨機放空（N20 停損規則相同），比較 2019 起的 Sharpe
        idx = np.flatnonzero(dn & (k.index >= pd.Timestamp(START, tz="UTC")))
        r20, t20 = short_leg(k, 20, dn)
        rng = np.random.default_rng(9); sims = []
        for _ in range(N_SIMS if ex == "BTC" else N_SIMS // 3):
            m = np.zeros(len(k), bool); m[rng.choice(idx, size=min(len(idx), max(len(t20) * 2, 1)), replace=False)] = True
            rr, tt = donchian(k, 20, 3.0, short_mask=m, trade_from=START)[0::2]
            sims.append(sh(rr[START:]))
        srows.append({"資料": ex, "規則": name, "空單筆數(2018H2起)": ts, **{str(c): v for c, v in y.items()},
                      "2019起Sharpe": round(sh(r[START:]), 2),
                      "N20勝過隨機放空": f"{np.mean(np.array(sims) < sh(r20[START:])):.0%}"})
print(pd.DataFrame(srows).to_string(index=False))
