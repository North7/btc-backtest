# Signal Tower：BTC 策略帳戶的紙上交易／手動實盤網站

每根 4H K 線收盤後，從 OKX 抓最新資料、重跑策略，產生下一步要做的事，並用網頁呈現：
K 線上的回測與紙上交易進出場、停損線、D 的調倉、資金曲線、回撤、曝險、逐年／每月報酬、逐筆損益、交易紀錄。

- **S1**：4H 通道突破只做多，N20、N30 兩個子帳戶各半；寬度濾網、200 日均線、3×ATR 移動停損、波動目標倉位（上限 1 倍）；OKX BTC-USDT **現貨**，成本 0.12%/邊、無資金費，提示直接寫買賣多少 BTC，停損用現貨止損委託
- **D**：日線趨勢＋動能＋波動目標（現貨、20% 調倉門檻）
- 預設 20,000 USDT，S1 與 D 各 10,000（S1 內 N20、N30 各 5,000），2026-10-20 起算（`config.json`）

## 現在的運作方式（全部在雲端，不需要本機電腦）
網站：**https://north7.github.io/signals/**

1. **cron-job.org** 在台灣時間 0/4/8/12/16/20 點過 2 分，呼叫 GitHub API 觸發排程
   （`POST /repos/North7/signals/actions/workflows/pages.yml/dispatches`，內容 `{"ref":"main"}`，
   用一組只有這個 repo「Actions 讀寫」權限的 fine-grained 權杖；權杖到期要重新產生並更新到 cron-job.org）
2. **GitHub Actions**（`.github/workflows/pages.yml`）從 OKX 抓最新 K 線、重跑引擎，有新 K 線才發布到 GitHub Pages
3. 有要操作的指令時，用 **[ntfy](https://ntfy.sh)** 推播到手機。頻道名稱存在 GitHub secret `NTFY_TOPIC`（不在程式碼裡），手機 ntfy App 訂閱同名頻道
   - N20、N30 相同指令合併成一則；台灣時間 0–8 點的上調停損是靜音推播，進場、出場、D 調倉是高優先
   - 同一根 K 線被觸發多次也沒關係：沒有新 K 線就不發布、不推播
4. 操作指令紀錄 `signals_log.csv` 存在**私人 repo** [North7/signals-state](https://github.com/North7/signals-state)（不放公開 repo）：
   每次執行前取回到 `paper/state/`，有新指令才提交推回，每筆提交就是當時發出的指令，供事後和重跑結果對照
   - 用部署金鑰存取（signals 的 secret `STATE_DEPLOY_KEY`，只有那個 repo 的寫入權限）
   - 取回或推送失敗時網站照常發布，但那次執行會標成失敗（`log-check` 工作）
   - 在本機查看：`gh repo clone North7/signals-state`，或直接到 GitHub 上看

備援：GitHub 內建排程每 15 分鐘嘗試一次，但實測常被延遲或丟棄（曾 7 小時只跑 1 次），只在 cron-job.org 出問題時有用。

注意：
- 公開網站是唯讀展示版：沒有立即更新、記錄成交、設定與提示紀錄；在 GitHub Actions 產生的 `data.js` 也不帶操作紀錄與成交（只存在私人 repo）
- 兩根 K 線之間不會重新發布，網頁上的「現價」最多落後 4 小時
- 公開 repo 若 60 天沒有任何提交，GitHub 會停用內建排程（cron-job.org 觸發不受影響）

## 本機版（選用）
需要「記錄實際成交」「切換正式資金」這些要寫入資料的功能時才需要：
```bash
cd ~/Documents/Claude\ Code/signals && .venv/bin/python paper/server.py
```
然後開 http://127.0.0.1:8787 。要讓它常駐並每 4 小時自動更新：`paper/launchd/install.sh`（移除：`uninstall.sh`）。
2026-10-22 起已移除本機排程，全部改由雲端執行；本機版跑完也會觸發公開網站更新（`config.json` 的 `publish_repo`）。
- 如果紀錄出現 `Operation not permitted`，到「系統設定 → 隱私權與安全性 → 完全取用磁碟」加入 `.venv/bin/python`

## 轉成正式資金
網站右上「設定」→ 模式改成「正式資金」，起始資金填策略帳戶的實際金額，起算日填開始下單那天。
之後的提示張數照這個資金計算；每次在 OKX 下單後按「記錄成交」輸入實際成交價，網站會對照模型價格算滑價。
模型淨值是照規則算出來的，不會讀你的交易所帳戶；兩者的差距就是執行誤差，要定期對帳。

## 檔案
| 檔案 | 用途 |
|---|---|
| `engine.py` | 抓資料、跑策略（啟動時自檢與 `backtest.donchian` 逐根一致）、產生提示、寫 `web/data.js` |
| `server.py` | 本機網站與 API（只綁 127.0.0.1；拒絕其他網站來源的請求） |
| `config.json` | 模式、資金、起算日、權重、K 線顏色 |
| `web/` | 網頁（「瓷 Porcelain」設計：日間／夜間預設跟隨系統，右上可手動切換並記住；字體 Manrope＋Noto Sans TC 從 Google Fonts 載入；每張圖右上 ⛶ 放大鋪滿視窗，支援的瀏覽器會同時進入系統全螢幕，Esc 離開；K 線圖用 TradingView lightweight-charts 4.2.0，從 unpkg 載入，需要網路） |
| `data/` | OKX BTC-USDT 現貨 4H K 線（第一次從 `../data/okx_btc_4h.csv` 複製，之後每次補新的） |
| `state/signals_log.csv` | 每次發出的操作指令（事後對照用）；雲端版存在私人 repo signals-state，本機版寫在這裡，兩份各自獨立 |
| `state/fills.csv` | 你輸入的實際成交 |

## 已知限制
- D 的回測在 2021 年和原始 CSV 有差異（20% 門檻有路徑依賴，起算日不同時倉位狀態不同）；2022 年起每年差距在 0.3 個百分點內
- 組合是兩個子帳戶直接相加，不再平衡
- 紙上帳戶開始前（起算日之前）已持有的部位，張數是用回測資金算的，只供參考
