"""Join verified rosters, stats and prior-file claims; compute JSG category scores."""
import csv, json, re
from collections import defaultdict

def n(s): return re.sub(r'[^a-z0-9]', '', str(s or '').lower())

# ---------- programs ----------
CONF_TIER = {
    'Atlantic Coast': 'High-major', 'Big Ten': 'High-major', 'Big 12': 'High-major',
    'Southeastern': 'High-major', 'Big East': 'High-major',
    'Atlantic 10': 'Upper mid-major', 'American': 'Upper mid-major', 'Mountain West': 'Upper mid-major',
    'West Coast': 'Upper mid-major', 'Pac-12': 'Upper mid-major', 'Missouri Valley': 'Upper mid-major',
    'Sun Belt': 'Upper mid-major', 'Conference USA': 'Upper mid-major', 'Coastal': 'Upper mid-major',
    'Mid-American': 'Upper mid-major', 'Summit League': 'Upper mid-major', 'Horizon League': 'Upper mid-major',
    'Big West': 'Upper mid-major',
    'Ivy League': 'Mid-major', 'Metro': 'Mid-major', 'Patriot League': 'Mid-major', 'America East': 'Mid-major',
    'Southern': 'Mid-major', 'Big Sky': 'Mid-major', 'Atlantic Sun': 'Mid-major', 'United Athletic': 'Mid-major',
    'Big South': 'Mid-major', 'Southland': 'Mid-major', 'Ohio Valley': 'Mid-major',
    'Northeast': 'Low-major', 'Southwestern': 'Low-major', 'Mid-Eastern': 'Low-major',
}
CONF_LABEL = {'Metro': 'MAAC (Metro)', 'Coastal': 'CAA (Coastal)', 'Atlantic Coast': 'ACC', 'Southeastern': 'SEC',
              'Mid-American': 'MAC', 'Southwestern': 'SWAC', 'Mid-Eastern': 'MEAC', 'Northeast': 'NEC',
              'United Athletic': 'UAC', 'American': 'American (AAC)', 'Atlantic Sun': 'ASUN'}
class _P(dict):
    def get(self, k, d=None):
        return dict.get(self, n(k), d)
programs = _P()
for l in open('programs_raw.txt'):
    s, c = l.strip().split('|')
    programs[n(s)] = c

# ---------- regions ----------
NJ = {
 'North': 'bayonne bergenfield edgewater elmwoodpark glenridge hillside hoboken irvington maywood montvale morrisplains northcaldwell oldtappan orange pequannock ramsey roselle shorthills springfield summit totowa union waldwick wayne westfield paterson teaneck franklin newark closter sparta linden unioncity pomptonplains chatham randolph rosellepark montclair wyckoff paramus englewood jefferson jeffersontownship ridgewood nutley eastorange jerseycity verona northarlington hawthorne westorange morristown parsippany tenafly southorange madison fairfield roseland livingston hamburg',
 'Central': 'baskingridge farhills somerville montgomery princeton southbrunswick northbrunswick newbrunswick fords manalapan keyport longbranch atlantichighlands middletown springlake southplainfield holmdel pennington freehold somerset fairhaven coltsneck leonardo hightstown hillsborough seagirt trenton edison oceanport monmouthbeach bradleybeach oakhurst springlakeheights manasquan ewing plainfield pointpleasantbeach rumson lawrenceville manchester tomsriver littlesilver marlboro colonia barnegat rahway bellemead oldbridge southamboy clark avenel neptune bedminster',
 'South': 'blackwood burlington camden haddonheights mountlaurel newfield pemberton pennsauken pitman pleasantville shamong somerdale swedesboro woolwichtownship atlanticcity marlton medfordlakes sewell sicklerville linwood cherryhill woodbury mullicahill eggharbortownship eggharbortwp voorhees glassboro oceancity haddontownship willingboro chesterfield moorestown williamstown galloway woodbine lawnside merchantville medford',
}
NY = {
 'NYC': 'corona flushing laurelton manhattan queensvillage queensbridge washingtonheights queens bronx thebronx brooklyn statenisland newyork newyorkcity harlem farrockaway cambriaheights jamaica whitestone',
 'Long Island': 'amityville deerpark elmont farmingdale franklinsquare greatneck huntington merrick mineola northbabylon oysterbay roosevelt smithtown stjames westbury wheatleyheights wyandanch dixhills masticbeach baldwin wantagh hewlett plainview westbabylon westhempstead bayshore centralislip longisland bellport rockvillecentre brookville floralpark',
 'Westchester': 'armonk bronxville chappaqua eastirvington rye tuckahoe whiteplains harrison larchmont yonkers portchester scarsdale newrochelle purchase somers ryebrook ardsley mountvernon westchester',
 'Hudson Valley': 'beacon newburgh kingston monroe suffern wallkill hudson eastfishkill wappingersfalls montgomery tappan newcity nanuet nyack warwick redhook clintondale middletown poughkeepsie hopewelljunction lagrangeville catskill',
}
PA = {
 'Philadelphia area': 'ardmore bensalem bluebell chestersprings doylestown dresher glenmills glenside haverford holland huntingdonvalley kingofprussia malvern merionstation phoenixville plymouthmeeting pottstown southampton upperdarby villanova wayne wyndmoor philadelphia conshohocken quakertown havertown horsham langhorne upperdublin upperdublintownship fortwashington brynmawr berwyn radnor lansdowne chester drexelhill westchester media newtownsquare devon coatesville yardley harleysville lafayettehill willowgrove wyncote norristown schwenksville pennsburg collegeville levittown chaddsford downingtown gladwyne aston',
 'Pittsburgh area': 'allisonpark baldwin gibsonia mckeesrocks midland moontownship munhall natronaheights peterstownship wilkinsburg pittsburgh washington venetia butler sewickley mars mcmurray bridgeville wexford monroeville greensburg mcdonald hampton beaverfalls rochester latrobe blairsville glassport',
 'Central PA': 'altoona camphill chambersburg dillsburg hershey lewisburg mechanicsburg newoxford selinsgrove denver enola littlestown lebanon elizabethtown mifflincounty mifflin york redlion harrisburg statecollege williamsport middletown mercersburg mcsherrystown gettysburg jerseyshore',
 'Other - Lehigh Valley': 'fogelsville orefield nazareth allentown bethlehem easton northampton',
 'Other - Northwest PA (Erie)': 'erie edinboro eastspringfield',
 'Other - Northeast PA': 'eaststroudsburg scranton shavertown tobyhanna dunmore',
 'Other - Berks County': 'reading westreading blandon',
 'Other - Western PA': 'johnstown berlin farrell indiana',
}
def region(state, town):
    t = n(town)
    tables = {'NJ': NJ, 'NY': NY, 'PA': PA}[state]
    for k, v in tables.items():
        if t in v.split(): return k
    return {'NJ': 'Central', 'NY': 'Upstate NY', 'PA': 'Other PA'}[state]

def parse_home(h):
    m = re.match(r'(.*?),\s*(NJ|NY|PA|N\.J\.|N\.Y\.|Pa\.)\b', h)
    if not m: return h.split(',')[0].strip(), None
    st = {'N.J.': 'NJ', 'N.Y.': 'NY', 'Pa.': 'PA'}.get(m.group(2), m.group(2))
    return m.group(1).strip(), st


# HS / prep ties
def cls_key(c):
    c = c.replace('.', '').lower()
    if c in ('fr', '1st', 'fy', 'freshman'): return 'Fr'
    if c in ('rfr',): return 'R-Fr'
    if c in ('so', '2nd'): return 'So'
    if c in ('rso',): return 'R-So'
    if c in ('jr', '3rd'): return 'Jr'
    if c in ('rjr',): return 'R-Jr'
    if c in ('sr', '4th'): return 'Sr'
    if c in ('rsr',): return 'R-Sr'
    if c in ('gr', '5th', 'fifth year', 'red 5th'): return 'Gr'
    if c in ('6th',): return '6th'
    return c
ELIG = {'Fr': (100, '~4 seasons (est.)'), 'R-Fr': (100, '~4 seasons (est.)'), 'So': (85, '~3 seasons (est.)'),
        'R-So': (88, '~3 seasons (est.)'), 'Jr': (60, '~2 seasons (est.)'), 'R-Jr': (65, '~2 seasons (est.)'),
        'Sr': (35, '~1 season (est.)'), 'R-Sr': (40, '~1 season (est.)'), 'Gr': (15, 'Final season (est.)'),
        '6th': (5, 'Final season (est.)')}
YOUTH = {'Fr': 85, 'R-Fr': 82, 'So': 75, 'R-So': 72, 'Jr': 55, 'R-Jr': 52, 'Sr': 35, 'R-Sr': 32, 'Gr': 20, '6th': 15}
TIER_MULT = {'High-major': 1.30, 'Upper mid-major': 1.10, 'Mid-major': 1.00, 'Low-major': 0.85}
TIER_EXPO = {'High-major': 100, 'Upper mid-major': 70, 'Mid-major': 50, 'Low-major': 35}

def inches(h):
    m = re.match(r"(\d)\D+(\d+)", h or '')
    return int(m.group(1)) * 12 + int(m.group(2)) if m else None
def f(x):
    try: return float(x)
    except: return None


players = []
