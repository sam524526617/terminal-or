import sys, json, numpy as np, pandas as pd
sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import robot as R, plan as P
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
rng = np.random.default_rng(3)
fin = pd.Timestamp("2026-11-10 17:00", tz="UTC")
idx = pd.date_range(fin - pd.Timedelta(days=730), fin, freq="15min")
ny = idx.tz_convert("America/New_York"); j, h = ny.dayofweek, ny.hour
ok = ~((j == 5) | ((j == 4) & (h >= 17)) | ((j == 6) & (h < 18)) | (h == 17))
idx = idx[ok]
c = 2400 * np.exp(np.cumsum(rng.normal(0.00002, 0.0012, len(idx))))
o = np.r_[c[0], c[:-1]]
b15 = pd.DataFrame({"open": o, "close": c, "high": np.maximum(o, c) * (1 + np.abs(rng.normal(0, .0006, len(c)))),
                    "low": np.minimum(o, c) * (1 - np.abs(rng.normal(0, .0006, len(c))))}, index=idx.tz_convert("Europe/Paris"))
hb = b15.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
intra = {"barres": b15[b15.index >= b15.index[-1] - pd.Timedelta(days=60)], "heures": hb}
now = datetime(2026, 11, 10, 18, 5, tzinfo=ZoneInfo("Europe/Paris"))
X = R.donnees(intra, now); print("D", len(X["D"]), X["D"]["fini"].sum(), "m15", len(X["m15"]), "h4", len(X["h4"]))
debut = (X["now"] - pd.Timedelta(days=55))
etat = {"version": 1, "debut": debut.isoformat(), "trades": {}, "vus": {}}
e2, vue, al = R.avancer(etat, intra, now, {"capital": 50000})
for k, S in vue["strats"].items(): print(k, S["statut"], round(S["poids"], 2), {x: S["stats"].get(x) for x in ("n", "total", "reussite")}, "ouvert" if S["ouvert"] else "")
print("robot", {k: vue["robot"].get(k) for k in ("n", "total", "pf", "dd")}, round(vue["dollars"]), "attente", vue["attente"], "pannes", vue["pannes"])
print("alertes", len(al), al[:2])
# relance : rien ne change, rien n'est renvoyé
e3, vue3, al3 = R.avancer(json.loads(json.dumps(e2)), intra, now, {"capital": 50000})
print("relance identique", e3["trades"] == e2["trades"], "alertes", len(al3))
# coûts / cohérence
for t in vue["historique"][:6]: print(t["strat"], t["sens"], round(t["entree"],2), round(t["sortie"],2), t["issue"], round(t["R"],2), t.get("poids"))
bad = [t for t in vue["historique"] if not (-1.6 < t["R"] < 15)]; print("R aberrants", len(bad))
html = R.bloc_robot(vue) + R.carte_accueil(vue); print(len(html), "nan" in html.lower(), "None" in html)
# premier démarrage
e4, v4, a4 = R.avancer(None, intra, now, {"capital": 50000}); print("démarrage", e4["debut"], len(e4["trades"]), len(v4["ouverts"]))
