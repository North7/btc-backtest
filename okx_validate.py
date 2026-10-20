"""
用 OKX 的 BTC 資料重跑 S1 與兩個改良（參數完全照搬 Binance 版 filters.py，不重新調參）：
1. 波動收縮濾網：突破前 N 根通道寬度在過去 180 天中的百分位 ≤0.5 才進場
2. 波動率目標化倉位：目標 40%、上限 1.5x，進場時定倉
資料：OKX 現貨 BTC-USDT 4H；資金費率用 OKX（2021-09 前及 2021-10~2022-01 OKX 缺檔時段以 Binance 補）。
另以 OKX 永續合約 BTC-USDT-SWAP 的 K 線（2019-12 起）再跑一次，因為實盤成交在永續上。

先抓資料：python fetch_okx.py
執行：python okx_validate.py [隨機次數，預設 300] → okx_results.csv、okx_vs_random.csv
"""
import warnings; warnings.filterwarnings("ignore")
import sys
import numpy as np, pandas as pd
from backtest import *

START, SPLIT = "2019-01-01", "2024-01-01"
IS_END = "2023-12-31"   # 樣本內到 2023-12-31 為止；字串切片含端點，寫成 [START:IS_END] 會把 2024-01-01 同時算進樣本內外
sh = lambda r: r.mean() / r.std() * np.sqrt(BARS_PER_YEAR) if r.std() > 0 else np.nan
cg = lambda r: (1 + r).prod() ** (BARS_PER_YEAR / len(r)) - 1
mdd = lambda r: ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()
N_SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
WIDTH_TH, VT_TARGET, VT_CAP = 0.5, 0.4, 1.5

DATA = {"Binance": load(),
        "OKX": load("data/okx_btc_4h.csv", "data/okx_funding_spliced.csv"),
        "OKX永續": load("data/okx_btc_swap_4h.csv", "data/okx_funding_spliced.csv")}

# ---------------------------------------------------------------- 與 filters.py 相同的定義
def width_ok(k, n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    wr = ((hi - lo) / k.close.shift(1)).rolling(6 * 180).rank(pct=True)
    return (wr <= WIDTH_TH).values

def vol_target(k, ret, pos, target=VT_TARGET, cap=VT_CAP):
    rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
    size_sig = (target / rv).clip(upper=cap).shift(1)          # t-1 收盤已知，t 開盤進場
    active = (pos != 0) | (ret != 0)                            # 含出場那根
    entry = active & (pos.shift(1, fill_value=0) == 0)
    size = size_sig.where(entry).ffill().where(active, 0.0).fillna(0.0)
    return ret * size

def entries(ret, pos):
    active = (pos != 0) | (ret != 0)
    return pos.index[active & (pos.shift(1, fill_value=0) == 0)]

def summarize(ret, pos, a=START, s=SPLIT):
    e = entries(ret, pos)
    return {"IS_Sharpe": sh(ret[a:IS_END]), "OOS_Sharpe": sh(ret[s:]), "全期Sharpe": sh(ret[a:]),
            "IS_CAGR": cg(ret[a:IS_END]), "OOS_CAGR": cg(ret[s:]), "IS_MaxDD": mdd(ret[a:IS_END]), "OOS_MaxDD": mdd(ret[s:]),
            "IS交易": int(((e >= pd.Timestamp(a, tz="UTC")) & (e < pd.Timestamp(s, tz="UTC"))).sum()),
            "OOS交易": int((e >= pd.Timestamp(s, tz="UTC")).sum())}

def variants(k, n):
    ok = width_ok(k, n)
    r0, p0, _ = donchian(k, n, 3.0)
    r1, p1, _ = donchian(k, n, 3.0, entry_ok=ok)
    return ok, {"基準": (r0, p0), "寬度≤0.5": (r1, p1),
                "波動目標": (vol_target(k, r0, p0), p0), "寬度≤0.5+波動目標": (vol_target(k, r1, p1), p1)}

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
def show(df):
    d = df.copy()
    for c in ["IS_CAGR", "OOS_CAGR", "IS_MaxDD", "OOS_MaxDD"]: d[c] = (d[c] * 100).round(1)
    for c in ["IS_Sharpe", "OOS_Sharpe", "全期Sharpe"]: d[c] = d[c].round(2)
    print(d.to_string(index=False))

# ---------------------------------------------------------------- 1) 現貨 K 線：Binance vs OKX 並排
rows, runs = [], {}
for ex in ["Binance", "OKX"]:
    k = DATA[ex]
    bh = k.close.pct_change().fillna(0)
    rows.append({"交易所": ex, "N": "-", "設定": "買入持有", **summarize(bh, pd.Series(1.0, k.index))})
    for n in [20, 30]:
        ok, v = variants(k, n); runs[(ex, n)] = (ok, v)
        rows += [{"交易所": ex, "N": n, "設定": name, **summarize(r, p)} for name, (r, p) in v.items()]
res = pd.DataFrame(rows)
res.loc[res.設定 == "買入持有", ["IS交易", "OOS交易"]] = 0
res.to_csv("okx_results.csv", index=False)
print("== 1) 現貨 4H K 線，S1 只做多、ATR 3 倍；樣本內 2019–23 / 樣本外 2024–26/9 ==")
show(res.sort_values(["N", "設定", "交易所"], key=lambda s: s.astype(str), kind="stable"))

# ---------------------------------------------------------------- 2) 逐筆訊號一致性
print("\n== 2) 進場時間比對（基準 S1）：OKX 的進場有多少在 Binance 同一根或前後一根也有 ==")
for n in [20, 30]:
    eb = entries(*runs[("Binance", n)][1]["基準"]); eo = entries(*runs[("OKX", n)][1]["基準"])
    eb, eo = eb[eb >= pd.Timestamp(START, tz="UTC")], eo[eo >= pd.Timestamp(START, tz="UTC")]
    near = lambda a, b: np.mean([np.min(np.abs((b - t).total_seconds())) <= 4 * 3600 for t in a])
    same = len(set(eb) & set(eo))
    print(f"N={n}: Binance {len(eb)} 筆、OKX {len(eo)} 筆；同一根 {same} 筆；"
          f"OKX 進場在 Binance ±1 根內 {near(eo, eb):.0%}，反向 {near(eb, eo):.0%}")

# ---------------------------------------------------------------- 3) OKX 上寬度濾網 vs 隨機刪掉同樣多的交易
def random_skip(k, n, ok_target, n_sims, seed=11):
    rng = np.random.default_rng(seed)
    target_tr = len(donchian(k, n, 3.0, entry_ok=ok_target, trade_from=START)[2])
    lo_p, hi_p = 0.02, 1.0
    for _ in range(8):  # 二分搜尋通過率，使隨機版的交易數 ≈ 濾網版
        p = (lo_p + hi_p) / 2
        m = np.mean([len(donchian(k, n, 3.0, entry_ok=rng.random(len(k)) < p, trade_from=START)[2]) for _ in range(6)])
        lo_p, hi_p = (p, hi_p) if m < target_tr else (lo_p, p)
    p = (lo_p + hi_p) / 2
    res = []
    for _ in range(n_sims):
        r, ps, t = donchian(k, n, 3.0, entry_ok=rng.random(len(k)) < p, trade_from=START)
        res.append((sh(r[START:IS_END]), sh(r[SPLIT:]), sh(r[START:]), len(t)))
    return target_tr, np.array(res)

print(f"\n== 3) OKX：寬度≤0.5 vs 隨機刪掉同樣多的交易（每組 {N_SIMS} 次）==")
cmp_rows = []
k = DATA["OKX"]
for n in [20, 30]:
    ok, v = runs[("OKX", n)]
    r, _ = v["寬度≤0.5"]
    s = (sh(r[START:IS_END]), sh(r[SPLIT:]), sh(r[START:]))
    tt, rs = random_skip(k, n, ok, N_SIMS)
    cmp_rows.append({"N": n, "交易數": tt, "隨機交易數中位": int(np.median(rs[:, 3])),
                     "IS": round(s[0], 2), "IS勝過隨機": f"{np.mean(rs[:,0] < s[0]):.0%}",
                     "OOS": round(s[1], 2), "OOS勝過隨機": f"{np.mean(rs[:,1] < s[1]):.0%}",
                     "隨機OOS中位": round(np.median(rs[:, 1]), 2),
                     "全期": round(s[2], 2), "全期勝過隨機": f"{np.mean(rs[:,2] < s[2]):.0%}"})
    print(cmp_rows[-1], flush=True)
pd.DataFrame(cmp_rows).to_csv("okx_vs_random.csv", index=False)

# ---------------------------------------------------------------- 4) OKX 永續 K 線（實盤標的），2021 起才有完整暖身
A2 = "2021-01-01"
print(f"\n== 4) OKX 永續 BTC-USDT-SWAP K 線 vs 現貨（同區間 {A2[:4]}–23 / 2024–26/9）==")
rows = []
for ex in ["Binance", "OKX", "OKX永續"]:
    kk = DATA[ex]
    for n in [20, 30]:
        _, v = variants(kk, n)
        rows += [{"資料": ex, "N": n, "設定": name, **summarize(r, p, A2)} for name, (r, p) in v.items()
                 if name in ("基準", "寬度≤0.5+波動目標")]
show(pd.DataFrame(rows).sort_values(["N", "設定", "資料"], kind="stable"))
