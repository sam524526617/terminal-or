# -*- coding: utf-8 -*-
"""Essais de contrat.py sur des données simulées qui reproduisent ce qu'on voit chez Yahoo :
le jour du changement, une bougie ouvre sur l'ancien contrat et clôture sur le nouveau, et il arrive que
quelques bougies reviennent sur l'ancien contrat."""
import sys
from datetime import date

import numpy as np
import pandas as pd

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.dirname(__import__("os").path.abspath(__file__))))
import contrat as K

rng = np.random.default_rng(7)
PORTAGE = {"Q26": 20.0, "Z26": 60.0, "G27": 95.0, "J27": 130.0}


def marche(debut, fin, pas):
    """Marché ouvert du dimanche 22 h au vendredi 21 h UTC, pause 21-22 h."""
    idx = pd.date_range(debut, fin, freq=pas)
    j, h = idx.dayofweek, idx.hour
    ok = ~((j == 5) | ((j == 6) & (h < 22)) | ((j == 4) & (h >= 21)) | (h == 21))
    return idx[ok]


def construire(pas, debut, fin, rolls, vol, retours=(), choc=None):
    """rolls : [(instant, avant, après)] ; retours : bougies revenues entièrement sur l'ancien contrat ;
    choc : (instant, facteur) le marché entier est multiplié par facteur à partir de cet instant."""
    idx = marche(debut, fin, pas)
    spot = 3000 + np.cumsum(rng.normal(0, vol, len(idx)))
    if choc:
        spot = np.where(idx >= pd.Timestamp(choc[0]), spot * choc[1], spot)
    jours = (idx - idx[0]).total_seconds() / 86400
    haut, bas = np.abs(rng.normal(0, vol * 0.6, len(idx))), np.abs(rng.normal(0, vol * 0.6, len(idx)))
    X = {}
    for k, v in PORTAGE.items():
        c = spot * (1 + v / 3000) - 0.02 * jours      # portage proportionnel au prix, qui fond lentement
        o = np.r_[c[0], c[:-1]]
        X[k] = pd.DataFrame({"open": o, "high": np.maximum(o, c) + haut, "low": np.minimum(o, c) - bas, "close": c,
                             "volume": 100.0}, index=idx.tz_localize("UTC"))
    front = np.empty(len(idx), dtype=object)
    front[:] = rolls[0][1]
    for t, _, apres in rolls:
        front[idx >= pd.Timestamp(t)] = apres
    for t in retours:
        i = idx.get_loc(pd.Timestamp(t))
        front[i] = front[idx.get_loc(pd.Timestamp(rolls[-1][0])) - 1]
    g = X[rolls[0][1]].copy()
    for k in set(front):
        m = front == k
        g.loc[m] = X[k].loc[m].to_numpy()
    for i in range(1, len(idx)):          # bougie à cheval : ouvre sur le contrat de la bougie précédente
        if front[i] != front[i - 1]:
            a, n = X[front[i - 1]].iloc[i], X[front[i]].iloc[i]
            g.iloc[i, [0, 1, 2, 3]] = [a["open"], max(a["open"], n["high"]), min(a["low"], n["close"]), n["close"]]
    return g, X, idx


echecs = 0


def verifie(nom, cond, detail=""):
    global echecs
    print(("  ok   " if cond else "  ÉCHEC ") + nom + (" · " + detail if detail else ""))
    echecs += 0 if cond else 1


def ecart_max(a, ref, depuis=None):
    """Plus grand écart entre la série corrigée et le contrat de référence, sur les 4 prix."""
    a, ref = a[["open", "high", "low", "close"]], ref[["open", "high", "low", "close"]]
    if depuis is not None:
        a, ref = a[a.index >= depuis], ref[ref.index >= depuis]
    return float(np.abs(a.to_numpy() - ref.to_numpy()).max())


def vrai(X, t, avant, apres):
    """Vrai écart entre les deux contrats à l'instant du changement."""
    t = pd.Timestamp(t, tz="UTC")
    return float(X[apres]["close"].loc[t] - X[avant]["close"].loc[t])


def autour(a, ref, t, jours=3):
    """Écart max entre la série corrigée et le contrat de référence dans les jours qui entourent un changement."""
    t = pd.Timestamp(t, tz="UTC")
    m = (a.index >= t - pd.Timedelta(days=jours)) & (a.index <= t + pd.Timedelta(days=jours))
    return float(np.abs(a.loc[m, ["open", "high", "low", "close"]].to_numpy() - ref.loc[m, ["open", "high", "low", "close"]].to_numpy()).max())


def trous(df):
    """Plus gros écart clôture -> ouverture, et plus gros corps de bougie."""
    return (float((df["open"] - df["close"].shift(1)).abs().max()), float((df["close"] - df["open"]).abs().max()))


print("1. Série 1 h sur 18 mois, deux changements (fin juillet et fin novembre)")
rolls = [("2026-07-29 07:00", "Q26", "Z26"), ("2026-11-26 08:00", "Z26", "G27")]
g, X, idx = construire("1h", "2025-06-01", "2026-12-10 12:00", rolls, 4.0)
l = K.changements(g, X["G27"], n=12)
print("   repérés :", [(str(r["t"]), round(r["saut"], 1)) for r in l], "· corps max brut :", round(trous(g)[1], 1))
verifie("deux changements, aux bons instants", [str(r["t"]) for r in l] == ["2026-07-29 07:00:00", "2026-11-26 08:00:00"])
v1, v2 = vrai(X, rolls[0][0], "Q26", "Z26"), vrai(X, rolls[1][0], "Z26", "G27")
verifie("sauts mesurés = vrais écarts entre contrats", len(l) == 2 and abs(l[0]["saut"] - v1) < 1.5 and abs(l[1]["saut"] - v2) < 1.5,
        f"mesurés {[round(r['saut'], 1) for r in l]}, vrais {[round(v1, 1), round(v2, 1)]}")
adj = K.appliquer(g, X["G27"], l)
verifie("autour du dernier changement, série corrigée = nouveau contrat sur les 4 prix", autour(adj, X["G27"], rolls[1][0]) < 1.5, f"{autour(adj, X['G27'], rolls[1][0]):.2f} $")
verifie("plus de bougie anormale", trous(adj)[1] < 0.75 * trous(g)[1], f"corps max {trous(g)[1]:.1f} -> {trous(adj)[1]:.1f} $, trou max {trous(adj)[0]:.1f} $")
verifie("dernières bougies inchangées", abs(adj["close"].iloc[-1] - g["close"].iloc[-1]) < 1e-9)

print("2. Série 15 min, changement dans la bougie en cours (la dernière)")
g, X, idx = construire("15min", "2026-10-01", "2026-11-26 08:00", [("2026-11-26 08:00", "Z26", "G27")], 1.2)
l = K.changements(g, X["G27"], n=24)
adj = K.appliquer(g, X["G27"], l)
verifie("repéré dès la première bougie", len(l) == 1 and str(l[0]["t"]) == "2026-11-26 08:00:00", str([(str(r["t"]), round(r["saut"], 1)) for r in l]))
verifie("série = nouveau contrat", ecart_max(adj, X["G27"]) < 1.5, f"{ecart_max(adj, X['G27']):.2f} $")

print("3. Aucun changement dans la fenêtre : rien n'est touché")
g, X, idx = construire("15min", "2026-09-10", "2026-11-10 12:00", [("2026-07-29 07:00", "Q26", "Z26")], 1.2)
verifie("aucun changement inventé", K.changements(g, X["Z26"], n=24) == [])

print("4. Contrat de comparaison peu traité dans le passé (une bougie sur trois, prix en retard de quelques dollars)")
g, X, idx = construire("1h", "2025-06-01", "2026-12-10 12:00", rolls, 4.0)
x = X["G27"].copy()
vieux = x.index < pd.Timestamp("2026-06-01", tz="UTC")
x = x[~vieux | (rng.random(len(x)) < 0.33)]
m = x.index < pd.Timestamp("2026-06-01", tz="UTC")
x.loc[m, "close"] += rng.normal(0, 5, int(m.sum()))
l = K.changements(g, x, n=12)
verifie("les deux vrais changements, aucun faux", [str(r["t"]) for r in l] == ["2026-07-29 07:00:00", "2026-11-26 08:00:00"], str([(str(r["t"]), round(r["saut"], 1)) for r in l]))

print("5. Krach de 12 % en pleine fenêtre de calendrier : l'écart en dollars fond, sans changement de contrat")
g, X, idx = construire("1h", "2026-08-10", "2027-02-10 12:00", [("2026-07-29 07:00", "Q26", "Z26")], 4.0, choc=("2027-01-28 10:00", 0.88))
verifie("pas de faux changement", K.changements(g, X["G27"], n=12) == [], str(K.reperer(g, X["G27"], n=12)))

print("6. Chaîne complète corriger()")
rng = np.random.default_rng(5)
g1h, X1h, _ = construire("1h", "2025-06-01", "2026-12-10 12:00", rolls, 4.0)
rng = np.random.default_rng(5)
g15, X15, _ = construire("15min", "2026-10-12", "2026-12-10 12:00", rolls[1:], 1.2)
noms = {"GCZ26.CMX": "Z26", "GCG27.CMX": "G27", "GCJ27.CMX": "J27"}
charger = lambda tk, per, itv: ((X15 if itv == "15m" else X1h)[noms[tk]] if tk in noms else pd.DataFrame())
gj = g1h.resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
a15, a1h, aj, info = K.corriger(charger, g15, g1h, gj, date(2026, 12, 10))
print("   info :", {k: (str(v) if not isinstance(v, list) else [(str(t), round(s_, 1)) for t, s_ in v]) for k, v in info.items()})
verifie("échéance suivie = févr. 2027, suivante avr. 2027", info["actif"] == "GCG27.CMX" and info["suivant"] == "avr. 2027")
verifie("écart avec l'échéance suivante mesuré", 25 < info["ecart_suivant"] < 50, f"{info['ecart_suivant']:.1f}")
verifie("changement probable fin janvier 2027", info["changement"] == date(2027, 1, 27), str(info["changement"]))
verifie("15 min = contrat actuel", ecart_max(a15, X15["G27"]) < 2.0, f"{ecart_max(a15, X15['G27']):.2f} $")
verifie("1 h = contrat actuel autour du dernier changement", autour(a1h, X1h["G27"], rolls[1][0]) < 1.5, f"{autour(a1h, X1h['G27'], rolls[1][0]):.2f} $")
verifie("1 h : plus de bougie anormale", trous(a1h)[1] < 0.75 * trous(g1h)[1] and trous(a1h)[0] < 20, f"corps {trous(g1h)[1]:.0f} -> {trous(a1h)[1]:.0f} $, trou {trous(a1h)[0]:.0f} $")
xj = X1h["G27"].resample("1D").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
verifie("quotidien = contrat actuel autour du dernier changement (jour même compris)", autour(aj, xj, "2026-11-26", 4) < 6.0, f"{autour(aj, xj, '2026-11-26', 4):.2f} $")
verifie("pas d'erreur", info["erreur"] is None, str(info["erreur"]))

print("7. Données manquantes : rien n'est modifié")
b15, b1h, bj, info = K.corriger(lambda *a: pd.DataFrame(), g15, g1h, gj, date(2026, 12, 10))
verifie("séries inchangées et erreur signalée", b15 is g15 and b1h is g1h and info["erreur"] is not None, str(info["erreur"]))

print("8. Dates probables de changement")
for (a, m), attendu in {(2026, 12): date(2026, 11, 26), (2026, 8): date(2026, 7, 29), (2026, 2): date(2026, 1, 28), (2026, 4): date(2026, 3, 27)}.items():
    verifie(f"{K.libelle(a, m)}", K.date_changement(a, m) == attendu, str(K.date_changement(a, m)))

print("9. Le jour du changement, quatre bougies reviennent sur l'ancien contrat (vu en nov. 2025)")
rng = np.random.default_rng(11)
ret = ("2026-11-26 12:00", "2026-11-26 16:00", "2026-11-26 18:00", "2026-11-26 19:00")
g, X, idx = construire("1h", "2026-06-01", "2026-12-10 12:00", [("2026-11-26 08:00", "Z26", "G27")], 4.0, retours=ret)
l = K.changements(g, X["G27"], n=12)
adj = K.appliquer(g, X["G27"], l)
verifie("un seul changement, au bon moment", len(l) == 1 and str(l[0]["t"]) == "2026-11-26 08:00:00", str([(str(r["t"]), round(r["saut"], 1)) for r in l]))
verifie("autour du changement, série = nouveau contrat", autour(adj, X["G27"], "2026-11-26 08:00", 2) < 1.5, f"{autour(adj, X['G27'], '2026-11-26 08:00', 2):.2f} $")

print("10. Changement en cours : les dernières bougies alternent entre les deux contrats")
ret = ("2026-11-26 08:30", "2026-11-26 09:00", "2026-11-26 09:15")
g, X, idx = construire("15min", "2026-10-01", "2026-11-26 09:15", [("2026-11-26 08:00", "Z26", "G27")], 1.2, retours=ret)
l = K.changements(g, X["G27"], n=24)
adj = K.appliquer(g, X["G27"], l)
verifie("repéré, saut = vrai écart", len(l) == 1 and abs(l[0]["saut"] - vrai(X, "2026-11-26 08:00", "Z26", "G27")) < 1.5, str([(str(r["t"]), round(r["saut"], 1)) for r in l]))
verifie("série = nouveau contrat, dernière bougie comprise", ecart_max(adj, X["G27"]) < 2.0, f"{ecart_max(adj, X['G27']):.2f} $")

print("11. Contrat très peu traité autour d'un vieux changement : le moment exact vient de GC=F lui-même")
rng = np.random.default_rng(3)
g, X, idx = construire("1h", "2025-12-01", "2026-06-10 12:00", [("2026-03-27 07:00", "Q26", "Z26")], 3.0)
x = X["Z26"]
x = x[rng.random(len(x)) < 0.06]
x = x[(x.index < pd.Timestamp("2026-03-26 10:00", tz="UTC")) | (x.index > pd.Timestamp("2026-03-27 21:00", tz="UTC"))]
l = K.changements(g, x, n=12)
adj = K.appliquer(g, x, l)
verifie("moment exact retrouvé", len(l) == 1 and str(l[0]["t"]) == "2026-03-27 07:00:00", str([(str(r["t"]), round(r["saut"], 1)) for r in l]))
verifie("plus de bougie anormale", trous(adj)[1] < 20 and trous(adj)[0] < 20, f"corps {trous(g)[1]:.0f} -> {trous(adj)[1]:.0f} $, trou {trous(adj)[0]:.0f} $")

print("12. Même alternance, mais contrat de comparaison bruité : continuité et correction après coup")
rng = np.random.default_rng(11)
ret = ("2026-11-26 12:00", "2026-11-26 16:00", "2026-11-26 18:00", "2026-11-26 19:00")
g, X, idx = construire("1h", "2026-06-01", "2026-12-10 12:00", [("2026-11-26 08:00", "Z26", "G27")], 4.0)
g = g.copy()
for h in ret:                      # bougies entières revenues sur l'ancien contrat (trous visibles entre bougies, comme en nov. 2025)
    g.loc[pd.Timestamp(h, tz="UTC"), ["open", "high", "low", "close"]] = X["Z26"].loc[pd.Timestamp(h, tz="UTC"), ["open", "high", "low", "close"]].to_numpy()
x = X["G27"].copy()
for c in ("open", "high", "low", "close"):
    x[c] = x[c] + rng.normal(0, 4, len(x))
l = K.changements(g, x, n=12)
adj = K.appliquer(g, x, l)
t0 = pd.Timestamp("2026-11-26 08:00", tz="UTC")
m = (adj.index >= t0 - pd.Timedelta(days=2)) & (adj.index <= t0 + pd.Timedelta(days=2))
e_oc = float(np.abs(adj.loc[m, ["open", "close"]].to_numpy() - X["G27"].loc[m, ["open", "close"]].to_numpy()).max())
verifie("repéré, comparaison jugée bruitée", len(l) == 1 and not l[0]["propre"], str([(str(r["t"]), round(r["saut"], 1), r["propre"]) for r in l]))
verifie("ouvertures et clôtures = nouveau contrat à 3 $ près", e_oc < 3.0, f"{e_oc:.2f} $")
verifie("plus aucun trou de la taille du saut", trous(adj[m])[0] < 15 and trous(g[m])[0] > 25, f"{trous(g[m])[0]:.0f} -> {trous(adj[m])[0]:.0f} $")

print("13. Garde-fou : saut de la taille de l'écart suivant, non corrigé, pendant les jours du changement")
g, X, idx = construire("15min", "2026-10-01", "2026-11-26 10:00", [("2026-11-26 08:00", "Z26", "G27")], 1.2)
verifie("saut signalé si rien n'a été corrigé", K.saut_suspect(g, [], 35.0) is not None, str(K.saut_suspect(g, [], 35.0)))
verifie("rien de signalé une fois corrigé", K.saut_suspect(g, K.changements(g, X["G27"], n=24), 35.0) is None)
g, X, idx = construire("15min", "2026-09-10", "2026-11-10 12:00", [("2026-07-29 07:00", "Q26", "Z26")], 1.2)
verifie("rien de signalé hors des jours de changement", K.saut_suspect(g, [], 35.0) is None)

print("\nRÉSULTAT :", "tout passe" if not echecs else f"{echecs} échec(s)")
sys.exit(1 if echecs else 0)
