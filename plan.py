# -*- coding: utf-8 -*-
"""
RÈGLES DU PLAN DE TRADING (or, XAU/USD) : un seul endroit pour le backtest et pour le terminal en direct
=======================================================================================================

Le plan, à la hausse (la baisse est le miroir) :
  1. Annonces    : aucune entrée 30 min avant ou après une annonce USD à fort impact.
  2. Fondamental : on n'achète pas si le bloc Fed / dollar / taux réels est baissier (neutre accepté).
  3. Tendance    : Daily et H4 haussiers (sens de la dernière cassure de structure), H1 pas baissier.
  4. Zone        : le prix revient dans un OB, au 50 % d'un FVG (M30, H1, H4) ou sur un support à 3 contacts (H1, H4).
  5. Déclencheur : cassure de structure M15 haussière qui laisse un FVG M15 ; ordre limite au 50 % de ce FVG.
  Stop sous l'OB d'entrée. 50 % fermé à 2R puis stop au point d'entrée, le reste sur la prochaine liquidité H1.
  Note A : Weekly dans le sens du trade ET (zone H4 ou divergence RSI M15). B sinon. RSI M15 extrême : un cran de moins.

Toutes les heures sont sur une horloge interne : heure de New York + 7 h, pour que la journée de trading
(qui commence à 17 h à New York) commence à minuit et que les bougies H4 et Daily tombent comme chez les brokers.
"""

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# PARAMÈTRES
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
NEWS_MIN = 30            # minutes bloquées avant et après une annonce USD forte
RISQUE_PCT = 1.0         # risque par trade, en % du compte
SR_CONTACTS = 3          # un support / une résistance doit avoir été touché au moins 3 fois (règle α1)
SR_TOL_ATR = 0.25        # deux points hauts ou bas à moins de 0,25 ATR l'un de l'autre = même niveau
SR_FENETRE = 240         # contacts comptés sur les 240 dernières bougies de l'unité de temps (10 jours en H1)

DECALAGE = pd.Timedelta(hours=7)
RANGS = {1: "M30", 2: "H1", 3: "H4"}


# ---------------------------------------------------------------------------
# BOUGIES
# ---------------------------------------------------------------------------

def vers_horloge(idx):
    """Index avec fuseau -> horloge interne (heure de New York + 7 h, sans fuseau)."""
    idx = pd.DatetimeIndex(idx)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    return idx.tz_convert("America/New_York").tz_localize(None) + DECALAGE


def depuis_horloge(t, fuseau="Europe/Paris"):
    """Instant de l'horloge interne -> heure locale (par défaut Paris)."""
    ts = pd.Timestamp(t) - DECALAGE
    try:
        return ts.tz_localize("America/New_York", ambiguous=True, nonexistent="shift_forward").tz_convert(fuseau)
    except Exception:
        return ts.tz_localize("UTC").tz_convert(fuseau)


def sur_horloge(df):
    out = df[["open", "high", "low", "close"]].copy()
    out.index = vers_horloge(df.index)
    out = out[~out.index.duplicated()].sort_index()
    return out.dropna()


def agreger(df, regle):
    """Regroupe des bougies plus fines (index sur l'horloge interne) en bougies `regle` ; ajoute leur heure de fin."""
    if regle == "W":
        cle = df.index.normalize() - pd.to_timedelta(df.index.weekday, unit="D")
        g = df.groupby(cle)
        out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                            "low": g["low"].min(), "close": g["close"].last()})
        out["fin"] = out.index + pd.Timedelta(days=7)
        return out
    g = df.resample(regle, label="left", closed="left")
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last()}).dropna()
    out["fin"] = out.index + pd.Timedelta(regle)
    return out


def atr(df, n=14):
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean().to_numpy()


def rsi(c, n=14):
    c = np.asarray(c, dtype=float)
    d = np.diff(c, prepend=c[0])
    hausse = pd.Series(np.clip(d, 0, None)).ewm(alpha=1 / n, adjust=False).mean()
    baisse = pd.Series(np.clip(-d, 0, None)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + hausse / baisse.replace(0, np.nan))).fillna(50).to_numpy()


# ---------------------------------------------------------------------------
# STRUCTURE ET ZONES
# ---------------------------------------------------------------------------

def structure(df, k):
    """Tendance = sens de la dernière cassure (clôture au-delà du dernier sommet ou creux confirmé).
    Renvoie aussi le dernier sommet et le dernier creux confirmés, cassés ou non (niveaux à surveiller)."""
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
    return {"tend": tend, "bos_h": bos_h, "bos_b": bos_b, "sommets": sommets, "creux": creux,
            "der_h": der_h, "der_l": der_l}


def etat_a(df_tf, tend, fins):
    """Tendance de l'unité de temps connue à chaque instant `fins` (seules les bougies terminées comptent)."""
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


def supports_resistances(df, st_, rang):
    """Supports et résistances à 3 contacts ou plus (règle α1), à partir des sommets et creux confirmés.
    Un niveau naît quand le 3e contact est confirmé : support si le prix est au-dessus, résistance s'il est en dessous.
    Même format que zones() : (sens, rang, "S/R", bas, haut, niveau de contact, heure de création)."""
    if len(df) < 20:
        return []
    a, fin, c = atr(df), df["fin"].to_numpy(), df["close"].to_numpy()
    points = sorted([(ic, j, p) for ic, j, p in st_["sommets"]] + [(ic, j, p) for ic, j, p in st_["creux"]])
    out, groupes = [], []       # groupe : [prix moyen, liste des (j, prix), déjà émis]
    for ic, j, p in points:
        if not np.isfinite(a[ic]):
            continue
        tol = SR_TOL_ATR * a[ic]
        groupes = [g for g in groupes if j - g[1][-1][0] <= SR_FENETRE]
        g = min((g for g in groupes if abs(g[0] - p) <= tol), key=lambda g: abs(g[0] - p), default=None)
        if g is None:
            groupes.append([p, [(j, p)], False])
            continue
        g[1].append((j, p))
        g[0] = float(np.mean([x[1] for x in g[1]]))
        if len(g[1]) >= SR_CONTACTS and not g[2]:
            g[2] = True
            niv, bas, haut = g[0], g[0] - tol, g[0] + tol
            if c[ic] > haut:
                out.append((1, rang, "S/R", bas, haut, haut, fin[ic]))
            elif c[ic] < bas:
                out.append((-1, rang, "S/R", bas, haut, bas, fin[ic]))
    return out


def armer(liste, m15):
    """Pour chaque bougie M15 : rang de la zone touchée récemment (0 = aucune, 1 = M30, 2 = H1, 3 = H4).
    Renvoie aussi les zones encore intactes et jamais touchées à la dernière bougie (à afficher)."""
    debut = m15.index.to_numpy()
    lo, hi, cl = (m15[x].to_numpy() for x in ("low", "high", "close"))
    n = len(m15)
    arme = {1: np.zeros(n, np.int8), -1: np.zeros(n, np.int8)}
    vierges = []
    for z in liste:
        d, rang, typ, bas, haut, niv, cree = z
        s = int(np.searchsorted(debut, cree))
        e = min(n, s + ZONE_VIE_M15)
        if s >= n:
            continue
        if d > 0:
            contact, casse = np.flatnonzero(lo[s:e] <= niv), np.flatnonzero(cl[s:e] < bas)
        else:
            contact, casse = np.flatnonzero(hi[s:e] >= niv), np.flatnonzero(cl[s:e] > haut)
        if not len(contact):
            if not len(casse) and s + ZONE_VIE_M15 > n:
                vierges.append(z)
            continue
        t0 = contact[0]
        i0 = casse[0] if len(casse) else e - s
        if i0 <= t0:
            continue
        a, b = s + t0, min(s + t0 + ARME_M15, s + i0, n)
        arme[d][a:b] = np.maximum(arme[d][a:b], rang)
    return arme, vierges


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


# ---------------------------------------------------------------------------
# CONTEXTE ET SIGNAUX
# ---------------------------------------------------------------------------

def contexte(tf):
    """tf : bougies par unité de temps ("M15", "M30", "H1", "H4", "D", "W"), avec la colonne "fin"."""
    m15 = tf["M15"]
    fins = m15["fin"].to_numpy()
    st = {k: structure(tf[k], K_SWING.get(k, 2)) for k in ("W", "D", "H4", "H1", "M15")}
    tend = {k: etat_a(tf[k], st[k]["tend"], fins) for k in ("W", "D", "H4", "H1")}
    zs = (zones(tf["M30"], 1) + zones(tf["H1"], 2) + zones(tf["H4"], 3)
          + supports_resistances(tf["H1"], st["H1"], 2) + supports_resistances(tf["H4"], st["H4"], 3))
    arme, vierges = armer(zs, m15)
    s15 = st["M15"]
    ctx = {"tf": tf, "st": st, "tend": tend, "zones": zs, "arme": arme, "vierges": vierges,
           "o": m15["open"].to_numpy(), "h": m15["high"].to_numpy(), "l": m15["low"].to_numpy(),
           "c": m15["close"].to_numpy(), "fins": fins, "a15": atr(m15), "r15": rsi(m15["close"].to_numpy()),
           "creux_conf": np.array([x[0] for x in s15["creux"]]),
           "sommets_conf": np.array([x[0] for x in s15["sommets"]])}
    return ctx


def divergence(ctx, i, d):
    s15 = ctx["st"]["M15"]
    pts, conf = (s15["creux"], ctx["creux_conf"]) if d > 0 else (s15["sommets"], ctx["sommets_conf"])
    k = int(np.searchsorted(conf, i, side="right"))
    if k < 2:
        return False
    (_, j1, p1), (_, j2, p2) = pts[k - 2], pts[k - 1]
    r15 = ctx["r15"]
    if i - j2 > 20:
        return False
    return bool((p2 < p1 and r15[j2] > r15[j1]) if d > 0 else (p2 > p1 and r15[j2] < r15[j1]))


def signal(ctx, i, d, fond=0):
    """Signal du plan à la clôture de la bougie M15 i dans le sens d, ou None.
    Ne regarde que la zone et le déclencheur : les filtres (tendance, fondamental, annonces) sont des colonnes."""
    s15 = ctx["st"]["M15"]
    if not (s15["bos_h"][i] if d > 0 else s15["bos_b"][i]):
        return None
    rang = int(ctx["arme"][d][max(0, i - FENETRE_DECL):i + 1].max())
    if rang == 0:
        return None
    o, h, l, c, a15 = ctx["o"], ctx["h"], ctx["l"], ctx["c"], ctx["a15"]
    j = next((x for x in (i, i - 1, i - 2) if x >= 2 and
              ((l[x] > h[x - 2]) if d > 0 else (h[x] < l[x - 2]))), None)
    if j is None or not np.isfinite(a15[i]):
        return None
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
    tend = ctx["tend"]
    div = divergence(ctx, i, d)
    note = "A" if (tend["W"][i] == d and (rang == 3 or div)) else "B"
    r = float(ctx["r15"][i])
    if (r > RSI_HAUT) if d > 0 else (r < RSI_BAS):
        note = {"A": "B", "B": "C"}[note]
    t_sig = pd.Timestamp(ctx["fins"][i])
    base = {"i": i, "t_sig": t_sig, "sens": d, "rang_zone": rang, "W": int(tend["W"][i]), "D": int(tend["D"][i]),
            "H4": int(tend["H4"][i]), "H1": int(tend["H1"][i]), "fond": int(fond), "divergence": div,
            "rsi": round(r, 1), "note": note, "atr15": float(a15[i]), "fvg": (float(bas_fvg), float(haut_fvg)),
            "ob": float(ob)}
    out = []
    for nom, e in entrees.items():
        risque = abs(e - stop)
        if (e - stop) * d <= 0 or not (R_MIN_ATR * a15[i] <= risque <= R_MAX_ATR * a15[i]):
            continue
        tp1 = e + d * TP1_R * risque
        liq = liquidite_h1(ctx["tf"]["H1"], ctx["st"]["H1"], t_sig, d, tp1)
        tp2_liq = liq is not None and abs(liq - e) <= TP2_MAX_R * risque
        tp2 = liq if tp2_liq else e + d * TP2_DEFAUT_R * risque
        out.append(dict(base, entree_type=nom, entree=float(e), stop=float(stop), tp1=float(tp1), tp2=float(tp2),
                        tp2_liquidite=bool(tp2_liq), risque=float(risque)))
    return out


def filtres_ok(sig):
    """Les filtres du plan qui ne dépendent pas de l'heure (tendance, fondamental, note)."""
    d = sig["sens"]
    return (sig["note"] in ("A", "B") and sig["D"] == d and sig["H4"] == d and sig["H1"] != -d and sig["fond"] != -d)


def bloc_fondamental(reel_pb5, dollar_pct5, us2_pb5):
    """Fed, dollar et taux réels comptés comme UN seul signal (ils bougent ensemble) : +1, 0 ou -1 pour l'or."""
    def vote(x, seuil):
        if x is None or not np.isfinite(x):
            return 0
        return -1 if x >= seuil else (1 if x <= -seuil else 0)
    v = vote(reel_pb5, 8) + vote(dollar_pct5, 0.5) + vote(us2_pb5, 8)
    return 1 if v >= 2 else (-1 if v <= -2 else 0)


# ---------------------------------------------------------------------------
# SIMULATION D'UN ORDRE (backtest minute par minute, ou suivi en direct sur bougies M15)
# ---------------------------------------------------------------------------

def suivre_ordre(sig, T, H, L, C, bloque=None):
    """Suit un ordre limite puis la position. T en nanosecondes (int64, horloge interne), H/L/C prix BID.
    Renvoie (t_entree, t_sortie, R, issue, etat) ; etat = 'attente', 'en cours', 'tp1' (stop au point d'entrée) ou 'fini'."""
    MIN, JOUR = 60 * 10**9, 86400 * 10**9
    d, e, sl, tp1, tp2 = sig["sens"], sig["entree"], sig["stop"], sig["tp1"], sig["tp2"]
    t0 = pd.Timestamp(sig["t_sig"]).value
    N = len(T)
    i = int(np.searchsorted(T, t0))
    lim = t0 + ORDRE_VIE_MIN * MIN
    rempli = None
    while i < N and T[i] < lim:
        if d > 0:
            if H[i] >= tp1:
                return None, None, None, "annulé (objectif atteint sans nous)", "fini"
            if L[i] + SPREAD <= e:
                rempli = i
                break
        else:
            if L[i] + SPREAD <= tp1:
                return None, None, None, "annulé (objectif atteint sans nous)", "fini"
            if H[i] >= e:
                rempli = i
                break
        i += 1
    if rempli is None:
        if i >= N and (N == 0 or T[-1] < lim):
            return None, None, None, "ordre en attente", "attente"
        return None, None, None, "non exécuté", "fini"
    t_f = pd.Timestamp(T[rempli])
    if bloque is not None and bloque(t_f):
        return None, None, None, "annonce", "fini"
    lundi = t_f.normalize() - pd.Timedelta(days=t_f.weekday())
    fin_max = min(T[rempli] + TENUE_MAX_J * JOUR, (lundi + pd.Timedelta(days=4, hours=23, minutes=45)).value)
    pos, pnl, stop_c, tp1_ok = 1.0, 0.0, sl, False
    for k in range(rempli, N):
        if T[k] >= fin_max:
            sortie = C[k] if d > 0 else C[k] + SPREAD
            pnl += pos * (sortie - e) * d
            return t_f, pd.Timestamp(T[k]), pnl / sig["risque"], "temps", "fini"
        bas, haut = (L[k], H[k]) if d > 0 else (L[k] + SPREAD, H[k] + SPREAD)
        if (bas <= stop_c) if d > 0 else (haut >= stop_c):
            pnl += pos * (stop_c - e) * d
            return t_f, pd.Timestamp(T[k]), pnl / sig["risque"], ("stop" if not tp1_ok else "TP1 puis BE"), "fini"
        if not tp1_ok and ((haut >= tp1) if d > 0 else (bas <= tp1)):
            pnl += 0.5 * (tp1 - e) * d
            pos, tp1_ok, stop_c = 0.5, True, e
        if tp1_ok and ((haut >= tp2) if d > 0 else (bas <= tp2)):
            pnl += pos * (tp2 - e) * d
            return t_f, pd.Timestamp(T[k]), pnl / sig["risque"], "TP2", "fini"
    return t_f, None, (pnl + pos * ((C[-1] if d > 0 else C[-1] + SPREAD) - e) * d) / sig["risque"], \
        "en cours", ("tp1" if tp1_ok else "en cours")
