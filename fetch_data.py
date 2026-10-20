"""從 data.binance.vision 下載 4H 現貨 K 線與 USDT 永續合約資金費率（月檔）。
用法：python fetch_data.py [SYMBOL]，預設 BTCUSDT → data/btc_4h.csv、data/funding.csv；
其他幣種存成 data/<幣>_4h.csv、data/<幣>_funding.csv（例：ETHUSDT → data/eth_4h.csv）"""
import io, sys, zipfile, urllib.request, pandas as pd
from datetime import date

BASE = "https://data.binance.vision/data"

def months(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m == 13: y, m = y + 1, 1

def get_zip_csv(url):
    try:
        raw = urllib.request.urlopen(url, timeout=30).read()
    except Exception:
        return None
    z = zipfile.ZipFile(io.BytesIO(raw))
    return z.read(z.namelist()[0]).decode()

def fetch_klines(start=(2018, 1), end=None, symbol="BTCUSDT"):
    end = end or (date.today().year, date.today().month)
    frames = []
    for y, m in months(start, end):
        txt = get_zip_csv(f"{BASE}/spot/monthly/klines/{symbol}/4h/{symbol}-4h-{y}-{m:02d}.zip")
        if txt is None:
            continue
        df = pd.read_csv(io.StringIO(txt), header=None).iloc[:, :6]
        if not str(df.iloc[0, 0]).isdigit():  # 有些檔案有表頭
            df = df.iloc[1:]
        df.columns = ["ts", "open", "high", "low", "close", "volume"]
        frames.append(df.astype(float))
    df = pd.concat(frames)
    # 2025 年起 Binance 現貨時間戳改為微秒
    df["ts"] = df["ts"].where(df["ts"] < 1e14, df["ts"] / 1000)
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.drop(columns="ts").sort_index()
    return df[~df.index.duplicated()]

def fetch_funding(start=(2019, 10), end=None, symbol="BTCUSDT"):
    end = end or (date.today().year, date.today().month)
    frames = []
    for y, m in months(start, end):
        txt = get_zip_csv(f"{BASE}/futures/um/monthly/fundingRate/{symbol}/{symbol}-fundingRate-{y}-{m:02d}.zip")
        if txt is None:
            continue
        df = pd.read_csv(io.StringIO(txt))
        frames.append(df)
    df = pd.concat(frames)
    df.columns = [c.strip().lower() for c in df.columns]
    tcol = [c for c in df.columns if "time" in c][0]
    rcol = [c for c in df.columns if "rate" in c and "interval" not in c][0]
    s = pd.Series(df[rcol].astype(float).values,
                  index=pd.to_datetime(df[tcol].astype("int64"), unit="ms", utc=True), name="funding")
    return s.sort_index()[~s.index.duplicated()]

def paths(symbol="BTCUSDT"):
    """(K 線路徑, 資金費率路徑)；BTC 沿用舊檔名"""
    if symbol == "BTCUSDT":
        return "data/btc_4h.csv", "data/funding.csv"
    c = symbol.removesuffix("USDT").lower()
    return f"data/{c}_4h.csv", f"data/{c}_funding.csv"

if __name__ == "__main__":
    sym = sys.argv[1].upper() if len(sys.argv) > 1 else "BTCUSDT"
    kp, fp = paths(sym)
    k = fetch_klines(symbol=sym); k.to_csv(kp)
    print(sym, "klines", k.index[0], "->", k.index[-1], len(k), "→", kp)
    f = fetch_funding(symbol=sym); f.to_csv(fp)
    print(sym, "funding", f.index[0], "->", f.index[-1], len(f), "→", fp)
