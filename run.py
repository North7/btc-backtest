import warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from backtest import *

k = load()
START, SPLIT = "2019-01-01", "2024-01-01"
bench_full = k.close.pct_change().fillna(0)

def evaluate(name, res):
    ret, pos, tr = res
    rows = []
    for label, a, b in [("全期", START, None), ("樣本內 2019–23", START, "2023-12-31"), ("樣本外 2024–26/9", SPLIT, None)]:
        r = ret[a:b]; p = pos[a:b] if pos is not None else None
        rows.append({"策略": name, "區間": label, **stats(r, p, None, bench_full[a:b])})
    return rows, ret, tr

results, curves, trade_lists = [], {}, {}
def add(name, res):
    rows, ret, tr = evaluate(name, res)
    results.extend(rows); curves[name] = ret; trade_lists[name] = tr

bh = (bench_full, pd.Series(1.0, index=k.index), None)
add("買入持有", bh)
add("S1 突破 只做多 N20 k3", donchian(k, 20, 3.0))
add("S1 突破 多空 N20 k3", donchian(k, 20, 3.0, allow_short=True))
add("S1 突破 只做多 無濾網", donchian(k, 20, 3.0, use_filter=False))
add("S2 動量 30天 只做多", tsmom(k, 30))
add("S2 動量 30天 多空", tsmom(k, 30, allow_short=True))
add("S3 費率套利 常開", carry(k, "always"))
add("S3 費率套利 濾網", carry(k, "filter"))

df = pd.DataFrame(results)
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 20)
fmt = df.copy()
for c in ["CAGR", "Vol", "MaxDD", "總報酬", "在場時間"]:
    fmt[c] = (fmt[c] * 100).round(1)
for c in ["Sharpe", "Calmar", "與BTC相關"]:
    fmt[c] = fmt[c].round(2)
print(fmt.to_string(index=False))
df.to_csv("results_summary.csv", index=False)

# 逐筆交易統計（全期）
print("\n逐筆交易（2018 中起，含暖身期）")
for name, tr in trade_lists.items():
    if tr is None or not len(tr): continue
    w, l = tr[tr > 0], tr[tr <= 0]
    print(f"{name:24s} 交易數 {len(tr):4d}  勝率 {len(w)/len(tr):.0%}  均賺 {w.mean():+.1%}  均虧 {l.mean():+.1%}  盈虧因子 {w.sum()/abs(l.sum()):.2f}  最長連虧 {max((len(s) for s in ''.join('L' if x<=0 else 'W' for x in tr).split('W')), default=0)}")
pd.DataFrame({n: (1 + r[START:]).cumprod() for n, r in curves.items()}).to_csv("equity_curves.csv")
