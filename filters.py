"""
S1（4H 突破只做多）加上額外指標／數據的進場濾網對照。
所有濾網都只用 t 收盤時已知的資訊；日線資料一律先 shift(1) 天。

檢驗流程：
1. 每個濾網族跑一個小網格，看樣本內 / 樣本外 Sharpe 與交易數
2. 每族只用「樣本內 Sharpe」挑一組參數（不看樣本外）
3. 被挑中的濾網和「隨機略過相同數量交易」比較 —— 濾網減少交易、降低在場時間本身就會改變 Sharpe，
   要勝過隨機刪單才算濾網有資訊量
4. 部位大小：波動率目標化（進場時決定倉位，持有期間不調整）
"""
import warnings; warnings.filterwarnings("ignore")
import sys
import numpy as np, pandas as pd
from backtest import *

k = load()
START, SPLIT = "2019-01-01", "2024-01-01"
IS_END = "2023-12-31"   # 樣本內到 2023-12-31 為止；字串切片含端點，寫成 [START:IS_END] 會把 2024-01-01 同時算進樣本內外
sh = lambda r: r.mean() / r.std() * np.sqrt(BARS_PER_YEAR) if r.std() > 0 else np.nan
cg = lambda r: (1 + r).prod() ** (BARS_PER_YEAR / len(r)) - 1
mdd = lambda r: ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()
N_SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 200

# ---------------------------------------------------------------- 指標（皆為 t 收盤時已知）
def adx(k, n=14):
    up, dn = k.high.diff(), -k.low.diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = k.close.shift()
    tr = pd.concat([k.high - k.low, (k.high - pc).abs(), (k.low - pc).abs()], axis=1).max(axis=1)
    a = 1 / n
    atr_ = tr.ewm(alpha=a, adjust=False).mean()
    pdi = 100 * pd.Series(pdm, k.index).ewm(alpha=a, adjust=False).mean() / atr_
    ndi = 100 * pd.Series(ndm, k.index).ewm(alpha=a, adjust=False).mean() / atr_
    dx = 100 * (pdi - ndi).abs() / (pdi + ndi)
    return dx.ewm(alpha=a, adjust=False).mean()

def daily_known(s_daily):
    """日線值 → 4H：先 shift(1) 天（標籤 D 的值是 D 日收盤才知道）"""
    return s_daily.shift(1).reindex(k.index, method="ffill")

def features(n):
    f = {}
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    # 波動率收縮：突破前 n 根通道寬度，在過去 180 天中的百分位
    width = (hi - lo) / k.close.shift(1)
    f["width_rank"] = width.rolling(6 * 180).rank(pct=True)
    # ATR 短長比（突破前一根）
    f["atr_ratio"] = (atr(k, 14) / atr(k, 100)).shift(1)
    # 成交量：突破這根量 / 前 20 根均量
    f["vol_ratio"] = k.volume / k.volume.rolling(20).mean().shift(1)
    # 資金費率：最近 9 次（3 天）平均；2020 前無真實資料 → 設為 NaN（濾網放行）
    ev = k.funding[k.index.hour % 8 == 0]
    fr = ev.rolling(9).mean().reindex(k.index, method="ffill")
    fr[k.index < pd.Timestamp("2020-01-03", tz="UTC")] = np.nan
    f["funding3d"] = fr
    # 趨勢強度
    f["adx"] = adx(k)
    # 日線中期動量：60 日報酬、50 日均線
    d = k.close.resample("1D").last()
    f["mom60"] = daily_known(d / d.shift(60) - 1)
    f["above_ma50"] = daily_known((d > d.rolling(50).mean()).astype(float))
    # 突破延伸：收盤超出通道上緣幾倍 ATR
    f["ext_atr"] = (k.close - hi) / atr(k)
    return pd.DataFrame(f)

def make_filters(F):
    """(族, 標籤, 布林陣列)；暖身期 NaN 擋掉；資金費率在 2020 前沒有資料，NaN 放行（否則 2019 年交易全被刪）"""
    pas = lambda cond, s: (cond & s.notna()).values
    pas_nan = lambda cond, s: (cond | s.isna()).values
    out = []
    for th in [0.2, 0.35, 0.5, 0.65]:
        out.append(("波動收縮(通道寬度)", f"寬度百分位≤{th:g}", pas(F.width_rank <= th, F.width_rank)))
    for th in [0.7, 0.85, 1.0]:
        out.append(("波動收縮(ATR比)", f"ATR14/100≤{th:g}", pas(F.atr_ratio <= th, F.atr_ratio)))
    for th in [1.0, 1.3, 1.6, 2.0]:
        out.append(("成交量確認", f"量比≥{th:g}", pas(F.vol_ratio >= th, F.vol_ratio)))
    for th in [0.00005, 0.0001, 0.00015, 0.0002]:
        out.append(("資金費率不擁擠", f"3日均費率≤{th*1e4:g}bp", pas_nan(F.funding3d <= th, F.funding3d)))
    for th in [15, 20, 25, 30]:
        out.append(("ADX 趨勢強度", f"ADX≥{th}", pas(F.adx >= th, F.adx)))
    for th in [0.0, 0.1, 0.2]:
        out.append(("60日動量", f"60日報酬>{th:.0%}", pas(F.mom60 > th, F.mom60)))
    out.append(("50日均線", "收盤>50MA", pas(F.above_ma50 > 0.5, F.above_ma50)))
    for th in [0.5, 1.0, 1.5]:
        out.append(("不追延伸", f"超出通道≤{th:g}ATR", pas(F.ext_atr <= th, F.ext_atr)))
    return out

def summarize(ret, tr):
    a, b = ret[START:IS_END], ret[SPLIT:]
    return {"IS_Sharpe": sh(a), "OOS_Sharpe": sh(b), "全期Sharpe": sh(ret[START:]),
            "IS_CAGR": cg(a), "OOS_CAGR": cg(b), "MaxDD": mdd(ret[START:]), "交易數": len(tr)}

pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
def show(df):
    d = df.copy()
    for c in ["IS_CAGR", "OOS_CAGR", "MaxDD"]: d[c] = (d[c] * 100).round(1)
    for c in ["IS_Sharpe", "OOS_Sharpe", "全期Sharpe"]: d[c] = d[c].round(2)
    print(d.to_string(index=False))

# ---------------------------------------------------------------- 1) 網格
rows, chosen = [], {}
for n in [20, 30]:
    F = features(n)
    r0, _, t0 = donchian(k, n, 3.0)
    rows.append({"N": n, "族": "基準", "濾網": "無", **summarize(r0, t0)})
    for fam, lab, ok in make_filters(F):
        r, _, t = donchian(k, n, 3.0, entry_ok=ok)
        rows.append({"N": n, "族": fam, "濾網": lab, **summarize(r, t)})
grid = pd.DataFrame(rows)
grid.to_csv("filters_grid.csv", index=False)
print("== 1) 濾網網格（ATR 3 倍停損）==")
for n in [20, 30]:
    print(f"\n-- N={n} --"); show(grid[grid.N == n].drop(columns="N"))

# ---------------------------------------------------------------- 2) 每族只用樣本內挑一組（N=20 與 N=30 平均 IS Sharpe）
g = grid[grid.族 != "基準"].groupby(["族", "濾網"]).IS_Sharpe.mean().reset_index()
pick = g.loc[g.groupby("族").IS_Sharpe.idxmax()]
print("\n== 2) 各族依樣本內挑出的參數 ==")
print(pick.to_string(index=False))

# ---------------------------------------------------------------- 3) 對照：隨機略過相同數量的交易
def random_skip(n, ok_target, n_sims, seed=11):
    """以「交易數相同」校準的隨機刪單：先找通過率 p，使隨機遮罩的平均交易數 ≈ 濾網交易數"""
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
        r, _, t = donchian(k, n, 3.0, entry_ok=rng.random(len(k)) < p, trade_from=START)
        res.append((sh(r[START:IS_END]), sh(r[SPLIT:]), sh(r[START:]), len(t)))
    return p, target_tr, np.array(res)

print(f"\n== 3) 濾網 vs 隨機刪掉同樣多的交易（每組 {N_SIMS} 次）==")
cmp_rows = []
for n in [20, 30]:
    F = features(n); fl = {(fam, lab): ok for fam, lab, ok in make_filters(F)}
    for _, row in pick.iterrows():
        ok = fl[(row.族, row.濾網)]
        r, _, t = donchian(k, n, 3.0, entry_ok=ok)
        s = summarize(r, t)
        p, tt, res = random_skip(n, ok, N_SIMS)
        cmp_rows.append({"N": n, "濾網": row.濾網, "交易數": tt, "隨機交易數中位": int(np.median(res[:, 3])),
                         "IS": round(s["IS_Sharpe"], 2), "IS勝過隨機": f"{np.mean(res[:,0] < s['IS_Sharpe']):.0%}",
                         "OOS": round(s["OOS_Sharpe"], 2), "OOS勝過隨機": f"{np.mean(res[:,1] < s['OOS_Sharpe']):.0%}",
                         "隨機OOS中位": round(np.median(res[:, 1]), 2),
                         "全期勝過隨機": f"{np.mean(res[:,2] < s['全期Sharpe']):.0%}"})
        print(cmp_rows[-1], flush=True)
pd.DataFrame(cmp_rows).to_csv("filters_vs_random.csv", index=False)

# ---------------------------------------------------------------- 4) 波動率目標化部位
print("\n== 4) 波動率目標化：倉位 = min(上限, 目標波動 / 過去 30 天實現波動)，進場時決定 ==")
rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
def vol_target(ret, pos, target, cap):
    size_sig = (target / rv).clip(upper=cap).shift(1)          # t-1 收盤已知，t 開盤進場
    active = (pos != 0) | (ret != 0)                            # 含出場那根（pos 已歸 0 但仍有損益）
    entry = active & (pos.shift(1, fill_value=0) == 0)
    size = size_sig.where(entry).ffill().where(active, 0.0).fillna(0.0)
    return ret * size, size
vt_rows = []
for n in [20, 30]:
    r, p, t = donchian(k, n, 3.0)
    vt_rows.append({"N": n, "設定": "固定 1 倍", **summarize(r, t), "平均倉位": 1.0})
    for target in [0.3, 0.4, 0.5]:
        for cap in [1.0, 1.5]:
            rs, size = vol_target(r, p, target, cap)
            vt_rows.append({"N": n, "設定": f"目標{target:.0%} 上限{cap:g}x", **summarize(rs, t),
                            "平均倉位": round(size[p != 0][START:].mean(), 2)})
vt = pd.DataFrame(vt_rows); show(vt)

# ---------------------------------------------------------------- 5) 組合與逐年
print("\n== 5) 組合：通道寬度濾網 + 波動率目標化（目標 40%、上限 1.5x）；逐年報酬 % ==")
yr_rows = {}
for n in [20, 30]:
    F = features(n)
    w_ok = (F.width_rank <= 0.5).values
    r0, p0, t0 = donchian(k, n, 3.0)
    r1, p1, t1 = donchian(k, n, 3.0, entry_ok=w_ok)
    combos = {"基準": (r0, t0), "寬度≤0.5": (r1, t1),
              "波動目標": (vol_target(r0, p0, 0.4, 1.5)[0], t0),
              "寬度≤0.5+波動目標": (vol_target(r1, p1, 0.4, 1.5)[0], t1)}
    show(pd.DataFrame([{"N": n, "設定": name, **summarize(r, t)} for name, (r, t) in combos.items()]))
    for name, (r, _) in combos.items():
        rr = r[START:]; yr_rows[f"N{n} {name}"] = (rr.groupby(rr.index.year).apply(lambda x: (1 + x).prod() - 1) * 100).round(0)
print(pd.DataFrame(yr_rows).T.to_string())

# 波動目標化是否對隨機進場一樣有效？（若是 → 它是風險管理效果，不是訊號 alpha）
rng = np.random.default_rng(3); idx = np.arange(len(k))
n_tr = len(donchian(k, 20, 3.0)[2]); gain = []
for _ in range(min(N_SIMS, 100)):
    mask = np.zeros(len(k), bool); mask[rng.choice(idx[1300:], size=n_tr * 4, replace=False)] = True
    r, p, _ = donchian(k, 20, 3.0, entry_mask=mask)
    gain.append(sh(vol_target(r, p, 0.4, 1.5)[0][START:]) - sh(r[START:]))
print(f"\n隨機進場套用波動目標化，全期 Sharpe 變化中位數 {np.median(gain):+.2f}（S1 本身 "
      f"{sh(vol_target(*donchian(k, 20, 3.0)[:2], 0.4, 1.5)[0][START:]) - sh(donchian(k, 20, 3.0)[0][START:]):+.2f}）")
