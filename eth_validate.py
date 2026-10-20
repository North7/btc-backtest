"""
在 ETHUSDT 上獨立驗證 S1 的兩個改良（參數完全照搬 BTC 的 filters.py 結果，不重新調參）：
1. 波動收縮濾網：突破前 N 根通道寬度 (hi-lo)/前收盤，在過去 180 天中的百分位 ≤0.5 才進場
2. 波動率目標化倉位：目標 40%、上限 1.5x，進場時定倉
並和「隨機刪掉同樣多交易」對照（random_skip 邏輯與 filters.py 相同）。

先抓資料：python fetch_data.py ETHUSDT
執行：python eth_validate.py [隨機次數，預設 300] → eth_results.csv、eth_vs_random.csv
"""
import warnings; warnings.filterwarnings("ignore")
import sys
import numpy as np, pandas as pd
from backtest import *

k = load("data/eth_4h.csv", "data/eth_funding.csv")
START, SPLIT = "2019-01-01", "2024-01-01"
IS_END = "2023-12-31"   # 樣本內到 2023-12-31 為止；字串切片含端點，寫成 [START:IS_END] 會把 2024-01-01 同時算進樣本內外
sh = lambda r: r.mean() / r.std() * np.sqrt(BARS_PER_YEAR) if r.std() > 0 else np.nan
cg = lambda r: (1 + r).prod() ** (BARS_PER_YEAR / len(r)) - 1
mdd = lambda r: ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()
N_SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
WIDTH_TH, VT_TARGET, VT_CAP = 0.5, 0.4, 1.5

# ---------------------------------------------------------------- 與 filters.py 相同的定義
def width_ok(n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    width = (hi - lo) / k.close.shift(1)
    wr = width.rolling(6 * 180).rank(pct=True)
    return (wr <= WIDTH_TH).values

rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
def vol_target(ret, pos, target=VT_TARGET, cap=VT_CAP):
    size_sig = (target / rv).clip(upper=cap).shift(1)          # t-1 收盤已知，t 開盤進場
    active = (pos != 0) | (ret != 0)                            # 含出場那根
    entry = active & (pos.shift(1, fill_value=0) == 0)
    size = size_sig.where(entry).ffill().where(active, 0.0).fillna(0.0)
    return ret * size, size

def n_entries(ret, pos):
    """各區間的進場次數（含進場當根就停損的交易）"""
    active = (pos != 0) | (ret != 0)
    e = active & (pos.shift(1, fill_value=0) == 0)
    return int(e[START:IS_END].sum()), int(e[SPLIT:].sum())

def summarize(ret, pos):
    a, b = ret[START:IS_END], ret[SPLIT:]
    ni, no = n_entries(ret, pos)
    return {"IS_Sharpe": sh(a), "OOS_Sharpe": sh(b), "全期Sharpe": sh(ret[START:]),
            "IS_CAGR": cg(a), "OOS_CAGR": cg(b), "IS_MaxDD": mdd(a), "OOS_MaxDD": mdd(b),
            "IS交易": ni, "OOS交易": no}

def random_skip(n, ok_target, n_sims, seed=11):
    """以「交易數相同」校準的隨機刪單（同 filters.py）；另回傳套用波動目標後的 Sharpe"""
    rng = np.random.default_rng(seed)
    target_tr = len(donchian(k, n, 3.0, entry_ok=ok_target, trade_from=START)[2])
    lo_p, hi_p = 0.02, 1.0
    for _ in range(8):  # 二分搜尋
        p = (lo_p + hi_p) / 2
        m = np.mean([len(donchian(k, n, 3.0, entry_ok=rng.random(len(k)) < p, trade_from=START)[2]) for _ in range(6)])
        lo_p, hi_p = (p, hi_p) if m < target_tr else (lo_p, p)
    p = (lo_p + hi_p) / 2
    res = []
    for _ in range(n_sims):
        r, ps, t = donchian(k, n, 3.0, entry_ok=rng.random(len(k)) < p, trade_from=START)
        rv_ = vol_target(r, ps)[0]
        res.append((sh(r[START:IS_END]), sh(r[SPLIT:]), sh(r[START:]), len(t),
                    sh(rv_[START:IS_END]), sh(rv_[SPLIT:]), sh(rv_[START:])))
    return p, target_tr, np.array(res)

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
def show(df):
    d = df.copy()
    for c in ["IS_CAGR", "OOS_CAGR", "IS_MaxDD", "OOS_MaxDD"]: d[c] = (d[c] * 100).round(1)
    for c in ["IS_Sharpe", "OOS_Sharpe", "全期Sharpe"]: d[c] = d[c].round(2)
    print(d.to_string(index=False))

print(f"ETHUSDT 4H {k.index[0]} → {k.index[-1]}，真實資金費率自 "
      f"{pd.read_csv('data/eth_funding.csv', index_col=0).index[0]} 起（之前以 0.01%/8h 代入）")

# ---------------------------------------------------------------- 1) 結果表
bh = k.close.pct_change().fillna(0)
rows = [{"N": "-", "設定": "買入持有 ETH", **summarize(bh, pd.Series(1.0, k.index)), "平均倉位": 1.0}]
rows[0].update({"IS交易": 0, "OOS交易": 0})
runs = {}
for n in [20, 30]:
    ok = width_ok(n)
    r0, p0, t0 = donchian(k, n, 3.0)
    r1, p1, t1 = donchian(k, n, 3.0, entry_ok=ok)
    v0, s0 = vol_target(r0, p0); v1, s1 = vol_target(r1, p1)
    runs[n] = ok
    for name, r, p, size in [("基準", r0, p0, None), ("寬度≤0.5", r1, p1, None),
                             ("波動目標", v0, p0, s0), ("寬度≤0.5+波動目標", v1, p1, s1)]:
        rows.append({"N": n, "設定": name, **summarize(r, p),
                     "平均倉位": 1.0 if size is None else round(size[p != 0][START:].mean(), 2)})
res = pd.DataFrame(rows)
res.to_csv("eth_results.csv", index=False)
print("\n== ETH：S1 只做多、ATR 3 倍；樣本內 2019–23 / 樣本外 2024–26 ==")
show(res)

# ---------------------------------------------------------------- 2) 寬度濾網 vs 隨機刪掉同樣多的交易
print(f"\n== 寬度≤0.5 vs 隨機刪掉同樣多的交易（每組 {N_SIMS} 次）==")
cmp_rows = []
for n in [20, 30]:
    ok = runs[n]
    r, p, t = donchian(k, n, 3.0, entry_ok=ok)
    v = vol_target(r, p)[0]
    s = {"IS": sh(r[START:IS_END]), "OOS": sh(r[SPLIT:]), "全期": sh(r[START:]),
         "vIS": sh(v[START:IS_END]), "vOOS": sh(v[SPLIT:]), "v全期": sh(v[START:])}
    pr, tt, rs = random_skip(n, ok, N_SIMS)
    pct = lambda col, key: f"{np.mean(rs[:, col] < s[key]):.0%}"
    cmp_rows.append({"N": n, "交易數": tt, "隨機交易數中位": int(np.median(rs[:, 3])),
                     "IS": round(s["IS"], 2), "IS勝過隨機": pct(0, "IS"),
                     "OOS": round(s["OOS"], 2), "OOS勝過隨機": pct(1, "OOS"),
                     "隨機OOS中位": round(np.median(rs[:, 1]), 2),
                     "全期": round(s["全期"], 2), "全期勝過隨機": pct(2, "全期"),
                     "隨機全期中位": round(np.median(rs[:, 2]), 2),
                     "+波動目標 IS勝過隨機": pct(4, "vIS"), "+波動目標 OOS勝過隨機": pct(5, "vOOS"),
                     "+波動目標 全期勝過隨機": pct(6, "v全期")})
    print(cmp_rows[-1], flush=True)
pd.DataFrame(cmp_rows).to_csv("eth_vs_random.csv", index=False)

# ---------------------------------------------------------------- 3) 波動目標化對隨機進場是否一樣有效
rng = np.random.default_rng(3); idx = np.arange(len(k))
n_tr = len(donchian(k, 20, 3.0)[2]); gain = []
for _ in range(min(N_SIMS, 100)):
    mask = np.zeros(len(k), bool); mask[rng.choice(idx[1300:], size=n_tr * 4, replace=False)] = True
    r, p, _ = donchian(k, 20, 3.0, entry_mask=mask)
    gain.append(sh(vol_target(r, p)[0][START:]) - sh(r[START:]))
r0, p0, _ = donchian(k, 20, 3.0)
print(f"\n隨機進場套用波動目標化，全期 Sharpe 變化中位數 {np.median(gain):+.2f}"
      f"（S1 N20 本身 {sh(vol_target(r0, p0)[0][START:]) - sh(r0[START:]):+.2f}）")
