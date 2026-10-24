"use strict";
/* Signal Tower（BTC 策略帳戶）：讀 data.js（paper/engine.py 產生）畫 K 線、交易標記、資金曲線與各種統計。
   圖表時間一律平移成台灣時間（UTC+8）餵給 lightweight-charts，所以軸上直接顯示台灣時間。 */
const LC = window.LightweightCharts;
const TZ = 8 * 3600, H4 = 4 * 3600, DAY = 86400;
let P = window.PAPER_DATA;
let SERVER = false;
const $ = (id) => document.getElementById(id);
const cssv = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const STRATS = ["S1-N20", "S1-N30"];
const SHORT = { "S1-N20": "N20", "S1-N30": "N30", D: "D" };
// D 調倉方向：比例變大 = 加倉，變小 = 減倉（第一次建倉沒有前值，視為加倉）
const dAct = (o) => (o.weight_from == null || o.weight_to >= o.weight_from ? "加倉" : "減倉");
const pctw = (w) => (w == null ? "—" : Math.round(w * 100) + "%");

// ---------------------------------------------------------------- 格式
const pad = (n) => String(n).padStart(2, "0");
function fmtShifted(t, time = true) {            // t 已經平移成台灣時間
  const d = new Date(t * 1000);
  const s = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`;
  return time ? `${s} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` : s;
}
const tstr = (t, time = true) => (t == null ? "—" : fmtShifted(t + TZ, time));
const hhmm = (t) => fmtShifted(t + TZ).slice(11);
const num = (v, d = 0) => (v == null || !isFinite(v) ? "—" : Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
const pct = (v, d = 1, sign = true) => (v == null || !isFinite(v) ? "—" : (sign && v >= 0 ? "+" : "") + (v * 100).toFixed(d) + "%");
const cls = (v) => (v == null ? "" : v > 0 ? "pos" : v < 0 ? "neg" : "");
const L = (t) => t + TZ;
function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k === "style") e.style.cssText = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const c of kids.flat()) if (c != null) e.append(c.nodeType ? c : document.createTextNode(String(c)));
  return e;
}
function rgba(hex, a) {
  const h = hex.replace("#", "");
  const n = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
}

// ---------------------------------------------------------------- 顏色（隨深淺色切換）
let C = {};
function readColors() {
  for (const k of ["s1", "s1b", "d", "total", "btc", "pos", "neg", "up", "down", "grid", "ink", "ink-2", "muted", "surface", "surface-3", "axis", "warning", "mid", "on-strong"])
    C[k.replace("-", "_")] = cssv("--" + k);
  C.strat = { "S1-N20": C.s1, "S1-N30": C.s1b, D: C.d };
}
function baseOpts(extra = {}) {
  return Object.assign({
    autoSize: true,
    layout: { background: { type: "solid", color: C.surface }, textColor: C.ink_2, fontSize: 12,
              fontFamily: getComputedStyle(document.body).fontFamily },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.axis },
    timeScale: { borderColor: C.axis, timeVisible: true, secondsVisible: false, rightOffset: 4, minBarSpacing: 0.02 },
    crosshair: { mode: LC.CrosshairMode.Normal,
                 vertLine: { color: rgba(C.muted, 0.6), labelBackgroundColor: C.surface_3 || C.axis },
                 horzLine: { color: rgba(C.muted, 0.6), labelBackgroundColor: C.surface_3 || C.axis } },
    localization: { timeFormatter: (t) => fmtShifted(t), priceFormatter: (p) => num(p, Math.abs(p) < 10 ? 3 : 1) },
  }, extra);
}
const candleColors = () => {
  const redUp = !!P.meta.red_up;
  const up = redUp ? C.down : C.up, dn = redUp ? C.up : C.down;
  return { upColor: up, downColor: dn, borderUpColor: up, borderDownColor: dn, wickUpColor: up, wickDownColor: dn };
};

// ---------------------------------------------------------------- 狀態
const S = { tf: "4h", range: 90, eq: "backtest", markerText: true, tradeTab: "s1" };
const charts = {};

// ---------------------------------------------------------------- K 線
function bucket(t) { return S.tf === "4h" ? L(t) : L(Math.floor(t / DAY) * DAY); }
function candleData() {
  if (S.tf === "4h") return P.candles.map(([t, o, h, l, c]) => ({ time: L(t), open: o, high: h, low: l, close: c }));
  const m = new Map();
  for (const [t, o, h, l, c] of P.candles) {
    const k = Math.floor(t / DAY) * DAY, x = m.get(k);
    if (!x) m.set(k, { time: L(k), open: o, high: h, low: l, close: c });
    else { x.high = Math.max(x.high, h); x.low = Math.min(x.low, l); x.close = c; }
  }
  return [...m.values()];
}
function stopLineData(strat) {
  const m = new Map();
  const step = S.tf === "4h" ? H4 : DAY;
  for (const tr of P.trades.filter((x) => x.strat === strat)) {
    const end = tr.exit_t ?? P.meta.next_open;
    for (const [t, v] of tr.stops) m.set(bucket(t), v);
    m.set(bucket(end), tr.stops[tr.stops.length - 1][1]);
    const gap = bucket(end + step);
    if (!m.has(gap)) m.set(gap, null);                    // 斷開兩筆交易之間的線
  }
  // 這個函式庫的折線不會在空白點斷開：兩筆交易之間改放一個透明點，值取下一筆的第一個停損，
  // 階梯線的水平段與垂直段因此都畫成透明
  const pts = [...m.entries()].sort((a, b) => a[0] - b[0]);
  const out = [];
  for (let i = 0; i < pts.length; i++) {
    const [time, value] = pts[i];
    if (value != null) { out.push({ time, value }); continue; }
    let nxt = null;
    for (let j = i + 1; j < pts.length; j++) if (pts[j][1] != null) { nxt = pts[j][1]; break; }
    if (nxt == null) nxt = out.length ? out[out.length - 1].value : null;
    if (nxt != null) out.push({ time, value: nxt, color: "rgba(0,0,0,0)" });
  }
  return out;
}
function markerData() {
  const show = { "S1-N20": $("shN20").checked, "S1-N30": $("shN30").checked, D: $("shD").checked };
  const txt = S.markerText, out = [];
  // N20、N30 在同一根 K 線進場或出場時合成一個標記，避免標籤疊在一起
  const groups = new Map();
  const put = (t, kind, tr) => {
    const key = `${bucket(t)}|${kind}`;
    const g = groups.get(key) || { time: bucket(t), kind, items: [] };
    g.items.push(tr); groups.set(key, g);
  };
  for (const tr of P.trades) {
    if (!show[tr.strat]) continue;
    put(tr.entry_t, "in", tr);
    if (tr.exit_t) put(tr.exit_t, "out", tr);
  }
  for (const g of groups.values()) {
    const both = g.items.length > 1;
    const color = both ? C.s1 : C.strat[g.items[0].strat];
    const name = both ? "N20+N30" : SHORT[g.items[0].strat];
    const pre = g.items.some((t) => t.phase === "paper") ? "紙 " : "";
    if (g.kind === "in") out.push({ time: g.time, position: "belowBar", color, shape: "arrowUp", text: txt ? pre + name : "" });
    else {
      const r = g.items.reduce((a, t) => a + t.ret_acct, 0) / g.items.length;
      out.push({ time: g.time, position: "aboveBar", color, shape: "arrowDown", text: txt ? `${name} ${pct(r)}` : "" });
    }
  }
  if (show.D) for (const o of P.d_orders) {
    if (o.pending) continue;
    const add = dAct(o) === "加倉";
    out.push({ time: bucket(o.exec_t), position: add ? "belowBar" : "aboveBar", color: C.d, shape: add ? "arrowUp" : "arrowDown",
               text: txt ? `D ${dAct(o)}→${pctw(o.weight_to)}` : "" });
  }
  const st = P.meta.start;
  if (st >= P.candles[0][0] && st <= P.meta.last_bar + H4)
    out.push({ time: bucket(Math.min(st, P.meta.last_bar)), position: "aboveBar", color: C.ink, shape: "square", text: "紙上交易開始" });
  return out.sort((a, b) => a.time - b.time);
}
// 每根 K 線上發生的事件（hover 說明用）
function eventIndex() {
  const m = new Map();
  const add = (t, s) => { const k = bucket(t); m.set(k, (m.get(k) || []).concat(s)); };
  for (const tr of P.trades) {
    add(tr.entry_t, `${tr.strat} 進場 @ ${num(tr.entry_px, 1)}（${num(tr.btc, 5)} BTC，子帳戶的 ${pct(tr.size, 0, false)}）`);
    if (tr.exit_t) add(tr.exit_t, `${tr.strat} 出場 @ ${num(tr.exit_px, 1)}，帳戶 ${pct(tr.ret_acct)}`);
  }
  for (const o of P.d_orders) if (!o.pending) add(o.exec_t, `D ${dAct(o)}：BTC 比例 ${pctw(o.weight_from)} → ${pctw(o.weight_to)} @ ${num(o.px, 1)}`);
  return m;
}

function initK() {
  const ch = LC.createChart($("kchart"), baseOpts({ watermark: { visible: true, text: "BTC / USDT · 4H", fontSize: 54, color: rgba(C.ink, 0.045), horzAlign: "center", vertAlign: "center" } }));
  const k = { chart: ch };
  k.candle = ch.addCandlestickSeries(candleColors());
  const line = (o) => ch.addLineSeries(Object.assign({ priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false, lineWidth: 1 }, o));
  k.ma = line({ color: C.ink_2, lineWidth: 2, lineStyle: LC.LineStyle.Dashed, lineType: LC.LineType.WithSteps });
  k.ch20 = line({ color: rgba(C.s1, 0.55), lineType: LC.LineType.WithSteps });
  k.ch30 = line({ color: rgba(C.s1b, 0.55), lineType: LC.LineType.WithSteps });
  k.st20 = line({ color: C.s1, lineWidth: 2, lineStyle: LC.LineStyle.Dotted, lineType: LC.LineType.WithSteps });
  k.st30 = line({ color: C.s1b, lineWidth: 2, lineStyle: LC.LineStyle.Dotted, lineType: LC.LineType.WithSteps });
  k.priceLines = [];
  ch.subscribeCrosshairMove((p) => kLegend(p));
  ch.timeScale().subscribeVisibleTimeRangeChange((r) => {
    if (!r) return;
    // 每天分到的寬度太小時只畫箭頭、不寫文字，避免標籤擠成一團
    const pxPerDay = $("kchart").clientWidth / Math.max(1, (r.to - r.from) / DAY);
    const txt = pxPerDay >= 7;
    if (txt !== S.markerText) { S.markerText = txt; k.candle.setMarkers(markerData()); }
  });
  charts.k = k;
}
function updateK(keepRange) {
  const k = charts.k;
  const vr = keepRange ? k.chart.timeScale().getVisibleRange() : null;
  k.chart.applyOptions({ watermark: { text: `BTC / USDT · ${S.tf === "4h" ? "4H" : "1D"}`, color: rgba(C.ink, 0.045) } });
  k.candles = candleData();
  k.candle.applyOptions(candleColors());
  k.candle.setData(k.candles);
  k.ma.setData($("shMA").checked ? P.lines.ma200.map(([t, v]) => ({ time: L(t), value: v })) : []);
  const ch = $("shCh").checked && S.tf === "4h";
  k.ch20.setData(ch && $("shN20").checked ? P.lines.hi20.map(([t, v]) => ({ time: L(t), value: v })) : []);
  k.ch30.setData(ch && $("shN30").checked ? P.lines.hi30.map(([t, v]) => ({ time: L(t), value: v })) : []);
  const st = $("shStop").checked;
  k.st20.setData(st && $("shN20").checked ? stopLineData("S1-N20") : []);
  k.st30.setData(st && $("shN30").checked ? stopLineData("S1-N30") : []);
  k.candle.setMarkers(markerData());
  k.events = eventIndex();
  // 目前的停損／突破價
  for (const pl of k.priceLines) k.candle.removePriceLine(pl);
  k.priceLines = [];
  for (const s of STRATS) {
    const x = P.status[s];
    if (!x || !$(s === "S1-N20" ? "shN20" : "shN30").checked) continue;
    if (x.state === "long") k.priceLines.push(k.candle.createPriceLine({ price: x.stop, color: C.strat[s], lineWidth: 1, lineStyle: LC.LineStyle.Dashed, axisLabelVisible: true, title: `${SHORT[s]} 停損` }));
    else if (x.state === "flat" && x.next.trend_ok && x.next.width_ok)
      k.priceLines.push(k.candle.createPriceLine({ price: x.next.trigger, color: C.strat[s], lineWidth: 1, lineStyle: LC.LineStyle.SparseDotted, axisLabelVisible: true, title: `${SHORT[s]} 突破價` }));
  }
  if (vr) k.chart.timeScale().setVisibleRange(vr); else setRange(S.range);
  const keys = $("kkeys"); keys.replaceChildren(
    keyItem(C.s1, "S1-N20 進出／停損", "tri"), keyItem(C.s1b, "S1-N30 進出／停損", "tri"), keyItem(C.d, "D 加倉 ▲／減倉 ▼"),
    keyItem(C.ink_2, "200 日均線（前一日）", "dash"), keyItem(rgba(C.s1, .55), "通道上緣（突破價）"));
  kLegend({});
}
function keyItem(color, label, kind) {
  const i = el("i", { class: kind || "", style: kind === "tri" ? `border-bottom-color:${color};color:${color}` : `background:${color};color:${color}` });
  return el("span", {}, i, label);
}
function setRange(days) {
  const k = charts.k; const c = k.candles; if (!c || !c.length) return;
  if (!days) { k.chart.timeScale().fitContent(); return; }
  const to = c[c.length - 1].time;
  k.chart.timeScale().setVisibleRange({ from: to - days * DAY, to: to + (S.tf === "4h" ? 3 * H4 : 2 * DAY) });
}
function kLegend(p) {
  const k = charts.k, box = $("klegend");
  let bar = p && p.time ? p.seriesData.get(k.candle) : null;
  let t = p && p.time;
  if (!bar) { bar = k.candles[k.candles.length - 1]; t = bar && bar.time; }
  if (!bar) return;
  const chg = bar.close / bar.open - 1;
  const parts = [el("b", {}, fmtShifted(t)), `  開 ${num(bar.open, 1)}  高 ${num(bar.high, 1)}  低 ${num(bar.low, 1)}  收 `,
    el("b", {}, num(bar.close, 1)), el("span", { class: cls(chg) }, `  ${pct(chg, 2)}`)];
  const ma = p && p.seriesData && p.seriesData.get(k.ma);
  if (ma && ma.value) parts.push(`  ｜ 200MA ${num(ma.value, 0)}`);
  const ev = k.events && k.events.get(t);
  const kids = [el("div", {}, ...parts)];
  if (ev) for (const s of ev) kids.push(el("div", {}, "• " + s));
  box.replaceChildren(...kids);
}
function zoomTo(from, to) {
  if (S.tf !== "4h") { S.tf = "4h"; segOn("tfSeg", "tf", "4h"); updateK(false); }
  charts.k.chart.timeScale().setVisibleRange({ from: L(from) - 4 * DAY, to: L(to) + 4 * DAY });
  $("kchart").scrollIntoView({ behavior: "smooth", block: "center" });
}

// ---------------------------------------------------------------- 資金、回撤、曝險
function eqSet() {
  const e = S.eq === "paper" ? P.equity.paper : P.equity.backtest;
  if (!e) return null;
  const s1 = new Map(e["S1-N20"].map(([t, v]) => [t, v]));
  for (const [t, v] of e["S1-N30"]) s1.set(t, (s1.get(t) || 0) + v);
  const toPts = (arr) => arr.map(([t, v]) => ({ time: L(t), value: v }));
  return { total: toPts(e.total), s1: [...s1.entries()].sort((a, b) => a[0] - b[0]).map(([t, v]) => ({ time: L(t), value: v })),
           d: toPts(e.D), btc: toPts(e.btc) };
}
function initEq() {
  const ch = LC.createChart($("eqchart"), baseOpts({ localization: { timeFormatter: (t) => fmtShifted(t, false), priceFormatter: (p) => num(p, 0) } }));
  const line = (color, w, style) => ch.addLineSeries({ color, lineWidth: w, lineStyle: style || 0, priceLineVisible: false, lastValueVisible: true });
  const eq = { chart: ch, btc: line(C.btc, 1, LC.LineStyle.Dashed), d: line(C.d, 2), s1: line(C.s1, 2),
               total: ch.addAreaSeries({ lineColor: C.total, topColor: rgba(C.total, 0.16), bottomColor: rgba(C.total, 0.0), lineWidth: 3,
                                         priceLineVisible: false, lastValueVisible: true }) };
  ch.subscribeCrosshairMove((p) => eqLegend(p));
  charts.eq = eq;
  const dd = LC.createChart($("ddchart"), baseOpts({ localization: { timeFormatter: (t) => fmtShifted(t, false), priceFormatter: (p) => (p * 100).toFixed(0) + "%" } }));
  const dl = (color, w, style) => dd.addLineSeries({ color, lineWidth: w, lineStyle: style || 0, priceLineVisible: false, lastValueVisible: false });
  // 後加的畫在上層：BTC 參考線在最底，組合在最上
  charts.dd = { chart: dd, btc: dl(C.btc, 1, LC.LineStyle.Dashed), d: dl(C.d, 2), s1: dl(C.s1, 2), total: dl(C.total, 2), min: {} };
  dd.subscribeCrosshairMove((p) => ddLegend(p));
  const ex = LC.createChart($("expchart"), baseOpts({ localization: { timeFormatter: (t) => fmtShifted(t, false), priceFormatter: (p) => (p * 100).toFixed(0) + "%" } }));
  charts.exp = { chart: ex,
    tot: ex.addLineSeries({ color: C.total, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }),
    d: ex.addAreaSeries({ lineColor: C.d, topColor: rgba(C.d, 0.30), bottomColor: rgba(C.d, 0.05), lineWidth: 2, priceLineVisible: false, lastValueVisible: false }),
    s1: ex.addLineSeries({ color: C.s1, lineWidth: 2, priceLineVisible: false, lastValueVisible: false }) };
  ex.subscribeCrosshairMove((p) => {
    const g = (s) => (p.time ? p.seriesData.get(s) : null);
    const a = g(charts.exp.s1), b = g(charts.exp.d), c = g(charts.exp.tot);
    $("explegend").replaceChildren(p.time && c ? el("span", {}, fmtShifted(p.time, false) + "  合計 ", el("b", {}, pct(c.value, 0, false)),
      `  S1 ${pct(a ? a.value : 0, 0, false)}  D ${pct(b ? b.value : 0, 0, false)}`) : el("span", {}, "S1 名目＋D 持有的 BTC，佔總資金比例"));
  });
  // 三張圖的時間軸連動
  let lock = false;
  const all = [ch, dd, ex];
  for (const a of all) a.timeScale().subscribeVisibleTimeRangeChange((r) => {
    if (lock || !r) return; lock = true;
    for (const b of all) if (b !== a) { try { b.timeScale().setVisibleRange(r); } catch (e) { /* 範圍外 */ } }
    lock = false;
  });
}
function updateEq() {
  const e = eqSet(), eq = charts.eq;
  const empty = !e || e.total.length < 2;
  for (const k of ["total", "s1", "d", "btc"]) eq[k].setData(empty ? [] : e[k]);
  eq.chart.priceScale("right").applyOptions({ mode: $("eqLog").checked ? LC.PriceScaleMode.Logarithmic : LC.PriceScaleMode.Normal });
  eq.data = e;
  if (!empty) eq.chart.timeScale().fitContent();
  // 回撤：各策略各自從自己的高點算
  const ddOf = (arr) => {
    let pk = -Infinity, mn = 0;
    const out = arr.map((x) => { pk = Math.max(pk, x.value); const v = x.value / pk - 1; mn = Math.min(mn, v); return { time: x.time, value: v }; });
    return [out, mn];
  };
  for (const k of ["total", "s1", "d", "btc"]) {
    const [d, mn] = empty ? [[], null] : ddOf(e[k]);
    charts.dd.min[k] = mn;
    charts.dd[k].setData(k === "btc" && !$("ddBtc").checked ? [] : d);
  }
  const mx = (k) => ` 最大 ${pct(charts.dd.min[k], 1, false)}`;
  $("ddkeys").replaceChildren(keyItem(C.total, "組合" + mx("total")), keyItem(C.s1, "S1（N20＋N30）" + mx("s1")), keyItem(C.d, "D" + mx("d")),
    ...($("ddBtc").checked ? [keyItem(C.btc, "BTC 持有" + mx("btc"), "dash")] : []));
  ddLegend({});
  // 曝險（全期，紙上模式只看起算之後）
  const ex = P.exposure.filter((x) => S.eq !== "paper" || x[0] >= P.meta.start);
  charts.exp.s1.setData(ex.map(([t, a]) => ({ time: L(t), value: a })));
  charts.exp.d.setData(ex.map(([t, , b]) => ({ time: L(t), value: b })));
  charts.exp.tot.setData(ex.map(([t, a, b]) => ({ time: L(t), value: a + b })));
  $("expkeys").replaceChildren(keyItem(C.total, "合計"), keyItem(C.s1, "S1 名目"), keyItem(C.d, "D 持有 BTC"));
  $("eqkeys").replaceChildren(keyItem(C.total, "組合"), keyItem(C.s1, "S1（N20＋N30）"), keyItem(C.d, "D"), keyItem(C.btc, "BTC 持有（同資金）", "dash"));
  if (empty) $("eqlegend").replaceChildren(el("span", {}, S.eq === "paper"
    ? `紙上／實盤帳戶自 ${tstr(P.meta.start, false)} 起算，累積到第二天後才會有曲線。` : "沒有資料"));
  else eqLegend({});
  for (const c of [charts.dd.chart, charts.exp.chart]) c.timeScale().fitContent();
}
function ddLegend(p) {
  const dd = charts.dd, showBtc = $("ddBtc").checked;
  const items = [["total", "組合", C.total], ["s1", "S1", C.s1], ["d", "D", C.d]].concat(showBtc ? [["btc", "BTC", C.btc]] : []);
  const key = (color) => el("i", { style: `display:inline-block;width:12px;height:2px;background:${color};vertical-align:middle;margin-right:4px` });
  if (p && p.time) {
    const row = items.map(([k, label, color]) => {
      const v = p.seriesData.get(dd[k]);
      return v ? el("span", { style: "margin-right:10px" }, key(color), label + " ", el("b", {}, pct(v.value, 1, false))) : null;
    });
    $("ddlegend").replaceChildren(el("b", { style: "margin-right:12px" }, fmtShifted(p.time, false)), ...row);
  } else {
    $("ddlegend").replaceChildren(el("span", {}, "把滑鼠移到圖上，查看當天各策略離高點多遠"));
  }
}
function eqLegend(p) {
  const eq = charts.eq, e = eq.data; if (!e || e.total.length < 2) return;
  const pick = (k) => {
    if (p && p.time) { const v = p.seriesData.get(eq[k]); return v ? v.value : null; }
    return e[k][e[k].length - 1].value;
  };
  const t = p && p.time ? p.time : e.total[e.total.length - 1].time;
  const base = e.total[0].value;
  const row = (k, label, color) => {
    const v = pick(k); if (v == null) return null;
    return el("span", { style: "margin-right:12px" }, el("i", { style: `display:inline-block;width:12px;height:2px;background:${color};vertical-align:middle;margin-right:4px` }),
      label + " ", el("b", {}, num(v, 0)), ` (${pct(v / (k === "total" || k === "btc" ? base : e[k][0].value) - 1, 0)})`);
  };
  $("eqlegend").replaceChildren(el("div", {}, el("b", {}, fmtShifted(t, false))),
    el("div", {}, row("total", "組合", C.total), row("s1", "S1", C.s1), row("d", "D", C.d), row("btc", "BTC", C.btc)));
}

// ---------------------------------------------------------------- 自製長條圖與熱力圖
const tip = () => $("tip");
function showTip(ev, rows) {
  const t = tip(); t.replaceChildren(...rows); t.style.display = "block";
  const x = Math.min(ev.clientX + 14, window.innerWidth - t.offsetWidth - 8), y = Math.min(ev.clientY + 14, window.innerHeight - t.offsetHeight - 8);
  t.style.left = x + "px"; t.style.top = y + "px";
}
const hideTip = () => { tip().style.display = "none"; };
const SVG = "http://www.w3.org/2000/svg";
const sv = (tag, a = {}) => { const e = document.createElementNS(SVG, tag); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); return e; };
function barPath(x, y0, y1, w, r) {          // 只有資料端圓角，基線端是直角
  const up = y1 < y0, h = Math.abs(y1 - y0); r = Math.min(r, h / 2, w / 2);
  if (h < 0.5) return `M${x},${y0}h${w}`;
  return up ? `M${x},${y0}V${y1 + r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 + r}V${y0}Z`
            : `M${x},${y0}V${y1 - r}Q${x},${y1} ${x + r},${y1}H${x + w - r}Q${x + w},${y1} ${x + w},${y1 - r}V${y0}Z`;
}
function renderYearly() {
  const box = $("yearly"); box.replaceChildren();
  const Y = P.stats.yearly, years = Object.keys(Y.total);
  const ser = [["組合", Y.total, C.total], ["S1", Y.S1, C.s1], ["D", Y.D, C.d]];
  const W = Math.max(box.clientWidth, 320), Ht = fsHeight(box, 260), pl = 44, pb = 40, pt = 14;
  const vals = ser.flatMap(([, s]) => years.map((y) => s[y] || 0));
  const hi = Math.max(0.1, ...vals), lo = Math.min(-0.05, ...vals);
  const yv = (v) => pt + (hi - v) / (hi - lo) * (Ht - pt - pb);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${Ht}`, height: Ht, role: "img", "aria-label": "逐年報酬長條圖" });
  for (const g of niceTicks(lo, hi, 5)) {
    svg.append(sv("line", { x1: pl, x2: W, y1: yv(g), y2: yv(g), stroke: g === 0 ? C.axis : C.grid, "stroke-width": 1 }));
    const tx = sv("text", { x: pl - 6, y: yv(g) + 4, "text-anchor": "end" }); tx.textContent = Math.round(g * 100) + "%"; svg.append(tx);
  }
  const gw = (W - pl) / years.length, bw = Math.min(18, (gw - 14) / ser.length - 2);
  years.forEach((y, i) => {
    const gx = pl + i * gw + (gw - (bw + 2) * ser.length) / 2;
    ser.forEach(([name, s, color], j) => {
      const v = s[y] || 0, x = gx + j * (bw + 2);
      const p = sv("path", { d: barPath(x, yv(0), yv(v), bw, 4), fill: color, class: "mark" });
      const hit = sv("rect", { x: x - 1, y: pt, width: bw + 2, height: Ht - pt - pb, fill: "transparent" });
      const on = (ev) => showTip(ev, [el("div", {}, el("b", {}, y)), el("div", { class: "r" }, el("i", { style: `background:${color}` }), el("b", {}, pct(v)), " " + name),
        el("div", { class: "r", style: "color:var(--muted)" }, `BTC ${pct(Y.btc[y])}`)]);
      hit.addEventListener("pointermove", on); hit.addEventListener("pointerleave", hideTip);
      svg.append(p, hit);
      if (j === 0) { const t = sv("text", { x: x + bw / 2, y: v >= 0 ? yv(v) - 4 : yv(v) + 12, "text-anchor": "middle", class: "val" }); t.textContent = Math.round(v * 100) + "%"; svg.append(t); }
    });
    const lab = sv("text", { x: pl + i * gw + gw / 2, y: Ht - pb + 15, "text-anchor": "middle" }); lab.textContent = y; svg.append(lab);
    const b = sv("text", { x: pl + i * gw + gw / 2, y: Ht - pb + 29, "text-anchor": "middle" }); b.textContent = "BTC " + pct(Y.btc[y], 0); svg.append(b);
  });
  box.append(svg);
  $("ykeys").replaceChildren(keyItem(C.total, "組合"), keyItem(C.s1, "S1"), keyItem(C.d, "D"));
}
function niceTicks(lo, hi, n) {
  const span = hi - lo, step0 = span / n, mag = Math.pow(10, Math.floor(Math.log10(step0)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0);
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v / step) * step);
  if (!out.includes(0)) out.push(0);
  return out;
}
function heatColor(v) {
  const a = Math.min(1, Math.abs(v) / 0.15);
  return { bg: v === 0 ? C.mid : rgba(v > 0 ? C.pos : C.neg, 0.12 + 0.78 * a), fg: a > 0.55 ? C.on_strong : C.ink };
}
function renderHeat() {
  const box = $("heat"); box.replaceChildren();
  const g = el("div", { class: "heat" });
  g.append(el("div", { class: "h" }, ""));
  for (let m = 1; m <= 12; m++) g.append(el("div", { class: "h" }, m + "月"));
  g.append(el("div", { class: "h" }, "全年"));
  const by = {};
  for (const [y, m, v] of P.stats.monthly) (by[y] = by[y] || {})[m] = v;
  for (const y of Object.keys(by)) {
    g.append(el("div", { class: "h y" }, y));
    for (let m = 1; m <= 12; m++) {
      const v = by[y][m];
      if (v == null) { g.append(el("div", {}, "")); continue; }
      const c = heatColor(v);
      const cell = el("div", { class: "c", style: `background:${c.bg};color:${c.fg}` }, (v * 100).toFixed(0));
      cell.addEventListener("pointermove", (ev) => showTip(ev, [el("div", {}, `${y} 年 ${m} 月`), el("div", {}, el("b", {}, pct(v)), " 組合")]));
      cell.addEventListener("pointerleave", hideTip);
      g.append(cell);
    }
    const yv = P.stats.yearly.total[y];
    const c = heatColor((yv ?? 0) / 4);
    g.append(el("div", { class: "c y", style: `background:${c.bg};color:${c.fg}` }, yv == null ? "" : (yv * 100).toFixed(0) + "%"));
  }
  box.append(g);
}
function renderPnl() {
  const box = $("pnl"); box.replaceChildren();
  const tr = P.trades.filter((t) => !t.open && STRATS.includes(t.strat)).sort((a, b) => a.exit_t - b.exit_t);
  if (!tr.length) return;
  const W = Math.max(box.clientWidth, 320), Ht = fsHeight(box, 230), pl = 44, pb = 24, pt = 10;
  const hi = Math.max(0.02, ...tr.map((t) => t.ret_acct)), lo = Math.min(-0.02, ...tr.map((t) => t.ret_acct));
  const yv = (v) => pt + (hi - v) / (hi - lo) * (Ht - pt - pb);
  const svg = sv("svg", { viewBox: `0 0 ${W} ${Ht}`, height: Ht, role: "img", "aria-label": "S1 逐筆損益" });
  for (const g of niceTicks(lo, hi, 4)) {
    svg.append(sv("line", { x1: pl, x2: W, y1: yv(g), y2: yv(g), stroke: g === 0 ? C.axis : C.grid }));
    const t = sv("text", { x: pl - 6, y: yv(g) + 4, "text-anchor": "end" }); t.textContent = Math.round(g * 100) + "%"; svg.append(t);
  }
  const bw = (W - pl) / tr.length, w = Math.max(1, bw - 2);
  let lastYear = null;
  tr.forEach((t, i) => {
    const x = pl + i * bw + 1, color = t.ret_acct >= 0 ? C.pos : C.neg;
    const p = sv("path", { d: barPath(x, yv(0), yv(t.ret_acct), w, Math.min(4, w / 2)), fill: color, class: "mark" });
    if (t.phase === "paper") { p.setAttribute("stroke", C.ink); p.setAttribute("stroke-width", "1"); }
    const hit = sv("rect", { x: pl + i * bw, y: pt, width: Math.max(bw, 3), height: Ht - pt - pb, fill: "transparent", style: "cursor:pointer" });
    hit.addEventListener("pointermove", (ev) => showTip(ev, [
      el("div", { class: "r" }, el("i", { style: `background:${C.strat[t.strat]}` }), t.strat + (t.phase === "paper" ? "（紙上）" : "")),
      el("div", {}, el("b", {}, pct(t.ret_acct)), ` 帳戶 · ${num(t.pnl, 0)} USDT`),
      el("div", { style: "color:var(--muted)" }, `${tstr(t.entry_t)} → ${tstr(t.exit_t)}`),
      el("div", { style: "color:var(--muted)" }, `價格 ${pct(t.ret_px)} · 子帳戶的 ${pct(t.size, 0, false)}`)]));
    hit.addEventListener("pointerleave", hideTip);
    hit.addEventListener("click", () => zoomTo(t.entry_t, t.exit_t));
    svg.append(p, hit);
    const yr = tstr(t.exit_t).slice(0, 4);
    if (yr !== lastYear) {
      lastYear = yr;
      const lb = sv("text", { x: x, y: Ht - 6 }); lb.textContent = yr; svg.append(lb);
      svg.append(sv("line", { x1: x - 1, x2: x - 1, y1: pt, y2: Ht - pb, stroke: C.grid }));
    }
  });
  box.append(svg);
}

// ---------------------------------------------------------------- 表格
function table(id, head, rows) {
  const t = $(id);
  t.replaceChildren(el("thead", {}, el("tr", {}, head.map(([h, l]) => el("th", { class: l ? "l" : "" }, h)))), el("tbody", {}, rows));
}
function renderStats() {
  const B = P.stats.backtest, Pp = P.stats.paper, T = P.stats.trades_bt;
  const names = [["組合", "total"], ["S1 合計", "S1"], ["　S1-N20", "S1-N20"], ["　S1-N30", "S1-N30"], ["D", "D"], ["BTC 持有", "btc"]];
  const head = [["", true], ["回測 總報酬"], ["年化"], ["Sharpe"], ["最大回撤"], ["紙上 總報酬"], ["紙上 最大回撤"], ["交易數"], ["勝率"], ["平均賺"], ["平均賠"], ["盈虧比"], ["持有中位（天）"]];
  const rows = names.map(([label, k]) => {
    const b = B[k] || {}, p = Pp ? Pp[k] : null, ts = T[k] || null;
    return el("tr", {}, el("td", { class: "l" }, label), el("td", { class: cls(b.total) }, pct(b.total, 0)), el("td", {}, pct(b.cagr)),
      el("td", {}, b.sharpe == null ? "—" : b.sharpe.toFixed(2)), el("td", {}, pct(b.maxdd, 1, false)),
      el("td", { class: cls(p && p.total) }, p ? pct(p.total, 2) : "—"), el("td", {}, p ? pct(p.maxdd, 1, false) : "—"),
      el("td", {}, ts ? ts.n : ""), el("td", {}, ts ? pct(ts.win, 0, false) : ""), el("td", {}, ts ? pct(ts.avg_win) : ""),
      el("td", {}, ts ? pct(ts.avg_loss) : ""), el("td", {}, ts && ts.pf ? ts.pf.toFixed(2) : ""), el("td", {}, ts ? ts.hold_days.toFixed(1) : ""));
  });
  table("statsTable", head, rows);
}
function renderTrades() {
  const fs = $("fStrat").value, fp = $("fPhase").value;
  $("fStrat").style.display = S.tradeTab === "s1" ? "" : "none";
  if (S.tradeTab === "s1") {
    const list = P.trades.filter((t) => (!fs || t.strat === fs) && (!fp || t.phase === fp)).sort((a, b) => b.entry_t - a.entry_t);
    $("tradeCount").textContent = `${list.length} 筆`;
    table("tradeTable", [["策略", true], ["階段", true], ["進場（台灣）", true], ["進場價"], ["出場（台灣）", true], ["出場價"], ["佔子帳戶"], ["BTC"], ["金額 USDT"], ["價格漲跌"], ["帳戶損益"], ["損益 USDT"], ["持有（天）"], ["狀態", true]],
      list.map((t) => el("tr", { class: "click", onclick: () => zoomTo(t.entry_t, t.exit_t || P.meta.last_bar) },
        el("td", { class: "l" }, el("i", { style: `display:inline-block;width:10px;height:2px;background:${C.strat[t.strat]};vertical-align:middle;margin-right:5px` }), t.strat),
        el("td", { class: "l" }, el("span", { class: "pill " + (t.phase === "paper" ? "paper" : "") }, t.phase === "paper" ? "紙上" : "回測")),
        el("td", { class: "l" }, tstr(t.entry_t)), el("td", {}, num(t.entry_px, 1)),
        el("td", { class: "l" }, t.exit_t ? tstr(t.exit_t) : "—"), el("td", {}, num(t.exit_px, 1)),
        el("td", {}, pct(t.size, 0, false)), el("td", {}, num(t.btc, 5)), el("td", {}, num(t.notional, 0)),
        el("td", { class: cls(t.ret_px) }, pct(t.ret_px)), el("td", { class: cls(t.ret_acct) }, pct(t.ret_acct, 2)),
        el("td", { class: cls(t.pnl) }, num(t.pnl, 0)), el("td", {}, (t.hold_h / 24).toFixed(1)),
        el("td", { class: "l" }, t.open ? el("span", { class: "pill open" }, "持倉中") : t.reason))));
  } else {
    const list = P.d_orders.filter((o) => !fp || o.phase === fp).sort((a, b) => b.exec_t - a.exec_t);
    $("tradeCount").textContent = `${list.length} 次`;
    table("tradeTable", [["階段", true], ["動作", true], ["訊號日（UTC 收盤）", true], ["成交（台灣）", true], ["成交價"], ["BTC 比例"], ["變動"], ["分數"]],
      list.map((o) => el("tr", { class: "click", onclick: () => zoomTo(o.exec_t, o.exec_t) },
        el("td", { class: "l" }, el("span", { class: "pill " + (o.phase === "paper" ? "paper" : "") }, o.pending ? "待執行" : o.phase === "paper" ? "紙上" : "回測")),
        el("td", { class: "l" }, (dAct(o) === "加倉" ? "▲ " : "▼ ") + dAct(o)),
        el("td", { class: "l" }, fmtShifted(o.signal_t, false)), el("td", { class: "l" }, tstr(o.exec_t)), el("td", {}, num(o.px, 1)),
        el("td", {}, `${pctw(o.weight_from)} → ${pctw(o.weight_to)}`),
        el("td", {},
          o.weight_from == null ? "—" : `${o.weight_to >= o.weight_from ? "+" : ""}${Math.round((o.weight_to - o.weight_from) * 100)}%`),
        el("td", {}, o.score.toFixed(2)))));
  }
}
function renderLog() {
  const rows = [...P.log].reverse().map((r) => el("tr", {}, el("td", { class: "l" }, r.logged_at), el("td", { class: "l" }, r.strat),
    el("td", { class: "l" }, r.title), el("td", { class: "wrap" }, r.text), el("td", { class: "l" }, r.mode === "live" ? "實盤" : "紙上")));
  table("logTable", [["時間（台灣）", true], ["策略", true], ["指令", true], ["內容", true], ["模式", true]],
    rows.length ? rows : [el("tr", {}, el("td", { class: "l", colspan: 5 }, "還沒有發出過操作指令。每根 4H 收盤後更新時，新的指令會記在這裡。"))]);
  const fr = [...P.fills].reverse().map((f) => el("tr", {}, el("td", { class: "l" }, f.time), el("td", { class: "l" }, f.strat),
    el("td", { class: "l" }, f.side === "buy" ? "買進" : "賣出"), el("td", {}, num(f.price, 1)),
    el("td", {}, f.btc ? num(f.btc, 5) + " BTC" : f.contracts ? num(f.contracts, 2) + " 張" : "—"),
    el("td", {}, f.model_px ? num(f.model_px, 1) : "—"), el("td", { class: cls(f.slip == null ? null : -f.slip) }, f.slip == null ? "—" : pct(f.slip, 3))));
  table("fillTable", [["成交時間", true], ["策略", true], ["方向", true], ["成交價"], ["數量"], ["模型價"], ["滑價"]],
    fr.length ? fr : [el("tr", {}, el("td", { class: "l", colspan: 7 }, "還沒有成交紀錄。實際下單後按上方「記錄成交」輸入。"))]);
}

// ---------------------------------------------------------------- 提示、KPI、頁首
function renderPrompts() {
  const box = $("prompts"); box.replaceChildren();
  const tagText = { action: "要操作", watch: "等待突破", info: "資訊" };
  const order = { action: 0, watch: 1, info: 2 };
  for (const p of [...P.prompts].sort((a, b) => order[a.level] - order[b.level])) {
    const card = el("div", { class: "prompt " + p.level },
      el("div", { class: "head" }, el("span", { class: "key", style: `background:${C.strat[p.strat] || C.ink}` }),
        el("span", { class: "tag" }, (p.level === "action" ? "⚠ " : "") + tagText[p.level]), p.title),
      el("div", { class: "text" }, p.text));
    const st = P.status[p.strat];
    if (p.level === "watch" && st && st.next) {
      const trig = st.next.trigger, price = P.meta.price, gap = trig / price - 1;
      const above = price > trig;
      const fill = above ? 1 : Math.max(0.02, Math.min(1, 1 - gap / 0.08));          // 距離 8% 以上 = 幾乎空
      const gapTxt = above ? "盤中已超過" : `還差 ${num(trig - price, 0)} USDT（${(gap * 100).toFixed(2)}%）`;
      card.append(el("div", { class: "progress" },
        el("div", { class: "bar" }, el("i", { style: `width:${fill * 100}%;background:${C.strat[p.strat]}` })),
        el("div", { class: "lbl" }, el("span", {}, `現價 ${num(price, 1)}`), el("span", {}, `突破價 ${num(trig, 1)}，${gapTxt}`))));
      // 規則看的是 4H 收盤價，盤中碰到不算
      card.append(el("div", { class: "hint-line" }, above
        ? `盤中價格已超過突破價，但要等 ${tstr(st.next.bar_close).slice(5)} 這根 K 線收盤仍高於 ${num(trig, 1)} 才算突破。`
        : `看的是 ${tstr(st.next.bar_close).slice(5)} 這根 4H K 線的收盤價，盤中碰到不算。`));
    }
    if (p.level === "action") {
      let due = null;
      if (p.key && (p.key.endsWith("|entry") || p.key.endsWith("|stop"))) due = P.meta.next_open;
      if (p.key && p.key.endsWith("|rebalance")) { const o = P.d_orders.find((x) => x.pending); due = o ? o.exec_t : null; }
      if (due) card.append(el("div", { class: "due", "data-due": String(due) }));
    }
    if (p.level === "action") {
      const side = p.key && p.key.endsWith("|exit") ? "sell" : p.key && p.key.endsWith("|rebalance") && p.text.includes("賣出") ? "sell" : "buy";
      if (!(p.key && p.key.endsWith("|stop")))
        card.append(el("div", { class: "row" }, el("button", { onclick: () => openFill(p.strat, side, p.ref_price, p.qty) }, "已執行，記錄成交")));
    }
    box.append(card);
  }
  $("promptSub").textContent = `依 ${tstr(P.meta.last_bar + H4)} 收盤的資料計算`;
}
function kpi(label, value, note, color, valueCls) {
  return el("div", { class: "card kpi" }, el("div", { class: "label" }, color ? el("i", { style: `display:inline-block;width:14px;height:3px;border-radius:2px;background:${color}` }) : null, label),
    el("div", { class: "value " + (valueCls || "") }, value), note ? el("div", { class: "note" }, note) : null);
}
function accountNow() {
  const m = P.meta, pe = P.equity.paper, started = !!(pe && pe.total.length);
  const eq = started ? pe.total[pe.total.length - 1][1] : m.capital;
  return { started, eq, chg: eq / m.capital - 1 };
}
function renderKpis() {
  const st = P.status, m = P.meta, price = m.price;
  const { eq: eqNow } = accountNow();
  const s1Long = STRATS.filter((s) => st[s].state === "long");
  const s1Val = s1Long.length ? s1Long.map((s) => `${SHORT[s]} ${num(st[s].btc, 4)} BTC`).join("、") : STRATS.some((s) => st[s].state === "pending") ? "下一根進場" : "空手";
  const s1Note = s1Long.length ? s1Long.map((s) => `${SHORT[s]} 未實現 ${pct(st[s].unreal)}、停損 ${num(st[s].stop, 0)}`).join("；")
    : STRATS.map((s) => `${SHORT[s]} 子帳戶 ${num(st[s].equity, 0)}`).join("、");
  const s1Notional = s1Long.reduce((a, s) => a + st[s].btc * price, 0);
  const dVal = st.D.weight * st.D.equity;
  const watch = STRATS.map((s) => st[s]).filter((x) => x.state === "flat" && x.next.trend_ok && x.next.width_ok);
  const trig = watch.length ? Math.min(...watch.map((x) => x.next.trigger)) : null;
  $("kpis").replaceChildren(
    kpi("S1 部位", s1Val, s1Note, C.s1),
    kpi("D 持有 BTC", pct(st.D.weight, 0, false), `${num(st.D.btc, 4)} BTC · 目標 ${pct(st.D.target, 0, false)} · 子帳戶 ${num(st.D.equity, 0)}`, C.d),
    kpi("總曝險", pct((s1Notional + dVal) / eqNow, 0, false), `S1 ${num(s1Notional, 0)} ＋ D ${num(dVal, 0)} USDT`, null),
    kpi("距 S1 突破價", trig ? pct(trig / price - 1, 1) : "—",
      trig ? `突破價 ${num(trig, 1)}，下一根 4H 收盤高於它就進場` : s1Long.length ? "S1 持倉中" : "濾網未通過，暫不進場", null),
  );
}
// ---------------------------------------------------------------- 主視覺
const counted = new WeakSet();
function countUp(node, to, fmt, ms = 700) {                 // 第一次顯示時從 0 滾到目標值；之後直接更新
  if (counted.has(node) || matchMedia("(prefers-reduced-motion: reduce)").matches) { node.textContent = fmt(to); counted.add(node); return; }
  counted.add(node);
  const t0 = performance.now();
  const step = (now) => {
    const k = Math.min(1, (now - t0) / ms), e = 1 - Math.pow(1 - k, 3);
    node.textContent = fmt(to * e);
    if (k < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
  setTimeout(() => { node.textContent = fmt(to); }, ms + 80);   // 保底：分頁在背景時動畫可能暫停
}
// 頁首大標：有要操作的指令就列出來，沒有就說明下一個檢查點
const watching = (s) => { const x = P.status[s]; return x.state === "flat" && x.next && x.next.trend_ok && x.next.width_ok; };
function renderStatus() {
  const m = P.meta, st = P.status;
  const g = new Date((m.generated_at + TZ) * 1000);
  const day = Math.floor((m.generated_at - m.start) / DAY) + 1;
  $("statusWhen").textContent = `${g.getUTCMonth() + 1} 月 ${g.getUTCDate()} 日 週${"日一二三四五六"[g.getUTCDay()]} · ${hhmm(m.generated_at)} 更新`
    + (day >= 1 ? ` · ${m.mode === "live" ? "正式資金" : "紙上交易"}第 ${day} 天` : "");
  const acts = P.prompts.filter((p) => p.level === "action");
  $("status").classList.toggle("act", acts.length > 0);
  if (acts.length) {
    $("statusTitle").textContent = acts.length === 1 ? acts[0].title : `有 ${acts.length} 件事要處理`;
    $("statusText").replaceChildren(...acts.map((p) => el("span", {}, acts.length > 1 ? `${p.title}：` : "", p.text)),
      el("span", {}, el("a", { href: "#actions" }, "查看操作指令")));
    return;
  }
  $("statusTitle").textContent = "一切照常，無需操作";
  const parts = [`下一個檢查點是 ${tstr(m.next_close).slice(5)} 收盤`];
  const w = STRATS.filter(watching), lg = STRATS.filter((s) => st[s].state === "long");
  const trigs = [...new Set(w.map((s) => st[s].next.trigger))];
  for (const t of trigs) {
    const who = w.filter((s) => st[s].next.trigger === t).map((s) => s).join("、");
    parts.push([`${who} 要收盤高於 `, el("span", { class: "hl" }, num(t, 1)), " 才會進場"]);
  }
  for (const s of lg) parts.push([`${s} 持倉中，停損 `, el("span", { class: "hl" }, num(st[s].stop, 1))]);
  if (!w.length && !lg.length) parts.push("S1 濾網未通過，暫不進場");
  parts.push(`D 維持 BTC ${pct(st.D.weight, 0, false)}`);
  const kids = [];
  parts.forEach((x, i) => kids.push(...[].concat(x), i < parts.length - 1 ? "；" : "。"));
  $("statusText").replaceChildren(el("span", {}, ...kids));
}
function renderHero() {
  const m = P.meta, BS = P.stats.backtest, st = P.status;
  const a = accountNow();
  $("heroLabel").textContent = m.mode === "live" ? "實盤帳戶淨值（模型）· USDT" : "紙上帳戶淨值 · USDT";
  countUp($("heroEq"), a.eq, (v) => num(v, 2));
  const pb = P.equity.paper && P.equity.paper.btc;
  const btcChg = a.started && pb && pb.length ? pb[pb.length - 1][1] / pb[0][1] - 1 : null;
  $("heroDelta").replaceChildren(
    a.started ? el("span", {}, el("b", { class: cls(a.chg) }, `${a.eq - m.capital >= 0 ? "+" : "-"}${num(Math.abs(a.eq - m.capital), 2)} · ${pct(a.chg, 2)}`))
              : el("span", {}, "起算 ", el("b", {}, tstr(m.start, false))),
    el("span", {}, `自 ${tstr(m.start, false).slice(5)} 起算 ${num(m.capital, 0)}`),
    btcChg != null ? el("span", {}, "同期 BTC 持有 ", el("b", {}, pct(btcChg, 2))) : null,
    el("span", {}, "配置 ", el("b", {}, `S1 ${pct(m.weights.S1, 0, false)} · D ${pct(m.weights.D, 0, false)}`)));
  // 走勢：紙上帳戶有兩個點以上就畫紙上，否則畫回測
  const pt = P.equity.paper && P.equity.paper.total;
  const usePaper = pt && pt.length >= 2;
  const src = usePaper ? pt : P.equity.backtest.total;
  $("heroSparkLabel").textContent = usePaper
    ? `紙上帳戶 ${tstr(src[0][0], false).slice(5)} → ${tstr(src[src.length - 1][0] + H4).slice(5)}，每 4 小時一點`
    : `回測：同樣配置自 ${tstr(m.backtest_from, false)} 起，${num(src[0][1], 0)} → ${num(src[src.length - 1][1], 0)} USDT`;
  charts.spark.s.setData(src.map(([t, v]) => ({ time: L(t), value: v })));
  charts.spark.chart.timeScale().fitContent();
  // 三個子帳戶：目前狀態一句話＋回測績效
  const price = m.price;
  const tile = (s) => {
    const x = st[s], isD = s === "D";
    const cap0 = isD ? m.capital * m.weights.D : m.capital * m.weights.S1 / STRATS.length;
    const chg = x.equity / cap0 - 1;
    let kind = "", pill = "空手", state;
    if (isD) {
      kind = "d"; pill = `持有 ${pct(x.weight, 0, false)}`;
      const pend = P.d_orders.find((o) => o.pending);
      state = [el("b", {}, num(x.btc, 5)), ` BTC，最新目標 ${pct(x.target, 0, false)}。`,
        pend ? `${tstr(pend.exec_t).slice(5)} 調到 ${pctw(pend.weight_to)}。` : "相差未達 20%，不調倉；每天 08:00 判斷。"];
    } else if (x.state === "long") {
      kind = "on"; pill = "持倉中";
      state = [`持有 `, el("b", {}, num(x.btc, 5)), ` BTC，未實現 ${pct(x.unreal, 2)}；停損 `, el("b", {}, num(x.stop, 1)), `（距現價 ${pct(x.stop / price - 1, 1)}）。`];
    } else if (x.state === "pending") {
      kind = "on"; pill = "下一根進場";
      state = [`${tstr(P.meta.next_open).slice(5)} 開盤買進約 `, el("b", {}, num(x.btc, 5)), " BTC，成交後掛止損委託。"];
    } else if (watching(s)) {
      kind = "watch"; pill = "等待突破";
      state = ["觸發價 ", el("b", {}, num(x.next.trigger, 1)), `，距現價 ${pct(x.next.trigger / price - 1, 2)}。${tstr(x.next.bar_close).slice(5)} 收盤高於它就在下一根進場。`];
    } else {
      const why = [];
      if (!x.next.trend_ok) why.push("日線在 200 日均線下方");
      if (!x.next.width_ok) why.push(`盤整寬度 ${x.next.width_rank.toFixed(2)}，需 ≤ 0.5`);
      state = [`${why.join("、")}，下一根不會進場。`];
    }
    const b = BS[s] || {};
    return el("div", { class: "card tile " + kind, style: `--c:${C.strat[s]}` },
      el("div", { class: "name" }, el("i", { class: "dot" }), isD ? "D · 日線趨勢" : `S1 · ${SHORT[s]}`, el("span", { class: "pillstate" }, pill)),
      el("div", { class: "big" }, num(x.equity, 2), el("small", { class: cls(chg) }, pct(chg, 2))),
      el("div", { class: "state" }, ...state),
      el("div", { class: "row" }, el("span", {}, "回測年化 ", el("b", {}, pct(b.cagr))),
        el("span", {}, "Sharpe ", el("b", {}, b.sharpe == null ? "—" : b.sharpe.toFixed(2))),
        el("span", {}, "最大回撤 ", el("b", {}, pct(b.maxdd, 1, false)))));
  };
  $("heroTiles").replaceChildren(...STRATS.map(tile), tile("D"));
}
function initSpark() {
  const ch = LC.createChart($("heroSpark"), {
    autoSize: true, handleScroll: false, handleScale: false,
    layout: { background: { type: "solid", color: "transparent" }, textColor: C.muted, attributionLogo: false },
    grid: { vertLines: { visible: false }, horzLines: { visible: false } },
    rightPriceScale: { visible: false }, leftPriceScale: { visible: false },
    timeScale: { visible: false, borderVisible: false, minBarSpacing: 0.01 },
    crosshair: { vertLine: { visible: false, labelVisible: false }, horzLine: { visible: false, labelVisible: false } },
  });
  new ResizeObserver(() => ch.timeScale().fitContent()).observe($("heroSpark"));   // 尺寸改變時重新撐滿
  charts.spark = { chart: ch, s: ch.addAreaSeries({ lineColor: C.s1, topColor: rgba(C.s1, 0.35), bottomColor: rgba(C.s1, 0.0), lineWidth: 2,
                                                     priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }) };
}
function renderHeader() {
  const m = P.meta;
  $("price").textContent = num(m.price, 1);
  $("lastBar").textContent = `${tstr(m.last_bar).slice(5)}–${hhmm(m.last_bar + H4)}`;
  const badge = $("modeBadge");
  badge.textContent = m.mode === "live" ? "正式資金" : "紙上交易"; badge.className = "badge" + (m.mode === "live" ? " live" : "");
  const ban = $("modeBanner");
  if (m.mode === "live") {
    ban.hidden = false; ban.className = "banner live";
    ban.textContent = `正式資金模式：提示的買賣數量以起始資金 ${num(m.capital, 0)} USDT（${tstr(m.start, false)} 起）計算。每次在 OKX 下單後，請按「記錄成交」輸入實際成交價，網站會對照模型算滑價。`;
  } else ban.hidden = true;
  const age = (Date.now() / 1000 - (m.last_bar + H4)) / 3600;          // 距最後一根 K 線收盤幾小時
  const fresh = $("fresh"); fresh.replaceChildren();
  if (m.update && m.update.error) fresh.append(el("span", { class: "warn" }, "⚠ 上次抓資料失敗，顯示的是舊資料"));
  else if (Date.now() / 1000 > m.next_close + (SERVER ? 15 : 90) * 60) fresh.append(el("span", { class: "warn" }, SERVER
    ? `⚠ 資料已 ${age.toFixed(1)} 小時沒更新，排程可能沒在跑`
    : `⚠ 公開版資料已落後 ${age.toFixed(1)} 小時（GitHub 排程延遲），最新提示以本機版為準`));
  fresh.hidden = !fresh.childElementCount;                              // 正常時更新時間已寫在頁首大標上方
  $("btnRefresh").disabled = !SERVER; $("btnFill").disabled = !SERVER; $("btnSettings").disabled = false;
  if (!SERVER) $("btnRefresh").title = "需要用本機伺服器開啟（paper/server.py）";
}
function tick() {
  for (const d of document.querySelectorAll("[data-due]")) {
    const t = +d.dataset.due, left = t - Date.now() / 1000;
    d.textContent = left > 0 ? `⏱ 執行時間 ${tstr(t).slice(5)}，還有 ${Math.floor(left / 3600)} 時 ${pad(Math.floor(left % 3600 / 60))} 分` : `⏱ 已到執行時間（${tstr(t).slice(5)}）`;
    d.classList.toggle("now", left <= 0);
  }
  const m = P.meta, left = m.next_close - Date.now() / 1000;
  $("countdown").textContent = left > 0
    ? `${tstr(m.next_close).slice(11)}（${Math.floor(left / 3600)} 時 ${pad(Math.floor(left % 3600 / 60))} 分）` : `${tstr(m.next_close).slice(11)}（等待更新）`;
}

// ---------------------------------------------------------------- 對話框與 API
async function api(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) throw new Error(j.error || "伺服器錯誤");
  return j;
}
function localInput(t) { const d = new Date((t + TZ) * 1000); return d.toISOString().slice(0, 16); }
function openFill(strat, side, price, qty) {
  $("fS").value = strat || "S1-N20"; $("fSide").value = side || "buy";
  $("fTime").value = localInput(Math.floor(Date.now() / 1000));
  $("fPx").value = price ? Number(price).toFixed(1) : ""; $("fBtc").value = qty ? Number(qty).toFixed(5) : ""; $("fFee").value = ""; $("fNote").value = "";
  $("fErr").textContent = ""; $("dlgFill").showModal();
}
function bindDialogs() {
  $("btnFill").onclick = () => openFill();
  $("fCancel").onclick = () => $("dlgFill").close();
  $("fSave").onclick = async () => {
    try {
      await api("/api/fill", { strat: $("fS").value, side: $("fSide").value, time: $("fTime").value.replace("T", " "), price: $("fPx").value,
                               btc: $("fBtc").value, fee_usdt: $("fFee").value, note: $("fNote").value });
      $("dlgFill").close(); await reloadData();
    } catch (e) { $("fErr").textContent = e.message; }
  };
  $("btnSettings").onclick = () => {
    const m = P.meta;
    $("sMode").value = m.mode; $("sCap").value = m.capital; $("sStart").value = tstr(m.start, false);
    $("sW").value = m.weights.S1; $("sRed").value = m.red_up ? "1" : "0"; $("sErr").textContent = SERVER ? "" : "需要用本機伺服器開啟才能儲存（見 README）。";
    $("sSave").disabled = !SERVER; $("dlgSettings").showModal();
  };
  $("sCancel").onclick = () => $("dlgSettings").close();
  $("sSave").onclick = async () => {
    const w = Number($("sW").value);
    if (!(w >= 0 && w <= 1)) { $("sErr").textContent = "S1 佔比要在 0–1 之間"; return; }
    if ($("sMode").value === "live" && P.meta.mode !== "live" && !confirm("切換到正式資金模式？之後的提示會以你輸入的資金計算，下單仍需你自己在交易所操作。")) return;
    try {
      await api("/api/config", { mode: $("sMode").value, capital_usdt: Number($("sCap").value), start: $("sStart").value,
                                 weights: { S1: w, D: Math.round((1 - w) * 1e6) / 1e6 }, red_up: $("sRed").value === "1" });
      $("dlgSettings").close(); await reloadData();
    } catch (e) { $("sErr").textContent = e.message; }
  };
  $("btnRefresh").onclick = async () => {
    const b = $("btnRefresh"); b.disabled = true; b.textContent = "更新中…";
    try { await api("/api/refresh"); await reloadData(); } catch (e) { alert("更新失敗：" + e.message); }
    b.textContent = "立即更新"; b.disabled = !SERVER;
  };
}
function reloadData() {
  return new Promise((res) => {
    const s = document.createElement("script");
    s.src = "data.js?ts=" + Date.now();
    s.onload = () => { P = window.PAPER_DATA; s.remove(); renderAll(true); res(); };
    s.onerror = () => { s.remove(); res(); };
    document.body.append(s);
  });
}

// ---------------------------------------------------------------- 全螢幕
// 先把卡片放大鋪滿視窗（任何瀏覽器都有效），支援時再進入系統全螢幕
function fsHeight(box, normal) {
  const card = box.closest(".card");
  return card && card.classList.contains("max") ? Math.max(normal, window.innerHeight - 190) : normal;
}
function setMax(card) {
  for (const c of document.querySelectorAll(".card.max")) if (c !== card) c.classList.remove("max");
  if (card) card.classList.add("max");
  document.body.classList.toggle("has-max", !!card);
  for (const b of document.querySelectorAll("[data-fs]")) {
    const on = b.closest(".card") === card;
    b.textContent = on ? "✕" : "⛶"; b.title = on ? "離開全螢幕（Esc）" : "全螢幕";
  }
  setTimeout(() => { renderYearly(); renderPnl(); }, 80);           // 自製 SVG 依新尺寸重畫；圖表函式庫會自動調整
}
function bindFullscreen() {
  const real = () => document.fullscreenEnabled && document.documentElement.requestFullscreen;
  for (const b of document.querySelectorAll("[data-fs]")) {
    b.onclick = () => {
      const card = b.closest(".card");
      if (card.classList.contains("max")) {
        setMax(null);
        if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
      } else {
        setMax(card);
        if (real() && !document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
      }
    };
  }
  $("btnFull").onclick = () => {
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    else if (real()) document.documentElement.requestFullscreen().catch(() => {});
  };
  $("btnFull").hidden = !real();
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && document.querySelector(".card.max")) setMax(null); });
  document.addEventListener("fullscreenchange", () => {
    if (!document.fullscreenElement && document.querySelector(".card.max")) setMax(null);   // 用瀏覽器的 Esc 離開時一起還原
    $("btnFull").textContent = document.fullscreenElement && !document.querySelector(".card.max") ? "✕" : "⛶";
  });
}

// ---------------------------------------------------------------- 區塊導覽
function bindNav() {
  const hdr = document.querySelector("header.top");
  new ResizeObserver(() => document.documentElement.style.setProperty("--hdr", hdr.offsetHeight + "px")).observe(hdr);
  const links = [...document.querySelectorAll("#secnav a")];
  const targets = links.map((a) => document.querySelector(a.getAttribute("href")));
  const mark = () => {                                       // 標示目前所在區塊：最後一個頂端已經過畫面 30% 的區塊
    const y = window.innerHeight * 0.3; let cur = 0;
    targets.forEach((t, i) => { if (t && t.getBoundingClientRect().top < y) cur = i; });
    links.forEach((a, i) => a.classList.toggle("on", i === cur));
  };
  let raf = 0;
  window.addEventListener("scroll", () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(mark); }, { passive: true });
  mark();
}

// ---------------------------------------------------------------- 控制項
function segOn(id, attr, val) { for (const b of $(id).querySelectorAll("button")) b.classList.toggle("on", b.dataset[attr] === String(val)); }
function bindControls() {
  $("tfSeg").onclick = (e) => { const v = e.target.dataset.tf; if (!v) return; S.tf = v; segOn("tfSeg", "tf", v); updateK(false); };
  $("rangeSeg").onclick = (e) => { const v = e.target.dataset.r; if (v == null) return; S.range = +v; segOn("rangeSeg", "r", v); setRange(S.range); };
  for (const id of ["shN20", "shN30", "shD", "shStop", "shCh", "shMA"]) $(id).onchange = () => updateK(true);
  $("eqSeg").onclick = (e) => { const v = e.target.dataset.e; if (!v) return; S.eq = v; segOn("eqSeg", "e", v); updateEq(); };
  $("eqLog").onchange = () => updateEq();
  $("ddBtc").onchange = () => updateEq();
  $("tradeTab").onclick = (e) => { const v = e.target.dataset.t; if (!v) return; S.tradeTab = v; segOn("tradeTab", "t", v); renderTrades(); };
  $("fStrat").onchange = renderTrades; $("fPhase").onchange = renderTrades;
  $("btnTheme").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("theme", next); } catch (e) { /* 無痕模式 */ }
    applyTheme();
  };
  let rt; window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => { renderYearly(); renderPnl(); }, 150); });
}
function applyTheme() {
  readColors();
  const th = { layout: { background: { type: "solid", color: C.surface }, textColor: C.ink_2 },
              crosshair: { vertLine: { color: rgba(C.muted, .6), labelBackgroundColor: C.surface_3 }, horzLine: { color: rgba(C.muted, .6), labelBackgroundColor: C.surface_3 } },
              grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
              rightPriceScale: { borderColor: C.axis }, timeScale: { borderColor: C.axis } };
  for (const c of [charts.k.chart, charts.eq.chart, charts.dd.chart, charts.exp.chart]) c.applyOptions(th);
  const k = charts.k;
  k.ma.applyOptions({ color: C.ink_2 }); k.ch20.applyOptions({ color: rgba(C.s1, .55) }); k.ch30.applyOptions({ color: rgba(C.s1b, .55) });
  k.st20.applyOptions({ color: C.s1 }); k.st30.applyOptions({ color: C.s1b });
  const e = charts.eq; e.total.applyOptions({ lineColor: C.total, topColor: rgba(C.total, .16), bottomColor: rgba(C.total, 0) });
  charts.spark.s.applyOptions({ lineColor: C.s1, topColor: rgba(C.s1, .35), bottomColor: rgba(C.s1, 0) }); e.s1.applyOptions({ color: C.s1 }); e.d.applyOptions({ color: C.d }); e.btc.applyOptions({ color: C.btc });
  charts.dd.total.applyOptions({ color: C.total }); charts.dd.s1.applyOptions({ color: C.s1 }); charts.dd.d.applyOptions({ color: C.d }); charts.dd.btc.applyOptions({ color: C.btc });
  charts.exp.tot.applyOptions({ color: C.total }); charts.exp.s1.applyOptions({ color: C.s1 });
  charts.exp.d.applyOptions({ lineColor: C.d, topColor: rgba(C.d, .3), bottomColor: rgba(C.d, .05) });
  renderAll(true);
}

function renderIntro() {
  const m = P.meta, B = P.stats.backtest, st = P.status;
  const s1cap = m.capital * m.weights.S1, dcap = m.capital * m.weights.D;
  const item = (label, v) => el("span", {}, label + " ", el("b", {}, v));
  const perf = (b) => [item("回測年化", pct(b.cagr)), item("Sharpe", b.sharpe == null ? "—" : b.sharpe.toFixed(2)), item("最大回撤", pct(b.maxdd, 1, false))];
  const s1Now = STRATS.map((s) => st[s].state === "long" ? `${SHORT[s]} 持倉` : st[s].state === "pending" ? `${SHORT[s]} 待進場` : `${SHORT[s]} 空手`).join("、");
  $("introS1").replaceChildren(item("配置", `${num(s1cap, 0)} USDT（N20、N30 各 ${num(s1cap / 2, 0)}）`), ...perf(B.S1 || {}), item("目前", s1Now));
  $("introD").replaceChildren(item("配置", `${num(dcap, 0)} USDT`), ...perf(B.D || {}), item("目前", `持有 BTC ${pct(st.D.weight, 0, false)}`));
}
function renderAll(keep) {
  renderHeader(); renderStatus(); renderIntro(); renderHero(); renderPrompts(); renderKpis();
  updateK(keep); updateEq(); renderYearly(); renderHeat(); renderPnl(); renderStats(); renderTrades(); renderLog(); tick();
}

// ---------------------------------------------------------------- 啟動
(function boot() {
  if (!P) { document.body.prepend(el("div", { class: "banner" }, "找不到 data.js：請先執行 paper/engine.py。")); return; }
  const intro = $("intro");                                               // 記住策略說明是否收合
  try { if (localStorage.getItem("introClosed") === "1") intro.open = false; } catch (e) { /* 忽略 */ }
  intro.addEventListener("toggle", () => { try { localStorage.setItem("introClosed", intro.open ? "0" : "1"); } catch (e) { /* 忽略 */ } });
  readColors(); initSpark(); initK(); initEq(); bindControls(); bindDialogs(); bindFullscreen(); bindNav(); renderAll(false);
  requestAnimationFrame(() => setTimeout(() => {                          // 等圖表排版完成再設定範圍
    setRange(S.range);
    for (const c of [charts.eq.chart, charts.dd.chart, charts.exp.chart]) c.timeScale().fitContent();
  }, 60));
  setInterval(tick, 1000);
  // 沒有手動選過日夜時跟著系統切換
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", (e) => {
    let saved = null; try { saved = localStorage.getItem("theme"); } catch (err) { /* 無痕模式 */ }
    if (saved) return;
    document.documentElement.dataset.theme = e.matches ? "dark" : "light"; applyTheme();
  });
  // 沒有本機伺服器（例如 GitHub Pages）→ 唯讀展示版：隱藏需要伺服器的操作
  const setStatic = () => { document.body.classList.add("static"); $("staticBanner").hidden = false; renderHeader(); };
  fetch("api/ping").then((r) => (r.ok ? r.json() : null)).then((j) => { SERVER = !!(j && j.ok); if (SERVER) renderHeader(); else setStatic(); }).catch(setStatic);
  // 每 5 分鐘重讀一次資料（排程每 4 小時更新 data.js）
  setInterval(() => { if (!document.querySelector("dialog[open]")) reloadData(); }, 5 * 60 * 1000);
})();
