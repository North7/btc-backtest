"""
BTC 策略回測引擎（4H 級別）
- 訊號在 K 線收盤時計算，下一根 K 線開盤成交（避免偷看未來）
- 成本：每邊 0.05% 手續費 + 0.02% 滑點 = 0.07%
- 方向性策略以永續合約 1 倍名目計算，並扣/收實際資金費率（2020 前以 0.01%/8h 代入）
"""
import numpy as np, pandas as pd

COST = 0.0007          # 每邊成本
BARS_PER_YEAR = 6 * 365

def load(kline_path="data/btc_4h.csv", funding_path="data/funding.csv"):
    """預設讀 BTC；其他幣種傳入路徑，例如 load("data/eth_4h.csv", "data/eth_funding.csv")"""
    k = pd.read_csv(kline_path, index_col=0, parse_dates=True)
    f = pd.read_csv(funding_path, index_col=0)
    f.index = pd.to_datetime(f.index, utc=True, format="mixed").floor("h")
    f = f["funding"].groupby(level=0).last()
    # 對齊到 4H K 線：資金費在 00/08/16 UTC 結算，落在該時間開盤的 K 線
    fund = f.reindex(k.index).fillna(0.0)
    pre = k.index < f.index[0]
    fund[pre & (k.index.hour % 8 == 0)] = 0.0001
    k["funding"] = fund
    return k

def atr(k, n=14):
    pc = k.close.shift()
    tr = pd.concat([k.high - k.low, (k.high - pc).abs(), (k.low - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

def daily_filter(k, n=200):
    """日線收盤 vs 200 日均線；只用已收完的日線（往後移一天），避免偷看未來"""
    d = k.close.resample("1D").last()
    above = (d > d.rolling(n).mean()).shift(1)
    below = (d < d.rolling(n).mean()).shift(1)
    return (above.reindex(k.index, method="ffill").fillna(False).astype(bool),
            below.reindex(k.index, method="ffill").fillna(False).astype(bool))

def sma4h_filter(k, n=1200):
    """4H 收盤 vs 4H 的 n 根均線（1200 根 ≈ 200 天）；第 t 根收盤即已知，沒有日線重取樣的延遲"""
    m = k.close.rolling(n).mean()
    return (k.close > m).fillna(False), (k.close < m).fillna(False)

# ---------------------------------------------------------------- 策略 1
def donchian(k, n=20, mult=3.0, allow_short=False, use_filter=True, entry_mask=None, entry_ok=None,
             trend="daily200", ratchet=None, trade_from=None, short_ok=None, short_mask=None):
    """通道突破 + ATR 移動停損。entry_mask 給隨機進場測試用；entry_ok 為額外的多單進場濾網（布林陣列，t 收盤時已知）。
    trend：'daily200'（日線 200MA，延後一天）或 'sma1200'（4H 1200 根均線）。
    ratchet=(觸發倍數, 收緊倍數)：浮盈達「觸發倍數 × 進場時 ATR」後，移動停損改用收緊倍數。
    trade_from：只記錄在此時間（含）之後進場的交易；期末未平倉的交易以最後收盤價計入。
    short_ok：空單的額外進場濾網（同 entry_ok）；short_mask：隨機空單進場測試用（只開空、不開多）。"""
    o, h, l, c, fu = (k[x].values for x in ["open", "high", "low", "close", "funding"])
    a = atr(k).values
    hi = k.high.rolling(n).max().shift(1).values
    lo = k.low.rolling(n).min().shift(1).values
    up_ok, dn_ok = sma4h_filter(k) if trend == "sma1200" else daily_filter(k)
    up_ok, dn_ok = up_ok.values.copy(), dn_ok.values.copy()
    if not use_filter:
        up_ok[:] = True; dn_ok[:] = True
    rec_from = 0 if trade_from is None else k.index.searchsorted(pd.Timestamp(trade_from, tz="UTC"))
    N = len(c)
    ret = np.zeros(N); pos_arr = np.zeros(N)
    pos, stop, best, pending, entry_px, entry_atr, entry_t = 0, np.nan, np.nan, 0, np.nan, np.nan, 0
    trades = []
    def record(x):
        if entry_t >= rec_from: trades.append(x)
    for t in range(1, N):
        # 1) 執行上一根收盤產生的進場/反手（在本根開盤成交）
        entered = False
        if pending != 0 and pending != pos:
            if pos != 0:  # 反手：先結算舊部位從上根收盤到本根開盤
                ret[t] += pos * (o[t] / c[t - 1] - 1) - COST
                record(pos * (o[t] / entry_px - 1) - 2 * COST)
            pos = pending; entry_px = o[t]; ret[t] -= COST; entered = True
            best = o[t]; entry_atr = a[t - 1]; entry_t = t
            stop = o[t] - pos * mult * a[t - 1]
        pending = 0
        # 2) 本根損益
        ref = o[t] if entered else c[t - 1]
        if pos != 0:
            if not entered:
                ret[t] -= pos * fu[t]                            # 多單付正資金費
            hit = (pos == 1 and l[t] <= stop) or (pos == -1 and h[t] >= stop)
            if hit:
                fill = min(o[t], stop) if pos == 1 else max(o[t], stop)
                if entered: fill = stop if (pos == 1 and stop < o[t]) or (pos == -1 and stop > o[t]) else fill
                ret[t] += pos * (fill / ref - 1) - COST
                record(pos * (fill / entry_px - 1) - 2 * COST)
                pos = 0; stop = np.nan
            else:
                ret[t] += pos * (c[t] / ref - 1)
                best = max(best, c[t]) if pos == 1 else min(best, c[t])
                m = mult
                if ratchet is not None and pos * (best - entry_px) >= ratchet[0] * entry_atr:
                    m = ratchet[1]
                new = best - pos * m * a[t]
                stop = max(stop, new) if pos == 1 else min(stop, new)
        pos_arr[t] = pos
        # 3) 收盤判斷下一根的訊號
        if np.isnan(hi[t]) or np.isnan(a[t]):
            continue
        if entry_mask is not None:
            if pos == 0 and entry_mask[t]: pending = 1
            continue
        if short_mask is not None:
            if pos == 0 and short_mask[t]: pending = -1
            continue
        if c[t] > hi[t] and up_ok[t] and pos != 1 and (entry_ok is None or entry_ok[t]):
            pending = 1
        elif allow_short and c[t] < lo[t] and dn_ok[t] and pos != -1 and (short_ok is None or short_ok[t]):
            pending = -1
    if pos != 0:                                                 # 期末未平倉：以最後收盤價計（不扣出場成本）
        record(pos * (c[-1] / entry_px - 1) - COST)
    return pd.Series(ret, k.index), pd.Series(pos_arr, k.index), np.array(trades)

# ---------------------------------------------------------------- 策略 2
def tsmom(k, lookback=30, allow_short=False):
    """每日 UTC 00:00 檢查過去 lookback 天報酬，>0 持多，否則空手（或做空）"""
    d = k.close.resample("1D").last()
    sig = np.sign(d / d.shift(lookback) - 1)
    if not allow_short:
        sig = sig.clip(lower=0)
    # 注意：resample 的日線標籤是當天 00:00，但值是當天 24:00 的收盤 → 必須先往後移 1 天
    known = sig.shift(1)                             # 標籤 D 的值 = D-1 日收盤後才知道的訊號
    target = known.reindex(k.index, method="ffill").fillna(0)
    pos = target.shift(1).fillna(0)                  # 保守：再晚一根 4H（次日 04:00 開盤）才成交
    r = k.close.pct_change().fillna(0)
    gross = pos * r - pos * k.funding
    cost = pos.diff().abs().fillna(0) * COST
    ret = gross - cost
    # 逐筆交易
    trades, cur, ent = [], 0, None
    eq = (1 + ret).cumprod()
    for t, p in pos.items():
        if p != cur:
            if cur != 0: trades.append(eq[t] / ent - 1)
            cur, ent = p, eq[t]
    return ret, pos, np.array(trades)

# ---------------------------------------------------------------- 策略 3
def carry(k, mode="always", thresh=0.00005, window=21):
    """現貨多 + 永續空，收資金費。mode='filter' 時只在近 window 次平均費率 > thresh 才開。
    報酬以名目本金計（假設可用現貨當保證金的統一帳戶）。開/平各 2 邊。"""
    f = k.funding
    events = f[k.index.hour % 8 == 0]
    if mode == "always":
        on = pd.Series(1.0, index=events.index)
    else:
        on = (events.rolling(window).mean().shift(1) > thresh).astype(float)
    on = on.reindex(k.index, method="ffill").fillna(0)
    ret = on * f - on.diff().abs().fillna(0) * 2 * COST
    return ret, on, None

# ---------------------------------------------------------------- 指標
def stats(ret, pos=None, trades=None, bench=None):
    eq = (1 + ret).cumprod()
    yrs = len(ret) / BARS_PER_YEAR
    cagr = eq.iloc[-1] ** (1 / yrs) - 1
    vol = ret.std() * np.sqrt(BARS_PER_YEAR)
    sharpe = ret.mean() / ret.std() * np.sqrt(BARS_PER_YEAR) if ret.std() > 0 else np.nan
    dd = (eq / eq.cummax() - 1).min()
    out = {"CAGR": cagr, "Vol": vol, "Sharpe": sharpe, "MaxDD": dd,
           "Calmar": cagr / abs(dd) if dd < 0 else np.nan, "總報酬": eq.iloc[-1] - 1}
    if pos is not None:
        out["在場時間"] = (pos != 0).mean()
    if trades is not None and len(trades):
        w, ls = trades[trades > 0], trades[trades <= 0]
        out.update({"交易數": len(trades), "勝率": len(w) / len(trades),
                    "盈虧因子": w.sum() / abs(ls.sum()) if len(ls) else np.nan})
    if bench is not None:
        out["與BTC相關"] = np.corrcoef(ret, bench)[0, 1]
    return out
