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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import plan as P  # noqa: E402  (règles communes avec le terminal)
from plan import (SPREAD, DECALAGE, MAX_TRADES_JOUR, PERTE_MAX_JOUR_R)  # noqa: E402

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


def charger_dxy(debut):
    """Dollar index (DXY) quotidien, la même série que le terminal en direct. Repli : dollar large de la Fed."""
    try:
        import yfinance as yf
        d = yf.download("DX-Y.NYB", start=str(debut - timedelta(days=60)), progress=False, auto_adjust=False)
        c = d["Close"]
        c = c.iloc[:, 0] if isinstance(c, pd.DataFrame) else c
        c = c.dropna()
        c.index = pd.DatetimeIndex(c.index).tz_localize(None).normalize()
        if len(c) > 200:
            log(f"  DXY (Yahoo) : {len(c)} jours")
            return c, "DXY"
    except Exception as e:
        log(f"  DXY Yahoo indisponible : {e}")
    return None, None


def charger_fred(debut):
    """Bloc fondamental du plan jour par jour : sur 5 jours (règle actuelle) et sur 20 jours (variante)."""
    series = {"reel": "DFII10", "dollar_large": "DTWEXBGS", "us2": "DGS2"}
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
            if nom != "dollar_large":
                return None
    dxy, src = charger_dxy(debut)
    cols["dollar"] = dxy if dxy is not None else cols.get("dollar_large")
    if cols["dollar"] is None:
        return None
    cols.pop("dollar_large", None)
    f = pd.DataFrame(cols).sort_index().ffill().dropna()
    f = f[f.index >= pd.Timestamp(debut) - pd.Timedelta(days=60)]

    def bloc(n, k):
        out = []
        for r, dl, u in zip((f["reel"] - f["reel"].shift(n)) * 100, (f["dollar"] / f["dollar"].shift(n) - 1) * 100,
                            (f["us2"] - f["us2"].shift(n)) * 100):
            out.append(P.bloc_fondamental(r / k, dl / k, u / k))
        return pd.Series(out, index=f.index)
    log(f"  FRED : {len(f)} jours (taux réels, taux 2 ans) · dollar : {src or 'dollar large Fed'}")
    return pd.DataFrame({"f5": bloc(5, 1), "f20": bloc(20, 2)})   # 20 jours : seuils doublés


# ---------------------------------------------------------------------------
# SIGNAUX ET SIMULATION (règles de plan.py)
# ---------------------------------------------------------------------------

def horloge(df_utc):
    out = df_utc[["open", "high", "low", "close"]].copy()
    out.index = P.vers_horloge(df_utc.index)
    return out[~out.index.duplicated()].sort_index()


# Décisions de la Fed (annonce à 14 h à New York, conférence de presse à 14 h 30), réunions d'urgence de 2020 comprises
FOMC = {date.fromisoformat(x) for x in (
    "2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11 "
    "2020-01-29 2020-03-03 2020-03-15 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16 "
    "2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15 "
    "2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14 "
    "2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13 "
    "2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18 "
    "2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10 "
    "2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16 2026-10-28 2026-12-09").split()}


def fenetre_annonce(t):
    """Grosses annonces US : 8 h - 9 h à New York (CPI, emploi, ventes...) chaque jour de semaine,
    et 13 h 30 - 15 h les jours de décision de la Fed (annonce puis conférence de presse)."""
    ny = t - DECALAGE
    m = ny.hour * 60 + ny.minute
    if ny.weekday() < 5 and 8 * 60 <= m < 9 * 60:
        return True
    return ny.date() in FOMC and 13 * 60 + 30 <= m < 15 * 60


def candidats(m1, fond):
    log("Construction des unités de temps...")
    tf = {"M15": P.agreger(m1, "15min"), "M30": P.agreger(m1, "30min"), "H1": P.agreger(m1, "1h"),
          "H4": P.agreger(m1, "4h"), "D": P.agreger(m1, "1D"), "W": P.agreger(m1, "W")}
    log("Structure de marché, zones OB et FVG...")
    ctx = P.contexte(tf)
    log(f"  {len(ctx['zones'])} zones")
    if fond is not None:
        f_dates, f5, f20 = fond.index.to_numpy(), fond["f5"].to_numpy(), fond["f20"].to_numpy()
    out = []
    for i in range(30, len(tf["M15"])):
        for d in (1, -1):
            fv = fv20 = 0
            if fond is not None and (ctx["st"]["M15"]["bos_h"][i] or ctx["st"]["M15"]["bos_b"][i]):
                jour = pd.Timestamp(ctx["fins"][i]).normalize()
                p = int(np.searchsorted(f_dates, np.datetime64(jour - pd.Timedelta(days=1)), side="right")) - 1
                fv, fv20 = (int(f5[p]), int(f20[p])) if p >= 0 else (0, 0)
            for sg in P.signal(ctx, i, d, fv) or []:
                sg["fond20"] = fv20
                ny = pd.Timestamp(sg["t_sig"]) - DECALAGE
                sg["gros_jour"] = ny.date() in FOMC or (ny.weekday() == 4 and ny.day <= 7)   # Fed ou emploi (1er vendredi)
                out.append(sg)
    log(f"  {len(out)} signaux candidats (avant filtres)")
    return pd.DataFrame(out)


def simuler(cand, m1):
    T = m1.index.to_numpy().astype("datetime64[ns]").astype(np.int64)
    H, L, C = (m1[x].to_numpy() for x in ("high", "low", "close"))
    res = []
    for sig in cand.to_dict("records"):
        t_e, t_s, r, issue, etat = P.suivre_ordre(sig, T, H, L, C, bloque=fenetre_annonce)
        if etat in ("en cours", "tp1"):
            t_s, issue = pd.Timestamp(T[-1]), "fin des données"
        res.append((t_e, t_s, r, issue))
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
    ce = cand["entree_type"] == "ce"
    base_sans_fond = (cand["note"].isin(["A", "B"]) & (cand["D"] == cand["sens"]) & (cand["H4"] == cand["sens"])
                      & (cand["H1"] != -cand["sens"]) & ce)
    if "fond20" in cand:
        variantes += [
            ("Fondamental strict (bloc dans le sens du trade)", base_sans_fond & (cand["fond"] == cand["sens"])),
            ("Fondamental sur 20 jours", base_sans_fond & (cand["fond20"] != -cand["sens"])),
            ("Pas de trade les jours de Fed et d'emploi", plan & ce & ~cand["gros_jour"].astype(bool)),
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
    nb_non = cand[plan & (cand["entree_type"] == "ce")]["issue"].isin(["non exécuté", "annulé (objectif atteint sans nous)"]).sum()
    L += ["", "## Limites de ce test", "",
          "- Les annonces sont approximées par la fenêtre 8 h - 9 h de New York chaque jour, plus 13 h 30 - 15 h les jours "
          "de décision de la Fed. Les autres annonces (ISM à 10 h...) ne sont pas bloquées.",
          "- Le fondamental testé est le bloc Fed / dollar / taux réels (FRED). COT, ETF et liquidité ne sont pas rejoués.",
          "- Quand une même minute touche le stop et l'objectif, le test compte le stop (hypothèse prudente).",
          f"- {nb_non} signaux du plan n'ont jamais été exécutés (le prix n'est pas revenu dans le FVG en 2 h, ou a filé vers l'objectif sans nous).",
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
