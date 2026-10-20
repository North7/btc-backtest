"""
S1 結合 4H Supertrend（超級趨勢）。Supertrend 用 TradingView 的標準算法：
hl2 ± 倍數 × ATR（Wilder 平滑），上下軌只朝趨勢方向移動，收盤穿越另一側軌道時翻轉。第 t 根收盤即已知。
測四種結合方式（其餘規則同現版：寬度≤0.5、日線 200MA、波動目標 40%/1.5x、N20+N30 各半）：
  A 基準：通道突破進場、3×ATR 移動停損
  B 進場濾網：突破當下 Supertrend 必須是多頭
  C 出場改用 Supertrend：停損 = Supertrend 下軌（只升不降），觸價出場
  D 原停損＋翻空出場：3×ATR 停損之外，Supertrend 收盤翻空就在下一根開盤出場
  E 用 Supertrend 翻多取代突破進場（仍需寬度與 200MA），3×ATR 停損
參數：預設 (10, 3)，鄰近值 (10,2)(10,4)(14,3)(20,3)；只用樣本內挑
執行：python supertrend.py
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from backtest import *

START, SPLIT, IS_END = "2019-01-01", "2024-01-01", "2023-12-31"
sh = lambda r: r.mean() / r.std() * np.sqrt(BARS_PER_YEAR) if r.std() > 0 else np.nan
cg = lambda r: (1 + r).prod() ** (BARS_PER_YEAR / len(r)) - 1
mdd = lambda r: ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()
DATA = {"BTC": load(), "OKX": load("data/okx_btc_4h.csv", "data/okx_funding_spliced.csv"),
        "ETH": load("data/eth_4h.csv", "data/eth_funding.csv")}
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)

def supertrend(k, period=10, mult=3.0):
    h, l, c = k.high.values, k.low.values, k.close.values
    pc = np.r_[np.nan, c[:-1]]
    tr = np.nanmax(np.c_[h - l, np.abs(h - pc), np.abs(l - pc)], axis=1)
    a = pd.Series(tr).ewm(alpha=1 / period, adjust=False).mean().values      # Wilder ATR
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fu, fl, up = ub.copy(), lb.copy(), np.ones(len(c), bool)
    for t in range(1, len(c)):
        fu[t] = ub[t] if (ub[t] < fu[t - 1] or c[t - 1] > fu[t - 1]) else fu[t - 1]
        fl[t] = lb[t] if (lb[t] > fl[t - 1] or c[t - 1] < fl[t - 1]) else fl[t - 1]
        if up[t - 1]: up[t] = not (c[t] < fl[t])
        else:         up[t] = c[t] > fu[t]
    up[:period] = False
    return up, fl

def width_ok(k, n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    return (((hi - lo) / k.close.shift(1)).rolling(6 * 180).rank(pct=True) <= 0.5).values

def run(k, n, mode, st_up, st_line, mult=3.0):
    """mode: A/B/C/D/E；與 backtest.donchian 相同的成交與成本假設"""
    o, h, l, c, fu = (k[x].values for x in ["open", "high", "low", "close", "funding"])
    a = atr(k).values
    hi = k.high.rolling(n).max().shift(1).values
    up_ok = daily_filter(k)[0].values
    ok = width_ok(k, n)
    N = len(c); ret = np.zeros(N); pos_arr = np.zeros(N)
    pos, stop, best, pending, exit_next, ntr = 0, np.nan, np.nan, 0, False, 0
    for t in range(1, N):
        entered = False
        if pos and exit_next:                                   # D：翻空後下一根開盤出場
            ret[t] += o[t] / c[t - 1] - 1 - COST; pos = 0; stop = np.nan; exit_next = False
            pos_arr[t] = 0
        if pending == 1 and pos == 0:
            pos = 1; ret[t] -= COST; entered = True; best = o[t]; ntr += 1
            stop = st_line[t - 1] if mode == "C" else o[t] - mult * a[t - 1]
        pending = 0
        ref = o[t] if entered else c[t - 1]
        if pos:
            if not entered: ret[t] -= fu[t]
            if l[t] <= stop:
                fill = stop if (entered and stop < o[t]) else min(o[t], stop)
                ret[t] += fill / ref - 1 - COST; pos = 0; stop = np.nan
            else:
                ret[t] += c[t] / ref - 1
                best = max(best, c[t])
                new = st_line[t] if mode == "C" else best - mult * a[t]
                stop = max(stop, new)
                if mode == "D" and not st_up[t]: exit_next = True
        pos_arr[t] = pos
        if np.isnan(hi[t]) or np.isnan(a[t]): continue
        if pos == 0 and up_ok[t] and ok[t]:
            if mode == "E":
                if st_up[t] and not st_up[t - 1]: pending = 1
            elif c[t] > hi[t] and (mode != "B" or st_up[t]):
                pending = 1
    return pd.Series(ret, k.index), pd.Series(pos_arr, k.index), ntr

def vol_target(k, ret, pos, target=0.4, cap=1.5):
    rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
    s = (target / rv).clip(upper=cap).shift(1)
    act = (pos != 0) | (ret != 0)
    e = act & (pos.shift(1, fill_value=0) == 0)
    return ret * s.where(e).ffill().where(act, 0.0).fillna(0.0)

# 自檢：模式 A 與研究引擎一致
k = DATA["BTC"]; up, line = supertrend(k)
for n in [20, 30]:
    ref = donchian(k, n, 3.0, entry_ok=width_ok(k, n))[0]
    assert np.allclose(ref.values, run(k, n, "A", up, line)[0].values, atol=1e-12)
print("自檢通過：模式 A 與 backtest.donchian 逐根一致")
print(f"Supertrend(10,3) 4H 多頭時間比例（BTC，2019 起）：{pd.Series(up, k.index)['2019':].mean():.0%}")
# 突破訊號當下 Supertrend 已是多頭的比例
for n in [20, 30]:
    hi = k.high.rolling(n).max().shift(1).values
    brk = (k.close.values > hi) & daily_filter(k)[0].values & width_ok(k, n)
    brk &= (k.index >= pd.Timestamp(START, tz="UTC"))
    print(f"N{n} 突破訊號根數 {brk.sum()}，其中 Supertrend 已翻多 {up[brk].mean():.0%}")

NAMES = {"A": "A 基準", "B": "B 進場濾網", "C": "C 出場改 ST", "D": "D 加翻空出場", "E": "E ST 翻多進場"}
PARAMS = [(10, 3.0), (10, 2.0), (10, 4.0), (14, 3.0), (20, 3.0)]
rows = []
for ex, k in DATA.items():
    for (per, m) in PARAMS:
        up, line = supertrend(k, per, m)
        for mode in "ABCDE":
            if mode == "A" and (per, m) != (10, 3.0): continue
            rs, nt = [], 0
            for n in [20, 30]:
                r, p, c = run(k, n, mode, up, line); rs.append(vol_target(k, r, p)); nt += c
            r = (rs[0] + rs[1]) / 2
            rows.append({"資料": ex, "方式": NAMES[mode], "ST參數": "-" if mode == "A" else f"({per},{m:g})",
                         "IS": sh(r[START:IS_END]), "OOS": sh(r[SPLIT:]), "全期": sh(r[START:]),
                         "CAGR%": cg(r[START:]) * 100, "MaxDD%": mdd(r[START:]) * 100,
                         "25–26%": ((1 + r["2025":]).prod() - 1) * 100, "交易": nt})
df = pd.DataFrame(rows)
print("\n== N20+N30 各半，寬度＋200MA＋波動目標；樣本內 2019–23 / 樣本外 2024–26/9 ==")
for ex in DATA:
    print(f"\n-- {ex} --"); print(df[df.資料 == ex].drop(columns="資料").round(2).to_string(index=False))
g = df[(df.資料 == "BTC") & (df.方式 != "A 基準")].groupby(["方式", "ST參數"]).IS.mean().unstack()
print("\nBTC 樣本內 Sharpe（只用這張挑參數）：\n", g.round(2).to_string())
df.to_csv("supertrend_results.csv", index=False)
