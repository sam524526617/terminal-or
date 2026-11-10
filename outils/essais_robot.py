# -*- coding: utf-8 -*-
"""Essais du robot papier sur des données simulées : registre stable, reprise d'un ancien registre, plafonds."""
import json, os, sys
from datetime import datetime
from zoneinfo import ZoneInfo
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import robot as R

def marche(graine, base, vol, weekend):
    rng = np.random.default_rng(graine)
    fin = pd.Timestamp("2026-11-10 17:00", tz="UTC")
    idx = pd.date_range(fin - pd.Timedelta(days=730), fin, freq="15min")
    if not weekend:
        ny = idx.tz_convert("America/New_York"); j, h = ny.dayofweek, ny.hour
        idx = idx[~((j == 5) | ((j == 4) & (h >= 17)) | ((j == 6) & (h < 18)) | (h == 17))]
    c = base * np.exp(np.cumsum(rng.normal(0, vol, len(idx)))); o = np.r_[c[0], c[:-1]]
    b = pd.DataFrame({"open": o, "close": c, "high": np.maximum(o, c) * (1 + np.abs(rng.normal(0, vol / 2, len(c)))),
                      "low": np.minimum(o, c) * (1 - np.abs(rng.normal(0, vol / 2, len(c))))}, index=idx)
    return {"barres": b[b.index >= b.index[-1] - pd.Timedelta(days=60)],
            "heures": b.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()}

ok = True
def verifie(nom, cond, detail=""):
    global ok; ok &= bool(cond); print(("  ok   " if cond else "  ÉCHEC ") + nom + (" · " + str(detail) if detail else ""))

M = {"XAU": marche(3, 2400, 0.0012, False), "BTC": marche(4, 70000, 0.003, True), "PEPE": marche(5, 0.000008, 0.006, True)}
now = datetime(2026, 11, 10, 18, 5, tzinfo=ZoneInfo("Europe/Paris"))
# 1. ancien registre (or seul, version 1) repris sans perte
X = R.donnees(M["XAU"], now)
ancien = {"version": 1, "debut": (X["now"] - pd.Timedelta(days=40)).isoformat(), "trades": {}, "vus": {}}
e1, v1, a1 = R.avancer(ancien, M["XAU"], now, {"capital": 50000}, rejeu=False)
n_or = len(e1["trades"])
verifie("ancien appel (or seul) : trades enregistrés", n_or > 5, n_or)
e2, v2, a2 = R.avancer(json.loads(json.dumps(e1)), M, now, {"capital": 50000})
verifie("passage au multi-marchés : les trades de l'or sont gardés à l'identique", all(e2["trades"][k] == v for k, v in e1["trades"].items()))
verifie("les nouveaux marchés démarrent maintenant, sans trade du passé", not any(k.startswith(("BTC:", "PEPE:")) for k in e2["trades"]))
verifie("marchés sans données : hors ligne, pas d'erreur", v2["marches"]["ETH"]["ok"] is False and not v2.get("erreur"))
e3, v3, a3 = R.avancer(json.loads(json.dumps(e2)), M, now, {"capital": 50000})
verifie("relance : registre identique, aucune alerte en double", e3["trades"] == e2["trades"] and len(a3) == 0, len(a3))
# 2. plafonds et rejeu
verifie("rejeu calculé, séparé du registre", v2["rejeu"] and v2["rejeu"]["stats"]["n"] > len(e2["trades"]))
verifie("memecoins plafonnés à 0,25 %", all(t["poids"] <= 0.25 + 1e-9 for t in v2["rejeu"]["trades"] if t["marche"] == "PEPE"))
verifie("résultats plausibles (frais comptés)", all(-2.5 < t["R"] < 20 for t in v2["rejeu"]["trades"]))
h = R.bloc_robot(v2) + R.carte_accueil(v2)
verifie("page générée sans valeur vide", "None" not in h and ">nan" not in h.lower(), len(h))
print("RÉSULTAT :", "tout passe" if ok else "ÉCHEC"); sys.exit(0 if ok else 1)
