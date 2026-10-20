# BTC 短線策略回測（策略帳戶研究用）

資料：Binance BTCUSDT 現貨 4H K 線（2018-01 起）與永續合約資金費率（2020-01 起），來自 data.binance.vision。
回測區間：2019-01 至 2026-09；樣本內 2019–2023，樣本外 2024–2026/9。

## 檔案
- `fetch_data.py`：下載／更新資料（重跑即可抓到最新月份）
- `backtest.py`：回測引擎與三個策略（S1 通道突破、S2 時間序列動量、S3 資金費率套利）
- `run.py`：主要比較表 → `results_summary.csv`、`equity_curves.csv`
- `robustness.py`：參數網格、隨機進場對照、套利逐年拆解
- `filters.py`：S1 加上額外指標／數據的進場濾網、與「隨機刪掉同樣多交易」對照、波動率目標化倉位 → `filters_grid.csv`、`filters_vs_random.csv`（`python filters.py [隨機次數]`）
- `eth_validate.py`：用 BTC 挑好的參數在 ETHUSDT 上獨立驗證寬度濾網與波動目標化 → `eth_results.csv`、`eth_vs_random.csv`（先 `python fetch_data.py ETHUSDT`）
- `fetch_okx.py`：下載 OKX BTC 現貨與永續 4H K 線、永續資金費率（OKX 月檔只到 2021-09，且 2021-10~2022-01 缺檔，缺的時段用 Binance 補成 `okx_funding_spliced.csv`；OKX 會擋 Python 預設 User-Agent）
- `okx_validate.py`：用 OKX 資料重跑 S1 與兩個改良，和 Binance 並排、逐筆進場比對、隨機刪單對照、永續 K 線版本 → `okx_results.csv`、`okx_vs_random.csv`

- `manual_exec.py`：手動操作版（只在醒著時進場、上調停損）vs 全自動的績效差異
- `review_gemini.py`：逐項驗證 Gemini 交叉審查的主張（整段抽樣隨機對照、4H 均線、ATR 百分位、浮盈收緊、N 合成與 BTC/ETH 組合）
- `longshort.py`：S1 多空版（空單上限 1 倍）vs 只做多、空單逐年拆解、空單 vs 200MA 下方隨機放空
- `regime.py`：更嚴格的趨勢確認（200MA 斜率、50/200 排列、連續 20 天）× 只做多／多空，空單逐年（含 2018 下半年）與隨機放空對照
- `fusion.py`：S1 與日線策略 D（strategy_daily.csv）的相關性、分資金組合、以 D 的方向分數當 S1 濾網
- `supertrend.py`：4H Supertrend 當進場濾網、出場、或取代突破進場的對照
- `paper/`：Signal Tower，紙上交易／手動實盤網站（S1＋D，K 線與交易標記、操作提示、資金與績效圖表），說明見 `paper/README.md`
- `STRATEGY.md`：完整策略規格與驗證結果（給其他 AI／人交叉審查用）

## 執行
```
pip install pandas numpy
python fetch_data.py            # BTC；python fetch_data.py ETHUSDT 抓 ETH
python run.py
python robustness.py
python filters.py 300
python eth_validate.py 300
python fetch_okx.py
python okx_validate.py 300
python manual_exec.py
python review_gemini.py 300
python longshort.py 300
python regime.py 300
python fusion.py ~/Downloads/strategy_daily.csv
```

## 回測假設
- 訊號在 K 線收盤計算，下一根開盤成交；日線訊號一律延後一天，避免偷看未來
- 每邊成本 0.07%（手續費 0.05% + 滑點 0.02%）
- 方向性策略以永續合約 1 倍名目計，扣／收實際資金費（2020 前以 0.01%/8h 代入）
- S3 只計資金費與開平倉成本，未計現貨與合約價差（基差）波動，也未計交易所風險
