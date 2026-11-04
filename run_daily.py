"""Daily JSG run for GitHub Actions: read Sheet -> run engine -> write Sheet -> email.

Env (GitHub secrets):
  GOOGLE_SERVICE_ACCOUNT_JSON  full JSON key of a service account that has Editor access to the Sheet
  SMTP_USER                    Gmail address that sends the report (samir@jerseysportsgroup.com)
  SMTP_PASS                    Google app password for SMTP_USER
  REPORT_TO                    recipient (defaults to SMTP_USER)
Optional: SHEET_ID (defaults to the JSG database), DRY_RUN=1 (no Sheet writes, no email).
"""
import datetime as dt, json, os, re, smtplib, subprocess, sys, traceback
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

HERE = os.path.dirname(os.path.abspath(__file__))
SHEET_ID = os.environ.get('SHEET_ID', '1hZP-C68txx08Ny4RQfi8xqMgrER74j7Hv-9kOOqTVtQ')
DISC = "'JSG | REGIONAL PROSPECT DISCOVERY'"
READS = {'log.json': "'ENGINE LOG'!B1:B4", 'disc_a.json': f'{DISC}!A2:A', 'disc_tw.json': f'{DISC}!T2:W',
         'rep.json': "'REPRESENTATION RESEARCH'!A5:F"}
DRY = os.environ.get('DRY_RUN') == '1'
WORK = os.path.join(HERE, 'run')


def sheets():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    info = json.loads(os.environ['GOOGLE_SERVICE_ACCOUNT_JSON'])
    creds = service_account.Credentials.from_service_account_info(info, scopes=['https://www.googleapis.com/auth/spreadsheets'])
    return build('sheets', 'v4', credentials=creds, cache_discovery=False).spreadsheets().values()


def iso(v):
    """ENGINE LOG dates may come back formatted (11/3/2026); the engine needs YYYY-MM-DD."""
    s = str(v).strip()
    for f in ('%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y', '%Y/%m/%d'):
        try: return dt.datetime.strptime(s, f).date().isoformat()
        except ValueError: pass
    return s


def send(subject, html):
    user, pw = os.environ.get('SMTP_USER'), os.environ.get('SMTP_PASS')
    to = os.environ.get('REPORT_TO') or user
    if DRY or not (user and pw):
        print(f'[email skipped] {subject}'); return
    m = MIMEMultipart('alternative'); m['Subject'], m['From'], m['To'] = subject, user, to
    m.attach(MIMEText(html, 'html', 'utf-8'))
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(user, pw); s.sendmail(user, [to], m.as_string())
    print(f'[email sent] {subject} -> {to}')


def main():
    os.makedirs(WORK, exist_ok=True)
    api = None if os.environ.get('LOCAL_INPUTS') else sheets()
    # 1. read the 4 input ranges
    for fn, rng in READS.items():
        if api is None:
            continue  # LOCAL_INPUTS=1: files already in run/
        vals = api.get(spreadsheetId=SHEET_ID, range=rng).execute().get('values', [])
        if fn == 'log.json':
            vals = [[iso(r[0])] if r else [] for r in vals]
        json.dump({'range': rng, 'values': vals}, open(os.path.join(WORK, fn), 'w'))
    # 2. engine
    subprocess.run([sys.executable, os.path.join(HERE, 'make_inputs.py'), WORK], check=True)
    subprocess.run([sys.executable, os.path.join(HERE, 'engine.py'), os.path.join(WORK, 'inputs.json')], check=True)
    out = os.path.join(WORK, 'out')
    ops_dir = os.path.join(out, 'ops')
    ops = [json.load(open(os.path.join(ops_dir, f))) for f in sorted(os.listdir(ops_dir)) if f.endswith('.json')]
    # 3. apply Sheet writes in order
    for i, op in enumerate(ops, 1):
        t, rng = op['tool'], op['range']
        n = len(op.get('values') or op.get('formulas') or [])
        print(f'op {i:02d} {t} {rng} rows={n}')
        if DRY or api is None:
            continue
        if t == 'append_values':
            api.append(spreadsheetId=SHEET_ID, range=rng, valueInputOption='USER_ENTERED',
                       insertDataOption='INSERT_ROWS', body={'values': op['values']}).execute()
        elif t == 'update_values':
            api.update(spreadsheetId=SHEET_ID, range=rng, valueInputOption='USER_ENTERED', body={'values': op['values']}).execute()
        elif t == 'update_formulas':
            api.update(spreadsheetId=SHEET_ID, range=rng, valueInputOption='USER_ENTERED', body={'values': op['formulas']}).execute()
        else:
            raise RuntimeError(f'unknown op {t}')
    # 4. spot-check
    if api is not None and not DRY:
        b1 = api.get(spreadsheetId=SHEET_ID, range="'ENGINE LOG'!B1").execute().get('values', [['']])[0][0]
        run_date = json.load(open(os.path.join(WORK, 'inputs.json')))['run_date']
        if iso(b1) != run_date:
            raise RuntimeError(f'spot-check failed: ENGINE LOG B1 is {b1!r}, expected {run_date}')
    # 5. email(s)
    send(open(os.path.join(out, 'email_subject.txt')).read().strip(), open(os.path.join(out, 'email.html')).read())
    if os.path.exists(os.path.join(out, 'weekly.html')):
        send(open(os.path.join(out, 'weekly_subject.txt')).read().strip(), open(os.path.join(out, 'weekly.html')).read())


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        tb = traceback.format_exc(); print(tb)
        try:
            send('JSG daily update did not run',
                 f'<p>The daily JSG update failed on GitHub Actions.</p><pre>{re.sub("<", "&lt;", tb[-3000:])}</pre>')
        except Exception:
            traceback.print_exc()
        sys.exit(1)
