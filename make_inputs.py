"""Build inputs.json from the raw Google Sheets get_values results saved by the scheduled run.

Usage: python3 make_inputs.py <dir>   (dir holds log.json, disc_a.json, disc_tw.json, rep.json)
Each file is the JSON returned by the Sheets get_values tool: {"range": ..., "values": [[...], ...]}.
"""
import json, os, sys, datetime as dt
from zoneinfo import ZoneInfo

d = sys.argv[1] if len(sys.argv) > 1 else '.'
load = lambda f: (json.load(open(os.path.join(d, f))).get('values') or []) if os.path.exists(os.path.join(d, f)) else []
log = load('log.json')            # 'ENGINE LOG'!B1:B4
cell = lambda i: (log[i][0] if i < len(log) and log[i] else '')
disc_a = load('disc_a.json')      # discovery tab A2:A
disc_tw = load('disc_tw.json')    # discovery tab T2:W
rep = load('rep.json')            # 'REPRESENTATION RESEARCH'!A5:F
today = dt.datetime.now(ZoneInfo('America/New_York')).date()
disc = []
for i in range(max(len(disc_a), len(disc_tw))):
    a = disc_a[i][0] if i < len(disc_a) and disc_a[i] else ''
    tw = (disc_tw[i] if i < len(disc_tw) else []) + ['', '', '', '']
    if not a and not tw[2]: continue
    disc.append({'row': i + 2, 'id': a, 'status': str(tw[0]).strip(), 'aid': str(tw[1]).strip(), 'pid': str(tw[3]).strip()})
def _is_rep(r):
    st = str(r[4]).strip().lower() if len(r) >= 5 else ''
    if st.startswith('not ') or st.startswith('unknown') : return False   # e.g. 'Not represented (per JSG)'
    return bool(st) or (len(r) >= 6 and str(r[5]).strip() not in ('', '-'))
represented = [r[0] for r in rep if r and _is_rep(r)]
inp = {'run_date': today.isoformat(), 'last_cutoff': str(cell(1)).strip(),
       'next_row': {'M': int(float(cell(2) or 571)), 'W': int(float(cell(3) or 492))},
       'discovery': disc, 'represented': represented, 'weekly': today.weekday() == 0, 'outdir': os.path.join(d, 'out')}
json.dump(inp, open(os.path.join(d, 'inputs.json'), 'w'), indent=1)
print(json.dumps({k: (v if k not in ('discovery', 'represented') else len(v)) for k, v in inp.items()}))
