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
