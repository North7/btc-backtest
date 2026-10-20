"""
驗證 Gemini 交叉審查（REVIEW_FEEDBACK.md）的主張，按本專案的紀律重測：
- 參數只用樣本內（2019–2023）挑，樣本外（2024–2026/9）只用來檢驗
- 同一想法要在 Binance BTC、OKX BTC、Binance ETH 上方向一致
- 加了規則就要和「隨機版」比
各節：
A) 隨機刪單改成「整段訊號抽樣」：在基準策略的進場中無放回抽出與濾網版相同筆數
B) 4H SMA1200 取代日線 200MA（含鄰近值）
C) ATR/收盤價百分位取代通道寬度百分位
D) 浮盈階梯收緊（ratchet）網格
E) N=20 與 N=30 等權合成；BTC/ETH 等權組合（不調權重）
執行：python review_gemini.py [隨機次數，預設 300]
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

def width_rank(k, n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    return ((hi - lo) / k.close.shift(1)).rolling(6 * 180).rank(pct=True)

def atrp_rank(k):
    """ATR(14)/收盤價，取前一根，在過去 180 天中的百分位（Gemini 建議 2）"""
    return (atr(k) / k.close).shift(1).rolling(6 * 180).rank(pct=True)

def vol_target(k, ret, pos, target=0.4, cap=1.5):
    rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
    size_sig = (target / rv).clip(upper=cap).shift(1)
    active = (pos != 0) | (ret != 0)
    entry = active & (pos.shift(1, fill_value=0) == 0)
    return ret * size_sig.where(entry).ffill().where(active, 0.0).fillna(0.0)

def run(k, n, ok=None, vt=True, **kw):
    r, p, t = donchian(k, n, 3.0, entry_ok=ok, trade_from=START, **kw)
    return (vol_target(k, r, p) if vt else r), p, t

def row(r, t=None):
    d = {"IS": sh(r[START:IS_END]), "OOS": sh(r[SPLIT:]), "全期": sh(r[START:]),
         "CAGR%": cg(r[START:]) * 100, "MaxDD%": mdd(r[START:]) * 100,
         "25–26%": ((1 + r["2025":]).prod() - 1) * 100}
    if t is not None: d["交易"] = len(t)
    return d

def show(rows, title):
    print(f"\n== {title} ==")
    df = pd.DataFrame(rows)
    print(df.round(2).to_string(index=False))
    return df

# ---------------------------------------------------------------- A) 整段訊號抽樣的隨機對照
def entry_signal_bars(k, n):
    """基準策略每筆進場的訊號根（進場前一根）"""
    r, p, _ = donchian(k, n, 3.0)
    active = (p != 0) | (r != 0)
    e = np.flatnonzero((active & (p.shift(1, fill_value=0) == 0)).values)
    return e - 1

rows = []
for ex in ["BTC", "OKX", "ETH"]:
    k = DATA[ex]
    for n in [20, 30]:
        ok = (width_rank(k, n) <= 0.5).values
        rf, _, tf = donchian(k, n, 3.0, entry_ok=ok, trade_from=START)
        sig = entry_signal_bars(k, n)
        sig = sig[k.index[sig] >= pd.Timestamp(START, tz="UTC") - pd.Timedelta(hours=4)]
        rng = np.random.default_rng(5); res = []
        for _ in range(N_SIMS):
            m = np.zeros(len(k), bool); m[rng.choice(sig, size=min(len(tf), len(sig)), replace=False)] = True
            r, _, _ = donchian(k, n, 3.0, entry_ok=m)
            res.append((sh(r[START:IS_END]), sh(r[SPLIT:]), sh(r[START:])))
        res = np.array(res); s = (sh(rf[START:IS_END]), sh(rf[SPLIT:]), sh(rf[START:]))
        rows.append({"資料": ex, "N": n, "濾網交易": len(tf), "基準進場": len(sig),
                     "隨機全期中位": np.median(res[:, 2]),
                     "勝過隨機 IS": f"{np.mean(res[:,0] < s[0]):.0%}", "OOS": f"{np.mean(res[:,1] < s[1]):.0%}",
                     "全期": f"{np.mean(res[:,2] < s[2]):.0%}"})
        print(rows[-1], flush=True)
show(rows, f"A) 寬度≤0.5 vs 從基準進場中無放回抽出同樣筆數（{N_SIMS} 次），不加波動目標")

# ---------------------------------------------------------------- B) 趨勢濾網：日線 vs 4H 均線（含鄰近值）
def trend_variant(k, kind, days):
    if kind == "日線":
        d = k.close.resample("1D").last()
        return (d > d.rolling(days).mean()).shift(1).reindex(k.index, method="ffill").fillna(False).astype(bool).values
    m = k.close.rolling(days * 6).mean()
    return (k.close > m).fillna(False).values

rows = []
for ex in ["BTC", "OKX", "ETH"]:
    k = DATA[ex]
    for n in [20, 30]:
        wr = (width_rank(k, n) <= 0.5).values
        for kind in ["日線", "4H"]:
            for days in [150, 200, 250]:
                ok = wr & trend_variant(k, kind, days)
                r, _, t = run(k, n, ok, use_filter=False)
                rows.append({"資料": ex, "N": n, "趨勢": f"{kind}{days}天", **row(r, t)})
dfB = show(rows, "B) 寬度≤0.5＋波動目標，趨勢濾網換成 4H 均線（4H 150/200/250 天 = 900/1200/1500 根）")

# ---------------------------------------------------------------- C) 波動收縮的定義：通道寬度 vs ATR/價格
rows = []
for ex in ["BTC", "OKX", "ETH"]:
    k = DATA[ex]; ar = atrp_rank(k)
    for n in [20, 30]:
        wr = width_rank(k, n)
        for th in [0.35, 0.5, 0.65]:
            for lab, s in [("通道寬度", wr), ("ATR/價格", ar)]:
                r, _, t = run(k, n, (s <= th).values)
                rows.append({"資料": ex, "N": n, "定義": lab, "門檻": th, **row(r, t)})
dfC = show(rows, "C) 波動收縮定義 × 門檻（＋波動目標，日線 200MA）")
print("\n樣本內平均 Sharpe（BTC N20+N30）：")
print(dfC[dfC.資料 == "BTC"].groupby(["定義", "門檻"]).IS.mean().round(2).unstack())

# ---------------------------------------------------------------- D) 浮盈階梯收緊
rows = []
for ex in ["BTC", "OKX", "ETH"]:
    k = DATA[ex]
    for n in [20, 30]:
        ok = (width_rank(k, n) <= 0.5).values
        r, _, t = run(k, n, ok)
        rows.append({"資料": ex, "N": n, "觸發×ATR": "-", "收緊至": 3.0, **row(r, t)})
        for trig in [3, 4, 5, 6]:
            for rm in [1.5, 2.0, 2.5]:
                r, _, t = run(k, n, ok, ratchet=(trig, rm))
                rows.append({"資料": ex, "N": n, "觸發×ATR": trig, "收緊至": rm, **row(r, t)})
dfD = pd.DataFrame(rows)
g = dfD[(dfD.資料 == "BTC") & (dfD["觸發×ATR"] != "-")].groupby(["觸發×ATR", "收緊至"])
print("\n== D) 浮盈收緊網格（寬度≤0.5＋波動目標），BTC 樣本內平均 Sharpe（N20/N30）==")
print(g.IS.mean().round(2).unstack())
print("BTC 樣本外平均 Sharpe（只供對照，不用來挑）：")
print(g.OOS.mean().round(2).unstack())
best = g.IS.mean().idxmax(); print("依樣本內挑出：", best)
sel = dfD[(dfD["觸發×ATR"] == "-") | ((dfD["觸發×ATR"] == best[0]) & (dfD.收緊至 == best[1]))
          | ((dfD["觸發×ATR"] == 4) & (dfD.收緊至 == 2.0))]
print(sel.round(2).to_string(index=False))

# ---------------------------------------------------------------- E) 合成
rows = []
port = {}
for ex in ["BTC", "OKX", "ETH"]:
    k = DATA[ex]
    rs = {}
    for n in [20, 30]:
        rs[n], _, _ = run(k, n, (width_rank(k, n) <= 0.5).values)
        rows.append({"組合": f"{ex} N{n}", **row(rs[n])})
    port[ex] = (rs[20] + rs[30]) / 2                   # 兩個子帳戶各半資金
    rows.append({"組合": f"{ex} N20+N30 各半", **row(port[ex])})
bt = pd.concat([port["BTC"], port["ETH"]], axis=1).dropna()
rows.append({"組合": "BTC+ETH 各半（各自 N20+N30）", **row(bt.mean(axis=1))})
rows.append({"組合": "BTC 60%+ETH 40%（Gemini 權重）", **row(0.6 * bt.iloc[:, 0] + 0.4 * bt.iloc[:, 1])})
show(rows, "E) 合成（寬度≤0.5＋波動目標，日線 200MA）")
print(f"BTC 與 ETH 策略日報酬相關係數：{bt.resample('1D').sum().corr().iloc[0,1]:.2f}")
