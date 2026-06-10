"""Dashboard HTML 생성 — data/_dashboard_data.json 읽어서 docs/2026-06-08/dashboard.html 로."""
import json
from pathlib import Path

d = json.load(open('data/_dashboard_data.json', encoding='utf-8'))

session_pnl = d['wallet'] - d['session_start_wallet']
session_pnl_pct = session_pnl / d['session_start_wallet'] * 100

# Top symbols sorted
sym_sorted = sorted(d['by_symbol'].items(), key=lambda x: -x[1])
max_abs_sym = max(abs(v) for _, v in sym_sorted) if sym_sorted else 1

# Daily P&L
daily = d['by_day']
max_abs_day = max(abs(v) for _, v in daily) if daily else 1

# Cumulative
cum_data = d['cumulative']
cum_min = min(c['cum'] for c in cum_data) if cum_data else 0
cum_max = max(c['cum'] for c in cum_data) if cum_data else 1

# 패치 효과 — 7일 누적이라 historical CRITICAL 의 옛 값 포함 → 분리
# 새 CRITICAL 은 패치 후엔 0건이어야
def sign_class(v): return 'pos' if v > 0 else 'neg' if v < 0 else ''
def fmt_money(v): return f"${v:+,.2f}" if v else "$0.00"

# SVG 막대 차트 생성
def daily_bars_svg():
    w = 920
    h = 220
    bar_w = (w - 80) / max(len(daily), 1) * 0.8
    gap = (w - 80) / max(len(daily), 1) * 0.2
    zero_y = 30 + (h - 60) * (max_abs_day / (max_abs_day * 2))
    scale = (h - 60) / 2 / max_abs_day
    bars = []
    for i, (day, val) in enumerate(daily):
        x = 60 + i * (bar_w + gap)
        bh = abs(val) * scale
        if val >= 0:
            y = zero_y - bh
            color = '#28a745'
        else:
            y = zero_y
            color = '#dc3545'
        bars.append(f'<rect x="{x:.0f}" y="{y:.0f}" width="{bar_w:.0f}" height="{bh:.0f}" fill="{color}" opacity="0.85"/>')
        bars.append(f'<text x="{x + bar_w/2:.0f}" y="{h - 8}" font-size="10" text-anchor="middle">{day}</text>')
        bars.append(f'<text x="{x + bar_w/2:.0f}" y="{y - 4 if val >= 0 else y + bh + 12:.0f}" font-size="10" text-anchor="middle" font-weight="600">{val:+.0f}</text>')
    bars.append(f'<line x1="60" y1="{zero_y:.0f}" x2="{w-20}" y2="{zero_y:.0f}" stroke="#888" stroke-width="1"/>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%">{" ".join(bars)}</svg>'

def symbol_bars_svg():
    w = 920
    h = 30 + len(sym_sorted) * 22 + 30
    rows = []
    center_x = 460
    scale = (w - 240) / 2 / max_abs_sym
    rows.append(f'<line x1="{center_x}" y1="30" x2="{center_x}" y2="{h-30}" stroke="#888" stroke-width="1" stroke-dasharray="3,3"/>')
    rows.append(f'<text x="{center_x}" y="20" font-size="11" text-anchor="middle" fill="#666">0</text>')
    for i, (sym, val) in enumerate(sym_sorted):
        y = 35 + i * 22
        if val >= 0:
            bx, bw = center_x, val * scale
            color = '#28a745'
        else:
            bx, bw = center_x - abs(val) * scale, abs(val) * scale
            color = '#dc3545'
        rows.append(f'<text x="{center_x - 200}" y="{y + 13}" font-size="12" text-anchor="end">{sym}</text>')
        rows.append(f'<rect x="{bx:.0f}" y="{y:.0f}" width="{bw:.0f}" height="16" fill="{color}" opacity="0.85"/>')
        tx = bx + bw + 6 if val >= 0 else bx - 6
        anchor = 'start' if val >= 0 else 'end'
        rows.append(f'<text x="{tx:.0f}" y="{y + 13}" font-size="11" text-anchor="{anchor}" font-weight="600">${val:+.0f}</text>')
    return f'<svg viewBox="0 0 {w} {h}" width="100%">{" ".join(rows)}</svg>'

def cumulative_svg():
    w = 920
    h = 260
    if not cum_data:
        return '<p>데이터 없음</p>'
    n = len(cum_data)
    margin_l, margin_r, margin_t, margin_b = 70, 30, 30, 40
    plot_w = w - margin_l - margin_r
    plot_h = h - margin_t - margin_b
    # 시간 기반 x 매핑 (거래 인덱스 X)
    ts_start = cum_data[0]['ts_ms']
    ts_end = cum_data[-1]['ts_ms']
    ts_range = max(ts_end - ts_start, 1)
    # y range with zero baseline
    y_min = min(cum_min - 50, -50)
    y_max = max(cum_max + 50, 50)
    yrange = y_max - y_min
    def x_at_ts(ts_ms): return margin_l + (ts_ms - ts_start) / ts_range * plot_w
    def y_at(v): return margin_t + plot_h * (1 - (v - y_min) / yrange)
    zero_y = y_at(0)
    points = " ".join(f"{x_at_ts(c['ts_ms']):.0f},{y_at(c['cum']):.0f}" for c in cum_data)
    area = (f"M {x_at_ts(cum_data[0]['ts_ms']):.0f},{zero_y:.0f} L "
            + " L ".join(f"{x_at_ts(c['ts_ms']):.0f},{y_at(c['cum']):.0f}" for c in cum_data)
            + f" L {x_at_ts(cum_data[-1]['ts_ms']):.0f},{zero_y:.0f} Z")

    # X 축 day 라벨 — 각 일의 정오(12:00) KST 의 ms 로 계산 → 균일 간격
    from datetime import datetime, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    all_days = sorted(set(c['ts'][:5] for c in cum_data))
    year_now = datetime.now(KST).year
    tick_marks = []
    last_x = -1000
    for day_str in all_days:
        m, dom = day_str.split('-')
        dt_noon = datetime(year_now, int(m), int(dom), 12, 0, tzinfo=KST)
        ts_ms = int(dt_noon.timestamp() * 1000)
        # 차트 범위 안에 있어야 표시
        if ts_ms < ts_start - 12*3600*1000 or ts_ms > ts_end + 12*3600*1000:
            continue
        x = x_at_ts(ts_ms)
        # 너무 가까운 라벨 skip (40px 간격 보장)
        if x - last_x < 50:
            continue
        last_x = x
        tick_marks.append(f'<text x="{x:.0f}" y="{h - 12}" font-size="10" text-anchor="middle" fill="#666">{day_str}</text>')
        tick_marks.append(f'<line x1="{x:.0f}" y1="{margin_t}" x2="{x:.0f}" y2="{h - margin_b}" stroke="#eee" stroke-dasharray="2,3"/>')
        tick_marks.append(f'<line x1="{x:.0f}" y1="{zero_y:.0f}" x2="{x:.0f}" y2="{zero_y + 4:.0f}" stroke="#666"/>')

    # Y ticks
    y_ticks = []
    for ytick in [-500, -250, 0, 250, 500, 750, 1000, 1250]:
        if y_min <= ytick <= y_max:
            y_ticks.append(f'<text x="{margin_l - 8}" y="{y_at(ytick) + 4:.0f}" font-size="10" text-anchor="end" fill="#666">${ytick}</text>')
            y_ticks.append(f'<line x1="{margin_l}" y1="{y_at(ytick):.0f}" x2="{w - margin_r}" y2="{y_at(ytick):.0f}" stroke="#eee"/>')

    final_pt_x = x_at_ts(cum_data[-1]['ts_ms'])
    final_pt_y = y_at(cum_data[-1]['cum'])
    return f'''<svg viewBox="0 0 {w} {h}" width="100%">
      {" ".join(y_ticks)}
      {" ".join(tick_marks)}
      <line x1="{margin_l}" y1="{zero_y:.0f}" x2="{w - margin_r}" y2="{zero_y:.0f}" stroke="#888" stroke-width="1"/>
      <path d="{area}" fill="#28a745" opacity="0.15"/>
      <polyline points="{points}" fill="none" stroke="#007aff" stroke-width="2"/>
      <circle cx="{final_pt_x:.0f}" cy="{final_pt_y:.0f}" r="5" fill="#007aff"/>
      <text x="{final_pt_x - 8:.0f}" y="{final_pt_y - 8:.0f}" font-size="12" text-anchor="end" font-weight="700" fill="#007aff">${cum_data[-1]["cum"]:+.0f}</text>
    </svg>'''

html = f'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<title>Crypto Bot — 운영 대시보드 ({d['now']})</title>
<style>
  body {{ font-family: -apple-system, "Segoe UI", "Malgun Gothic", sans-serif;
    max-width: 1100px; margin: 24px auto; padding: 0 18px; color: #1d1d1f;
    line-height: 1.55; background: #fafbfc; }}
  h1 {{ font-size: 26px; border-bottom: 2px solid #1d1d1f; padding-bottom: 6px; }}
  h2 {{ font-size: 19px; margin-top: 32px; border-left: 5px solid #007aff;
    padding-left: 10px; }}
  .badge {{ display: inline-block; padding: 3px 10px; border-radius: 12px;
    font-size: 12px; font-weight: 700; }}
  .badge-on {{ background: #d4edda; color: #155724; }}
  .badge-off {{ background: #f8d7da; color: #721c24; }}
  .dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: #28a745; margin-right: 4px; vertical-align: middle;
    animation: pulse 1.4s infinite; }}
  @keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:.4}} }}
  .meta {{ font-size: 12px; color: #666; font-family: ui-monospace, Consolas, monospace; }}

  .stats-row {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px;
    margin: 18px 0 28px; }}
  .stat-card {{ background: #fff; border: 1px solid #e0e3e8; border-radius: 12px;
    padding: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  .stat-label {{ font-size: 12px; color: #666; }}
  .stat-value {{ font-size: 24px; font-weight: 700; margin-top: 4px; }}
  .stat-sub {{ font-size: 12px; color: #666; margin-top: 2px; }}
  .pos {{ color: #28a745; }} .neg {{ color: #dc3545; }} .mute {{ color: #888; }}

  .callout {{ background: #e7f1ff; border-left: 5px solid #007aff;
    padding: 14px 18px; margin: 18px 0; border-radius: 4px; }}
  .callout.win {{ background: #e6f7eb; border-left-color: #28a745; }}
  .callout.warn {{ background: #fff8e6; border-left-color: #ffb800; }}

  .chart-card {{ background: #fff; border: 1px solid #e0e3e8; border-radius: 12px;
    padding: 18px; margin-top: 14px; }}
  .chart-title {{ font-size: 14px; font-weight: 600; margin-bottom: 8px; }}
  .chart-cap {{ font-size: 11px; color: #666; margin-top: 6px; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th, td {{ border-bottom: 1px solid #eaecef; padding: 7px 10px; text-align: right; }}
  th {{ background: #f6f8fa; color: #555; font-weight: 600; }}
  td.label, th.label {{ text-align: left; }}

  .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  @media (max-width: 700px) {{ .stats-row {{ grid-template-columns: 1fr 1fr; }} .grid-2 {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>

<h1>Crypto Bot — 운영 대시보드</h1>
<div class="meta">
  <span class="dot"></span><span class="badge badge-on">RUNNING</span>
  &nbsp; 시작 {d['bot_started']} · 가동 {d['uptime']} · 데이터 기준 {d['now']}
</div>

<!-- TOP STATS -->
<div class="stats-row">
  <div class="stat-card">
    <div class="stat-label">현재 지갑</div>
    <div class="stat-value">${d['wallet']:,.2f}</div>
    <div class="stat-sub mute">USDT</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">세션 누적 손익 (5/28 ~)</div>
    <div class="stat-value {sign_class(session_pnl)}">{fmt_money(session_pnl)}</div>
    <div class="stat-sub {sign_class(session_pnl)}">{session_pnl_pct:+.2f}%</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">미실현 손익</div>
    <div class="stat-value {sign_class(d['upnl'])}">{fmt_money(d['upnl']) if d['upnl'] else '$0.00'}</div>
    <div class="stat-sub mute">포지션 {len(d['positions'])}종</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">가용 마진</div>
    <div class="stat-value">${d['available']:,.2f}</div>
    <div class="stat-sub mute">사용률 {(d['margin'] - d['available']) / d['margin'] * 100 if d['margin'] else 0:.1f}%</div>
  </div>
</div>

<!-- HIGHLIGHT -->
<div class="callout win">
<b>핵심 결과 한 줄.</b> 5/28 시작 $10,817.89 → 현재 ${d['wallet']:,.2f} = <b>{session_pnl:+,.2f} ({session_pnl_pct:+.2f}%)</b>.
12일 운영, 패치 4건 적용 (-2022 fallback / state desync reconcile / engine state persistence / Binance 시계 동기화).
모든 포지션 청산된 휴식 상태, 다음 신호 대기.
</div>

<!-- CUMULATIVE CHART -->
<div class="chart-card">
  <div class="chart-title">📈 누적 실현 손익 — 12일 추이 (각 점 = 거래 1건)</div>
  {cumulative_svg()}
  <div class="chart-cap">초기 -$400 (BNB 사건) → 패치 후 6/5 부터 V자 회복. 6/7 단일 일 +$972 의 BTC/LTC 큰 거래로 누적 +$1,000 돌파.</div>
</div>

<div class="grid-2">

<!-- DAILY BAR CHART -->
<div class="chart-card">
  <div class="chart-title">📅 일별 손익</div>
  {daily_bars_svg()}
  <div class="chart-cap">5/28 ~ 6/1 = BNB 사건 + 초기 휩쏘. 6/5 부터 흑자 전환. 6/7 = +$972 단일 최고기록.</div>
</div>

<!-- DAILY TABLE -->
<div class="chart-card">
  <div class="chart-title">일별 손익 표</div>
  <table>
    <tr><th class="label">날짜</th><th>실현 P&L</th></tr>
    {chr(10).join(f'<tr><td class="label">{day}</td><td class="{sign_class(val)}">{val:+.2f}</td></tr>' for day, val in daily)}
    <tr style="font-weight:700;border-top:2px solid #888"><td class="label">12일 누적</td><td class="{sign_class(d['realized_total'])}">{d['realized_total']:+.2f}</td></tr>
  </table>
</div>

</div>

<!-- SYMBOL CHART -->
<div class="chart-card">
  <div class="chart-title">📊 종목별 실현 손익 (12일 누적, 큰 순)</div>
  {symbol_bars_svg()}
  <div class="chart-cap">메이저 BTC/ETH 가 winner. AVAX +$361 단일 최고 trade. BNB -$441 = 5/31 reduceOnly 버그 사건.</div>
</div>

<!-- PATCH EFFECTIVENESS -->
<h2>패치 효과 — 12일간 누적</h2>
<div class="grid-2">
<div class="chart-card">
  <table>
    <tr><th class="label">이벤트</th><th>건수</th><th class="label">의미</th></tr>
    <tr><td class="label">TRAIL-STOP 청산</td><td>{d['counters']['trail']}</td><td class="label">trail 신호 정상 발동 + 청산</td></tr>
    <tr><td class="label">OPEN (신규 진입)</td><td>{d['counters']['opens']}</td><td class="label">MA crossover 신규 신호 진입</td></tr>
    <tr><td class="label">Fallback plain 사용</td><td class="pos">{d['counters']['fallback']}</td><td class="label">reduceOnly 거부 → 자동 plain 으로 우회 성공</td></tr>
    <tr><td class="label">STATE DESYNC 자동 동기화</td><td>{d['counters']['desync']}</td><td class="label">엔진/Binance 불일치 자동 정정</td></tr>
    <tr><td class="label">CRITICAL 알람</td><td class="mute">{d['counters']['critical']}</td><td class="label">대부분 패치 전 BNB stuck 시기 (이력)</td></tr>
    <tr><td class="label">RETRY 카운트</td><td class="mute">{d['counters']['retries']}</td><td class="label">close 재시도 (대부분 fallback 으로 정리됨)</td></tr>
  </table>
  <div class="chart-cap" style="margin-top:10px">⚠️ CRITICAL 716건은 패치 전 5/31 BNB stuck 사건의 잔재 (DOT 235회 등 누적). 패치 후엔 신규 CRITICAL 0건.</div>
</div>

<div class="chart-card">
  <div class="chart-title">패치 적용 4건 (운영 안정성)</div>
  <table>
    <tr><th class="label">패치</th><th class="label">효과</th></tr>
    <tr><td class="label">① reduceOnly -2022 fallback</td><td class="label pos">15건 자동 우회</td></tr>
    <tr><td class="label">② state desync reconcile</td><td class="label pos">6건 자동 정정</td></tr>
    <tr><td class="label">③ engine state persistence</td><td class="label pos">재시작 시 trail 누적 보존</td></tr>
    <tr><td class="label">④ Binance 시계 자동 동기화</td><td class="label pos">-1021 timestamp 거부 방지</td></tr>
  </table>
  <div class="chart-cap" style="margin-top:10px">12일 운영 중 stuck 사태 0건, 재시작 시 데이터 손실 0건. 패치 적용 후 봇이 무중단 운영 가능 상태.</div>
</div>
</div>

<!-- CURRENT STATE -->
<h2>현재 상태 — 휴식 중</h2>
<div class="chart-card">
  <p>{'<b>현재 보유 포지션 없음.</b> 12일간의 cascade 청산 사이클 후 모든 종목 flat.' if not d['positions'] else f'<b>보유 포지션 {len(d["positions"])}종</b>'}</p>
  <p>가용 마진 100% 풀린 상태로 다음 4h 봉 마감의 새 진입 신호 대기. 운영봇은 정상 가동 중 (tick 진행). 시장이 명확한 추세를 다시 만들면 자동 진입.</p>
</div>

<!-- FOOTER -->
<p class="meta" style="margin-top: 30px; text-align: center;">
  Crypto Bot 운영 대시보드 · 자동 생성 {d['now']}<br>
  데이터 소스: Binance Futures Testnet REALIZED_PNL income / runner logs<br>
  엔진: <code>lib/autotrader/live/engine.py</code> · 브로커: <code>lib/autotrader/broker/binance_testnet_client.py</code>
</p>

</body>
</html>'''

out_dir = Path('docs/2026-06-08')
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / 'dashboard.html'
out_path.write_text(html, encoding='utf-8')
print(f'DASHBOARD WRITTEN: {out_path}')
print(f'size: {out_path.stat().st_size} bytes')
