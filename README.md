# JSG engine

Daily JSG regional scouting update for the NJ/NY/PA D1 basketball database (private).

Run:
```
python3 make_inputs.py <dir>     # dir holds log.json, disc_a.json, disc_tw.json, rep.json (Sheets get_values results)
python3 engine.py <dir>/inputs.json
```
Outputs go to `<dir>/out/`: `ops/NN.json` (Sheets writes, apply in order), `email.html`, `email_subject.txt`, and on Mondays `weekly.html` / `weekly_subject.txt`.

- `registry.json`: tracked players (incl. personal-watch adds JSG-W-0487 Autumn McCall, JSG-W-0488 Gia Powell)
- Underclassman impact bonus (Fr/So, 2+ GP, up to +10 for MPG/PPG) is built into `engine.py` and the Sheet's JSG Target Score.
- Data: public ESPN stats only.

## Daily automation (GitHub Actions)
`.github/workflows/daily.yml` runs `run_daily.py` every day at 12:24 UTC (7:24 AM EST) and can be run manually from the Actions tab.
It reads the Sheet, runs the engine, writes the Sheet and emails the report. Needs repo secrets
`GOOGLE_SERVICE_ACCOUNT_JSON`, `SMTP_USER`, `SMTP_PASS`. Test locally with `DRY_RUN=1`.
