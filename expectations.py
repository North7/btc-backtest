"""
預期行為範圍（2026-10-22）：直接呼叫紙上交易引擎（paper/engine.py）的 s1_run、d_run，同一套規則與成本，
在 OKX 現貨 4H K 線上從 2019 年起重跑，算出「照規則做時，正常會看到什麼」，作為 RULES.md 的數字依據。
A) 逐筆交易：每年筆數、勝率、盈虧、連續虧損筆數（歷史＋打亂交易順序抽樣），以及要多少筆才能確認優勢
B) 淨值路徑：最大回撤、水下時間、虧損月份、最差年份
C) 滾動視窗（任一天開始）：3/6/12 個月報酬、1/3 年內最大回撤；組合照紙上帳戶方式，視窗內不再平衡
D) 區塊自助抽樣（日報酬、平均區塊 60 天，三個子帳戶同一天一起抽）：未來 1、3 年最大回撤與報酬分布 → 停用門檻
E) 紙上交易 3 個月看得到多少：完整交易數、各類操作指令數
F) 成本敏感度：每邊成本（手續費＋滑價）提高時 S1 還剩多少
執行：.venv/bin/python expectations.py [抽樣次數，預設 2000]
"""
import warnings; warnings.filterwarnings("ignore")
import sys
from pathlib import Path
import numpy as np, pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent / "paper"))
import engine as ENG

START, SPLIT = pd.Timestamp("2019-01-01", tz="UTC"), pd.Timestamp("2024-01-01", tz="UTC")
N_SIMS = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
BLOCK = 60
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
pct = lambda x: f"{x * 100:+.1f}%"

k = ENG.load_k()
S = {n: ENG.s1_run(k, n) for n in ENG.NS}
D = ENG.d_run(k)
YEARS = (k.index[-1] - START).days / 365.25
print(f"資料：OKX BTC-USDT 現貨 4H，{START.date()} ～ {k.index[-1]}（{YEARS:.1f} 年）；S1 成本 {ENG.S1_COST:.2%}/邊、上限 {ENG.S1_CAP:g} 倍；D {ENG.D_COST:.2%}/邊")

def day_eq(r4):
    """4H 報酬 → 日收盤淨值（START 前一刻 = 1）"""
    r4 = r4[r4.index >= START]
    eq = (1 + r4).cumprod().resample("1D").last()
    return pd.concat([pd.Series([1.0], [START - pd.Timedelta(days=1)]), eq])

EQ = pd.DataFrame({f"N{n}": day_eq(S[n]["ret"] * S[n]["size"]) for n in ENG.NS})
dv = D["value"].resample("1D").last()
EQ["D"] = dv[dv.index >= START - pd.Timedelta(days=1)] / dv[dv.index >= START - pd.Timedelta(days=1)].iloc[0]
EQ = EQ.ffill()
COMBOS = {"S1": {"N20": .5, "N30": .5}, "D": {"D": 1.0}, "合計": {"N20": .25, "N30": .25, "D": .5}}
W = {c: np.array([w.get(x, 0.0) for x in EQ.columns]) for c, w in COMBOS.items()}

# ---------------------------------------------------------------- A) 逐筆交易
def trades_of(n):
    s = S[n]; rs = (s["ret"] * s["size"]).values; out = []
    for tr in s["trades"]:
        if k.index[tr["entry_i"]] < START: continue
        ei, xi = tr["entry_i"], tr["exit_i"]
        out.append({"entry": k.index[ei], "exit": k.index[xi], "ret": float(np.prod(1 + rs[ei:xi + 1]) - 1),
                    "px": tr["exit_px"] / tr["entry_px"] - 1, "size": tr["size"], "hold_d": (xi - ei + 1) / 6})
    return pd.DataFrame(out)
TR = {n: trades_of(n) for n in ENG.NS}

def runs(mask):
    """連續 True 的長度與起訖位置"""
    out, i = [], 0
    while i < len(mask):
        if mask[i]:
            j = i
            while j < len(mask) and mask[j]: j += 1
            out.append((j - i, i, j)); i = j
        else:
            i += 1
    return out

def max_streak_batch(loss):
    """loss：(模擬次數, 筆數) 布林 → 每列最長連續 True"""
    best = np.zeros(len(loss), int); cur = np.zeros(len(loss), int)
    for j in range(loss.shape[1]):
        cur = np.where(loss[:, j], cur + 1, 0); best = np.maximum(best, cur)
    return best

print("\n== A) 逐筆交易（帳戶層級報酬，已含倉位倍數與成本）==")
rng = np.random.default_rng(7)
rows, streak_rows = [], []
for n, t in TR.items():
    r = t.ret.values; w, ls = r[r > 0], r[r <= 0]
    per_yr = len(r) / YEARS
    n95 = (1.645 * r.std() / r.mean()) ** 2 if r.mean() > 0 else np.nan
    rows.append({"子帳戶": f"N{n}", "筆數": len(r), "每年": round(per_yr, 1), "勝率": f"{len(w) / len(r):.0%}",
                 "平均獲利": pct(w.mean()), "平均虧損": pct(ls.mean()), "盈虧比": round(w.mean() / -ls.mean(), 2),
                 "最好": pct(r.max()), "最差": pct(r.min()), "每筆期望": pct(r.mean()), "每筆標準差": pct(r.std()),
                 "持有中位天數": round(t.hold_d.median(), 1),
                 "確認優勢需筆數": int(np.ceil(n95)), "≈年": round(n95 / per_yr, 1)})
    rl = runs(r <= 0)
    worst_run = min(np.prod(1 + r[i:j]) - 1 for _, i, j in rl)
    lens = np.array([x[0] for x in rl])
    m1, m3 = int(round(per_yr)), int(round(3 * per_yr))
    sims1 = max_streak_batch(rng.choice(r, (20000, m1)) <= 0)
    sims3 = max_streak_batch(rng.choice(r, (20000, m3)) <= 0)
    longest = max(rl, key=lambda x: x[0])
    streak_rows.append({"子帳戶": f"N{n}", "歷史最長連虧": int(lens.max()),
                        "發生於": f"{t.entry.iloc[longest[1]].date()}～{t.exit.iloc[longest[2] - 1].date()}",
                        "連虧≥5 次數": int((lens >= 5).sum()), "連虧期間最大累計虧損": pct(worst_run),
                        "1 年筆數": m1, "1 年中位": int(np.median(sims1)), "1 年 95%": int(np.percentile(sims1, 95)),
                        "1 年 99%": int(np.percentile(sims1, 99)),
                        "3 年筆數": m3, "3 年中位": int(np.median(sims3)), "3 年 95%": int(np.percentile(sims3, 95)),
                        "3 年 99%": int(np.percentile(sims3, 99))})
print(pd.DataFrame(rows).to_string(index=False))
print("（確認優勢需筆數：每筆期望 > 0 的單邊 95% 檢定，(1.645 × 標準差 / 期望)²）")
print("\n連續虧損筆數（打亂交易順序抽樣 20000 次）：")
print(pd.DataFrame(streak_rows).to_string(index=False))
both = pd.merge(TR[20][["entry", "ret"]], TR[30][["entry", "ret"]], on="entry", how="outer", suffixes=("20", "30"))
same = both.dropna()
print(f"N20、N30 同一根進場：{len(same)} 次（占所有進場 {len(same) / len(both):.0%}），其中兩邊同賺或同賠 "
      f"{((same.ret20 > 0) == (same.ret30 > 0)).mean():.0%}")

# ---------------------------------------------------------------- B) 淨值路徑
def path_of(c):
    A = EQ.values @ W[c] if c != "合計" else None
    if c == "合計":   # 全期路徑用每日再平衡近似（不再平衡的話到期末 D 會佔七成）；視窗統計（C、D）才用不再平衡的精確版
        rS = pd.Series(EQ.values @ W["S1"], EQ.index).pct_change().fillna(0); rD = EQ.D.pct_change().fillna(0)
        return (1 + 0.5 * rS + 0.5 * rD).cumprod()
    return pd.Series(A, EQ.index)

def underwater(eq):
    """每段水下：從前高那天到收復前高那天（含尚未收復的最後一段）"""
    peak_val, peak_t, spells = eq.iloc[0], eq.index[0], []
    trough = 0.0
    for t, v in eq.items():
        if v >= peak_val:
            if t != peak_t and trough < 0: spells.append(((t - peak_t).days, trough, peak_t, True))
            peak_val, peak_t, trough = v, t, 0.0
        else:
            trough = min(trough, v / peak_val - 1)
    if trough < 0: spells.append(((eq.index[-1] - peak_t).days, trough, peak_t, False))
    return spells

print("\n== B) 淨值路徑（2019 起；合計為每日再平衡近似）==")
rows = []
for c in COMBOS:
    eq = path_of(c); r = eq.pct_change().dropna()
    dd = eq / eq.cummax() - 1
    sp = underwater(eq); lens = np.array([s[0] for s in sp])
    big = [s for s in sp if s[1] <= -0.10]
    mo = eq.resample("ME").last().pct_change().dropna()
    neg_run = max([x[0] for x in runs((mo < 0).values)] or [0])
    yr = eq.resample("YE").last().pct_change().dropna()           # 起點是 2018-12-31 = 1，所以從 2019 起
    cur = sp[-1] if sp and not sp[-1][3] else None
    rows.append({"策略": c, "CAGR": pct(eq.iloc[-1] ** (365.25 / (eq.index[-1] - eq.index[0]).days) - 1),
                 "Sharpe": round(r.mean() / r.std() * np.sqrt(365), 2),
                 "最大回撤": pct(dd.min()), "谷底": dd.idxmin().date(),
                 "最長水下天數": int(lens.max()), "起於": max(sp, key=lambda s: s[0])[2].date(),
                 "回撤≥10% 次數": len(big), "回撤>5% 時間占比": f"{(dd < -0.05).mean():.0%}",
                 "前三長水下（天、最深）": "；".join(f"{s[2].date()} 起 {s[0]}、{pct(s[1])}{'' if s[3] else '（未收復）'}"
                                            for s in sorted(sp, key=lambda s: -s[0])[:3]),
                 "虧損月份": f"{(mo < 0).mean():.0%}", "最長連續虧損月": neg_run, "最差月": pct(mo.min()),
                 "最差年": f"{pct(yr.min())}（{yr.idxmin().year}）",
                 "目前": f"水下 {cur[0]} 天、{pct(dd.iloc[-1])}" if cur else "在高點"})
print(pd.DataFrame(rows).T.to_string(header=False))
print("\n逐年報酬：")
print(pd.DataFrame({c: path_of(c).resample("YE").last().pct_change().dropna().rename(lambda t: t.year).map(pct)
                    for c in COMBOS}).T.to_string())
print(f"（{k.index[-1].year} 為年初至今）")

# ---------------------------------------------------------------- C) 滾動視窗
A = EQ.values
def windows(c, h):
    """每一天開始、持有 h 天：期末報酬與期間最大回撤（視窗內不再平衡，與紙上帳戶相同）"""
    rets, mdds = [], []
    for i in range(len(A) - h):
        v = (A[i:i + h + 1] / A[i]) @ W[c]
        rets.append(v[-1] - 1); mdds.append((v / np.maximum.accumulate(v) - 1).min())
    return np.array(rets), np.array(mdds)

print("\n== C) 滾動視窗：從任一天開始（重疊視窗，獨立樣本其實只有幾段）==")
rows, mrows = [], []
for c in COMBOS:
    for h, name in [(91, "3 個月"), (182, "6 個月"), (365, "12 個月")]:
        r, m = windows(c, h)
        rows.append({"策略": c, "期間": name, "最差": pct(r.min()), "5%": pct(np.percentile(r, 5)),
                     "10%": pct(np.percentile(r, 10)), "中位": pct(np.median(r)), "90%": pct(np.percentile(r, 90)),
                     "虧損機率": f"{(r < 0).mean():.0%}", "期間內回撤 中位": pct(np.median(m)),
                     "回撤 95%": pct(np.percentile(m, 5)), "回撤 最差": pct(m.min())})
    r, m = windows(c, 1095)
    mrows.append({"策略": c, "3 年內最大回撤 中位": pct(np.median(m)), "90%": pct(np.percentile(m, 10)),
                  "最差": pct(m.min()), "3 年報酬 最差": pct(r.min()), "3 年虧損機率": f"{(r < 0).mean():.0%}"})
print(pd.DataFrame(rows).to_string(index=False))
print(pd.DataFrame(mrows).to_string(index=False))

# ---------------------------------------------------------------- D) 區塊自助抽樣
R = EQ.pct_change().dropna().values
def boot(h, sims, rng):
    T = len(R); idx = np.empty((sims, h), int)
    for s in range(sims):
        new = rng.random(h) < 1 / BLOCK; new[0] = True
        blk = np.cumsum(new) - 1
        starts = rng.integers(T, size=blk[-1] + 1)
        pos = np.arange(h) - np.flatnonzero(new)[blk]
        idx[s] = (starts[blk] + pos) % T
    return idx

print(f"\n== D) 區塊自助抽樣（{N_SIMS} 次、平均區塊 {BLOCK} 天）：未來的分布 ==")
rng = np.random.default_rng(11)
rows = []
for h, name in [(365, "1 年"), (1095, "3 年")]:
    idx = boot(h, N_SIMS, rng)
    path = np.cumprod(1 + R[idx], axis=1)                       # (模擬, 天, 子帳戶)
    for c in COMBOS:
        v = path @ W[c]
        mdd = (v / np.maximum.accumulate(np.maximum(v, 1.0), axis=1) - 1).min(axis=1)
        mdd = np.minimum(mdd, 0)
        rows.append({"策略": c, "期間": name, "報酬 1%": pct(np.percentile(v[:, -1] - 1, 1)),
                     "報酬 5%": pct(np.percentile(v[:, -1] - 1, 5)), "報酬 中位": pct(np.median(v[:, -1] - 1)),
                     "虧損機率": f"{(v[:, -1] < 1).mean():.0%}",
                     "最大回撤 中位": pct(np.median(mdd)), "90%": pct(np.percentile(mdd, 10)),
                     "95%": pct(np.percentile(mdd, 5)), "99%": pct(np.percentile(mdd, 1))})
print(pd.DataFrame(rows).to_string(index=False))

# ---------------------------------------------------------------- E) 紙上交易 3 個月看得到多少
print("\n== E) 從任一天開始 3 個月（91 天）內會發生多少事 ==")
days = EQ.index[(EQ.index >= START) & (EQ.index <= EQ.index[-1] - pd.Timedelta(days=91))]
def count_in(ts, h=91):
    ts = np.sort(np.array(ts, dtype="datetime64[ns]"))
    d0 = days.values.astype("datetime64[ns]")
    return np.searchsorted(ts, d0 + np.timedelta64(h, "D")) - np.searchsorted(ts, d0)
ev = {"N20 完整交易": TR[20].exit, "N30 完整交易": TR[30].exit,
      "進場指令（N20/N30 同根算一次）": pd.concat([TR[20].entry, TR[30].entry]).drop_duplicates(),
      "停損出場（同上）": pd.concat([TR[20].exit, TR[30].exit]).drop_duplicates()}
raises = set()
for n in ENG.NS:
    for tr in S[n]["trades"] + ([S[n]["open"]] if S[n]["open"] else []):
        for i, _ in tr["stops"][1:]:
            if k.index[i - 1] >= START: raises.add(k.index[i - 1])
ev["上調停損（同上）"] = pd.Series(sorted(raises))
ev["D 調倉"] = pd.Series([o["exec_t"] for o in D["orders"] if not o["pending"] and o["exec_t"] >= START])
rows = []
for name, ts in ev.items():
    c = count_in(pd.to_datetime(ts).dt.tz_convert(None) if hasattr(ts, "dt") else ts)
    rows.append({"事件": name, "中位": int(np.median(c)), "10%": int(np.percentile(c, 10)), "90%": int(np.percentile(c, 90)),
                 "最少": int(c.min()), "最多": int(c.max()), "完全沒有的機率": f"{(c == 0).mean():.0%}"})
print(pd.DataFrame(rows).to_string(index=False))
uniq = pd.concat([TR[20], TR[30]]).drop_duplicates(subset="entry").exit      # 同一根進場的 N20、N30 是完全相同的交易，只算一次
for label, ex in [("N20", TR[20].exit), ("N20＋N30，同一根進場算一筆", uniq), ("N20＋N30 各算", pd.concat([TR[20].exit, TR[30].exit]))]:
    ex = np.sort(pd.to_datetime(ex).dt.tz_convert(None).values)
    d0 = days.values.astype("datetime64[ns]")
    waits = []
    for d in d0:
        j = np.searchsorted(ex, d)
        if j + 4 < len(ex): waits.append((ex[j + 4] - d) / np.timedelta64(1, "D"))
    waits = np.array(waits)
    print(f"累積 5 筆完整交易要多久（{label}）：中位 {np.median(waits):.0f} 天、75% {np.percentile(waits, 75):.0f} 天、"
          f"90% {np.percentile(waits, 90):.0f} 天、最久 {waits.max():.0f} 天")

# ---------------------------------------------------------------- F) 成本敏感度
print("\n== F) 成本敏感度：S1（N20+N30）每邊成本提高時（2019 起；樣本外 2024 起）==")
rows, base = [], ENG.S1_COST
for cost in [0.0008, 0.0012, 0.0020, 0.0030, 0.0050]:
    ENG.S1_COST = cost
    eq = sum(0.5 * day_eq(x["ret"] * x["size"]) for x in (ENG.s1_run(k, n) for n in ENG.NS))
    r = eq.pct_change().dropna(); ro = r[r.index >= SPLIT]
    rows.append({"每邊成本": f"{cost:.2%}", "相當於滑價": f"{cost - 0.0010:+.2%}", "Sharpe": round(r.mean() / r.std() * np.sqrt(365), 2),
                 "樣本外 Sharpe": round(ro.mean() / ro.std() * np.sqrt(365), 2),
                 "CAGR": pct(eq.iloc[-1] ** (365.25 / (eq.index[-1] - eq.index[0]).days) - 1),
                 "最大回撤": pct((eq / eq.cummax() - 1).min())})
ENG.S1_COST = base
print(pd.DataFrame(rows).to_string(index=False))
print("（手續費以一般用戶 taker 0.10% 計；「相當於滑價」= 每邊成本 − 0.10%）")
