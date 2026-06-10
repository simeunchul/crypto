"use strict";

// ── auth: ?token= → localStorage. localhost 는 토큰 없어도 됨.
const urlToken = new URLSearchParams(location.search).get("token");
if (urlToken) localStorage.setItem("apiToken", urlToken);
const TOKEN = localStorage.getItem("apiToken") || "";
const ORIGIN = location.origin;

function authHeaders() {
  return TOKEN ? { Authorization: "Bearer " + TOKEN } : {};
}
async function api(path, opts = {}) {
  const res = await fetch(ORIGIN + path, {
    ...opts,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...(opts.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return res.json();
}

// ── element refs
const $ = (id) => document.getElementById(id);
const envBadge = $("env-badge"), connEl = $("conn"), stateEl = $("state");
const uptimeEl = $("uptime"), tickEl = $("tick");
const presetSel = $("preset"), btnStart = $("btn-start"), btnStop = $("btn-stop");
const presetDesc = $("preset-desc"), msgEl = $("msg");
const walletEl = $("wallet"), upnlEl = $("upnl"), pnlEl = $("pnl"), pnlPctEl = $("pnl-pct");
const posBody = document.querySelector("#positions tbody"), posCount = $("pos-count");
const logsEl = $("logs");

let presetMap = {};
let currentEnv = "testnet";
let seenLogTs = new Set();

// ── format helpers
const fmt = (n, d = 2) => (n == null || isNaN(n)) ? "—" :
  Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
const signClass = (n) => n == null ? "" : (n > 0 ? "pos" : n < 0 ? "neg" : "");
function fmtUptime(s) {
  if (s == null) return "—";
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60;
  return h > 0 ? `${h}h ${m}m` : m > 0 ? `${m}m ${ss}s` : `${ss}s`;
}

// ── rendering
function renderEnv(env) {
  currentEnv = env;
  const main = env === "mainnet";
  envBadge.textContent = main ? "MAINNET · 실거래" : "TESTNET";
  envBadge.className = "badge " + (main ? "badge-mainnet" : "badge-testnet");
}
function renderState(st) {
  const s = (st || "idle").toLowerCase();
  stateEl.textContent = s.toUpperCase();
  stateEl.className = "state state-" + s;
  const running = (s === "running" || s === "starting");
  btnStart.disabled = running;
  btnStop.disabled = !running;
  presetSel.disabled = running;
}
function renderStatus(st) {
  if (!st) return;
  if (st.env) renderEnv(st.env);
  renderState(st.state);
  uptimeEl.textContent = fmtUptime(st.uptime_sec);
  tickEl.textContent = st.tick != null ? st.tick : "—";
  if (st.wallet_balance != null) walletEl.textContent = fmt(st.wallet_balance);
  if (st.unrealized_pnl != null) {
    upnlEl.textContent = fmt(st.unrealized_pnl);
    upnlEl.className = "big " + signClass(st.unrealized_pnl);
  }
  if (st.pnl != null) {
    pnlEl.textContent = (st.pnl >= 0 ? "+" : "") + fmt(st.pnl);
    pnlEl.className = "big " + signClass(st.pnl);
    pnlPctEl.textContent = (st.pnl_pct >= 0 ? "+" : "") + fmt(st.pnl_pct) + "%";
  }
  renderPositions(st.positions || []);
}
function renderPositions(positions) {
  const open = positions.filter((p) => p.position !== 0);
  posCount.textContent = open.length ? `(${open.length})` : "";
  if (!open.length) {
    posBody.innerHTML = `<tr><td colspan="7" class="muted center">보유 포지션 없음</td></tr>`;
    return;
  }
  posBody.innerHTML = open.map((p) => {
    const tag = p.side === "LONG" ? "tag-long" : p.side === "SHORT" ? "tag-short" : "tag-flat";
    const peak = p.position === 1 ? p.high_water : p.low_water;
    let dist = "—";
    if (peak && p.mark_price) {
      const d = p.position === 1 ? (p.mark_price - peak) / peak * 100 : (peak - p.mark_price) / peak * 100;
      dist = `<span class="${signClass(d)}">${d >= 0 ? "+" : ""}${fmt(d)}%</span>`;
    }
    const sig = p.target_signal === 1 ? "▲ buy" : p.target_signal === -1 ? "▼ sell" : "—";
    return `<tr>
      <td>${p.symbol}</td>
      <td><span class="tag ${tag}">${p.side}</span></td>
      <td class="r">${p.weight != null ? fmt(p.weight * 100, 1) + "%" : "—"}</td>
      <td class="r">${fmt(p.entry_price)}</td>
      <td class="r">${fmt(p.mark_price)}</td>
      <td class="r">${dist}</td>
      <td>${sig}</td></tr>`;
  }).join("");
}

function renderLogs(logs) {
  if (!logs || !logs.length) return;
  const frag = [];
  for (const l of logs) {
    if (seenLogTs.has(l.ts + l.msg)) continue;
    seenLogTs.add(l.ts + l.msg);
    const t = (l.ts || "").slice(11, 19);
    frag.push(`<div class="line lv-${l.level}"><span class="t">${t}</span> ${escapeHtml(l.msg)}</div>`);
  }
  if (!frag.length) return;
  logsEl.insertAdjacentHTML("beforeend", frag.join(""));
  while (logsEl.childElementCount > 600) logsEl.removeChild(logsEl.firstChild);
  logsEl.scrollTop = logsEl.scrollHeight;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

// ── live: WebSocket + balance polling
function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const tq = TOKEN ? "?token=" + encodeURIComponent(TOKEN) : "";
  const ws = new WebSocket(`${proto}://${location.host}/ws${tq}`);
  ws.onopen = () => { connEl.textContent = "● 실시간 연결됨"; connEl.className = "conn conn-on"; };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "tick") { renderStatus(m.status); renderLogs(m.logs); }
  };
  ws.onclose = () => {
    connEl.textContent = "● 재연결 중…"; connEl.className = "conn conn-off";
    setTimeout(connectWS, 3000);
  };
  ws.onerror = () => ws.close();
}

async function pollBalance() {
  try {
    const r = await api("/api/balance");
    const b = r.balance;
    if (b) {
      walletEl.textContent = fmt(b.total_wallet_balance);
      upnlEl.textContent = fmt(b.total_unrealized_pnl);
      upnlEl.className = "big " + signClass(b.total_unrealized_pnl);
    }
  } catch (e) { /* idle/no-keys */ }
}

// ── presets + controls
async function loadPresets() {
  try {
    const r = await api("/api/presets");
    presetMap = {};
    presetSel.innerHTML = "";
    for (const p of r.presets) {
      presetMap[p.key] = p;
      const opt = document.createElement("option");
      opt.value = p.key; opt.textContent = p.label;
      presetSel.appendChild(opt);
    }
    presetSel.value = r.default || (r.presets[0] && r.presets[0].key);
    updatePresetDesc();
  } catch (e) { setMsg("프리셋 로드 실패: " + e.message, true); }
}
function updatePresetDesc() {
  const p = presetMap[presetSel.value];
  if (!p) return;
  const c = p.config;
  presetDesc.textContent = `${p.description}  ·  ${c.symbols.length}종 · ${c.interval} · lev ${c.leverage}x`;
}
function setMsg(text, isErr) {
  msgEl.textContent = text || "";
  msgEl.className = "msg " + (isErr ? "err" : "ok");
}

async function doStart() {
  const preset = presetSel.value;
  setMsg("");
  try {
    await api("/api/start", { method: "POST", body: JSON.stringify({ preset }) });
    setMsg("봇 시작됨.", false);
  } catch (e) { setMsg("시작 실패: " + e.message, true); }
}
async function doStop() {
  setMsg("");
  try {
    const r = await api("/api/stop", { method: "POST" });
    setMsg(r.message || "정지 요청됨.", false);
  } catch (e) { setMsg("정지 실패: " + e.message, true); }
}

// mainnet 일 때 실거래 확인 모달
btnStart.onclick = () => {
  if (currentEnv === "mainnet") {
    const p = presetMap[presetSel.value];
    $("confirm-body").innerHTML =
      `<b style="color:#ff5b6e">실거래(MAINNET)</b> 계정에서 봇을 시작합니다.<br><br>` +
      `구성: <b>${p ? p.label : presetSel.value}</b><br>진짜 돈으로 주문이 실행됩니다.<br><br>계속하시겠습니까?`;
    $("confirm").classList.remove("hidden");
  } else {
    doStart();
  }
};
$("confirm-ok").onclick = () => { $("confirm").classList.add("hidden"); doStart(); };
$("confirm-cancel").onclick = () => $("confirm").classList.add("hidden");
btnStop.onclick = doStop;
presetSel.onchange = updatePresetDesc;

$("btn-pair").onclick = async () => {
  try {
    const p = await api("/api/pairing");
    $("pair-url").textContent = p.url;
    $("pair-token").textContent = p.token;
    $("pair").classList.remove("hidden");
  } catch (e) { setMsg("연결 정보는 PC 앱에서만 볼 수 있습니다.", true); }
};
$("pair-close").onclick = () => $("pair").classList.add("hidden");

// ── init
(async function init() {
  await loadPresets();
  try { const h = await api("/api/health"); renderEnv(h.env); } catch (e) {}
  try { renderStatus(await api("/api/status")); } catch (e) {}
  await pollBalance();
  setInterval(pollBalance, 10000);
  connectWS();
})();
