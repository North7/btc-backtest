"""
Meta-labeling 實驗（2026-10-23）：S1 的進場訊號照舊，用一個簡單模型判斷「這筆要不要做」，
和寬度濾網（≤0.5）、隨機刪掉同樣多交易比較。

=== 事先寫定（2026-10-23，寫於看到任何結果之前；之後不改）===
主訊號：不加寬度濾網的 S1（paper/engine.py 的 s1_run，wth=1.0）：4H N 根高點突破只做多、日線 200MA、3×ATR 移動停損、
        波動目標 40%（上限 1 倍）、現貨 0.12%/邊、無資金費；N20、N30 兩個子帳戶
資料：Binance 現貨 4H，BTC、ETH、SOL、BNB、XRP（到 2026-09-30）；只用 2019-01-01 起進場的交易
標籤：這筆交易的帳戶報酬（含成本）> 0
特徵（7 個，訊號 K 線收盤時已知）：通道寬度百分位、ATR14/ATR100（前一根）、30 天已實現波動、距日線 200MA（前一天）、
      30 天報酬、收盤超出通道上緣幾倍 ATR、log(成交量 / 前 20 根均量)
模型：邏輯迴歸；特徵用訓練資料標準化、截在 ±4、缺值補 0；L2 懲罰 λ=1（截距不罰）；不調任何超參數，只試這一個模型
驗證：逐年 walk-forward，測試 2021～2026 每一年；訓練資料 = 五幣、N20+N30、在該年 1/1 前已出場的所有交易
門檻：模型在訓練資料中保留的比例 = 寬度濾網在訓練資料中保留的比例（兩者刪掉差不多多的交易）
執行：寬度濾網與模型都在「不加濾網的交易序列」上逐筆決定做或不做（略過的交易期間空手）；
      另列現行實作（濾網擋在進場判斷裡，s1_run wth=0.5）作參考
組合：每幣 N20、N30 各半；五幣等權（每日再平衡；幣種上線滿 380 天才納入，測試期只算這之後進場的交易）
隨機對照：每個幣 × N，從同一批測試期交易中隨機保留與模型相同的筆數，1000 次

通過條件（全部符合才算通過；測試期 2021-01-01 ～ 2026-09-30，五幣組合日報酬 Sharpe，√365）：
  C1 模型 − 寬度濾網 ≥ +0.20（超出約 ±0.2 的價格路徑雜訊）
  C2 模型勝過隨機刪單 ≥ 95%
  C3 兩個子期間（2021–2023、2024–2026/9）模型都高於寬度濾網
  C4 BTC 單幣、BTC+ETH 的測試期 Sharpe，模型都不低於寬度濾網（實盤只會用到這兩種）
通過 → 列為候選，照 RULES.md 第 5 節當作新策略重新紙上交易；不通過 → 不採用，結案
只報告、不當條件：測試交易的 AUC（模型 vs 只用寬度）、保留與略過交易的平均報酬、各年係數
事先知道的限制：寬度門檻 0.5 是用 BTC 2019–2023 挑的（BTC 2021–2023 對寬度濾網有利）；五個幣是「今天還活著」的大幣
=== 事先寫定到此 ===

執行：.venv/bin/python metalabel.py [隨機次數，預設 1000]
"""
import warnings; warnings.filterwarnings("ignore")
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent / "paper"))
import engine as ENG
from backtest import atr

COINS, NS = ["btc", "eth", "sol", "bnb", "xrp"], [20, 30]
START, TEST_FROM, SPLIT = (pd.Timestamp(x, tz="UTC") for x in ["2019-01-01", "2021-01-01", "2024-01-01"])
TEST_YEARS = range(2021, 2027)
LAM, WTH = 1.0, 0.5
FEATS = ["width_rank", "atr_ratio", "rv30", "ma200_dist", "mom30", "ext_atr", "log_vol_ratio"]
N_SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
assert ENG.S1_COST == 0.0012 and ENG.S1_CAP == 1.0

def load(sym):
    k = pd.read_csv(f"data/{sym}_4h.csv", index_col=0, parse_dates=True)
    k["funding"] = 0.0
    return k

def features(k, n):
    hi = k.high.rolling(n).max().shift(1)
    a14 = atr(k, 14)
    d = k.close.resample("1D").last()
    ma = d.rolling(200).mean().shift(1).reindex(k.index, method="ffill")     # 前一天收盤為止的 200 日均線
    return pd.DataFrame({
        "width_rank": ENG.width_rank(k, n),
        "atr_ratio": (a14 / atr(k, 100)).shift(1),
        "rv30": pd.Series(ENG.realized_vol(k), k.index),
        "ma200_dist": k.close / ma - 1,
        "mom30": k.close / k.close.shift(180) - 1,
        "ext_atr": (k.close - hi) / a14,
        "log_vol_ratio": np.log(k.volume / k.volume.rolling(20).mean().shift(1)),
    }, index=k.index)

# ---------------------------------------------------------------- 交易與逐根報酬
K = {c: load(c) for c in COINS}
END = min(k.index[-1] for k in K.values()).normalize()
ELIG = {c: max(TEST_FROM, K[c].index[0].normalize() + pd.Timedelta(days=380)) for c in COINS}
rows, LEG = [], {}
for c in COINS:
    k = K[c]
    days, starts = np.unique(k.index.normalize().values, return_index=True)
    for n in NS:
        s = ENG.s1_run(k, n, wth=1.0)
        F = features(k, n)
        r = (s["ret"] * s["size"]).values
        tid = np.full(len(k), -1)
        for tr in s["trades"]:
            ei, xi = tr["entry_i"], tr["exit_i"]
            if k.index[ei] < START: continue
            tid[ei:xi + 1] = len(rows)
            rows.append({"coin": c, "n": n, "entry_i": ei, "exit_i": xi, "entry": k.index[ei], "exit": k.index[xi],
                         "ret": float(np.prod(1 + r[ei:xi + 1]) - 1), "test": k.index[ei] >= ELIG[c],
                         **F.iloc[ei - 1].to_dict()})
        # 自檢：START 之後、不屬於任何已平倉交易的 K 線，報酬應為 0（期末未平倉那筆除外）
        after = k.index >= START
        if s["open"]: after[s["open"]["entry_i"]:] = False
        assert np.allclose(r[after & (tid < 0)], 0), f"{c} N{n}：交易外的 K 線有報酬"
        live = ENG.s1_run(k, n, wth=WTH)                                      # 現行實作（濾網擋在進場判斷裡）
        rl = (live["ret"] * live["size"]).values; ml = np.zeros(len(k))
        for tr in live["trades"]:
            if k.index[tr["entry_i"]] >= ELIG[c]: ml[tr["entry_i"]:tr["exit_i"] + 1] = 1
        LEG[(c, n)] = {"r": r, "tid": tid, "days": pd.DatetimeIndex(days, tz="UTC"), "starts": starts, "live": rl * ml}
T = pd.DataFrame(rows)
T["y"] = (T.ret > 0).astype(float)
X = T[FEATS].replace([np.inf, -np.inf], np.nan)
D = pd.date_range(TEST_FROM, END, freq="D")
for leg in LEG.values():
    leg["pos"] = D.get_indexer(leg["days"])

def daily_of(leg, x):
    return np.expm1(np.add.reduceat(np.log1p(x), leg["starts"]))

def portfolio(keep, coins=COINS, live=False):
    """keep：每筆交易做或不做（布林，長度 = len(T)）→ 組合日報酬（D 上）"""
    M = np.full((len(D), len(coins)), np.nan)
    for j, c in enumerate(coins):
        acc = np.zeros(len(D))
        for n in NS:
            leg = LEG[(c, n)]
            if live: x = leg["live"]
            else: x = leg["r"] * np.where(leg["tid"] >= 0, keep[np.maximum(leg["tid"], 0)], False)
            d = daily_of(leg, x); ok = leg["pos"] >= 0
            acc[leg["pos"][ok]] += 0.5 * d[ok]
        acc[D < ELIG[c]] = np.nan
        M[:, j] = acc
    return pd.Series(np.nanmean(M, axis=1), D)

def sharpe(r):
    return r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan
def summ(r):
    eq = (1 + r).cumprod()
    return {"測試期": sharpe(r), "2021–23": sharpe(r[r.index < SPLIT]), "2024–26/9": sharpe(r[r.index >= SPLIT]),
            "CAGR%": (eq.iloc[-1] ** (365 / len(r)) - 1) * 100, "MaxDD%": (eq / eq.cummax() - 1).min() * 100}

# ---------------------------------------------------------------- 樣本
print(f"資料：Binance 現貨 4H 到 {END.date()}；成本 {ENG.S1_COST:.2%}/邊、波動目標 40%、上限 {ENG.S1_CAP:g} 倍")
print("\n== 樣本：不加濾網的 S1 交易（2019 起進場）==")
smp = T.groupby(["coin", "n"]).agg(筆數=("y", "size"), 勝率=("y", "mean"), 測試期筆數=("test", "sum"),
                                   寬度通過率=("width_rank", lambda s: (s <= WTH).mean()))
print(smp.round(2).to_string())
miss = "、".join(f"{f} {X[f].isna().sum()}" for f in FEATS if X[f].isna().any())
print(f"合計 {len(T)} 筆（N20、N30 同一根進場的交易完全相同，兩邊都算）；特徵缺值：{miss or '無'}")

# ---------------------------------------------------------------- 逐年 walk-forward
def fit_logit(Z, y, lam):
    A = np.c_[np.ones(len(Z)), Z]; w = np.zeros(A.shape[1])
    P = np.eye(len(w)) * lam; P[0, 0] = 0
    for _ in range(100):
        p = 1 / (1 + np.exp(-A @ w))
        step = np.linalg.solve(A.T @ (A * (p * (1 - p))[:, None]) + P, A.T @ (p - y) + P @ w)
        w -= step
        if np.abs(step).max() < 1e-10: break
    return w

def auc(score, y):
    rk = pd.Series(score).rank().values; pos = y == 1; n1, n0 = pos.sum(), (~pos).sum()
    return (rk[pos].sum() - n1 * (n1 + 1) / 2) / (n1 * n0) if n1 and n0 else np.nan

T["p"] = np.nan; T["keep_meta"] = False
wf, coef = [], {}
for Y in TEST_YEARS:
    y0, y1 = pd.Timestamp(f"{Y}-01-01", tz="UTC"), pd.Timestamp(f"{Y + 1}-01-01", tz="UTC")
    tr = (T.exit < y0).values
    te = ((T.entry >= y0) & (T.entry < y1) & T.test).values
    mu, sd = X[tr].mean(), X[tr].std()
    Z = ((X - mu) / sd).clip(-4, 4).fillna(0).values
    w = fit_logit(Z[tr], T.y.values[tr], LAM)
    p = 1 / (1 + np.exp(-np.c_[np.ones(len(Z)), Z] @ w))
    q = (T.width_rank[tr] <= WTH).mean()
    th = np.quantile(p[tr], 1 - q)
    T.loc[te, "p"] = p[te]; T.loc[te, "keep_meta"] = p[te] >= th
    coef[Y] = pd.Series(w[1:], FEATS)
    yt = T.y.values[te]
    wf.append({"測試年": Y, "訓練筆數": int(tr.sum()), "訓練勝率": T.y[tr].mean(), "保留比例": q, "測試筆數": int(te.sum()),
               "模型保留": int((p[te] >= th).sum()), "寬度保留": int((T.width_rank[te] <= WTH).sum()),
               "AUC 模型": auc(p[te], yt), "AUC 只用寬度": auc(-T.width_rank.values[te], yt),
               "訓練內 AUC": auc(p[tr], T.y.values[tr])})
print("\n== 逐年 walk-forward（每年只用之前已出場的交易訓練）==")
print(pd.DataFrame(wf).round(2).to_string(index=False))
print("\n各年係數（標準化後；正 = 越大越可能賺錢）：")
print(pd.DataFrame(coef).round(2).to_string())

# ---------------------------------------------------------------- 組合比較
tst = T.test.values
KEEP = {"不加濾網（全做）": tst.copy(), "寬度≤0.5（逐筆）": tst & (T.width_rank.values <= WTH),
        "模型（逐筆）": tst & T.keep_meta.values}
te_all = tst
print("\n== 測試期交易：保留 vs 略過的平均報酬（帳戶層級）==")
for name in ["寬度≤0.5（逐筆）", "模型（逐筆）"]:
    kp = KEEP[name]
    print(f"{name}：保留 {kp.sum()} 筆、平均 {T.ret[kp].mean():+.2%}、勝率 {T.y[kp].mean():.0%}；"
          f"略過 {(te_all & ~kp).sum()} 筆、平均 {T.ret[te_all & ~kp].mean():+.2%}、勝率 {T.y[te_all & ~kp].mean():.0%}")
yt = T.y.values[te_all]
print(f"整個測試期 AUC：模型 {auc(T.p.values[te_all], yt):.3f}、只用寬度 {auc(-T.width_rank.values[te_all], yt):.3f}（0.5 = 沒有資訊）")

GROUPS = {"五幣": COINS, "BTC": ["btc"], "BTC+ETH": ["btc", "eth"]}
res, rows = {}, []
for g, cs in GROUPS.items():
    for name, kp in KEEP.items():
        res[(name, g)] = summ(portfolio(kp, cs))
        rows.append({"組合": g, "版本": name, "筆數": int(kp[T.coin.isin(cs).values].sum()), **res[(name, g)]})
    rows.append({"組合": g, "版本": "寬度≤0.5（現行實作，參考）", "筆數": np.nan, **summ(portfolio(None, cs, live=True))})
print("\n== 測試期 2021-01 ～ 2026-09：日報酬 Sharpe ==")
print(pd.DataFrame(rows).round(2).to_string(index=False))

# ---------------------------------------------------------------- 隨機刪單
def random_runs(kp, sims, seed):
    rng = np.random.default_rng(seed)
    groups = [(np.flatnonzero(tst & (T.coin == c).values & (T.n == n).values), int(kp[(T.coin == c).values & (T.n == n).values].sum()))
              for c in COINS for n in NS]
    out = []
    for _ in range(sims):
        keep = np.zeros(len(T), bool)
        for ids, m in groups:
            keep[rng.choice(ids, m, replace=False)] = True
        out.append(sharpe(portfolio(keep)))
    return np.array(out)
print(f"\n== 隨機刪單（每個幣 × N 保留同樣筆數，{N_SIMS} 次；五幣組合測試期 Sharpe）==")
RND = {}
for name, seed in [("模型（逐筆）", 21), ("寬度≤0.5（逐筆）", 22)]:
    RND[name] = random_runs(KEEP[name], N_SIMS, seed)
    s0 = res[(name, "五幣")]["測試期"]
    print(f"{name}：Sharpe {s0:.2f}；隨機中位 {np.median(RND[name]):.2f}（5%～95%：{np.percentile(RND[name], 5):.2f}～"
          f"{np.percentile(RND[name], 95):.2f}）；勝過隨機 {np.mean(RND[name] < s0):.0%}")

# ---------------------------------------------------------------- 事先寫定的通過條件
m5, w5 = res[("模型（逐筆）", "五幣")], res[("寬度≤0.5（逐筆）", "五幣")]
beat = np.mean(RND["模型（逐筆）"] < m5["測試期"])
checks = [
    ("C1 模型 − 寬度 ≥ +0.20（五幣測試期）", m5["測試期"] - w5["測試期"] >= 0.20, f"{m5['測試期']:.2f} − {w5['測試期']:.2f} = {m5['測試期'] - w5['測試期']:+.2f}"),
    ("C2 勝過隨機刪單 ≥ 95%", beat >= 0.95, f"{beat:.0%}"),
    ("C3 兩個子期間都高於寬度", m5["2021–23"] > w5["2021–23"] and m5["2024–26/9"] > w5["2024–26/9"],
     f"2021–23 {m5['2021–23']:.2f} vs {w5['2021–23']:.2f}；2024–26/9 {m5['2024–26/9']:.2f} vs {w5['2024–26/9']:.2f}"),
    ("C4 BTC、BTC+ETH 都不低於寬度", all(res[("模型（逐筆）", g)]["測試期"] >= res[("寬度≤0.5（逐筆）", g)]["測試期"] for g in ["BTC", "BTC+ETH"]),
     "；".join(f"{g} {res[('模型（逐筆）', g)]['測試期']:.2f} vs {res[('寬度≤0.5（逐筆）', g)]['測試期']:.2f}" for g in ["BTC", "BTC+ETH"])),
]
print("\n== 事先寫定的通過條件 ==")
for name, ok, detail in checks:
    print(f"{'✓' if ok else '✗'} {name}：{detail}")
print("結論：" + ("全部通過 → 列為候選，需當作新策略重新紙上交易" if all(ok for _, ok, _ in checks) else "未通過 → 不採用，結案"))
