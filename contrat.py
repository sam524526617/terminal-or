# -*- coding: utf-8 -*-
"""
CHANGEMENT DE CONTRAT DU FUTURE (« roll ») : une série de prix continue
======================================================================

Le prix « GC=F » de Yahoo recopie l'échéance la plus traitée de l'or COMEX (févr., avr., juin, août, déc.).
Quelques jours avant la livraison il passe à l'échéance suivante, qui cote plus cher (le portage) : le prix
saute d'un coup de 20 à 60 $ alors que le marché n'a pas bougé. Sans correction ce saut fabrique une fausse
cassure de structure, un faux FVG et de fausses zones.

Ici on compare GC=F à un contrat précis (par exemple GCZ26.CMX), dont l'historique ne change jamais de contrat.
L'écart entre les deux est plat, sauf aux changements de contrat où il fait une marche : la hauteur de la marche
est exactement le saut artificiel. On l'ajoute aux bougies d'avant, et la série redevient continue
(ajustement « par l'arrière », la méthode standard des séries de futures continues).
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

MOIS_ACTIFS = (2, 4, 6, 8, 12)        # échéances que suit GC=F
CODES_MOIS = "FGHJKMNQUVXZ"
MOIS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
COLS = ("open", "high", "low", "close")


def contrats_proches(jour, n=3):
    """Les n prochaines échéances actives à partir du mois en cours : [(ticker Yahoo, année, mois)]."""
    out, a, m = [], jour.year, jour.month
    while len(out) < n:
        if m in MOIS_ACTIFS:
            out.append((f"GC{CODES_MOIS[m - 1]}{str(a)[2:]}.CMX", a, m))
        m += 1
        if m > 12:
            m, a = 1, a + 1
    return out


def libelle(a, m):
    return f"{MOIS_FR[m - 1]} {a}"


def date_changement(a, m):
    """Jour probable où GC=F quitte l'échéance (a, m) : deux jours ouvrés avant le dernier jour ouvré du mois précédent."""
    j = date(a, m, 1) - timedelta(days=1)
    recul = 0
    while True:
        if j.weekday() < 5:
            if recul == 2:
                return j
            recul += 1
        j -= timedelta(days=1)


def fenetre_ok(t):
    """Un changement de contrat n'arrive qu'autour de la fin janv., mars, mai, juil. et nov."""
    return (t.month in (1, 3, 5, 7, 11) and t.day >= 17) or (t.month in (2, 4, 6, 8, 12) and t.day <= 6)


def _utc(df):
    """Même convention d'index pour toutes les séries : horodatage UTC sans fuseau."""
    if df is None or len(df) == 0:
        return None
    idx = pd.DatetimeIndex(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    out = df.copy()
    out.index = idx
    return out[~out.index.duplicated(keep="last")].sort_index()


def ecart(g, x):
    """Écart (contrat − GC=F) sur les bougies communes, et le prix de GC=F aux mêmes instants."""
    g, x = _utc(g), _utc(x)
    if g is None or x is None:
        return pd.Series(dtype=float), pd.Series(dtype=float)
    c = pd.concat({"g": g["close"], "x": x["close"]}, axis=1, join="inner").dropna()
    return c["x"] - c["g"], c["g"]


def tolerance(prix):
    """Deux séries sont « le même contrat » si elles diffèrent de moins que ça."""
    return max(0.6, 0.00015 * prix)


def reperer(g, x, n=24, garde=True):
    """[(instant UTC, saut en $)] : à chaque instant GC=F a changé de contrat et son prix a sauté de `saut`
    sans mouvement réel. g : bougies de GC=F, x : bougies d'un contrat précis (même unité de temps)."""
    d, pg = ecart(g, x)
    if len(d) < 12:
        return []
    v, t = d.to_numpy(dtype=float), d.index
    N = len(v)
    prix = float(np.median(pg.to_numpy()))
    seuil, tol = max(8.0, 0.0025 * prix), tolerance(prix)
    # Marche de l'écart à chaque bougie : médiane des n bougies d'après moins médiane des n bougies d'avant
    marche = np.zeros(N)
    for i in range(3, N):
        marche[i] = np.median(v[i:i + n]) - np.median(v[max(0, i - n):i])
    sauts, i = [], 3
    while i < N:
        if abs(marche[i]) < seuil:
            i += 1
            continue
        j = i                                   # groupe de bougies voisines au-dessus du seuil : une seule marche
        while j + 1 < N and (abs(marche[j + 1]) >= seuil or (j + 2 < N and abs(marche[j + 2]) >= seuil)):
            j += 1
        k = i + int(np.argmax(np.abs(marche[i:j + 1])))
        avant, apres = float(np.median(v[max(0, k - n):k])), float(np.median(v[k:k + n]))
        # position exacte : la coupure qui sépare le mieux les deux paliers
        d0, d1 = max(1, k - n // 2), min(N - 1, k + n // 2)
        cout = [np.abs(v[max(0, d0 - n // 2):c] - avant).sum() + np.abs(v[c:min(N, d1 + n // 2)] - apres).sum()
                for c in range(d0, d1 + 1)]
        k = d0 + int(np.argmin(cout))
        avant, apres = float(np.median(v[max(0, k - n):k])), float(np.median(v[k:k + n]))
        saut = avant - apres
        recent = N - k < 3                       # moins de 3 bougies après : on exige l'identité avec le contrat
        if (abs(saut) >= seuil and abs(saut) <= 0.03 * prix and (not recent or abs(apres) <= tol)
                and (not garde or fenetre_ok(t[k]))):
            sauts.append((t[k], saut))
        i = max(j, k) + 1
    return sauts


def ajuster(df, sauts, par_jour=False):
    """Ajoute aux bougies d'avant chaque changement de contrat le saut correspondant (série continue).
    par_jour : la série est quotidienne, on compare les dates."""
    if df is None or len(df) == 0 or not sauts:
        return df
    out = df.copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    cols = [c for c in COLS if c in out.columns]
    vals = np.array(out[cols].to_numpy(dtype=float), copy=True)
    for t, s in sauts:
        t = pd.Timestamp(t)
        avant = (idx.normalize() < t.normalize()) if par_jour else (idx < t)
        vals[np.asarray(avant)] += s
    out[cols] = vals
    return out


def corriger(charger, g15, g1h, gj, aujourdhui):
    """Rend continues les bougies 15 min, 1 h et quotidiennes de GC=F.
    charger(ticker, periode, intervalle) -> bougies (colonnes open/high/low/close), vide si indisponible.
    Renvoie (g15, g1h, gj, info) ; en cas de doute rien n'est modifié et info["erreur"] dit pourquoi."""
    info = {"actif": None, "libelle": None, "sauts": [], "suivant": None, "ecart_suivant": None,
            "changement": None, "erreur": None}
    try:
        cands = contrats_proches(aujourdhui, 3)
        x15 = {tk: charger(tk, "60d", "15m") for tk, _, _ in cands}
        actif = None
        for k, (tk, a, m) in enumerate(cands):
            d, pg = ecart(g15, x15[tk])
            if len(d) >= 3 and abs(float(np.median(d.to_numpy()[-3:]))) <= tolerance(float(pg.iloc[-1])):
                actif = k
                break
        if actif is None:
            info["erreur"] = "échéance suivie par GC=F non identifiée"
            return g15, g1h, gj, info
        tk, a, m = cands[actif]
        info.update(actif=tk, libelle=libelle(a, m), changement=date_changement(a, m))
        if actif + 1 < len(cands):
            tk2, a2, m2 = cands[actif + 1]
            d2, _ = ecart(g15, x15[tk2])
            if len(d2) >= 8:
                info.update(suivant=libelle(a2, m2), ecart_suivant=float(np.median(d2.to_numpy()[-40:])))
        s15 = reperer(g15, x15[tk], n=24)
        s1h = reperer(g1h, charger(tk, "730d", "1h"), n=12)
        info["sauts"] = [(pd.Timestamp(t), float(s)) for t, s in s1h]
        info["sauts_15"] = [(pd.Timestamp(t), float(s)) for t, s in s15]
        # un changement vu en 15 min mais pas encore en 1 h (tout récent) est appliqué aux deux séries
        vus_1h = [t for t, _ in s1h]
        for t, s in s15:
            if not any(abs((pd.Timestamp(t) - pd.Timestamp(u)).total_seconds()) < 36 * 3600 for u in vus_1h):
                s1h.append((t, s))
                info["sauts"].append((pd.Timestamp(t), float(s)))
        info["sauts"].sort()
        return ajuster(g15, s15), ajuster(g1h, s1h), ajuster(gj, s1h, par_jour=True), info
    except Exception as e:      # une donnée manquante ne doit jamais arrêter le terminal
        info["erreur"] = f"correction impossible ({str(e)[:80]})"
        return g15, g1h, gj, info
