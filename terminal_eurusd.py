#!/usr/bin/env python3
"""
TERMINAL EUR/USD : la page sœur du terminal de l'or.

Même moteur que terminal_or.py (technique, playbook, contrôle Achat / Vente, mode Trader, suivi des prévisions),
avec un fondamental propre à l'euro : écart de taux entre les États-Unis et la zone euro, attentes de la BCE face à
la Fed, énergie européenne, appétit pour le risque, dollar global et positionnement des fonds sur l'euro.

Prix suivis en interne en pips (prix x 10 000), affichés à 5 décimales. Les calculs de l'or ne sont pas modifiés :
cette page tourne dans un processus séparé.

Usage :  python terminal_eurusd.py --site site/eurusd      (lancé automatiquement après la page de l'or)
         python terminal_eurusd.py --demo                  (données fictives pour tester l'affichage)
"""

import argparse
import concurrent.futures as cf
import csv
import io
import json
import math
import os
import re
import sys

import webbrowser
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import terminal_or as T  # noqa: E402
from terminal_or import (nb, fp, esc, derniere, variation, changements, percentile, clamp, date_fr, noter,  # noqa: E402
                         http_get, maintenant, tz_local, COUL, classe_effet, fraicheur, barre_score,
                         barre_centree, graphique_double, MOIS_FR)

PIP = 10000  # 1 pip = 0,0001 : les prix sont suivis en interne x 10 000

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

T.ECH_PRIX, T.DEC_PRIX, T.UNITE = PIP, 5, "pips"
T.ACTIF = {"nom": "l'euro", "Nom": "L'EUR/USD", "sym": "EUR/USD", "de": "de l'euro",
           "titre": "TERMINAL EUR/USD", "court": "EUR/USD", "page": "Terminal EUR/USD", "marche": "Marché des changes",
           "spot": "EUR/USD spot", "future": "future CME", "tv_pays": "us,eu",
           "mini": "EUR/USD", "rs": "EUR Relative Strength", "semaine": "Annonces à fort impact aux États-Unis et en zone euro, décisions de la Fed et de la BCE.",
           "reg_spread": "Spread moyen de ton broker sur EUR/USD (pips)",
           "pages": [("Or", "../", False), ("EUR/USD", "./", True)],
           "cfg_js": {"ech": PIP, "pxd": 5, "u": "pips", "fx": True, "titre": "Terminal EUR/USD", "sym": "EUR/USD",
                      "ferme": "Marché des changes fermé.", "rel": "de l’euro face aux taux et aux actions",
                      "cleReglages": "reglages-eurusd", "src": "Kraken",
                      "titrePrix": "Prix EUR/USD en direct (Kraken) : tu peux le remplacer par celui de ta plateforme",
                      # Prix en direct lisible depuis la page : Kraken (vraie paire EUR/USD), sinon référence du robot
                      "live": [{"nom": "Kraken", "url": "https://api.kraken.com/0/public/Ticker?pair=EURUSD",
                                "chemin": ["result", "*", "c", 0]}]}}

T.DOSSIER_CACHE = T.DOSSIER / ".cache_terminal_or" / "eurusd"   # dans le cache déjà conservé par le robot
T.FICHIER_HTML = T.DOSSIER / "terminal_eurusd.html"
T.FICHIER_BRIEF = T.DOSSIER / "brief_eurusd.txt"

T.TV_OR = "OANDA:EURUSD"
T.TV_BANDEAU = [("OANDA:EURUSD", "EUR/USD"), ("CAPITALCOM:DXY", "Dollar index"), ("OANDA:USB02YUSD", "T-Note 2 ans"),
                ("OANDA:DE10YBEUR", "Bund 10 ans"), ("OANDA:EU50EUR", "Euro Stoxx 50"), ("FX:EURGBP", "EUR/GBP"),
                ("FX:EURJPY", "EUR/JPY"), ("FX:USDJPY", "USD/JPY"), ("TVC:UKOIL", "Brent"), ("OANDA:XAUUSD", "Or")]
T.TV_MINIS = [("CAPITALCOM:DXY", "Dollar index"), ("OANDA:USB02YUSD", "T-Note 2 ans (monte si les taux baissent)"),
              ("OANDA:DE10YBEUR", "Bund 10 ans (monte si les taux baissent)"), ("OANDA:EU50EUR", "Euro Stoxx 50")]

# Taille de position : 1 lot = 100 000 euros, soit 10 $ par pip
T.COMPTE = {"capital": 50000, "risque_pct": 0.5, "spread": 0.8, "once_par_lot": 10}

T.THEMES_NEWS = [
    ("EUR/USD", "EUR/USD OR \"euro dollar\" OR \"euro exchange rate\""),
    ("BCE", "ECB OR Lagarde OR \"European Central Bank\""),
    ("Fed et dollar", "Federal Reserve OR \"US dollar\" OR Treasury yields"),
    ("Économie européenne", "eurozone economy OR German economy OR eurozone inflation OR \"gas prices\" Europe"),
]

T.IA_MAX_JOUR = 6            # le quota gratuit de Gemini est partagé avec la page de l'or
T.IA_INTERVALLE_MIN = 180

# Réunions de politique monétaire de la BCE (jour de la décision, 14 h 15 à Paris)
BCE = ["2026-10-29", "2026-12-17", "2027-02-04", "2027-03-18", "2027-04-29", "2027-06-10", "2027-07-22",
       "2027-09-09", "2027-10-28", "2027-12-16"]

SEUILS_EUR = {"ecart2_pb": 5, "ecart10_pb": 6, "attentes_pb": 8, "ttf_pct": 8, "brent_pct": 4, "usd_pct": 0.5,
              "cot_haut": 85, "cot_bas": 15, "surprise": 0.3}

T.YAHOO = {
    "or": ("EURUSD=X", "EUR/USD"),       # « or » = l'actif suivi par le moteur (ici l'EUR/USD), nom interne
    "dxy": ("DX-Y.NYB", "Dollar index (DXY)"),
    "us10": ("^TNX", "Taux US 10 ans"),
    "spx": ("^GSPC", "S&P 500"),
    "stoxx": ("^STOXX50E", "Euro Stoxx 50"),
    "dax": ("^GDAXI", "DAX"),
    "vix": ("^VIX", "VIX"),
    "move": ("^MOVE", "Volatilité obligataire (MOVE)"),
    "evz": ("^EVZ", "Volatilité implicite de l'euro (EVZ)"),
    "brent": ("BZ=F", "Brent"),
    "ttf": ("TTF=F", "Gaz européen (TTF)"),
    "gold": ("GC=F", "Or"),
    "usdjpy": ("JPY=X", "USD/JPY"),
    "gbpusd": ("GBPUSD=X", "GBP/USD"),
    "usdchf": ("CHF=X", "USD/CHF"),
    "eurgbp": ("EURGBP=X", "EUR/GBP"),
    "eurjpy": ("EURJPY=X", "EUR/JPY"),
}

T.FRED = {
    "us2": ("DGS2", "Taux US 2 ans"),
    "us1": ("DGS1", "Taux US 1 an"),
    "us10f": ("DGS10", "Taux US 10 ans"),
    "reel10": ("DFII10", "Taux réel US 10 ans"),
    "be10": ("T10YIE", "Inflation anticipée US 10 ans"),
    "effr": ("DFF", "Fed funds effectif"),
    "sofr": ("SOFR", "SOFR"),
    "cible_bas": ("DFEDTARL", "Cible Fed, borne basse"),
    "cible_haut": ("DFEDTARU", "Cible Fed, borne haute"),
    "hy": ("BAMLH0A0HYM2", "Spread crédit high yield"),
    "nfci": ("NFCI", "Conditions financières (Chicago Fed)"),
}


def effet_energie():
    """Pour l'euro, l'énergie chère est toujours une mauvaise nouvelle : la zone euro importe son gaz et son pétrole."""
    return -1


T.effet_petrole = effet_energie


def fixings_fx(jour):
    """Le fixing WM/Reuters de 16 h à Londres : le principal pic de flux de la journée sur les devises."""
    try:
        from zoneinfo import ZoneInfo
        lon = ZoneInfo("Europe/London")
        return [datetime.combine(jour, datetime.min.time()).replace(hour=16, tzinfo=lon).astimezone(tz_local())]
    except Exception:
        return []


T.heures_fixing = fixings_fx

T.MECANISMES = {
    "ecart2": "L'écart de taux à 2 ans entre les États-Unis et la zone euro résume les politiques attendues de la Fed "
              "et de la BCE : quand il se resserre, détenir de l'euro devient relativement plus rémunérateur.",
    "us2": "Le taux US 2 ans suit les attentes sur la Fed : sa hausse renforce le dollar face à l'euro.",
    "eu2": "Le taux à 2 ans de la zone euro (courbe AAA) suit les attentes sur la BCE : sa hausse soutient l'euro.",
    "dxy": "L'euro pèse près de 58 % du dollar index : les deux bougent presque en miroir.",
    "stoxx": "Les actions européennes mesurent la confiance dans la zone euro : l'euro est une devise pro-cyclique.",
    "spx": "L'appétit pour le risque mondial : en panique, le dollar joue son rôle de valeur refuge.",
    "vix": "La peur sur les actions : un VIX qui s'envole profite en général au dollar.",
    "move": "La volatilité des obligations US : quand elle grimpe, le dollar est recherché.",
    "ttf": "Le gaz européen : la zone euro l'importe, son envolée dégrade sa balance commerciale et sa croissance.",
    "brent": "Le pétrole, payé en dollars et importé par l'Europe : sa hausse pèse sur l'euro.",
    "gold": "L'or monte souvent quand le dollar baisse : une divergence avec l'euro signale un mouvement propre au dollar.",
    "usdjpy": "Le dollar face au yen : il montre si le dollar se renforce partout ou seulement face à l'euro.",
    "gbpusd": "La livre face au dollar : même lecture, pour isoler ce qui est propre à l'euro.",
    "evz": "La volatilité attendue de l'euro : sa hausse annonce des mouvements plus amples, donc des stops à élargir.",
}


# ---------------------------------------------------------------------------
# COLLECTE DES DONNÉES
# ---------------------------------------------------------------------------

def charger_yahoo_eur():
    try:
        out = T.charger_yahoo()   # liste T.YAHOO ci-dessus ; « or » = EUR/USD
    except RuntimeError as e:
        raise RuntimeError(str(e).replace("cours de l'or", "cours EUR/USD"))
    out["or"] = out["or"] * PIP
    for cle in ("us10",):
        if cle in out and out[cle].iloc[-1] > 20:
            out[cle] = out[cle] / 10
    return out


def _en_paris(df):
    idx = pd.to_datetime(df.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    tz = tz_local()
    df.index = idx.tz_convert(tz) if tz else idx
    return df


def charger_intraday_eur():
    """Bougies du future euro CME (6E=F, avec volumes, pour le VWAP et le profil de volume) et bougies 15 min de
    l'EUR/USD spot (pour mesurer l'écart future-spot au même instant). Prix x 10 000."""
    if T.yf is None:
        return None
    ib = T.extraire_ohlcv(T.telecharger_yahoo("6E=F", "60d", "15m"), "6E=F")
    if ib.empty:
        ib = T.extraire_ohlcv(T.telecharger_yahoo("6E=F", "5d", "15m"), "6E=F")
    jb = T.extraire_ohlcv(T.telecharger_yahoo("6E=F", "1y", "1d"), "6E=F")
    hb = T.extraire_ohlcv(T.telecharger_yahoo("6E=F", "730d", "1h"), "6E=F")
    sp = T.extraire_ohlcv(T.telecharger_yahoo("EURUSD=X", "5d", "15m"), "EURUSD=X")
    if ib.empty:
        noter("Yahoo intraday", False, "bougies 15 min du future euro indisponibles")
        return None
    prix = ["open", "high", "low", "close"]
    for df in (ib, jb, hb, sp):
        if not df.empty:
            df[[c for c in prix if c in df]] = df[[c for c in prix if c in df]] * PIP
    ib = _en_paris(ib)
    if not jb.empty:
        jb.index = T.normaliser_index(jb.index)
    if not hb.empty:
        hb = _en_paris(hb)
    spot = _en_paris(sp)["close"].dropna() if not sp.empty else None
    noter("Yahoo intraday", True, f"{len(ib)} bougies 15 min, {len(hb)} bougies horaires (future euro CME)"
          + (", spot 15 min" if spot is not None else ""))
    return {"barres": ib, "jours": jb, "heures": hb, "spot15": spot}


def charger_intermarche_eur():
    """Bougies 15 min : future euro, dollar index, taux US 2 ans, Euro Stoxx 50, or (5 jours)."""
    if T.yf is None:
        return None
    tick = ["6E=F", "DX-Y.NYB", "2YY=F", "ZT=F", "^STOXX50E", "GC=F"]
    df = T.telecharger_yahoo(tick, "5d", "15m")
    c = T._niveau(df, "Close") if df is not None and len(df) else None
    if c is None or c.empty:
        noter("Yahoo intermarché 15 min", False, "indisponible")
        return None
    c = _en_paris(c.copy())
    if "6E=F" in c:
        c["6E=F"] = c["6E=F"] * PIP
    if "2YY=F" in c and c["2YY=F"].dropna().size and 0 < c["2YY=F"].dropna().iloc[-1] < 20:
        c["US2Y"] = c["2YY=F"]
    elif "ZT=F" in c and c["ZT=F"].dropna().size:
        zt = c["ZT=F"]
        c["US2Y"] = 4.0 - (zt / zt.dropna().iloc[-1] - 1) * 100 / 1.9
    noter("Yahoo intermarché 15 min", True, ", ".join(t for t in ("6E=F", "DX-Y.NYB", "US2Y", "^STOXX50E", "GC=F")
                                                      if t in c and c[t].dropna().size))
    return c


def _csv_bce(flux, cle, n=520):
    """Séries de la BCE (data-api.ecb.europa.eu, format CSV, sans clé). Retourne {suffixe de série: pd.Series}."""
    txt = http_get(f"https://data-api.ecb.europa.eu/service/data/{flux}/{cle}",
                   {"format": "csvdata", "lastNObservations": str(n)}, ttl_min=180, timeout=25,
                   agents=(T.UA_SCRIPT, T.UA_NAVIGATEUR), valider=lambda t: "OBS_VALUE" in t[:3000])
    lecteur = csv.DictReader(io.StringIO(txt))
    brut = {}
    for r in lecteur:
        r = {(k or "").strip().upper(): v for k, v in r.items()}
        k = r.get("KEY") or r.get("DATA_TYPE_FM") or cle
        brut.setdefault(k.split(".")[-1], []).append((r.get("TIME_PERIOD"), r.get("OBS_VALUE")))
    out = {}
    for k, pts in brut.items():
        s = pd.Series(pd.to_numeric([v for _, v in pts], errors="coerce"),
                      index=pd.to_datetime([d for d, _ in pts], errors="coerce")).dropna().sort_index()
        s = s[~s.index.isna()]
        if len(s):
            s.index = T.normaliser_index(s.index)
            out[k] = s
    return out


def charger_bce():
    """Courbe des taux de la zone euro (obligations notées AAA, proche de l'Allemagne), taux de dépôt et €STR."""
    out, manq = {}, []
    try:
        yc = _csv_bce("YC", "B.U2.EUR.4F.G_N_A.SV_C_YM.SR_1Y+SR_2Y+SR_10Y")
        for k, nom in (("SR_1Y", "eu1"), ("SR_2Y", "eu2"), ("SR_10Y", "eu10")):
            if k in yc:
                out[nom] = yc[k]
            else:
                manq.append(nom)
    except Exception:
        manq.append("courbe des taux")
    for flux, cle, nom in (("FM", "D.U2.EUR.4F.KR.DFR.LEV", "dfr"), ("EST", "B.EU000A2X2A25.WT", "estr")):
        try:
            r = _csv_bce(flux, cle, 400)
            if r:
                out[nom] = list(r.values())[0]
            else:
                manq.append(nom)
        except Exception:
            manq.append(nom)
    if not out:
        raise RuntimeError("aucune série BCE récupérée")
    noter("BCE (taux zone euro)", "eu2" in out, (f"{len(out)} séries" + (", manquantes : " + ", ".join(manq) if manq else "")))
    return out


def charger_cot_eur():
    """Positionnement sur le future euro CME (code CFTC 099741), rapport Traders in Financial Futures :
    fonds spéculatifs (leveraged funds), gestionnaires d'actifs et banques."""
    exclure = ("change", "pct", "traders", "spread", "_old", "_other", "conc")
    derniere = None
    for dataset in ("gpe5-46if", "yw9f-hn96"):
        url = f"https://publicreporting.cftc.gov/resource/{dataset}.json"
        for params in ({"cftc_contract_market_code": "099741", "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "400"},
                       {"cftc_contract_market_code": "099741", "$limit": "5000"}):
            try:
                lignes = json.loads(http_get(url, params, ttl_min=180))
                if not lignes:
                    continue
                cles = set().union(*[l.keys() for l in lignes])
                f_date = T._champ(cles, ("report_date",))
                f_oi = T._champ(cles, ("open_interest",), ("change", "pct", "_old", "_other"))
                if not f_date:
                    continue
                cols = {"date": pd.to_datetime([l.get(f_date) for l in lignes], errors="coerce")}
                cols["oi"] = pd.to_numeric([l.get(f_oi) for l in lignes], errors="coerce") if f_oi else np.nan
                for g, motif in (("mm", "lev_money"), ("am", "asset_mgr"), ("deal", "dealer")):
                    fl, fs = T._champ(cles, (motif, "long"), exclure), T._champ(cles, (motif, "short"), exclure)
                    if fl and fs:
                        cols[g + "_long"] = pd.to_numeric([l.get(fl) for l in lignes], errors="coerce")
                        cols[g + "_short"] = pd.to_numeric([l.get(fs) for l in lignes], errors="coerce")
                if "mm_long" not in cols:
                    continue
                df = pd.DataFrame(cols).dropna(subset=["date", "mm_long", "mm_short"])
                df = df.drop_duplicates("date").sort_values("date").tail(400).reset_index(drop=True)
                for g in ("mm", "am", "deal"):
                    if g + "_long" in df:
                        df[g + "_net"] = df[g + "_long"] - df[g + "_short"]
                df["long"], df["short"], df["net"] = df["mm_long"], df["mm_short"], df["mm_net"]
                df.attrs["categorie"] = "fonds spéculatifs (leveraged funds)"
                noter("CFTC (COT euro)", True, "fonds spéculatifs et gestionnaires d'actifs")
                return df
            except Exception as e:
                derniere = e
    raise RuntimeError(f"COT euro indisponible ({derniere})")


def charger_calendrier_eur():
    """ForexFactory : annonces USD et EUR à impact moyen et fort. Les annonces européennes sont préfixées « EUR · »."""
    bruts = []
    for nom in ("thisweek", "nextweek"):
        try:
            bruts.extend(json.loads(http_get(f"https://nfs.faireconomy.media/ff_calendar_{nom}.json", ttl_min=60)))
        except Exception:
            if nom == "thisweek":
                raise
    tz = tz_local()
    vus, out = set(), []
    for e in bruts:
        dev = e.get("country")
        if dev not in ("USD", "EUR") or e.get("impact") not in ("High", "Medium"):
            continue
        try:
            d = datetime.fromisoformat(str(e["date"]))
            d = d.astimezone(tz) if tz else d.astimezone()
        except Exception:
            continue
        titre = ("EUR · " if dev == "EUR" else "") + (e.get("title") or "")
        cle = (titre, d.isoformat())
        if cle in vus:
            continue
        vus.add(cle)
        out.append({"date": d, "titre": titre, "impact": e.get("impact"), "devise": dev,
                    "prevision": e.get("forecast") or "", "precedent": e.get("previous") or "",
                    "reel": e.get("actual") or ""})
    out.sort(key=lambda x: x["date"])
    noter("ForexFactory (calendrier)", True, f"{sum(x['devise'] == 'USD' for x in out)} annonces USD, "
                                             f"{sum(x['devise'] == 'EUR' for x in out)} annonces EUR")
    return out


def charger_bce_officiel():
    """Communiqués et discours de la BCE (flux RSS officiel), sinon Google News."""
    out = {}
    try:
        out["communiques"] = T.lire_rss("https://www.ecb.europa.eu/rss/press.html", ttl_min=60, n=8,
                                        agents=(T.UA_SCRIPT, T.UA_NAVIGATEUR))
        noter("BCE (RSS)", True, "flux officiel")
    except Exception:
        out["communiques"] = T.lire_rss("https://news.google.com/rss/search",
                                        {"q": "ECB Lagarde when:3d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        noter("BCE (RSS)", True, "via Google News (flux officiel indisponible)")
    return out


def collecter_eur():
    data = {"yahoo": {}, "fred": {}, "bce": {}, "cot": None, "cal": [], "news": {}, "zq": {}, "intraday": None,
            "fed_off": {}, "tresor": {}, "nyfed": {}, "intermarche": None, "gld": None, "courbe": [], "adjudic": None,
            "oanda": None, "xaus": None}
    taches = {
        "yahoo": (charger_yahoo_eur, "Yahoo Finance"),
        "fred": (T.charger_fred, "FRED (Fed de St. Louis)"),
        "bce": (charger_bce, "BCE (taux zone euro)"),
        "cot": (charger_cot_eur, "CFTC (COT euro)"),
        "cal": (charger_calendrier_eur, "ForexFactory (calendrier)"),
        "news": (T.charger_news, "Google News"),
        "fed_off": (charger_bce_officiel, "BCE (RSS)"),
        "intraday": (charger_intraday_eur, "Yahoo intraday"),
        "zq": (T.charger_zq, "Futures fed funds"),
        "tresor": (T.charger_tresor, "Trésor américain (courbes)"),
        "nyfed": (T.charger_nyfed, "Fed de New York (EFFR, SOFR)"),
        "intermarche": (charger_intermarche_eur, "Yahoo intermarché 15 min"),
        "devises": (T.charger_devises, "Yahoo devises"),
    }
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(fn): (cle, nom) for cle, (fn, nom) in taches.items()}
        for f in cf.as_completed(futs):
            cle, nom = futs[f]
            try:
                res = f.result()
                if res is not None:
                    data[cle] = res
            except Exception as e:
                noter(nom, False, (str(e) or type(e).__name__)[:160])
    T.completer_taux(data)
    fusionner_taux(data)
    return data


def fusionner_taux(data):
    """Ajoute aux séries US les séries européennes et les écarts de taux (en points de pourcentage)."""
    f, b = data["fred"], data.get("bce") or {}
    for k in ("eu1", "eu2", "eu10", "dfr", "estr"):
        if k in b:
            f[k] = b[k]
    us10 = f.get("us10f") if f.get("us10f") is not None else (data["yahoo"] or {}).get("us10")
    for nom, us, eu in (("ecart2", f.get("us2"), f.get("eu2")), ("ecart10", us10, f.get("eu10"))):
        if us is not None and eu is not None:
            d = pd.concat([us, eu], axis=1, join="inner").dropna()
            if len(d) > 10:
                f[nom] = (d.iloc[:, 0] - d.iloc[:, 1])
    # Attentes de politique monétaire à 1 an : taux à 1 an moins le taux au jour le jour (en points)
    for nom, un_an, jj in (("att_fed", f.get("us1"), f.get("effr")), ("att_bce", f.get("eu1"), f.get("estr"))):
        if un_an is not None and jj is not None:
            d = pd.concat([un_an, jj], axis=1, join="inner").dropna()
            if len(d) > 10:
                f[nom] = d.iloc[:, 0] - d.iloc[:, 1]
    if f.get("att_fed") is not None and f.get("att_bce") is not None:
        d = pd.concat([f["att_bce"], f["att_fed"]], axis=1, join="inner").dropna()
        if len(d) > 10:
            f["att_rel"] = d.iloc[:, 0] - d.iloc[:, 1]   # > 0 : la BCE est attendue plus dure que la Fed


# ---------------------------------------------------------------------------
# ANALYSE FONDAMENTALE DE L'EURO
# ---------------------------------------------------------------------------

def moteurs_liste_eur(y, f):
    """Les moteurs suivis : (clé, série, libellé, mode de variation, effet sur l'EUR/USD, unité)."""
    return [
        ("ecart2", f.get("ecart2"), "Écart de taux 2 ans (US − zone euro)", "pb", -1, ""),
        ("us2", f.get("us2"), "Taux US 2 ans", "pb", -1, "%"),
        ("eu2", f.get("eu2"), "Taux 2 ans zone euro", "pb", 1, "%"),
        ("dxy", y.get("dxy"), "Dollar (DXY)", "pct", -1, ""),
        ("stoxx", y.get("stoxx"), "Euro Stoxx 50", "pct", 1, ""),
        ("spx", y.get("spx"), "S&P 500", "pct", 0, ""),
        ("vix", y.get("vix"), "VIX", "abs", 0, ""),
        ("move", y.get("move"), "Volatilité obligataire (MOVE)", "abs", 0, ""),
        ("ttf", y.get("ttf"), "Gaz européen (TTF)", "pct", -1, ""),
        ("brent", y.get("brent"), "Brent", "pct", -1, "$"),
        ("usdjpy", y.get("usdjpy"), "USD/JPY", "pct", 0, ""),
        ("gbpusd", y.get("gbpusd"), "GBP/USD", "pct", 0, ""),
        ("gold", y.get("gold"), "Or", "pct", 0, "$"),
        ("evz", y.get("evz"), "Volatilité de l'euro (EVZ)", "abs", 0, ""),
    ]


T.moteurs_liste = moteurs_liste_eur


def dollar_hors_euro(y, n=5):
    """Le dollar face au yen, à la livre et au franc suisse : le dollar se renforce-t-il partout ?"""
    parts = []
    for cle, signe in (("usdjpy", 1), ("gbpusd", -1), ("usdchf", 1)):
        v = variation(y.get(cle), n)
        if v is not None:
            parts.append(signe * v)
    return sum(parts) / len(parts) if parts else None


def surprise_eur(cal):
    """Indice de surprise maison par devise : les données publiées sortent-elles au-dessus des prévisions ?"""
    res = {}
    for dev in ("USD", "EUR"):
        sens_l = []
        for e in cal or []:
            if e.get("devise", "USD") != dev:
                continue
            a_, p_ = T._nombre(e.get("reel")), T._nombre(e.get("prevision"))
            if a_ is None or p_ is None:
                continue
            s_ = 0 if a_ == p_ else (1 if a_ > p_ else -1)
            if re.search(r"unemployment|claims|jobless", e["titre"], re.I):
                s_ = -s_
            sens_l.append(s_)
        res[dev] = {"score": sum(sens_l) / len(sens_l), "n": len(sens_l)} if sens_l else None
    return res


def biais_eur(y, f, cot, risque, surp):
    S, sig = SEUILS_EUR, []
    sg = T.signal

    d = variation(f.get("ecart2"), 5, "pb")
    if d is not None:
        sc = 1 if d <= -S["ecart2_pb"] else (-1 if d >= S["ecart2_pb"] else 0)
        sig.append(sg("Écart de taux 2 ans (US − zone euro)", sc, f"{nb(derniere(f['ecart2']), 2)} pt, {nb(d, 0, True)} pb sur 5 j",
                      {1: "L'écart se resserre : l'euro devient relativement plus rémunérateur",
                       -1: "L'écart se creuse en faveur du dollar", 0: "Écart de taux stable"}[sc]))
    d = variation(f.get("ecart10"), 5, "pb")
    if d is not None:
        sc = 1 if d <= -S["ecart10_pb"] else (-1 if d >= S["ecart10_pb"] else 0)
        sig.append(sg("Écart de taux 10 ans", sc, f"{nb(d, 0, True)} pb sur 5 j",
                      {1: "Les taux longs européens rattrapent les américains", -1: "Les taux longs US s'écartent",
                       0: "Écart long stable"}[sc]))
    d = variation(f.get("att_rel"), 10, "pb")
    if d is not None:
        sc = 1 if d >= S["attentes_pb"] else (-1 if d <= -S["attentes_pb"] else 0)
        ab, af = derniere(f.get("att_bce")), derniere(f.get("att_fed"))
        sig.append(sg("Attentes BCE face à la Fed", sc,
                      f"sur 1 an : BCE {nb((ab or 0) * 100, 0, True)} pb, Fed {nb((af or 0) * 100, 0, True)} pb ; {nb(d, 0, True)} pb en 10 j",
                      {1: "Le marché voit la BCE plus dure (ou la Fed plus souple) qu'il y a deux semaines",
                       -1: "Le marché voit la Fed plus dure (ou la BCE plus souple)", 0: "Attentes relatives stables"}[sc]))
    t5, b5 = variation(y.get("ttf"), 5), variation(y.get("brent"), 5)
    if t5 is not None or b5 is not None:
        hausse = (t5 or 0) >= S["ttf_pct"] or (b5 or 0) >= S["brent_pct"]
        baisse = (t5 or 0) <= -S["ttf_pct"] or (b5 or 0) <= -S["brent_pct"]
        sc = -1 if hausse and not baisse else (1 if baisse and not hausse else 0)
        sig.append(sg("Énergie (gaz TTF et Brent)", sc, f"gaz {nb(t5, 1, True)} %, Brent {nb(b5, 1, True)} % sur 5 j",
                      {-1: "Énergie en hausse : facture plus lourde pour la zone euro", 1: "Énergie en baisse : soulagement pour la zone euro",
                       0: "Énergie sans mouvement marqué"}[sc]))
    if risque:
        sc = 1 if risque["etat"] == "Risk-on" else (-1 if risque["etat"] == "Risk-off" else 0)
        sig.append(sg("Appétit pour le risque", sc, f"{risque['etat']} (score {nb(risque['score'], 0, True)})",
                      {1: "Les investisseurs prennent du risque : l'euro, devise pro-cyclique, en profite",
                       -1: "Recherche de sécurité : le dollar est recherché", 0: "Pas de mouvement de fond"}[sc]))
    d = dollar_hors_euro(y)
    if d is not None:
        sc = -1 if d >= S["usd_pct"] else (1 if d <= -S["usd_pct"] else 0)
        sig.append(sg("Dollar face aux autres devises", sc, f"{nb(d, 2, True)} % sur 5 j (yen, livre, franc)",
                      {-1: "Le dollar se renforce partout : vent contraire pour l'euro",
                       1: "Le dollar faiblit partout : vent porteur pour l'euro", 0: "Pas de mouvement global du dollar"}[sc]))
    if cot and cot.get("pct3a") is not None:
        p = cot["pct3a"]
        sc = -1 if p >= S["cot_haut"] else (1 if p <= S["cot_bas"] else 0)
        sig.append(sg("Positionnement des fonds (COT euro)", sc, f"percentile 3 ans : {nb(p, 0)}",
                      {-1: "Fonds très chargés à l'achat d'euro : peu d'acheteurs restants, risque de débouclage",
                       1: "Fonds très vendeurs d'euro : un rachat peut accélérer la hausse", 0: "Positionnement sans excès"}[sc]))
    su_e, su_u = (surp or {}).get("EUR"), (surp or {}).get("USD")
    if su_e and su_u:
        d = su_e["score"] - su_u["score"]
        sc = 1 if d >= S["surprise"] else (-1 if d <= -S["surprise"] else 0)
        sig.append(sg("Surprises économiques (zone euro − US)", sc, f"{nb(d, 2, True)} ({su_e['n']} EUR, {su_u['n']} USD)",
                      {1: "Les chiffres européens surprennent mieux que les américains",
                       -1: "Les chiffres américains surprennent mieux", 0: "Surprises équilibrées"}[sc]))
    total = sum(s_["score"] for s_ in sig)
    verdict = "Haussier" if total >= 2 else ("Baissier" if total <= -2 else "Neutre")
    return {"signaux": sig, "total": total, "n": len(sig), "verdict": verdict}


def decomp_eur(y, f):
    """Régression sur 60 jours : variation de l'EUR/USD expliquée par l'écart de taux, les actions européennes et le gaz."""
    series = {"ecart2": (f.get("ecart2"), "pb"), "stoxx": (y.get("stoxx"), "pct"), "ttf": (y.get("ttf"), "pct")}
    series = {k: v for k, v in series.items() if v[0] is not None}
    if y.get("or") is None or "ecart2" not in series:
        return None
    cols = {"or": changements(y["or"], "pct")}
    for k, (s, mode) in series.items():
        cols[k] = changements(s, mode)
    df = pd.concat(cols, axis=1, join="inner").dropna()
    if len(df) < 50:
        return None
    hist = df.iloc[-61:-1]
    fact = list(series)
    A = np.column_stack([np.ones(len(hist))] + [hist[k].values for k in fact])
    coef, *_ = np.linalg.lstsq(A, hist["or"].values, rcond=None)
    pred = A @ coef
    ss_tot = float(((hist["or"] - hist["or"].mean()) ** 2).sum())
    r2 = 1 - float(((hist["or"] - pred) ** 2).sum()) / ss_tot if ss_tot else 0.0

    def bilan(l):
        contrib = {k: float(coef[i + 1] * l[k].sum()) for i, k in enumerate(fact)}
        reel = float(l["or"].sum())
        att = sum(contrib.values())
        return {"contrib": contrib, "attendu": att, "reel": reel, "residu": reel - att}

    return {"r2": r2, "betas": {k: float(coef[i + 1]) for i, k in enumerate(fact)},
            "jour": bilan(df.iloc[-1:]), "semaine": bilan(df.iloc[-5:]), "date": df.index[-1]}


def betas_intraday(y, f):
    """Sensibilités quotidiennes de l'EUR/USD au taux US 2 ans et à l'Euro Stoxx (60 jours), appliquées en intraday."""
    if y.get("or") is None or f.get("us2") is None:
        return None
    cols = {"or": changements(y["or"], "pct"), "us2": changements(f["us2"], "pb")}
    if y.get("stoxx") is not None:
        cols["stoxx"] = changements(y["stoxx"], "pct")
    df = pd.concat(cols, axis=1, join="inner").dropna().tail(60)
    if len(df) < 40:
        return None
    fact = [k for k in cols if k != "or"]
    A = np.column_stack([np.ones(len(df))] + [df[k].values for k in fact])
    coef, *_ = np.linalg.lstsq(A, df["or"].values, rcond=None)
    return {k: float(coef[i + 1]) for i, k in enumerate(fact)}


def confirmation_eur(im, betas):
    """L'euro suit-il ce que le taux US 2 ans et les actions européennes lui dictent ? (dernière heure, 4 heures)"""
    if im is None or "6E=F" not in im or not betas:
        return None
    g = im["6E=F"].dropna()
    if len(g) < 20:
        return None
    t1 = g.index[-1]
    out = []
    for lib, n, seuil in (("Dernière heure", 4, 0.05), ("4 dernières heures", 16, 0.10)):
        t0 = t1 - pd.Timedelta(minutes=15 * n)
        if g.index[0] > t0:
            continue
        ligne = {"lib": lib, "or": (float(g.iloc[-1]) / float(g.asof(t0)) - 1) * 100}
        attendu, morceaux = 0.0, []
        for col, cle, mode, lib_m in (("US2Y", "us2", "pb", "US 2 ans"), ("^STOXX50E", "stoxx", "pct", "Euro Stoxx")):
            v = T._mv(im[col], t0, t1, mode) if col in im and cle in betas else None
            ligne[cle] = v
            if v is not None:
                attendu += betas[cle] * v
                morceaux.append(f"{lib_m} {nb(v, 1 if mode == 'pb' else 2, True)} {'pb' if mode == 'pb' else '%'}")
        if not morceaux:
            continue
        ligne["attendu"], ligne["residu"] = attendu, ligne["or"] - attendu
        ligne["signal"] = "force" if ligne["residu"] >= seuil else ("faiblesse" if ligne["residu"] <= -seuil else "neutre")
        ligne["texte"] = (f"{lib} : {', '.join(morceaux)}, les moteurs poussaient l'EUR/USD de {nb(attendu, 2, True)} % ; "
                          f"il a fait {nb(ligne['or'], 2, True)} %."
                          + {"force": " Force relative de l'euro : des acheteurs sont là au-delà des moteurs.",
                             "faiblesse": " Faiblesse relative : l'euro ne profite pas de ses moteurs, des vendeurs pèsent.",
                             "neutre": " L'euro suit ses moteurs."}[ligne["signal"]])
        out.append(ligne)
    return {"lignes": out, "maj": t1} if out else None


def intermarket_eur(a, data):
    """L'EUR/USD face au taux US 2 ans / à l'écart de taux, aux actions européennes et au gaz : CONFIRMED, MIXED,
    DIVERGENCE par horizon."""
    im, y, f = data.get("intermarche"), data.get("yahoo") or {}, data.get("fred") or {}
    lignes = []
    seuils = {"1 h": (0.04, 1.0, 0.15, None), "4 h": (0.08, 2.0, 0.30, None),
              "1 jour": (0.15, 2.0, 0.40, 3.0), "5 jours": (0.40, 5.0, 1.0, 8.0)}
    for hz, (s_eur, s_taux, s_act, s_gaz) in seuils.items():
        if hz in ("1 h", "4 h"):
            if im is None or "6E=F" not in im:
                continue
            g = im["6E=F"].dropna()
            if len(g) < 20:
                continue
            t1 = g.index[-1]
            t0 = t1 - pd.Timedelta(hours=1 if hz == "1 h" else 4)
            mv_eur = T._mv(g, t0, t1, "pct")
            mv_taux = T._mv(im.get("US2Y"), t0, t1, "pb") if "US2Y" in im else None
            mv_act = T._mv(im.get("^STOXX50E"), t0, t1, "pct") if "^STOXX50E" in im else None
            mv_gaz, lib_taux = None, "US 2 ans"
        else:
            n = 1 if hz == "1 jour" else 5
            mv_eur = variation(y.get("or"), n)
            mv_taux, lib_taux = variation(f.get("ecart2"), n, "pb"), "Écart 2 ans"
            mv_act, mv_gaz = variation(y.get("stoxx"), n), variation(y.get("ttf"), n)
        if mv_eur is None:
            continue
        impl = [0 if (mv_taux is None or abs(mv_taux) < s_taux) else (-1 if mv_taux > 0 else 1),
                0 if (mv_act is None or abs(mv_act) < s_act) else (1 if mv_act > 0 else -1),
                0 if (mv_gaz is None or s_gaz is None or abs(mv_gaz) < s_gaz) else (-1 if mv_gaz > 0 else 1)]
        actifs = [x for x in impl if x]
        consensus = actifs[0] if actifs and all(x == actifs[0] for x in actifs) else 0
        g_sens = 0 if abs(mv_eur) < s_eur else (1 if mv_eur > 0 else -1)
        if consensus and g_sens == consensus:
            statut, lect = "CONFIRMED", ("L'euro monte avec des moteurs favorables : mouvement sain." if consensus > 0 else
                                         "L'euro baisse avec des moteurs défavorables : mouvement sain.")
        elif consensus and g_sens == -consensus:
            statut, lect = "DIVERGENCE", ("Les moteurs poussent l'euro à la hausse mais il baisse : flux vendeurs de fond."
                                          if consensus > 0 else
                                          "Les moteurs pèsent sur l'euro mais il monte : flux acheteurs de fond.")
        elif consensus:
            statut, lect = "MIXED", "Les moteurs donnent une direction mais l'euro ne réagit pas encore."
        else:
            statut, lect = "MIXED", ("Moteurs partagés : pas de signal intermarché." if actifs else "Moteurs sans mouvement notable.")
        lignes.append({"horizon": hz, "eur": mv_eur, "taux": mv_taux, "lib_taux": lib_taux, "act": mv_act, "gaz": mv_gaz,
                       "impl": impl, "consensus": consensus, "sens_eur": g_sens, "statut": statut, "lecture": lect})
    if not lignes:
        return None
    poids = {"1 h": 1, "4 h": 2, "1 jour": 2, "5 jours": 1}
    sc = {"CONFIRMED": 0, "DIVERGENCE": 0, "MIXED": 0}
    for l in lignes:
        sc[l["statut"]] += poids[l["horizon"]]
    glob = max(sc, key=sc.get)
    sens_g = next((l["consensus"] for l in lignes if l["statut"] == "CONFIRMED"), 0) if glob == "CONFIRMED" else 0
    return {"lignes": lignes, "statut": glob, "sens": sens_g}


def regime_eur(y, f, corr, risque):
    c = {x["cle"]: x["c20"] for x in corr}
    ttf20 = variation(y.get("ttf"), 20)
    if (ttf20 is not None and ttf20 >= 25) or (c.get("ttf") or 0) <= -0.45:
        return {"nom": "Choc énergétique", "ton": "down",
                "resume": "Le prix du gaz et du pétrole pilote l'euro.",
                "explication": "La zone euro importe l'essentiel de son énergie : quand elle flambe, la balance commerciale "
                               "et la croissance européennes se dégradent, et l'euro baisse avec.",
                "consequence": "Surveille le gaz TTF et le Brent en priorité : leurs hausses sont des signaux de vente sur l'euro."}
    if risque and risque["score"] <= -40 and (c.get("stoxx") or 0) >= 0.35:
        return {"nom": "Aversion au risque", "ton": "down",
                "resume": "La peur sur les marchés profite au dollar.",
                "explication": "En période de stress, les investisseurs rapatrient leurs capitaux vers le dollar, valeur refuge. "
                               "L'euro suit alors les actions européennes à la baisse.",
                "consequence": "Les rebonds de l'euro sont fragiles tant que les actions chutent ; surveille le VIX et l'Euro Stoxx."}
    if abs(c.get("ecart2") or 0) >= 0.4 or abs(c.get("us2") or 0) >= 0.45:
        return {"nom": "Écart de taux aux commandes", "ton": "flat",
                "resume": "L'euro suit l'écart de taux avec les États-Unis.",
                "explication": "Le régime classique des devises : l'argent va là où il est le mieux rémunéré. Quand l'écart de "
                               "taux à 2 ans se resserre, l'euro monte ; quand il se creuse en faveur du dollar, l'euro baisse.",
                "consequence": "Le taux US 2 ans et les annonces de la Fed et de la BCE sont tes indicateurs prioritaires."}
    if abs(c.get("usdjpy") or 0) >= 0.5 or abs(c.get("gbpusd") or 0) >= 0.6:
        return {"nom": "Dollar global", "ton": "flat",
                "resume": "Le dollar bouge face à toutes les devises en même temps.",
                "explication": "Le mouvement vient du dollar lui-même (Fed, flux mondiaux) plutôt que de l'Europe : l'euro, la "
                               "livre et le yen bougent ensemble face à lui.",
                "consequence": "Les annonces américaines comptent plus que les européennes ; surveille le dollar index."}
    return {"nom": "Sans moteur dominant", "ton": "flat",
            "resume": "Aucun facteur ne domine : marché de range probable.",
            "explication": "Les corrélations avec les taux, les actions et l'énergie sont faibles : l'euro oscille au gré des "
                           "flux de court terme.",
            "consequence": "Privilégie les extrémités du range et les niveaux techniques ; les cassures sans catalyseur échouent souvent."}


def regimes6_eur(a, data):
    """Six forces qui pilotent l'EUR/USD, avec leur intensité (0-100) et leur effet sur l'euro."""
    y, f = data.get("yahoo") or {}, data.get("fred") or {}
    c = {x["cle"]: x["c20"] for x in a.get("correlations") or []}
    out = []
    d = variation(f.get("ecart2"), 5, "pb")
    if f.get("ecart2") is not None:
        out.append({"nom": "Écart de taux", "intensite": clamp(abs(c.get("ecart2") or 0) * 160, 0, 100),
                    "effet": 0 if d is None or abs(d) < 3 else (-1 if d > 0 else 1),
                    "etat": f"écart 2 ans {nb(derniere(f['ecart2']), 2)} pt, {nb(d, 0, True)} pb sur 5 j ; corrélation 20 j {nb(c.get('ecart2'), 2, True)}"})
    d = variation(f.get("att_rel"), 10, "pb")
    if d is not None:
        out.append({"nom": "BCE face à la Fed", "intensite": clamp(abs(d) * 4, 0, 100), "effet": 0 if abs(d) < 5 else (1 if d > 0 else -1),
                    "etat": f"attentes relatives à 1 an {nb(d, 0, True)} pb en 10 j"})
    r = a.get("risque")
    if r:
        out.append({"nom": "Appétit pour le risque", "intensite": clamp(abs(r["score"]), 0, 100),
                    "effet": 1 if r["etat"] == "Risk-on" else (-1 if r["etat"] == "Risk-off" else 0),
                    "etat": f"{r['etat']} (score {nb(r['score'], 0, True)}), corrélation Euro Stoxx {nb(c.get('stoxx'), 2, True)}"})
    t20, b20 = variation(y.get("ttf"), 20), variation(y.get("brent"), 20)
    if t20 is not None or b20 is not None:
        sc = (t20 or 0) / 20 + (b20 or 0) / 10
        out.append({"nom": "Énergie", "intensite": clamp(abs(sc) * 50, 0, 100), "effet": 0 if abs(sc) < 0.5 else (-1 if sc > 0 else 1),
                    "etat": f"gaz TTF {nb(t20, 1, True)} %, Brent {nb(b20, 1, True)} % sur 20 j"})
    d = dollar_hors_euro(y)
    if d is not None:
        out.append({"nom": "Dollar global", "intensite": clamp(abs(d) * 80, 0, 100), "effet": 0 if abs(d) < 0.3 else (-1 if d > 0 else 1),
                    "etat": f"dollar face au yen, à la livre et au franc : {nb(d, 2, True)} % sur 5 j"})
    hy, nfci, move, vix = derniere(f.get("hy")), derniere(f.get("nfci")), derniere(y.get("move")), derniere(y.get("vix"))
    pts = [clamp((hy - 3.0) / 3.0, 0, 1) if hy is not None else None, clamp((nfci + 0.3) / 0.8, 0, 1) if nfci is not None else None,
           clamp((vix - 15) / 20, 0, 1) if vix is not None else None, clamp((move - 90) / 60, 0, 1) if move is not None else None]
    pts = [p for p in pts if p is not None]
    if pts:
        st = sum(pts) / len(pts) * 100
        out.append({"nom": "Stress financier", "intensite": st, "effet": 2 if st >= 50 else 0,
                    "etat": f"spread HY {nb(hy, 2)} %, VIX {nb(vix, 1)}, MOVE {nb(move, 0)}"
                            + (" : stress élevé, le dollar refuge est recherché" if st >= 50 else "")})
    cot = a.get("cot")
    if cot and cot.get("pct3a") is not None:
        p = cot["pct3a"]
        out.append({"nom": "Positionnement", "intensite": clamp(abs(p - 50) * 2, 0, 100),
                    "effet": -1 if p >= 85 else (1 if p <= 15 else 0), "etat": f"fonds spéculatifs, percentile 3 ans {nb(p, 0)}"})
    if not out:
        return None
    return {"dimensions": out, "dominant": max(out, key=lambda x: x["intensite"])}


# ---------------------------------------------------------------------------
# ANNONCES : TYPES, FICHES, RÉACTIONS (USD et EUR)
# ---------------------------------------------------------------------------

_type_annonce_us = T.type_annonce


def type_annonce_eur(titre):
    if titre.startswith("EUR · "):
        for motif, nom in ((r"main refinancing|deposit facility|monetary policy statement|ecb.*rate", "Décision de la BCE"),
                           (r"press conference|lagarde", "Conférence de la BCE"),
                           (r"german.*cpi", "Inflation allemande"), (r"cpi|hicp|inflation", "Inflation zone euro"),
                           (r"pmi", "PMI zone euro"), (r"ifo", "Ifo allemand"), (r"zew", "ZEW"),
                           (r"gdp", "PIB zone euro"), (r"unemployment", "Chômage zone euro"),
                           (r"retail sales", "Ventes au détail zone euro")):
            if re.search(motif, titre, re.I):
                return nom
        return None
    return _type_annonce_us(titre)


T.type_annonce = type_annonce_eur

KB_EUR = [
    (r"main refinancing|deposit facility|monetary policy statement|ecb.*rate", "Décision de la BCE", "bce", "EUR",
     "La décision de taux de la Banque centrale européenne, à 14 h 15, suivie de la conférence de presse à 14 h 45."),
    (r"press conference|lagarde", "Conférence de la BCE", "bce", "EUR",
     "Le ton de la présidente fait souvent plus bouger l'euro que la décision elle-même."),
    (r"german.*cpi", "Inflation allemande", "inflation", "EUR",
     "Publiée avant le chiffre de la zone euro : elle donne le ton pour la BCE."),
    (r"cpi|hicp|inflation", "Inflation zone euro", "inflation", "EUR",
     "L'inflation que vise la BCE à 2 % ; l'estimation rapide (flash) fait bouger l'euro le plus fort."),
    (r"pmi", "PMI zone euro", "activite", "EUR", "L'enquête d'activité auprès des entreprises, premier signal sur la croissance du mois."),
    (r"ifo", "Ifo allemand", "activite", "EUR", "Le moral des entreprises allemandes, première économie de la zone euro."),
    (r"zew", "ZEW", "activite", "EUR", "Le moral des investisseurs sur l'économie allemande."),
    (r"gdp", "PIB zone euro", "activite", "EUR", "La croissance de la zone euro sur le trimestre."),
    (r"unemployment", "Chômage zone euro", "chomage", "EUR", "Le marché du travail européen."),
    (r"retail sales", "Ventes au détail zone euro", "activite", "EUR", "La consommation des ménages européens."),
]

REACTIONS_EUR = {
    ("USD", "inflation"): ("Au-dessus de la prévision : la Fed doit rester dure, le dollar monte, l'EUR/USD baisse.",
                           "En dessous : moins de pression sur la Fed, le dollar recule, l'EUR/USD monte."),
    ("USD", "activite"): ("Au-dessus : économie américaine solide, dollar plus fort, l'EUR/USD baisse.",
                          "En dessous : le marché anticipe une Fed plus souple, l'EUR/USD monte."),
    ("USD", "chomage"): ("Au-dessus : l'économie américaine ralentit, Fed plus souple attendue, l'EUR/USD monte.",
                         "En dessous : marché du travail solide, dollar plus fort, l'EUR/USD baisse."),
    ("USD", "fed"): ("Ton plus dur que prévu : le dollar monte, l'EUR/USD baisse.",
                     "Ton plus souple : le dollar recule, l'EUR/USD monte."),
    ("USD", "adjudication"): ("Demande faible : taux US en hausse, dollar plutôt soutenu.", "Demande solide : taux US en baisse."),
    ("EUR", "inflation"): ("Au-dessus de la prévision : la BCE peut rester dure plus longtemps, les taux européens montent, "
                           "l'EUR/USD monte.", "En dessous : la BCE a de la marge pour assouplir, l'EUR/USD baisse."),
    ("EUR", "activite"): ("Au-dessus : la croissance européenne tient, l'euro est recherché, l'EUR/USD monte.",
                          "En dessous : inquiétude sur l'économie européenne, l'EUR/USD baisse."),
    ("EUR", "chomage"): ("Au-dessus : l'économie européenne ralentit, l'EUR/USD baisse.",
                         "En dessous : marché du travail solide, l'EUR/USD monte."),
    ("EUR", "bce"): ("Ton plus dur que prévu (inflation jugée persistante, pas de baisse en vue) : l'EUR/USD monte.",
                     "Ton plus souple (baisse de taux évoquée, croissance fragile) : l'EUR/USD baisse."),
}


def fiche_eur(e, a):
    titre = e["titre"]
    dev = e.get("devise") or ("EUR" if titre.startswith("EUR · ") else "USD")
    if dev == "EUR":
        kb = next((k for k in KB_EUR if re.search(k[0], titre, re.I)), None)
        nom, typ, pourquoi = (kb[1], kb[2], kb[4]) if kb else (titre.replace("EUR · ", "") + " (zone euro)", None, "")
    else:
        kb = next((k for k in T.KB_ANNONCES if re.search(k[0], titre, re.I)), None)
        nom, typ, pourquoi = (kb[1], kb[2], kb[3]) if kb else (titre, None, "")
        nom = nom + " (US)" if kb else nom
    haut, bas = REACTIONS_EUR.get((dev, typ), ("", ""))
    nuances = []
    fw = a.get("fedwatch")
    if dev == "USD" and fw and fw["reunions"] and typ in ("inflation", "activite", "chomage", "fed"):
        r0 = fw["reunions"][0]
        if r0["p_hausse"] >= 0.5:
            nuances.append(f"Le marché price déjà {nb(r0['p_hausse'] * 100, 0)} % de chances de hausse de la Fed le "
                           f"{date_fr(r0['date'])} : une surprise souple aurait plus d'impact sur l'EUR/USD qu'un chiffre fort.")
        elif r0["p_baisse"] >= 0.5:
            nuances.append(f"Le marché price déjà {nb(r0['p_baisse'] * 100, 0)} % de chances de baisse de la Fed le "
                           f"{date_fr(r0['date'])} : un chiffre fort surprendrait davantage.")
    b = a.get("bce") or {}
    if dev == "EUR" and b.get("att_bce") is not None and typ in ("inflation", "activite", "bce"):
        att = b["att_bce"]
        if att >= 15:
            nuances.append(f"Le marché attend déjà une BCE plus dure ({nb(att, 0, True)} pb sur un an) : une surprise souple "
                           f"aurait plus d'impact.")
        elif att <= -15:
            nuances.append(f"Le marché attend déjà des baisses de la BCE ({nb(att, 0, True)} pb sur un an) : une surprise dure "
                           f"aurait plus d'impact.")
    reg = a.get("regime") or {}
    if reg.get("nom") == "Écart de taux aux commandes":
        nuances.append("Dans le régime actuel, l'euro suit de près les taux : attends-toi à une réaction franche.")
    elif reg.get("nom") == "Choc énergétique" and dev == "EUR":
        nuances.append("En choc énergétique, une inflation européenne élevée inquiète plus qu'elle ne rassure : la réaction "
                       "peut être plus faible que d'habitude.")
    histo = ""
    r = next((x for x in a.get("reactions") or [] if x["type"] == type_annonce_eur(titre)), None)
    if r:
        histo = (f"Historique ({r['n']} publications) : amplitude médiane de {nb(r['med_rng'], 0)} pips dans l'heure, "
                 f"premier mouvement retourné dans {nb(r['retour_pct'], 0)} % des cas.")
    return {"titre": titre, "nom": nom, "type": typ, "devise": dev, "pourquoi": pourquoi, "haut": haut, "bas": bas,
            "nuances": nuances, "date": e["date"], "prevision": e["prevision"], "precedent": e["precedent"],
            "amplitude": a.get("range_jour"), "histo": histo}


T.fiche_annonce = fiche_eur


def reactions_evt_eur(a, data):
    """Après chaque annonce majeure publiée : consensus, réel, surprise, réaction de l'EUR/USD, du dollar index et du
    taux US 2 ans, puis interprétation."""
    im = data.get("intermarche")
    out = []
    for e in data.get("cal") or []:
        if e["impact"] != "High" or not e.get("reel"):
            continue
        dev = e.get("devise", "USD")
        typ = (next((k[2] for k in KB_EUR if re.search(k[0], e["titre"], re.I)), None) if dev == "EUR"
               else T.type_reaction(e["titre"]))
        if not typ:
            continue
        reel, prev = T._nombre(e["reel"]), T._nombre(e["prevision"])
        surprise = (reel - prev) if (reel is not None and prev is not None) else None
        sens_s = 0 if not surprise else (1 if surprise > 0 else -1)
        if typ == "chomage":
            sens_s = -sens_s
        attendu = sens_s if dev == "EUR" else -sens_s  # surprise « dure » : euro en hausse si européenne, en baisse si américaine
        t = pd.Timestamp(e["date"])
        reac = {}
        if im is not None:
            for cle, col, mode in (("eur", "6E=F", "pct"), ("dxy", "DX-Y.NYB", "pct"), ("us2", "US2Y", "pb")):
                s = im[col] if col in im else None
                reac[cle + "_15"] = T._mv(s, t - pd.Timedelta(minutes=1), t + pd.Timedelta(minutes=15), mode) if s is not None else None
                reac[cle + "_60"] = T._mv(s, t - pd.Timedelta(minutes=1), t + pd.Timedelta(minutes=60), mode) if s is not None else None
            if "6E=F" in im and reac.get("eur_60") is not None:
                g0 = float(im["6E=F"].dropna().asof(t - pd.Timedelta(minutes=1)))
                reac["eur_pips_60"] = g0 * reac["eur_60"] / 100
        reac["or_60"], reac["or_usd_60"] = reac.get("eur_60"), reac.get("eur_pips_60")   # noms lus par le moteur commun
        mv = reac.get("eur_60")
        if mv is None:
            interp = "Réaction pas encore mesurable (moins d'une heure, ou données intraday indisponibles)."
        elif sens_s == 0:
            interp = "Chiffre conforme au consensus : la réaction vient du détail ou des révisions."
        elif abs(mv) < 0.05:
            interp = "Surprise sans réaction de l'euro : l'information était déjà dans les prix, ou un autre moteur domine."
        elif (mv > 0) == (attendu > 0):
            interp = "Réaction conforme à la surprise : le marché a validé le chiffre."
        else:
            interp = ("Réaction inverse à la surprise : l'EUR/USD part à contre-sens du chiffre. Signe d'un positionnement "
                      "déjà chargé ou d'un autre moteur dominant, à surveiller.")
        nom = fiche_eur(e, a)["nom"]
        out.append({"titre": e["titre"], "nom": nom, "devise": dev, "date": e["date"], "prevision": e["prevision"], "reel": e["reel"],
                    "surprise": surprise, "sens_surprise": sens_s, "reac": reac, "interpretation": interp})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:8]


# ---------------------------------------------------------------------------
# ASSEMBLAGE DE L'ANALYSE
# ---------------------------------------------------------------------------

def prochaine_bce():
    now = maintenant().date()
    for d in BCE:
        jd = date.fromisoformat(d)
        if jd >= now:
            return jd
    return None


def troisieme_mercredi(annee, mois):
    d = date(annee, mois, 15)
    return d + timedelta(days=(2 - d.weekday()) % 7)


def ecart_future_spot_eur(data, f):
    """Écart entre le future euro CME et l'EUR/USD spot : mesuré au même instant si possible, sinon estimé par
    l'écart de taux entre le dollar (SOFR) et l'euro (€STR) jusqu'à l'échéance."""
    intra = data.get("intraday") or {}
    b, sp = intra.get("barres"), intra.get("spot15")
    if b is None or b.empty:
        return None
    ref, t = float(b["close"].iloc[-1]), b.index[-1]
    if sp is not None and len(sp) > 10 and sp.index[0] <= t:
        der = sp[sp.index <= t]
        if len(der) and (t - der.index[-1]) <= pd.Timedelta(minutes=30):
            base = ref - float(der.iloc[-1])
            if abs(base) < ref * 0.02:
                return {"base": base, "contrat": "mesuré au même instant (EUR/USD spot)", "mesure": True}
    sofr, estr = derniere(f.get("sofr")), derniere(f.get("estr"))
    if sofr is None or estr is None:
        return None
    auj = maintenant().date()
    ech = None
    for an in (auj.year, auj.year + 1):
        for m in (3, 6, 9, 12):
            d = troisieme_mercredi(an, m) - timedelta(days=2)
            if d > auj:
                ech = d
                break
        if ech:
            break
    jours = (ech - auj).days
    spot = ref / (1 + (sofr - estr) / 100 * jours / 360)
    return {"base": ref - spot, "contrat": f"estimé ({jours} j jusqu'à l'échéance, SOFR − €STR)", "mesure": False}


def analyser_eur(data):
    y, f = data["yahoo"], data["fred"]
    a = {"mouvements": T.mouvements(y, f), "liquidite": None, "structure": None, "gld": None, "carnets": None,
         "adjudic": None, "devises": [], "ratios": [], "divergence": None}
    or_s = y.get("or")
    a["or"] = {"prix": derniere(or_s), "d1": variation(or_s, 1), "d5": variation(or_s, 5), "d20": variation(or_s, 20),
               "sma50": float(or_s.tail(50).mean()) if or_s is not None and len(or_s) >= 50 else None,
               "sma200": float(or_s.tail(200).mean()) if or_s is not None and len(or_s) >= 200 else None,
               "haut52": float(or_s.tail(252).max()) if or_s is not None else None,
               "bas52": float(or_s.tail(252).min()) if or_s is not None else None}
    evz = derniere(y.get("evz"))
    a["range_jour"] = (a["or"]["prix"] * evz / 100 / math.sqrt(252)) if evz and a["or"]["prix"] else None

    paires = [("ecart2", "Écart 2 ans US − zone euro", f.get("ecart2"), "diff"), ("us2", "Taux US 2 ans", f.get("us2"), "diff"),
              ("eu2", "Taux 2 ans zone euro", f.get("eu2"), "diff"), ("dxy", "Dollar index", y.get("dxy"), "pct"),
              ("stoxx", "Euro Stoxx 50", y.get("stoxx"), "pct"), ("ttf", "Gaz TTF", y.get("ttf"), "pct"),
              ("brent", "Brent", y.get("brent"), "pct"), ("gold", "Or", y.get("gold"), "pct"),
              ("usdjpy", "USD/JPY", y.get("usdjpy"), "pct"), ("gbpusd", "GBP/USD", y.get("gbpusd"), "pct"),
              ("vix", "VIX", y.get("vix"), "diff")]
    a["correlations"] = [{"cle": k, "nom": n, "c20": T.correlation(or_s, s, m, 20), "c60": T.correlation(or_s, s, m, 60)}
                         for k, n, s, m in paires if s is not None]
    valides = [c for c in a["correlations"] if c["c20"] is not None and c["cle"] not in ("vix", "dxy")]
    a["moteur"] = max(valides, key=lambda c: abs(c["c20"])) if valides else None
    a["risque"] = T.jauge_risque(a, f)
    a["regime"] = regime_eur(y, f, a["correlations"], a["risque"])
    a["decomp"] = decomp_eur(y, f)
    a["cot"] = T.cot_etendu(data.get("cot"), or_s)
    if a["cot"] is not None:
        cot, groupes = data["cot"], []
        der, prec, h = cot.iloc[-1], cot.iloc[-2], cot.tail(156)
        for g, lib in (("am", "Gestionnaires d'actifs"), ("deal", "Banques (dealers)")):
            if g + "_net" in cot:
                groupes.append({"lib": lib, "net": float(der[g + "_net"]), "chg": float(der[g + "_net"] - prec[g + "_net"]),
                                "pct3a": percentile(h[g + "_net"], float(der[g + "_net"]))})
        a["cot"]["groupes"] = groupes
    a["surprise_dev"] = surprise_eur(data.get("cal"))
    a["surprise"] = None
    a["biais"] = biais_eur(y, f, a["cot"], a["risque"], a["surprise_dev"])

    ecart = ecart_future_spot_eur(data, f)
    a["ecart"] = ecart
    a["base"] = ecart["base"] if ecart else None
    T.AJUST = (-a["base"] if a["base"] is not None else 0.0) + T.DECALAGE_CFD
    a["seance"] = T.seance(data.get("intraday"))

    effr, us2 = derniere(f.get("effr")), derniere(f.get("us2"))
    a["fed"] = {"effr": effr, "us2": us2, "spread_pb": (us2 - effr) * 100 if effr is not None and us2 is not None else None}
    a["fedwatch"] = T.fedwatch(data.get("zq"), effr, derniere(f.get("cible_bas")), derniere(f.get("cible_haut")))
    pv = lambda s, n=5: variation(s, n, "pb")
    a["bce"] = {"dfr": derniere(f.get("dfr")), "estr": derniere(f.get("estr")), "eu2": derniere(f.get("eu2")),
                "eu10": derniere(f.get("eu10")), "eu2_d5": pv(f.get("eu2")), "us2_d5": pv(f.get("us2")),
                "ecart2": derniere(f.get("ecart2")), "ecart2_d5": pv(f.get("ecart2")), "ecart2_d20": pv(f.get("ecart2"), 20),
                "ecart10": derniere(f.get("ecart10")), "ecart10_d5": pv(f.get("ecart10")),
                "att_bce": (derniere(f.get("att_bce")) or 0) * 100 if f.get("att_bce") is not None else None,
                "att_fed": (derniere(f.get("att_fed")) or 0) * 100 if f.get("att_fed") is not None else None,
                "att_rel_d10": pv(f.get("att_rel"), 10), "prochaine": prochaine_bce()}
    r = changements(or_s, "pct").dropna().tail(20) if or_s is not None else None
    a["vol"] = {"realisee": float(r.std() * math.sqrt(252)) if r is not None and len(r) > 10 else None, "implicite": evz}
    a["macro"] = []
    a["barometre"] = T.barometre(data.get("news"))
    a["annonce"], a["zone_news"], a["minutes_annonce"] = T.prochaine_annonce(data.get("cal") or [])
    a["intermarche"] = confirmation_eur(data.get("intermarche"), betas_intraday(y, f))
    a["matrice"] = T.matrice_tendance(data.get("intraday"))
    a["technique"] = T.analyse_technique(a, data)
    a["playbook"] = T.playbook(a, data)
    a["profil_vol"] = T.profil_volatilite(data.get("intraday"))
    hist = data.setdefault("hist", {"previsions": [], "annonces": []})
    T.memoriser_annonces(hist, data.get("cal"))
    a["reactions"] = T.reactions_annonces(data.get("intraday"), hist)
    a["moteurs_jour"] = T.moteurs_du_jour(a)
    for m in a["moteurs_jour"]:
        m["effet"] = m["effet"].replace("l'or", "l'euro")
    a["point"] = point_eur(a)
    now = maintenant()
    a["fiches"] = [fiche_eur(e, a) for e in data.get("cal") or [] if e["impact"] == "High" and e["date"] >= now][:4]
    a["vue"] = vue_eur(a, data)
    a["intermarket"] = intermarket_eur(a, data)
    a["grs"] = T.force_relative(a, data)
    if a["grs"]:
        a["grs"]["etat"] = a["grs"]["etat"].replace("plus forte", "plus fort").replace("plus faible", "plus faible")
    a["regimes"] = regimes6_eur(a, data)
    a["reactions_evt"] = reactions_evt_eur(a, data)
    a["trader"] = T.plan_trader(a, data)
    a["force_devises"] = T.analyser_devises(data)
    a["setup_a"] = T.setup_a(a, data)
    a["plan"] = T.plan_du_jour(a, data)
    a["bilan"] = T.bilan_seance(a, data)
    a["semaine"] = semaine_eur(a, data)
    return a


def vue_eur(a, data):
    v = T.vue_marche(a, data)
    remp = (("l'or fait mieux que ce que la macro explique", "l'euro fait mieux que ce que ses moteurs expliquent"),
            ("l'or fait moins bien que ce que la macro explique", "l'euro fait moins bien que ce que ses moteurs expliquent"))
    det = []
    for lib, d, t in v["sentiment"]:
        for x, y_ in remp:
            d = d.replace(x, y_)
        det.append(("Flux de fond" if lib == "Demande de fond" else lib, d, t))
    v["sentiment"] = det
    pb = a.get("bce") or {}
    if pb.get("prochaine") and (pb["prochaine"] - maintenant().date()).days <= 7:
        v["catalyseurs"] = ([f"Décision de la BCE le {date_fr(pb['prochaine'])} à 14:15"] + v["catalyseurs"])[:6]
    return v


def semaine_eur(a, data):
    s = T.semaine_a_venir(a, data)
    d = (a.get("bce") or {}).get("prochaine")
    now = maintenant()
    if d and (d - now.date()).days <= 14 and not any(re.search(r"main refinancing|deposit facility", t, re.I)
                                                       for _, l in s["jours"] for _, t in l):
        jours = dict(s["jours"])
        quand = datetime.combine(d, datetime.min.time()).replace(hour=14, minute=15, tzinfo=tz_local())
        jours.setdefault(d, []).append((quand, "Décision de la BCE (14:15), conférence de presse à 14:45"))
        s["jours"] = sorted(jours.items())[:10]
    return s


def point_eur(a):
    o, b, reg = a["or"], a["biais"], a["regime"]
    phr = []
    if o["prix"] is not None:
        sens = "en hausse" if (o["d5"] or 0) > 0 else "en baisse"
        phr.append(f"L'EUR/USD cote {fp(o['prix'])}, {sens} de {nb(abs(o['d5'] or 0), 2)} % sur 5 jours. Le biais fondamental est "
                   f"{b['verdict'].lower()} ({nb(b['total'], 0, True) if b['total'] else '0'} sur {b['n']} signaux).")
    phr.append(f"Régime de marché : {reg['nom'].lower()}. {reg['resume']}")
    bc = a.get("bce") or {}
    if bc.get("ecart2") is not None:
        phr.append(f"L'écart de taux à 2 ans entre les États-Unis et la zone euro est de {nb(bc['ecart2'], 2)} point"
                   + (f", {nb(bc['ecart2_d5'], 0, True)} pb sur 5 jours" if bc.get("ecart2_d5") is not None else "") + ".")
    md = a.get("moteurs_jour")
    if md:
        m = md[0]
        phr.append(f"Mouvement le plus notable du jour : {m['lib']}, {m['var']} (mouvement {m['intensite']}), {m['effet']}.")
    dc = a.get("decomp")
    if dc:
        j = dc["jour"]
        if abs(j["residu"]) >= 0.15:
            qual = "fait mieux" if j["residu"] > 0 else "fait moins bien"
            phr.append(f"Les taux, les actions et le gaz expliquaient {nb(j['attendu'], 2, True)} % pour l'EUR/USD sur la dernière "
                       f"séance ; il a fait {nb(j['reel'], 2, True)} %. L'euro {qual} que ses moteurs, un écart qui vient des flux.")
        else:
            phr.append(f"Le mouvement de l'EUR/USD ({nb(j['reel'], 2, True)} %) est cohérent avec ses moteurs : pas de force cachée.")
    e = a.get("annonce")
    if e:
        rng = f", amplitude journalière normale ±{nb(a['range_jour'], 0)} pips" if a.get("range_jour") else ""
        phr.append(f"Prochain risque : {fiche_eur(e, a)['nom']} le {date_fr(e['date'], True)}{rng}.")
    return phr


# ---------------------------------------------------------------------------
# AFFICHAGE PROPRE À L'EURO
# ---------------------------------------------------------------------------

def pct_txt(v, d=2):
    return f"{nb(v, d, True)} %" if v is not None else "n.d."


def bloc_strip_eur(a, y, f):
    it = []
    b = a["biais"]
    it.append(("Biais fondamental", f'<span class="{ {"Haussier": "up", "Baissier": "down"}.get(b["verdict"], "flat") }">'
                                     f'{b["verdict"]}</span>', f'score {nb(b["total"], 0, True) if b["total"] else "0"} / {b["n"]}'))
    t_ = a.get("technique")
    if t_:
        it.append(("Biais technique", f'<span class="{COUL.get(t_["sens"], "flat")}">{ {1: "Haussier", -1: "Baissier", 0: "Neutre"}[t_["sens"]] }</span>',
                   f'score {nb(t_["score"], 2, True)}'))
    v = a.get("vue")
    if v:
        it.append((f"Vue {T.HORIZON_H} h", f'<span class="{ {1: "up", -1: "down"}.get(v["sens"], "flat") }">{v["direction"]}</span>',
                   f'conviction {v["conviction"]}'))
    pb = a.get("playbook")
    if pb:
        it.append(("Playbook", f'<span class="{ {1: "up", -1: "down"}.get(pb["sens"], "flat") }">{esc(T.MATRICE_PB[(pb["fond"], pb["tech"])])}</span>',
                   f'technique {nb(t_["score"], 2, True)}, {t_["regime"]["nom"]}' if t_ else ""))
    it.append(("Régime", esc(a["regime"]["nom"]), esc(a["regime"]["resume"][:48] + ("…" if len(a["regime"]["resume"]) > 48 else ""))))
    bc = a.get("bce") or {}
    if bc.get("ecart2") is not None:
        it.append(("Écart 2 ans US − EU", f'{nb(bc["ecart2"], 2)} pt',
                   f'<span class="{classe_effet(bc.get("ecart2_d5"), -1)}">{nb(bc.get("ecart2_d5"), 0, True)} pb / 5 j</span>'))
    if bc.get("att_bce") is not None and bc.get("att_fed") is not None:
        it.append(("Attentes à 1 an", f'BCE {nb(bc["att_bce"], 0, True)} · Fed {nb(bc["att_fed"], 0, True)}',
                   f'<span class="{classe_effet(bc.get("att_rel_d10"), 1)}">écart {nb(bc.get("att_rel_d10"), 0, True)} pb / 10 j</span>'))
    if bc.get("dfr") is not None:
        it.append(("Taux de dépôt BCE", f'{nb(bc["dfr"], 2)} %',
                   f'prochaine réunion {bc["prochaine"].day} {MOIS_FR[bc["prochaine"].month - 1]}' if bc.get("prochaine") else ""))
    fw = a.get("fedwatch")
    if fw and fw["reunions"]:
        r0 = fw["reunions"][0]
        txt = (f'Hausse {nb(r0["p_hausse"] * 100, 0)} %' if r0["p_hausse"] >= max(r0["p_baisse"], 0.5) else
               (f'Baisse {nb(r0["p_baisse"] * 100, 0)} %' if r0["p_baisse"] >= 0.5 else f'Statu quo {nb(r0["p_statu"] * 100, 0)} %'))
        it.append((f'Fed {r0["date"].day} {MOIS_FR[r0["date"].month - 1]}', txt, f'taux attendu {nb(r0["taux"], 2)} %'))
    r = a.get("risque")
    if r:
        it.append(("Appétit risque", r["etat"], f'score {nb(r["score"], 0, True)}'))
    d = variation(y.get("ttf"), 5)
    if y.get("ttf") is not None:
        it.append(("Gaz TTF", nb(derniere(y.get("ttf")), 1), f'<span class="{classe_effet(d, -1)}">{pct_txt(d, 1)} / 5 j</span>'))
    d = variation(y.get("dxy"), 5)
    it.append(("Dollar DXY", nb(derniere(y.get("dxy")), 2), f'<span class="{classe_effet(d, -1)}">{pct_txt(d)} / 5 j</span>'))
    it.append(("Vol. euro EVZ", nb(derniere(y.get("evz")), 1), f'±{nb(a["range_jour"], 0)} pips / jour' if a.get("range_jour") else ""))
    if a.get("cot"):
        it.append(("Fonds (COT euro)", f'pct {nb(a["cot"]["pct3a"], 0)}', f'{nb(a["cot"]["chg"], 0, True)} contrats'))
    if a.get("base") is not None:
        it.append(("Écart future-spot", f'{nb(a["base"], 1, True)} pips', "mesuré" if (a.get("ecart") or {}).get("mesure") else "estimé"))
    corps = "".join(f'<div class="it"><span class="k">{lib}</span><span class="v">{v_}</span><span class="s">{sub}</span></div>'
                    for lib, v_, sub in it)
    return f'<div class="strip analyst" data-zone="strip">{corps}</div>'


def bloc_lecture_eur(a):
    paras = "".join(f"<p>{esc(p)}</p>" for p in a["point"])
    r = a["regime"]
    reg = (f'<div class="regime"><div class="label">Régime de marché</div><div class="nom {r["ton"]}">{esc(r["nom"])}</div>'
           f'<p>{esc(r["explication"])}</p><p class="cons">{esc(r["consequence"])}</p>')
    dc = a.get("decomp")
    if dc:
        j = dc["jour"]
        vals = list(j["contrib"].values()) + [j["attendu"], j["reel"], j["residu"]]
        ech = max(abs(x) for x in vals) or 1
        noms = {"ecart2": "Écart de taux 2 ans", "stoxx": "Actions européennes", "ttf": "Gaz européen"}
        lignes = "".join(f'<span>{noms[k]}</span>{barre_centree(x, ech)}<span class="v">{nb(x, 2, True)} %</span>'
                         for k, x in j["contrib"].items())
        lignes += (f'<span class="total">Attendu</span>{barre_centree(j["attendu"], ech)}<span class="v total">{nb(j["attendu"], 2, True)} %</span>'
                   f'<span class="total">Réel</span>{barre_centree(j["reel"], ech)}<span class="v total">{nb(j["reel"], 2, True)} %</span>'
                   f'<span>Écart (flux)</span>{barre_centree(j["residu"], ech)}<span class="v">{nb(j["residu"], 2, True)} %</span>')
        reg += (f'<h3>Décomposition du dernier mouvement de l\'EUR/USD</h3><p class="legende" style="margin:0">Modèle sur 60 '
                f'jours : l\'écart de taux, les actions européennes et le gaz expliquent {nb(dc["r2"] * 100, 0)} % des variations '
                f'de l\'EUR/USD.</p><div class="decomp">{lignes}</div>')
    reg += "</div>"
    return (f'<section id="lecture"><h2>Le point en 30 secondes</h2><div class="grille2 grille-lec">'
            f'<div class="lecture">{paras}</div>{reg}</div></section>')


def table_fedwatch(fw):
    if not fw:
        return '<p class="vide">Futures fed funds indisponibles.</p>'
    lignes = ""
    for r in fw["reunions"]:
        pb_, ps_, ph_ = r["p_baisse"] * 100, r["p_statu"] * 100, r["p_hausse"] * 100
        lignes += (f'<tr><td class="l" style="white-space:nowrap">{r["date"].day} {MOIS_FR[r["date"].month - 1]} '
                   f'{str(r["date"].year)[2:]}</td><td class="v">{nb(r["taux"], 2)} %</td>'
                   f'<td class="d {classe_effet(r["delta_pb"], 1)}">{nb(r["delta_pb"], 0, True)}</td>'
                   f'<td style="padding-left:12px"><div class="proba"><i class="pr-b" style="width:{pb_:.0f}%"></i>'
                   f'<i class="pr-s" style="width:{ps_:.0f}%"></i><i class="pr-h" style="width:{ph_:.0f}%"></i></div></td>'
                   f'<td class="d">{nb(ph_ if ph_ >= pb_ else -pb_, 0, True)} %</td></tr>')
    return (f'<div class="defile"><table class="m"><tr><th>Réunion Fed</th><th>Taux</th><th>Var. pb</th><th>Probabilités</th>'
            f'<th>Proba.</th></tr>{lignes}</table></div><p class="legende" style="margin-top:8px">Futures fed funds, calcul maison '
            f'proche du CME FedWatch. Vert : baisse des taux US (favorable à l\'euro), rouge : hausse.</p>')


def bloc_taux_eur(a, y, f):
    bc, fed = a.get("bce") or {}, a.get("fed") or {}
    kv = (f'<div class="kv"><span>Taux de dépôt BCE</span><span>{nb(bc.get("dfr"), 2)} %</span>'
          f'<span>€STR (taux au jour le jour en euro)</span><span>{nb(bc.get("estr"), 2)} %</span>'
          f'<span>Fed funds effectif</span><span>{nb(fed.get("effr"), 2)} %</span>'
          f'<span>Prochaine décision de la BCE</span><span>{date_fr(bc["prochaine"]) + " à 14:15" if bc.get("prochaine") else "n.d."}</span>'
          f'<span>Attente BCE sur 1 an</span><span>{nb(bc.get("att_bce"), 0, True)} pb</span>'
          f'<span>Attente Fed sur 1 an</span><span>{nb(bc.get("att_fed"), 0, True)} pb</span>'
          f'<span>Écart des attentes (BCE − Fed), sur 10 j</span><span class="{classe_effet(bc.get("att_rel_d10"), 1)}">'
          f'{nb(bc.get("att_rel_d10"), 0, True)} pb</span></div>')
    lignes = "".join(T.ligne_metrique(lib, s, "pb", 2, eff, " %") for lib, s, eff in (
        ("US 2 ans", f.get("us2"), -1), ("Zone euro 2 ans (AAA)", f.get("eu2"), 1), ("Écart 2 ans US − EU", f.get("ecart2"), -1),
        ("US 10 ans", f.get("us10f") if f.get("us10f") is not None else y.get("us10"), -1),
        ("Zone euro 10 ans (AAA)", f.get("eu10"), 1), ("Écart 10 ans US − EU", f.get("ecart10"), -1)))
    g = graphique_double(y.get("or") / PIP if y.get("or") is not None else None, f.get("ecart2"), d1=4, d2=2)
    return (f'<section id="taux"><h2>BCE, Fed et écart de taux {fraicheur("daily", T.derniere_date(f.get("eu2")))}</h2>'
            f'<p class="pourquoi">Le moteur numéro un d\'une paire de devises : l\'argent va là où il est le mieux rémunéré. '
            f'Les attentes à 1 an comparent le taux à 1 an au taux au jour le jour : positif, le marché attend des hausses ; '
            f'négatif, des baisses. Quand la BCE est vue plus dure que la Fed, l\'euro monte.</p>'
            f'<div class="grille3"><div><h3 style="margin-top:0">Taux directeurs et attentes</h3>{kv}</div>'
            f'<div><h3 style="margin-top:0">Courbes des taux</h3><table class="m">{T.ENTETE_M}{lignes}</table>'
            f'<p class="legende" style="margin-top:6px">Zone euro : courbe BCE des États notés AAA, très proche de l\'Allemagne. '
            f'Variations en pb ; vert = favorable à l\'euro.</p></div>'
            f'<div><h3 style="margin-top:0">Fed : probabilités par réunion</h3>{table_fedwatch(a.get("fedwatch"))}</div></div>'
            f'<h3>EUR/USD et écart de taux 2 ans, 12 mois</h3><div class="cle"><span><i style="background:var(--brass)"></i>'
            f'EUR/USD (axe gauche)</span><span><i style="background:var(--steel)"></i>Écart 2 ans US − zone euro (points, axe droit '
            f'inversé)</span></div>{g}<p class="legende" style="margin-top:8px">Axe inversé : quand les deux courbes montent '
            f'ensemble, l\'euro suit bien le resserrement de l\'écart. Quand elles s\'écartent, un autre moteur a pris la main.</p>'
            f'</section>')


def bloc_flux_eur(a, cot):
    c = a.get("cot")
    if not c:
        return '<section><h2>Positionnement des fonds sur l\'euro</h2><p class="vide">Rapport COT indisponible.</p></section>'
    grp = "".join(f'<span>{esc(g["lib"])}</span><span>{nb(g["net"], 0)} ({nb(g["chg"], 0, True)}), pct {nb(g["pct3a"], 0)}</span>'
                  for g in c.get("groupes", []))
    lect = (f'<p class="note"><b>{esc(c["lecture"][0])}.</b> {esc(c["lecture"][1])}</p>' if c.get("lecture") else "")
    kv = (f'<div class="kv"><span>Fonds spéculatifs, position nette</span><span>{nb(c["net"], 0)} contrats</span>'
          f'<span>Variation sur la semaine</span><span class="{classe_effet(c["chg"], 1)}">{nb(c["chg"], 0, True)}</span>'
          f'<span>Achats / ventes</span><span>{nb(c["long"], 0)} / {nb(c["short"], 0)}</span>'
          f'<span>Percentile sur 3 ans</span><span>{nb(c["pct3a"], 0)}</span>'
          f'<span>Part de l\'open interest</span><span>{nb(c["pct_oi"], 1)} %</span>{grp}</div>')
    return (f'<section id="flux"><h2>Positionnement des fonds sur l\'euro {fraicheur("weekly", c["date"])}</h2>'
            f'<p class="pourquoi">Rapport CFTC « Traders in Financial Futures » sur le future euro du CME (1 contrat = '
            f'125 000 euros). Les fonds spéculatifs suivent les tendances ; à l\'extrême (au-dessus du 85e percentile), '
            f'il reste peu d\'acheteurs et le risque de débouclage augmente. Données du mardi, publiées le vendredi.</p>'
            f'<div class="grille2"><div>{kv}{lect}</div><div>{T.graphique_cot(cot, c)}</div></div></section>')


def bloc_marches_eur(y, f, a):
    sp = lambda s: (s / PIP) if s is not None else None
    lignes = (T.ligne_metrique("EUR/USD", sp(y.get("or")), "pct", 5, 1)
              + "".join(T.ligne_metrique(lib, s, mode, dec, eff, u) for lib, s, mode, dec, eff, u in (
                  ("Dollar index (DXY)", y.get("dxy"), "pct", 2, -1, ""), ("EUR/GBP", y.get("eurgbp"), "pct", 4, 0, ""),
                  ("EUR/JPY", y.get("eurjpy"), "pct", 2, 0, ""), ("USD/JPY", y.get("usdjpy"), "pct", 2, 0, ""),
                  ("GBP/USD", y.get("gbpusd"), "pct", 4, 0, ""), ("Euro Stoxx 50", y.get("stoxx"), "pct", 0, 1, ""),
                  ("DAX", y.get("dax"), "pct", 0, 1, ""), ("S&P 500", y.get("spx"), "pct", 0, 0, ""),
                  ("VIX", y.get("vix"), "abs", 1, 0, ""), ("Volatilité euro (EVZ)", y.get("evz"), "abs", 1, 0, ""),
                  ("Gaz TTF (€/MWh)", y.get("ttf"), "pct", 1, -1, ""), ("Brent", y.get("brent"), "pct", 2, -1, " $"),
                  ("Or", y.get("gold"), "pct", 0, 0, " $"))))
    vol = a.get("vol") or {}
    return (f'<section id="marches"><h2>Marchés liés à l\'euro {fraicheur("daily", T.derniere_date(y.get("or")))}</h2>'
            f'<p class="pourquoi">Couleur des variations : vert si le mouvement est favorable à l\'euro, rouge s\'il lui est '
            f'défavorable, gris sans effet direct.</p><div class="grille2"><div><table class="m">{T.ENTETE_M}{lignes}</table></div>'
            f'<div><h3 style="margin-top:0">Volatilité de l\'euro</h3><div class="kv"><span>Réalisée sur 20 jours</span>'
            f'<span>{nb(vol.get("realisee"), 1)} %</span><span>Anticipée par les options (EVZ)</span><span>{nb(vol.get("implicite"), 1)} %</span>'
            f'<span>Amplitude journalière normale</span><span>±{nb(a.get("range_jour"), 0)} pips</span></div></div></div></section>')


def bloc_correlations_eur(a):
    if not a["correlations"]:
        return ""
    lignes = ""
    for c in a["correlations"]:
        v20, v60 = c["c20"], c["c60"]
        barre = ""
        if v20 is not None:
            larg = abs(v20) * 50
            barre += f'<b style="left:{50 - larg if v20 < 0 else 50:.1f}%;width:{larg:.1f}%"></b>'
        if v60 is not None:
            barre += f'<i style="left:{50 + v60 * 50:.1f}%"></i>'
        lignes += f'<span>{esc(c["nom"])}</span><div class="cbar">{barre}</div><span class="cv">{nb(v20, 2, True)}</span>'
    m = a.get("moteur")
    phrase = ""
    if m:
        sens = "à l'inverse de" if m["c20"] < 0 else "dans le même sens que"
        phrase = (f'<p class="sous" style="margin-top:14px">Moteur dominant sur 20 jours : <b>{esc(m["nom"])}</b> (corrélation '
                  f'{nb(m["c20"], 2, True)}). L\'euro bouge {sens} lui. Surveille-le en priorité pendant ta séance.</p>')
    return (f'<section><h2>Qui mène l\'euro en ce moment</h2><p class="legende">Corrélation des variations quotidiennes avec '
            f'l\'EUR/USD. Barre : 20 jours. Trait fin : 60 jours. Le dollar index est mécaniquement lié (l\'euro en pèse 58 %).</p>'
            f'<div class="corr">{lignes}</div>{phrase}</section>')


def bloc_regimes_eur(a, compact=False):
    rg = a.get("regimes")
    if not rg:
        return ""
    lignes = ""
    for d in sorted(rg["dimensions"], key=lambda x: -x["intensite"]):
        cls = COUL.get(d["effet"], "flat")
        eff = {1: "favorable à l'euro", -1: "défavorable à l'euro", 0: "neutre", 2: "risque (dollar refuge)"}[d["effet"]]
        lignes += (f'<span>{esc(d["nom"])}</span><div class="jauge-s"><i class="{cls}" style="width:{d["intensite"]:.0f}%"></i></div>'
                   f'<span class="v {cls}">{nb(d["intensite"], 0)}</span>'
                   + ("" if compact else f'<span class="{cls}">{eff}</span><span class="flat">{esc(d["etat"])}</span>'))
    dom = rg["dominant"]
    return (f'<section id="regimes"><h2>Forces en présence : <span class="{COUL.get(dom["effet"], "flat")}">{esc(dom["nom"])}</span> '
            f'<small class="flat">domine (intensité {nb(dom["intensite"], 0)})</small></h2>'
            f'<p class="pourquoi">Les forces qui pilotent l\'EUR/USD, classées par intensité. Vert : favorable à l\'euro, '
            f'rouge : défavorable, orange : risque.</p><div class="regimes{" compact" if compact else ""}">{lignes}</div></section>')


def bloc_intermarket_eur(a):
    im, grs = a.get("intermarket"), a.get("grs")
    if not im:
        return ""
    fl = lambda v, impl, d, u: (f'<td class="num {COUL.get(impl, "flat")}">{nb(v, d, True)}{u} {({1: "▲", -1: "▼"}).get(impl, "·")}</td>'
                                if v is not None else '<td class="num flat">n.d.</td>')
    lignes = ""
    for l in im["lignes"]:
        cls = "warn" if l["statut"] == "DIVERGENCE" else ("flat" if l["statut"] == "MIXED" else COUL.get(l["consensus"], "flat"))
        frais = fraicheur("daily") if l["horizon"] in ("1 jour", "5 jours") else '<span class="fr rel15">15 MIN</span>'
        lignes += (f'<tr><td>{esc(l["horizon"])} {frais}</td><td class="num {COUL.get(l["sens_eur"], "flat")}">{nb(l["eur"], 2, True)} %</td>'
                   f'<td class="flat">{esc(l["lib_taux"])}</td>' + fl(l["taux"], l["impl"][0], 1, " pb") + fl(l["act"], l["impl"][1], 2, " %")
                   + fl(l["gaz"], l["impl"][2], 1, " %")
                   + f'<td><span class="statut {cls}">{l["statut"]}</span></td><td class="flat">{esc(l["lecture"])}</td></tr>')
    g = ""
    if grs:
        pos = (grs["score"] + 100) / 2
        g = (f'<h3>EUR Relative Strength : <span class="{COUL.get(1 if grs["score"] > 25 else (-1 if grs["score"] < -25 else 0))}">'
             f'{nb(grs["score"], 0, True)}</span></h3><div class="pctbar"><i style="left:50%"></i><b style="left:{pos:.1f}%"></b></div>'
             f'<div class="pctleg"><span>Plus faible que ses moteurs</span><span>en ligne</span><span>Plus fort que ses moteurs</span></div>'
             f'<p class="legende" style="margin-top:6px">Écart entre ce que l\'EUR/USD a fait et ce que les taux, les actions et le gaz '
             f'expliquent, sur 1 h, 4 h, 1 jour et 5 jours. Plus fort que prévu : des acheteurs d\'euro de fond ; plus faible : des vendeurs.</p>')
    cls_g = "warn" if im["statut"] == "DIVERGENCE" else ("flat" if im["statut"] == "MIXED" else COUL.get(im["sens"], "flat"))
    return (f'<section id="intermarket"><h2>Intermarket : <span class="statut {cls_g}">{im["statut"]}</span></h2>'
            f'<p class="pourquoi">L\'EUR/USD face à ses moteurs : le taux US 2 ans en intraday, l\'écart de taux à 2 ans en '
            f'quotidien, les actions européennes et le gaz. Chaque flèche dit ce que le mouvement implique pour l\'euro '
            f'(▲ favorable, ▼ défavorable). CONFIRMED : l\'euro suit ses moteurs. DIVERGENCE : il va contre. MIXED : pas de '
            f'message clair.</p><div class="defile"><table class="cal"><tr><th>Horizon</th><th>EUR/USD</th><th>Taux</th><th></th>'
            f'<th>Euro Stoxx</th><th>Gaz TTF</th><th>Statut</th><th>Lecture</th></tr>{lignes}</table></div>{g}</section>')


def bloc_intermarche_eur(a):
    im = a.get("intermarche")
    if not im:
        return ""
    lignes = "".join(f'<tr><td>{esc(l["lib"])}</td><td class="num {classe_effet(l["or"], 1)}">{nb(l["or"], 2, True)} %</td>'
                     f'<td class="num">{nb(l.get("us2"), 1, True) + " pb" if l.get("us2") is not None else "n.d."}</td>'
                     f'<td class="num">{nb(l.get("stoxx"), 2, True) + " %" if l.get("stoxx") is not None else "n.d."}</td>'
                     f'<td class="num">{nb(l["attendu"], 2, True)} %</td>'
                     f'<td class="num {({"force": "up", "faiblesse": "down"}).get(l["signal"], "flat")}">{nb(l["residu"], 2, True)} %</td>'
                     f'<td class="{({"force": "up", "faiblesse": "down"}).get(l["signal"], "flat")}">{esc(l["signal"])}</td></tr>'
                     for l in im["lignes"])
    textes = "".join(f'<p class="bloc-txt">{esc(l["texte"])}</p>' for l in im["lignes"])
    return (f'<section><h2>Confirmation intermarché</h2><p class="pourquoi">L\'euro suit-il ce que le taux US 2 ans et les '
            f'actions européennes lui dictent ? Le mouvement attendu applique les sensibilités mesurées sur 60 jours aux '
            f'mouvements récents. Bougies 15 min Yahoo, environ 15 min de décalage ; l\'Euro Stoxx ne cote que de 9 h à 17 h 30.</p>'
            f'<div class="defile"><table class="cal"><tr><th>Période</th><th>EUR/USD</th><th>US 2 ans</th><th>Euro Stoxx</th>'
            f'<th>Attendu</th><th>Écart</th><th>Lecture</th></tr>{lignes}</table></div>{textes}</section>')


T.bloc_intermarche = bloc_intermarche_eur


def bloc_reactions_evt_eur(a):
    rs = a.get("reactions_evt") or []
    if not rs:
        return ('<section><h2>Event Reaction Engine</h2><p class="vide">Aucune annonce majeure publiée cette semaine pour '
                'l\'instant. Après chaque inflation, emploi, PMI ou décision de la BCE et de la Fed, la réaction de l\'EUR/USD, '
                'du dollar et du taux US 2 ans s\'affiche ici.</p></section>')
    cartes = ""
    for r in rs:
        rc = r["reac"]
        sp = {1: "dure", -1: "souple", 0: "conforme"}[r["sens_surprise"]]

        def val(k, d, u):
            x = rc.get(k)
            if x is None:
                return '<span class="flat">n.d.</span>'
            effet = (1 if x > 0 else -1) * (1 if k.startswith("eur") else -1) if x else 0
            return f'<span class="{COUL.get(effet)}">{nb(x, d, True)}{u}</span>'
        cartes += (f'<div class="fiche"><div class="t">{esc(r["nom"])}</div><div class="q">{date_fr(r["date"], True)}</div>'
                   f'<div class="pp"><div><span>Consensus</span>{esc(r["prevision"] or "n.d.")}</div><div><span>Réel</span>'
                   f'<b>{esc(r["reel"])}</b></div><div><span>Surprise</span>{esc(sp)}</div></div>'
                   f'<div class="kv"><span>EUR/USD 15 min / 1 h</span><span>{val("eur_15", 2, " %")} / {val("eur_60", 2, " %")}</span>'
                   f'<span>Dollar index 15 min / 1 h</span><span>{val("dxy_15", 2, " %")} / {val("dxy_60", 2, " %")}</span>'
                   f'<span>US 2 ans 15 min / 1 h</span><span>{val("us2_15", 1, " pb")} / {val("us2_60", 1, " pb")}</span></div>'
                   f'<p class="nu">{esc(r["interpretation"])}</p></div>')
    return (f'<section id="reactions"><h2>Event Reaction Engine</h2><p class="pourquoi">Après chaque annonce majeure, '
            f'américaine ou européenne : consensus, chiffre réel, surprise, réaction de l\'EUR/USD, du dollar et du taux US 2 ans, '
            f'puis lecture. Une réaction inverse à la surprise est souvent le signal le plus intéressant de la journée.</p>'
            f'<div class="fiches">{cartes}</div></section>')


def bloc_calendrier_eur(cal, a):
    fiches = ""
    for fi in a["fiches"]:
        nu = "".join(f'<p class="nu">{esc(n)}</p>' for n in fi["nuances"])
        scen = (f'<div class="sc"><span class="num">▲</span><span>{esc(fi["haut"])}</span><span class="num">▼</span>'
                f'<span>{esc(fi["bas"])}</span></div>') if fi["haut"] else ""
        amp = f'<p class="flat">Amplitude journalière normale de l\'EUR/USD : ±{nb(fi["amplitude"], 0)} pips.</p>' if fi["amplitude"] else ""
        fiches += (f'<div class="fiche"><div class="t">{esc(fi["nom"])}</div><div class="q">{date_fr(fi["date"], True)} (Paris)</div>'
                   f'<div class="pp"><div><span>Prévision</span>{esc(fi["prevision"] or "n.d.")}</div>'
                   f'<div><span>Précédent</span>{esc(fi["precedent"] or "n.d.")}</div></div><p>{esc(fi["pourquoi"])}</p>{scen}{nu}{amp}</div>')
    fiches_html = (f'<h2>Scénarios avant les prochaines annonces</h2><p class="pourquoi">Pour chaque annonce à fort impact, '
                   f'américaine ou européenne : la réaction probable de l\'EUR/USD selon que le chiffre sort au-dessus ou en '
                   f'dessous de la prévision. Attention au sens : une bonne surprise américaine fait baisser l\'EUR/USD, une bonne '
                   f'surprise européenne le fait monter.</p><div class="fiches">{fiches}</div>') if fiches else \
        '<h2>Scénarios avant les prochaines annonces</h2><p class="vide">Aucune annonce à fort impact à venir dans le calendrier.</p>'
    now = maintenant()
    if not cal:
        cal_html = '<p class="vide">Calendrier indisponible.</p>'
    else:
        lignes = ""
        for e in cal:
            passe = e["date"] < now - timedelta(minutes=T.FENETRE_NEWS[1])
            lignes += (f'<tr class="{"passe" if passe else ""}"><td class="num">{date_fr(e["date"], True)}</td>'
                       f'<td class="num">{esc(e.get("devise", "USD"))}</td>'
                       f'<td><span class="imp {e["impact"]}"></span>{esc(e["titre"].replace("EUR · ", ""))}</td>'
                       f'<td class="num">{esc(e["reel"])}</td><td class="num">{esc(e["prevision"])}</td>'
                       f'<td class="num">{esc(e["precedent"])}</td></tr>')
        cal_html = ('<div class="defile"><table class="cal"><tr><th>Heure de Paris</th><th>Devise</th><th>Annonce</th><th>Réel</th>'
                    f'<th>Prévision</th><th>Précédent</th></tr>{lignes}</table></div>')
    su = a.get("surprise_dev") or {}
    su_html = "".join(f'<span>{"Zone euro" if dev == "EUR" else "États-Unis"}</span><span class="{classe_effet(x["score"], 1 if dev == "EUR" else -1)}">'
                      f'{nb(x["score"], 2, True)} sur {x["n"]} publications</span>' for dev, x in su.items() if x)
    su_html = (f'<h3>Surprises économiques</h3><div class="kv">{su_html}</div><p class="legende" style="margin-top:6px">'
               f'+1 : toutes les publications au-dessus des prévisions ; -1 : toutes en dessous. Des surprises européennes '
               f'meilleures que les américaines soutiennent l\'euro.</p>') if su_html else ""
    return (f'<section id="calendrier">{fiches_html}</section><section class="grille2 grille-cal"><div><h2>Calendrier économique</h2>'
            f'<p class="legende">Point rouge : fort impact. Point orange : impact moyen. Règle en scalping : aucune position ouverte à '
            f'l\'approche d\'un point rouge.</p>{cal_html}</div><div>{su_html}</div></section>')


def bloc_actus_eur(news, off, a):
    b = a.get("barometre")
    if b:
        ex = lambda cat: "".join(f"« {esc(t)} » " for t in b[cat + "_ex"])
        baro = (f'<p class="pourquoi">Comptage de mots-clés dans les {b["n"]} titres récents. Indicatif.</p>'
                f'{T.jauge(b["dove"], b["hawk"], "Banques centrales souples", "Banques centrales dures", "var(--steel)", "var(--amber)")}'
                f'<p class="exemples">{ex("hawk") or ex("dove")}</p>'
                f'{T.jauge(b["desc"], b["esc"], "Détente", "Escalade", "var(--up)", "var(--down)")}'
                f'<p class="exemples">{ex("esc") or ex("desc")}</p><p class="legende">Les titres mélangent Fed et BCE : une Fed '
                f'dure pèse sur l\'euro, une BCE dure le soutient. Une escalade géopolitique profite au dollar refuge et renchérit '
                f'l\'énergie importée par l\'Europe.</p>')
    else:
        baro = '<p class="vide">Pas assez de titres pour le baromètre.</p>'
    items = (off or {}).get("communiques") or []
    li = "".join(f'<li><a href="{esc(i["lien"])}" target="_blank" rel="noopener">{esc(i["titre"])}</a>'
                 f'<small>{date_fr(i["date"]) if i["date"] else ""}</small></li>' for i in items[:7])
    cols = ""
    for theme, _ in T.THEMES_NEWS:
        its = (news or {}).get(theme) or []
        li2 = "".join(f'<li><a href="{esc(i["lien"])}" target="_blank" rel="noopener">{esc(i["titre"])}</a>'
                      f'<small>{esc(i["source"])}{", " + date_fr(i["date"], True) if i["date"] else ""}</small></li>' for i in its)
        cols += f'<div><h3 style="margin-top:0">{esc(theme)}</h3>' + (f'<ul>{li2}</ul>' if li2 else '<p class="vide">Pas de titre récupéré.</p>') + '</div>'
    return (f'<section id="actus" class="news"><div class="grille2"><div><h2>Baromètre des titres</h2>{baro}</div>'
            f'<div><h2>BCE, communiqués et discours</h2>' + (f'<ul>{li}</ul>' if li else '<p class="vide">Indisponible.</p>') +
            f'</div></div></section><section class="news"><h2>Dernières actualités (48 h)</h2><div class="grille4">{cols}</div></section>')


def bloc_routine_eur():
    etapes = [
        ("Lis le point en 30 secondes.", "Régime, biais et prochain risque : le contexte de ta séance."),
        ("Regarde l'écart de taux et le taux US 2 ans.", "C'est le moteur numéro un de l'EUR/USD : garde-le ouvert à côté de ton graphique."),
        ("Vérifie les annonces des deux côtés.", "Une bonne surprise américaine fait baisser l'EUR/USD, une bonne surprise européenne le fait monter."),
        ("Place les niveaux de la séance.", "Plus haut et plus bas de la veille, pivot, VWAP, chiffres ronds en 00 et 50 : les zones où le prix réagit."),
        ("Note le biais dans ton journal.", "Colonne « biais du jour » et « trade aligné oui/non » pour mesurer si le filtre t'aide."),
    ]
    li = "".join(f'<li><b>{esc(t)}</b> {esc(d)}</li>' for t, d in etapes)
    return f'<div><h2>Routine avant séance, 2 minutes</h2><ol class="routine">{li}</ol></div>'


T.bloc_routine = bloc_routine_eur


def bloc_risque_eur(a):
    r = a.get("risque")
    if not r:
        return ""
    comp = "".join(f'<span>{esc(l)}</span>{barre_score(v / 3)}<span class="v">{nb(v, 1, True)} σ</span>' for l, v in r["composantes"])
    ton = {"Risk-on": "up", "Risk-off": "down"}.get(r["etat"], "flat")
    return (f'<section><h2>Appétit pour le risque : <span class="{ton}">{r["etat"]}</span> <small class="flat">score '
            f'{nb(r["score"], 0, True)} sur 100</small></h2><p class="pourquoi">{esc(r["lecture"])} Chaque barre montre le '
            f'mouvement sur 5 jours, orienté pour que la droite signifie « prise de risque ».</p>'
            f'<div class="decomp" style="max-width:640px">{comp}</div></section>')


def bloc_moteurs_eur(a):
    items = ""
    for m in a.get("moteurs_jour") or []:
        items += (f'<li><div class="t"><b>{esc(m["lib"])}</b><span class="var {m["ton"]}">{esc(m["var"])}</span>'
                  f'<span class="z">{nb(m["z"], 1, True)} σ, {esc(m["intensite"])}</span>'
                  f'<span class="{m["ton"]}">{esc(m["effet"])}</span></div><div class="mec">{esc(m["mecanisme"])}</div></li>')
    lignes = ""
    for m in a["mouvements"]:
        cells = "".join(f'<td class="c" style="{T.couleur_z(m[z], m["effet"])}">{nb(m[z], 1, True)}</td>' for z in ("z1", "z5", "z20"))
        lignes += f'<tr><td>{esc(m["lib"])}</td>{cells}</tr>'
    return (f'<section id="moteurs"><div class="grille2"><div><h2>Ce qui bouge l\'euro aujourd\'hui</h2>'
            f'<p class="pourquoi">Les mouvements du jour classés par intensité. σ = combien de fois le mouvement habituel '
            f'(écart-type sur 60 jours) : au-delà de 2, c\'est un mouvement fort.</p><ul class="moteurs">{items}</ul></div>'
            f'<div><h2>Carte de chaleur des moteurs</h2><p class="pourquoi">Intensité des mouvements sur 1, 5 et 20 jours, en σ. '
            f'Vert : favorable à l\'euro. Rouge : défavorable. Bleu : sans effet direct.</p>'
            f'<table class="heat"><tr><th></th><th>1 j</th><th>5 j</th><th>20 j</th></tr>{lignes}</table></div></div></section>')


def contenus_onglets_eur(data, a, ia):
    y, f = data["yahoo"], data["fred"]
    return {
        "synthese": (T.bloc_synthese_tech(a) + bloc_intermarket_eur(a) + bloc_regimes_eur(a, compact=True) + T.bloc_plan(a) +
                     f'<section>{bloc_routine_eur()}</section>'),
        "fondamental": (bloc_regimes_eur(a) + bloc_lecture_eur(a) + T.bloc_devises(a) + T.bloc_biais_detail(a) + T.bloc_ia(ia) + bloc_taux_eur(a, y, f) +
                        bloc_moteurs_eur(a) + bloc_risque_eur(a) + bloc_correlations_eur(a) + bloc_marches_eur(y, f, a) +
                        bloc_flux_eur(a, data.get("cot"))),
        "technique": T.bloc_technique(a, data),
        "annonces": bloc_reactions_evt_eur(a) + bloc_calendrier_eur(data["cal"], a) + T.bloc_reactions(a),
        "actus": bloc_actus_eur(data.get("news"), data.get("fed_off"), a),
        "suivi": T.bloc_suivi(a) + T.bloc_methode(),
    }


T.contenus_onglets = contenus_onglets_eur
T.bloc_strip = bloc_strip_eur

METHODE_EUR = [
    ("Le biais fondamental de l'euro", [
        "Huit règles votent chacune -1, 0 ou +1 : écart de taux à 2 ans et à 10 ans entre les États-Unis et la zone euro "
        "(sur 5 séances), attentes de la BCE face à la Fed (sur 10 séances), énergie (gaz TTF et Brent), appétit pour le "
        "risque, dollar face aux autres devises (yen, livre, franc), positionnement des fonds sur le future euro et "
        "surprises économiques comparées. Score de +2 ou plus : haussier. -2 ou moins : baissier. Sinon : neutre.",
        "Le biais indique dans quel sens le vent souffle. Il ne donne ni le point d'entrée ni le moment : c'est le rôle de "
        "l'analyse technique."]),
    ("Les taux et les banques centrales", [
        "Taux de la zone euro : courbe publiée chaque jour par la BCE pour les États notés AAA, très proche de l'Allemagne. "
        "Taux américains : FRED et Trésor. Les attentes à 1 an comparent le taux à 1 an au taux au jour le jour (€STR et fed "
        "funds) : c'est ce que le marché attend des banques centrales sur les douze prochains mois.",
        "Les probabilités de la Fed viennent des futures fed funds. Il n'existe pas d'équivalent gratuit pour la BCE : on "
        "utilise donc la courbe des taux."]),
    ("Les prix, en pips", [
        "Le moteur suit le future euro du CME (6E), qui a des volumes, indispensables pour le VWAP et le profil de volume. "
        "Ses prix sont convertis en EUR/USD spot avec l'écart mesuré au même instant sur les bougies spot (ou estimé par "
        "l'écart entre les taux du dollar et de l'euro jusqu'à l'échéance). Toutes les distances sont en pips (0,0001). "
        "Le prix en direct en haut de la page vient de Kraken ; s'il est indisponible, la page affiche DIFFÉRÉ et utilise "
        "la référence du dernier relevé du robot."]),
    ("Intermarket et EUR Relative Strength", [
        "L'EUR/USD est comparé au taux US 2 ans (intraday), à l'écart de taux à 2 ans (quotidien), aux actions européennes "
        "et au gaz. CONFIRMED : l'euro suit ses moteurs. DIVERGENCE : il va contre, souvent le signal le plus intéressant. "
        "L'EUR Relative Strength (-100 à +100) mesure l'écart entre le mouvement réel et celui que les moteurs expliquent."]),
    ("Les annonces des deux côtés de l'Atlantique", [
        "Le calendrier couvre les annonces américaines et européennes. Le sens est opposé : une surprise forte aux "
        "États-Unis renforce le dollar (EUR/USD en baisse), une surprise forte en zone euro renforce l'euro (EUR/USD en "
        "hausse). L'Event Reaction Engine mesure ensuite la réaction de l'EUR/USD, du dollar index et du taux US 2 ans."]),
] + [m for m in T.METHODE if m[0] in ("L'analyse technique retenue, et pourquoi", "Liquidité, FVG, order blocks, OTE, premium / discount",
                                       "Le playbook technico-fondamental", "Les zones de confluence", "La vue de marché (où va le marché)",
                                       "Le contrôle avant l'entrée", "Le suivi des prévisions")] + [
    ("Le mode Trader", [
        "La page principale résume tout en une fiche : biais fondamental, biais technique, signal, action, setup (le "
        "playbook), statut intermarket et prochaine annonce. Le signal n'apparaît que quand le prix est dans la zone du "
        "playbook et qu'une confirmation est présente ; trente minutes avant une annonce forte, tout setup est à éviter."]),
    ("Les limites", [
        "Les analyses reposent sur Yahoo (environ 15 min de décalage) et sont recalculées toutes les 15 min. Les taux "
        "européens de la BCE et la FRED ont un jour ouvré de décalage, le COT reflète le mardi précédent. Il n'existe pas de "
        "probabilités gratuites pour la BCE ni de carnet d'ordres gratuit sur le forex.",
        "Ce terminal est un outil d'aide à la lecture du marché, pas un conseil en investissement."]),
]
T.METHODE = METHODE_EUR


# ---------------------------------------------------------------------------
# ANALYSTE IA, BRIEF ET RÉSUMÉ
# ---------------------------------------------------------------------------

T.CONSIGNE_IA = (T.CONSIGNE_IA
                 .replace("spécialisé sur l'or (XAU/USD)", "spécialisé sur l'EUR/USD")
                 .replace("niveaux de séance, Fed, flux, annonces, actualités",
                          "niveaux de séance, BCE et Fed, écart de taux, énergie, positionnement, annonces, actualités")
                 .replace("la situation de l'or maintenant", "la situation de l'EUR/USD maintenant")
                 .replace("réaction probable de l'or si le chiffre", "réaction probable de l'EUR/USD si le chiffre")
                 .replace("(ils sont exprimés en XAU/USD spot)",
                          "(ils sont exprimés en EUR/USD spot à 5 décimales ; les distances sont en pips)"))


def construire_brief_eur(data, a):
    y, f = data["yahoo"], data["fred"]
    o, b = a["or"], a["biais"]
    L = [f"BRIEF TERMINAL EUR/USD v{T.VERSION} ({date_fr(maintenant(), True)}, heure de Paris)", ""]
    L.append(f"EUR/USD : {fp(o['prix'])} (1 j {nb(o['d1'], 2, True)} %, 5 j {nb(o['d5'], 2, True)} %, 20 j {nb(o['d20'], 2, True)} %). "
             f"Amplitude attendue du jour : ±{nb(a['range_jour'], 0)} pips.")
    L.append(f"Régime : {a['regime']['nom']} ({a['regime']['resume']})")
    v = a.get("vue")
    if v:
        L.append(f"Vue de marché à {T.HORIZON_H} h : {v['direction']} (conviction {v['conviction']}, score {nb(v['score'], 2, True)}). "
                 f"{v['central']} {v['alternatif']}")
    L.append(f"Biais fondamental : {b['verdict']} (score {nb(b['total'], 0, True)} sur {b['n']})")
    for s in b["signaux"]:
        L.append(f"  - {s['nom']} : {s['valeur']} -> {s['lecture']}")
    bc = a.get("bce") or {}
    L.append(f"Taux : dépôt BCE {nb(bc.get('dfr'), 2)} %, €STR {nb(bc.get('estr'), 2)} %, fed funds {nb((a.get('fed') or {}).get('effr'), 2)} % ; "
             f"2 ans US {nb(derniere(f.get('us2')), 2)} %, zone euro {nb(bc.get('eu2'), 2)} %, écart {nb(bc.get('ecart2'), 2)} pt "
             f"({nb(bc.get('ecart2_d5'), 0, True)} pb sur 5 j) ; attentes à 1 an BCE {nb(bc.get('att_bce'), 0, True)} pb, "
             f"Fed {nb(bc.get('att_fed'), 0, True)} pb ; prochaine BCE {date_fr(bc['prochaine']) if bc.get('prochaine') else 'n.d.'}")
    fw = a.get("fedwatch")
    if fw:
        L.append("Probabilités Fed : " + " ; ".join(f"{date_fr(r['date'])} : hausse {nb(r['p_hausse'] * 100, 0)} % / baisse "
                                                     f"{nb(r['p_baisse'] * 100, 0)} %" for r in fw["reunions"][:3]))
    L.append(f"Énergie : gaz TTF {nb(derniere(y.get('ttf')), 1)} ({nb(variation(y.get('ttf'), 5), 1, True)} % sur 5 j), "
             f"Brent {nb(derniere(y.get('brent')), 2)} ({nb(variation(y.get('brent'), 5), 1, True)} %)")
    if a.get("risque"):
        L.append(f"Appétit pour le risque : {a['risque']['etat']} (score {nb(a['risque']['score'], 0, True)})")
    c = a.get("cot")
    if c:
        L.append(f"COT euro : fonds spéculatifs {nb(c['net'], 0)} contrats nets ({nb(c['chg'], 0, True)}), percentile 3 ans "
                 f"{nb(c['pct3a'], 0)} ; {c['lecture'][0] if c.get('lecture') else ''}")
    dv_ = a.get("force_devises")
    if dv_:
        L.append("Devises face au panier de 7 (1 j / 5 j) : " + ", ".join(
            f"{d} {nb(dv_['lignes'][d]['1j'], 2, True)} % / {nb(dv_['lignes'][d]['5j'], 2, True)} %"
            for d, _ in T.DEVISES_AFFICHEES if d in dv_["lignes"]) + ". " + " ".join(dv_["lecture"]))
    im = a.get("intermarket")
    if im:
        L.append("Intermarket : " + im["statut"] + " ; " + " ; ".join(f"{l['horizon']} {l['statut']}" for l in im["lignes"]))
    t_, pb_ = a.get("technique"), a.get("playbook")
    if t_ and pb_:
        L.append(f"Technique : score {nb(t_['score'], 2, True)}, régime {t_['regime']['nom']} ; structure "
                 + ", ".join(f"{x['nom']} {x['etat'].lower()} ({x['label_sommet']}+{x['label_creux']})" for x in t_["structure"]))
        L.append(f"Playbook : {pb_['titre']}. " + " ".join(pb_["etapes"]))
        if t_["confluences"]:
            L.append("Zones de confluence : " + " ; ".join(f"{fp(z['bas'])}-{fp(z['haut'])} (score {nb(z['score'], 1)} : "
                                                          f"{', '.join(z['elements'][:3])})" for z in t_["confluences"][:5]))
    if v and a.get("seance"):
        dessus, dessous = T.niveaux_autour(a, v["prix"])
        L.append(f"Niveaux clés en EUR/USD spot (dernier relevé {fp(v['prix'])}) : au-dessus "
                 + ", ".join(f"{l} {fp(x)}" for l, x in dessus[:5]) + " ; en dessous " + ", ".join(f"{l} {fp(x)}" for l, x in dessous[:5]))
    now = maintenant()
    ev = [e for e in data.get("cal") or [] if e["impact"] == "High" and e["date"] >= now][:6]
    if ev:
        L.append("Annonces fortes à venir : " + " ; ".join(f"{date_fr(e['date'], True)} {e['titre']} (prévision {e['prevision'] or 'n.d.'})" for e in ev))
    for theme, items in (data.get("news") or {}).items():
        if items:
            L.append(f"Titres « {theme} » : " + " | ".join(i["titre"] for i in items[:4]))
    return "\n".join(L)


T.construire_brief = construire_brief_eur


def resume_eur(a):
    o, b = a["or"], a["biais"]
    print()
    print(f"TERMINAL EUR/USD v{T.VERSION}  {date_fr(maintenant(), True)}")
    print(f"EUR/USD {fp(o['prix'])}   1 j {nb(o['d1'], 2, True)} %   5 j {nb(o['d5'], 2, True)} %")
    print(f"Régime : {a['regime']['nom']}")
    print(f"Biais fondamental : {b['verdict']} ({nb(b['total'], 0, True)} sur {b['n']})")
    for s in b["signaux"]:
        print(f"  {({1: '▲', -1: '▼'}).get(s['score'], '•')} {s['nom']:<40} {s['valeur']}")
    ko = [n for n, s in T.STATUT.items() if not s["ok"]]
    if ko:
        print("Sources en panne : " + ", ".join(ko))
    print()


T.resume_terminal = resume_eur

T.IA_DEMO = json.dumps({
    "titre": "L'EUR/USD hésite sous 1,1350 : l'écart de taux se resserre mais le gaz pèse.",
    "lecture": "L'écart de taux à 2 ans entre les États-Unis et la zone euro s'est resserré de 6 pb en cinq séances, ce qui "
               "soutient l'euro. Le gaz européen a pris 9 % sur la semaine et freine la hausse. Les fonds sont modérément "
               "acheteurs d'euro : il reste de la marge.",
    "direction": "neutre", "accord": "nuancé",
    "accord_explication": "La structure 1 h est haussière, mais l'énergie et un dollar ferme face au yen limitent l'élan.",
    "forces": ["Soutien : écart 2 ans -6 pb sur 5 jours", "Pression : gaz TTF +9 % sur 5 jours", "Soutien : risk-on modéré"],
    "annonces": ["Inflation zone euro au-dessus de 2,2 % : euro soutenu ; en dessous : repli probable vers 1,1290"],
    "invalidation": ["Clôture horaire sous 1,1290", "Écart 2 ans qui repasse au-dessus de 1,80 point"],
    "achat": "Un achat a du sens sur un repli vers la zone 1,1300-1,1310 avec un rejet net en 15 min, cible 1,1350.",
    "vente": "Une vente n'a de sens qu'après une cassure sous 1,1290 avec un retour raté ; éviter de vendre sur le support.",
    "vigilance": ["Zone news autour de l'inflation européenne", "Amplitude normale ±60 pips"]}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# DONNÉES DE DÉMONSTRATION
# ---------------------------------------------------------------------------

def donnees_demo_eur():
    d = T.donnees_demo()
    rng = np.random.default_rng(7)
    y, f = d["yahoo"], d["fred"]
    g = y["or"]
    eur = 11300 * (g / g.iloc[-1]) ** (1 / 3)
    idx = g.index
    marche = lambda debut, vol: pd.Series(debut * np.exp(np.cumsum(rng.normal(0, vol, len(idx)))), index=idx)
    y2 = {"or": eur, "dxy": y.get("dxy"), "us10": y.get("us10"), "spx": y.get("spx"), "vix": y.get("vix"), "move": y.get("move"),
          "stoxx": marche(5600, 0.009), "dax": marche(24000, 0.01), "evz": pd.Series(7.5 + rng.normal(0, 0.3, len(idx)), index=idx),
          "brent": y.get("brent"), "ttf": marche(34, 0.025), "gold": g, "usdjpy": y.get("usdjpy"),
          "gbpusd": marche(1.33, 0.004), "usdchf": marche(0.80, 0.004), "eurgbp": marche(0.85, 0.003), "eurjpy": marche(178, 0.005)}
    d["yahoo"] = {k: v for k, v in y2.items() if v is not None}
    bidx = f["us2"].index
    rw = lambda debut, vol: pd.Series(debut + np.cumsum(rng.normal(0, vol, len(bidx))), index=bidx)
    f.update({"eu1": rw(2.05, 0.015), "eu2": rw(2.15, 0.02), "eu10": rw(2.65, 0.02), "us1": f["us2"] - 0.15,
              "dfr": pd.Series(2.0, index=bidx), "estr": pd.Series(1.93, index=bidx)})
    f["us10f"] = f.get("us10f", d["yahoo"]["us10"])
    fusionner_taux(d)
    intra = d["intraday"]
    dec = 11300 - float(intra["barres"]["close"].iloc[-1]) + 35
    conv = lambda df: df.assign(**{c: df[c] + dec for c in ("open", "high", "low", "close") if c in df})
    b = conv(intra["barres"])
    d["intraday"] = {"barres": b, "jours": conv(intra["jours"]), "heures": conv(intra["heures"]) if intra.get("heures") is not None else None,
                     "spot15": b["close"] - 35}
    im = d.get("intermarche")
    if im is not None:
        im = im.copy()
        im["6E=F"] = im["GC=F"] + dec
        im["^STOXX50E"] = 5600 * (1 + (im["GC=F"] / im["GC=F"].iloc[0] - 1) * 0.6)
        d["intermarche"] = im
    cot = d.get("cot")
    if cot is not None:
        cot = cot.copy()
        for g_, k in (("mm", 1.0), ("am", 1.8), ("deal", -2.4)):
            cot[g_ + "_long"] = cot["long"] * abs(k)
            cot[g_ + "_short"] = cot["short"] * abs(k) * (1.6 if k < 0 else 1)
            cot[g_ + "_net"] = cot[g_ + "_long"] - cot[g_ + "_short"]
        cot.attrs["categorie"] = "fonds spéculatifs (leveraged funds)"
        d["cot"] = cot
    now = maintenant()
    for e in d["cal"]:
        e["devise"] = "USD"
    for dh, titre, imp, prev, prec, reel in ((-26, "EUR · German Prelim CPI m/m", "High", "0.2%", "0.1%", "0.3%"),
                                           (5, "EUR · CPI Flash Estimate y/y", "High", "2.2%", "2.1%", ""),
                                           (29, "EUR · Flash Manufacturing PMI", "Medium", "49.8", "49.5", ""),
                                           (52, "EUR · Main Refinancing Rate", "High", "2.15%", "2.15%", "")):
        d["cal"].append({"date": now + timedelta(hours=dh), "titre": titre, "impact": imp, "devise": "EUR",
                         "prevision": prev, "precedent": prec, "reel": reel})
    d["cal"].sort(key=lambda e: e["date"])
    d["fed_off"] = {"communiques": [{"titre": "Monetary policy decisions", "lien": "#", "source": "BCE", "date": now - timedelta(days=20)}]}
    d["news"] = {th: [{"titre": f"Exemple de titre : {th}", "lien": "#", "source": "démo", "date": now}] for th, _ in T.THEMES_NEWS}
    T.STATUT.clear()
    for nom in ("Yahoo Finance", "FRED (Fed de St. Louis)", "BCE (taux zone euro)", "CFTC (COT euro)", "ForexFactory (calendrier)",
                "Google News", "BCE (RSS)", "Yahoo intraday", "Futures fed funds", "Yahoo intermarché 15 min"):
        noter(nom, True, "données de démonstration")
    return d


# ---------------------------------------------------------------------------
# GÉNÉRATION
# ---------------------------------------------------------------------------

def charger_historique_eur():
    fh = T.DOSSIER_CACHE / T.FICHIER_HIST
    if fh.exists():
        try:
            return json.loads(fh.read_text(encoding="utf-8"))
        except Exception:
            pass
    depot = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in depot:
        proprio, nom = depot.split("/", 1)
        try:
            return json.loads(http_get(f"https://{proprio.lower()}.github.io/{nom}/eurusd/{T.FICHIER_HIST}", timeout=15))
        except Exception:
            pass
    return {"previsions": [], "annonces": []}


def generer_eur(demo=False, site_dir=None):
    T.STATUT.clear()
    T.DOSSIER_CACHE.mkdir(parents=True, exist_ok=True)
    data = donnees_demo_eur() if demo else collecter_eur()
    if site_dir is not None and "or" not in data["yahoo"]:
        print("Prix EUR/USD indisponible : la page précédente est remise en ligne.")
        for nom, st in T.STATUT.items():
            print(f"  {nom} : {'ok' if st['ok'] else 'EN PANNE'} {st['message']}")
        sys.exit(0 if restaurer_page(site_dir) else 1)
    data["hist"] = {"previsions": [], "annonces": []} if demo else charger_historique_eur()
    a = analyser_eur(data)
    if a.get("risque"):
        a["risque"]["lecture"] = {"Risk-on": "Les investisseurs prennent du risque : l'euro, devise pro-cyclique, en profite en "
                                             "général face au dollar.",
                                  "Risk-off": "Les investisseurs cherchent la sécurité : le dollar refuge est recherché, l'euro "
                                              "souffre.",
                                  "Neutre": "Pas de mouvement de fond sur l'appétit pour le risque."}[a["risque"]["etat"]]
    if demo:
        data["hist"]["previsions"] = T.previsions_demo(data)
    T.suivre_previsions(data["hist"], a, data)
    a["suivi"] = T.stats_previsions(data["hist"])
    brief = construire_brief_eur(data, a)
    ia = ({"texte": T.IA_DEMO, "json": T.lire_json_ia(T.IA_DEMO), "modele": "démonstration", "heure": maintenant().isoformat(),
           "raison": "démonstration", "fournisseur": "Gemini"} if demo else T.analyste_ia(brief, a, data))
    a["ia"] = ia
    page = T.rendre_html(data, a, brief, ia, None, site=site_dir is not None)
    if site_dir is not None:
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "index.html").write_text(page, encoding="utf-8")
        (site_dir / "brief_claude.txt").write_text(brief, encoding="utf-8")
        if not demo:
            try:   # copie de secours : remise en ligne si une prochaine mise à jour échoue
                (T.DOSSIER_CACHE / "derniere_page.html").write_text(page, encoding="utf-8")
            except Exception:
                pass
    if not demo:
        T.sauver_historique(data["hist"], site_dir)
    else:
        T.FICHIER_HTML.write_text(page, encoding="utf-8")
    resume_eur(a)
    return a


def restaurer_page(site_dir):
    """Remet en ligne la dernière page réussie (sinon le site publié n'aurait plus de page EUR/USD)."""
    try:
        src = T.DOSSIER_CACHE / "derniere_page.html"
        if src.exists():
            site_dir.mkdir(parents=True, exist_ok=True)
            (site_dir / "index.html").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            h = T.DOSSIER_CACHE / T.FICHIER_HIST
            if h.exists():
                (site_dir / T.FICHIER_HIST).write_text(h.read_text(encoding="utf-8"), encoding="utf-8")
            return True
    except Exception:
        pass
    return False


def main():
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Terminal fondamental et technique de l'EUR/USD")
    p.add_argument("--demo", action="store_true", help="données fictives pour tester l'affichage")
    p.add_argument("--site", metavar="DOSSIER", help="génère un site statique (index.html) dans DOSSIER")
    p.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    args = p.parse_args()
    if args.site:
        print("Collecte des données EUR/USD (mode site)...")
        try:
            generer_eur(args.demo, Path(args.site))
        except SystemExit:
            raise
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Erreur pendant la page EUR/USD ({e}) : la page précédente est remise en ligne.")
            sys.exit(0 if restaurer_page(Path(args.site)) else 1)
        print(f"Page EUR/USD générée : {Path(args.site).resolve() / 'index.html'}")
        for nom, st in sorted(T.STATUT.items()):
            print(f"  {nom} : {'ok' if st['ok'] else 'EN PANNE'} {st['message']}")
        return
    generer_eur(args.demo)
    print(f"Tableau de bord : {T.FICHIER_HTML}")
    if not args.no_browser:
        webbrowser.open(T.FICHIER_HTML.as_uri())


if __name__ == "__main__":
    main()
