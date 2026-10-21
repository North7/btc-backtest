"""
紙上交易網站的本機伺服器（只綁 127.0.0.1，不對外）。
- 提供 paper/web 下的網頁與 data.js
- POST /api/fill    記錄實際成交 → state/fills.csv，並重算
- POST /api/config  切換模式（paper / live）、資金、起始日、權重
- POST /api/refresh 立刻從 OKX 抓最新資料並重算
用法：.venv/bin/python paper/server.py [--port 8787]  → 開 http://127.0.0.1:8787
"""
import csv, json, subprocess, sys, datetime as dt, threading
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB, STATE, CFG = ROOT / "web", ROOT / "state", ROOT / "config.json"
FILL_PATH = STATE / "fills.csv"
FILL_COLS = ["recorded_at", "strat", "side", "time", "price", "contracts", "btc", "fee_usdt", "note"]
PORT = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8787   # 避開其他專案常用的 8765
LOCK = threading.Lock()

def run_engine(online):
    args = [sys.executable, str(ROOT / "engine.py"), "--no-notify"] + ([] if online else ["--offline"])
    p = subprocess.run(args, capture_output=True, text=True, timeout=180)
    return p.returncode == 0, (p.stdout + p.stderr)[-2000:]

class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/ping":
            return self.reply(200, {"ok": True, "config": json.loads(CFG.read_text())})
        return super().do_GET()

    def do_POST(self):
        # 只接受同源請求，避免其他網站透過瀏覽器打本機 API
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self.reply(403, {"ok": False, "error": "forbidden origin"})
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self.reply(400, {"ok": False, "error": "bad json"})
        with LOCK:
            if self.path == "/api/fill":
                return self.fill(body)
            if self.path == "/api/config":
                return self.config(body)
            if self.path == "/api/refresh":
                ok, out = run_engine(online=True)
                return self.reply(200 if ok else 500, {"ok": ok, "log": out})
        return self.reply(404, {"ok": False})

    def fill(self, b):
        try:
            strat = str(b["strat"]); side = str(b["side"])
            assert strat in ("S1-N20", "S1-N30", "D"), "策略只能是 S1-N20、S1-N30、D"
            assert side in ("buy", "sell"), "方向只能是買進或賣出"
            t = dt.datetime.fromisoformat(str(b["time"]))
            price = float(b["price"]); assert price > 0, "成交價要大於 0"
            contracts = float(b.get("contracts") or 0); btc = float(b.get("btc") or 0)
            fee = float(b.get("fee_usdt") or 0)
            note = str(b.get("note") or "")[:200]
        except Exception as e:
            return self.reply(400, {"ok": False, "error": f"欄位錯誤：{e}"})
        STATE.mkdir(exist_ok=True)
        new = not FILL_PATH.exists()
        with FILL_PATH.open("a", newline="") as f:
            w = csv.writer(f)
            if new: w.writerow(FILL_COLS)
            w.writerow([dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), strat, side, t.strftime("%Y-%m-%d %H:%M"),
                        price, contracts, btc, fee, note])
        ok, out = run_engine(online=False)
        return self.reply(200 if ok else 500, {"ok": ok, "log": out})

    def config(self, b):
        cfg = json.loads(CFG.read_text())
        try:
            if "mode" in b:
                assert b["mode"] in ("paper", "live"); cfg["mode"] = b["mode"]
            if "capital_usdt" in b:
                v = float(b["capital_usdt"]); assert 0 < v < 1e9; cfg["capital_usdt"] = v
            if "start" in b:
                cfg["start"] = dt.date.fromisoformat(str(b["start"])).isoformat()
            if "weights" in b:
                s1 = float(b["weights"]["S1"]); d = float(b["weights"]["D"])
                assert s1 >= 0 and d >= 0 and abs(s1 + d - 1) < 1e-6
                cfg["weights"] = {"S1": s1, "D": d}
            if "red_up" in b:
                cfg["red_up"] = bool(b["red_up"])
        except Exception as e:
            return self.reply(400, {"ok": False, "error": f"設定錯誤：{e}"})
        CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
        ok, out = run_engine(online=False)
        return self.reply(200 if ok else 500, {"ok": ok, "log": out, "config": cfg})

if __name__ == "__main__":
    print(f"Signal Tower：http://127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
