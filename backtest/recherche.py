#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RECHERCHE D'UNE STRATÉGIE OR QUI GAGNE VRAIMENT : méthode stricte
=================================================================

Quatre familles d'idées connues sur l'or, 2 à 3 variantes chacune (pas de recherche aveugle de réglages) :
  A. Suivi de tendance Daily : cassure du plus haut (bas) de N jours, stop initial 2 ATR, stop suiveur 3 ATR,
     sortie aussi sur la cassure opposée de 10 jours.
  B. Dérive de nuit : achat à la réouverture (18 h New York), sortie à 8 h New York, avant la séance américaine.
  C. Repli court dans la tendance (RSI 2) : achat si RSI(2) Daily < 10 au-dessus de la moyenne 200 jours, sortie quand
     la clôture repasse au-dessus de la moyenne 5 jours (vente : l'inverse).
  D. Cassure du range asiatique : ordre au-dessus / en dessous du range 18 h - 3 h (New York), entre 3 h et 8 h,
     stop de l'autre côté du range, objectif 1 ou 2 fois le risque, sortie à 15 h New York.

Trois périodes : choix sur 2019-2023, contrôle sur 2024, test unique sur 2025-2026.
Coûts : écart achat-vente 0,30 $ + glissement 0,10 $ par aller-retour, et financement de nuit (5 % par an du prix,
dans les deux sens) pour chaque passage de 17 h New York en position. Coûts doublés en contrôle de robustesse.
1 R = la distance au stop initial. Résultats en R et en % du compte à 1 % de risque par trade.
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backtest_plan as bt  # noqa: E402
import plan as P  # noqa: E402

COUT_FIXE = 0.40          # écart achat-vente + glissement, en $ par aller-retour
FINANCEMENT = 0.05        # par an, appliqué à chaque nuit (passage de 17 h New York)
PERIODES = [("Choix 2019-2023", "2019-01-01", "2024-01-01"), ("Contrôle 2024", "2024-01-01", "2025-01-01"),
            ("Test 2025-2026", "2025-01-01", "2100-01-01")]


def log(*a):
    print(*a, flush=True)


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def rsi(c, n):
    d = c.diff()
    h = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    b = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + h / b.replace(0, np.nan))


def trade(t, d, entree, sortie, risque, nuits, cout=1.0):
    """Résultat en R, coûts déduits (achat payé au prix demandé : le coût fixe couvre l'écart)."""
    brut = (sortie - entree) * d
    frais = cout * (COUT_FIXE + nuits * entree * FINANCEMENT / 365)
    return {"t": t, "sens": d, "R": (brut - frais) / risque, "R_brut": brut / risque, "nuits": nuits}


# ---------------------------------------------------------------------------
# A. SUIVI DE TENDANCE DAILY
# ---------------------------------------------------------------------------

def tendance_daily(D, n, long_seul, cout):
    o, h, l, c = (D[x].to_numpy() for x in ("open", "high", "low", "close"))
    a = atr(D, 20).to_numpy()
    hh, ll = D["high"].rolling(n).max().shift(1).to_numpy(), D["low"].rolling(n).min().shift(1).to_numpy()
    h10, l10 = D["high"].rolling(10).max().shift(1).to_numpy(), D["low"].rolling(10).min().shift(1).to_numpy()
    out, i, N = [], max(n, 21), len(D)
    while i < N - 1:
        d = 1 if c[i] > hh[i] else (-1 if (c[i] < ll[i] and not long_seul) else 0)
        if not d or not np.isfinite(a[i]):
            i += 1
            continue
        k = i + 1                                   # entrée à l'ouverture du jour suivant
        e, risque = o[k], 2 * a[i]
        stop, extreme, j = e - d * risque, c[i], k
        sortie = None
        while j < N:
            if d > 0 and l[j] <= stop or d < 0 and h[j] >= stop:
                sortie = (min(o[j], stop) if d > 0 else max(o[j], stop))
                break
            extreme = max(extreme, c[j]) if d > 0 else min(extreme, c[j])
            stop = max(stop, extreme - 3 * a[j]) if d > 0 else min(stop, extreme + 3 * a[j])
            if j + 1 < N and ((d > 0 and c[j] < l10[j]) or (d < 0 and c[j] > h10[j])):
                sortie, j = o[j + 1], j + 1
                break
            j += 1
        if sortie is None:
            sortie, j = c[N - 1], N - 1
        out.append(trade(D.index[k], d, e, sortie, risque, max(0, j - k), cout))
        i = j + 1
    return out


# ---------------------------------------------------------------------------
# B. DÉRIVE DE NUIT
# ---------------------------------------------------------------------------

def derive_nuit(m1, D, filtre, cout):
    """Achat à 18 h New York (1 h sur l'horloge interne), sortie à 8 h New York (15 h). Stop 2 ATR H1 de secours."""
    T = m1.index
    O, H, L, C = (m1[x].to_numpy() for x in ("open", "high", "low", "close"))
    h1 = P.agreger(m1, "1h")
    a_h1 = atr(h1).shift(1).reindex(T, method="ffill").to_numpy()   # ATR de la dernière bougie H1 terminée
    ema50 = D["close"].ewm(span=50, adjust=False).mean()
    out = []
    for jour in D.index[60:]:
        t0, t1 = jour + pd.Timedelta(hours=1), jour + pd.Timedelta(hours=15)
        if t0.weekday() >= 5:
            continue
        if filtre:
            prec = D.index[D.index < jour]
            if not len(prec) or D["close"].loc[prec[-1]] <= ema50.loc[prec[-1]]:
                continue
        i0, i1 = T.searchsorted(t0), T.searchsorted(t1)
        if i1 - i0 < 300 or not np.isfinite(a_h1[i0]):
            continue
        e, risque = O[i0], 2 * a_h1[i0]
        stop = e - risque
        touche = np.flatnonzero(L[i0:i1] <= stop)
        sortie = stop if len(touche) else C[i1 - 1]
        out.append(trade(T[i0], 1, e, sortie, risque, 0, cout))
    return out


# ---------------------------------------------------------------------------
# C. REPLI COURT RSI(2)
# ---------------------------------------------------------------------------

def repli_rsi2(D, long_seul, cout):
    o, h, l, c = (D[x].to_numpy() for x in ("open", "high", "low", "close"))
    r2 = rsi(D["close"], 2).to_numpy()
    s200, s5 = D["close"].rolling(200).mean().to_numpy(), D["close"].rolling(5).mean().to_numpy()
    a = atr(D, 20).to_numpy()
    out, i, N = [], 201, len(D)
    while i < N - 1:
        d = 1 if (c[i] > s200[i] and r2[i] < 10) else (-1 if (not long_seul and c[i] < s200[i] and r2[i] > 90) else 0)
        if not d:
            i += 1
            continue
        k = i + 1
        e, risque = o[k], 3 * a[i]
        stop, sortie, j = e - d * risque, None, k
        while j < N:
            if d > 0 and l[j] <= stop or d < 0 and h[j] >= stop:
                sortie = min(o[j], stop) if d > 0 else max(o[j], stop)
                break
            if (d > 0 and c[j] > s5[j]) or (d < 0 and c[j] < s5[j]) or j - k >= 10:
                sortie = o[j + 1] if j + 1 < N else c[j]
                j = min(j + 1, N - 1)
                break
            j += 1
        if sortie is None:
            sortie, j = c[N - 1], N - 1
        out.append(trade(D.index[k], d, e, sortie, risque, max(0, j - k), cout))
        i = j + 1
    return out


# ---------------------------------------------------------------------------
# D. CASSURE DU RANGE ASIATIQUE
# ---------------------------------------------------------------------------

def cassure_asie(m1, D, objectif_r, filtre, cout):
    T = m1.index
    H, L, C = (m1[x].to_numpy() for x in ("high", "low", "close"))
    ema50 = D["close"].ewm(span=50, adjust=False).mean()
    out = []
    for jour in D.index[60:]:
        if jour.weekday() >= 5:
            continue
        a0, a1 = T.searchsorted(jour + pd.Timedelta(hours=1)), T.searchsorted(jour + pd.Timedelta(hours=10))
        f0, f1 = a1, T.searchsorted(jour + pd.Timedelta(hours=15))   # ordres valables de 3 h à 8 h New York
        fin = T.searchsorted(jour + pd.Timedelta(hours=22))           # sortie à 15 h New York
        if a1 - a0 < 300 or f1 <= f0:
            continue
        haut, bas = H[a0:a1].max(), L[a0:a1].min()
        largeur = haut - bas
        if largeur <= 0:
            continue
        sens_ok = (1, -1)
        if filtre:
            prec = D.index[D.index < jour]
            if not len(prec):
                continue
            sens_ok = (1,) if D["close"].loc[prec[-1]] > ema50.loc[prec[-1]] else (-1,)
        ach = np.flatnonzero(H[f0:f1] > haut + 0.10) if 1 in sens_ok else np.array([])
        ven = np.flatnonzero(L[f0:f1] < bas - 0.10) if -1 in sens_ok else np.array([])
        if not len(ach) and not len(ven):
            continue
        if len(ach) and (not len(ven) or ach[0] <= ven[0]):
            d, k, e, stop = 1, f0 + ach[0], haut + 0.10, bas
        else:
            d, k, e, stop = -1, f0 + ven[0], bas - 0.10, haut
        risque = abs(e - stop)
        tp = e + d * objectif_r * risque
        seg_h, seg_l = H[k:fin], L[k:fin]
        s = np.flatnonzero(seg_l <= stop) if d > 0 else np.flatnonzero(seg_h >= stop)
        g = np.flatnonzero(seg_h >= tp) if d > 0 else np.flatnonzero(seg_l <= tp)
        if len(s) and (not len(g) or s[0] <= g[0]):
            sortie = stop
        elif len(g):
            sortie = tp
        else:
            sortie = C[fin - 1]
        out.append(trade(T[k], d, e, sortie, risque, 0, cout))
    return out


# ---------------------------------------------------------------------------
# RAPPORT
# ---------------------------------------------------------------------------

def stats(tr):
    if not tr:
        return None
    R = np.array([x["R"] for x in tr])
    cum = np.cumsum(R)
    dd = float((cum - np.maximum.accumulate(np.r_[0, cum])[1:]).min())
    pertes = -R[R < 0].sum()
    return {"n": len(R), "moy": float(R.mean()), "total": float(cum[-1]), "gagn": float((R > 0).mean() * 100),
            "pf": float(R[R > 0].sum() / pertes) if pertes else float("inf"), "dd": dd}


def cellule(s):
    if not s:
        return "—"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return f"{s['n']} tr · {s['moy']:+.3f} R · {s['total']:+.1f} R · PF {pf}"


def decouper(tr, debut, fin):
    return [x for x in tr if pd.Timestamp(debut) <= pd.Timestamp(x["t"]) < pd.Timestamp(fin)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debut", default="2018-06-01")
    ap.add_argument("--sortie", default="resultats_recherche")
    ap.add_argument("--demo", action="store_true")
    a = ap.parse_args()
    if a.demo:
        m1u = bt.donnees_demo(jours=900, graine=4)
    else:
        m1u = bt.charger_dukascopy(date.fromisoformat(a.debut), date.today() - timedelta(days=1), Path("backtest/.donnees"))
    m1 = bt.horloge(m1u)
    D = P.agreger(m1, "1D")
    D = D[D.index.weekday < 5]
    log(f"{len(m1):,} minutes, {len(D)} jours de {D.index[0]:%d/%m/%Y} à {D.index[-1]:%d/%m/%Y}")

    familles = {
        "A · Tendance Daily": [("cassure 20 jours, achats et ventes", lambda c: tendance_daily(D, 20, False, c)),
                               ("cassure 55 jours, achats et ventes", lambda c: tendance_daily(D, 55, False, c)),
                               ("cassure 20 jours, achats seulement", lambda c: tendance_daily(D, 20, True, c))],
        "B · Dérive de nuit": [("tous les jours", lambda c: derive_nuit(m1, D, False, c)),
                               ("seulement au-dessus de l'EMA 50 Daily", lambda c: derive_nuit(m1, D, True, c))],
        "C · Repli RSI(2)": [("achats et ventes", lambda c: repli_rsi2(D, False, c)),
                             ("achats seulement", lambda c: repli_rsi2(D, True, c))],
        "D · Cassure du range asiatique": [("objectif 1 R, deux sens", lambda c: cassure_asie(m1, D, 1.0, False, c)),
                                           ("objectif 2 R, deux sens", lambda c: cassure_asie(m1, D, 2.0, False, c)),
                                           ("objectif 2 R, sens de l'EMA 50 Daily", lambda c: cassure_asie(m1, D, 2.0, True, c))],
    }
    L = ["# Recherche d'une stratégie or qui gagne vraiment", "",
         f"Données 1 minute Dukascopy, {D.index[0]:%d/%m/%Y} → {D.index[-1]:%d/%m/%Y}. Coûts : {COUT_FIXE:.2f} $ par aller-retour "
         f"+ financement de nuit {FINANCEMENT * 100:.0f} % par an. Méthode : la variante est choisie sur 2019-2023 SEULEMENT, "
         "contrôlée sur 2024, puis testée une seule fois sur 2025-2026.", "",
         "Chaque case : trades · gain moyen par trade · total · profit factor (au-delà de 1 = gagnant).", ""]
    candidats = []
    for fam, variantes in familles.items():
        L += [f"## {fam}", "", "| Variante | " + " | ".join(p[0] for p in PERIODES) + " | Test, coûts doublés |",
              "|---|" + "---|" * (len(PERIODES) + 1)]
        meilleur = None
        for nom, fn in variantes:
            tr, tr2 = fn(1.0), fn(2.0)
            par = [stats(decouper(tr, d0, d1)) for _, d0, d1 in PERIODES]
            test2 = stats(decouper(tr2, PERIODES[2][1], PERIODES[2][2]))
            L.append(f"| {nom} | " + " | ".join(cellule(s) for s in par) + f" | {cellule(test2)} |")
            choix = par[0]
            if choix and (meilleur is None or choix["moy"] > meilleur[1][0]["moy"]):
                meilleur = (nom, par, test2, tr)
        if meilleur:
            nom, par, test2, tr = meilleur
            ok = all(s and s["moy"] > 0 and s["pf"] >= 1.1 for s in par) and test2 and test2["moy"] > 0 \
                and all(s and s["n"] >= 30 for s in par)
            verdict = "**passe**" if ok else "échoue"
            L += ["", f"Variante retenue sur 2019-2023 : **{nom}** → {verdict} au contrôle 2024 et au test 2025-2026."]
            if ok:
                candidats.append((fam, nom, par, test2, tr))
            ann = pd.DataFrame(tr).assign(an=lambda x: pd.to_datetime(x["t"]).dt.year).groupby("an")["R"].agg(["count", "sum"])
            L.append("Par année (R) : " + ", ".join(f"{int(k)} : {v['sum']:+.1f} ({int(v['count'])} tr)" for k, v in ann.iterrows()))
        L.append("")
    L += ["## Verdict", ""]
    if candidats:
        for fam, nom, par, test2, tr in candidats:
            s_tot = stats(tr)
            L.append(f"- **{fam} · {nom}** : positive sur les trois périodes et avec coûts doublés. Sur tout l'historique : "
                     f"{s_tot['n']} trades, {s_tot['moy']:+.3f} R par trade, {s_tot['total']:+.1f} R, pire baisse {s_tot['dd']:.1f} R.")
    else:
        L.append("- Aucune variante ne passe les trois périodes avec des coûts réalistes.")
    txt = "\n".join(L) + "\n"
    out = Path(a.sortie)
    out.mkdir(parents=True, exist_ok=True)
    (out / "recherche.md").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    sys.exit(main())
