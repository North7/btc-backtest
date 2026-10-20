import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from backtest import *
k = load(); START, SPLIT = "2019-01-01", "2024-01-01"
IS_END = "2023-12-31"   # 樣本內到 2023-12-31 為止；字串切片含端點，寫成 [START:IS_END] 會把 2024-01-01 同時算進樣本內外
sh = lambda r: r.mean() / r.std() * np.sqrt(BARS_PER_YEAR)
cg = lambda r: (1 + r).prod() ** (BARS_PER_YEAR / len(r)) - 1

print("== S1 只做多：參數網格（Sharpe，樣本內 / 樣本外）==")
rows = []
for n in [10, 20, 30, 40, 55, 80]:
    row = {"N": n}
    for m in [2.0, 3.0, 4.0, 5.0]:
        r, _, _ = donchian(k, n, m)
        row[f"k={m:g}"] = f"{sh(r[START:IS_END]):.2f} / {sh(r[SPLIT:]):.2f}"
    rows.append(row)
print(pd.DataFrame(rows).to_string(index=False))

print("\n== S2 只做多：回看天數（Sharpe / CAGR，樣本內 → 樣本外）==")
for lb in [10, 20, 30, 45, 60, 90, 120]:
    r, _, _ = tsmom(k, lb)
    print(f"{lb:4d}天  IS {sh(r[START:IS_END]):.2f} / {cg(r[START:IS_END]):+.0%}   OOS {sh(r[SPLIT:]):.2f} / {cg(r[SPLIT:]):+.0%}")

print("\n== S1 vs 隨機進場（相同 ATR 停損、相同進場次數，300 次）==")
r0, p0, tr0 = donchian(k, 20, 3.0)
n_entries = len(tr0)
rng = np.random.default_rng(7)
idx = np.arange(len(k)); valid = idx > 1300
res = []
for _ in range(300):
    mask = np.zeros(len(k), bool)
    mask[rng.choice(idx[valid], size=n_entries * 4, replace=False)] = True  # 持倉中會略過，所以多撒一些
    r, p, tr = donchian(k, 20, 3.0, entry_mask=mask)
    res.append((sh(r[START:]), cg(r[START:]), (p[START:] != 0).mean()))
res = np.array(res)
print(f"策略 Sharpe {sh(r0[START:]):.2f}  CAGR {cg(r0[START:]):+.1%}  在場 {(p0[START:]!=0).mean():.0%}")
print(f"隨機 Sharpe 中位數 {np.median(res[:,0]):.2f}  95百分位 {np.percentile(res[:,0],95):.2f}  在場中位 {np.median(res[:,2]):.0%}")
print(f"策略勝過 {np.mean(res[:,0] < sh(r0[START:])):.0%} 的隨機版本")

print("\n== S3 費率套利（常開）逐年 ==")
r, _, _ = carry(k, "always")
print((r["2020":].groupby(r["2020":].index.year).sum() * 100).round(1).to_string())
f = k.funding[k.index.hour % 8 == 0]["2020":]
print("資金費為負的比例：", f"{(f<0).mean():.0%}")
print("近 12 個月年化：", f"{r[-6*365:].sum():.1%}")
