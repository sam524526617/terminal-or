#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BACKTEST INDÉPENDANT DES STRATÉGIES MTF (autobot_mtf)
====================================================

Les trois stratégies sont recodées à partir de leur description (les fichiers .pine n'étaient pas disponibles) :
  BTC 1 · repli dans la tendance H1 : stochastique M15 < 20 (achat) / > 80 (vente), prix au-dessus (sous) de l'EMA50 H1,
          H1 directionnel (efficiency ratio 10 > seuil). Stop 2 ATR H1, objectif 1,5 ATR H1, 12 h max.
  BTC 2 · cassure M15 + pente EMA200 : ADX M15 > 25 avec +DI devant (-DI pour une vente), clôture au-delà du plus haut
          (plus bas) des 20 bougies précédentes, EMA200 M15 qui monte (descend). Stop 2 ATR H1, objectif 3 ATR H1, 12 h max.
  XAU 3 · momentum M15 + H4 : momentum 10 bougies positif (négatif) en M15 et sur la bougie H4 précédente, volatilité H4
          haute (ATR H4 au-dessus de sa médiane sur 100 bougies). Stop 3 ATR H1, objectif 4 ATR H1, 12 h max.

Règles d'exécution : signal à la clôture d'une bougie M15, entrée à l'ouverture de la minute suivante, une position à la
fois par stratégie, stop et objectif suivis minute par minute (stop compté en premier si les deux sont touchés dans la
même minute), sortie au bout de 48 bougies M15. Écart achat-vente compté à l'entrée et à la sortie.

Deux lectures des unités de temps supérieures :
  - « honnête » : seules les bougies H1 / H4 TERMINÉES au moment du signal sont utilisées (ce qu'un robot voit en direct) ;
  - « bougie en cours » : la bougie H1 / H4 qui contient le signal est utilisée avec sa clôture finale. C'est un regard
    vers le futur : si cette version fait beaucoup mieux, les résultats d'origine en profitent peut-être.
"""

import argparse
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest_plan as bt  # noqa: E402  (téléchargement et lecture Dukascopy)

INSTRUMENTS = {
    "BTCUSD": {"nom": "BTCUSD", "diviseur": 10, "samedi": True, "spread_pct": 0.04},   # ~25 $ à 60 000 $
    "XAUUSD": {"nom": "XAUUSD", "diviseur": None, "samedi": False, "spread_abs": 0.30},
}

STRATEGIES = [
    {"nom": "BTC 1 · repli dans la tendance H1", "inst": "BTCUSD", "regle": "btc1", "sl": 2.0, "tp": 1.5, "duree": 48,
     "origine": {"trades": 1383, "total_R": 166.3, "R_moyen": 0.12, "gagnants": 60, "pf": 1.47,
                 "2025": (223, -3.4, 52), "2026": (150, 17.7, 59)}},
    {"nom": "BTC 2 · cassure M15 + pente EMA200", "inst": "BTCUSD", "regle": "btc2", "sl": 2.0, "tp": 3.0, "duree": 48,
     "origine": {"trades": 1382, "total_R": 135.5, "R_moyen": 0.098, "gagnants": 48, "pf": 1.25,
                 "2025": (218, -2.2, 43), "2026": (162, 17.3, 48)}},
    {"nom": "XAU 3 · momentum M15 + H4", "inst": "XAUUSD", "regle": "xau3", "sl": 3.0, "tp": 4.0, "duree": 48,
     "origine": {"trades": 1797, "total_R": 43.5, "R_moyen": 0.024, "gagnants": 49, "pf": 1.09,
                 "2025": (233, 6.1, 50), "2026": (184, 20.4, 53)}},
]
ER_SEUILS = (0.3, 0.2, 0.4)        # « H1 directionnel » : seuil principal puis sensibilité


def log(*a):
    print(*a, flush=True)


# ---------------------------------------------------------------------------
# INDICATEURS
# ---------------------------------------------------------------------------

def bougies(m1, regle):
    g = m1.resample(regle, label="left", closed="left")
    df = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                       "close": g["close"].last()}).dropna()
    df["fin"] = df.index + pd.Timedelta(regle)
    return df


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def atr(df, n=14):
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def stoch_k(df, n=14, lissage=3):
    lo, hi = df["low"].rolling(n).min(), df["high"].rolling(n).max()
    k = 100 * (df["close"] - lo) / (hi - lo).replace(0, np.nan)
    return k.rolling(lissage).mean()


def efficiency_ratio(c, n=10):
    return (c - c.shift(n)).abs() / c.diff().abs().rolling(n).sum().replace(0, np.nan)


def adx(df, n=14):
    up, dn = df["high"].diff(), -df["low"].diff()
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(df, n)
    pdi = 100 * pd.Series(pdm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * pd.Series(mdm, index=df.index).ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean(), pdi, mdi


def vers_m15(m15, sup, col, futur=False):
    """Valeur d'une unité supérieure lue à la clôture de chaque bougie M15.
    futur=False : dernière bougie supérieure TERMINÉE ; futur=True : bougie supérieure qui contient la bougie M15."""
    cle = sup["fin"] if not futur else sup.index.to_series()
    s = pd.Series(sup[col].to_numpy(), index=pd.DatetimeIndex(cle.to_numpy()))
    if futur:
        s = s[~s.index.duplicated()]
        return s.reindex(m15.index, method="ffill").to_numpy()
    return s.reindex(m15["fin"].to_numpy(), method="ffill").to_numpy()


# ---------------------------------------------------------------------------
# SIGNAUX
# ---------------------------------------------------------------------------

def signaux(m15, h1, h4, regle, futur, er_seuil):
    """Tableaux (achat, vente) de booléens, un par bougie M15 (signal à sa clôture)."""
    c = m15["close"]
    if regle == "btc1":
        k = stoch_k(m15).to_numpy()
        h1 = h1.assign(ema50=ema(h1["close"], 50), er=efficiency_ratio(h1["close"], 10))
        e50 = vers_m15(m15, h1, "ema50", futur)
        er = vers_m15(m15, h1, "er", futur)
        dirige = er > er_seuil
        return (k < 20) & (c.to_numpy() > e50) & dirige, (k > 80) & (c.to_numpy() < e50) & dirige
    if regle == "btc2":
        ax, pdi, mdi = adx(m15)
        hh = m15["high"].rolling(20).max().shift(1)
        ll = m15["low"].rolling(20).min().shift(1)
        e200 = ema(c, 200)
        pente = e200 - e200.shift(1)
        a = ((ax > 25) & (pdi > mdi) & (c > hh) & (pente > 0)).to_numpy()
        v = ((ax > 25) & (mdi > pdi) & (c < ll) & (pente < 0)).to_numpy()
        return a, v
    if regle == "xau3":
        roc15 = (c / c.shift(10) - 1).to_numpy()
        a4 = atr(h4)
        h4 = h4.assign(roc=h4["close"] / h4["close"].shift(10) - 1, volh=(a4 > a4.rolling(100).median()).astype(float))
        # « bougie H4 précédente » (@1) : en version honnête, c'est la dernière terminée ; en version futur, la bougie en cours
        roc4 = vers_m15(m15, h4, "roc", futur)
        volh = vers_m15(m15, h4, "volh", futur) > 0.5
        return (roc15 > 0) & (roc4 > 0) & volh, (roc15 < 0) & (roc4 < 0) & volh
    raise ValueError(regle)


# ---------------------------------------------------------------------------
# SIMULATION
# ---------------------------------------------------------------------------

def simuler(strat, m1, m15, atr_h1, achat, vente, spread_fn, debut_compte, cout=1.0):
    T = m1.index.to_numpy().astype("datetime64[ns]").astype(np.int64)
    O, H, L, C = (m1[x].to_numpy() for x in ("open", "high", "low", "close"))
    fins = m15["fin"].to_numpy().astype("datetime64[ns]").astype(np.int64)
    duree_ns = strat["duree"] * 15 * 60 * 10**9
    libre_a = 0
    debut_ns = pd.Timestamp(debut_compte).value
    trades = []
    for i in np.flatnonzero(achat | vente):
        t_sig = fins[i]
        if t_sig < max(libre_a, debut_ns) or not np.isfinite(atr_h1[i]) or atr_h1[i] <= 0:
            continue
        d = 1 if achat[i] else -1
        k0 = int(np.searchsorted(T, t_sig))
        if k0 >= len(T) or T[k0] - t_sig > 30 * 60 * 10**9:      # trou de données (week-end, panne)
            continue
        sp = spread_fn(O[k0]) * cout
        entree = O[k0] + (sp if d > 0 else 0.0)                    # achat au prix demandé, vente au prix offert
        dist_sl, dist_tp = strat["sl"] * atr_h1[i], strat["tp"] * atr_h1[i]
        sl, tp = entree - d * dist_sl, entree + d * dist_tp
        k1 = int(np.searchsorted(T, t_sig + duree_ns))
        if d > 0:   # sorties au prix offert (BID)
            touche_sl, touche_tp = L[k0:k1] <= sl, H[k0:k1] >= tp
        else:       # sorties au prix demandé (BID + écart)
            touche_sl, touche_tp = H[k0:k1] + sp >= sl, L[k0:k1] + sp <= tp
        a = int(np.argmax(touche_sl)) if touche_sl.any() else None
        b = int(np.argmax(touche_tp)) if touche_tp.any() else None
        if a is not None and (b is None or a <= b):
            r, fin, issue = -1.0, k0 + a, "stop"
        elif b is not None:
            r, fin, issue = dist_tp / dist_sl, k0 + b, "objectif"
        else:
            fin = min(k1, len(T)) - 1
            sortie = C[fin] + (0.0 if d > 0 else sp)
            r, issue = (sortie - entree) * d / dist_sl, "temps"
        trades.append({"t": pd.Timestamp(T[k0]), "sens": d, "R": r, "issue": issue})
        libre_a = T[fin] + 1
    return pd.DataFrame(trades)


def stats(tr):
    if tr is None or tr.empty:
        return None
    R = tr["R"].to_numpy()
    cum = np.cumsum(R)
    dd = float((cum - np.maximum.accumulate(np.r_[0, cum])[1:]).min())
    pertes = -R[R < 0].sum()
    return {"n": len(R), "total": float(cum[-1]), "moy": float(R.mean()), "gagnants": float((R > 0).mean() * 100),
            "pf": float(R[R > 0].sum() / pertes) if pertes else float("inf"), "dd": dd}


def ligne(nom, s, extra=""):
    if not s:
        return f"| {nom} | 0 | — | — | — | — | — |{extra}"
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"| {nom} | {s['n']} | {s['total']:+.1f} R | {s['moy']:+.3f} R | {s['gagnants']:.0f} % | {pf} | "
            f"{s['dd']:.1f} R |{extra}")


ENTETE = ("| | Trades | Total | Gain moyen | Gagnants | Profit factor | Pire baisse |\n"
          "|---|---|---|---|---|---|---|")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--debut", default="2025-01-01", help="premier jour compté")
    ap.add_argument("--fin", default=None)
    ap.add_argument("--sortie", default="resultats_mtf")
    ap.add_argument("--telecharger-seulement", action="store_true")
    a = ap.parse_args()
    debut = date.fromisoformat(a.debut)
    fin = date.fromisoformat(a.fin) if a.fin else date.today() - timedelta(days=1)
    chauffe = debut - timedelta(days=45)          # de quoi former EMA200, ADX, médiane d'ATR H4...
    donnees = {}
    for inst in ("BTCUSD", "XAUUSD"):
        bt.INSTRUMENT.update({k: v for k, v in INSTRUMENTS[inst].items() if k in ("nom", "diviseur", "samedi")})
        dossier = Path("backtest/.donnees" if inst == "XAUUSD" else "backtest/.donnees_btc")
        if a.telecharger_seulement:
            bt.telecharger(chauffe, fin, dossier)
            continue
        m1 = bt.charger_dukascopy(chauffe, fin, dossier)[["open", "high", "low", "close"]]
        log(f"{inst} : premier prix {m1['close'].iloc[0]:,.2f} ({m1.index[0]:%d/%m/%Y}), dernier {m1['close'].iloc[-1]:,.2f}")
        donnees[inst] = m1
    if a.telecharger_seulement:
        return
    L = [f"# Stratégies de ton amie : backtest indépendant {debut:%d/%m/%Y} → {fin:%d/%m/%Y}", "",
         "Recodées à partir de leur description (sans les fichiers .pine), données 1 minute Dukascopy, entrée à "
         "l'ouverture de la minute qui suit la clôture du signal M15, une position à la fois, stop compté en premier si "
         "stop et objectif tombent dans la même minute. Écart achat-vente : 0,04 % pour le BTC, 0,30 $ pour l'or, "
         "compté à l'entrée et à la sortie. 1 R = la distance au stop.", ""]
    for strat in STRATEGIES:
        inst = strat["inst"]
        m1 = donnees[inst]
        spread_fn = (lambda p: p * INSTRUMENTS[inst]["spread_pct"] / 100) if "spread_pct" in INSTRUMENTS[inst] \
            else (lambda p: INSTRUMENTS[inst]["spread_abs"])
        t0 = time.time()
        m15, h1, h4 = bougies(m1, "15min"), bougies(m1, "1h"), bougies(m1, "4h")
        atr_h1 = vers_m15(m15, h1.assign(a=atr(h1)), "a", False)
        res = {}
        for nom_v, futur, er_s, cout in (("honnête", False, ER_SEUILS[0], 1.0), ("honnête, coûts doublés", False, ER_SEUILS[0], 2.0),
                                         ("bougie H1/H4 en cours (regard vers le futur)", True, ER_SEUILS[0], 1.0)) + \
                (tuple((f"honnête, seuil « directionnel » ER > {x}", False, x, 1.0) for x in ER_SEUILS[1:])
                 if strat["regle"] == "btc1" else ()):
            ach, ven = signaux(m15, h1, h4, strat["regle"], futur, er_s)
            ach, ven = np.nan_to_num(ach, nan=0).astype(bool), np.nan_to_num(ven, nan=0).astype(bool)
            res[nom_v] = simuler(strat, m1, m15, atr_h1, ach, ven, spread_fn, debut, cout)
        o = strat["origine"]
        L += [f"## {strat['nom']}", "",
              f"Ses chiffres à elle (2019-2026) : {o['trades']} trades, {o['total_R']:+.1f} R, {o['R_moyen']:+.3f} R par trade, "
              f"{o['gagnants']} % de gagnants, profit factor {o['pf']:.2f}. Sur 2025 : {o['2025'][0]} trades, {o['2025'][1]:+.1f} R, "
              f"{o['2025'][2]} % de gagnants. Sur 2026 : {o['2026'][0]} trades, {o['2026'][1]:+.1f} R, {o['2026'][2]} % de gagnants.", "",
              ENTETE]
        L += [ligne(k, stats(v)) for k, v in res.items()]
        h = res["honnête"]
        if not h.empty:
            h = h.assign(annee=h["t"].dt.year)
            L += ["", "Version honnête, par année et par sens :", "", ENTETE]
            L += [ligne(str(y), stats(g)) for y, g in h.groupby("annee")]
            L += [ligne("Achats", stats(h[h["sens"] > 0])), ligne("Ventes", stats(h[h["sens"] < 0]))]
            iss = h["issue"].value_counts()
            L += ["", "Fin des trades : " + ", ".join(f"{k} {v} ({v / len(h) * 100:.0f} %)" for k, v in iss.items())]
        L.append("")
        log(f"{strat['nom']} : {time.time() - t0:.0f} s")
    txt = "\n".join(L) + "\n"
    out = Path(a.sortie)
    out.mkdir(parents=True, exist_ok=True)
    (out / "rapport_mtf.md").write_text(txt, encoding="utf-8")
    print(txt)


if __name__ == "__main__":
    sys.exit(main())
