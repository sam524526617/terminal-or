#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BACKTEST DU PLAN : or (XAU/USD), règles de Samuel
==================================================

Le plan testé, à la hausse (la baisse est le miroir) :
  1. Annonces   : aucune entrée pendant la fenêtre des grosses annonces US (8 h - 9 h à New York, approximation).
  2. Fondamental: on n'achète pas si le bloc Fed / dollar / taux réels est baissier (neutre accepté).
  3. Tendance   : Daily et H4 haussiers (dernière cassure de structure), H1 pas baissier. Weekly : bonus de note.
  4. Zone       : le prix revient dans un OB ou au 50 % d'un FVG haussier tracé en M30, H1 ou H4.
  5. Déclencheur: cassure de structure M15 haussière qui laisse un FVG M15. Ordre limite au retour dans ce FVG.
  Stop sous l'OB d'entrée. 50 % fermé à 2R puis stop au point d'entrée, le reste sur la prochaine liquidité H1.
  Risque 1 % par trade, 2 trades maximum par jour, arrêt de la journée à -2R. Une seule position à la fois.

Note : A = Weekly dans le sens du trade ET (zone H4 ou divergence RSI M15). B sinon. Un RSI M15 extrême
(au-dessus de 70 pour un achat, sous 30 pour une vente) fait perdre un cran. Le plan ne prend que A et B.

Données : bougies 1 minute Dukascopy (prix BID), écart achat-vente compté à 0,30 $. Taux et dollar : FRED.

Utilisation :
  python backtest/backtest_plan.py --debut 2019-01-01 --sortie resultats
  python backtest/backtest_plan.py --demo          -> données synthétiques, pour vérifier que tout tourne
"""

import argparse
import concurrent.futures as cf
import io
import lzma
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ---------------------------------------------------------------------------
# PARAMÈTRES DU PLAN
# ---------------------------------------------------------------------------

SPREAD = 0.30            # écart achat-vente, en $
K_SWING = {"W": 2, "D": 2, "H4": 2, "H1": 2, "M15": 2}   # bougies de chaque côté pour valider un sommet ou un creux
FVG_MIN_ATR = 0.10       # taille minimale d'un FVG de zone, en ATR de son unité de temps
ZONE_VIE_M15 = 480       # une zone reste valable 5 jours (480 bougies M15)
ARME_M15 = 24            # après le contact avec la zone, le déclencheur a 6 h pour arriver
FENETRE_DECL = 8         # la cassure M15 doit suivre le contact de 2 h au plus
ORDRE_VIE_MIN = 120      # l'ordre limite est annulé s'il n'est pas exécuté en 2 h
TENUE_MAX_J = 3          # position fermée au bout de 3 jours, et toujours avant la clôture du vendredi
STOP_MARGE_ATR = 0.10    # marge sous l'OB, en ATR M15
R_MIN_ATR, R_MAX_ATR = 0.25, 4.0   # stops trop serrés ou trop larges ignorés (en ATR M15)
TP1_R = 2.0
TP2_DEFAUT_R = 3.0       # si aucune liquidité H1 n'est trouvée
TP2_MAX_R = 8.0
MAX_TRADES_JOUR = 2
PERTE_MAX_JOUR_R = -2.0
RSI_HAUT, RSI_BAS = 70, 30

DECALAGE = pd.Timedelta(hours=7)   # horloge interne : heure de New York + 7 h, la journée de trading commence à 0 h
URL_DUKA = "https://datafeed.dukascopy.com/datafeed/XAUUSD/{y}/{m:02d}/{d:02d}/BID_candles_min_1.bi5"
URL_FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}"
UA = {"User-Agent": "Mozilla/5.0 (backtest terminal-or)"}

SESSIONS = [("Asie (0 h - 8 h)", 0, 8), ("Londres (8 h - 14 h 30)", 8, 14.5),
            ("New York (14 h 30 - 22 h)", 14.5, 22), ("Soir (22 h - 24 h)", 22, 24)]


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------
# DONNÉES
# ---------------------------------------------------------------------------

def telecharger_jour(j, dossier):
    f = dossier / f"{j:%Y%m%d}.bi5"
    if f.exists():
        return f
    url = URL_DUKA.format(y=j.year, m=j.month - 1, d=j.day)   # mois numérotés à partir de 0 chez Dukascopy
    for essai in range(4):
        try:
            r = requests.get(url, timeout=40, headers=UA)
            if r.status_code == 404:
                if j < date.today() - timedelta(days=3):
                    f.write_bytes(b"")
                return f if f.exists() else None
            r.raise_for_status()
            if j < date.today() - timedelta(days=3):
                f.write_bytes(r.content)
                return f
            tmp = dossier / f"recent_{j:%Y%m%d}.bi5"
            tmp.write_bytes(r.content)
            return tmp
        except Exception:
            time.sleep(2 * (essai + 1))
    return None


def lire_jour(f, j):
    brut = f.read_bytes()
    if not brut:
        return None
    try:
        data = lzma.decompress(brut)
    except lzma.LZMAError:
        return None
    n = len(data) // 24
    if n == 0:
        return None
    ent = np.frombuffer(data[:n * 24], dtype=">i4").reshape(n, 6)
    vol = np.frombuffer(data[:n * 24], dtype=">f4").reshape(n, 6)[:, 5]
    t = ent[:, 0].astype(np.int64)
    base = pd.Timestamp(j).value // 10**9
    return t, ent[:, 1:5].astype(np.float64), vol.astype(np.float64), base


def charger_dukascopy(debut, fin, dossier):
    dossier.mkdir(parents=True, exist_ok=True)
    jours = [debut + timedelta(days=i) for i in range((fin - debut).days + 1)]
    jours = [j for j in jours if j.weekday() != 5]   # le samedi, l'or ne cote pas
    log(f"Téléchargement de {len(jours)} jours de bougies 1 minute (Dukascopy)...")
    fichiers, rates = {}, 0
    with cf.ThreadPoolExecutor(8) as ex:
        for j, f in zip(jours, ex.map(lambda x: telecharger_jour(x, dossier), jours)):
            if f is None:
                rates += 1
            else:
                fichiers[j] = f
    log(f"  {len(fichiers)} fichiers, {rates} échecs de téléchargement")
    blocs = [b for b in (lire_jour(f, j) for j, f in sorted(fichiers.items())) if b is not None]
    if not blocs:
        raise SystemExit("Aucune donnée Dukascopy lisible.")
    tmax = max(int(b[0].max()) for b in blocs)
    unite = 1000 if tmax > 86400 else 1     # secondes ou millisecondes depuis minuit UTC
    ts = np.concatenate([b[3] + b[0] // unite for b in blocs])
    px = np.concatenate([b[1] for b in blocs])
    vol = np.concatenate([b[2] for b in blocs])
    med = np.median(px[:, 0])
    div = next((d for d in (1000, 100, 10, 1, 10000) if 300 <= med / d <= 10000), 1000)
    px = px / div
    # ordre des champs : (open, close, low, high) ou (open, high, low, close) ; on garde le plus cohérent
    ordres = {"ocLH": (0, 3, 2, 1), "oHLc": (0, 1, 2, 3)}
    def coherence(o):
        op, hi, lo, cl = (px[:, k] for k in o)
        return np.mean((lo <= np.minimum(op, cl) + 1e-9) & (hi >= np.maximum(op, cl) - 1e-9))
    nom, ordre = max(ordres.items(), key=lambda kv: coherence(kv[1]))
    log(f"  prix divisés par {div}, ordre des champs {nom} (cohérence {coherence(ordre):.1%})")
    df = pd.DataFrame({"open": px[:, ordre[0]], "high": px[:, ordre[1]], "low": px[:, ordre[2]],
                       "close": px[:, ordre[3]], "vol": vol},
                      index=pd.to_datetime(ts, unit="s", utc=True))
    if (df["vol"] > 0).mean() > 0.2:
        df = df[df["vol"] > 0]          # minutes sans échange (marché fermé) retirées
    df = df[~df.index.duplicated()].sort_index()
    log(f"  {len(df):,} bougies 1 minute de {df.index[0]:%Y-%m-%d} à {df.index[-1]:%Y-%m-%d}")
    return df


def donnees_demo(jours=120, graine=7):
    rng = np.random.default_rng(graine)
    idx = pd.date_range("2025-01-06", periods=jours * 1440, freq="1min", tz="UTC")
    ny = idx.tz_convert("America/New_York")
    ouvert = ~((ny.weekday == 5) | ((ny.weekday == 4) & (ny.hour >= 17)) | ((ny.weekday == 6) & (ny.hour < 18)))
    idx = idx[ouvert]
    n = len(idx)
    vol = 0.18 * (1 + 0.8 * np.abs(np.sin(np.arange(n) / 900)))
    tendance = 0.004 * np.sin(np.arange(n) / 20000)
    cl = 2600 + np.cumsum(tendance + vol * rng.standard_t(4, n))
    op = np.r_[cl[0], cl[:-1]]
    hi = np.maximum(op, cl) + np.abs(rng.normal(0, 0.12, n))
    lo = np.minimum(op, cl) - np.abs(rng.normal(0, 0.12, n))
    return pd.DataFrame({"open": op, "high": hi, "low": lo, "close": cl, "vol": 1.0}, index=idx)


def charger_fred(debut):
    series = {"reel": "DFII10", "dollar": "DTWEXBGS", "us2": "DGS2"}
    cols = {}
    for nom, code in series.items():
        try:
            r = requests.get(URL_FRED.format(code=code), timeout=40, headers=UA)
            r.raise_for_status()
            d = pd.read_csv(io.StringIO(r.text))
            d.columns = ["date", "v"]
            d["date"] = pd.to_datetime(d["date"])
            d["v"] = pd.to_numeric(d["v"], errors="coerce")
            cols[nom] = d.dropna().set_index("date")["v"]
        except Exception as e:
            log(f"  FRED {code} indisponible : {e}")
            return None
    f = pd.DataFrame(cols).sort_index().ffill()
    f = f[f.index >= pd.Timestamp(debut) - pd.Timedelta(days=30)]
    vote = lambda s: np.where(s >= 1, -1, np.where(s <= -1, 1, 0))
    v = (vote((f["reel"] - f["reel"].shift(5)) * 100 / 8)
         + vote((f["dollar"] / f["dollar"].shift(5) - 1) * 100 / 0.5)
         + vote((f["us2"] - f["us2"].shift(5)) * 100 / 8))
    bloc = np.where(v >= 2, 1, np.where(v <= -2, -1, 0))
    log(f"  FRED : {len(f)} jours de taux réels, dollar et taux 2 ans")
    return pd.Series(bloc, index=f.index)


# ---------------------------------------------------------------------------
# BOUGIES, STRUCTURE, ZONES
# ---------------------------------------------------------------------------

def horloge(df_utc):
    """Index UTC -> horloge interne (heure de New York + 7 h, sans fuseau)."""
    t = df_utc.index.tz_convert("America/New_York").tz_localize(None) + DECALAGE
    out = df_utc.copy()
    out.index = t
    return out[~out.index.duplicated()].sort_index()


def agreger(m1, regle):
    if regle == "W":
        cle = m1.index.normalize() - pd.to_timedelta(m1.index.weekday, unit="D")
        g = m1.groupby(cle)
        df = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                           "low": g["low"].min(), "close": g["close"].last()})
        df["fin"] = df.index + pd.Timedelta(days=7)
        return df
    g = m1.resample(regle, label="left", closed="left")
    df = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                       "low": g["low"].min(), "close": g["close"].last()}).dropna()
    df["fin"] = df.index + pd.Timedelta(regle)
    return df


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean().to_numpy()


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    hausse = pd.Series(np.clip(d, 0, None)).ewm(alpha=1 / n, adjust=False).mean()
    baisse = pd.Series(np.clip(-d, 0, None)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + hausse / baisse.replace(0, np.nan))).fillna(50).to_numpy()


def structure(df, k):
    """Tendance = sens de la dernière cassure (clôture au-delà du dernier sommet ou creux confirmé)."""
    h, l, c = df["high"].to_numpy(), df["low"].to_numpy(), df["close"].to_numpy()
    n = len(df)
    tend = np.zeros(n, np.int8)
    bos_h, bos_b = np.zeros(n, bool), np.zeros(n, bool)
    sommets, creux = [], []          # (indice de confirmation, indice du point, prix)
    der_h = der_l = None
    t = 0
    for i in range(n):
        j = i - k
        if j >= k:
            if h[j] > h[j - k:j].max() and h[j] >= h[j + 1:i + 1].max():
                der_h = [h[j], False]
                sommets.append((i, j, h[j]))
            if l[j] < l[j - k:j].min() and l[j] <= l[j + 1:i + 1].min():
                der_l = [l[j], False]
                creux.append((i, j, l[j]))
        if der_h is not None and not der_h[1] and c[i] > der_h[0]:
            der_h[1], t, bos_h[i] = True, 1, True
        if der_l is not None and not der_l[1] and c[i] < der_l[0]:
            der_l[1], t, bos_b[i] = True, -1, True
        tend[i] = t
    return {"tend": tend, "bos_h": bos_h, "bos_b": bos_b, "sommets": sommets, "creux": creux}


def etat_a(df_tf, tend, fins):
    pos = np.searchsorted(df_tf["fin"].to_numpy(), fins, side="right") - 1
    return np.where(pos >= 0, tend[np.clip(pos, 0, None)], 0)


def zones(df, rang):
    """FVG et OB de l'unité de temps : (sens, rang, type, bas, haut, niveau de contact, heure de création)."""
    o, h, l, c = (df[x].to_numpy() for x in ("open", "high", "low", "close"))
    a, fin = atr(df), df["fin"].to_numpy()
    out = []
    for j in range(3, len(df)):
        if not np.isfinite(a[j]):
            continue
        if l[j] > h[j - 2] and l[j] - h[j - 2] >= FVG_MIN_ATR * a[j]:
            out.append((1, rang, "FVG", h[j - 2], l[j], (h[j - 2] + l[j]) / 2, fin[j]))
            for m in (j - 1, j - 2, j - 3):
                if c[m] < o[m]:
                    out.append((1, rang, "OB", l[m], o[m], o[m], fin[j]))
                    break
        if h[j] < l[j - 2] and l[j - 2] - h[j] >= FVG_MIN_ATR * a[j]:
            out.append((-1, rang, "FVG", h[j], l[j - 2], (h[j] + l[j - 2]) / 2, fin[j]))
            for m in (j - 1, j - 2, j - 3):
                if c[m] > o[m]:
                    out.append((-1, rang, "OB", o[m], h[m], o[m], fin[j]))
                    break
    return out


def armer(liste, m15):
    """Pour chaque bougie M15 : rang de la zone touchée récemment (0 = aucune, 1 = M30, 2 = H1, 3 = H4)."""
    debut = m15.index.to_numpy()
    lo, hi, cl = (m15[x].to_numpy() for x in ("low", "high", "close"))
    n = len(m15)
    arme = {1: np.zeros(n, np.int8), -1: np.zeros(n, np.int8)}
    for d, rang, typ, bas, haut, niv, cree in liste:
        s = int(np.searchsorted(debut, cree))
        e = min(n, s + ZONE_VIE_M15)
        if s >= e:
            continue
        if d > 0:
            contact, casse = np.flatnonzero(lo[s:e] <= niv), np.flatnonzero(cl[s:e] < bas)
        else:
            contact, casse = np.flatnonzero(hi[s:e] >= niv), np.flatnonzero(cl[s:e] > haut)
        if not len(contact):
            continue
        t0 = contact[0]
        i0 = casse[0] if len(casse) else e - s
        if i0 <= t0:
            continue
        a, b = s + t0, min(s + t0 + ARME_M15, s + i0, n)
        arme[d][a:b] = np.maximum(arme[d][a:b], rang)
    return arme


# ---------------------------------------------------------------------------
# SIGNAUX
# ---------------------------------------------------------------------------

def fenetre_annonce(t):
    """Approximation des grosses annonces US : 8 h - 9 h à New York (CPI, emploi, ventes...), du lundi au vendredi."""
    ny = t - DECALAGE
    m = ny.hour * 60 + ny.minute
    return ny.weekday() < 5 and 8 * 60 <= m < 9 * 60


def liquidite_h1(h1, st_h1, t_sig, d, seuil):
    """Prochain sommet (achat) ou creux (vente) H1 confirmé et pas encore pris, au-delà de seuil."""
    fins = h1["fin"].to_numpy()
    dern = int(np.searchsorted(fins, np.datetime64(t_sig), side="right")) - 1
    if dern < 0:
        return None
    hi, lo = h1["high"].to_numpy(), h1["low"].to_numpy()
    pts = st_h1["sommets"] if d > 0 else st_h1["creux"]
    meilleur = None
    for ic, j, p in reversed(pts):
        if ic > dern:
            continue
        if dern - ic > 240:
            break
        if d > 0:
            pris = j + 1 <= dern and hi[j + 1:dern + 1].max() >= p
            if not pris and p > seuil and (meilleur is None or p < meilleur):
                meilleur = p
        else:
            pris = j + 1 <= dern and lo[j + 1:dern + 1].min() <= p
            if not pris and p < seuil and (meilleur is None or p > meilleur):
                meilleur = p
    return meilleur


def candidats(m1, fond):
    log("Construction des unités de temps...")
    tf = {"M15": agreger(m1, "15min"), "M30": agreger(m1, "30min"), "H1": agreger(m1, "1h"),
          "H4": agreger(m1, "4h"), "D": agreger(m1, "1D"), "W": agreger(m1, "W")}
    m15 = tf["M15"]
    fins = m15["fin"].to_numpy()
    log("Structure de marché par unité de temps...")
    st = {k: structure(tf[k], K_SWING.get(k, 2)) for k in ("W", "D", "H4", "H1", "M15")}
    tend = {k: etat_a(tf[k], st[k]["tend"], fins) for k in ("W", "D", "H4", "H1")}
    log("Zones OB et FVG en M30, H1 et H4...")
    zs = zones(tf["M30"], 1) + zones(tf["H1"], 2) + zones(tf["H4"], 3)
    arme = armer(zs, m15)
    log(f"  {len(zs)} zones")
    o, h, l, c = (m15[x].to_numpy() for x in ("open", "high", "low", "close"))
    a15, r15 = atr(m15), rsi(c)
    s15 = st["M15"]
    creux_conf = np.array([x[0] for x in s15["creux"]])
    sommets_conf = np.array([x[0] for x in s15["sommets"]])
    if fond is not None:
        f_dates = fond.index.to_numpy()
        f_vals = fond.to_numpy()

    def divergence(i, d):
        pts, conf = (s15["creux"], creux_conf) if d > 0 else (s15["sommets"], sommets_conf)
        k = int(np.searchsorted(conf, i, side="right"))
        if k < 2:
            return False
        (_, j1, p1), (_, j2, p2) = pts[k - 2], pts[k - 1]
        if i - j2 > 20:
            return False
        return (p2 < p1 and r15[j2] > r15[j1]) if d > 0 else (p2 > p1 and r15[j2] < r15[j1])

    out = []
    for i in range(30, len(m15)):
        for d in (1, -1):
            if not (s15["bos_h"][i] if d > 0 else s15["bos_b"][i]):
                continue
            rang = int(arme[d][max(0, i - FENETRE_DECL):i + 1].max())
            if rang == 0:
                continue
            j = next((x for x in (i, i - 1, i - 2) if x >= 2 and
                      ((l[x] > h[x - 2]) if d > 0 else (h[x] < l[x - 2]))), None)
            if j is None or not np.isfinite(a15[i]):
                continue
            if d > 0:
                bas_fvg, haut_fvg = h[j - 2], l[j]
                m = next((x for x in range(j - 1, max(j - 6, -1), -1) if c[x] < o[x]), None)
                ob = l[m] if m is not None else l[max(0, j - 4):j + 1].min()
                stop = ob - STOP_MARGE_ATR * a15[i]
                entrees = {"ce": (bas_fvg + haut_fvg) / 2, "bord": haut_fvg}
            else:
                bas_fvg, haut_fvg = h[j], l[j - 2]
                m = next((x for x in range(j - 1, max(j - 6, -1), -1) if c[x] > o[x]), None)
                ob = h[m] if m is not None else h[max(0, j - 4):j + 1].max()
                stop = ob + STOP_MARGE_ATR * a15[i]
                entrees = {"ce": (bas_fvg + haut_fvg) / 2, "bord": bas_fvg}
            t_sig = pd.Timestamp(fins[i])
            jour = t_sig.normalize()
            fv = 0
            if fond is not None:
                p = int(np.searchsorted(f_dates, np.datetime64(jour - pd.Timedelta(days=1)), side="right")) - 1
                fv = int(f_vals[p]) if p >= 0 else 0
            div = divergence(i, d)
            note = "A" if (tend["W"][i] == d and (rang == 3 or div)) else "B"
            extreme = (r15[i] > RSI_HAUT) if d > 0 else (r15[i] < RSI_BAS)
            if extreme:
                note = {"A": "B", "B": "C"}[note]
            base = {"t_sig": t_sig, "sens": d, "rang_zone": rang, "W": int(tend["W"][i]), "D": int(tend["D"][i]),
                    "H4": int(tend["H4"][i]), "H1": int(tend["H1"][i]), "fond": fv, "divergence": div,
                    "rsi": round(float(r15[i]), 1), "note": note, "atr15": float(a15[i])}
            for nom, e in entrees.items():
                risque = abs(e - stop)
                if (e - stop) * d <= 0 or not (R_MIN_ATR * a15[i] <= risque <= R_MAX_ATR * a15[i]):
                    continue
                tp1 = e + d * TP1_R * risque
                liq = liquidite_h1(tf["H1"], st["H1"], t_sig, d, tp1)
                if liq is None or abs(liq - e) > TP2_MAX_R * risque:
                    tp2 = e + d * TP2_DEFAUT_R * risque
                else:
                    tp2 = liq
                out.append(dict(base, entree_type=nom, entree=e, stop=stop, tp1=tp1, tp2=tp2, risque=risque))
    log(f"  {len(out)} signaux candidats (avant filtres)")
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# SIMULATION MINUTE PAR MINUTE
# ---------------------------------------------------------------------------

def simuler(cand, m1):
    T = m1.index.to_numpy().astype("datetime64[ns]").astype(np.int64)
    H, L, C = (m1[x].to_numpy() for x in ("high", "low", "close"))
    N = len(T)
    MIN, JOUR = 60 * 10**9, 86400 * 10**9
    res = []
    for r in cand.itertuples(index=False):
        d, e, sl, tp1, tp2 = r.sens, r.entree, r.stop, r.tp1, r.tp2
        t0 = pd.Timestamp(r.t_sig).value
        i = int(np.searchsorted(T, t0))
        lim = t0 + ORDRE_VIE_MIN * MIN
        rempli = None
        while i < N and T[i] < lim:
            if d > 0:
                if H[i] >= tp1:
                    break
                if L[i] + SPREAD <= e:
                    rempli = i
                    break
            else:
                if L[i] + SPREAD <= tp1:
                    break
                if H[i] >= e:
                    rempli = i
                    break
            i += 1
        if rempli is None:
            res.append((None, None, None, "non exécuté"))
            continue
        t_f = pd.Timestamp(T[rempli])
        if fenetre_annonce(t_f):
            res.append((None, None, None, "annonce"))
            continue
        lundi = t_f.normalize() - pd.Timedelta(days=t_f.weekday())
        fin_max = min(T[rempli] + TENUE_MAX_J * JOUR,
                      (lundi + pd.Timedelta(days=4, hours=23, minutes=45)).value)
        pos, pnl, stop_c, tp1_ok, issue, t_x = 1.0, 0.0, sl, False, None, None
        for k in range(rempli, N):
            if T[k] >= fin_max:
                sortie = C[k] if d > 0 else C[k] + SPREAD
                pnl += pos * (sortie - e) * d
                issue, t_x = "temps", T[k]
                break
            bas, haut = (L[k], H[k]) if d > 0 else (L[k] + SPREAD, H[k] + SPREAD)
            touche_stop = (bas <= stop_c) if d > 0 else (haut >= stop_c)
            if touche_stop:
                pnl += pos * (stop_c - e) * d
                issue = "stop" if not tp1_ok else "TP1 puis BE"
                t_x = T[k]
                break
            if not tp1_ok and ((haut >= tp1) if d > 0 else (bas <= tp1)):
                pnl += 0.5 * (tp1 - e) * d
                pos, tp1_ok, stop_c = 0.5, True, e
            if tp1_ok and ((haut >= tp2) if d > 0 else (bas <= tp2)):
                pnl += pos * (tp2 - e) * d
                issue, t_x = "TP2", T[k]
                break
        if issue is None:
            pnl += pos * ((C[-1] if d > 0 else C[-1] + SPREAD) - e) * d
            issue, t_x = "fin des données", T[-1]
        res.append((t_f, pd.Timestamp(t_x), pnl / r.risque, issue))
    cand = cand.copy()
    cand["t_entree"] = [x[0] for x in res]
    cand["t_sortie"] = [x[1] for x in res]
    cand["R"] = [x[2] for x in res]
    cand["issue"] = [x[3] for x in res]
    return cand


# ---------------------------------------------------------------------------
# RÈGLES DE COMPTE ET STATISTIQUES
# ---------------------------------------------------------------------------

def portefeuille(tr):
    tr = tr.dropna(subset=["R"]).sort_values("t_entree")
    garde, occupe, jours = [], None, {}
    for idx, r in tr.iterrows():
        if occupe is not None and r.t_entree < occupe:
            continue
        j = r.t_entree.normalize()
        nb, clos = jours.setdefault(j, [0, []])
        realise = sum(x for t, x in clos if t <= r.t_entree)
        if nb >= MAX_TRADES_JOUR or realise <= PERTE_MAX_JOUR_R:
            continue
        jours[j][0] += 1
        clos.append((r.t_sortie, r.R))
        garde.append(idx)
        occupe = r.t_sortie
    return tr.loc[garde]


def heure_paris(t):
    idx = pd.DatetimeIndex(t) - DECALAGE
    p = idx.tz_localize("America/New_York", ambiguous="NaT", nonexistent="shift_forward").tz_convert("Europe/Paris")
    return p.hour + p.minute / 60


def stats(tr):
    R = tr["R"].to_numpy(dtype=float)
    if len(R) == 0:
        return None
    cum = np.cumsum(R)
    dd = float((cum - np.maximum.accumulate(np.r_[0, cum])[1:]).min())
    serie = pire = 0
    for x in R:
        serie = serie + 1 if x < 0 else 0
        pire = max(pire, serie)
    gains, pertes = R[R > 0].sum(), -R[R < 0].sum()
    mois = max(1.0, (tr["t_entree"].max() - tr["t_entree"].min()).days / 30.44)
    return {"n": len(R), "reussite": (R > 0).mean() * 100, "nul": (np.abs(R) < 1e-9).mean() * 100,
            "esp": R.mean(), "total": cum[-1], "pf": gains / pertes if pertes else float("inf"),
            "dd": dd, "pire": pire, "par_mois": len(R) / mois}


def ligne(nom, s):
    if s is None:
        return f"| {nom} | 0 | — | — | — | — | — | — | — |"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"| {nom} | {s['n']} | {s['reussite']:.0f} % | {s['esp']:+.2f} R | {s['total']:+.1f} R | {pf} | "
            f"{s['dd']:.1f} R | {s['pire']} | {s['par_mois']:.1f} |")


ENTETE = ("| | Trades | Réussite | Gain moyen | Total | Profit factor | Pire baisse | Pire série de pertes | Trades/mois |\n"
          "|---|---|---|---|---|---|---|---|---|")


def rapport(cand, periode, avec_fond, sortie):
    ok = cand["R"].notna()
    plan = (cand["note"].isin(["A", "B"]) & (cand["D"] == cand["sens"]) & (cand["H4"] == cand["sens"])
            & (cand["H1"] != -cand["sens"]) & (cand["fond"] != -cand["sens"]))
    variantes = [
        ("**Plan complet** (entrée au 50 % du FVG)", plan & (cand["entree_type"] == "ce")),
        ("Entrée au bord du FVG", plan & (cand["entree_type"] == "bord")),
        ("Note A seulement", plan & (cand["entree_type"] == "ce") & (cand["note"] == "A")),
        ("Sans filtre fondamental", (plan | (cand["fond"] == -cand["sens"])) & (cand["note"].isin(["A", "B"]))
         & (cand["D"] == cand["sens"]) & (cand["H4"] == cand["sens"]) & (cand["H1"] != -cand["sens"])
         & (cand["entree_type"] == "ce")),
        ("Sans condition H1", cand["note"].isin(["A", "B"]) & (cand["D"] == cand["sens"]) & (cand["H4"] == cand["sens"])
         & (cand["fond"] != -cand["sens"]) & (cand["entree_type"] == "ce")),
        ("Sans filtre de tendance (D, H4, H1)", cand["note"].isin(["A", "B"]) & (cand["fond"] != -cand["sens"])
         & (cand["entree_type"] == "ce")),
    ]
    paris_tout = pd.Series(np.nan, index=cand.index)
    paris_tout[ok] = heure_paris(cand.loc[ok, "t_entree"]).to_numpy()
    cand["heure_paris"] = paris_tout
    sans_asie = plan & (cand["entree_type"] == "ce") & (cand["heure_paris"] >= 8)
    variantes.append(("Plan sans la session asiatique", sans_asie))

    pris = {nom: portefeuille(cand[masque & ok]) for nom, masque in variantes}
    base = pris[variantes[0][0]]
    L = [f"# Backtest du plan : or (XAU/USD)", "",
         f"Période : **{periode}**. Données 1 minute Dukascopy, écart achat-vente de {SPREAD:.2f} $ compté.",
         f"Règles de compte appliquées : une position à la fois, {MAX_TRADES_JOUR} trades par jour au plus, "
         f"arrêt de la journée à {PERTE_MAX_JOUR_R:.0f} R. Avec 1 % de risque par trade, 1 R = 1 % du compte.", ""]
    if not avec_fond:
        L += ["> ⚠️ Données FRED indisponibles : le filtre fondamental n'a pas été appliqué.", ""]
    L += ["## Résultat du plan et des variantes", "", ENTETE]
    L += [ligne(nom, stats(t)) for nom, t in pris.items()]
    s = stats(base)
    if s:
        L += ["", "## Lecture rapide", ""]
        verdict = ("**avantage positif** sur la période" if s["esp"] > 0.1 and s["pf"] > 1.2 else
                   ("**pas d'avantage net** : le résultat est proche de zéro" if s["esp"] > -0.05 else
                    "**avantage négatif** : en l'état, le plan perd de l'argent"))
        L += [f"- Le plan complet montre un {verdict} ({s['esp']:+.2f} R par trade en moyenne, sur {s['n']} trades).",
              f"- Pire série de pertes : **{s['pire']} trades** d'affilée, soit environ **−{s['pire']} %** du compte "
              f"à 1 % de risque. Pire baisse depuis un sommet : **{s['dd']:.1f} %**.",
              f"- Environ **{s['par_mois']:.1f} trades par mois**."]
        if s["n"] < 100:
            L.append("- ⚠️ Moins de 100 trades : les chiffres sont fragiles, une poignée de trades peut tout changer.")

    def tableau(titre, groupes):
        L.extend(["", f"## {titre}", "", ENTETE])
        L.extend(ligne(nom, stats(t)) for nom, t in groupes)

    b = base.copy()
    b["annee"] = b["t_entree"].dt.year
    tableau("Plan complet, par année", [(str(a), g) for a, g in b.groupby("annee")])
    tableau("Plan complet, par session (heure de Paris de l'entrée)",
            [(nom, b[(b["heure_paris"] >= h0) & (b["heure_paris"] < h1)]) for nom, h0, h1 in SESSIONS])
    tableau("Plan complet, par sens", [("Achats", b[b["sens"] == 1]), ("Ventes", b[b["sens"] == -1])])
    tableau("Plan complet, par note", [("A", b[b["note"] == "A"]), ("B", b[b["note"] == "B"])])
    tableau("Plan complet, par zone d'origine",
            [(n, b[b["rang_zone"] == r]) for r, n in ((1, "M30"), (2, "H1"), (3, "H4"))])
    tableau("Plan complet, par alignement du Weekly",
            [("Weekly dans le sens du trade", b[b["W"] == b["sens"]]), ("Weekly contre ou neutre", b[b["W"] != b["sens"]])])
    tableau("Plan complet, avec ou sans divergence RSI",
            [("Avec divergence", b[b["divergence"]]), ("Sans divergence", b[~b["divergence"]])])
    if len(b):
        iss = b["issue"].value_counts()
        L += ["", "## Comment les trades du plan se terminent", "", "| Issue | Trades | Part |", "|---|---|---|"]
        L += [f"| {k} | {v} | {v / len(b) * 100:.0f} % |" for k, v in iss.items()]
    nb_non = (cand[plan & (cand["entree_type"] == "ce")]["issue"] == "non exécuté").sum()
    L += ["", "## Limites de ce test", "",
          "- Les annonces sont approximées par la fenêtre 8 h - 9 h de New York ; les réunions de la Fed (20 h à Paris) "
          "ne sont pas bloquées.",
          "- Le fondamental testé est le bloc Fed / dollar / taux réels (FRED). COT, ETF et liquidité ne sont pas rejoués.",
          "- Les supports et résistances à 3 contacts ne sont pas encore codés : seules les zones OB et FVG sont testées.",
          "- Quand une même minute touche le stop et l'objectif, le test compte le stop (hypothèse prudente).",
          f"- {nb_non} signaux du plan n'ont jamais été exécutés (le prix n'est pas revenu dans le FVG en 2 h).",
          "- Un backtest décrit le passé. Il sert à éliminer ce qui ne marche pas, pas à garantir l'avenir."]
    txt = "\n".join(L) + "\n"
    sortie.mkdir(parents=True, exist_ok=True)
    (sortie / "rapport.md").write_text(txt, encoding="utf-8")
    cand.to_csv(sortie / "signaux.csv", index=False)
    base.to_csv(sortie / "trades_plan.csv", index=False)
    return txt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debut", default="2019-01-01")
    ap.add_argument("--fin", default=None)
    ap.add_argument("--sortie", default="resultats")
    ap.add_argument("--donnees", default="backtest/.donnees")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    debut = date.fromisoformat(a.debut)
    fin = date.fromisoformat(a.fin) if a.fin else date.today() - timedelta(days=1)
    if a.demo:
        m1u, fond = donnees_demo(), None
    else:
        m1u = charger_dukascopy(debut, fin, Path(a.donnees))
        log("Taux et dollar (FRED)...")
        fond = charger_fred(debut)
    m1 = horloge(m1u)
    periode = f"{m1u.index[0]:%d/%m/%Y} au {m1u.index[-1]:%d/%m/%Y}"
    cand = candidats(m1, fond)
    if cand.empty:
        raise SystemExit("Aucun signal candidat : vérifier les données.")
    log("Simulation minute par minute...")
    t = time.time()
    cand = simuler(cand, m1)
    log(f"  terminé en {time.time() - t:.0f} s")
    print(rapport(cand, periode + (" (DÉMO, données synthétiques)" if a.demo else ""), fond is not None,
                  Path(a.sortie)))


if __name__ == "__main__":
    sys.exit(main())
