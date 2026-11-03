"""JSG daily regional engine.

Reads registry.json (+ inputs.json written by the scheduled run), pulls ESPN 2026-27 game data for every tracked
NJ/NY/PA player, re-scores with sample-size blending (mirrors the Sheet formulas), finds new NJ/NY/PA players,
and writes out/plan.json (Sheet operations), out/email.html (daily) and out/weekly.html (Mondays).
"""
import json, re, sys, os, math, time, datetime as dt, urllib.request, concurrent.futures as cf
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
ET = ZoneInfo('America/New_York')
SHEET_URL = 'https://docs.google.com/spreadsheets/d/1hZP-C68txx08Ny4RQfi8xqMgrER74j7Hv-9kOOqTVtQ/edit'
SPORT = {'M': 'mens-college-basketball', 'W': 'womens-college-basketball'}
DB = {'M': 'MENS NATIONAL DATABASE', 'W': 'WOMENS NATIONAL DATABASE'}
EXTRA_TEAMS = {'W': ['2385'], 'M': []}          # programs missing from ESPN's team index
SEASON = '2027'                                    # ESPN season id for 2026-27
SEASON_START = dt.date(2026, 10, 15)
W8 = dict(T=.25, P=.20, U=.15, N=.15, E=.10, R=.10, REP=.05)
n = lambda s: re.sub(r'[^a-z0-9]', '', str(s or '').lower())
def hu(x, d=0):
    return float(Decimal(repr(x + (1e-9 if x >= 0 else -1e-9))).quantize(Decimal(1) if d == 0 else Decimal('0.' + '0' * (d - 1) + '1'), ROUND_HALF_UP))

# ---------------------------------------------------------------- column map (DB tabs) ----
COLS = ['Player ID', 'Player Name', 'Gender', 'Current School', 'Conference', 'Program Tier', 'Position', 'Height',
        'Academic Year', 'Eligibility Status', 'Hometown', 'State', 'High School', '25-26 GP', '25-26 GS',
        '25-26 MPG', '25-26 PPG', '25-26 RPG', '25-26 APG', '25-26 SPG', '25-26 BPG', '25-26 FG%', '25-26 3PT%', '25-26 FT%',
        'Advanced Metrics', 'Previous Schools', 'Transfer Direction', 'Projected Destinations', 'Transfer Score', 'NIL Tier',
        'Representation Status', 'Agency', 'Instagram', 'X/Twitter', 'Regional Connection', 'JSG Target Score',
        'Priority Tier', 'Scouting Notes', 'Sources', 'Last Verified', 'Sub-Region', '2025-26 Stats School', 'Stats Source',
        'Data Source', 'Verification Status', 'Score: Transfer Opp. (25%)', 'Score: Production (20%)', 'Score: Upside (15%)',
        'Score: NIL (15%)', 'Score: Eligibility (10%)', 'Score: Regional (10%)', 'Score: Representation (5%)', 'Rank',
        # live-engine columns (BB:CK)
        'ESPN Athlete ID', 'Base Prod Index 25-26', 'Base MPG 25-26', 'Base PPG 25-26', 'Level Multiplier',
        'Conference Exposure', 'Home Market Score', 'Upside Base (class+size)', 'Size Score', 'Service Academy',
        'Class Key', 'High-Major', 'Fit State',
        '26-27 GP', '26-27 MPG', '26-27 PPG', '26-27 RPG', '26-27 APG', '26-27 SPG', '26-27 BPG', '26-27 FG%', '26-27 3PT%',
        '26-27 FT%', 'Last Game (L5 PPG)', 'Last Game Date',
        '26-27 Prod Index', 'Sample Weight', 'Blended Prod Index', 'Blended MPG', 'Blended PPG', 'Transfer Score (raw)',
        'NJ Rank', 'NY Rank', 'PA Rank', 'Last Rank Move', 'Base Direction']
def L(i):
    s = ''
    while i:
        i, r = divmod(i - 1, 26); s = chr(65 + r) + s
    return s
C = {c: L(i + 1) for i, c in enumerate(COLS)}
assert C['Rank'] == 'BA' and C['ESPN Athlete ID'] == 'BB' and C['26-27 GP'] == 'BO' and C['Base Direction'] == 'CK'
STATIC_LIVE = ['ESPN Athlete ID', 'Base Prod Index 25-26', 'Base MPG 25-26', 'Base PPG 25-26', 'Home Market Score',
               'Upside Base (class+size)', 'Size Score', 'Class Key', 'Fit State']
FITS_RANGE = "'SCORING & METHODOLOGY'!$A$31:$D$33"
WREF = "'SCORING & METHODOLOGY'!$C$"

def formulas(r):
    """Every formula cell of a DB row (row r). Same text is used for setup and for promoted players."""
    c = {k: f"${v}{r}" for k, v in C.items()}
    col = lambda k: C[k]
    f = {}
    f['Level Multiplier'] = f'=SWITCH($F{r},"High-major",1.3,"Upper mid-major",1.1,"Mid-major",1,"Low-major",0.85,1)'
    f['Conference Exposure'] = f'=SWITCH($F{r},"High-major",100,"Upper mid-major",70,"Mid-major",50,"Low-major",35,50)'
    f['Service Academy'] = f'=IF(OR($D{r}="Army",$D{r}="Navy",$D{r}="Air Force"),1,0)'
    f['High-Major'] = f'=IF($F{r}="High-major",1,0)'
    f['26-27 GP'] = f'=IFERROR(XLOOKUP($A{r},FEED!$A:$A,FEED!$C:$N,"",0,-1),"")'
    gp, mp, pp, rb, ab, sb, bb, fg = (c[k] for k in ['26-27 GP', '26-27 MPG', '26-27 PPG', '26-27 RPG', '26-27 APG', '26-27 SPG', '26-27 BPG', '26-27 FG%'])
    f['26-27 Prod Index'] = f'=IF(N({gp})>0,({pp}+0.8*{rb}+1.2*{ab}+1.5*{sb}+1.5*{bb})*MAX(0.85,MIN(1.15,1+N({fg})-0.4))*{c["Level Multiplier"]},"")'
    f['Sample Weight'] = f'=IF(N({gp})>0,{gp}/({gp}+8),0)'
    w, pi27, bpi, bmin, bpts = c['Sample Weight'], c['26-27 Prod Index'], c['Base Prod Index 25-26'], c['Base MPG 25-26'], c['Base PPG 25-26']
    cb = col('Base Prod Index 25-26')
    f['Blended Prod Index'] = f'=IF(N({gp})>0,{w}*{pi27}+(1-{w})*IF({bpi}="",PERCENTILE.INC(${cb}$6:${cb},0.3),{bpi}),IF({bpi}="","",{bpi}))'
    f['Blended MPG'] = f'=IF(N({gp})>0,IF({bmin}="",{mp},{w}*{mp}+(1-{w})*{bmin}),{bmin})'
    f['Blended PPG'] = f'=IF(N({gp})>0,IF({bpts}="",{pp},{w}*{pp}+(1-{w})*{bpts}),{bpts})'
    pi, bm, bp = c['Blended Prod Index'], c['Blended MPG'], c['Blended PPG']
    pr, up, el, sz = c['Score: Production (20%)'], c['Score: Upside (15%)'], c['Score: Eligibility (10%)'], c['Size Score']
    cpi = col('Blended Prod Index')
    f['Score: Production (20%)'] = f'=IF({pi}="",30,ROUND(100*PERCENTRANK.INC(FILTER(${cpi}$6:${cpi},ISNUMBER(${cpi}$6:${cpi})),{pi},6),0))'
    f['Score: Upside (15%)'] = f'=MIN(100,{c["Upside Base (class+size)"]}+IF(N({bm})>=5,IF({bp}/{bm}*30>=15,10,IF({bp}/{bm}*30>=10,5,0)),0))'
    hm, ck, ac, bk = c['High-Major'], c['Class Key'], c['Service Academy'], c['Base Direction']
    f['Base Direction'] = (f'=IF({hm}=1,IF(AND({ck}="Fr",N({gp})<5),"Developmental - first season (monitor)",IF(N({bm})>=20,'
                           f'"Retain / develop at current high-major","Transfer Down candidate (limited high-major role)")),'
                           f'IF(AND({ck}="Fr",N({gp})<5),"Developmental - first season (monitor)",IF({pr}>=60,"Transfer Up candidate",'
                           f'IF({up}>=70,"Transfer Up - developmental","Monitor"))))')
    tu = f'0.45*{pr}+0.25*{up}+0.15*{el}+0.15*{sz}'
    f['Transfer Score (raw)'] = (f'=IF({hm}=1,IF(LEFT({bk},3)="Dev",35,IF(LEFT({bk},3)="Ret",30,'
                                 f'0.35*(100-MIN(N({bm}),30)/30*100)+0.25*{up}+0.25*{el}+0.15*{sz})),IF(LEFT({bk},3)="Dev",MIN({tu},45),{tu}))')
    f['Transfer Score'] = f'=ROUND({c["Transfer Score (raw)"]},0)'
    ts = c['Transfer Score']
    f['Transfer Direction'] = f'={bk}&IF({ac}=1," - SERVICE ACADEMY (service obligation / NIL limits)","")'
    f['Score: Transfer Opp. (25%)'] = f'=IF({ac}=1,MIN(IF(ISNUMBER(SEARCH("Transfer",{bk})),{ts},MIN({ts},40)),25),IF(ISNUMBER(SEARCH("Transfer",{bk})),{ts},MIN({ts},40)))'
    nil = f'0.4*{pr}+0.25*{c["Conference Exposure"]}+0.2*{c["Home Market Score"]}+0.15*{up}'
    f['Score: NIL (15%)'] = f'=IF({ac}=1,MIN(ROUND({nil},0),45),ROUND({nil},0))'
    f['NIL Tier'] = (f'=IF({ac}=1,IF({c["Score: NIL (15%)"]}>=42,"Emerging","Developmental"),IF({nil}>=80,"Elite",IF({nil}>=68,"High",'
                     f'IF({nil}>=55,"Moderate",IF({nil}>=42,"Emerging","Developmental")))))')
    f['Projected Destinations'] = (f'=IF(LEFT({bk},21)="Transfer Up candidate",INDEX({FITS_RANGE},MATCH({c["Fit State"]},INDEX({FITS_RANGE},0,1),0),IF({pr}>=80,2,3))'
                                   f'&" - projected fits, not confirmed destinations",IF(LEFT({bk},27)="Transfer Up - developmental",'
                                   f'INDEX({FITS_RANGE},MATCH({c["Fit State"]},INDEX({FITS_RANGE},0,1),0),3)&" - projected fits, not confirmed destinations",'
                                   f'IF(LEFT({bk},13)="Transfer Down",INDEX({FITS_RANGE},MATCH({c["Fit State"]},INDEX({FITS_RANGE},0,1),0),4)'
                                   f'&" - projected fits, not confirmed destinations","-")))')
    sc = {k: c[k] for k in COLS if k.startswith('Score:')}
    f['JSG Target Score'] = (f"=ROUND({sc['Score: Transfer Opp. (25%)']}*{WREF}6+{sc['Score: Production (20%)']}*{WREF}7+{sc['Score: Upside (15%)']}*{WREF}8"
                             f"+{sc['Score: NIL (15%)']}*{WREF}9+{sc['Score: Eligibility (10%)']}*{WREF}10+{sc['Score: Regional (10%)']}*{WREF}11"
                             f"+{sc['Score: Representation (5%)']}*{WREF}12+IF(AND(OR({ck}=\"Fr\",{ck}=\"R-Fr\",{ck}=\"So\",{ck}=\"R-So\"),N({gp})>=2),MIN(10,IF(N({mp})>=25,5,IF(N({mp})>=20,3,IF(N({mp})>=15,1,0)))+IF(N({pp})>=15,5,IF(N({pp})>=10,3,IF(N({pp})>=7,1,0)))),0),1)")
    j = c['JSG Target Score']
    f['Priority Tier'] = (f'=IF({j}>=85,"Tier 1 - Immediate Research & Outreach",IF({j}>=70,"Tier 2 - Strong Recruiting Target",'
                          f'IF({j}>=55,"Tier 3 - Developmental Watchlist","Tier 4 - Monitor")))')
    jc = col('JSG Target Score')
    f['Rank'] = f'=RANK({j},${jc}$6:${jc})'
    for st in ('NJ', 'NY', 'PA'):
        f[f'{st} Rank'] = f'=IF(ISNUMBER(SEARCH("{st}",$L{r})),COUNTIFS($L$6:$L,"*{st}*",${jc}$6:${jc},">"&{j})+1,"")'
    f['Last Rank Move'] = f"=IFERROR(XLOOKUP($A{r},'RANK HISTORY'!$B:$B,'RANK HISTORY'!$L:$L,\"\",0,-1),\"\")"
    return f
FORMULA_COLS = list(formulas(6).keys())

# ---------------------------------------------------------------- scoring mirror ----
def score_pool(players, snap, prior_pool=None):
    """players: list of registry dicts for ONE gender (the DB pool). snap: pid -> season line or None.
    Returns pid -> result dict. Mirrors the Sheet formulas exactly."""
    out = {}
    bvals = sorted(p['base_pi'] for p in players if p['base_pi'] is not None)
    h = (len(bvals) - 1) * 0.3; lo = int(math.floor(h))
    prior = bvals[lo] + (h - lo) * (bvals[min(lo + 1, len(bvals) - 1)] - bvals[lo])   # = PERCENTILE.INC(base, 0.3)
    for p in players:
        s = snap.get(p['pid'])
        gp = s['GP'] if s else 0
        tmult = {'High-major': 1.3, 'Upper mid-major': 1.1, 'Mid-major': 1.0, 'Low-major': 0.85}.get(p['tier'], 1)
        expo = {'High-major': 100, 'Upper mid-major': 70, 'Mid-major': 50, 'Low-major': 35}.get(p['tier'], 50)
        if gp > 0:
            pi27 = (s['PTS'] + .8 * s['REB'] + 1.2 * s['AST'] + 1.5 * s['STL'] + 1.5 * s['BLK']) * max(.85, min(1.15, 1 + s['FG'] - .4)) * tmult
            w = gp / (gp + 8)
            pi = w * pi27 + (1 - w) * (prior if p['base_pi'] is None else p['base_pi'])
            bm = s['MIN'] if p['base_min'] is None else w * s['MIN'] + (1 - w) * p['base_min']
            bp = s['PTS'] if p['base_pts'] is None else w * s['PTS'] + (1 - w) * p['base_pts']
        else:
            pi, bm, bp = p['base_pi'], p['base_min'], p['base_pts']
        out[p['pid']] = dict(pi=pi, bm=bm, bp=bp, expo=expo, gp=gp, fb=(min(10,(5 if s['MIN']>=25 else 3 if s['MIN']>=20 else 1 if s['MIN']>=15 else 0)+(5 if s['PTS']>=15 else 3 if s['PTS']>=10 else 1 if s['PTS']>=7 else 0)) if p['cls'] in ('Fr','R-Fr','So','R-So') and gp>=2 else 0))
    vals = sorted(o['pi'] for o in out.values() if o['pi'] is not None)
    import bisect
    for p in players:
        o = out[p['pid']]
        if o['pi'] is None: prod = 30
        else:
            pr = bisect.bisect_left(vals, o['pi']) / max(1, len(vals) - 1)
            prod = hu(100 * (math.floor(pr * 1e6 + 1e-9) / 1e6))
        bm, bp = o['bm'] or 0, o['bp']
        up = min(100, p['youth_base'] + ((10 if bp / bm * 30 >= 15 else 5 if bp / bm * 30 >= 10 else 0) if bm >= 5 else 0))
        cls, gp = p['cls'], o['gp']
        if p['hm']:
            if cls == 'Fr' and gp < 5: d, t = 'Developmental - first season (monitor)', 35
            elif bm >= 20: d, t = 'Retain / develop at current high-major', 30
            else:
                d = 'Transfer Down candidate (limited high-major role)'
                t = .35 * (100 - min(bm, 30) / 30 * 100) + .25 * up + .25 * p['elig'] + .15 * p['size']
        else:
            tu = .45 * prod + .25 * up + .15 * p['elig'] + .15 * p['size']
            if cls == 'Fr' and gp < 5: d, t = 'Developmental - first season (monitor)', min(tu, 45)
            elif prod >= 60: d, t = 'Transfer Up candidate', tu
            elif up >= 70: d, t = 'Transfer Up - developmental', tu
            else: d, t = 'Monitor', tu
        ts = hu(t)
        st = ts if 'Transfer' in d else min(ts, 40)
        nilraw = .4 * prod + .25 * o['expo'] + .2 * p['market'] + .15 * up
        nil = hu(nilraw)
        if p['academy']:
            st, nil = min(st, 25), min(nil, 45)
            ntier = 'Emerging' if nil >= 42 else 'Developmental'
        else:
            ntier = 'Elite' if nilraw >= 80 else 'High' if nilraw >= 68 else 'Moderate' if nilraw >= 55 else 'Emerging' if nilraw >= 42 else 'Developmental'
        jsg = hu(st * W8['T'] + prod * W8['P'] + up * W8['U'] + nil * W8['N'] + p['elig'] * W8['E'] + p['reg'] * W8['R'] + p['rep'] * W8['REP'] + o['fb'], 1)
        o.update(prod=prod, up=up, dir=d, t=ts, s_tr=st, nil=nil, ntier=ntier, jsg=jsg)
    allj = sorted((o['jsg'] for o in out.values()), reverse=True)
    for p in players:
        o = out[p['pid']]
        o['rank'] = 1 + sum(1 for x in allj if x > o['jsg'])
        for stt in ('NJ', 'NY', 'PA'):
            if stt in p['states']:
                o[stt] = 1 + sum(1 for q in players if stt in q['states'] and out[q['pid']]['jsg'] > o['jsg'])
    return out

def tier_label(j):
    return 'Tier 1' if j >= 85 else 'Tier 2' if j >= 70 else 'Tier 3' if j >= 55 else 'Tier 4'

# ---------------------------------------------------------------- ESPN ----
def get(url, tries=3):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404, 500): return None
            time.sleep(1 + i)
        except Exception:
            time.sleep(1 + i)
    return None

def pmap(fn, items, workers=12):
    with cf.ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, items))

def et_date(iso):
    return dt.datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(ET).date()

def mk(made_att):
    try:
        a, b = str(made_att).split('-'); return int(a), int(b)
    except Exception: return 0, 0
def num(x):
    try: return float(x)
    except Exception: return 0.0

def gamelog(g, aid):
    d = get(f'https://site.web.api.espn.com/apis/common/v3/sports/basketball/{SPORT[g]}/athletes/{aid}/gamelog?season={SEASON}')
    games = {}
    if not d: return games
    labels = d.get('labels') or []
    evs = d.get('events') or {}
    for stype in d.get('seasonTypes') or []:
        for cat in stype.get('categories') or []:
            for e in cat.get('events') or []:
                meta = evs.get(e['eventId'])
                if not meta: continue
                row = dict(zip(labels, e['stats']))
                if str(row.get('MIN', '')).strip() in ('', '--', '0'): continue
                date = et_date(meta['gameDate'])
                if date < SEASON_START: continue
                fgm, fga = mk(row.get('FG')); tpm, tpa = mk(row.get('3PT')); ftm, fta = mk(row.get('FT'))
                games[e['eventId']] = dict(date=date, MIN=num(row.get('MIN')), PTS=num(row.get('PTS')), REB=num(row.get('REB')),
                                           AST=num(row.get('AST')), STL=num(row.get('STL')), BLK=num(row.get('BLK')), TO=num(row.get('TO')),
                                           PF=num(row.get('PF')), OREB=None, fgm=fgm, fga=fga, tpm=tpm, tpa=tpa, ftm=ftm, fta=fta,
                                           opp=(meta.get('opponent') or {}).get('abbreviation', ''), at=meta.get('atVs', ''),
                                           res=meta.get('gameResult', ''), score=meta.get('score', ''))
    return games

def box_scores(g, dates):
    """Final games on the given ET dates -> {aid: {eventId: line}} (fills ESPN gamelog lag)."""
    out = {}
    evs = []
    for d in dates:
        sb = get(f'https://site.api.espn.com/apis/site/v2/sports/basketball/{SPORT[g]}/scoreboard?dates={d:%Y%m%d}&groups=50&limit=500')
        for e in (sb or {}).get('events', []):
            if e.get('status', {}).get('type', {}).get('completed'):
                evs.append(e)
    def one(e):
        s = get(f'https://site.api.espn.com/apis/site/v2/sports/basketball/{SPORT[g]}/summary?event={e["id"]}')
        return e, s
    comp_abbr = lambda e: {c['team']['id']: c['team'].get('abbreviation', '') for c in e['competitions'][0]['competitors']}
    for e, s in pmap(one, evs):
        if not s: continue
        date = et_date(e['date'])
        ab = comp_abbr(e)
        comps = {c['team']['id']: c for c in e['competitions'][0]['competitors']}
        for team in (s.get('boxscore') or {}).get('players', []):
            tid = team['team']['id']
            opp = [k for k in ab if k != tid]
            oppab = ab.get(opp[0], '') if opp else ''
            home = comps.get(tid, {}).get('homeAway') == 'home'
            my, their = comps.get(tid, {}).get('score'), comps.get(opp[0], {}).get('score') if opp else None
            res = ''
            try: res = 'W' if int(my) > int(their) else 'L'
            except Exception: pass
            for stt in team.get('statistics', []):
                labels = stt.get('labels') or []
                for a in stt.get('athletes', []):
                    if a.get('didNotPlay') or not a.get('stats'): continue
                    row = dict(zip(labels, a['stats']))
                    if str(row.get('MIN', '')).strip() in ('', '--', '0'): continue
                    fgm, fga = mk(row.get('FG')); tpm, tpa = mk(row.get('3PT')); ftm, fta = mk(row.get('FT'))
                    out.setdefault(a['athlete']['id'], {})[e['id']] = dict(
                        date=date, MIN=num(row.get('MIN')), PTS=num(row.get('PTS')), REB=num(row.get('REB')), AST=num(row.get('AST')),
                        STL=num(row.get('STL')), BLK=num(row.get('BLK')), TO=num(row.get('TO')), PF=num(row.get('PF')),
                        OREB=num(row.get('OREB')), fgm=fgm, fga=fga, tpm=tpm, tpa=tpa, ftm=ftm, fta=fta, opp=oppab,
                        at='vs' if home else '@', res=res, score=f'{my}-{their}')
    return out

def season_line(games, cutoff):
    gs = sorted((x for x in games.values() if x['date'] <= cutoff), key=lambda x: x['date'])
    if not gs: return None
    k = len(gs)
    a = lambda f: sum(x[f] for x in gs) / k
    fga, tpa, fta = sum(x['fga'] for x in gs), sum(x['tpa'] for x in gs), sum(x['fta'] for x in gs)
    last = gs[-1]
    l5 = sum(x['PTS'] for x in gs[-5:]) / len(gs[-5:])
    return dict(GP=k, MIN=round(a('MIN'), 1), PTS=round(a('PTS'), 1), REB=round(a('REB'), 1), AST=round(a('AST'), 1),
                STL=round(a('STL'), 1), BLK=round(a('BLK'), 1),
                FG=round(sum(x['fgm'] for x in gs) / fga, 3) if fga else 0.0,
                TP=round(sum(x['tpm'] for x in gs) / tpa, 3) if tpa else '',
                FT=round(sum(x['ftm'] for x in gs) / fta, 3) if fta else '',
                last=last, l5=round(l5, 1))

def game_score(x):
    return x['PTS'] + .4 * x['fgm'] - .7 * x['fga'] - .4 * (x['fta'] - x['ftm']) + .5 * x['REB'] + .7 * x['AST'] + x['STL'] + .7 * x['BLK'] - .4 * x['PF'] - x['TO']

def last_txt(sl):
    x = sl['last']
    return f"{x['date']:%m/%d} {x['at']} {x['opp']} {x['res']}: {int(x['PTS'])}p {int(x['REB'])}r {int(x['AST'])}a, {x['fgm']}-{x['fga']} FG, {int(x['MIN'])}m (L5 {sl['l5']} ppg)"

# ---------------------------------------------------------------- discovery support ----
def program_maps():
    prog = {n(l.split('|')[0]): l.strip().split('|') for l in open(os.path.join(HERE, 'programs_raw.txt'))}
    ALIAS = {'americanuniversity': 'American', 'appstate': 'Appalachian State', 'floridainternational': 'FIU', 'grambling': 'Grambling State',
             'iuindianapolis': 'IU Indy', 'lsuneworleans': 'New Orleans', 'longislanduniversity': 'LIU', 'massachusetts': 'UMass',
             'miami': 'Miami (FL)', 'pennsylvania': 'Penn', 'selouisiana': 'Southeastern Louisiana', 'sanjosstate': 'San Jose State',
             'seattleu': 'Seattle', 'southcarolinaupstate': 'USC Upstate', 'ualbany': 'Albany', 'ulmonroe': 'Louisiana–Monroe',
             'queensuniversity': 'Queens'}
    MEN_ONLY = {'thecitadel': ['The Citadel', 'Southern'], 'vmi': ['VMI', 'Southern']}
    def lookup(location):
        k = n(location)
        if k in prog: return prog[k]
        if k in ALIAS and n(ALIAS[k]) in prog: return prog[n(ALIAS[k])]
        if k in MEN_ONLY: return MEN_ONLY[k]
        return [location, None]
    return lookup

def load_prep():
    src = open(os.path.join(HERE, 'prep.py')).read()
    ns = {}
    cwd = os.getcwd(); os.chdir(HERE)
    try:
        exec(src.split('# HS / prep ties')[0], ns)
        exec('def cls_key' + src.split('def cls_key')[1].split('players = []')[0], ns)
    finally:
        os.chdir(cwd)
    return ns

def size_score(pos, ht):
    m = re.match(r"(\d)\D+(\d+)", ht or '')
    hi = int(m.group(1)) * 12 + int(m.group(2)) if m else None
    big = (pos or '').upper().startswith(('F', 'C')) or '/F' in (pos or '') or (pos or '').upper() == 'W'
    if not hi: return 50
    if big: return 90 if hi >= 77 else 75 if hi >= 75 else 55 if hi >= 73 else 40
    return 90 if hi >= 73 else 75 if hi >= 71 else 55 if hi >= 69 else 40

# ---------------------------------------------------------------- main ----
def main():
    inp = json.load(open(sys.argv[1] if len(sys.argv) > 1 else 'inputs.json'))
    outdir = inp.get('outdir', 'out'); os.makedirs(outdir, exist_ok=True)
    run = dt.date.fromisoformat(inp['run_date'])
    now_cut = run - dt.timedelta(days=1)
    prev_cut = dt.date.fromisoformat(inp['last_cutoff']) if inp.get('last_cutoff') else SEASON_START - dt.timedelta(days=1)
    if prev_cut >= now_cut: prev_cut = now_cut - dt.timedelta(days=1)   # same-day rerun: compare vs the day before
    week_cut = now_cut - dt.timedelta(days=7)
    reg = json.load(open(os.path.join(HERE, 'registry.json')))
    if isinstance(reg, dict) and 'keys' in reg:
        reg = [dict(zip(reg['keys'], row)) for row in reg['rows']]
    disc_in = inp.get('discovery', [])
    represented = set(inp.get('represented', []))
    prep = load_prep()
    lookup = program_maps()

    # ---- ESPN team index + roster sweep (discovery) ----
    teams = {}
    for g in 'MW':
        d = get(f'https://site.api.espn.com/apis/site/v2/sports/basketball/{SPORT[g]}/teams?limit=500')
        lst = [t['team'] for t in d['sports'][0]['leagues'][0]['teams']]
        teams[g] = {t['id']: t for t in lst}
        for x in EXTRA_TEAMS[g]:
            teams[g].setdefault(x, {'id': x, 'location': 'Mercyhurst', 'displayName': 'Mercyhurst Lakers', 'slug': 'mercyhurst-lakers'})
    def roster(args):
        g, tid = args
        return g, tid, get(f'https://site.api.espn.com/apis/site/v2/sports/basketball/{SPORT[g]}/teams/{tid}/roster')
    rosters = pmap(roster, [(g, tid) for g in 'MW' for tid in teams[g]], 16)
    tracked_aids = {r['aid'] for r in reg if r['aid']}
    disc_aids = {str(d.get('aid')).strip() for d in disc_in if d.get('aid')}
    new_hits, by_aid = [], {}
    slug2loc = {g: {t.get('slug'): t.get('location') for t in teams[g].values()} for g in 'MW'}
    for g, tid, d in rosters:
        for a in (d or {}).get('athletes', []):
            by_aid[a['id']] = (g, tid, a)
            bp = a.get('birthPlace') or {}
            if bp.get('state') in ('NJ', 'NY', 'PA') and a['id'] not in tracked_aids and a['id'] not in disc_aids:
                new_hits.append((g, tid, a))
    def build_rec(g, tid, a, pid):
        t = teams[g].get(tid, {})
        school, conf_raw = lookup(t.get('location', ''))
        conf_raw = conf_raw or 'Northeast'
        tier = prep['CONF_TIER'].get(conf_raw, 'Mid-major')
        bp = a.get('birthPlace') or {}
        st, town = bp.get('state'), bp.get('city', '')
        cls = {'FR': 'Fr', 'SO': 'So', 'JR': 'Jr', 'SR': 'Sr', 'GR': 'Gr'}.get((a.get('experience') or {}).get('abbreviation', ''), 'So')
        pos = (a.get('position') or {}).get('abbreviation', '-'); ht = (a.get('displayHeight') or '').replace("' ", '-').replace('"', '')
        size = size_score(pos, ht)
        sub = prep['region'](st, town)
        nj = sub if st == 'NJ' else ''; ny = sub if st == 'NY' else ''; pa = sub if st == 'PA' else ''
        mkt = 100 if (ny in ('NYC', 'Long Island', 'Westchester') or pa == 'Philadelphia area' or nj == 'North') else 85 if st == 'NJ' else 70 if pa == 'Pittsburgh area' else 55
        regs = 100 if st == 'NJ' else 90 if pa == 'Philadelphia area' else 85 if ny in ('NYC', 'Long Island', 'Westchester') else 65
        # 2025-26 baseline from ESPN season averages
        sd = get(f'https://site.web.api.espn.com/apis/common/v3/sports/basketball/{SPORT[g]}/athletes/{a["id"]}/stats')
        base = None
        if sd:
            cats = {c['name']: c for c in sd.get('categories', [])}
            av = cats.get('averages')
            for s in (av or {}).get('statistics', []):
                if (s.get('season') or {}).get('displayName') == '2025-26':
                    base = dict(zip(av['labels'], s['stats'])); base['team'] = s.get('teamSlug')
        bpi = bmin = bpts = None; stats_school = ''
        if base:
            gp = num(base.get('GP'))
            loc = slug2loc[g].get(base['team'], base['team'])
            stats_school, c2 = lookup(loc or '')
            t2 = prep['CONF_TIER'].get(c2, 'Mid-major') if c2 else 'Mid-major'
            raw = num(base.get('PTS')) + .8 * num(base.get('REB')) + 1.2 * num(base.get('AST')) + 1.5 * num(base.get('STL')) + 1.5 * num(base.get('BLK'))
            fg = num(base.get('FG%')) / 100
            bpi = round(raw * max(.85, min(1.15, 1 + fg - .4)) * {'High-major': 1.3, 'Upper mid-major': 1.1, 'Mid-major': 1.0, 'Low-major': .85}[t2] * min(1, gp / 10), 4)
            bmin, bpts = num(base.get('MIN')), num(base.get('PTS'))
        yb = {'Fr': 85, 'So': 75, 'Jr': 55, 'Sr': 35, 'Gr': 20}[cls] + (10 if size >= 75 else 0) + (5 if size >= 90 else 0)
        elig = {'Fr': 100, 'So': 85, 'Jr': 60, 'Sr': 35, 'Gr': 15}[cls]
        return dict(pid=pid, g=g, aid=a['id'], name=a['fullName'], school=school, conf=prep['CONF_LABEL'].get(conf_raw, conf_raw), conf_raw=conf_raw,
                    tier=tier, cls=cls, pos=pos, ht=ht, states=st, ties={st: f'Birthplace (ESPN): {town}, {st}'}, base_pi=bpi, base_min=bmin, base_pts=bpts,
                    market=mkt, youth_base=yb, size=size, elig=elig, reg=regs, rep=50, fit=st, academy=1 if school in ('Army', 'Navy', 'Air Force') else 0,
                    hm=1 if tier == 'High-major' else 0, agency=None, hometown=f'{town}, {st}', sub=sub, stats_school=stats_school,
                    base=base, tid=tid, roster_url=f'https://www.espn.com/{SPORT[g]}/team/roster/_/id/{tid}')
    new_recs = pmap(lambda x: build_rec(x[0], x[1], x[2], None), new_hits, 12)

    # ---- tracked pools: registry + approved/promoted discoveries (rebuilt from ESPN by athlete id) ----
    pid_max = {'M': max(int(r['pid'][-4:]) for r in reg if r['g'] == 'M'), 'W': max(int(r['pid'][-4:]) for r in reg if r['g'] == 'W')}
    for d in disc_in:
        if str(d.get('pid', '')).startswith('JSG-'):
            g = d['pid'][4]
            pid_max[g] = max(pid_max[g], int(d['pid'][-4:]))
    promoted_recs, to_promote = [], []
    for d in disc_in:
        st_ = str(d.get('status', '')).strip()
        aid = str(d.get('aid', '')).strip()
        if st_ not in ('Approved', 'Promoted') or aid not in by_aid: continue
        g, tid, a = by_aid[aid]
        if st_ == 'Promoted' and str(d.get('pid', '')).startswith('JSG-'):
            pid = d['pid']
        else:
            pid_max[g] += 1; pid = f"JSG-{g}-{pid_max[g]:04d}"
        rec = build_rec(g, tid, a, pid)
        promoted_recs.append(rec)
        if st_ == 'Approved':
            to_promote.append((d, rec))

    pools = {g: [r for r in reg if r['g'] == g] + [r for r in promoted_recs if r['g'] == g] for g in 'MW'}

    # ---- game data ----
    everyone = [r for g in 'MW' for r in pools[g]] + new_recs
    logs = dict(zip([r['pid'] or 'D' + r['aid'] for r in everyone],
                    pmap(lambda r: gamelog(r['g'], r['aid']) if r['aid'] else {}, everyone, 16)))
    window = [now_cut - dt.timedelta(days=i) for i in range(min(7, (now_cut - prev_cut).days))]
    box = {g: box_scores(g, window) for g in 'MW'}
    for r in everyone:
        k = r['pid'] or 'D' + r['aid']
        for eid, line in box[r['g']].get(r['aid'], {}).items():
            logs[k].setdefault(eid, line)
    key = lambda r: r['pid'] or 'D' + r['aid']
    snap = {c: {key(r): season_line(logs[key(r)], cut) for r in everyone} for c, cut in (('now', now_cut), ('prev', prev_cut), ('week', week_cut))}

    res = {c: {} for c in snap}
    for c in snap:
        for g in 'MW':
            res[c].update(score_pool(pools[g], {r['pid']: snap[c][r['pid']] for r in pools[g]}))
    # discoveries scored against the current pool of their gender
    for r in new_recs:
        r['pid'] = 'D' + r['aid']
    for g in 'MW':
        for r in [x for x in new_recs if x['g'] == g]:
            tmp = score_pool(pools[g] + [r], {q['pid']: snap['now'][q['pid']] for q in pools[g] + [r]})
            res['now'][r['pid']] = tmp[r['pid']]

    plan = []
    def chunked(rng_tab, rows, size=120):
        for i in range(0, len(rows), size):
            plan.append({'tool': 'append_values', 'range': f"'{rng_tab}'!A1", 'values': rows[i:i + size]})

    # ---- FEED rows: players with games since the last cutoff ----
    feed = []
    for g in 'MW':
        for r in pools[g]:
            s = snap['now'][r['pid']]
            if not s: continue
            if s['last']['date'] <= prev_cut and inp.get('last_cutoff'): continue
            feed.append([r['pid'], run.isoformat(), s['GP'], s['MIN'], s['PTS'], s['REB'], s['AST'], s['STL'], s['BLK'], s['FG'], s['TP'], s['FT'],
                         last_txt(s), s['last']['date'].isoformat()])
    chunked('FEED', feed)

    # ---- rank movement ----
    def reason(r):
        a, b = res['prev'][r['pid']], res['now'][r['pid']]
        s0, s1 = snap['prev'][r['pid']], snap['now'][r['pid']]
        ng = (s1['GP'] if s1 else 0) - (s0['GP'] if s0 else 0)
        parts = []
        if ng > 0:
            gs = sorted((x for x in logs[r['pid']].values() if prev_cut < x['date'] <= now_cut), key=lambda x: x['date'])
            if gs:
                k = len(gs)
                parts.append(f"{k}G {sum(x['PTS'] for x in gs) / k:.1f}p/{sum(x['REB'] for x in gs) / k:.1f}r/{sum(x['AST'] for x in gs) / k:.1f}a {sum(x['MIN'] for x in gs) / k:.0f}m")
        else:
            parts.append('no games')
        for lab, k in (('Prod', 'prod'), ('Upside', 'up'), ('Transfer', 's_tr'), ('NIL', 'nil')):
            if a[k] != b[k]: parts.append(f'{lab} {a[k]:g}->{b[k]:g}')
        if a['dir'] != b['dir']: parts.append('now ' + b['dir'].split(' (')[0])
        return '; '.join(parts)
    moves = []
    for g in 'MW':
        for r in pools[g]:
            a, b = res['prev'][r['pid']], res['now'][r['pid']]
            if a['rank'] != b['rank']:
                moves.append((g, r, a['rank'], b['rank'], a['rank'] - b['rank']))
    moves.sort(key=lambda x: -abs(x[4]))
    hist = []
    for g in 'MW':
        for (gg, r, pr, nr, dlt) in [m for m in moves if m[0] == g and abs(m[4]) >= 3][:25]:
            rs = reason(r)
            hist.append([run.isoformat(), r['pid'], r['name'], 'Men' if g == 'M' else 'Women', r['school'], pr, nr, dlt, rs,
                         res['prev'][r['pid']]['jsg'], res['now'][r['pid']]['jsg'],
                         f"{run:%m/%d}: #{pr}->#{nr} ({'+' if dlt > 0 else ''}{dlt})"])
    chunked('RANK HISTORY', hist)

    # ---- discoveries ----
    seq0 = 1 + sum(1 for d in disc_in if str(d.get('id', '')).startswith(f'D-{run:%y%m%d}'))
    disc_rows = []
    for i, r in enumerate(sorted(new_recs, key=lambda x: -res['now'][x['pid']]['jsg'])):
        o = res['now'][r['pid']]; s = snap['now'][r['pid']]; b = r['base']
        prio = 'High' if o['jsg'] >= 70 else 'Medium' if o['jsg'] >= 55 else 'Low'
        tr = (f"{o['dir']} (transfer score {o['t']:g})")
        nilt = f"{o['ntier']} (NIL score {o['nil']:g}; {r['conf']} exposure, {r['sub'] or r['states']} market)"
        avg25 = (f"25-26 at {r['stats_school']}: {b.get('PTS')} ppg / {b.get('REB')} rpg / {b.get('AST')} apg in {b.get('MIN')} mpg ({b.get('GP')} GP)" if b else '25-26: no D1 stats')
        avg26 = (f"26-27: {s['PTS']} ppg / {s['REB']} rpg / {s['AST']} apg in {s['MIN']} mpg ({s['GP']} GP)" if s else '26-27: no games yet')
        disc_rows.append([f'D-{run:%y%m%d}-{seq0 + i:02d}', r['name'], r['school'], r['conf'], 'Men' if r['g'] == 'M' else 'Women', r['pos'],
                          {'Fr': 'Fr.', 'So': 'So.', 'Jr': 'Jr.', 'Sr': 'Sr.', 'Gr': 'Gr.'}[r['cls']], r['states'], r['hometown'],
                          'Not verified (ESPN lists birthplace only)', f"Born {r['hometown']} (ESPN 2026-27 roster birthplace). Confirm hometown/HS on the official roster before approving.",
                          last_txt(s) if s else 'No 2026-27 games yet', avg26 + ' | ' + avg25, o['jsg'], tr, nilt, run.isoformat(),
                          f"{r['roster_url']} | https://www.espn.com/{SPORT[r['g']]}/player/_/id/{r['aid']}", prio, 'Pending Review', r['aid'], '', ''])
    chunked('JSG | REGIONAL PROSPECT DISCOVERY', disc_rows, 60)

    # ---- promotions (Approved -> DB rows) ----
    next_row = dict(inp.get('next_row', {'M': 571, 'W': 492}))
    promos = []
    for d, r in to_promote:
        g = r['g']; pid = r['pid']; row = next_row[g]; next_row[g] += 1
        b = r.get('base') or {}
        fm = formulas(row)
        yr = {'Fr': 'Fr.', 'So': 'So.', 'Jr': 'Jr.', 'Sr': 'Sr.', 'Gr': 'Gr./5th'}.get(r['cls'], r['cls'])
        vals = {'Player ID': pid, 'Player Name': r['name'], 'Gender': 'Men' if g == 'M' else 'Women', 'Current School': r['school'],
                'Conference': r['conf'], 'Program Tier': r['tier'], 'Position': r['pos'], 'Height': r['ht'] or '-', 'Academic Year': yr,
                'Eligibility Status': {'Fr': '~4 seasons (est.)', 'So': '~3 seasons (est.)', 'Jr': '~2 seasons (est.)', 'Sr': '~1 season (est.)', 'Gr': 'Final season (est.)'}[r['cls']],
                'Hometown': r['hometown'] + ' (birthplace)', 'State': r['states'], 'High School': 'Not verified',
                '25-26 GP': b.get('GP', ''), '25-26 GS': b.get('GS', ''), '25-26 MPG': b.get('MIN', ''), '25-26 PPG': b.get('PTS', ''),
                '25-26 RPG': b.get('REB', ''), '25-26 APG': b.get('AST', ''), '25-26 SPG': b.get('STL', ''), '25-26 BPG': b.get('BLK', ''),
                '25-26 FG%': num(b['FG%']) / 100 if b.get('FG%') else '', '25-26 3PT%': num(b['3P%']) / 100 if b.get('3P%') else '',
                '25-26 FT%': num(b['FT%']) / 100 if b.get('FT%') else '', 'Advanced Metrics': '', 'Previous Schools': '-',
                'Representation Status': 'Unknown / insufficient evidence', 'Agency': '-', 'Instagram': 'Not researched', 'X/Twitter': 'Not researched',
                'Regional Connection': '; '.join(f'{k}: {v}' for k, v in r['ties'].items()),
                'Scouting Notes': f"Added from REGIONAL PROSPECT DISCOVERY ({d.get('id')}), approved by JSG. ESPN birthplace only - confirm hometown/HS.",
                'Sources': r['roster_url'], 'Last Verified': run.isoformat(), 'Sub-Region': f"{r['states']}: {r.get('sub') or '-'}",
                '2025-26 Stats School': r.get('stats_school') or ('No 2025-26 D1 stats' if r['cls'] != 'Fr' else 'Freshman - no college stats'),
                'Stats Source': f"https://www.espn.com/{SPORT[g]}/player/stats/_/id/{r['aid']}", 'Data Source': 'ESPN roster sweep (discovery)',
                'Verification Status': f"Discovered {d.get('id')}; approved {run.isoformat()}; birthplace only - official roster check pending",
                'Score: Eligibility (10%)': r['elig'], 'Score: Regional (10%)': r['reg'], 'Score: Representation (5%)': 50,
                'ESPN Athlete ID': r['aid'], 'Base Prod Index 25-26': r['base_pi'] if r['base_pi'] is not None else '',
                'Base MPG 25-26': r['base_min'] if r['base_min'] is not None else '', 'Base PPG 25-26': r['base_pts'] if r['base_pts'] is not None else '',
                'Home Market Score': r['market'], 'Upside Base (class+size)': r['youth_base'], 'Size Score': r['size'], 'Class Key': r['cls'], 'Fit State': r['fit']}
        rowvals = [fm[c] if c in fm else vals.get(c, '') for c in COLS]
        # spilled cells (BP:BZ) must stay empty
        for c in COLS[COLS.index('26-27 MPG'):COLS.index('Last Game Date') + 1]:
            rowvals[COLS.index(c)] = None
        plan.append({'tool': 'update_formulas', 'range': f"'{DB[g]}'!A{row}:{C['Base Direction']}{row}", 'formulas': [rowvals]})
        plan.append({'tool': 'update_values', 'range': f"'JSG | REGIONAL PROSPECT DISCOVERY'!T{d['row']}", 'values': [['Promoted']]})
        plan.append({'tool': 'update_values', 'range': f"'JSG | REGIONAL PROSPECT DISCOVERY'!W{d['row']}", 'values': [[pid]]})
        promos.append((pid, r['name'], r['school']))

    plan.append({'tool': 'update_values', 'range': "'ENGINE LOG'!B1:B4", 'values': [[run.isoformat()], [now_cut.isoformat()], [next_row['M']], [next_row['W']]]})
    plan.append({'tool': 'append_values', 'range': "'ENGINE LOG'!A7", 'values': [[run.isoformat(), now_cut.isoformat(), len(feed), len(hist), len(disc_rows), len(promos), 'see Gmail']]})
    json.dump(plan, open(os.path.join(outdir, 'plan.json'), 'w'), default=str)
    os.makedirs(os.path.join(outdir, 'ops'), exist_ok=True)
    for i, op in enumerate(plan, 1):
        json.dump(op, open(os.path.join(outdir, 'ops', f'{i:02d}.json'), 'w'), default=str)

    # ---- emails ----
    write_daily(outdir, run, now_cut, prev_cut, pools, res, snap, logs, moves, disc_rows, promos, represented, reason)
    if inp.get('weekly'):
        write_weekly(outdir, run, now_cut, week_cut, pools, res, snap, represented, disc_in, disc_rows)
    print(json.dumps({'run_date': run.isoformat(), 'stats_cutoff': now_cut.isoformat(), 'prev_cutoff': prev_cut.isoformat(),
                      'feed_rows': len(feed), 'history_rows': len(hist), 'new_discoveries': len(disc_rows), 'promoted': len(promos),
                      'plan_ops': len(plan), 'players_with_2026_27_games': sum(1 for k, v in snap['now'].items() if v)}))

# ---------------------------------------------------------------- email builders (compact HTML) ----
G = lambda g: 'M' if g == 'M' else 'W'
def sec(title, lines, note=''):
    body = '<br>'.join(lines) if lines else '<i>None today.</i>'
    return f'<p><b style="color:#1F3864">{title}</b>{"<br><i>" + note + "</i>" if note else ""}<br>{body}</p>'
def sgn(d): return f"+{d}" if d > 0 else str(d)
def line26(s): return f"{s['PTS']}p/{s['REB']}r/{s['AST']}a, {s['GP']}G" if s else 'no 26-27 games'

def write_daily(outdir, run, now_cut, prev_cut, pools, res, snap, logs, moves, disc_rows, promos, represented, reason):
    allp = [r for g in 'MW' for r in pools[g]]
    risers = [m for m in moves if m[4] > 0][:10]
    fallers = sorted([m for m in moves if m[4] < 0], key=lambda m: m[4])[:10]
    mv = lambda lst: [f"{i}. {r['name']} ({G(g)}, {r['school']}, {r['states']}) #{a}&rarr;#{b} ({sgn(d)}): {reason(r)}" for i, (g, r, a, b, d) in enumerate(lst, 1)]
    perf = sorted(((game_score(x), r, x) for r in allp for x in logs.get(r['pid'], {}).values() if prev_cut < x['date'] <= now_cut), key=lambda t: -t[0])
    perf_l = [f"{i}. {r['name']} ({G(r['g'])}, {r['school']}) {x['at']} {x['opp']} {x['res']}: {int(x['PTS'])} pts, {int(x['REB'])} reb, {int(x['AST'])} ast, "
              f"{int(x['STL'])} stl, {int(x['BLK'])} blk, {x['fgm']}-{x['fga']} FG, {int(x['MIN'])} min" for i, (_, r, x) in enumerate(perf[:10], 1)]
    njs = sorted(((x['PTS'], game_score(x), r, x) for r in allp if 'NJ' in r['states'] for x in logs.get(r['pid'], {}).values() if prev_cut < x['date'] <= now_cut),
                 key=lambda t: (-t[0], -t[1]))
    nj_l = [f"{i}. {r['name']} ({G(r['g'])}, {r['school']}) {x['at']} {x['opp']} {x['res']}: {int(x['PTS'])} pts ({x['fgm']}-{x['fga']} FG), {int(x['REB'])} reb, {int(x['AST'])} ast, "
            f"{int(x['MIN'])} min; season {snap['now'][r['pid']]['PTS'] if snap['now'][r['pid']] else '-'} ppg"
            for i, (_, _, r, x) in enumerate([t for t in njs if t[2]['g'] == 'M'][:5] + [t for t in njs if t[2]['g'] == 'W'][:5], 1)]
    nj_up = [m for m in moves if m[4] > 0 and 'NJ' in m[1]['states']][:10]
    nj_dn = sorted([m for m in moves if m[4] < 0 and 'NJ' in m[1]['states']], key=lambda m: m[4])[:10]
    disc = lambda st: [f"{d[1]} ({d[4][0]}, {d[2]}, {d[6]}) - born {d[8]}; est. JSG {d[13]}; {d[18]} priority" for d in disc_rows if st in d[7]]
    mm = sorted([r for r in allp if not r['hm'] and res['now'][r['pid']]['dir'] == 'Transfer Up candidate' and res['now'][r['pid']]['prod'] >= 80],
                key=lambda r: -res['now'][r['pid']]['jsg'])[:10]
    mm_l = [f"{i}. {r['name']} ({G(r['g'])}, {r['school']}, {r['conf']}, {r['cls']}) prod {res['now'][r['pid']]['prod']:g}, JSG {res['now'][r['pid']]['jsg']}; {line26(snap['now'][r['pid']])}"
            for i, r in enumerate(mm, 1)]
    rep = sorted([r for r in allp if res['now'][r['pid']]['jsg'] >= 70 and r['pid'] not in represented and not r.get('agency') and not r['academy']],
                 key=lambda r: -res['now'][r['pid']]['jsg'])[:10]
    rep_l = [f"{i}. {r['name']} ({G(r['g'])}, {r['school']}, {r['states']}, {r['cls']}) JSG {res['now'][r['pid']]['jsg']} - {res['now'][r['pid']]['dir'].split(' (')[0]}"
             for i, r in enumerate(rep, 1)]
    nplayed = sum(1 for r in allp if snap['now'][r['pid']] and snap['now'][r['pid']]['last']['date'] > prev_cut)
    out = [f'<div style="font-family:Arial;font-size:13px">',
           f'<p><b>JSG Daily Regional Report - {run:%a %b %d, %Y}</b><br>Games {prev_cut + dt.timedelta(days=1):%b %d}-{now_cut:%b %d}: {nplayed} tracked NJ/NY/PA players played. '
           f'Ranks are within gender. <a href="{SHEET_URL}">Open the database</a></p>']
    if promos: out.append(f"<p>Promoted to the master database: {', '.join(f'{a} {b} ({c})' for a, b, c in promos)}.</p>")
    out += [sec('1. Top 10 risers', mv(risers)), sec('2. Top 10 fallers', mv(fallers)), sec('3. Best individual performances', perf_l),
            sec('4. NJ players - top scorers', nj_l, 'Most points by NJ-tied players in the games above (top 5 men, then top 5 women).'),
            sec('5. NJ players - top risers', mv(nj_up)), sec('6. NJ players - top fallers', mv(nj_dn))]
    for i, st in enumerate(('NJ', 'PA', 'NY')):
        out.append(sec(f'{7 + i}. New {st} prospects (pending your review in the Discovery tab)', disc(st)))
    out += [sec('10. Mid-major players showing high-major potential', mm_l),
            sec('11. High-priority representation opportunities', rep_l, 'JSG 70+ with no representation recorded in the sheet - verify before outreach.'),
            f'<p><b style="color:#1F3864">12. Google Sheet</b><br><a href="{SHEET_URL}">{SHEET_URL}</a></p>',
            '<p style="font-size:11px;color:#666">Automated from ESPN game logs and box scores. Follow NCAA, state NIL and school compliance rules before outreach.</p></div>']
    open(os.path.join(outdir, 'email.html'), 'w').write(''.join(out))
    open(os.path.join(outdir, 'email_subject.txt'), 'w').write(f'JSG Daily Regional Report - {run:%b %d, %Y}')

def write_weekly(outdir, run, now_cut, week_cut, pools, res, snap, represented, disc_in, disc_rows):
    out = [f'<div style="font-family:Arial;font-size:13px"><p><b>JSG Weekly Regional Scouting Report - week ending {now_cut:%b %d, %Y}</b><br>'
           f'Wk = rank change since {week_cut:%b %d} (within gender and state). <a href="{SHEET_URL}">Open the database</a></p>']
    for st in ('NJ', 'PA', 'NY'):
        for g in 'MW':
            lst = sorted([r for r in pools[g] if st in r['states']], key=lambda r: res['now'][r['pid']][st])[:25]
            lines = []
            for r in lst:
                o, w = res['now'][r['pid']], res['week'][r['pid']]
                d = w[st] - o[st]
                lines.append(f"{o[st]}. {r['name']} ({r['school']}, {r['cls']}) {o['jsg']}{' (' + sgn(d) + ')' if d else ''} - {line26(snap['now'][r['pid']])}")
            out.append(sec(f"Top 25 {st} {'Men' if g == 'M' else 'Women'}", lines))
    mv = sorted(((res['week'][r['pid']]['rank'] - res['now'][r['pid']]['rank'], g, r) for g in 'MW' for r in pools[g]), key=lambda x: -abs(x[0]))
    out.append(sec('Biggest ranking movements (week, within gender)',
                   [f"{r['name']} ({G(g)}, {r['school']}) #{res['week'][r['pid']]['rank']}&rarr;#{res['now'][r['pid']]['rank']} ({sgn(d)})" for d, g, r in mv[:15] if d]))
    br = sorted(((res['now'][r['pid']]['prod'] - res['week'][r['pid']]['prod'], g, r) for g in 'MW' for r in pools[g]
                 if snap['now'][r['pid']] and snap['now'][r['pid']]['GP'] >= 2), key=lambda x: -x[0])
    out.append(sec('New breakout players (production score up 10+ this week)',
                   [f"{r['name']} ({G(g)}, {r['school']}, {r['cls']}) prod {res['week'][r['pid']]['prod']:g}&rarr;{res['now'][r['pid']]['prod']:g}; {line26(snap['now'][r['pid']])}"
                    for d, g, r in br[:12] if d >= 10]))
    tu = sorted([r for g in 'MW' for r in pools[g] if res['now'][r['pid']]['dir'].startswith('Transfer Up')], key=lambda r: -res['now'][r['pid']]['t'])[:12]
    out.append(sec('Most promising transfer-upside candidates',
                   [f"{r['name']} ({G(r['g'])}, {r['school']}, {r['conf']}, {r['cls']}) transfer score {res['now'][r['pid']]['t']:g}, JSG {res['now'][r['pid']]['jsg']}" for r in tu]))
    rec = sorted([r for g in 'MW' for r in pools[g] if r['pid'] not in represented and not r.get('agency') and not r['academy']],
                 key=lambda r: -res['now'][r['pid']]['jsg'])[:12]
    out.append(sec('Recommended prospects for JSG outreach',
                   [f"{r['name']} ({G(r['g'])}, {r['school']}, {r['states']}, {r['cls']}) JSG {res['now'][r['pid']]['jsg']}, NIL {res['now'][r['pid']]['ntier']}" for r in rec],
                   'Highest JSG with no representation recorded. Check the OUTREACH TRACKER and verify representation first.'))
    pend = sum(1 for d in disc_in if str(d.get('status', '')).strip() == 'Pending Review') + len(disc_rows)
    out.append(f'<p>Discovery queue: {pend} players pending your review.</p></div>')
    open(os.path.join(outdir, 'weekly.html'), 'w').write(''.join(out))
    open(os.path.join(outdir, 'weekly_subject.txt'), 'w').write(f'JSG Weekly Regional Scouting Report - {now_cut:%b %d, %Y}')

if __name__ == '__main__':
    main()
