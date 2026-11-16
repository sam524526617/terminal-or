#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Essai de la carte des options sur les vraies données (lancement manuel) : lit le GLD et affiche l'analyse."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yfinance as yf  # noqa: E402

import options  # noqa: E402

D = options.charger(Path(tempfile.mkdtemp()))
print("GLD", D["spot"], D["jour"], "échéances", [(e["date"], len(e["calls"]), len(e["puts"]), sum(l[1] for l in e["calls"]), sum(l[1] for l in e["puts"])) for e in D["ech"]])
g = yf.download("GC=F", period="5d", interval="1d", progress=False, auto_adjust=False)["Close"].dropna()
print(g.tail(3))
prix = float(g.iloc[-1].iloc[0] if hasattr(g.iloc[-1], "iloc") else g.iloc[-1])
print("or_ref", D.get("or_ref"))
O = options.analyser(D, prix, 0.0)
for k in ("prix", "rapport", "murs_haut", "murs_bas", "aimant", "aimant_date", "pc", "tot_c", "tot_p", "attendu", "jour"):
    print(k, ":", O[k])
print(len(O["profil"]), "prix dans le profil")
print(options.lecture(O))
e = D["ech"][0]
print("exemple lignes", e["calls"][len(e["calls"]) // 2], e["puts"][len(e["puts"]) // 2])
