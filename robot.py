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
  - moins de 10 trades clos : phase d'observation, un quart du risque plein ;
  - ensuite : gain moyen réduit par prudence (× n / (n + 20)) ; risque plein (5 %) à partir de +0,10 R par trade,
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
RISQUE_PCT = 5.0            # risque plein par trade, en % du solde (demandé : 5 %)
OBSERVATION = 10          # trades clos avant de juger une stratégie
PRUDENCE = 20             # réduction du gain moyen : × n / (n + PRUDENCE)
CIBLE_R = 0.10            # gain moyen (réduit) qui vaut le risque plein

# Marchés du robot. cout : frais d'un aller-retour ; ecart : écart achat-vente ; pas : marge de déclenchement d'un
# ordre ; fin : financement annuel d'une position gardée la nuit ; plafond : part maximale du risque.
MARCHES = {
    "XAU": {"nom": "Or", "sym": "XAU/USD", "yahoo": None, "classe": "or", "weekend": False, "plafond": 1.0,
            "cout": lambda px: COUT_FIXE, "ecart": lambda px: SPREAD_MOM, "pas": lambda px: 0.10, "fin": FINANCEMENT},
    "BTC": {"nom": "Bitcoin", "sym": "BTC/USD", "yahoo": "BTC-USD", "classe": "crypto", "weekend": True, "plafond": 1.0,
            "cout": lambda px: px * 0.0010, "ecart": lambda px: px * 0.0005, "pas": lambda px: px * 0.0002, "fin": 0.10},
    "ETH": {"nom": "Ethereum", "sym": "ETH/USD", "yahoo": "ETH-USD", "classe": "crypto", "weekend": True, "plafond": 1.0,
            "cout": lambda px: px * 0.0010, "ecart": lambda px: px * 0.0005, "pas": lambda px: px * 0.0002, "fin": 0.10},
    "SOL": {"nom": "Solana", "sym": "SOL/USD", "yahoo": "SOL-USD", "classe": "crypto", "weekend": True, "plafond": 1.0,
            "cout": lambda px: px * 0.0010, "ecart": lambda px: px * 0.0005, "pas": lambda px: px * 0.0002, "fin": 0.10},
    "XRP": {"nom": "XRP", "sym": "XRP/USD", "yahoo": "XRP-USD", "classe": "crypto", "weekend": True, "plafond": 1.0,
            "cout": lambda px: px * 0.0010, "ecart": lambda px: px * 0.0005, "pas": lambda px: px * 0.0002, "fin": 0.10},
    "BNB": {"nom": "BNB", "sym": "BNB/USD", "yahoo": "BNB-USD", "classe": "crypto", "weekend": True, "plafond": 1.0,
            "cout": lambda px: px * 0.0010, "ecart": lambda px: px * 0.0005, "pas": lambda px: px * 0.0002, "fin": 0.10},
    "ADA": {"nom": "Cardano", "sym": "ADA/USD", "yahoo": "ADA-USD", "classe": "crypto", "weekend": True, "plafond": 1.0,
            "cout": lambda px: px * 0.0010, "ecart": lambda px: px * 0.0005, "pas": lambda px: px * 0.0002, "fin": 0.10},
    "AVAX": {"nom": "Avalanche", "sym": "AVAX/USD", "yahoo": "AVAX-USD", "classe": "crypto", "weekend": True, "plafond": 1.0,
            "cout": lambda px: px * 0.0010, "ecart": lambda px: px * 0.0005, "pas": lambda px: px * 0.0002, "fin": 0.10},
    "LINK": {"nom": "Chainlink", "sym": "LINK/USD", "yahoo": "LINK-USD", "classe": "crypto", "weekend": True, "plafond": 1.0,
            "cout": lambda px: px * 0.0010, "ecart": lambda px: px * 0.0005, "pas": lambda px: px * 0.0002, "fin": 0.10},
    "DOGE": {"nom": "Dogecoin", "sym": "DOGE/USD", "yahoo": "DOGE-USD", "classe": "meme", "weekend": True, "plafond": 0.25,
            "cout": lambda px: px * 0.0030, "ecart": lambda px: px * 0.0015, "pas": lambda px: px * 0.0005, "fin": 0.15},
    "SHIB": {"nom": "Shiba Inu", "sym": "SHIB/USD", "yahoo": "SHIB-USD", "classe": "meme", "weekend": True, "plafond": 0.25,
            "cout": lambda px: px * 0.0030, "ecart": lambda px: px * 0.0015, "pas": lambda px: px * 0.0005, "fin": 0.15},
    "PEPE": {"nom": "Pepe", "sym": "PEPE/USD", "yahoo": "PEPE24478-USD", "classe": "meme", "weekend": True, "plafond": 0.25,
            "cout": lambda px: px * 0.0030, "ecart": lambda px: px * 0.0015, "pas": lambda px: px * 0.0005, "fin": 0.15},
    "BONK": {"nom": "Bonk", "sym": "BONK/USD", "yahoo": "BONK-USD", "classe": "meme", "weekend": True, "plafond": 0.25,
            "cout": lambda px: px * 0.0030, "ecart": lambda px: px * 0.0015, "pas": lambda px: px * 0.0005, "fin": 0.15},
    "WIF": {"nom": "dogwifhat", "sym": "WIF/USD", "yahoo": "WIF-USD", "classe": "meme", "weekend": True, "plafond": 0.25,
            "cout": lambda px: px * 0.0030, "ecart": lambda px: px * 0.0015, "pas": lambda px: px * 0.0005, "fin": 0.15},
    "FLOKI": {"nom": "Floki", "sym": "FLOKI/USD", "yahoo": "FLOKI-USD", "classe": "meme", "weekend": True, "plafond": 0.25,
            "cout": lambda px: px * 0.0030, "ecart": lambda px: px * 0.0015, "pas": lambda px: px * 0.0005, "fin": 0.15},
}
CAPITAL = 500.0           # capital de départ du compte papier, en $
COINBASE = {k: k + "-USD" for k in MARCHES if k not in ("XAU", "BNB")}   # prix en direct dans la page
REJEU_JOURS = 55          # rejeu : ce que le robot aurait fait sur les 55 derniers jours (simulation, hors registre)

STRATS = {
    "T20": {"nom": "Tendance 20 jours", "court": "Achat sur cassure du plus haut de 20 jours, stop suiveur",
            "ut": "Daily", "horizon": "plusieurs jours à semaines"},
    "RSI2": {"nom": "Repli RSI 2", "court": "Achat d'un repli court dans une tendance de fond haussière",
             "ut": "Daily", "horizon": "1 à 10 jours"},
    "ASIE": {"nom": "Cassure du range asiatique", "court": "Cassure du range de la nuit pendant Londres, objectif 2 R",
             "ut": "M15", "horizon": "la journée"},
    "SCALP": {"nom": "Scalp repli M15", "court": "Repli éclair (RSI 2 en M15) dans le sens de la tendance 1 h : stop et objectif à 1,5 ATR M15, 2 h maximum",
              "ut": "M15", "horizon": "15 min à 2 h"},
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


def fpx(x):
    """Prix lisible quelle que soit sa taille (4 100,25 comme 0,00001234)."""
    if x is None or not math.isfinite(float(x)):
        return "—"
    x = float(x)
    d = 2 if abs(x) >= 100 else (3 if abs(x) >= 1 else min(10, 3 - int(math.floor(math.log10(abs(x) or 1e-12)))))
    return fr(x, d)


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

def donnees(intra, maintenant, weekend=False):
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
    if not weekend:
        D = D[D.index.weekday < 5]
    D = D.copy()
    D["fini"] = D["fin"] <= now
    h4 = P.agreger(h1_fini[["open", "high", "low", "close"]], "4h")
    h4 = h4[h4["fin"] <= now]
    return {"m15": m15, "h1": h1_fini, "h4": h4, "D": D, "now": now}


def trade(strat, sens, t_e, entree, risque, stop, objectif=None, M=None, cle="XAU"):
    M = M or MARCHES["XAU"]
    pre = "" if cle == "XAU" else cle + ":"
    return {"strat": strat, "marche": cle, "id": f"{pre}{strat}-{pd.Timestamp(t_e):%Y%m%d%H%M}", "sens": int(sens),
            "t_entree": pd.Timestamp(t_e), "entree": float(entree), "risque": float(risque), "stop": float(stop),
            "objectif": None if objectif is None else float(objectif), "t_sortie": None, "sortie": None,
            "issue": None, "R": None, "nuits": 0, "frais": float(M["cout"](entree)), "fin": M["fin"]}


def clore(t, t_s, sortie, issue, cout=True):
    brut = (sortie - t["entree"]) * t["sens"]
    frais = (t.get("frais", COUT_FIXE) + t["nuits"] * t["entree"] * t.get("fin", FINANCEMENT) / 365) if cout else 0.0
    t.update(t_sortie=pd.Timestamp(t_s), sortie=float(sortie), issue=issue, R=float((brut - frais) / t["risque"]))
    return t


def latent(t, px):
    """Résultat d'une position ouverte au prix px (coûts compris)."""
    if px is None:
        return None
    brut = (px - t["entree"]) * t["sens"]
    frais = t.get("frais", COUT_FIXE) if t["strat"] not in ("MOM", "SCALP") else 0.0
    return float((brut - frais) / t["risque"])


# ---------------------------------------------------------------------------
# STRATÉGIES (même logique que backtest/recherche.py et backtest/strategies_mtf.py)
# ---------------------------------------------------------------------------

def strat_t20(X, debut, M=None, cle="XAU"):
    M = M or MARCHES["XAU"]
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
        t = trade("T20", 1, D.index[k], o[k], 2 * a[i], o[k] - 2 * a[i], M=M, cle=cle)
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


def strat_rsi2(X, debut, M=None, cle="XAU"):
    M = M or MARCHES["XAU"]
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
        t = trade("RSI2", 1, D.index[k], o[k], 3 * a[i], o[k] - 3 * a[i], M=M, cle=cle)
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


def strat_asie(X, debut, M=None, cle="XAU"):
    M = M or MARCHES["XAU"]
    m, D, now = X["m15"], X["D"], X["now"]
    T, H, L, C, O = m.index, m["high"].to_numpy(), m["low"].to_numpy(), m["close"].to_numpy(), m["open"].to_numpy()
    Df = D[D["fini"]]
    ema50 = Df["close"].ewm(span=50, adjust=False).mean()
    out, attente = [], None
    for jour in D.index:
        if jour + pd.Timedelta(hours=22) < debut or (jour.weekday() >= 5 and not M["weekend"]):
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
        pas = M["pas"](haut)
        niveau = haut + pas if d > 0 else bas - pas
        stop = bas if d > 0 else haut
        dec = np.flatnonzero(H[f0:f1] > niveau) if d > 0 else np.flatnonzero(L[f0:f1] < niveau)
        if not len(dec):
            if now < jour + pd.Timedelta(hours=15):         # fenêtre encore ouverte : ordre en attente
                attente = {"strat": "ASIE", "marche": cle, "sens": d, "niveau": niveau, "stop": stop,
                           "objectif": niveau + d * 2 * abs(niveau - stop), "expire": jour + pd.Timedelta(hours=15)}
            continue
        k = f0 + int(dec[0])
        if T[k] < debut:
            continue
        e = max(O[k], niveau) if d > 0 else min(O[k], niveau)      # ouverture au-delà du niveau : exécuté à l'ouverture
        risque = abs(e - stop)
        if risque <= 0:
            continue
        t = trade("ASIE", d, T[k], e, risque, stop, e + d * 2 * risque, M=M, cle=cle)
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


def strat_mom(X, debut, M=None, cle="XAU"):
    M = M or MARCHES["XAU"]
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
        sp = M["ecart"](O[k])
        e = O[k] + (sp if d > 0 else 0.0)
        t = trade("MOM", d, T[k], e, 3 * a1[i], e - d * 3 * a1[i], e + d * 4 * a1[i], M=M, cle=cle)
        k1 = min(len(m), k + 48)
        if d > 0:
            ts, tg = L[k:k1] <= t["stop"], H[k:k1] >= t["objectif"]
        else:
            ts, tg = H[k:k1] + sp >= t["stop"], L[k:k1] + sp <= t["objectif"]
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
            clore(t, T[fin_], C[fin_] + (0.0 if d > 0 else sp), "12 h écoulées", cout=False)
        else:
            out.append(t)
            break
        out.append(t)
        libre = m["fin"].iloc[fin_]
    return out


def strat_scalp(X, debut, M=None, cle="XAU"):
    """Scalp : dans le sens de la tendance 1 h (clôture H1 par rapport à son EMA 50), on entre sur un repli éclair en M15
    (RSI 2 sous 10 pour un achat, au-dessus de 90 pour une vente). Entrée à l'ouverture de la bougie suivante,
    stop 1,5 ATR M15, objectif 1,5 ATR M15, sortie au bout de 8 bougies (2 h). Une position à la fois par marché."""
    M = M or MARCHES["XAU"]
    m, h1 = X["m15"], X["h1"]
    if len(h1) < 80 or len(m) < 60:
        return []
    r2 = rsi(m["close"], 2).to_numpy()
    a15 = atr(m).to_numpy()
    h1 = h1.assign(t=np.sign(h1["close"] - h1["close"].ewm(span=50, adjust=False).mean()))
    tend = pd.Series(h1["t"].to_numpy(), index=pd.DatetimeIndex(h1["fin"].to_numpy())).reindex(m["fin"].to_numpy(), method="ffill").to_numpy()
    achat, vente = (tend > 0) & (r2 < 10), (tend < 0) & (r2 > 90)
    T, O, H, L, C, fins = m.index, m["open"].to_numpy(), m["high"].to_numpy(), m["low"].to_numpy(), m["close"].to_numpy(), m["fin"]
    out, libre = [], debut
    for i in np.flatnonzero(achat | vente):
        if fins.iloc[i] < libre or i + 1 >= len(m) or not np.isfinite(a15[i]) or a15[i] <= 0:
            continue
        k = i + 1
        if T[k] - fins.iloc[i] > pd.Timedelta(minutes=30):
            continue
        d = 1 if achat[i] else -1
        sp = M["ecart"](O[k])
        e = O[k] + (sp if d > 0 else 0.0)
        t = trade("SCALP", d, T[k], e, 1.5 * a15[i], e - d * 1.5 * a15[i], e + d * 1.5 * a15[i], M=M, cle=cle)
        k1 = min(len(m), k + 8)
        if d > 0:
            ts, tg = L[k:k1] <= t["stop"], H[k:k1] >= t["objectif"]
        else:
            ts, tg = H[k:k1] + sp >= t["stop"], L[k:k1] + sp <= t["objectif"]
        s_ = int(np.argmax(ts)) if ts.any() else None
        g = int(np.argmax(tg)) if tg.any() else None
        if s_ is not None and (g is None or s_ <= g):
            fin_ = k + s_
            clore(t, T[fin_], t["stop"], "stop", cout=False)
        elif g is not None:
            fin_ = k + g
            clore(t, T[fin_], t["objectif"], "objectif", cout=False)
        elif k + 8 <= len(m):
            fin_ = k + 7
            clore(t, T[fin_], C[fin_] + (0.0 if d > 0 else sp), "2 h écoulées", cout=False)
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


def poids(clos_avant, plafond=1.0):
    """Part du risque engagée sur une stratégie d'un marché, d'après ses trades déjà clos."""
    n = len(clos_avant)
    if n < OBSERVATION:
        return min(0.25, plafond), "observation"
    moy = float(np.mean([t["R"] for t in clos_avant])) * n / (n + PRUDENCE)
    if moy <= 0:
        return 0.0, "coupée"
    return min(plafond, moy / CIBLE_R), "engagée"


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


def cle_cellule(t):
    return (t.get("marche", "XAU"), t["strat"])


def ponderer(clos, autres=()):
    """Donne à chaque trade (clos ou ouvert) le risque que le robot avait engagé à son entrée."""
    clos = sorted(clos, key=lambda t: t["t_sortie"])
    for t in list(clos) + list(autres):
        avant = [x for x in clos if cle_cellule(x) == cle_cellule(t) and x["t_sortie"] <= t["t_entree"]]
        t["poids"], _ = poids(avant, MARCHES[t.get("marche", "XAU")]["plafond"])
    return clos


def diagnostic(X, M):
    """Ce que chaque stratégie voit en ce moment sur un marché : {strat: (texte, 'signal' | 'proche' | 'rien')}."""
    out = {}
    D, m, h4, now = X["D"], X["m15"], X["h4"], X["now"]
    Df = D[D["fini"]]
    pc = lambda a, b: fr((a / b - 1) * 100, 2, True, " %")
    try:
        c, hh = float(Df["close"].iloc[-1]), float(Df["high"].iloc[-21:-1].max())
        out["T20"] = ((f"clôture {fpx(c)} au-dessus du plus haut 20 jours {fpx(hh)} : achat à l'ouverture", "signal") if c > hh else
                      (f"clôture {fpx(c)} · plus haut 20 jours {fpx(hh)} · manque {pc(hh, c)}", "proche" if hh / c - 1 < 0.01 else "rien"))
    except Exception:
        out["T20"] = ("historique insuffisant", "rien")
    try:
        r2, s200, c = float(rsi(Df["close"], 2).iloc[-1]), float(Df["close"].iloc[-200:].mean()), float(Df["close"].iloc[-1])
        fond = c > s200
        out["RSI2"] = ((f"RSI(2) {r2:.0f} sous 10 en tendance haussière : achat à l'ouverture", "signal") if (fond and r2 < 10) else
                       (f"RSI(2) {r2:.0f} (seuil 10) · prix {'au-dessus' if fond else 'sous'} la moyenne 200 jours"
                        + ("" if fond else " : pas d'achat"), "proche" if (fond and r2 < 25) else "rien"))
    except Exception:
        out["RSI2"] = ("historique insuffisant", "rien")
    try:
        jour = D.index[-1]
        ema = Df["close"].ewm(span=50, adjust=False).mean()
        d = 1 if Df["close"].iloc[-1] > ema.iloc[-1] else -1
        sens = "achats" if d > 0 else "ventes"
        if now < jour + pd.Timedelta(hours=10):
            out["ASIE"] = (f"range de la nuit en formation jusqu'à {P.depuis_horloge(jour + pd.Timedelta(hours=10)):%H:%M} · sens du jour : {sens}", "rien")
        else:
            r = m[(m.index >= jour + pd.Timedelta(hours=1)) & (m.index < jour + pd.Timedelta(hours=10))]
            hi, lo = float(r["high"].max()), float(r["low"].min())
            if now < jour + pd.Timedelta(hours=15):
                out["ASIE"] = (f"range {fpx(lo)} – {fpx(hi)} · ordre {'achat' if d > 0 else 'vente'} à {fpx(hi + M['pas'](hi) if d > 0 else lo - M['pas'](lo))} "
                               f"jusqu'à {P.depuis_horloge(jour + pd.Timedelta(hours=15)):%H:%M}", "proche")
            else:
                out["ASIE"] = (f"range {fpx(lo)} – {fpx(hi)} · fenêtre d'ordres fermée pour aujourd'hui", "rien")
    except Exception:
        out["ASIE"] = ("range indisponible", "rien")
    try:
        h1 = X["h1"]
        t1 = float(h1["close"].iloc[-1] - h1["close"].ewm(span=50, adjust=False).mean().iloc[-1])
        r = float(rsi(m["close"], 2).iloc[-1])
        sig = (t1 > 0 and r < 10) or (t1 < 0 and r > 90)
        pres = (t1 > 0 and r < 25) or (t1 < 0 and r > 75)
        out["SCALP"] = (f"tendance 1 h {'haussière' if t1 > 0 else 'baissière'} · RSI(2) M15 {r:.0f} "
                        + (f": {'achat' if t1 > 0 else 'vente'} à l'ouverture" if sig else f"(seuil {'10' if t1 > 0 else '90'})"),
                        "signal" if sig else ("proche" if pres else "rien"))
    except Exception:
        out["SCALP"] = ("historique insuffisant", "rien")
    try:
        c = m["close"]
        r15 = float(c.iloc[-1] / c.iloc[-11] - 1)
        a4 = atr(h4)
        r4 = float(h4["close"].iloc[-1] / h4["close"].iloc[-11] - 1)
        vol = bool(a4.iloc[-1] > a4.rolling(100).median().iloc[-1])
        sig = vol and (r15 > 0) == (r4 > 0)
        out["MOM"] = (f"M15 {fr(r15 * 100, 2, True, ' %')} · H4 {fr(r4 * 100, 2, True, ' %')} · volatilité H4 {'haute' if vol else 'basse'}"
                      + (f" : {'achat' if r15 > 0 else 'vente'}" if sig else (" : pas d'accord" if vol else " : pas de trade")), "signal" if sig else "rien")
    except Exception:
        out["MOM"] = ("historique insuffisant", "rien")
    return out


def calculer(X, debut, M, cle):
    """Toutes les stratégies sur un marché. Renvoie (trades, ordres en attente, pannes)."""
    out, attentes, pannes = [], [], {}
    for nom, f in (("T20", strat_t20), ("RSI2", strat_rsi2), ("ASIE", strat_asie), ("SCALP", strat_scalp), ("MOM", strat_mom)):
        try:
            r = f(X, debut, M, cle)
            if nom == "ASIE":
                r, at = r
                if at:
                    attentes.append(at)
            out += [t for t in r if t["t_entree"] >= debut]
        except Exception as e:                       # une stratégie en panne n'arrête pas les autres
            pannes[f"{cle} {nom}"] = str(e)[:80]
    return out, attentes, pannes


def avancer(etat, marches, maintenant, compte, rejeu=True):
    """Un passage du robot. etat : registre précédent (dict JSON) ou None pour démarrer ;
    marches : {"XAU": bougies, "BTC": bougies, ...} (barres 15 min + heures).
    Renvoie (nouvel état, vue pour la page, alertes téléphone)."""
    etat = dict(etat or {})
    if marches is not None and "barres" in marches:      # ancien appel : l'or seul
        marches = {"XAU": marches}
    debuts = dict(etat.get("debuts") or {})
    if etat.get("debut") and "XAU" not in debuts:
        debuts["XAU"] = etat["debut"]
    registre = {k: depuis_json(v) for k, v in etat.get("trades", {}).items()}
    for t in registre.values():
        t.setdefault("marche", "XAU")
    vus = dict(etat.get("vus", {}))
    ouverts, attentes, pannes, infos, simu = [], [], {}, {}, []
    for cle, M in MARCHES.items():
        X = donnees((marches or {}).get(cle), maintenant, M["weekend"])
        if X is None:
            infos[cle] = {"ok": False}
            continue
        if cle not in debuts:
            debuts[cle] = X["now"].isoformat()
        debut = pd.Timestamp(debuts[cle])
        px = float(X["m15"]["close"].iloc[-1])
        c = X["m15"]["close"]
        Dj = X["D"][X["D"]["fini"]]
        force = None
        if len(Dj) > 30:
            aj = float(atr(Dj, 20).iloc[-1])
            force = float((Dj["close"].iloc[-1] - Dj["close"].iloc[-21]) / (aj * math.sqrt(20))) if aj > 0 else None
        infos[cle] = {"ok": True, "px": px, "diag": diagnostic(X, M), "force": force, "nb": len(X["m15"]) + len(X["h1"]), "var": float(px / c.iloc[-97] - 1) if len(c) > 97 else None,
                      "spark": [float(v) for v in c.iloc[-96::4]], "debut": P.depuis_horloge(debut)}
        tr, at, pn = calculer(X, debut, M, cle)
        attentes += at
        pannes.update(pn)
        for t in tr:
            if t["R"] is not None:
                registre.setdefault(t["id"], t)
            elif t["id"] not in registre:
                t["latent"] = latent(t, px)
                ouverts.append(t)
        if rejeu:
            try:
                tr2, _, _ = calculer(X, X["now"] - pd.Timedelta(days=REJEU_JOURS), M, cle)
                simu += [t for t in tr2 if t["R"] is not None]
            except Exception:
                pass
    if not any(i.get("ok") for i in infos.values()):
        return etat, {"erreur": "pas assez de bougies pour faire tourner le robot"}, []
    clos = ponderer(list(registre.values()), ouverts)
    # alertes : entrées et sorties, une seule fois chacune
    alertes = []
    mot = lambda t: ("achat" if t["sens"] > 0 else "vente")
    titre = lambda t: f"Robot · {MARCHES[t['marche']]['nom']} · {STRATS[t['strat']]['nom']}"
    for t in ouverts:
        cle = "in|" + t["id"]
        if cle not in vus:
            vus[cle] = maintenant.isoformat()
            alertes.append((f"robot|{cle}", titre(t),
                            f"{mot(t).capitalize()} papier à {fpx(t['entree'])}, stop {fpx(t['stop'])}"
                            + (f", objectif {fpx(t['objectif'])}" if t.get("objectif") else "")
                            + (f". Risque engagé : {fr(t['poids'] * RISQUE_PCT, 2)} %." if t["poids"] > 0 else
                               ". Stratégie coupée (elle perd en direct) : suivie sans risque engagé."),
                            3 if t["poids"] > 0 else 2, ["robot"]))
    for t in registre.values():
        cle = "out|" + t["id"]
        if cle not in vus and (maintenant - pd.Timestamp(P.depuis_horloge(t["t_sortie"]))) < timedelta(hours=6):
            vus[cle] = maintenant.isoformat()
            alertes.append((f"robot|{cle}", titre(t), f"{mot(t).capitalize()} papier clôturé ({t['issue']}) : {fr(t['R'], 2, True)} R.",
                            3 if t.get("poids", 0) > 0 else 2, ["robot", "white_check_mark" if t["R"] > 0 else "x"]))
    limite = (maintenant - timedelta(days=30)).isoformat()
    vus = {k: v for k, v in vus.items() if v >= limite}
    etat.update(version=2, debuts=debuts, trades={k: vers_json(v) for k, v in registre.items()}, vus=vus, maj=maintenant.isoformat())
    etat.pop("pannes", None)
    capital = float(compte.get("capital_robot", CAPITAL))

    def compte_de(liste, titre_):
        pond = sorted([dict(t, R=t["R"] * t["poids"]) for t in liste], key=lambda t: t["t_sortie"])
        solde, soldes = capital, []
        for t in pond:                               # 1 % du solde du moment risqué à chaque trade (intérêts composés)
            solde += solde * RISQUE_PCT / 100 * t["R"]
            soldes.append(solde)
        par_m = {m: stats([t for t in pond if t["marche"] == m]) for m in MARCHES}
        par_s = {k: stats([t for t in pond if t["strat"] == k]) for k in STRATS}
        return {"titre": titre_, "stats": stats(pond), "dollars": solde - capital, "solde": solde, "soldes": soldes,
                "par_marche": par_m, "par_strat": par_s, "R": [t["R"] for t in pond],
                "trades": sorted(liste, key=lambda t: t["t_sortie"], reverse=True)}
    cellules = {}
    for m, M in MARCHES.items():
        for k in STRATS:
            cl = [t for t in clos if cle_cellule(t) == (m, k)]
            w, statut = poids(cl, M["plafond"])
            cellules[(m, k)] = {"stats": stats(cl), "poids": w, "statut": statut if infos[m].get("ok") else "hors ligne",
                                "ouvert": next((t for t in ouverts if cle_cellule(t) == (m, k)), None)}
    direct = compte_de(clos, "en direct")
    d0 = min((pd.Timestamp(v) for v in debuts.values()), default=None)
    for t in ouverts:                                # mise et taille de chaque position sur le compte de 500 $
        t["mise"] = direct["solde"] * RISQUE_PCT / 100 * t["poids"]
        t["taille"] = t["mise"] / t["risque"] if t["risque"] > 0 else 0.0
    hm = f"{maintenant:%H:%M}"
    act = [{"t": f"{hm}  PASSAGE DU ROBOT · {maintenant:%d/%m/%Y}", "c": "h", "e": 0},
           {"t": f"{hm}  prix chargés : {sum(1 for i in infos.values() if i.get('ok'))} marchés sur {len(MARCHES)}, "
                 f"{sum(i.get('nb', 0) for i in infos.values()):,} bougies".replace(",", " "), "c": "ok", "e": 1}]
    for m, M in MARCHES.items():
        i = infos[m]
        if not i.get("ok"):
            act.append({"t": f"{hm}  {M['sym']:<9} hors ligne : prix indisponibles, marché ignoré ce passage", "c": "ko"})
            continue
        act.append({"t": f"{hm}  {M['sym']:<9} {fpx(i['px'])}" + (f"  ({fr(i['var'] * 100, 2, True, ' %')} sur 24 h)" if i.get("var") is not None else ""), "c": "m"})
        for k in STRATS:
            c_ = cellules[(m, k)]
            txt, niv = i["diag"].get(k, ("—", "rien"))
            if c_["ouvert"]:
                t = c_["ouvert"]
                act.append({"t": f"         {k:<4} POSITION {'achat' if t['sens'] > 0 else 'vente'} {fpx(t['entree'])} · stop {fpx(t['stop'])} · "
                                 f"latent {fr(t.get('latent'), 2, True)} R · mise {fr(t['mise'], 2)} $", "c": "pos"})
            else:
                act.append({"t": f"         {k:<4} {txt}" + ("  [cellule coupée : suivie sans risque]" if c_["statut"] == "coupée" else ""),
                            "c": {"signal": "sig", "proche": "pr", "rien": "dim"}[niv]})
    act.append({"t": f"{hm}  {len(cellules)} cellules contrôlées", "c": "ok", "e": 2})
    nouveaux = [a_ for a_ in alertes if "|in|" in a_[0]]
    sortis = [a_ for a_ in alertes if "|out|" in a_[0]]
    for a_ in nouveaux + sortis:
        act.append({"t": f"{hm}  >> {a_[1].replace('Robot · ', '')} : {a_[2]}", "c": "sig"})
    act.append({"t": f"{hm}  positions : {len(ouverts)} ouverte(s), {len(attentes)} ordre(s) en attente"
                     + ("" if (nouveaux or sortis) else " · aucune action nécessaire"), "c": "ok", "e": 3})
    act.append({"t": f"{hm}  registre enregistré : {len(registre)} trade(s) clos · solde {fr(direct['solde'], 2)} $", "c": "ok", "e": 4})
    suivi = []
    for m, M in MARCHES.items():
        if not infos[m].get("ok"):
            continue
        suivi.append({"m": m, "sym": M["sym"], "cb": COINBASE.get(m), "px": infos[m]["px"],
                      "pos": [{"k": t["strat"], "s": t["sens"], "e": t["entree"], "st": t["stop"], "o": t.get("objectif"),
                               "r": t["risque"], "f": t.get("frais", 0.0) if t["strat"] not in ("MOM", "SCALP") else 0.0, "mise": t["mise"]}
                              for t in ouverts if t["marche"] == m],
                      "ord": [{"s": a_["sens"], "n": a_["niveau"]} for a_ in attentes if a_["marche"] == m]})
    il_y_a_7j = P.vers_horloge(pd.DatetimeIndex([maintenant]))[0] - pd.Timedelta(days=7)
    sem = [t for t in clos if t["t_sortie"] >= il_y_a_7j]
    vue = {"semaine": {"n": len(sem) + len(ouverts), "r": float(sum(t["R"] * t["poids"] for t in sem))},
           "activite": {"run": maintenant.isoformat(), "lignes": act, "suivi": suivi},
           "debut": P.depuis_horloge(d0) if d0 is not None else maintenant, "cellules": cellules, "marches": infos,
           "ouverts": ouverts, "attentes": attentes, "attente": attentes[0] if attentes else None,
           "direct": direct, "robot": direct["stats"], "dollars": direct["dollars"], "capital": capital,
           "historique": direct["trades"][:80], "pannes": pannes,
           "jours": max(0, (pd.Timestamp(maintenant).tz_localize(None) - pd.Timestamp(P.depuis_horloge(d0)).tz_localize(None)).days) if d0 is not None else 0,
           "rejeu": compte_de(ponderer(simu), f"rejeu {REJEU_JOURS} jours") if (rejeu and simu) else None,
           "strats": {k: {"stats": direct["par_strat"][k]} for k in STRATS}}
    # les scalps (des dizaines par semaine) restent dans la console et le registre, sans alerte téléphone
    return etat, vue, [a_ for a_ in alertes if "SCALP-" not in a_[0]]


# ---------------------------------------------------------------------------
# AFFICHAGE : le centre de contrôle
# ---------------------------------------------------------------------------

import json

COUL_ETAT = {"observation": "obs", "engagée": "eng", "coupée": "off", "hors ligne": "hl"}
LIB_ETAT = {"observation": "Observation", "engagée": "Engagée", "coupée": "Coupée", "hors ligne": "Hors ligne"}


def heure(t):
    return P.depuis_horloge(t).strftime("%d/%m %H:%M") if t is not None else "—"


def mini(vals, w=120, h=30, coul=None, zero=False):
    """Petite courbe sans axes."""
    vals = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
    if len(vals) < 2:
        return f'<svg viewBox="0 0 {w} {h}" class="rx-mini"></svg>'
    mn, mx = min(vals + ([0.0] if zero else [])), max(vals + ([0.0] if zero else []))
    et = (mx - mn) or 1.0
    pts = " ".join(f"{i / (len(vals) - 1) * w:.1f},{2 + (mx - v) / et * (h - 4):.1f}" for i, v in enumerate(vals))
    coul = coul or ("var(--up)" if vals[-1] >= vals[0] else "var(--down)")
    base = f'<line x1="0" x2="{w}" y1="{2 + mx / et * (h - 4):.1f}" y2="{2 + mx / et * (h - 4):.1f}" class="z"/>' if zero else ""
    return (f'<svg viewBox="0 0 {w} {h}" class="rx-mini" preserveAspectRatio="none">{base}'
            f'<polyline points="{pts}" fill="none" stroke="{coul}" stroke-width="1.6" stroke-linejoin="round"/></svg>')


def courbe(cum, w=720, h=210):
    cum = [0.0] + list(cum or [])
    mn, mx = min(cum), max(cum)
    et = (mx - mn) or 1.0
    g, d, ht, b = 46, 10, 12, 20
    x = lambda i: g + i / max(1, len(cum) - 1) * (w - g - d)
    y = lambda v: ht + (mx - v) / et * (h - ht - b)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(cum))
    coul = "var(--up)" if cum[-1] >= 0 else "var(--down)"
    aire = f"{x(0):.1f},{y(0):.1f} {pts} {x(len(cum) - 1):.1f},{y(0):.1f}"
    return (f'<svg viewBox="0 0 {w} {h}" class="rx-svg" role="img" aria-label="Courbe du compte en R">'
            f'<polygon points="{aire}" fill="{coul}" opacity=".10"/>'
            f'<line class="gr" x1="{g}" x2="{w - d}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>'
            f'<text class="ax" x="{g - 6}" y="{y(mx) + 4:.1f}" text-anchor="end">{fr(mx, 1, True)}</text>'
            f'<text class="ax" x="{g - 6}" y="{y(mn) + 4:.1f}" text-anchor="end">{fr(mn, 1, True)}</text>'
            f'<polyline points="{pts}" fill="none" stroke="{coul}" stroke-width="2" stroke-linejoin="round"/>'
            f'<text class="ax" x="{w - d}" y="{h - 5}" text-anchor="end">{len(cum) - 1} trades · {fr(cum[-1], 2, True)} R</text></svg>')


def monte_carlo(R, n_suite=100, tirages=1500, graine=7):
    """Éventail des résultats possibles sur les n_suite prochains trades, en tirant au hasard dans les trades connus."""
    R = np.asarray(R, dtype=float)
    if len(R) < 15:
        return None
    rng = np.random.default_rng(graine)
    tir = np.c_[np.zeros(tirages), rng.choice(R, size=(tirages, n_suite), replace=True).cumsum(axis=1)]
    creux = (tir - np.maximum.accumulate(tir, axis=1)).min(axis=1)
    q = lambda p: np.percentile(tir, p, axis=0)
    return {"n": n_suite, "p5": q(5), "p25": q(25), "p50": q(50), "p75": q(75), "p95": q(95), "ex": tir[:26],
            "fin": tir[:, -1], "perte": float((tir[:, -1] < 0).mean()), "dd_med": float(np.median(creux))}


def svg_eventail(mc, w=640, h=250):
    g, hist, ht, b = 44, 96, 10, 20
    pw = w - g - hist
    mn, mx = float(min(mc["p5"].min(), mc["ex"].min())), float(max(mc["p95"].max(), mc["ex"].max()))
    et = (mx - mn) or 1.0
    x = lambda i: g + i / mc["n"] * pw
    y = lambda v: ht + (mx - v) / et * (h - ht - b)
    ligne = lambda a: " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(a))
    bande = lambda hi, lo: ligne(hi) + " " + " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in reversed(list(enumerate(lo))))
    out = [f'<svg viewBox="0 0 {w} {h}" class="rx-svg" role="img" aria-label="Éventail Monte Carlo des {mc["n"]} prochains trades">',
           f'<polygon points="{bande(mc["p95"], mc["p5"])}" class="b1"/><polygon points="{bande(mc["p75"], mc["p25"])}" class="b2"/>']
    out += [f'<polyline points="{ligne(e)}" class="ex"/>' for e in mc["ex"]]
    out.append(f'<line class="gr" x1="{g}" x2="{g + pw}" y1="{y(0):.1f}" y2="{y(0):.1f}"/><polyline points="{ligne(mc["p50"])}" class="med"/>')
    for v in (mx, 0.0, mn):
        out.append(f'<text class="ax" x="{g - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{fr(v, 0, True)}</text>')
    cpt, bords = np.histogram(mc["fin"], bins=14, range=(mn, mx))
    for k, n in enumerate(cpt):
        y1, y2 = y(bords[k + 1]), y(bords[k])
        lg = n / max(1, cpt.max()) * (hist - 14)
        out.append(f'<rect x="{g + pw + 8}" y="{y1 + 1:.1f}" width="{lg:.1f}" height="{max(1.0, y2 - y1 - 2):.1f}" class="{"hu" if bords[k] >= 0 else "hd"}"/>')
    out.append(f'<text class="ax" x="{g}" y="{h - 5}">trade 1</text><text class="ax" x="{g + pw}" y="{h - 5}" text-anchor="end">trade {mc["n"]}</text></svg>')
    return "".join(out)


def ligne_trade(t, simu=False):
    m = MARCHES[t["marche"]]
    return (f'<li class="{"rx-sim" if simu else ""}"><span class="rx-tag {m["classe"]}">{esc(t["marche"])}</span>'
            f'<span class="rx-lt">{esc(t["strat"])} · {"achat" if t["sens"] > 0 else "vente"} {fpx(t["entree"])}'
            f'<small>{heure(t["t_sortie"] or t["t_entree"])}{" · rejeu" if simu else ""}</small></span>'
            + (f'<b class="{"up" if t["R"] > 0 else "down"}">{fr(t["R"], 2, True)} R</b>' if t.get("R") is not None else
               f'<b class="{"up" if (t.get("latent") or 0) > 0 else "down"}">{fr(t.get("latent"), 2, True)} R<i>ouvert</i></b>') + '</li>')


def carte_accueil(vue):
    """Carte de la Vue d'ensemble : ce que fait le robot maintenant."""
    if not vue or vue.get("erreur"):
        return ""
    R = vue["robot"]
    lig = ""
    for t in vue["ouverts"][:6]:
        lig += (f'<tr><td>{esc(MARCHES[t["marche"]]["sym"])} · {esc(t["strat"])}</td><td class="{"up" if t["sens"] > 0 else "down"}">'
                f'{"Achat" if t["sens"] > 0 else "Vente"} {fpx(t["entree"])}</td>'
                f'<td class="num {"up" if (t.get("latent") or 0) > 0 else "down"}">{fr(t.get("latent"), 2, True)} R</td></tr>')
    for at in vue.get("attentes", [])[:3]:
        lig += (f'<tr><td>{esc(MARCHES[at["marche"]]["sym"])} · ASIE</td><td class="warn">Ordre {"achat" if at["sens"] > 0 else "vente"} '
                f'{fpx(at["niveau"])}</td><td class="num">→ {P.depuis_horloge(at["expire"]):%H:%M}</td></tr>')
    corps = (f'<table class="ps-mk">{lig}</table>' if lig else '<p class="ps-mini">Aucune position ouverte, aucun ordre en attente.</p>')
    res = (f'{R["n"]} trades clos · <b class="{"up" if R["total"] > 0 else "down"}">{fr(R["total"], 2, True)} R</b>'
           if R.get("n") else "aucun trade clos pour l'instant")
    nb = sum(1 for i in vue["marches"].values() if i.get("ok"))
    return (f'<div class="ps-carte rb-carte"><div class="ps-tete"><span class="k">Robot papier · {nb} marchés</span>'
            f'<a href="#analyse" data-aller="robot" class="ps-lien">centre de contrôle</a></div>{corps}'
            f'<p class="ps-mini">Depuis le {vue["debut"]:%d/%m} : {res}. Sans argent réel.</p></div>')


def bloc_robot(vue):
    if not vue or vue.get("erreur"):
        return (f'<section><h2>Robot papier</h2><p class="pourquoi">Indisponible : '
                f'{esc((vue or {}).get("erreur", "pas de données"))}.</p></section>')
    D, J = vue["direct"], vue.get("rejeu")
    R = D["stats"]
    cl = lambda v: "up" if (v or 0) > 0 else ("down" if (v or 0) < 0 else "")
    en_ligne = [m for m, i in vue["marches"].items() if i.get("ok")]
    cel = vue["cellules"]
    nb_eng = sum(1 for c in cel.values() if c["statut"] == "engagée")
    nb_off = sum(1 for c in cel.values() if c["statut"] == "coupée")

    # ---- bandeau défilant
    ev = [f'{MARCHES[t["marche"]]["sym"]} · {t["strat"]} · {"ACHAT" if t["sens"] > 0 else "VENTE"} OUVERT {fpx(t["entree"])} · {fr(t.get("latent"), 2, True)} R'
          for t in vue["ouverts"]]
    ev += [f'{MARCHES[a_["marche"]]["sym"]} · ASIE · ORDRE {"ACHAT" if a_["sens"] > 0 else "VENTE"} À {fpx(a_["niveau"])}' for a_ in vue["attentes"]]
    ev += [f'{MARCHES[t["marche"]]["sym"]} · {t["strat"]} · CLOS {fr(t["R"], 2, True)} R ({t["issue"]})' for t in D["trades"][:8]]
    ev += [f'{len(en_ligne)} MARCHÉS EN LIGNE', f'{len(cel)} CELLULES · {nb_eng} ENGAGÉES · {nb_off} COUPÉES',
           'COMPTE PAPIER : AUCUN ARGENT RÉEL', 'RÈGLES FIXES · REGISTRE FIGÉ']
    defil = "".join(f'<span>{esc(x)}</span>' for x in ev)
    tape = (f'<div class="rx-tape"><b class="rx-live">EN DIRECT</b><div class="rx-defil"><div>{defil}{defil}</div></div></div>')

    # ---- portefeuille
    wallet = (f'<div class="rx-p rx-wallet"><div class="rx-h"><span>Compte papier · en direct</span><i>depuis le {vue["debut"]:%d/%m %H:%M}</i></div>'
              f'<div class="rx-big {cl(vue["dollars"]) or "zero"}" id="rx-solde">{fr(D["solde"], 2)} $</div>'
              f'<p class="rx-dep">Départ {fr(vue["capital"], 0)} $ · <b class="{cl(vue["dollars"])}">{fr(vue["dollars"], 2, True)} $ '
              f'({fr(vue["dollars"] / vue["capital"] * 100, 2, True)} %)</b></p>'
              f'<div class="rx-kv"><div><span>Résultat</span><b class="{cl(R.get("total"))}">{fr(R.get("total", 0.0), 2, True)} R</b></div>'
              f'<div><span>Trades clos</span><b>{R.get("n", 0)}</b></div>'
              f'<div><span>Réussite</span><b>{(str(round(R["reussite"] * 100)) + " %") if R.get("n") else "—"}</b></div>'
              f'<div><span>Positions</span><b>{len(vue["ouverts"])}</b></div>'
              f'<div><span>Trades sur 7 jours</span><b>{vue["semaine"]["n"]}</b></div>'
              f'<div><span>Résultat 7 jours</span><b class="{cl(vue["semaine"]["r"])}">{fr(vue["semaine"]["r"], 2, True)} R</b></div></div>'
              + (f'<div class="rx-rejeu"><div class="rx-h"><span>Rejeu {REJEU_JOURS} jours · simulation</span><i>hors registre</i></div>'
                 f'<div class="rx-big2 {cl(J["dollars"])}">{fr(J["solde"], 2)} $ <small>({fr(J["dollars"], 2, True)} $)</small></div>'
                 f'<p>{J["stats"]["n"]} trades · {fr(J["stats"]["total"], 1, True)} R · réussite {J["stats"]["reussite"] * 100:.0f} % · '
                 f'pire baisse {fr(J["stats"]["dd"], 1, True)} R. Ce que les mêmes règles auraient donné sur le passé récent : '
                 f'un repère, pas un résultat.</p></div>' if J and J["stats"].get("n") else "")
              + f'<p class="rx-note">Compte papier de {fr(vue["capital"], 0)} $ : {fr(RISQUE_PCT, 0)} % du solde au maximum par trade. Aucun argent réel.</p></div>')

    # ---- courbe
    src = D if R.get("n", 0) >= 2 else J
    graph = (f'<div class="rx-p"><div class="rx-h"><span>Courbe du compte · {esc(src["titre"]) if src else ""}</span>'
             f'<i>{"registre en direct" if src is D else "simulation en attendant les premiers trades"}</i></div>'
             + (courbe(src["stats"]["courbe"]) if src and src["stats"].get("n") else '<p class="rx-vide">La courbe apparaît au deuxième trade clos.</p>')
             + '</div>')

    # ---- marchés
    lm = ""
    classement = sorted(MARCHES, key=lambda m: -abs(vue["marches"][m].get("force") or 0) if vue["marches"][m].get("ok") else 1)
    for rang, m in enumerate(classement):
        M = MARCHES[m]
        i = vue["marches"][m]
        f_ = i.get("force")
        barre = (f'<span class="rx-force {cl(f_)}" title="Force de la tendance sur 20 jours : {fr(f_, 2, True)}">'
                 f'<i style="width:{min(100, abs(f_) / 2 * 100):.0f}%"></i></span>' if f_ is not None else "")
        pos = [t for t in vue["ouverts"] if t["marche"] == m]
        etat = (f'<b class="{cl(sum(t.get("latent") or 0 for t in pos))}">{len(pos)} pos.</b>' if pos else
                ('<i>veille</i>' if i.get("ok") else '<i class="down">hors ligne</i>'))
        lm += (f'<tr><td><span class="rx-tag {M["classe"]}">{esc(m)}</span>{esc(M["nom"])}</td>'
               f'<td class="num" id="rx-px-{m}">{fpx(i.get("px")) if i.get("ok") else "—"}</td>'
               f'<td class="num {cl(i.get("var"))}">{fr((i.get("var") or 0) * 100, 2, True, " %") if i.get("var") is not None else "—"}</td>'
               f'<td class="rx-sp">{mini(i.get("spark") or [], 70, 20)}</td><td>{barre}</td><td class="num">{etat}</td></tr>')
    marches = (f'<div class="rx-p"><div class="rx-h"><span>Marchés · les plus prometteurs en tête</span><i>prix · 24 h · tendance 20 j</i></div>'
               f'<div class="rx-scroll rx-mks"><table class="rx-tab rx-mk">{lm}</table></div>'
               f'<p class="rx-note">Classés par force de tendance (barre verte : hausse, rouge : baisse). Le robot lit les {len(MARCHES)} marchés '
               f'à chaque passage. Memecoins (étiquette rose) : risque plafonné à {fr(RISQUE_PCT / 4, 2)} %, très volatils et manipulables.</p></div>')

    # ---- agents (stratégies)
    ag = ""
    for k, d in STRATS.items():
        st_d, st_j = D["par_strat"][k], (J["par_strat"][k] if J else {"n": 0})
        etats = [cel[(m, k)]["statut"] for m in MARCHES]
        pts = "".join(f'<i class="{COUL_ETAT[e]}" title="{esc(m)} : {esc(e)}"></i>' for m, e in zip(MARCHES, etats))
        ag += (f'<div class="rx-agent"><div class="rx-ag-t"><b>{esc(k)}</b><span>{esc(d["nom"])}</span></div>'
               f'{mini(st_j.get("courbe") or [], 150, 34, zero=True)}'
               f'<div class="rx-ag-b"><span class="rx-pts">{pts}</span>'
               f'<span>{("direct " + fr(st_d["total"], 1, True) + " R") if st_d.get("n") else ("rejeu " + fr(st_j["total"], 1, True) + " R" if st_j.get("n") else "en attente")}</span></div></div>')

    # ---- sphère : un disque par cellule (marché × stratégie) et par trade
    disques = []
    for (m, k), c in cel.items():
        disques.append({"k": "c", "e": COUL_ETAT[c["statut"]], "o": 1 if c["ouvert"] else 0})
    for t in D["trades"][:70]:
        disques.append({"k": "t", "e": "up" if t["R"] > 0 else "down", "o": 0})
    if J:
        for t in J["trades"][:max(0, 170 - len(disques))]:
            disques.append({"k": "s", "e": "up" if t["R"] > 0 else "down", "o": 0})
    sphere = (f'<div class="rx-sphere"><div class="rx-h"><span>Réseau du robot</span><i>{len(cel)} cellules · {len(disques) - len(cel)} trades</i></div>'
              f'<canvas id="rx-canvas" aria-label="Sphère animée : un disque par cellule marché × stratégie et par trade"></canvas>'
              f'<script type="application/json" id="rx-data">{json.dumps({"d": disques})}</script>'
              f'<div class="rx-leg"><span><i class="obs"></i>cellule en observation</span><span><i class="eng"></i>engagée</span>'
              f'<span><i class="off"></i>coupée</span><span><i class="up"></i>trade gagnant</span><span><i class="down"></i>trade perdant</span>'
              f'<span><i class="pulse"></i>position ouverte</span></div></div>')

    # ---- derniers ordres
    lst = "".join(ligne_trade(t) for t in vue["ouverts"][:6]) + "".join(ligne_trade(t) for t in D["trades"][:10])
    if J:
        lst += "".join(ligne_trade(t, True) for t in J["trades"][:max(0, 12 - len(vue["ouverts"][:6]) - len(D["trades"][:10]))])
    fills = (f'<div class="rx-p rx-fills"><div class="rx-h"><span>Derniers ordres</span><i>ouverts, clos, puis rejeu</i></div>'
             f'<ul>{lst or "<li class=rx-vide>Aucun ordre pour l’instant.</li>"}</ul></div>')

    # ---- grille marchés × stratégies
    tete = "".join(f'<th>{esc(k)}</th>' for k in STRATS)
    gr = ""
    for m, M in MARCHES.items():
        gr += f'<tr><th scope="row"><span class="rx-tag {M["classe"]}">{esc(m)}</span></th>'
        for k in STRATS:
            c = cel[(m, k)]
            st = c["stats"]
            gr += (f'<td class="rx-c {COUL_ETAT[c["statut"]]}{" rx-o" if c["ouvert"] else ""}" title="{esc(M["nom"])} · {esc(STRATS[k]["nom"])} : {esc(c["statut"])}">'
                   f'<b>{fr(st["total"], 1, True) if st.get("n") else "·"}</b><small>{st.get("n", 0)} tr.</small><small>{fr(c["poids"] * RISQUE_PCT, 2)} %</small></td>')
        gr += '</tr>'
    grille = (f'<div class="rx-p"><div class="rx-h"><span>Grille marchés × stratégies</span><i>résultat en direct (R) · risque engagé</i></div>'
              f'<table class="rx-grille"><thead><tr><th></th>{tete}</tr></thead><tbody>{gr}</tbody></table>'
              f'<div class="rx-leg"><span><i class="obs"></i>observation ({fr(RISQUE_PCT / 4, 2)} %)</span><span><i class="eng"></i>engagée</span>'
              f'<span><i class="off"></i>coupée (0 %)</span><span><i class="hl"></i>hors ligne</span></div></div>')

    # ---- journal
    src_t = [(t, False) for t in D["trades"][:40]] + ([(t, True) for t in J["trades"][:40]] if J else [])
    lj = "".join(
        f'<tr class="{"rx-sim" if sim else ""}"><td>{heure(t["t_sortie"])}</td><td><span class="rx-tag {MARCHES[t["marche"]]["classe"]}">{esc(t["marche"])}</span></td>'
        f'<td>{esc(t["strat"])}</td><td class="{"up" if t["sens"] > 0 else "down"}">{"A" if t["sens"] > 0 else "V"}</td>'
        f'<td class="num">{fpx(t["entree"])}</td><td class="num">{fpx(t["sortie"])}</td>'
        f'<td class="num {cl(t["R"])}">{fr(t["R"], 2, True)}</td><td>{"rejeu" if sim else esc(t["issue"])}</td></tr>' for t, sim in src_t[:60])
    journal = (f'<div class="rx-p"><div class="rx-h"><span>Journal des trades</span><i>{R.get("n", 0)} en direct'
               f'{" · rejeu en gris" if J else ""}</i></div><div class="rx-scroll"><table class="rx-tab rx-jr"><thead><tr><th>Clos le</th>'
               f'<th>Marché</th><th>Strat.</th><th></th><th>Entrée</th><th>Sortie</th><th>R</th><th>Issue</th></tr></thead>'
               f'<tbody>{lj or "<tr><td colspan=8 class=rx-vide>Pas encore de trade clos.</td></tr>"}</tbody></table></div></div>')

    # ---- Monte Carlo
    base = D if R.get("n", 0) >= 15 else J
    mc = monte_carlo(base["R"]) if base else None
    if mc:
        fan = (f'<div class="rx-p"><div class="rx-h"><span>Éventail Monte Carlo · {mc["n"]} prochains trades</span>'
               f'<i>tiré des trades {esc(base["titre"])}</i></div>{svg_eventail(mc)}'
               f'<div class="rx-kv rx-kv4"><div><span>Médiane</span><b class="{cl(mc["p50"][-1])}">{fr(float(mc["p50"][-1]), 1, True)} R</b></div>'
               f'<div><span>9 cas sur 10</span><b>{fr(float(mc["p5"][-1]), 0, True)} à {fr(float(mc["p95"][-1]), 0, True)}</b></div>'
               f'<div><span>Finit en perte</span><b>{mc["perte"] * 100:.0f} %</b></div>'
               f'<div><span>Creux médian</span><b class="down">{fr(mc["dd_med"], 1, True)} R</b></div></div></div>')
    else:
        fan = ('<div class="rx-p"><div class="rx-h"><span>Éventail Monte Carlo</span></div>'
               '<p class="rx-vide">Disponible à partir de 15 trades.</p></div>')

    act = dict(vue.get("activite") or {}, ajust=vue.get("ajust_or", 0.0), solde=D["solde"])
    etapes = [f"Charger les prix des {len(MARCHES)} marchés", f"Contrôler les {len(cel)} cellules", "Gérer positions et ordres", "Enregistrer le registre",
              "Surveiller les prix en direct"]
    travail = (f'<div class="rx-r0"><div class="rx-p rx-term"><div class="rx-h"><span>Le robot au travail</span>'
               f'<i id="rx-run">dernier passage : {vue["activite"]["lignes"][0]["t"][:5] if vue.get("activite") else "—"}</i></div>'
               f'<div class="rx-console" id="rx-console" role="log" aria-live="off" aria-label="Journal d\'activité du robot"></div>'
               f'<script type="application/json" id="rx-act">{json.dumps(act, ensure_ascii=False).replace("</", "<\\/")}</script></div>'
               f'<div class="rx-p rx-taches"><div class="rx-h"><span>Tâches du passage</span><i>toutes les 15 min</i></div>'
               f'<ol id="rx-taches">{"".join(f"<li><i></i>{esc(e)}</li>" for e in etapes)}</ol>'
               f'<div class="rx-cd"><span>Prochain passage dans</span><b id="rx-cd">--:--</b><div class="rx-barre"><i id="rx-cd-b"></i></div></div>'
               f'<div class="rx-h rx-h2"><span>En direct maintenant</span><i id="rx-live-age">connexion…</i></div>'
               f'<ul id="rx-live" class="rx-livel"><li class="rx-vide">En attente du premier prix en direct.</li></ul></div></div>')
    centre = (f'<div class="rx" id="rx">{tape}{travail}<div class="rx-r1">{wallet}{graph}{marches}</div>'
              f'<div class="rx-r2"><div class="rx-agents"><div class="rx-h"><span>Stratégies</span><i>courbe du rejeu · état par marché</i></div>{ag}</div>'
              f'{sphere}{fills}</div><div class="rx-r3">{grille}{journal}{fan}</div></div>')

    # ---- détail sous le centre de contrôle
    lig = ""
    for t in vue["ouverts"]:
        lig += (f'<tr><td>{esc(MARCHES[t["marche"]]["sym"])}</td><td>{esc(STRATS[t["strat"]]["nom"])}</td>'
                f'<td class="{"up" if t["sens"] > 0 else "down"}">{"Achat" if t["sens"] > 0 else "Vente"}</td>'
                f'<td>{heure(t["t_entree"])}</td><td class="num">{fpx(t["entree"])}</td><td class="num">{fpx(t["stop"])}</td>'
                f'<td class="num">{fpx(t.get("objectif"))}</td><td class="num">{fr(t.get("mise"), 2)} $</td>'
                f'<td class="num">{fpx(t.get("taille"))}</td>'
                f'<td class="num {cl(t.get("latent"))}">{fr(t.get("latent"), 2, True)} R</td></tr>')
    for at in vue["attentes"]:
        lig += (f'<tr class="rb-att"><td>{esc(MARCHES[at["marche"]]["sym"])}</td><td>{esc(STRATS["ASIE"]["nom"])}</td>'
                f'<td>Ordre {"achat" if at["sens"] > 0 else "vente"}</td><td>jusqu\'à {P.depuis_horloge(at["expire"]):%H:%M}</td>'
                f'<td class="num">{fpx(at["niveau"])}</td><td class="num">{fpx(at["stop"])}</td><td class="num">{fpx(at["objectif"])}</td><td></td><td></td><td>en attente</td></tr>')
    regles = "".join(f'<div class="rb-strat"><div class="rb-st-tete"><b>{esc(k)} · {esc(d["nom"])}</b></div><p>{esc(d["court"])}</p>'
                     f'<p class="ps-mini">{esc(d["ut"])} · {esc(d["horizon"])}</p></div>' for k, d in STRATS.items())
    pannes = "".join(f'<li>{esc(k)} : {esc(v)}</li>' for k, v in vue.get("pannes", {}).items())
    return (centre
            + '<section><h2>Positions et ordres en attente</h2>'
            + (f'<div class="tbl"><table class="rb-tab"><thead><tr><th>Marché</th><th>Stratégie</th><th>Sens</th><th>Entrée le</th><th>Entrée</th>'
               f'<th>Stop</th><th>Objectif</th><th>Mise risquée</th><th>Taille (unités)</th><th>Latent</th></tr></thead><tbody>{lig}</tbody></table></div>'
               if lig else '<p class="vide">Aucune position ouverte et aucun ordre en attente.</p>') + '</section>'
            + f'<section><h2>Comment le robot décide</h2><p class="pourquoi">Cinq stratégies aux règles écrites à l\'avance, dont un scalp M15, sur {len(MARCHES)} marchés (l\'or, 8 cryptos majeures, 6 memecoins) : '
              f'{len(cel)} cellules. Chaque cellule commence en observation (un quart du risque plein, {fr(RISQUE_PCT / 4, 2)} %). Après {OBSERVATION} trades clos, '
              f'le robot lui donne plus de risque si elle gagne en direct, et la coupe si elle perd. Les memecoins restent plafonnés à {fr(RISQUE_PCT / 4, 2)} %. '
              f'Frais comptés : 0,40 $ par aller-retour sur l\'or, 0,10 % sur BTC et ETH, 0,30 % sur les memecoins, plus le financement de nuit. '
              f'Le rejeu applique les mêmes règles aux {REJEU_JOURS} derniers jours : c\'est une simulation, elle ne compte pas dans le registre.</p>'
              f'<div class="rb-strats">{regles}</div>'
              + (f'<ul class="rb-panne">{pannes}</ul>' if pannes else "") + '</section>'
            + '<section><h2>Ce que ce robot n\'est pas</h2><p class="pourquoi">Il ne passe aucun ordre réel et ne garantit aucun gain. '
              'Moins de 50 trades en direct, le résultat dépend surtout du hasard. Avec 500 $ réels, la taille minimale d\'un broker (0,01 lot d\'or) dépasserait souvent la mise calculée ici : seul le compte papier peut prendre des tailles aussi petites. Le brancher sur un compte MetaTrader 5 ne se fera que si '
              'tu le décides, après des résultats en direct positifs, avec des garde-fous (taille maximale, arrêt si les données sont en retard, '
              'coupure d\'urgence). Les memecoins ne sont presque jamais proposés par les brokers MT5.</p></section>')


CSS = """
/* Robot : centre de contrôle */
.rx{--rx:#57c7ff;--rxp:#0a0f1a;--rxl:#1b2a44;display:flex;flex-direction:column;gap:10px;margin-bottom:28px}
.rx-p,.rx-sphere,.rx-agents{background:linear-gradient(180deg,#0c1322,#070b14);border:1px solid var(--rxl);border-radius:10px;padding:12px 14px;min-width:0}
.rx-h{display:flex;justify-content:space-between;align-items:baseline;gap:10px;margin-bottom:8px}
.rx-h span{font:700 10.5px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--rx)}
.rx-h i{font:500 11px var(--text);font-style:normal;color:var(--mute);text-align:right}
.rx-tape{display:flex;align-items:center;gap:10px;border:1px solid var(--rxl);border-radius:8px;background:#060a12;padding:6px 10px;overflow:hidden}
.rx-live{flex:none;font:700 10.5px var(--mono);letter-spacing:.1em;color:#fff;background:#d6303a;border-radius:4px;padding:3px 8px}
.rx-defil{flex:1;overflow:hidden;white-space:nowrap;mask-image:linear-gradient(90deg,transparent,#000 4%,#000 96%,transparent)}
.rx-defil>div{display:inline-block;animation:rx-defil 60s linear infinite}
.rx-defil span{font:500 11.5px var(--mono);color:#9fb4d4;margin-right:28px} .rx-defil span::before{content:"◆ ";color:var(--rx)}
@keyframes rx-defil{to{transform:translateX(-50%)}}
.rx-r0{display:grid;grid-template-columns:minmax(0,2.1fr) minmax(0,1fr);gap:10px}
.rx-console{height:380px;overflow-y:auto;background:#03060c;border:1px solid #131d30;border-radius:8px;padding:10px 12px;font:500 12.5px/1.55 var(--mono);color:#9fb4d4;white-space:pre-wrap;word-break:break-word}
.rx-console .l.h{color:var(--rx);font-weight:700;margin-top:4px} .rx-console .l.ok{color:#2fd27d} .rx-console .l.m{color:#fff;font-weight:600;margin-top:4px}
.rx-console .l.dim{color:#5f6f8a} .rx-console .l.pr{color:#ffb340} .rx-console .l.sig{color:#fff;background:rgba(47,210,125,.16)} .rx-console .l.pos{color:#57c7ff}
.rx-console .l.ko{color:var(--down)} .rx-console .l.v{color:#c9d6ea} .rx-console .l.al{color:#000;background:#ffb340;font-weight:700}
.rx-console .l:last-child::after{content:"▌";color:var(--rx);animation:rx-clign 1s steps(1) infinite;margin-left:2px}
@keyframes rx-clign{50%{opacity:0}}
.rx-taches ol{list-style:none;margin:0 0 10px;padding:0;display:flex;flex-direction:column;gap:7px;font-size:13px}
.rx-taches li{display:flex;align-items:center;gap:9px;color:var(--mute)}
.rx-taches li i{flex:none;width:16px;height:16px;border-radius:50%;border:2px solid #2b3c5c}
.rx-taches li.fait{color:var(--ink)} .rx-taches li.fait i{background:var(--up);border-color:var(--up);box-shadow:inset 0 0 0 3px #070b14}
.rx-taches li.cours{color:#fff;font-weight:600} .rx-taches li.cours i{border-color:var(--rx);border-top-color:transparent;animation:rx-tour .8s linear infinite}
@keyframes rx-tour{to{transform:rotate(360deg)}}
.rx-cd{border-top:1px solid var(--rxl);border-bottom:1px solid var(--rxl);padding:9px 0;margin-bottom:10px;display:grid;grid-template-columns:1fr auto;gap:4px 8px;align-items:baseline}
.rx-cd span{font-size:12px;color:var(--mute)} .rx-cd b{font:700 20px var(--mono);color:var(--rx)}
.rx-barre{grid-column:1/-1;height:4px;border-radius:2px;background:#131d30;overflow:hidden} .rx-barre i{display:block;height:100%;width:0;background:var(--rx);transition:width 1s linear}
.rx-h2{margin-bottom:6px}
.rx-livel{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:5px;font:500 12px var(--mono)}
.rx-livel li{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:8px;align-items:baseline}
.rx-livel b{color:var(--ink)} .rx-livel small{color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rx-dep{margin:4px 0 0;font:500 13px var(--mono);color:var(--mute)} .rx-big2 small{font-size:13px;color:var(--mute)}
.rx-flash{animation:rx-flash .8s} @keyframes rx-flash{from{background:rgba(87,199,255,.35)}}
.rx-r1{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.5fr) minmax(0,1.15fr);gap:10px}
.rx-r2{display:grid;grid-template-columns:minmax(0,.8fr) minmax(0,1.7fr) minmax(0,1fr);gap:10px}
.rx-r3{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.2fr) minmax(0,1.3fr);gap:10px}
.rx-big{font:700 44px/1.05 var(--mono);letter-spacing:-.03em;color:var(--up);text-shadow:0 0 22px rgba(47,210,125,.35)}
.rx-big.down{color:var(--down);text-shadow:0 0 22px rgba(255,90,95,.35)} .rx-big.zero{color:var(--ink);text-shadow:none}
.rx-big2{font:700 24px var(--mono);letter-spacing:-.02em} .rx-big2.up{color:var(--up)} .rx-big2.down{color:var(--down)}
.rx-kv{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:10px}
.rx-kv4{grid-template-columns:repeat(4,minmax(0,1fr))}
.rx-kv div{display:flex;flex-direction:column;gap:1px;min-width:0} .rx-kv span{font-size:11px;color:var(--mute)}
.rx-kv b{font:600 15px var(--mono)}
.rx-rejeu{margin-top:12px;padding-top:10px;border-top:1px dashed var(--rxl)} .rx-rejeu p,.rx-note{margin:4px 0 0;font-size:12px;color:var(--mute);line-height:1.45}
.rx-rejeu .rx-h span{color:var(--amber)}
.rx-svg{display:block;width:100%;height:auto} .rx-svg .gr{stroke:#2b3c5c} .rx-svg .ax{fill:var(--mute);font:500 11px var(--mono)}
.rx-svg .b1{fill:rgba(87,199,255,.10)} .rx-svg .b2{fill:rgba(87,199,255,.18)} .rx-svg .ex{fill:none;stroke:rgba(159,180,212,.22);stroke-width:1}
.rx-svg .med{fill:none;stroke:#fff;stroke-width:2} .rx-svg .hu{fill:var(--up)} .rx-svg .hd{fill:var(--down)}
.rx-mini{display:block;width:100%;height:30px} .rx-mini .z{stroke:#2b3c5c}
.rx-tab{width:100%;border-collapse:collapse;font-size:12.5px}
.rx-tab td,.rx-tab th{padding:6px 6px;border-bottom:1px solid #131d30;white-space:nowrap;text-align:left}
.rx-tab th{font:600 10px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.rx-tab td.num{text-align:right;font-family:var(--mono)} .rx-tab i{font-style:normal;color:var(--mute)}
.rx-mks{max-height:300px} .rx-force{display:block;width:54px;height:6px;border-radius:3px;background:#131d30;overflow:hidden}
.rx-force i{display:block;height:100%;background:#5b6170} .rx-force.up i{background:var(--up)} .rx-force.down i{background:var(--down)}
.rx-sp{width:70px} .rx-sp .rx-mini{height:22px}
.rx-tag{display:inline-block;font:700 10px var(--mono);letter-spacing:.04em;border-radius:4px;padding:1px 5px;margin-right:7px;border:1px solid}
.rx-tag.or{color:#ffb340;border-color:rgba(255,179,64,.5)} .rx-tag.crypto{color:var(--rx);border-color:rgba(87,199,255,.5)}
.rx-tag.meme{color:#ff7ad9;border-color:rgba(255,122,217,.5)}
.rx-agents{display:flex;flex-direction:column;gap:8px}
.rx-agent{border:1px solid var(--rxl);border-radius:8px;padding:8px 10px;background:#070c16}
.rx-ag-t{display:flex;align-items:baseline;gap:8px} .rx-ag-t b{font:700 13px var(--mono);color:var(--rx)} .rx-ag-t span{font-size:12px;color:var(--mute);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.rx-ag-b{display:flex;justify-content:space-between;align-items:center;gap:8px;font:500 11.5px var(--mono);color:var(--mute)}
.rx-pts{display:inline-flex;gap:3px} .rx-pts i,.rx-leg i{display:inline-block;width:8px;height:8px;border-radius:50%}
.rx i.obs{background:#ffb340} .rx i.eng{background:var(--up)} .rx i.off{background:#5b6170} .rx i.hl{background:#2a2f3a}
.rx i.up{background:var(--up)} .rx i.down{background:var(--down)} .rx i.pulse{background:#fff;box-shadow:0 0 0 3px rgba(255,255,255,.25)}
.rx-sphere{display:flex;flex-direction:column;background:radial-gradient(ellipse at 50% 45%,#0f1b33 0%,#070b14 70%)}
#rx-canvas{display:block;width:100%;height:460px}
.rx-leg{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:11.5px;color:var(--mute);margin-top:8px} .rx-leg span{display:inline-flex;align-items:center;gap:6px}
.rx-fills ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px;max-height:470px;overflow-y:auto}
.rx-fills li{display:grid;grid-template-columns:auto minmax(0,1fr) auto;align-items:center;gap:4px;border:1px solid var(--rxl);border-radius:7px;padding:6px 8px;background:#070c16}
.rx-lt{font:500 12.5px var(--mono);min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap} .rx-lt small{display:block;font-size:11px;color:var(--mute)}
.rx-fills b{font:700 13px var(--mono);text-align:right} .rx-fills b i{display:block;font:500 10px var(--text);font-style:normal;color:var(--mute)}
.rx-sim{opacity:.55}
.rx-grille{width:100%;border-collapse:separate;border-spacing:4px;table-layout:fixed}
.rx-grille th{font:700 10.5px var(--mono);color:var(--mute);text-align:center;padding:2px} .rx-grille tbody th{text-align:left;width:62px}
.rx-c{border-radius:7px;padding:7px 4px;text-align:center;border:1px solid var(--rxl);background:#0a1120}
.rx-c b{display:block;font:700 14px var(--mono)} .rx-c small{display:block;font:500 10px var(--mono);color:var(--mute);white-space:nowrap;overflow:hidden}
.rx-c.obs{border-color:rgba(255,179,64,.45)} .rx-c.eng{border-color:rgba(47,210,125,.7);background:rgba(47,210,125,.10)}
.rx-c.off{opacity:.5} .rx-c.hl{opacity:.3} .rx-c.rx-o{box-shadow:0 0 0 2px rgba(255,255,255,.55) inset}
.rx-scroll{max-height:330px;overflow:auto}
.rx-vide{color:var(--mute);font-size:13px;padding:14px 4px}
.rb-tab{width:100%;border-collapse:collapse;font-size:13.5px}
.rb-tab th{text-align:left;font:600 10.5px var(--text);letter-spacing:.07em;text-transform:uppercase;color:var(--mute);padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.rb-tab td{padding:8px 10px;border-bottom:1px solid var(--band2);white-space:nowrap}
.rb-tab td.num{font-family:var(--mono);text-align:right} .rb-tab tr.rb-att td{color:var(--amber)}
.rb-strats{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.rb-strat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:6px;min-width:0}
.rb-strat p{margin:0;font-size:13.5px;line-height:1.45}
.rb-panne{color:var(--down);font-size:12.5px}
.rb-carte td:nth-child(2){white-space:nowrap}
@media (max-width:1250px){.rx-r1,.rx-r3{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.rx-r1>:first-child{grid-column:1/-1}.rx-r3>:last-child{grid-column:1/-1}
  .rx-r2{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}.rx-sphere{grid-column:1/-1;order:-1}}
@media (max-width:1000px){.rx-r0{grid-template-columns:minmax(0,1fr)}}
@media (max-width:760px){.rx-console{height:320px;font-size:11.5px}.rx-r1,.rx-r2,.rx-r3{grid-template-columns:minmax(0,1fr)}.rx-r1>*,.rx-r2>*,.rx-r3>*{grid-column:auto}
  #rx-canvas{height:340px}.rx-big{font-size:36px}.rx-kv4{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (prefers-reduced-motion:reduce){.rx-defil>div{animation:none}}
"""

JS = r"""
(function(){
/* Sphère du robot : un disque par cellule (marché × stratégie) et par trade. Elle lit ses données dans la page. */
var pts = [], brut = null, t0 = performance.now();
var calme = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
var COUL = {obs: '#ffb340', eng: '#2fd27d', off: '#5b6170', hl: '#2a2f3a', up: '#2fd27d', down: '#ff5a5f'};
function lire(){
  var el = document.getElementById('rx-data'); if (!el || el.textContent === brut) return;
  brut = el.textContent; var D; try { D = JSON.parse(brut); } catch(e){ return; }
  var n = D.d.length;
  pts = D.d.map(function(d, i){ var y = 1 - 2 * (i + 0.5) / n, r = Math.sqrt(1 - y * y), a = i * 2.399963;
    return {x: Math.cos(a) * r, y: y, z: Math.sin(a) * r, d: d, i: i}; });
}
function dessiner(now){
  requestAnimationFrame(dessiner);
  var cv = document.getElementById('rx-canvas'); if (!cv || !cv.offsetParent) return;
  lire(); if (!pts.length) return;
  var dpr = Math.min(2, window.devicePixelRatio || 1), w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)){ cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
  var c = cv.getContext('2d'); c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, w, h);
  var R = Math.min(w, h) * 0.42, cx = w / 2, cy = h / 2, t = (now - t0) / 1000;
  var a = calme ? 0.7 : t / 9, ca = Math.cos(a), sa = Math.sin(a), cb = Math.cos(0.38), sb = Math.sin(0.38);
  var P = pts.map(function(p){ var x = p.x * ca + p.z * sa, z = -p.x * sa + p.z * ca, y = p.y * cb - z * sb; z = p.y * sb + z * cb;
    return {x: x, y: y, z: z, d: p.d, i: p.i}; });
  var par = {}; P.forEach(function(p){ par[p.i] = p; });
  var g = c.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 1.25);
  g.addColorStop(0, 'rgba(87,199,255,.10)'); g.addColorStop(1, 'rgba(87,199,255,0)'); c.fillStyle = g; c.fillRect(0, 0, w, h);
  var r0 = Math.max(7, Math.min(30, R * 1.75 / Math.sqrt(pts.length)));
  function disque(p){
    var av = p.z > 0, px = cx + p.x * R, py = cy + p.y * R, k = Math.max(0.14, Math.abs(p.z)), rot = Math.atan2(p.y, p.x);
    var coul = COUL[p.d.e] || '#888', sim = p.d.k === 's', cel = p.d.k === 'c';
    c.globalAlpha = (av ? 1 : 0.16) * (sim ? 0.6 : 1);
    c.beginPath(); c.ellipse(px, py, r0 * k, r0, rot, 0, 6.2832); c.fillStyle = cel ? '#56617a' : '#3c4658'; c.fill();
    c.beginPath(); c.ellipse(px, py, r0 * k * 0.68, r0 * 0.68, rot, 0, 6.2832); c.fillStyle = cel ? '#d8dee9' : '#8d97a8'; c.fill();
    c.beginPath(); c.ellipse(px, py, r0 * k * 0.42, r0 * 0.42, rot, 0, 6.2832); c.fillStyle = cel ? '#c9a24a' : '#566072'; c.fill();
    c.beginPath(); c.ellipse(px, py, r0 * k * 0.2, r0 * 0.2, rot, 0, 6.2832); c.fillStyle = coul; c.fill();
    if (p.d.o && av){
      var pu = calme ? 0.5 : (t * 1.2) % 1;
      c.globalAlpha = 1 - pu; c.strokeStyle = '#fff'; c.lineWidth = 2;
      c.beginPath(); c.ellipse(px, py, r0 * k * (1 + pu * 0.9), r0 * (1 + pu * 0.9), rot, 0, 6.2832); c.stroke();
      c.globalAlpha = 0.55; c.lineWidth = 1.2; c.beginPath(); c.moveTo(cx, cy); c.lineTo(px, py); c.stroke();
    }
    c.globalAlpha = 1;
  }
  P.filter(function(p){ return p.z <= 0; }).sort(function(u, v){ return u.z - v.z; }).forEach(disque);
  /* signaux : quelques liaisons qui s'allument entre disques voisins */
  var n = pts.length, pas = Math.floor(t / 0.9);
  c.lineWidth = 1.2;
  for (var m = 0; m < 7 && !calme; m++){
    var i = (pas * 7 + m * 13) % n, j = (i + 5 + (m * 3) % 8) % n, A = par[i], B = par[j];
    if (!A || !B || A.z <= 0.05 || B.z <= 0.05) continue;
    var f = 1 - ((t / 0.9) % 1);
    c.globalAlpha = 0.55 * f; c.strokeStyle = COUL[A.d.e] || '#57c7ff';
    c.beginPath(); c.moveTo(cx + A.x * R, cy + A.y * R); c.lineTo(cx + B.x * R, cy + B.y * R); c.stroke();
    c.globalAlpha = f; c.fillStyle = '#fff'; c.beginPath(); c.arc(cx + B.x * R, cy + B.y * R, 2.4, 0, 6.2832); c.fill();
  }
  c.globalAlpha = 1;
  P.filter(function(p){ return p.z > 0; }).sort(function(u, v){ return u.z - v.z; }).forEach(disque);
}
requestAnimationFrame(dessiner);
})();
"""

JS += r"""
(function(){
/* Le robot au travail : la console rejoue les tâches du dernier passage, puis suit les prix en direct. */
var T = window.terminalOr || {}, A = null, brut = null, E = {i: 0, fin: false, live: {}, vus: {}};
var calme = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
function nb(x, d){ return Number(x).toLocaleString('fr-FR', {minimumFractionDigits: d, maximumFractionDigits: d}); }
function px(x){ var a = Math.abs(x), d = a >= 100 ? 2 : (a >= 1 ? 3 : Math.min(10, 3 - Math.floor(Math.log10(a || 1e-12)))); return nb(x, d); }
function sg(x, d){ return (x > 0 ? '+' : (x < 0 ? '−' : '')) + nb(Math.abs(x), d); }
function hms(){ var d = new Date(), p = function(n){ return ('0' + n).slice(-2); }; return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds()); }
function lire(){
  var el = document.getElementById('rx-act'); if (!el) return null;
  if (el.textContent !== brut){ brut = el.textContent; try { A = JSON.parse(brut); } catch(e){ A = null; } }
  return A;
}
function ligne(txt, cls){
  var c = document.getElementById('rx-console'); if (!c) return;
  var bas = c.scrollHeight - c.scrollTop - c.clientHeight < 40;
  var d = document.createElement('div'); d.className = 'l ' + (cls || ''); d.textContent = txt; c.appendChild(d);
  while (c.children.length > 260) c.removeChild(c.firstChild);
  if (bas) c.scrollTop = c.scrollHeight;
}
function taches(n){
  document.querySelectorAll('#rx-taches li').forEach(function(li, k){ li.className = k < n ? 'fait' : (k === n ? 'cours' : ''); });
}
function rejouer(){
  var c = document.getElementById('rx-console'); if (!c || !c.offsetParent || !lire()) return;
  if (c.getAttribute('data-run') !== A.run){ c.setAttribute('data-run', A.run); c.innerHTML = ''; E.i = 0; E.fin = false; taches(0); }
  if (E.i < A.lignes.length){
    var n = calme ? A.lignes.length : 1;
    while (n-- > 0 && E.i < A.lignes.length){ var l = A.lignes[E.i++]; ligne(l.t, l.c); if (l.e != null) taches(l.e); }
  } else if (!E.fin){ E.fin = true; taches(4); ligne(hms() + '  SURVEILLANCE EN DIRECT · prix toutes les 20 s', 'h'); suivre(true); }
}
setInterval(rejouer, 110);

/* Compte à rebours : le robot passe à chaque quart d'heure */
function rebours(){
  var el = document.getElementById('rx-cd'); if (!el) return;
  var now = Date.now(), q = 900000, reste = q - (now % q), m = Math.floor(reste / 60000), s = Math.floor(reste % 60000 / 1000);
  el.textContent = ('0' + m).slice(-2) + ':' + ('0' + s).slice(-2);
  var b = document.getElementById('rx-cd-b'); if (b) b.style.width = (100 - reste / q * 100).toFixed(1) + '%';
}
setInterval(rebours, 1000); rebours();

/* Prix en direct : l'or par le terminal, les cryptos par Coinbase */
function prixOr(){ var p = T.px && T.px(); return (p && p.live && p.prix > 0 && A) ? p.prix - (A.ajust || 0) : null; }
function chercher(){
  if (!lire() || !window.fetch) return;
  A.suivi.forEach(function(S){
    if (S.m === 'XAU'){ var v = prixOr(); if (v) E.live.XAU = {px: v, t: Date.now()}; return; }
    if (!S.cb) return;
    fetch('https://api.coinbase.com/v2/prices/' + S.cb + '/spot', {cache: 'no-store'})
      .then(function(r){ if (!r.ok) throw 0; return r.json(); })
      .then(function(j){ var v = Number(j && j.data && j.data.amount); if (v > 0 && Math.abs(v / S.px - 1) < 0.5) E.live[S.m] = {px: v, t: Date.now()}; })
      .catch(function(){});
  });
}
var tour = 0;
function suivre(force){
  if (!lire()) return;
  var ul = document.getElementById('rx-live'), h = '', frais = 0, veille = [], cons = E.fin && document.getElementById('rx-console');
  A.suivi.forEach(function(S){
    var L = E.live[S.m]; if (!L || Date.now() - L.t > 90000) return;
    frais++;
    var var_ = (L.px / S.px - 1) * 100, td = document.getElementById('rx-px-' + S.m);
    if (td && td.textContent !== px(L.px)){ td.textContent = px(L.px); td.classList.remove('rx-flash'); void td.offsetWidth; td.classList.add('rx-flash'); }
    var detail = 'veille';
    S.pos.forEach(function(p){
      var R = ((L.px - p.e) * p.s - p.f) / p.r, dst = (L.px - p.st) * p.s / L.px * 100, cle = S.m + p.k;
      detail = p.k + ' ' + sg(R, 2) + ' R (' + sg(R * p.mise, 2) + ' $)';
      if (!cons) return;
      if (dst <= 0){ if (!E.vus[cle + 's']){ E.vus[cle + 's'] = 1; ligne(hms() + '  ' + S.sym + ' ' + p.k + ' : STOP TOUCHÉ EN DIRECT à ' + px(L.px) + ' · clôture au prochain passage', 'al'); } }
      else if (p.o && (L.px - p.o) * p.s >= 0){ if (!E.vus[cle + 'o']){ E.vus[cle + 'o'] = 1; ligne(hms() + '  ' + S.sym + ' ' + p.k + ' : OBJECTIF TOUCHÉ EN DIRECT à ' + px(L.px) + ' · clôture au prochain passage', 'al'); } }
      else ligne(hms() + '  ' + S.sym + ' ' + px(L.px) + ' · ' + p.k + ' latent ' + sg(R, 2) + ' R (' + sg(R * p.mise, 2) + ' $) · stop à ' + nb(dst, 2) + ' %', 'pos');
    });
    S.ord.forEach(function(o){
      var d = (o.n - L.px) * o.s, cle = S.m + 'ord';
      detail = 'ordre à ' + px(o.n) + ' (' + nb(Math.abs(d) / L.px * 100, 2) + ' %)';
      if (!cons) return;
      if (d <= 0){ if (!E.vus[cle]){ E.vus[cle] = 1; ligne(hms() + '  ' + S.sym + ' ASIE : NIVEAU DE L’ORDRE FRANCHI EN DIRECT à ' + px(L.px) + ' · entrée au prochain passage', 'al'); } }
      else ligne(hms() + '  ' + S.sym + ' ' + px(L.px) + ' · ordre ASIE à ' + px(o.n) + ' : encore ' + nb(d / L.px * 100, 2) + ' %', 'pr');
    });
    if (!S.pos.length && !S.ord.length) veille.push(S.m + ' ' + px(L.px) + ' (' + sg(var_, 2) + ' %)');
    h += '<li><b>' + S.m + '</b><small>' + detail + '</small><span class="' + (var_ > 0 ? 'up' : (var_ < 0 ? 'down' : '')) + '">' + px(L.px) + '</span></li>';
  });
  if (ul && h) ul.innerHTML = h;
  var ag = document.getElementById('rx-live-age'); if (ag) ag.textContent = frais ? frais + ' marché' + (frais > 1 ? 's' : '') + ' en direct · ' + hms() : 'prix en direct indisponibles';
  if (cons && veille.length && (force || tour % 3 === 0)) ligne(hms() + '  veille · ' + veille.join(' · ') + '  (depuis le passage)', 'v');
  tour++;
}
chercher(); setInterval(chercher, 20000); setInterval(function(){ suivre(false); }, 20000); setTimeout(function(){ suivre(false); }, 2500);
})();
"""
