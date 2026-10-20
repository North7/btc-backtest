"""
手動操作版 S1：只在「醒著能操作」的時間進場與上調停損，比較和全自動（每 4 小時）差多少。
- 停損單假設已掛在交易所（觸價自動出場），所以出場不受作息影響
- 睡覺時出現的進場訊號，兩種規則都測：
  「保留」= 延到下一個能操作的時間，以開盤價進場（期間若已跌回通道內也照樣進）
  「重新確認」= 能操作時只看最近一根收盤：仍突破（且濾網成立）才進場，否則放棄
- 移動停損：只在能操作的 K 線收盤才往上調，其餘時間維持舊停損
資料：OKX 永續 K 線 + 資金費率（實盤標的）；寬度濾網 ≤0.5、波動目標 40%/1.5x 與 filters.py 相同
執行：python manual_exec.py
"""
import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from backtest import *

k = load("data/okx_btc_swap_4h.csv", "data/okx_funding_spliced.csv")
A, SPLIT = "2021-01-01", "2024-01-01"
IS_END = "2023-12-31"   # 樣本內到 2023-12-31 為止；字串切片含端點，寫成 [START:SPLIT] 會把 2024-01-01 同時算進樣本內外
sh = lambda r: r.mean() / r.std() * np.sqrt(BARS_PER_YEAR)
cg = lambda r: (1 + r).prod() ** (BARS_PER_YEAR / len(r)) - 1
mdd = lambda r: ((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min()

def width_ok(n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    wr = ((hi - lo) / k.close.shift(1)).rolling(6 * 180).rank(pct=True)
    return (wr <= 0.5).values

def vol_target(ret, pos, target=0.4, cap=1.5):
    rv = k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)
    size_sig = (target / rv).clip(upper=cap).shift(1)
    active = (pos != 0) | (ret != 0)
    entry = active & (pos.shift(1, fill_value=0) == 0)
    return ret * size_sig.where(entry).ffill().where(active, 0.0).fillna(0.0)

def donchian_manual(n, mult, entry_ok, act_local_hours, recheck=False):
    """act_local_hours：台灣時間（UTC+8）哪些整點能操作；K 線開盤在這些時間才能成交、收盤在這些時間才能調停損"""
    o, h, l, c, fu = (k[x].values for x in ["open", "high", "low", "close", "funding"])
    a = atr(k).values
    hi = k.high.rolling(n).max().shift(1).values
    up_ok = daily_filter(k)[0].values
    local_open = ((k.index.hour + 8) % 24).values
    can_fill = np.isin(local_open, act_local_hours)
    can_upd = np.roll(can_fill, -1)                 # 第 t 根收盤 = 第 t+1 根開盤
    N = len(c); ret = np.zeros(N); pos_arr = np.zeros(N)
    pos, stop, best, pending = 0, np.nan, np.nan, 0
    sig = np.zeros(N, bool)
    for t in range(1, N):
        entered = False
        if pending == 1 and pos == 0 and can_fill[t] and (not recheck or sig[t - 1]):
            pos = 1; ret[t] -= COST; entered = True; best = o[t]
            stop = o[t] - mult * a[t - 1]; pending = 0
        ref = o[t] if entered else c[t - 1]
        if pos:
            if not entered: ret[t] -= fu[t]
            if l[t] <= stop:
                fill = stop if entered and stop < o[t] else min(o[t], stop)
                ret[t] += fill / ref - 1 - COST; pos = 0; stop = np.nan
            else:
                ret[t] += c[t] / ref - 1
                best = max(best, c[t])
                if can_upd[t]: stop = max(stop, best - mult * a[t])
        pos_arr[t] = pos
        if np.isnan(hi[t]) or np.isnan(a[t]): continue
        sig[t] = c[t] > hi[t] and up_ok[t] and entry_ok[t]
        if pos == 0 and sig[t]:
            pending = 1
    return pd.Series(ret, k.index), pd.Series(pos_arr, k.index)

SCHED = {"全自動（每 4 小時）": [0, 4, 8, 12, 16, 20],
         "睡 0–8 點（可操作 8/12/16/20/24 點）": [8, 12, 16, 20, 0],
         "一天兩次（8 點、20 點）": [8, 20],
         "一天一次（20 點）": [20]}
rows = []
for n in [20, 30]:
    ok = width_ok(n)
    for lab, hrs in SCHED.items():
      for rule, rc in [("保留", False), ("重新確認", True)]:
        if rc and len(hrs) == 6: continue                 # 全自動時兩種規則相同
        r, p = donchian_manual(n, 3.0, ok, hrs, recheck=rc)
        v = vol_target(r, p)
        rows.append({"N": n, "作息": lab, "睡覺時的訊號": rule if len(hrs) < 6 else "-", "IS_Sharpe 21–23": round(sh(v[A:IS_END]), 2),
                     "OOS_Sharpe 24–26": round(sh(v[SPLIT:]), 2), "CAGR%": round(cg(v[A:]) * 100, 1),
                     "MaxDD%": round(mdd(v[A:]) * 100, 1)})
pd.set_option("display.width", 200)
print("== OKX 永續、寬度≤0.5＋波動目標，2021–2026/9 ==")
print(pd.DataFrame(rows).to_string(index=False))
# 自檢：全自動版應與 backtest.donchian 一致
r0, p0, _ = donchian(k, 20, 3.0, entry_ok=width_ok(20))
r1, _ = donchian_manual(20, 3.0, width_ok(20), SCHED["全自動（每 4 小時）"])
r2, _ = donchian_manual(20, 3.0, width_ok(20), SCHED["全自動（每 4 小時）"], recheck=True)
print(f"\n自檢：全自動版與原引擎全期 Sharpe {sh(r1[A:]):.3f} / 重新確認 {sh(r2[A:]):.3f} vs {sh(r0[A:]):.3f}")
