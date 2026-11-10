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
    """Un changement de contrat n'arrive que dans les jours qui précèdent la fin de janv., mars, mai, juil. et nov.
    (deux jours ouvrés avant le dernier jour ouvré, à un jour près dans l'historique)."""
    j = pd.Timestamp(t).date()
    a, m = (j.year, j.month + 1) if j.month < 12 else (j.year + 1, 1)
    for an, mo in ((a, m), (j.year, j.month)):
        if mo in MOIS_ACTIFS and -3 <= (j - date_changement(an, mo)).days <= 4:
            return True
    return False


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


SEUIL = 0.003        # marche minimale de l'écart relatif (0,3 % du prix, soit 12 $ à 4 000 $)


def changements(g, x, n=24, garde=True):
    """Changements de contrat de GC=F, repérés en le comparant au contrat x (même unité de temps).
    Renvoie (liste, classes) :
      liste   : [{"t": instant UTC de la première bougie du nouveau contrat, "saut": hausse artificielle en $}]
      classes : {instant: True si la bougie est encore sur l'ancien contrat} pour les bougies proches d'un changement
                (le jour du changement, Yahoo alterne parfois entre les deux contrats d'une bougie à l'autre).
    On travaille sur le rapport contrat / GC=F : il ne dépend pas du niveau des prix, donc un krach ne crée pas de
    fausse marche (l'écart en dollars, lui, bouge avec le prix)."""
    g, x = _utc(g), _utc(x)
    if g is None or x is None:
        return [], {}
    c = pd.concat({"g": g["close"], "x": x["close"]}, axis=1, join="inner").dropna()
    N = len(c)
    if N < 12:
        return [], {}
    t, gc = c.index, c["g"].to_numpy(dtype=float)
    lr = np.log(c["x"].to_numpy(dtype=float) / gc)
    tol = tolerance(float(gc[-1])) / float(gc[-1])
    marche = np.zeros(N)
    for i in range(3, N):
        marche[i] = np.median(lr[i:i + n]) - np.median(lr[max(0, i - n):i])
    cands, i = [], 3
    while i < N:
        if abs(marche[i]) < SEUIL:
            i += 1
            continue
        j = i                                   # bougies voisines au-dessus du seuil : une seule marche
        while j + 1 < N and (abs(marche[j + 1]) >= SEUIL or (j + 2 < N and abs(marche[j + 2]) >= SEUIL)):
            j += 1
        k = i + int(np.argmax(np.abs(marche[i:j + 1])))
        avant, apres = float(np.median(lr[max(0, k - n):k])), float(np.median(lr[k:k + n]))
        d0, d1 = max(1, k - n // 2), min(N - 1, k + n // 2)     # coupure qui sépare le mieux les deux paliers
        cout = [np.abs(lr[max(0, d0 - n // 2):q] - avant).sum() + np.abs(lr[q:min(N, d1 + n // 2)] - apres).sum()
                for q in range(d0, d1 + 1)]
        k = d0 + int(np.argmin(cout))
        avant, apres = float(np.median(lr[max(0, k - n):k])), float(np.median(lr[k:k + n]))
        recent = N - k < 3                       # moins de 3 bougies après : on exige l'identité avec le contrat
        amp = abs(avant - apres)
        net = (np.mean(np.abs(lr[max(0, k - n):k] - avant) < 0.35 * amp) >= 0.6      # deux paliers bien séparés,
               and np.mean(np.abs(lr[k:k + n] - apres) < 0.35 * amp) >= 0.6)        # pas un mélange des deux
        if (SEUIL <= amp <= 0.03 and net and (not recent or abs(apres) <= tol)
                and (not garde or fenetre_ok(t[k]))):
            cands.append({"k": k, "avant": avant, "apres": apres})
        i = max(j, k) + 1
    # Changement en cours, pas encore visible dans les médianes : sur les 3 derniers jours, des bougies identiques
    # au contrat (nouveau) et d'autres nettement au-dessus (ancien)
    fin = t[-1]
    span = np.where(t >= fin - pd.Timedelta(days=3))[0]
    if len(span) >= 2 and (not garde or fenetre_ok(fin)) and not any(fenetre_ok(t[r["k"]]) and (fin - t[r["k"]]).days < 9 for r in cands):
        ident = np.abs(lr[span]) <= tol
        if ident.any() and (~ident).any():
            avant = float(np.median(lr[span][~ident]))
            if SEUIL <= abs(avant) <= 0.03:
                cands.append({"k": int(span[np.argmax(ident)]), "avant": avant, "apres": 0.0})
    # un seul changement par fin de mois : le plus net
    cands.sort(key=lambda r: r["k"])
    retenus = []
    for r in cands:
        if retenus and (t[r["k"]] - t[retenus[-1]["k"]]).days < 12:
            if abs(r["avant"] - r["apres"]) > abs(retenus[-1]["avant"] - retenus[-1]["apres"]):
                retenus[-1] = r
        else:
            retenus.append(r)
    liste, classes = [], {}
    gi = g.index
    for r in retenus:
        k = r["k"]
        saut = float(gc[k - 1] * (np.exp(r["avant"] - r["apres"]) - 1))
        t_roll = t[k]
        entre = gi[(gi > t[k - 1]) & (gi <= t[k])]          # bougies de GC=F entre les deux bougies communes
        if len(entre) > 1:
            # contrat peu traité : le moment exact est inconnu, on cherche dans GC=F la bougie qui fait le saut
            pos = gi.get_indexer(entre)
            sauts_g = g["open"].to_numpy(dtype=float)[pos] - g["close"].to_numpy(dtype=float)[pos - 1]
            q = int(np.argmin(np.abs(sauts_g - saut)))
            if abs(sauts_g[q] - saut) > 0.4 * abs(saut):
                continue                                  # introuvable : on ne corrige pas ce changement-là
            t_roll = entre[q]
        liste.append({"t": pd.Timestamp(t_roll), "saut": saut})
        # bougies proches : chacune est rangée sur l'ancien ou le nouveau contrat selon son propre écart
        amp = abs(r["avant"] - r["apres"])
        for q in np.where((t >= t_roll - pd.Timedelta(hours=30)) & (t <= t_roll + pd.Timedelta(hours=30)))[0]:
            if abs(lr[q] - r["avant"]) < 0.35 * amp:
                classes[t[q]] = True
            elif abs(lr[q] - r["apres"]) < 0.35 * amp:
                classes[t[q]] = False
    return liste, classes


def decalages(g, liste, classes):
    """Montant à ajouter à chaque bougie de g pour la ramener sur le contrat actuel."""
    idx = pd.DatetimeIndex(g.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    dec = np.zeros(len(idx))
    for r in liste:                               # règle générale : tout ce qui précède un changement est décalé
        dec[np.asarray(idx < r["t"])] += r["saut"]
    for k, r in enumerate(liste):                 # autour du changement : bougie par bougie
        suivants = sum(x["saut"] for x in liste[k + 1:])
        zone = np.where(np.asarray((idx >= r["t"] - pd.Timedelta(hours=30)) & (idx <= r["t"] + pd.Timedelta(hours=30))))[0]
        etat, age = None, 99
        for q in zone:
            cl = classes.get(idx[q])
            if cl is None:                        # pas de comparaison : contrat de la bougie précédente si elle est proche
                age += 1
                cl = etat if (etat is not None and age <= 3) else bool(idx[q] < r["t"])
            else:
                etat, age = cl, 0
            dec[q] = suivants + (r["saut"] if cl else 0.0)
    return dec


def reperer(g, x, n=24, garde=True):
    """[(instant UTC, saut en $)] : forme simple de changements()."""
    return [(r["t"], r["saut"]) for r in changements(g, x, n, garde)[0]]


def ajuster(df, sauts, par_jour=False, classes=None):
    """Rend la série continue : ajoute aux bougies d'avant chaque changement de contrat le saut correspondant.
    sauts : [(instant, saut)] ; par_jour : série quotidienne, on compare les dates."""
    if df is None or len(df) == 0 or not sauts:
        return df
    out = df.copy()
    cols = [c for c in COLS if c in out.columns]
    vals = np.array(out[cols].to_numpy(dtype=float), copy=True)
    if par_jour:
        idx = pd.DatetimeIndex(out.index)
        if idx.tz is not None:
            idx = idx.tz_localize(None)
        for t, s in sauts:
            vals[np.asarray(idx.normalize() < pd.Timestamp(t).normalize())] += s
    else:
        liste = [{"t": pd.Timestamp(t), "saut": float(s)} for t, s in sorted(sauts)]
        vals += decalages(out, liste, classes or {})[:, None]
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
            if len(d) >= 3 and bool((np.abs(d.to_numpy()[-8:]) <= tolerance(float(pg.iloc[-1]))).any()):
                actif = k           # on garde la plus lointaine : dès qu'une bougie a basculé, le changement est fait
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
        l15, c15 = changements(g15, x15[tk], n=24)
        l1h, c1h = changements(g1h, charger(tk, "730d", "1h"), n=12)
        s15, s1h = [(r["t"], r["saut"]) for r in l15], [(r["t"], r["saut"]) for r in l1h]
        # un changement vu en 15 min mais pas encore en 1 h (tout récent) est appliqué aux deux séries
        for t, sv in s15:
            if not any(abs((t - u).total_seconds()) < 36 * 3600 for u, _ in s1h):
                s1h.append((t, sv))
        s1h.sort()
        info["sauts"] = [(pd.Timestamp(t), float(sv)) for t, sv in s1h]
        info["sauts_15"] = [(pd.Timestamp(t), float(sv)) for t, sv in s15]
        return (ajuster(g15, s15, classes=c15), ajuster(g1h, s1h, classes=c1h), ajuster(gj, s1h, par_jour=True), info)
    except Exception as e:      # une donnée manquante ne doit jamais arrêter le terminal
        info["erreur"] = f"correction impossible ({str(e)[:80]})"
        return g15, g1h, gj, info
