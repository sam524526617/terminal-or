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
    Renvoie [{"t": instant UTC de la première bougie touchée, "saut": hausse artificielle en $,
              "avant" / "apres": niveau du rapport contrat / GC=F avant et après}].
    On travaille sur le rapport contrat / GC=F : il ne dépend pas du niveau des prix, donc un krach ne crée pas de
    fausse marche (l'écart en dollars, lui, bouge avec le prix)."""
    g, x = _utc(g), _utc(x)
    if g is None or x is None:
        return []
    c = pd.concat({"g": g["close"], "x": x["close"]}, axis=1, join="inner").dropna()
    N = len(c)
    if N < 12:
        return []
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
    liste = []
    gi = g.index
    for r in retenus:
        k = r["k"]
        saut = float(gc[k - 1] * (np.exp(r["avant"] - r["apres"]) - 1))
        t_roll = t[k]
        entre = gi[(gi > t[k - 1]) & (gi <= t[k])]          # bougies de GC=F entre les deux bougies communes
        if len(entre) > 1:
            # contrat peu traité : le moment exact est inconnu, on cherche dans GC=F la bougie qui fait le saut
            # (entre deux bougies, ou à l'intérieur d'une bougie qui ouvre sur l'ancien contrat et clôture sur le nouveau)
            pos = gi.get_indexer(entre)
            o_, c_ = g["open"].to_numpy(dtype=float), g["close"].to_numpy(dtype=float)
            ecarts = np.maximum.reduce([np.where(np.abs(o_[pos] - c_[pos - 1] - saut) <= 0.4 * abs(saut), 1.0, 0.0),
                                        np.where(np.abs(c_[pos] - o_[pos] - saut) <= 0.4 * abs(saut), 1.0, 0.0)])
            if not ecarts.any():
                continue                                  # introuvable : on ne corrige pas ce changement-là
            dist = np.minimum(np.abs(o_[pos] - c_[pos - 1] - saut), np.abs(c_[pos] - o_[pos] - saut))
            t_roll = entre[int(np.argmin(dist))]
        liste.append({"t": pd.Timestamp(t_roll), "saut": saut, "avant": r["avant"], "apres": r["apres"]})
    return liste


def appliquer(g, x, liste):
    """Série continue : ramène chaque bougie de g sur le contrat actuel.
    Règle générale : tout ce qui précède un changement est relevé du saut. Autour du changement (30 h), bougie par
    bougie : ce jour-là Yahoo mélange les deux contrats (une bougie ouvre sur l'ancien et clôture sur le nouveau, ou
    revient un moment sur l'ancien). L'ouverture et la clôture sont donc rangées séparément, en les comparant au
    contrat x quand il a coté à ce moment-là, sinon par continuité avec la bougie précédente."""
    if g is None or len(g) == 0 or not liste:
        return g
    out = g.copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    O, H, L, C = (np.array(out[c].to_numpy(dtype=float), copy=True) for c in COLS)
    o0, h0, l0, c0 = O.copy(), H.copy(), L.copy(), C.copy()      # valeurs d'origine
    for r in liste:
        avant = np.asarray(idx < r["t"])
        for arr in (O, H, L, C):
            arr[avant] += r["saut"]
    xu = _utc(x)
    if xu is not None:
        xu = xu.reindex(idx)
        XO, XH, XL, XC = (xu[c].to_numpy(dtype=float) for c in COLS)
    else:
        XO = XH = XL = XC = np.full(len(idx), np.nan)
    for k, r in enumerate(liste):
        suivants = sum(v["saut"] for v in liste[k + 1:])
        amp, saut = abs(r["avant"] - r["apres"]), r["saut"]

        def classe(val_x, val_g):                     # True : ancien contrat, False : nouveau, None : inconnu
            if not (np.isfinite(val_x) and val_x > 0 and val_g > 0):
                return None
            lr = np.log(val_x / val_g)
            if abs(lr - r["avant"]) < 0.35 * amp:
                return True
            if abs(lr - r["apres"]) < 0.35 * amp:
                return False
            return None
        zone = np.where(np.asarray((idx >= r["t"] - pd.Timedelta(hours=30)) & (idx <= r["t"] + pd.Timedelta(hours=30))))[0]
        prec = True                                   # la zone commence sur l'ancien contrat
        for q in zone:
            co = classe(XO[q], o0[q])
            if co is None:                            # par continuité : l'ouverture suit la clôture précédente
                ecart_o = o0[q] - c0[q - 1] if q > 0 else 0.0
                co = False if abs(ecart_o - saut) < 0.35 * abs(saut) else (True if abs(ecart_o + saut) < 0.35 * abs(saut) else prec)
            cc = classe(XC[q], c0[q])
            if cc is None:                            # la bougie du changement clôture, par définition, sur le nouveau
                cc = False if idx[q] == r["t"] else co
            do, dc = suivants + (saut if co else 0.0), suivants + (saut if cc else 0.0)
            O[q], C[q] = o0[q] + do, c0[q] + dc
            if co == cc:
                H[q], L[q] = h0[q] + do, l0[q] + do
            else:                                     # bougie à cheval sur les deux contrats : mèches du contrat x
                conv = np.exp(-r["apres"])
                xh = XH[q] * conv + suivants if np.isfinite(XH[q]) else -np.inf
                xl = XL[q] * conv + suivants if np.isfinite(XL[q]) else np.inf
                H[q], L[q] = max(O[q], C[q], xh), min(O[q], C[q], xl)
            prec = cc
    out["open"], out["high"], out["low"], out["close"] = O, H, L, C
    return out


def reperer(g, x, n=24, garde=True):
    """[(instant UTC, saut en $)] : forme simple de changements()."""
    return [(r["t"], r["saut"]) for r in changements(g, x, n, garde)]


def ajuster_jours(df, liste):
    """Bougies quotidiennes : les jours d'avant un changement sont relevés du saut ; le jour même, la bougie ouvre
    sur l'ancien contrat et clôture sur le nouveau, on relève son ouverture et son plus bas."""
    if df is None or len(df) == 0 or not liste:
        return df
    out = df.copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    jours = idx.normalize()
    cols = [c for c in COLS if c in out.columns]
    vals = {c: np.array(out[c].to_numpy(dtype=float), copy=True) for c in cols}
    for r in liste:
        j = pd.Timestamp(r["t"]).normalize()
        avant, meme = np.asarray(jours < j), np.asarray(jours == j)
        for c in cols:
            vals[c][avant] += r["saut"]
        if meme.any() and {"open", "low", "high", "close"} <= set(cols):
            vals["open"][meme] += r["saut"]
            bas, haut = ("low", "high") if r["saut"] > 0 else ("high", "low")
            vals[bas][meme] += r["saut"]
            vals["low"][meme] = np.minimum.reduce([vals["low"][meme], vals["open"][meme], vals["close"][meme]])
            vals["high"][meme] = np.maximum.reduce([vals["high"][meme], vals["open"][meme], vals["close"][meme]])
    for c in cols:
        out[c] = vals[c]
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
        x1h = charger(tk, "730d", "1h")
        l15, l1h = changements(g15, x15[tk], n=24), changements(g1h, x1h, n=12)
        # un changement vu en 15 min mais pas encore en 1 h (tout récent) est appliqué aux deux séries
        for r in l15:
            if not any(abs((r["t"] - u["t"]).total_seconds()) < 36 * 3600 for u in l1h):
                l1h.append(dict(r, t=r["t"].floor("h")))
        l1h.sort(key=lambda r: r["t"])
        info["sauts"] = [(r["t"], float(r["saut"])) for r in l1h]
        info["sauts_15"] = [(r["t"], float(r["saut"])) for r in l15]
        return appliquer(g15, x15[tk], l15), appliquer(g1h, x1h, l1h), ajuster_jours(gj, l1h), info
    except Exception as e:      # une donnée manquante ne doit jamais arrêter le terminal
        info["erreur"] = f"correction impossible ({str(e)[:80]})"
        return g15, g1h, gj, info
