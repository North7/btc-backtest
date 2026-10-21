"""
再次深入檢查（2026-10-21）。全部用現行實盤設定：現貨、每邊 0.12%、無資金費、波動目標倉位上限 1 倍。
A) S1 現行設定的穩健性：ATR 停損倍數、波動目標（不是要挑新參數，而是看 3 倍、40% 是否在懸崖邊）
B) 多幣種 S1：BTC 的參數原封不動套到 ETH、SOL、BNB、XRP，等權組合
C) 新策略：多頭中買回檔（日線 RSI(2) 均值回歸），補趨勢策略在震盪市的空缺
   參數只用 BTC 樣本內挑，其他幣與樣本外只用來檢驗；和「多頭中隨機買進、持有同樣天數」比
D) 與 S1、D 的相關性與組合
執行：python deep_review.py [隨機次數，預設 300]
"""
import warnings; warnings.filterwarnings("ignore")
import sys
import numpy as np, pandas as pd
import backtest as BT
from backtest import donchian, BARS_PER_YEAR

START, SPLIT, IS_END = "2019-01-01", "2024-01-01", "2023-12-31"
SPOT_COST = 0.0012
BT.COST = SPOT_COST
N_SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 300
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)

def load_spot(sym):
    k = pd.read_csv(f"data/{sym}_4h.csv", index_col=0, parse_dates=True)
    k["funding"] = 0.0
    return k
ASSETS = {s.upper(): load_spot(s) for s in ["btc", "eth", "sol", "bnb", "xrp"]}
ASSETS["OKX-BTC"] = load_spot("okx_btc")

def stats_d(r):
    """r：日報酬"""
    r = r.dropna()
    if len(r) < 30 or r.std() == 0: return {"Sharpe": np.nan, "CAGR%": np.nan, "MaxDD%": np.nan}
    eq = (1 + r).cumprod()
    return {"Sharpe": r.mean() / r.std() * np.sqrt(365), "CAGR%": (eq.iloc[-1] ** (365 / len(r)) - 1) * 100,
            "MaxDD%": (eq / eq.cummax() - 1).min() * 100}
def split_row(r, prefix=""):
    a, b, c = stats_d(r[START:IS_END]), stats_d(r[SPLIT:]), stats_d(r[START:])
    return {f"{prefix}IS": a["Sharpe"], f"{prefix}OOS": b["Sharpe"], f"{prefix}全期": c["Sharpe"],
            f"{prefix}CAGR%": c["CAGR%"], f"{prefix}MaxDD%": c["MaxDD%"]}
daily = lambda r4: (1 + r4).resample("1D").prod() - 1

# ---------------------------------------------------------------- S1（現行設定）
def width_ok(k, n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    return (((hi - lo) / k.close.shift(1)).rolling(6 * 180).rank(pct=True) <= 0.5).values

def vol_target(k, ret, pos, target=0.4, cap=1.0):
    rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
    s = (target / rv).clip(upper=cap).shift(1)
    act = (pos != 0) | (ret != 0)
    e = act & (pos.shift(1, fill_value=0) == 0)
    return ret * s.where(e).ffill().where(act, 0.0).fillna(0.0)

def s1(k, mult=3.0, target=0.4):
    rs = []
    for n in [20, 30]:
        r, p, _ = donchian(k, n, mult, entry_ok=width_ok(k, n))
        rs.append(vol_target(k, r, p, target))
    return daily((rs[0] + rs[1]) / 2)

# ---------------------------------------------------------------- A) 穩健性
print("== A) S1 現行設定（現貨、N20+N30、寬度＋波動目標）的參數鄰近值 ==")
rows = []
for ex in ["BTC", "OKX-BTC", "ETH"]:
    k = ASSETS[ex]
    for m in [2.0, 2.5, 3.0, 3.5, 4.0]:
        rows.append({"資料": ex, "ATR 倍數": m, "波動目標": "40%", **split_row(s1(k, m, 0.4))})
    for tg in [0.3, 0.5, 0.6]:
        rows.append({"資料": ex, "ATR 倍數": 3.0, "波動目標": f"{tg:.0%}", **split_row(s1(k, 3.0, tg))})
print(pd.DataFrame(rows).round(2).to_string(index=False))

# ---------------------------------------------------------------- B) 多幣種
print("\n== B) 多幣種 S1：BTC 參數原封不動（2019 起；SOL 2021 年中才有足夠暖身資料）==")
S1R = {ex: s1(ASSETS[ex]) for ex in ["BTC", "ETH", "SOL", "BNB", "XRP"]}
first = {ex: ASSETS[ex].index[0] + pd.Timedelta(days=380) for ex in S1R}   # 200 日均線＋180 天寬度百分位暖身
for ex in S1R: S1R[ex] = S1R[ex][S1R[ex].index >= max(first[ex], pd.Timestamp(START, tz="UTC"))]
rows = [{"幣": ex, "起算": S1R[ex].index[0].date(), **split_row(S1R[ex])} for ex in S1R]
M = pd.DataFrame(S1R)
def eq_port(cols):
    return M[cols].mean(axis=1, skipna=True)          # 每天在已上線的幣之間等分（每日再平衡）
for name, cols in [("BTC 單幣", ["BTC"]), ("BTC+ETH", ["BTC", "ETH"]), ("五幣等權", list(S1R))]:
    rows.append({"幣": name, "起算": "", **split_row(eq_port(cols))})
print(pd.DataFrame(rows).round(2).to_string(index=False))
print("各幣 S1 日報酬相關（2022 起）：")
print(M["2022":].corr().round(2).to_string())

# ---------------------------------------------------------------- C) 多頭中買回檔
def rsi(c, n):
    d = c.diff(); up = d.clip(lower=0); dn = -d.clip(upper=0)
    a = 1 / n
    rs = up.ewm(alpha=a, adjust=False).mean() / dn.ewm(alpha=a, adjust=False).mean()
    return 100 - 100 / (1 + rs)

def to_day(k):
    return k.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

def mr(day, th=10, exit_ma=5, trend=200, max_hold=10, entry_mask=None, hold_draw=None, rng=None):
    """第 i 天收盤：收盤 > 200 日均線且 RSI(2) < th → 第 i+1 天開盤買進（全倉）；
    收盤 > exit_ma 日均線或持有滿 max_hold 天 → 下一天開盤賣出。entry_mask / hold_draw 給隨機對照用"""
    o, c = day.open.values, day.close.values
    up = (day.close > day.close.rolling(trend).mean()).values
    r2 = rsi(day.close, 2).values
    mx = day.close.rolling(exit_ma).mean().values
    N = len(c); ret = np.zeros(N); pos = 0; hold = 0; pend_in = pend_out = False; trades = []; ent = 0; limit = max_hold
    for i in range(1, N):
        if pend_in and pos == 0:
            pos = 1; hold = 0; ent = o[i]; ret[i] += c[i] / o[i] - 1 - SPOT_COST
            if hold_draw is not None: limit = int(rng.choice(hold_draw))
        elif pend_out and pos == 1:
            ret[i] += o[i] / c[i - 1] - 1 - SPOT_COST; pos = 0; trades.append(o[i] / ent - 1 - 2 * SPOT_COST)
        elif pos == 1:
            ret[i] += c[i] / c[i - 1] - 1
        pend_in = pend_out = False
        if pos == 1:
            hold += 1
            if hold_draw is not None: pend_out = hold >= limit
            else: pend_out = c[i] > mx[i] or hold >= max_hold
        elif up[i] and (entry_mask[i] if entry_mask is not None else r2[i] < th):
            pend_in = True
    return pd.Series(ret, day.index), np.array(trades)

print("\n== C) 多頭中買回檔（日線 RSI(2)）：BTC 參數網格，只看樣本內挑 ==")
dayB = to_day(ASSETS["BTC"])
rows = []
for th in [5, 10, 15, 20]:
    for xm in [3, 5, 10]:
        r, t = mr(dayB, th, xm)
        rows.append({"RSI2 <": th, "出場均線": xm, **split_row(r), "交易數": len(t[t == t]),
                     "勝率": (t > 0).mean() if len(t) else np.nan, "在場%": (r[START:] != 0).mean() * 100})
G = pd.DataFrame(rows); print(G.round(2).to_string(index=False))
best = G.loc[G.IS.idxmax()]; TH, XM = int(best["RSI2 <"]), int(best["出場均線"])
print(f"依樣本內挑出：RSI2 < {TH}、出場均線 {XM} 日")

print("\n同一組參數套到其他資料（不重新調參）：")
rows, MR = [], {}
for ex in ["BTC", "OKX-BTC", "ETH", "SOL", "BNB", "XRP"]:
    d = to_day(ASSETS[ex]); r, t = mr(d, TH, XM)
    r = r[r.index >= d.index[0] + pd.Timedelta(days=200)]
    MR[ex] = r
    rows.append({"資料": ex, **split_row(r), "交易數": len(t), "勝率": (t > 0).mean() if len(t) else np.nan,
                 "平均每筆%": t.mean() * 100 if len(t) else np.nan})
print(pd.DataFrame(rows).round(2).to_string(index=False))

print(f"\n隨機對照：在 200 日均線上方的日子隨機買進、持有天數從實際交易抽樣（{N_SIMS} 次）")
for ex in ["BTC", "ETH"]:
    d = to_day(ASSETS[ex]); r0, t0 = mr(d, TH, XM)
    # 實際持有天數
    pos = (r0 != 0).astype(int).values
    holds, run = [], 0
    for v in pos:
        if v: run += 1
        elif run: holds.append(run); run = 0
    up = (d.close > d.close.rolling(200).mean()).values
    rng = np.random.default_rng(13); sims = []
    p_in = len(t0) / max(1, up.sum())
    for _ in range(N_SIMS):
        mask = rng.random(len(d)) < p_in * 1.6
        r, t = mr(d, TH, XM, entry_mask=mask, hold_draw=np.maximum(1, np.array(holds) - 1), rng=rng)   # 有報酬的天數含出場那天開盤
        sims.append((stats_d(r[START:])["Sharpe"], len(t)))
    sims = np.array(sims); s0 = stats_d(r0[START:])["Sharpe"]
    print(f"{ex}: 策略 Sharpe {s0:.2f}（{len(t0)} 筆）；隨機中位 {np.nanmedian(sims[:,0]):.2f}（{int(np.median(sims[:,1]))} 筆）；勝過隨機 {np.mean(sims[:,0] < s0):.0%}")

# ---------------------------------------------------------------- D) 相關性與組合（BTC）
sys.path.insert(0, "paper")
import engine as ENG
kB = ASSETS["BTC"]
Dd = ENG.d_run(kB)["value"].resample("1D").last().pct_change()
R = pd.DataFrame({"S1": S1R["BTC"], "D": Dd, "回檔": MR["BTC"], "BTC": daily(kB.close.pct_change())})[START:"2026-09-30"].fillna(0)
print("\n== D) BTC 上各策略日報酬相關（2019 起）==")
print(R.corr().round(2).to_string())
yr = lambda r: (r.groupby(r.index.year).apply(lambda x: (1 + x).prod() - 1) * 100).round(1)
print("\n逐年報酬 %：")
print(pd.DataFrame({k: yr(R[k]) for k in ["S1", "D", "回檔", "BTC"]}).T.to_string())
rows = []
for name, w in [("S1+D 各半（現行）", {"S1": .5, "D": .5}), ("S1+D+回檔 各 1/3", {"S1": 1/3, "D": 1/3, "回檔": 1/3}),
                ("S1 50%＋回檔 50%", {"S1": .5, "回檔": .5}), ("S1+D 40%＋回檔 20%", {"S1": .4, "D": .4, "回檔": .2})]:
    rows.append({"組合（每日再平衡）": name, **split_row(sum(R[k] * v for k, v in w.items()))})
print(pd.DataFrame(rows).round(2).to_string(index=False))
