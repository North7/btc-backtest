"""
紙上交易／手動實盤引擎。每根 4H K 線收盤後執行一次（台灣時間 00/04/08/12/16/20 點過 2 分）。
1. 從 OKX 補上最新的 BTC-USDT 現貨 4H K 線（只收已收盤的 K 線）
2. 從頭重跑兩個策略（規則與研究腳本完全相同，啟動時自檢）：
   S1：4H 通道突破只做多，N20 與 N30 兩個子帳戶各半；寬度百分位 ≤0.5、日線 200MA、3×ATR 移動停損、
       波動目標 40%（上限 1 倍，不加槓桿），現貨，扣 0.12%/邊（手續費 0.10%＋滑價 0.02%），無資金費
   D ：日線趨勢＋動能＋波動目標（strategy_summary.md），現貨帳本式、20% 調倉門檻、次日開盤成交、0.15%/邊
3. 產生逐筆交易、目前部位、下一步操作提示（可以直接照著在 OKX 下單）、資金與績效
4. 新的操作提示寫入 state/signals_log.csv，並發 macOS 通知；結果寫到 web/data.js 給網頁讀
用法：python paper/engine.py [--offline] [--no-notify]
設定：paper/config.json（mode: paper / live、capital_usdt、start、weights）
"""
import json, sys, time, subprocess, urllib.request, datetime as dt
from pathlib import Path
import numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parent
PROJ = ROOT.parent
sys.path.insert(0, str(PROJ))
import backtest as BT  # noqa: E402
from backtest import atr, daily_filter, donchian, BARS_PER_YEAR  # noqa: E402

DATA, STATE, WEB = ROOT / "data", ROOT / "state", ROOT / "web"
K_PATH = DATA / "btc_spot_4h.csv"
LOG_PATH, FILL_PATH = STATE / "signals_log.csv", STATE / "fills.csv"
INST, API = "BTC-USDT", "https://www.okx.com/api/v5"
UA = {"User-Agent": "Mozilla/5.0"}          # OKX 會擋 Python 預設 User-Agent
S1_COST = 0.0012                             # 現貨：手續費 0.10%＋滑價 0.02%（每邊）
S1_CAP = 1.0                                 # 現貨不加槓桿
QTY_STEP = 0.00001                           # OKX 現貨 BTC 最小下單量
D_COST = 0.0015
TW = dt.timezone(dt.timedelta(hours=8))
NS = [20, 30]

def qty_floor(x):
    return float(np.floor(x / QTY_STEP + 1e-9) * QTY_STEP)

def tw(ts):
    return pd.Timestamp(ts).tz_convert(TW).strftime("%m/%d %H:%M")

# ---------------------------------------------------------------- 資料更新
def http_json(url):
    err = None
    for i in range(4):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read())
        except Exception as e:  # 網路不穩時重試
            err = e; time.sleep(1 + i)
    raise RuntimeError(f"{url}: {err}")

HISTORY_FROM = "2018-01-01"

def update_candles():
    DATA.mkdir(parents=True, exist_ok=True)
    if not K_PATH.exists() and (PROJ / "data/okx_btc_4h.csv").exists():
        pd.read_csv(PROJ / "data/okx_btc_4h.csv").to_csv(K_PATH, index=False)
    if K_PATH.exists():
        k = pd.read_csv(K_PATH, index_col=0, parse_dates=True)
        last_ms = int(k.index[-1].timestamp() * 1000)
    else:                                                      # 沒有任何本機資料（例如 GitHub Actions）：從 OKX 抓完整歷史
        k = pd.DataFrame(columns=["open", "high", "low", "close", "volume"],
                         index=pd.DatetimeIndex([], tz="UTC", name="ts"))
        last_ms = int(pd.Timestamp(HISTORY_FROM, tz="UTC").timestamp() * 1000) - 1
    rows, after = [], ""
    while True:
        d = http_json(f"{API}/market/history-candles?instId={INST}&bar=4H&limit=100" + (f"&after={after}" if after else ""))["data"]
        if not d:
            break
        rows += d; after = d[-1][0]
        if int(after) <= last_ms:
            break
        time.sleep(0.15)
    new = [r for r in rows if r[8] == "1" and int(r[0]) > last_ms]
    if new:
        df = pd.DataFrame([[int(r[0])] + [float(x) for x in r[1:6]] for r in new],
                          columns=["ts", "open", "high", "low", "close", "volume"])
        df.index = pd.to_datetime(df.pop("ts"), unit="ms", utc=True); df.index.name = "ts"
        k = pd.concat([k, df]).sort_index() if len(k) else df.sort_index()
        k = k[~k.index.duplicated()]
        k.to_csv(K_PATH)
    return len(new)

def market_now():
    out = {}
    try:
        t = http_json(f"{API}/market/ticker?instId={INST}")["data"][0]
        out["price"] = float(t["last"]); out["price_ts"] = int(t["ts"]) // 1000
    except Exception as e:
        out["error"] = str(e)
    return out

def load_k():
    """現貨 K 線；沒有資金費（欄位補 0，讓研究引擎可以直接用）"""
    k = pd.read_csv(K_PATH, index_col=0, parse_dates=True)
    k["funding"] = 0.0
    return k

# ---------------------------------------------------------------- S1
def width_rank(k, n):
    hi = k.high.rolling(n).max().shift(1); lo = k.low.rolling(n).min().shift(1)
    return ((hi - lo) / k.close.shift(1)).rolling(6 * 180).rank(pct=True)

def realized_vol(k):
    return (k.close.pct_change().rolling(6 * 30).std() * np.sqrt(BARS_PER_YEAR)).values

def size_from_rv(rv):
    return float(min(S1_CAP, 0.4 / rv)) if rv and np.isfinite(rv) and rv > 0 else 0.0

def s1_run(k, n, mult=3.0, wth=0.5):
    """與 backtest.donchian(只做多, entry_ok=寬度) 逐根相同，另記錄每筆交易、停損路徑與倉位倍數"""
    o, h, l, c, fu = (k[x].values for x in ["open", "high", "low", "close", "funding"])
    a = atr(k).values
    hi = k.high.rolling(n).max().shift(1).values
    up_ok = daily_filter(k)[0].values
    ok = (width_rank(k, n) <= wth).values
    rv = realized_vol(k)
    N = len(c)
    ret, size_arr, stop_arr = np.zeros(N), np.zeros(N), np.full(N, np.nan)
    pos, stop, best, pending, cur, trades = 0, np.nan, np.nan, 0, None, []
    for t in range(1, N):
        entered, exited = False, False
        if pending == 1 and pos == 0:
            pos = 1; ret[t] -= S1_COST; entered = True; best = o[t]
            stop = o[t] - mult * a[t - 1]
            cur = {"entry_i": t, "entry_px": o[t], "size": size_from_rv(rv[t - 1]), "atr": a[t - 1],
                   "stops": [[t, stop]], "funding": 0.0}
        pending = 0
        ref = o[t] if entered else c[t - 1]
        if pos:
            if not entered:
                ret[t] -= fu[t]; cur["funding"] += fu[t]
            if l[t] <= stop:
                fill = stop if (entered and stop < o[t]) else min(o[t], stop)
                ret[t] += fill / ref - 1 - S1_COST
                cur.update(exit_i=t, exit_px=fill, reason="停損")
                trades.append(cur); size_arr[t] = cur["size"]; exited = True
                cur = None; pos = 0; stop = np.nan
            else:
                ret[t] += c[t] / ref - 1
                best = max(best, c[t])
                new = best - mult * a[t]
                if new > stop:
                    stop = new; cur["stops"].append([t + 1, stop])     # 收盤後上調，下一根起生效
        if pos:
            size_arr[t] = cur["size"]; stop_arr[t] = stop
        if np.isnan(hi[t]) or np.isnan(a[t]):
            continue
        if pos == 0 and c[t] > hi[t] and up_ok[t] and ok[t]:
            pending = 1
    return {"ret": pd.Series(ret, k.index), "size": pd.Series(size_arr, k.index), "stop": pd.Series(stop_arr, k.index),
            "trades": trades, "open": cur, "pending": pending == 1, "ok": ok, "up_ok": up_ok, "hi": hi, "atr": a, "rv": rv}

def s1_next_bar(k, n, wth=0.5):
    """下一根 K 線的進場門檻與濾網狀態：除了「下一根收盤價」之外全部現在就能算出"""
    nxt = k.index[-1] + pd.Timedelta(hours=4)
    ext = pd.concat([k, pd.DataFrame({"open": [np.nan], "high": [np.nan], "low": [np.nan], "close": [k.close.iloc[-1]],
                                      "volume": [0.0], "funding": [0.0]}, index=[nxt])])
    wr = width_rank(ext, n).iloc[-1]
    trend = bool(daily_filter(ext)[0].iloc[-1])
    hi_next = float(k.high.iloc[-n:].max())
    return {"bar_open": nxt, "bar_close": nxt + pd.Timedelta(hours=4), "trigger": hi_next,
            "width_rank": float(wr), "width_ok": bool(wr <= wth), "trend_ok": trend}

# ---------------------------------------------------------------- D
def d_signals(k):
    """D 的日線分數與目標倉位，只用已收完的 UTC 日"""
    last_complete = k.index[-1].normalize() if k.index[-1].hour == 20 else k.index[-1].normalize() - pd.Timedelta(days=1)
    day = k.resample("1D").agg({"open": "first", "close": "last"}).dropna()
    day = day[day.index <= last_complete]
    c = day.close
    m50, m140, m200 = (c.rolling(n).mean() for n in (50, 140, 200))
    trend = ((c > m200).astype(int) + (m50 > m200).astype(int) + (c > m140).astype(int)) / 3
    mom = sum((c / c.shift(n) - 1 > 0).astype(int) for n in (30, 90, 180)) / 3
    score = 0.5 * trend + 0.5 * mom
    vol = c.pct_change().rolling(30).std() * np.sqrt(365)
    target = (score * (0.4 / vol).clip(upper=1)).clip(0, 1).where(m200.notna() & c.shift(180).notna() & vol.notna())
    return day, score, target, {"trend": trend, "mom": mom, "vol": vol, "m200": m200}

def d_run(k):
    """帳本式：第 d 天收盤算目標 → 第 d+1 天 00:00 UTC（台灣 08:00）開盤調到指令比例；兩次指令間持有固定 BTC 數量"""
    day, score, target, comp = d_signals(k)
    opens = k.open.groupby(k.index.normalize()).first()
    cash, qty, sig = 1.0, 0.0, None
    orders, qty_by_day, cash_by_day = [], {}, {}
    days = list(target.index)
    for i, d0 in enumerate(days):
        tg = target.iloc[i]
        if np.isnan(tg):
            continue
        new = None
        if sig is None:
            new = tg
        elif (tg in (0.0, 1.0) and tg != sig) or abs(tg - sig) >= 0.2 - 1e-12:
            new = tg
        if new is None:
            continue
        sig = new
        d1 = d0 + pd.Timedelta(days=1)
        rec = {"signal_day": d0, "exec_t": d1, "target": float(tg), "score": float(score.iloc[i]), "weight_to": float(new)}
        if d1 in opens.index:                                    # 次日已開盤 → 成交
            px = opens[d1]; total = cash + qty * px
            new_qty = new * total / px
            traded = abs(new_qty - qty) * px
            rec.update(exec_px=float(px), weight_from=float(qty * px / total) if total else 0.0,
                       btc_delta_frac=float((new_qty - qty) * px / total), cost=float(traded * D_COST / total))
            cash = total - new_qty * px - traded * D_COST; qty = new_qty
            qty_by_day[d1], cash_by_day[d1] = qty, cash
            rec["pending"] = False
        else:
            rec["pending"] = True
        orders.append(rec)
    # 映射到 4H：每天開盤後的數量
    q = pd.Series(qty_by_day, dtype=float).reindex(k.index.normalize().unique()).ffill().fillna(0.0)
    cs = pd.Series(cash_by_day, dtype=float).reindex(k.index.normalize().unique()).ffill().fillna(1.0)
    qty4 = q.reindex(k.index.normalize()).values; cash4 = cs.reindex(k.index.normalize()).values
    value = pd.Series(cash4 + qty4 * k.close.values, k.index)
    weight = pd.Series(qty4 * k.close.values, k.index) / value
    return {"value": value, "weight": weight, "orders": orders, "target": target, "score": score, "comp": comp, "day": day,
            "signal": sig}

# ---------------------------------------------------------------- 績效
def perf(eq):
    """eq：4H 淨值序列 → 以日收盤計算（√365）"""
    d = eq.resample("1D").last().dropna()
    r = d.pct_change().dropna()
    if len(r) < 2:
        return {"total": float(eq.iloc[-1] / eq.iloc[0] - 1), "cagr": None, "sharpe": None,
                "maxdd": float((eq / eq.cummax() - 1).min()), "days": len(d)}
    yrs = len(r) / 365
    return {"total": float(d.iloc[-1] / d.iloc[0] - 1), "cagr": float((d.iloc[-1] / d.iloc[0]) ** (1 / yrs) - 1) if yrs > 0.25 else None,
            "sharpe": float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else None,
            "maxdd": float((eq / eq.cummax() - 1).min()), "days": len(d)}

def yearly(eq):
    y = eq.resample("YE").last()
    first = eq.iloc[0]
    out, prev = {}, first
    for t, v in y.items():
        out[str(t.year)] = float(v / prev - 1); prev = v
    return out

def monthly(eq):
    m = eq.resample("ME").last()
    r = m / m.shift(1).fillna(eq.iloc[0]) - 1
    return [[t.year, t.month, float(v)] for t, v in r.items()]

def trade_stats(tr):
    if not tr:
        return {"n": 0}
    r = np.array([t["ret_acct"] for t in tr])
    w, ls = r[r > 0], r[r <= 0]
    return {"n": len(r), "win": float(len(w) / len(r)), "avg_win": float(w.mean()) if len(w) else None,
            "avg_loss": float(ls.mean()) if len(ls) else None,
            "pf": float(w.sum() / abs(ls.sum())) if len(ls) and ls.sum() != 0 else None,
            "best": float(r.max()), "worst": float(r.min()),
            "hold_days": float(np.median([t["hold_h"] for t in tr]) / 24)}

# ---------------------------------------------------------------- 主流程
def main(offline=False, notify=True):
    cfg = json.loads((ROOT / "config.json").read_text())
    STATE.mkdir(exist_ok=True); WEB.mkdir(exist_ok=True)
    upd, mkt = {"candles": 0}, {}
    if not offline:
        try:
            upd["candles"] = update_candles()
        except Exception as e:
            upd["error"] = str(e)
        mkt = market_now()
    else:                                                      # 離線重算（記錄成交、改設定）沿用上次抓到的現價
        try:
            prev = json.loads((WEB / "data.js").read_text().split("=", 1)[1].rstrip().rstrip(";"))
            mkt = prev["meta"].get("market") or {}
        except Exception:
            mkt = {}
    k = load_k()
    T = k.index[-1]
    cap = float(cfg["capital_usdt"]); start = pd.Timestamp(cfg["start"], tz="UTC")
    bt_from = pd.Timestamp(cfg.get("backtest_from", "2021-01-01"), tz="UTC")
    wS, wD = cfg["weights"]["S1"], cfg["weights"]["D"]
    sub_cap = {f"S1-N{n}": cap * wS / len(NS) for n in NS}; sub_cap["D"] = cap * wD
    price = mkt.get("price") or float(k.close.iloc[-1])

    # ---- 策略
    S = {n: s1_run(k, n) for n in NS}
    BT.COST = S1_COST                                          # 自檢：與研究引擎逐根一致（同樣用現貨成本）
    for n in NS:
        ref = donchian(k, n, 3.0, entry_ok=S[n]["ok"])[0]
        assert np.allclose(ref.values, S[n]["ret"].values, atol=1e-12), f"S1 N{n} 與 backtest.donchian 不一致"
    D = d_run(k)

    # ---- 淨值：回測（bt_from 起）與紙上（start 起）兩條，各自以設定資金起算
    def curves(base):
        out = {}
        for n in NS:
            r = (S[n]["ret"] * S[n]["size"])[S[n]["ret"].index >= base]
            out[f"S1-N{n}"] = sub_cap[f"S1-N{n}"] * (1 + r).cumprod()
        v = D["value"][D["value"].index >= base]
        out["D"] = sub_cap["D"] * v / v.iloc[0] if len(v) else v
        return out
    bt = curves(bt_from)
    pp = curves(start) if T >= start else None

    def total(cv):
        return sum(cv.values())

    # ---- 逐筆交易
    trades = []
    for n in NS:
        name = f"S1-N{n}"; rs = S[n]["ret"] * S[n]["size"]
        for tr, is_open in [(t, False) for t in S[n]["trades"]] + ([(S[n]["open"], True)] if S[n]["open"] else []):
            ei = tr["entry_i"]; xi = tr.get("exit_i", len(k) - 1)
            et = k.index[ei]
            if et < bt_from:
                continue
            phase = "paper" if et >= start else "backtest"
            cv = pp if (phase == "paper" and pp is not None) else bt
            eq = cv[name]
            eq_before = float(eq[eq.index < et].iloc[-1]) if (eq.index < et).any() else sub_cap[name]
            acct = float((1 + rs.iloc[ei:xi + 1]).prod() - 1)
            exit_px = tr.get("exit_px", float(k.close.iloc[-1]))
            notional = eq_before * tr["size"]
            trades.append({
                "strat": name, "phase": phase, "open": is_open,
                "entry_t": int(et.timestamp()), "entry_px": round(tr["entry_px"], 1),
                "exit_t": None if is_open else int(k.index[xi].timestamp()), "exit_px": round(exit_px, 1),
                "reason": "持倉中" if is_open else tr["reason"],
                "size": round(tr["size"], 3), "notional": round(notional, 2),
                "btc": qty_floor(notional / tr["entry_px"]),
                "init_stop": round(tr["stops"][0][1], 1),
                "stop_now": round(tr["stops"][-1][1], 1),
                "stops": [[int((k.index[i] if i < len(k) else T + pd.Timedelta(hours=4)).timestamp()), round(s, 1)] for i, s in tr["stops"]],
                "ret_px": float(exit_px / tr["entry_px"] - 1),
                "ret_acct": acct, "pnl": round(acct * eq_before, 2),
                "funding": float(tr["funding"] * tr["size"]),
                "hold_h": float((xi - ei + 1) * 4),
            })
    d_orders = []
    for o in D["orders"]:
        if o["exec_t"] < bt_from:
            continue
        d_orders.append({"strat": "D", "phase": "paper" if o["exec_t"] >= start else "backtest",
                         "signal_t": int(o["signal_day"].timestamp()), "exec_t": int(o["exec_t"].timestamp()),
                         "weight_to": round(o["weight_to"], 4), "weight_from": round(o.get("weight_from", np.nan), 4) if not o["pending"] else None,
                         "px": round(o.get("exec_px", np.nan), 1) if not o["pending"] else None, "score": round(o["score"], 3),
                         "pending": o["pending"]})

    # ---- 目前狀態與操作提示
    next_open = T + pd.Timedelta(hours=4)
    prompts, status = [], {}
    # 紙上帳戶開始前，提示一律以設定的起始資金計算
    eq_now = pp if pp is not None else {kk: pd.Series([v], index=[T]) for kk, v in sub_cap.items()}
    for n in NS:
        name = f"S1-N{n}"; s = S[n]; nb = s1_next_bar(k, n)
        sub_eq = float(eq_now[name].iloc[-1])
        st = {"name": name, "equity": sub_eq, "next": {kk: (int(v.timestamp()) if isinstance(v, pd.Timestamp) else v) for kk, v in nb.items()}}
        size_est = size_from_rv(s["rv"][-1])
        if s["pending"]:
            notional = sub_eq * size_est
            stop_dist = 3 * s["atr"][-1]
            st.update(state="pending", size=size_est, notional=notional, btc=qty_floor(notional / price), stop_dist=stop_dist)
            prompts.append({"level": "action", "strat": name, "key": f"{int(T.timestamp())}|{name}|entry",
                            "title": f"{name} 進場：{tw(next_open)} 開盤市價買進 {st['btc']:.5f} BTC",
                            "text": f"現貨市價買進約 {st['btc']:.5f} BTC（約 {notional:,.0f} USDT，子帳戶的 {size_est:.0%}）。"
                                    f"成交後立刻掛止損委託：觸發價 = 成交價 − {stop_dist:,.0f}（3×ATR），觸發後市價賣出全部。",
                            "ref_price": price, "stop": None, "qty": st["btc"]})
        elif s["open"]:
            tr = s["open"]; cur_stop = tr["stops"][-1][1]
            prev_stop = tr["stops"][-2][1] if len(tr["stops"]) > 1 else None
            raised = tr["stops"][-1][0] == len(k)                # 這次收盤剛上調
            notional = float([t for t in trades if t["strat"] == name and t["open"]][0]["notional"])
            st.update(state="long", entry_t=int(k.index[tr["entry_i"]].timestamp()), entry_px=tr["entry_px"], stop=cur_stop,
                      size=tr["size"], notional=notional, btc=qty_floor(notional / tr["entry_px"]),
                      unreal=float(price / tr["entry_px"] - 1), stop_dist_pct=float(cur_stop / price - 1))
            if raised and prev_stop is not None:
                prompts.append({"level": "action", "strat": name, "key": f"{int(T.timestamp())}|{name}|stop",
                                "title": f"{name} 上調停損至 {cur_stop:,.1f}",
                                "text": f"把止損委託的觸發價從 {prev_stop:,.1f} 改到 {cur_stop:,.1f}（{tw(next_open)} 起生效），數量 {st['btc']:.5f} BTC 不變。",
                                "ref_price": price, "stop": cur_stop, "qty": st["btc"]})
            else:
                prompts.append({"level": "info", "strat": name, "key": None, "title": f"{name} 持倉中",
                                "text": f"停損維持 {cur_stop:,.1f}（距現價 {cur_stop / price - 1:+.1%}），不需動作。"})
        else:
            last_exit = s["trades"][-1] if s["trades"] else None
            just_exit = last_exit is not None and last_exit["exit_i"] == len(k) - 1
            st.update(state="flat", size_est=size_est, notional_est=sub_eq * size_est, btc_est=qty_floor(sub_eq * size_est / price))
            if just_exit:
                prompts.append({"level": "action", "strat": name, "key": f"{int(T.timestamp())}|{name}|exit",
                                "title": f"{name} 已停損出場 @ {last_exit['exit_px']:,.1f}",
                                "text": "交易所的止損委託應已觸發賣出；確認這個子帳戶的 BTC 已賣完，並撤掉殘留委託。",
                                "ref_price": last_exit["exit_px"], "stop": None, "qty": None})
            why = []
            if not nb["trend_ok"]: why.append("日線在 200 日均線下方")
            if not nb["width_ok"]: why.append(f"通道寬度百分位 {nb['width_rank']:.2f} > 0.5")
            if why:
                prompts.append({"level": "info", "strat": name, "key": None, "title": f"{name} 空手，下一根不會進場",
                                "text": "原因：" + "、".join(why) + "。"})
            else:
                prompts.append({"level": "watch", "strat": name, "key": None,
                                "title": f"{name} 空手，等待突破 {nb['trigger']:,.1f}",
                                "text": f"若 {tw(nb['bar_close'])} 收盤 > {nb['trigger']:,.1f}（距現價 {nb['trigger'] / price - 1:+.1%}），"
                                        f"下一根開盤買進約 {st['btc_est']:.5f} BTC（約 {st['notional_est']:,.0f} USDT）。"})
        status[name] = st

    # D
    d_eq = float(eq_now["D"].iloc[-1]); w_now = float(D["weight"].iloc[-1])
    last_sig_day = D["target"].dropna().index[-1]
    tg = float(D["target"].dropna().iloc[-1])
    status["D"] = {"name": "D", "equity": d_eq, "weight": w_now, "target": tg, "signal": D["signal"],
                   "score": float(D["score"].dropna().iloc[-1]), "btc": w_now * d_eq / price,
                   "signal_day": int(last_sig_day.timestamp()),
                   "components": {kk: float(v.dropna().iloc[-1]) for kk, v in D["comp"].items()}}
    pend = [o for o in D["orders"] if o["pending"]]
    if pend:
        o = pend[-1]; delta = o["weight_to"] - w_now
        btc = delta * d_eq / price
        prompts.append({"level": "action", "strat": "D", "key": f"{int(o['signal_day'].timestamp())}|D|rebalance",
                        "title": f"D 調倉：{tw(o['exec_t'])} 把 BTC 比例調到 {o['weight_to']:.0%}",
                        "text": f"目前約 {w_now:.0%} → {o['weight_to']:.0%}：{'買進' if btc > 0 else '賣出'} {abs(btc):.4f} BTC（約 {abs(btc) * price:,.0f} USDT，現貨）。",
                        "ref_price": price, "stop": None, "qty": abs(btc)})
    else:
        prompts.append({"level": "info", "strat": "D", "key": None, "title": f"D 持有 BTC {w_now:.0%}",
                        "text": f"最新目標 {tg:.0%}，指令 {D['signal']:.0%}，相差未達 20%，不調倉。下次判斷：台灣時間明早 08:00。"})

    # ---- 紀錄新的操作提示、通知
    log = pd.read_csv(LOG_PATH) if LOG_PATH.exists() else pd.DataFrame(columns=["logged_at", "key", "strat", "title", "text", "ref_price", "stop", "qty_btc", "mode"])
    new_rows = []
    for p in prompts:
        if p["level"] == "action" and p["key"] not in set(log["key"]):
            new_rows.append({"logged_at": dt.datetime.now(TW).strftime("%Y-%m-%d %H:%M:%S"), "key": p["key"], "strat": p["strat"],
                             "title": p["title"], "text": p["text"], "ref_price": p.get("ref_price"), "stop": p.get("stop"),
                             "qty_btc": p.get("qty"), "mode": cfg["mode"]})
    if new_rows:
        log = pd.concat([log, pd.DataFrame(new_rows)], ignore_index=True)
        log.to_csv(LOG_PATH, index=False)
        if notify and cfg.get("notify", True) and sys.platform == "darwin":
            for r in new_rows:
                msg = r["text"].replace('"', "'")[:180]; ttl = r["title"].replace('"', "'")
                subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Signal Tower" subtitle "{ttl}" sound name "Glass"'],
                               check=False)

    # ---- 實際成交紀錄（網頁輸入）與模型對照
    fills = []
    if FILL_PATH.exists():
        fdf = pd.read_csv(FILL_PATH)
        for _, f in fdf.iterrows():
            ft = pd.Timestamp(f["time"]).tz_localize(TW) if pd.Timestamp(f["time"]).tzinfo is None else pd.Timestamp(f["time"])
            rec = {kk: (None if pd.isna(v) else v) for kk, v in f.items()}
            rec["t"] = int(ft.timestamp())
            # 找同策略、同方向、8 小時內的模型成交
            cands = []
            for t in trades:
                if t["strat"] != f["strat"]: continue
                if f["side"] == "buy": cands.append((t["entry_t"], t["entry_px"]))
                elif f["side"] == "sell" and t["exit_t"]: cands.append((t["exit_t"], t["exit_px"]))
            for o in d_orders:
                if f["strat"] == "D" and o["px"]: cands.append((o["exec_t"], o["px"]))
            near = [c for c in cands if abs(c[0] - rec["t"]) <= 8 * 3600]
            if near:
                mt, mp = min(near, key=lambda c: abs(c[0] - rec["t"]))
                sgn = 1 if f["side"] == "buy" else -1
                rec.update(model_t=mt, model_px=mp, slip=float(sgn * (float(f["price"]) / mp - 1)))
            fills.append(rec)

    # ---- 輸出
    k_out = k[k.index >= bt_from - pd.Timedelta(days=30)]
    day_ma = k.close.resample("1D").last().rolling(200).mean().shift(1)
    day_ma = day_ma[day_ma.index >= bt_from - pd.Timedelta(days=30)].dropna()
    tot_bt = total(bt)
    btc_bh = cap * k.close[k.index >= bt_from] / k.close[k.index >= bt_from].iloc[0]
    def ser(s, step="1D"):
        x = s.resample(step).last().dropna()
        return [[int(t.timestamp()), round(float(v), 2)] for t, v in x.items()]
    expo = pd.DataFrame({"S1": sum(S[n]["size"] * (S[n]["ret"].index >= bt_from) for n in NS) / len(NS) * wS,
                         "D": D["weight"] * wD}, index=k.index)[k.index >= bt_from]
    out = {
        "meta": {"generated_at": int(time.time()), "last_bar": int(T.timestamp()), "next_close": int((T + pd.Timedelta(hours=8)).timestamp()),
                 "next_open": int(next_open.timestamp()), "mode": cfg["mode"], "capital": cap, "start": int(start.timestamp()),
                 "backtest_from": int(bt_from.timestamp()), "weights": cfg["weights"], "red_up": cfg.get("red_up", False),
                 "update": upd, "market": mkt, "price": price, "inst": INST, "market_type": "spot", "s1_cost": S1_COST, "s1_cap": S1_CAP,
                 "stale_hours": float((pd.Timestamp.now(tz="UTC") - (T + pd.Timedelta(hours=4))).total_seconds() / 3600)},
        "candles": [[int(t.timestamp()), round(r.open, 1), round(r.high, 1), round(r.low, 1), round(r.close, 1)] for t, r in k_out.iterrows()],
        "lines": {"ma200": [[int(t.timestamp()), round(float(v), 1)] for t, v in day_ma.items()],
                  **{f"hi{n}": [[int(t.timestamp()), round(float(v), 1)] for t, v in zip(k.index, S[n]["hi"]) if t >= bt_from - pd.Timedelta(days=30) and np.isfinite(v)] for n in NS}},
        "trades": trades, "d_orders": d_orders,
        "equity": {"backtest": {**{kk: ser(v) for kk, v in bt.items()}, "total": ser(tot_bt), "btc": ser(btc_bh)},
                   "paper": ({**{kk: ser(v, "4h") for kk, v in pp.items()}, "total": ser(total(pp), "4h"),
                              "btc": ser(cap * k.close[k.index >= start] / k.close[k.index >= start].iloc[0], "4h")} if pp is not None else None)},
        "exposure": [[int(t.timestamp()), round(float(r.S1), 3), round(float(r.D), 3)] for t, r in expo.resample("1D").mean().dropna().iterrows()],
        "stats": {
            "backtest": {**{kk: perf(v) for kk, v in bt.items()}, "S1": perf(bt["S1-N20"] + bt["S1-N30"]),
                         "total": perf(tot_bt), "btc": perf(btc_bh)},
            "paper": ({**{kk: perf(v) for kk, v in pp.items()}, "S1": perf(pp["S1-N20"] + pp["S1-N30"]),
                       "total": perf(total(pp))} if pp is not None else None),
            "trades_bt": {f"S1-N{n}": trade_stats([t for t in trades if t["strat"] == f"S1-N{n}" and not t["open"] and t["phase"] == "backtest"]) for n in NS},
            "yearly": {"total": yearly(tot_bt), "S1": yearly(bt["S1-N20"] + bt["S1-N30"]), "D": yearly(bt["D"]), "btc": yearly(btc_bh)},
            "monthly": monthly(tot_bt),
        },
        "status": status, "prompts": prompts,
        "log": log.fillna("").to_dict("records")[-200:], "fills": fills,
    }
    (WEB / "data.js").write_text("window.PAPER_DATA = " + json.dumps(out, ensure_ascii=False, separators=(",", ":"), default=float) + ";\n")
    print(f"[{dt.datetime.now(TW):%Y-%m-%d %H:%M}] 最後收盤 K 線 {tw(T)}–{tw(next_open)[-5:]}（台灣）收盤 {k.close.iloc[-1]:,.1f}；新增 K 線 {upd['candles']}；"
          f"新提示 {len(new_rows)} 筆" + (f"；更新錯誤：{upd['error']}" if "error" in upd else ""))
    for p in prompts:
        print(f"  [{p['level']}] {p['title']} — {p['text']}")
    return out

if __name__ == "__main__":
    main(offline="--offline" in sys.argv, notify="--no-notify" not in sys.argv)
