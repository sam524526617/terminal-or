# -*- coding: utf-8 -*-
"""
ROBOT PAPIER : il trade tout seul, en direct, sans argent réel
=============================================================

À chaque passage du terminal (toutes les 15 minutes), le robot applique ses stratégies aux bougies du moment, ouvre
et ferme ses positions, et tient un registre. Les règles sont écrites à l'avance et ne changent jamais en cours de
route : c'est un test « en avant », le seul qui ne puisse pas tricher avec le passé.

Quatre stratégies, chacune sur son propre compte papier (règles reprises telles quelles des backtests du projet) :
  T20  Tendance 20 jours, achats : clôture Daily au-dessus du plus haut des 20 jours précédents ; entrée à
       l'ouverture du jour suivant ; stop 2 ATR, stop suiveur 3 ATR ; sortie si clôture sous le plus bas de 10 jours.
  RSI2 Repli dans la tendance, achats : RSI(2) Daily sous 10 au-dessus de la moyenne 200 jours ; stop 3 ATR ;
       sortie quand la clôture repasse au-dessus de la moyenne 5 jours, ou après 10 jours.
  ASIE Cassure du range asiatique (18 h - 3 h New York) dans le sens de l'EMA 50 Daily, ordres de 3 h à 8 h New York ;
       stop de l'autre côté du range, objectif 2 R, sortie à 15 h New York.
  MOM  Momentum M15 + H4 (stratégie XAU 3 de ton amie) : momentum 10 bougies du même signe en M15 et en H4, volatilité
       H4 haute ; stop 3 ATR H1, objectif 4 ATR H1, 12 h maximum.

La stratégie du robot lui-même : il engage ses stratégies selon leurs résultats EN DIRECT, jamais selon le backtest.
  - moins de 10 trades clos : phase d'observation, un quart du risque (0,25 %) ;
  - ensuite : gain moyen réduit par prudence (× n / (n + 20)) ; risque plein (1 %) à partir de +0,10 R par trade,
    proportionnel en dessous, et zéro si la stratégie perd. Une stratégie qui perd est donc coupée automatiquement.

Coûts comptés comme au backtest : 0,40 $ d'écart achat-vente et de glissement par aller-retour, financement de nuit
de 5 % par an pour les positions gardées plusieurs jours (stratégies Daily) ; 0,30 $ d'écart pour MOM.
Toutes les heures sont sur l'horloge interne du plan (New York + 7 h : la journée de trading commence à minuit).
"""

import html
import math
from datetime import timedelta

import numpy as np
import pandas as pd

import plan as P

VERSION = 1
COUT_FIXE = 0.40
FINANCEMENT = 0.05
SPREAD_MOM = 0.30
RISQUE_PCT = 1.0
OBSERVATION = 10          # trades clos avant de juger une stratégie
PRUDENCE = 20             # réduction du gain moyen : × n / (n + PRUDENCE)
CIBLE_R = 0.10            # gain moyen (réduit) qui vaut le risque plein

STRATS = {
    "T20": {"nom": "Tendance 20 jours", "court": "Achat sur cassure du plus haut de 20 jours, stop suiveur",
            "ut": "Daily", "horizon": "plusieurs jours à semaines"},
    "RSI2": {"nom": "Repli RSI 2", "court": "Achat d'un repli court dans une tendance de fond haussière",
             "ut": "Daily", "horizon": "1 à 10 jours"},
    "ASIE": {"nom": "Cassure du range asiatique", "court": "Cassure du range de la nuit pendant Londres, objectif 2 R",
             "ut": "M15", "horizon": "la journée"},
    "MOM": {"nom": "Momentum M15 + H4", "court": "Momentum aligné M15 et H4 quand la volatilité est haute",
            "ut": "M15", "horizon": "12 h maximum"},
}


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(c, n):
    d = c.diff()
    h = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    b = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + h / b.replace(0, np.nan))


# ---------------------------------------------------------------------------
# DONNÉES
# ---------------------------------------------------------------------------

def donnees(intra, maintenant):
    """Bougies du terminal -> M15, H1, H4 terminées et Daily (le jour en cours, partiel, marqué non terminé)."""
    b, hb = (intra or {}).get("barres"), (intra or {}).get("heures")
    if b is None or hb is None or len(b) < 300 or len(hb) < 24 * 250:
        return None
    now = P.vers_horloge(pd.DatetimeIndex([maintenant]))[0]
    m15 = P.agreger(P.sur_horloge(b), "15min")
    m15 = m15[m15["fin"] <= now]
    h1 = P.agreger(P.sur_horloge(hb), "1h")
    h1_fini = h1[h1["fin"] <= now]
    # jour en cours : bougies H1 terminées complétées par les M15 terminées plus récentes
    dern_h1 = h1_fini["fin"].iloc[-1] if len(h1_fini) else None
    base = pd.concat([h1_fini[["open", "high", "low", "close"]],
                      m15[m15.index >= dern_h1][["open", "high", "low", "close"]] if dern_h1 is not None else m15.iloc[:0, :4]])
    D = P.agreger(base.sort_index(), "1D")
    D = D[D.index.weekday < 5]
    D["fini"] = D["fin"] <= now
    h4 = P.agreger(h1_fini[["open", "high", "low", "close"]], "4h")
    h4 = h4[h4["fin"] <= now]
    return {"m15": m15, "h1": h1_fini, "h4": h4, "D": D, "now": now}


def trade(strat, sens, t_e, entree, risque, stop, objectif=None):
    return {"strat": strat, "id": f"{strat}-{pd.Timestamp(t_e):%Y%m%d%H%M}", "sens": int(sens), "t_entree": pd.Timestamp(t_e),
            "entree": float(entree), "risque": float(risque), "stop": float(stop),
            "objectif": None if objectif is None else float(objectif), "t_sortie": None, "sortie": None,
            "issue": None, "R": None, "nuits": 0}


def clore(t, t_s, sortie, issue, cout=True):
    brut = (sortie - t["entree"]) * t["sens"]
    frais = (COUT_FIXE + t["nuits"] * t["entree"] * FINANCEMENT / 365) if cout else 0.0
    t.update(t_sortie=pd.Timestamp(t_s), sortie=float(sortie), issue=issue, R=float((brut - frais) / t["risque"]))
    return t


def latent(t, px):
    """Résultat d'une position ouverte au prix px (coûts compris)."""
    if px is None:
        return None
    brut = (px - t["entree"]) * t["sens"]
    frais = COUT_FIXE if t["strat"] != "MOM" else 0.0
    return float((brut - frais) / t["risque"])


# ---------------------------------------------------------------------------
# STRATÉGIES (même logique que backtest/recherche.py et backtest/strategies_mtf.py)
# ---------------------------------------------------------------------------

def strat_t20(X, debut):
    D = X["D"]
    o, h, l, c = (D[x].to_numpy() for x in ("open", "high", "low", "close"))
    fini = D["fini"].to_numpy()
    nf = int(fini.sum())                                   # jours terminés (le partiel, s'il existe, est le dernier)
    Df = D[fini]
    a = np.r_[atr(Df, 20).to_numpy(), [np.nan] * (len(D) - nf)]
    hh = np.r_[Df["high"].rolling(20).max().shift(1).to_numpy(), [np.nan] * (len(D) - nf)]
    l10 = np.r_[Df["low"].rolling(10).min().shift(1).to_numpy(), [np.nan] * (len(D) - nf)]
    out, i = [], 21
    while i < nf:
        if not (c[i] > hh[i]) or not np.isfinite(a[i]):
            i += 1
            continue
        k = i + 1
        if k >= len(D):                                     # le jour d'entrée n'a pas commencé : au prochain passage
            break
        if D.index[k] < debut:
            i += 1
            continue
        t = trade("T20", 1, D.index[k], o[k], 2 * a[i], o[k] - 2 * a[i])
        ext, j, ferme = c[i], k, False
        while j < len(D):
            if l[j] <= t["stop"]:
                t["nuits"] = j - k
                clore(t, D.index[j] + timedelta(hours=12), min(o[j], t["stop"]), "stop")
                ferme = True
                break
            if not fini[j]:
                break
            ext = max(ext, c[j])
            t["stop"] = max(t["stop"], ext - 3 * a[j])
            if c[j] < l10[j]:
                if j + 1 < len(D):
                    t["nuits"] = j + 1 - k
                    clore(t, D.index[j + 1], o[j + 1], "plus bas de 10 jours")
                    ferme = True
                else:
                    t["note"] = "sortie prévue à l'ouverture du prochain jour"
                j += 1
                break
            j += 1
        t["nuits"] = t["nuits"] or max(0, min(j, len(D) - 1) - k)
        out.append(t)
        if not ferme:
            break
        i = j + 1
    return out


def strat_rsi2(X, debut):
    D = X["D"]
    o, h, l, c = (D[x].to_numpy() for x in ("open", "high", "low", "close"))
    fini = D["fini"].to_numpy()
    nf = int(fini.sum())
    Df = D[fini]
    pad = [np.nan] * (len(D) - nf)
    r2 = np.r_[rsi(Df["close"], 2).to_numpy(), pad]
    s200 = np.r_[Df["close"].rolling(200).mean().to_numpy(), pad]
    s5 = np.r_[Df["close"].rolling(5).mean().to_numpy(), pad]
    a = np.r_[atr(Df, 20).to_numpy(), pad]
    out, i = [], 201
    while i < nf:
        if not (c[i] > s200[i] and r2[i] < 10):
            i += 1
            continue
        k = i + 1
        if k >= len(D):
            break
        if D.index[k] < debut:
            i += 1
            continue
        t = trade("RSI2", 1, D.index[k], o[k], 3 * a[i], o[k] - 3 * a[i])
        j, ferme = k, False
        while j < len(D):
            if l[j] <= t["stop"]:
                t["nuits"] = j - k
                clore(t, D.index[j] + timedelta(hours=12), min(o[j], t["stop"]), "stop")
                ferme = True
                break
            if not fini[j]:
                break
            if c[j] > s5[j] or j - k >= 10:
                if j + 1 < len(D):
                    t["nuits"] = j + 1 - k
                    clore(t, D.index[j + 1], o[j + 1], "clôture au-dessus de la moyenne 5 jours" if c[j] > s5[j] else "10 jours")
                    ferme = True
                else:
                    t["note"] = "sortie prévue à l'ouverture du prochain jour"
                j += 1
                break
            j += 1
        t["nuits"] = t["nuits"] or max(0, min(j, len(D) - 1) - k)
        out.append(t)
        if not ferme:
            break
        i = j + 1
    return out


def strat_asie(X, debut):
    m, D, now = X["m15"], X["D"], X["now"]
    T, H, L, C, O = m.index, m["high"].to_numpy(), m["low"].to_numpy(), m["close"].to_numpy(), m["open"].to_numpy()
    Df = D[D["fini"]]
    ema50 = Df["close"].ewm(span=50, adjust=False).mean()
    out, attente = [], None
    for jour in D.index:
        if jour + pd.Timedelta(hours=22) < debut or jour.weekday() >= 5:
            continue
        prec = Df.index[Df.index < jour]
        if len(prec) < 60:
            continue
        a0, a1 = T.searchsorted(jour + pd.Timedelta(hours=1)), T.searchsorted(jour + pd.Timedelta(hours=10))
        f0, f1 = a1, T.searchsorted(jour + pd.Timedelta(hours=15))
        fin = T.searchsorted(jour + pd.Timedelta(hours=22))
        if a1 - a0 < 20 or now < jour + pd.Timedelta(hours=10):
            continue
        haut, bas = float(H[a0:a1].max()), float(L[a0:a1].min())
        if haut <= bas:
            continue
        d = 1 if Df["close"].loc[prec[-1]] > ema50.loc[prec[-1]] else -1
        niveau = haut + 0.10 if d > 0 else bas - 0.10
        stop = bas if d > 0 else haut
        dec = np.flatnonzero(H[f0:f1] > niveau) if d > 0 else np.flatnonzero(L[f0:f1] < niveau)
        if not len(dec):
            if now < jour + pd.Timedelta(hours=15):         # fenêtre encore ouverte : ordre en attente
                attente = {"strat": "ASIE", "sens": d, "niveau": niveau, "stop": stop,
                           "objectif": niveau + d * 2 * abs(niveau - stop), "expire": jour + pd.Timedelta(hours=15)}
            continue
        k = f0 + int(dec[0])
        if T[k] < debut:
            continue
        e = max(O[k], niveau) if d > 0 else min(O[k], niveau)      # ouverture au-delà du niveau : exécuté à l'ouverture
        risque = abs(e - stop)
        if risque <= 0:
            continue
        t = trade("ASIE", d, T[k], e, risque, stop, e + d * 2 * risque)
        sh, sl_ = H[k:fin], L[k:fin]
        s = np.flatnonzero(sl_ <= stop) if d > 0 else np.flatnonzero(sh >= stop)
        g = np.flatnonzero(sh >= t["objectif"]) if d > 0 else np.flatnonzero(sl_ <= t["objectif"])
        if len(s) and (not len(g) or s[0] <= g[0]):
            clore(t, T[k + s[0]], stop, "stop")
        elif len(g):
            clore(t, T[k + g[0]], t["objectif"], "objectif 2 R")
        elif now >= jour + pd.Timedelta(hours=22) and fin > k:
            clore(t, T[fin - 1], C[fin - 1], "15 h New York")
        out.append(t)
    return out, attente


def strat_mom(X, debut):
    m, h1, h4 = X["m15"], X["h1"], X["h4"]
    if len(h4) < 120:
        return []
    c = m["close"]
    roc15 = (c / c.shift(10) - 1).to_numpy()
    a4 = atr(h4)
    h4 = h4.assign(roc=h4["close"] / h4["close"].shift(10) - 1, volh=(a4 > a4.rolling(100).median()).astype(float))
    lire = lambda sup, col: pd.Series(sup[col].to_numpy(), index=pd.DatetimeIndex(sup["fin"].to_numpy())).reindex(
        m["fin"].to_numpy(), method="ffill").to_numpy()
    roc4, volh = lire(h4, "roc"), lire(h4, "volh") > 0.5
    a1 = lire(h1.assign(a=atr(h1)), "a")
    achat, vente = (roc15 > 0) & (roc4 > 0) & volh, (roc15 < 0) & (roc4 < 0) & volh
    T, O, H, L, C, fins = m.index, m["open"].to_numpy(), m["high"].to_numpy(), m["low"].to_numpy(), m["close"].to_numpy(), m["fin"]
    out, libre = [], debut
    for i in np.flatnonzero(achat | vente):
        if fins.iloc[i] < libre or i + 1 >= len(m) or not np.isfinite(a1[i]) or a1[i] <= 0:
            continue
        k = i + 1
        if T[k] - fins.iloc[i] > pd.Timedelta(minutes=30):          # trou de données (pause, week-end)
            continue
        d = 1 if achat[i] else -1
        e = O[k] + (SPREAD_MOM if d > 0 else 0.0)
        t = trade("MOM", d, T[k], e, 3 * a1[i], e - d * 3 * a1[i], e + d * 4 * a1[i])
        k1 = min(len(m), k + 48)
        if d > 0:
            ts, tg = L[k:k1] <= t["stop"], H[k:k1] >= t["objectif"]
        else:
            ts, tg = H[k:k1] + SPREAD_MOM >= t["stop"], L[k:k1] + SPREAD_MOM <= t["objectif"]
        s = int(np.argmax(ts)) if ts.any() else None
        g = int(np.argmax(tg)) if tg.any() else None
        if s is not None and (g is None or s <= g):
            clore(t, T[k + s], t["stop"], "stop", cout=False)
            fin_ = k + s
        elif g is not None:
            clore(t, T[k + g], t["objectif"], "objectif 4 ATR", cout=False)
            fin_ = k + g
        elif k + 48 <= len(m):
            fin_ = k + 47
            clore(t, T[fin_], C[fin_] + (0.0 if d > 0 else SPREAD_MOM), "12 h écoulées", cout=False)
        else:
            out.append(t)
            break
        out.append(t)
        libre = m["fin"].iloc[fin_]
    return out


# ---------------------------------------------------------------------------
# REGISTRE ET COMPTE DU ROBOT
# ---------------------------------------------------------------------------

def vers_json(t):
    x = dict(t)
    for k in ("t_entree", "t_sortie"):
        if x.get(k) is not None:
            x[k] = pd.Timestamp(x[k]).isoformat()
    return x


def depuis_json(x):
    t = dict(x)
    for k in ("t_entree", "t_sortie"):
        if t.get(k):
            t[k] = pd.Timestamp(t[k])
    return t


def poids(clos_avant):
    """Part du risque engagée sur une stratégie, d'après ses trades déjà clos."""
    n = len(clos_avant)
    if n < OBSERVATION:
        return 0.25, "observation"
    moy = float(np.mean([t["R"] for t in clos_avant])) * n / (n + PRUDENCE)
    if moy <= 0:
        return 0.0, "coupée"
    return min(1.0, moy / CIBLE_R), "engagée"


def stats(liste):
    R = np.array([t["R"] for t in liste], dtype=float)
    if not len(R):
        return {"n": 0}
    cum = np.cumsum(R)
    serie = pire = 0
    for x in R:
        serie = serie + 1 if x < 0 else 0
        pire = max(pire, serie)
    pertes = -R[R < 0].sum()
    return {"n": int(len(R)), "reussite": float((R > 0).mean()), "moy": float(R.mean()), "total": float(cum[-1]),
            "pf": float(R[R > 0].sum() / pertes) if pertes > 0 else None,
            "dd": float((cum - np.maximum.accumulate(np.r_[0, cum])[1:]).min()), "pire": int(pire), "courbe": cum.tolist()}


def avancer(etat, intra, maintenant, compte):
    """Un passage du robot. etat : registre précédent (dict JSON) ou None pour démarrer.
    Renvoie (nouvel état, vue pour la page, alertes téléphone)."""
    etat = dict(etat or {})
    X = donnees(intra, maintenant)
    if X is None:
        vue = {"erreur": "pas assez de bougies pour faire tourner le robot"}
        return etat, vue, []
    if not etat.get("debut"):
        etat = {"version": VERSION, "debut": X["now"].isoformat(), "trades": {}, "vus": {}}
    debut = pd.Timestamp(etat["debut"])
    registre = {k: depuis_json(v) for k, v in etat.get("trades", {}).items()}
    vus = dict(etat.get("vus", {}))
    calc = {}
    attente = None
    for nom, f in (("T20", strat_t20), ("RSI2", strat_rsi2), ("ASIE", strat_asie), ("MOM", strat_mom)):
        try:
            r = f(X, debut)
            if nom == "ASIE":
                r, attente = r
            calc[nom] = r
        except Exception as e:                       # une stratégie en panne n'arrête pas les autres
            calc[nom] = []
            etat.setdefault("pannes", {})[nom] = f"{maintenant:%d/%m %H:%M} {str(e)[:80]}"
    px = float(X["m15"]["close"].iloc[-1]) if len(X["m15"]) else None
    ouverts, alertes = [], []
    for nom, liste in calc.items():
        for t in liste:
            if t["t_entree"] < debut:
                continue
            if t["R"] is not None:
                if t["id"] not in registre:
                    registre[t["id"]] = t
            elif t["id"] not in registre:
                t["latent"] = latent(t, px)
                ouverts.append(t)
    # compte du robot : chaque trade clos pondéré par les résultats de sa stratégie au moment de l'entrée
    clos = sorted(registre.values(), key=lambda t: t["t_sortie"])
    for t in sorted(registre.values(), key=lambda t: t["t_entree"]):
        avant = [x for x in clos if x["strat"] == t["strat"] and x["t_sortie"] <= t["t_entree"]]
        t["poids"], _ = poids(avant)
    for t in ouverts:
        avant = [x for x in clos if x["strat"] == t["strat"] and x["t_sortie"] <= t["t_entree"]]
        t["poids"], _ = poids(avant)
    # alertes : entrées et sorties, une seule fois chacune
    mot = lambda t: ("achat" if t["sens"] > 0 else "vente")
    for t in ouverts:
        cle = "in|" + t["id"]
        if cle not in vus:
            vus[cle] = maintenant.isoformat()
            alertes.append((f"robot|{cle}", f"Robot · {STRATS[t['strat']]['nom']}",
                            f"{mot(t).capitalize()} papier à {fr(t['entree'])}, stop {fr(t['stop'])}"
                            + (f", objectif {fr(t['objectif'])}" if t.get("objectif") else "")
                            + (f". Risque engagé : {fr(t['poids'] * RISQUE_PCT, 2)} %." if t["poids"] > 0 else
                               ". Stratégie coupée (elle perd en direct) : suivie sans risque engagé."),
                            3 if t["poids"] > 0 else 2, ["robot"]))
    for t in registre.values():
        cle = "out|" + t["id"]
        if cle not in vus and t["t_sortie"] is not None and (maintenant - pd.Timestamp(P.depuis_horloge(t["t_sortie"]))) < timedelta(hours=6):
            vus[cle] = maintenant.isoformat()
            alertes.append((f"robot|{cle}", f"Robot · {STRATS[t['strat']]['nom']}",
                            f"{mot(t).capitalize()} papier clôturé ({t['issue']}) : {fr(t['R'], 2, True)} R.", 3,
                            ["robot", "white_check_mark" if t["R"] > 0 else "x"]))
    limite = (maintenant - timedelta(days=30)).isoformat()
    vus = {k: v for k, v in vus.items() if v >= limite}
    etat.update(version=VERSION, trades={k: vers_json(v) for k, v in registre.items()}, vus=vus,
                maj=maintenant.isoformat())
    # vue pour la page
    par_strat = {}
    for nom in STRATS:
        cl = [t for t in clos if t["strat"] == nom]
        w, statut = poids(cl)
        par_strat[nom] = {"stats": stats(cl), "poids": w, "statut": statut,
                          "ouvert": next((t for t in ouverts if t["strat"] == nom), None)}
    robot = sorted([dict(t, R=t["R"] * t["poids"]) for t in clos], key=lambda t: t["t_sortie"])
    capital = compte.get("capital", 50000)
    vue = {"debut": P.depuis_horloge(debut), "strats": par_strat, "ouverts": ouverts, "attente": attente,
           "robot": stats(robot), "dollars": sum(t["R"] for t in robot) * capital * RISQUE_PCT / 100,
           "capital": capital, "historique": sorted(clos, key=lambda t: t["t_sortie"], reverse=True)[:60],
           "px": px, "pannes": etat.get("pannes", {}), "jours": max(0, (X["now"] - debut).days)}
    return etat, vue, alertes


# ---------------------------------------------------------------------------
# AFFICHAGE
# ---------------------------------------------------------------------------

def courbe(cum, w=720, h=180):
    if not cum:
        return ""
    cum = [0.0] + list(cum)
    mn, mx = min(cum), max(cum)
    et = (mx - mn) or 1.0
    g, d, ht, b = 44, 10, 12, 22
    x = lambda i: g + i / max(1, len(cum) - 1) * (w - g - d)
    y = lambda v: ht + (mx - v) / et * (h - ht - b)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(cum))
    coul = "var(--up)" if cum[-1] >= 0 else "var(--down)"
    return (f'<svg viewBox="0 0 {w} {h}" class="rb-svg" role="img" aria-label="Courbe du compte du robot en R">'
            f'<line class="gr" x1="{g}" x2="{w - d}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>'
            f'<text class="ax" x="{g - 6}" y="{y(mx) + 4:.1f}" text-anchor="end">{fr(mx, 1, True)}</text>'
            f'<text class="ax" x="{g - 6}" y="{y(mn) + 4:.1f}" text-anchor="end">{fr(mn, 1, True)}</text>'
            f'<polyline points="{pts}" fill="none" stroke="{coul}" stroke-width="2" stroke-linejoin="round"/></svg>')


def monte_carlo(R, n_suite=100, tirages=2000, graine=7):
    """Éventail des résultats possibles sur les n_suite prochains trades, en tirant au hasard dans les trades réels."""
    R = np.asarray(R, dtype=float)
    if len(R) < 15:
        return None
    rng = np.random.default_rng(graine)
    tir = rng.choice(R, size=(tirages, n_suite), replace=True).cumsum(axis=1)
    pire = np.minimum.accumulate(np.minimum(tir, 0), axis=1)[:, -1]
    q = lambda p: float(np.percentile(tir[:, -1], p))
    return {"p5": q(5), "p50": q(50), "p95": q(95), "perte": float((tir[:, -1] < 0).mean()),
            "dd_med": float(np.median(pire)), "n": n_suite}


def heure(t):
    return P.depuis_horloge(t).strftime("%d/%m %H:%M") if t is not None else "—"


def carte_accueil(vue):
    """Carte de la Vue d'ensemble : ce que fait le robot maintenant."""
    if not vue or vue.get("erreur"):
        return ""
    R = vue["robot"]
    ouv = vue["ouverts"]
    lig = ""
    for t in ouv:
        lig += (f'<tr><td>{esc(STRATS[t["strat"]]["nom"])}</td><td class="{"up" if t["sens"] > 0 else "down"}">'
                f'{"Achat" if t["sens"] > 0 else "Vente"} {fr(t["entree"], 1)}</td>'
                f'<td class="num {"up" if (t.get("latent") or 0) > 0 else "down"}">{fr(t.get("latent"), 2, True)} R</td></tr>')
    at = vue.get("attente")
    if at:
        lig += (f'<tr><td>{esc(STRATS[at["strat"]]["nom"])}</td><td class="warn">Ordre {"achat" if at["sens"] > 0 else "vente"} '
                f'à {fr(at["niveau"], 1)}</td><td class="num">jusqu\'à {P.depuis_horloge(at["expire"]):%H:%M}</td></tr>')
    corps = (f'<table class="ps-mk">{lig}</table>' if lig else '<p class="ps-mini">Aucune position ouverte, aucun ordre en attente.</p>')
    res = (f'{R["n"]} trades clos · <b class="{"up" if R["total"] > 0 else "down"}">{fr(R["total"], 1, True)} R</b>'
           if R.get("n") else "aucun trade clos pour l'instant")
    return (f'<div class="ps-carte rb-carte" data-zone="rb-carte"><div class="ps-tete"><span class="k">Robot papier</span>'
            f'<a href="#analyse" data-aller="robot" class="ps-lien">détail</a></div>{corps}'
            f'<p class="ps-mini">Depuis le {vue["debut"]:%d/%m} : {res}. Sans argent réel.</p></div>')


def bloc_robot(vue):
    if not vue or vue.get("erreur"):
        return (f'<section><h2>Robot papier</h2><p class="pourquoi">Indisponible : '
                f'{esc((vue or {}).get("erreur", "pas de données"))}.</p></section>')
    R = vue["robot"]
    h = []
    intro = (f'<p class="pourquoi">Le robot trade tout seul depuis le <b>{vue["debut"]:%d/%m/%Y à %H:%M}</b>, à chaque passage du '
             f'terminal, <b>sans argent réel</b>. Ses règles sont fixées à l\'avance et ne changent jamais : ce registre est un '
             f'test en direct, impossible à arranger après coup. Il engage chaque stratégie selon ses résultats en direct : '
             f'un quart du risque pendant les {OBSERVATION} premiers trades, puis plus de risque si elle gagne, et plus rien si elle perd. '
             f'Compte de référence : {fr(vue["capital"], 0)} $ à {fr(RISQUE_PCT, 0)} % de risque par trade.</p>')
    if R.get("n"):
        kp = [("Trades clos", f'{R["n"]}', f'en {vue["jours"]} jours'),
              ("Résultat", f'{fr(R["total"], 2, True)} R', f'{fr(vue["dollars"], 0, True)} $ sur le compte de référence'),
              ("Réussite", f'{R["reussite"] * 100:.0f} %', ""), ("Gain moyen", f'{fr(R["moy"], 3, True)} R', "par trade, pondéré"),
              ("Profit factor", "∞" if R["pf"] is None else fr(R["pf"], 2), "gains ÷ pertes"),
              ("Pire baisse", f'{fr(R["dd"], 2, True)} R', f'pire série : {R["pire"]} pertes')]
    else:
        kp = [("Trades clos", "0", f'démarré il y a {vue["jours"]} jour{"s" if vue["jours"] > 1 else ""}'),
              ("Positions ouvertes", str(len(vue["ouverts"])), "")]
    kps = "".join(f'<div><span class="k">{esc(k)}</span><b>{esc(v)}</b><small>{esc(s)}</small></div>' for k, v, s in kp)
    h.append(f'<section><h2>Le robot</h2>{intro}<div class="rb-kpis">{kps}</div>'
             + (f'<div class="rb-graph">{courbe(R["courbe"])}</div>' if R.get("n") else "")
             + (f'<p class="rb-avert">Moins de 30 trades : le résultat dépend encore surtout du hasard. Il faut en général '
                f'2 à 3 mois et au moins 50 trades avant de juger.</p>' if R.get("n", 0) < 30 else "") + '</section>')
    # positions ouvertes
    lig = ""
    for t in vue["ouverts"]:
        lig += (f'<tr><td>{esc(STRATS[t["strat"]]["nom"])}</td><td class="{"up" if t["sens"] > 0 else "down"}">{"Achat" if t["sens"] > 0 else "Vente"}</td>'
                f'<td>{heure(t["t_entree"])}</td><td class="num">{fr(t["entree"])}</td><td class="num">{fr(t["stop"])}</td>'
                f'<td class="num">{fr(t.get("objectif"))}</td><td class="num">{fr(t["poids"] * RISQUE_PCT, 2)} %</td>'
                f'<td class="num {"up" if (t.get("latent") or 0) > 0 else "down"}">{fr(t.get("latent"), 2, True)} R</td>'
                f'<td>{esc(t.get("note", ""))}</td></tr>')
    at = vue.get("attente")
    if at:
        lig += (f'<tr class="rb-att"><td>{esc(STRATS["ASIE"]["nom"])}</td><td class="{"up" if at["sens"] > 0 else "down"}">'
                f'Ordre {"achat" if at["sens"] > 0 else "vente"}</td><td>jusqu\'à {P.depuis_horloge(at["expire"]):%H:%M}</td>'
                f'<td class="num">{fr(at["niveau"])}</td><td class="num">{fr(at["stop"])}</td><td class="num">{fr(at["objectif"])}</td>'
                f'<td></td><td></td><td>ordre stop en attente</td></tr>')
    h.append('<section><h2>Positions et ordres</h2><p class="pourquoi">Prix du future converti, comme tous les calculs du plan ; '
             'le résultat latent compte les frais.</p>'
             + (f'<div class="tbl"><table class="rb-tab"><thead><tr><th>Stratégie</th><th>Sens</th><th>Entrée le</th><th>Entrée</th>'
                f'<th>Stop</th><th>Objectif</th><th>Risque</th><th>Latent</th><th></th></tr></thead><tbody>{lig}</tbody></table></div>'
                if lig else '<p class="vide">Aucune position ouverte et aucun ordre en attente.</p>') + '</section>')
    # stratégies
    cartes = ""
    for nom, S in vue["strats"].items():
        st, d = S["stats"], STRATS[nom]
        etat = {"observation": ("Observation", "warn"), "engagée": ("Engagée", "up"), "coupée": ("Coupée", "down")}[S["statut"]]
        chiffres = (f'{st["n"]} trades · {fr(st["total"], 1, True)} R · réussite {st["reussite"] * 100:.0f} %'
                    if st.get("n") else "pas encore de trade clos")
        cartes += (f'<div class="rb-strat"><div class="rb-st-tete"><b>{esc(d["nom"])}</b><span class="rb-etat {etat[1]}">{etat[0]}</span></div>'
                   f'<p>{esc(d["court"])}</p><p class="ps-mini">{esc(d["ut"])} · {esc(d["horizon"])}</p>'
                   f'<div class="rb-st-ch"><span>{esc(chiffres)}</span><span>risque engagé {fr(S["poids"] * RISQUE_PCT, 2)} %</span></div>'
                   + (f'<p class="rb-panne">En panne : {esc(vue["pannes"][nom])}</p>' if nom in vue.get("pannes", {}) else "")
                   + '</div>')
    h.append(f'<section><h2>Ses quatre stratégies</h2><p class="pourquoi">Chacune a ses règles écrites à l\'avance (onglet Labo '
             f'pour leurs résultats au backtest). Le statut change tout seul selon les résultats en direct.</p>'
             f'<div class="rb-strats">{cartes}</div></section>')
    mc = monte_carlo([t["R"] for t in sorted(vue["historique"], key=lambda t: t["t_sortie"])]) if R.get("n", 0) >= 15 else None
    if mc:
        h.append(f'<section><h2>Éventail des {mc["n"]} prochains trades</h2><p class="pourquoi">2 000 suites de trades tirées au hasard '
                 f'parmi les trades réels du robot (méthode de Monte Carlo). Si l\'avenir ressemble au passé récent : résultat '
                 f'médian {fr(mc["p50"], 1, True)} R, 9 fois sur 10 entre {fr(mc["p5"], 1, True)} et {fr(mc["p95"], 1, True)} R, '
                 f'perte au bout de {mc["n"]} trades dans {mc["perte"] * 100:.0f} % des cas, baisse médiane en cours de route '
                 f'{fr(mc["dd_med"], 1, True)} R.</p></section>')
    # registre
    lig = "".join(
        f'<tr><td>{esc(STRATS[t["strat"]]["nom"])}</td><td class="{"up" if t["sens"] > 0 else "down"}">{"Achat" if t["sens"] > 0 else "Vente"}</td>'
        f'<td>{heure(t["t_entree"])}</td><td class="num">{fr(t["entree"])}</td><td>{heure(t["t_sortie"])}</td><td class="num">{fr(t["sortie"])}</td>'
        f'<td>{esc(t["issue"])}</td><td class="num {"up" if t["R"] > 0 else "down"}">{fr(t["R"], 2, True)} R</td>'
        f'<td class="num">{fr(t.get("poids", 0) * RISQUE_PCT, 2)} %</td></tr>' for t in vue["historique"])
    h.append('<section><h2>Registre des trades clos</h2><p class="pourquoi">Un trade clos est figé : il ne peut plus être modifié.</p>'
             + (f'<div class="tbl"><table class="rb-tab"><thead><tr><th>Stratégie</th><th>Sens</th><th>Entrée le</th><th>Entrée</th>'
                f'<th>Sortie le</th><th>Sortie</th><th>Issue</th><th>Résultat</th><th>Risque engagé</th></tr></thead><tbody>{lig}</tbody></table></div>'
                if lig else '<p class="vide">Pas encore de trade clos.</p>') + '</section>')
    h.append('<section><h2>Vers le réel (MetaTrader 5)</h2><p class="pourquoi">Le robot ne passe aucun ordre réel. Le brancher sur ton '
             'compte MT5 ne se fera que si tu le décides, après au moins 50 trades papier gagnants, avec des garde-fous : taille '
             'maximale, arrêt automatique si les données sont en retard, coupure d\'urgence. Pour l\'instant, tu peux recopier '
             'ses ordres à la main sur un compte démo MT5 et comparer.</p></section>')
    return "".join(h)


CSS = """
/* Robot papier */
.rb-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:12px}
.rb-kpis div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:2px;min-width:0}
.rb-kpis b{font:700 20px var(--mono);letter-spacing:-.02em} .rb-kpis small{font-size:12px;color:var(--mute)}
.rb-graph{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.rb-svg{display:block;width:100%;height:auto} .rb-svg .gr{stroke:#2a2a31} .rb-svg .ax{fill:var(--mute);font:500 11px var(--mono)}
.rb-avert{margin:10px 0 0;font-size:13px;color:var(--amber)}
.rb-tab{width:100%;border-collapse:collapse;font-size:13.5px}
.rb-tab th{text-align:left;font:600 10.5px var(--text);letter-spacing:.07em;text-transform:uppercase;color:var(--mute);padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.rb-tab td{padding:8px 10px;border-bottom:1px solid var(--band2);white-space:nowrap}
.rb-tab td.num{font-family:var(--mono);text-align:right} .rb-tab tr.rb-att td{color:var(--amber)}
.rb-strats{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.rb-strat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:6px;min-width:0}
.rb-strat p{margin:0;font-size:13.5px;line-height:1.45}
.rb-st-tete{display:flex;justify-content:space-between;align-items:center;gap:8px}
.rb-etat{font:700 11px var(--text);border:1px solid currentColor;border-radius:5px;padding:2px 8px}
.rb-st-ch{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px 12px;font:500 12.5px var(--mono);color:var(--mute);padding-top:6px;border-top:1px solid var(--band2)}
.rb-panne{color:var(--down)!important;font-size:12.5px!important}
.rb-carte td:nth-child(2){white-space:nowrap}
"""
