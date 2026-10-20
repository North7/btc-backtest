"""從 OKX 下載 BTC 4H K 線（現貨 BTC-USDT 與永續 BTC-USDT-SWAP）與永續資金費率。
- K 線：公開 API /api/v5/market/history-candles 往回分頁（現貨約自 2018-01、永續約自 2020-01）
- 資金費率：/api/v5/public/market-data-history（module=3）的月檔，OKX 只回補到 2021-09；
  且 2021-10~2022-01 中 OKX 本身缺檔；另存一份「OKX 缺的時段用 Binance 實際費率補」的版本 data/okx_funding_spliced.csv
"""
import io, json, sys, time, zipfile, urllib.request
import pandas as pd

API = "https://www.okx.com/api/v5"
END = pd.Timestamp("2026-10-01", tz="UTC")       # 與 Binance 資料同樣截到 2026-09 底，方便對照
UA = {"User-Agent": "Mozilla/5.0"}                # OKX 會擋 Python 預設 User-Agent（403）

def fetch(url, timeout=30):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()

def get(url):
    for i in range(5):
        try:
            return json.loads(fetch(url))
        except Exception:
            time.sleep(1 + i)
    raise RuntimeError(url)

def fetch_candles(inst, start="2018-01-01"):
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    after, rows = int(END.timestamp() * 1000), []
    while True:
        d = get(f"{API}/market/history-candles?instId={inst}&bar=4H&limit=100&after={after}")["data"]
        if not d:
            break
        rows += d
        after = int(d[-1][0])
        if after <= start_ms:
            break
        time.sleep(0.12)                       # 限速 20 次 / 2 秒
    df = pd.DataFrame(rows).iloc[:, [0, 1, 2, 3, 4, 5, 8]]
    df.columns = ["ts", "open", "high", "low", "close", "volume", "confirm"]
    df = df[df.confirm == "1"].drop(columns="confirm").astype(float)
    df.index = pd.to_datetime(df.pop("ts").astype("int64"), unit="ms", utc=True)
    df = df.sort_index()
    df = df[~df.index.duplicated()]
    return df[(df.index >= pd.Timestamp(start, tz="UTC")) & (df.index < END)]

def fetch_funding(begin="2020-01-01"):
    urls, b = [], pd.Timestamp(begin, tz="UTC")
    while b < END:                              # 每次最多 10 個月
        e = min(b + pd.DateOffset(months=9), END)
        q = (f"{API}/public/market-data-history?module=3&instType=SWAP&instFamilyList=BTC-USDT"
             f"&dateAggrType=monthly&begin={int(b.timestamp()*1000)}&end={int(e.timestamp()*1000)}")
        for det in get(q)["data"][0]["details"]:
            urls += [g["url"] for g in det["groupDetails"]]
        b = e + pd.DateOffset(months=1)
        time.sleep(0.5)
    frames = []
    for u in sorted(set(urls)):
        z = zipfile.ZipFile(io.BytesIO(fetch(u, 60)))
        frames.append(pd.read_csv(io.BytesIO(z.read(z.namelist()[0]))))
    df = pd.concat(frames)
    s = pd.Series(df.funding_rate.astype(float).values,
                  index=pd.to_datetime(df.funding_time.astype("int64"), unit="ms", utc=True), name="funding")
    s = s.sort_index()
    s = s[~s.index.duplicated()]
    return s[s.index < END]

if __name__ == "__main__":
    only_funding = "--funding" in sys.argv
    for inst, path in [("BTC-USDT", "data/okx_btc_4h.csv"), ("BTC-USDT-SWAP", "data/okx_btc_swap_4h.csv")]:
        if only_funding: break
        k = fetch_candles(inst); k.to_csv(path)
        print(inst, k.index[0], "->", k.index[-1], len(k), "→", path)
    f = fetch_funding(); f.to_csv("data/okx_funding.csv", index_label="calc_time")
    print("funding", f.index[0], "->", f.index[-1], len(f), "→ data/okx_funding.csv")
    b = pd.read_csv("data/funding.csv", index_col=0)["funding"]
    b.index = pd.to_datetime(b.index, utc=True, format="mixed").floor("h")
    fo = f.copy(); fo.index = fo.index.floor("h"); fo = fo[~fo.index.duplicated()]
    sp = fo.combine_first(b)                    # OKX 沒有的時段（2021-09 前、2021-10~2022-01 缺檔）用 Binance
    sp.to_csv("data/okx_funding_spliced.csv", index_label="calc_time")
    print("spliced（OKX 缺的時段用 Binance）", sp.index[0], "->", sp.index[-1], len(sp),
          f"其中 OKX {sp.index.isin(fo.index).mean():.0%}")
