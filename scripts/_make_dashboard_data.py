"""Dashboard 데이터 수집."""
import sys, os, time, json, glob
from datetime import datetime, timezone, timedelta

sys.path.insert(0,'lib')
for line in open('.env',encoding='utf-8'):
    line=line.split('#')[0].strip()
    if '=' in line:
        k,v=line.split('=',1); os.environ.setdefault(k.strip(),v.strip())

from autotrader.broker.binance_testnet_client import BinanceTestnetClient,BinanceTestnetConfig
c=BinanceTestnetClient(BinanceTestnetConfig.from_env())
KST = timezone(timedelta(hours=9))

b = c.balance()

end_ms = int(time.time()*1000)
start_ms = end_ms - 12*24*3600*1000
syms = ['BTCUSDT','ETHUSDT','SOLUSDT','AVAXUSDT','BNBUSDT','DOGEUSDT','ADAUSDT','XRPUSDT','DOTUSDT','LINKUSDT','LTCUSDT','BCHUSDT','ARBUSDT','OPUSDT','SUIUSDT','INJUSDT','NEARUSDT','ATOMUSDT']
income_all = []
for s in syms:
    try:
        rows = c._client.futures_income_history(symbol=s, incomeType='REALIZED_PNL',
                                                 startTime=start_ms, endTime=end_ms, limit=500)
        for r in rows: r['sym'] = s
        income_all.extend(rows)
    except Exception: pass
income_all.sort(key=lambda x: int(x['time']))

log_files = sorted(glob.glob('data/_runner_*.log'))
counters = dict(trail=0, fallback=0, desync=0, critical=0, opens=0, retries=0)
for f in log_files:
    try:
        with open(f, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                if 'TRAIL-STOP buy' in line or 'TRAIL-STOP sell' in line:
                    counters['trail'] += 1
                if 'fallback plain' in line: counters['fallback'] += 1
                if 'STATE DESYNC' in line: counters['desync'] += 1
                if 'CRITICAL' in line: counters['critical'] += 1
                if 'OPEN buy' in line or 'OPEN sell' in line:
                    counters['opens'] += 1
                if 'RETRY' in line and 'close_retry' in line: counters['retries'] += 1
    except Exception: pass

total_realized = sum(float(r['income']) for r in income_all)
by_sym = {}
for r in income_all:
    by_sym[r['sym']] = by_sym.get(r['sym'],0) + float(r['income'])

cumulative = []
running = 0
for r in income_all:
    running += float(r['income'])
    cumulative.append({
        'ts_ms': int(r['time']),
        'ts': datetime.fromtimestamp(int(r['time'])/1000, tz=KST).strftime('%m-%d %H:%M'),
        'sym': r['sym'],
        'pnl': float(r['income']),
        'cum': running,
    })

by_day = {}
for r in income_all:
    d = datetime.fromtimestamp(int(r['time'])/1000, tz=KST).strftime('%m-%d')
    by_day[d] = by_day.get(d, 0) + float(r['income'])

bot_start = datetime(2026,6,4,11,6,17, tzinfo=KST)
now = datetime.now(KST)
uptime = now - bot_start
uptime_str = f"{uptime.days}일 {uptime.seconds//3600}시간 {(uptime.seconds%3600)//60}분"

out = {
    'now': now.strftime('%Y-%m-%d %H:%M:%S KST'),
    'bot_status': 'running',
    'bot_started': bot_start.strftime('%Y-%m-%d %H:%M:%S KST'),
    'uptime': uptime_str,
    'wallet': b['total_wallet_balance'],
    'upnl': b['total_unrealized_pnl'],
    'margin': b['total_margin_balance'],
    'available': b['available_balance'],
    'positions': b['positions'],
    'session_start_wallet': 10817.89,
    'session_start_date': '2026-05-28 14:00',
    'realized_total': total_realized,
    'by_symbol': by_sym,
    'by_day': sorted(by_day.items()),
    'cumulative': cumulative,
    'counters': counters,
}

with open('data/_dashboard_data.json', 'w', encoding='utf-8') as fh:
    json.dump(out, fh, indent=2, ensure_ascii=False, default=str)
print('DASHBOARD DATA SAVED')
print(f'wallet ${out["wallet"]:,.2f}  realized ${total_realized:+,.2f}  positions {len(out["positions"])}')
print(f'patches: trail={counters["trail"]} fallback={counters["fallback"]} desync={counters["desync"]} critical={counters["critical"]} opens={counters["opens"]}')
