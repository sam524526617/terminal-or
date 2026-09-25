#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TERMINAL OR : tableau de bord fondamental XAU/USD
====================================================

Ce que fait ce programme :
  1. Va chercher les données fondamentales de l'or sur des sources gratuites
     (Yahoo Finance, FRED de la Fed de St. Louis, CFTC, SPDR, ForexFactory, Google News).
  2. Calcule un biais fondamental du jour (haussier / baissier / neutre) à partir de règles explicites.
  3. Génère une page terminal_or.html et l'ouvre dans ton navigateur.
  4. Écrit brief_claude.txt : un résumé à coller dans Claude pour une analyse.

Utilisation :
  python terminal_or.py              -> génère le tableau une fois et l'ouvre
  python terminal_or.py --watch 10   -> rafraîchit toutes les 10 minutes (la page se recharge seule)
  python terminal_or.py --no-browser -> génère sans ouvrir le navigateur
  python terminal_or.py --demo       -> données fictives, pour tester l'affichage sans internet
  python terminal_or.py --site site  -> génère un site statique (site/index.html) pour GitHub Pages

Dépendances : pip install yfinance pandas requests tzdata
"""

import argparse
import concurrent.futures as cf
import csv
import hashlib
import html
import io
import json
import logging
import math
import os
import sys
import time
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

import pandas as pd
import requests

try:
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
except ImportError:
    yf = None

# ---------------------------------------------------------------------------
# CONFIGURATION : tout ce que tu peux ajuster est ici
# ---------------------------------------------------------------------------

FUSEAU = "Europe/Paris"
DOSSIER = Path(__file__).resolve().parent
FICHIER_HTML = DOSSIER / "terminal_or.html"
FICHIER_BRIEF = DOSSIER / "brief_claude.txt"
DOSSIER_CACHE = DOSSIER / ".cache_terminal_or"

# Marchés suivis sur Yahoo Finance : clé -> (ticker, libellé)
YAHOO = {
    "or": ("GC=F", "Or (future COMEX)"),
    "argent": ("SI=F", "Argent"),
    "dxy": ("DX-Y.NYB", "Dollar index (DXY)"),
    "us5": ("^FVX", "Taux US 5 ans"),
    "us10": ("^TNX", "Taux US 10 ans"),
    "us30": ("^TYX", "Taux US 30 ans"),
    "brent": ("BZ=F", "Brent"),
    "wti": ("CL=F", "WTI"),
    "spx": ("^GSPC", "S&P 500"),
    "vix": ("^VIX", "VIX"),
    "gvz": ("^GVZ", "Volatilité implicite de l'or (GVZ)"),
    "usdjpy": ("JPY=X", "USD/JPY"),
    "eurusd": ("EURUSD=X", "EUR/USD"),
    "usdcny": ("CNY=X", "USD/CNY"),
    "usdinr": ("INR=X", "USD/INR"),
}

# Séries macro de la FRED (téléchargement public, sans clé) : clé -> (code, libellé)
FRED = {
    "reel10": ("DFII10", "Taux réel 10 ans (TIPS)"),
    "be10": ("T10YIE", "Inflation anticipée 10 ans"),
    "fwd5y5y": ("T5YIFR", "Inflation anticipée 5 ans dans 5 ans"),
    "us2": ("DGS2", "Taux US 2 ans"),
    "effr": ("DFF", "Fed funds effectif"),
    "hy": ("BAMLH0A0HYM2", "Spread crédit high yield"),
    "cpi": ("CPIAUCSL", "CPI"),
    "cpi_core": ("CPILFESL", "CPI core"),
    "pce_core": ("PCEPILFE", "PCE core"),
    "chomage": ("UNRATE", "Taux de chômage"),
    "nfp": ("PAYEMS", "Emplois non agricoles"),
    "claims": ("ICSA", "Inscriptions hebdo au chômage"),
}

# Seuils des règles du biais fondamental (variations sur 5 séances)
SEUILS = {
    "reel_pb": 8,        # taux réel 10 ans, en points de base
    "dxy_pct": 0.5,      # DXY, en %
    "us2_pb": 8,         # taux 2 ans (= repricing de la Fed), en pb
    "us10_pb": 5,        # taux 10 ans, pour le signal de défiance envers les actifs US
    "brent_pct": 4.0,    # Brent, en %
    "cot_haut": 85,      # percentile 3 ans au-dessus duquel les fonds sont "trop longs"
    "cot_bas": 15,       # percentile 3 ans en dessous duquel les fonds ont de la marge pour racheter
    "gld_t": 5.0,        # variation des avoirs du fonds GLD, en tonnes
}

# Comment lire le pétrole :
#   "inflation" : pétrole en hausse -> inflation -> Fed plus dure -> or en baisse (régime 2026, choc pétrolier)
#   "refuge"    : pétrole en hausse -> peur géopolitique -> or en hausse (régime classique)
REGIME_PETROLE = "inflation"

# Zone interdite autour des annonces USD à fort impact : (minutes avant, minutes après)
FENETRE_NEWS = (15, 10)

# Thèmes d'actualité (Google News) : (titre affiché, requête)
THEMES_NEWS = [
    ("Or", "gold price"),
    ("Fed et taux", "Federal Reserve OR Treasury yields"),
    ("Pétrole et géopolitique", "Iran OR Hormuz OR oil prices"),
    ("Flux et banques centrales", "central bank gold OR gold ETF"),
]

MOIS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
JOURS_FR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]

# ---------------------------------------------------------------------------
# OUTILS
# ---------------------------------------------------------------------------

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
})

# Statut de chaque source, affiché en bas de page : nom -> dict(ok, message, heure)
STATUT = {}


def tz_local():
    if ZoneInfo is None:
        return None
    try:
        return ZoneInfo(FUSEAU)
    except Exception:
        return None


def maintenant():
    tz = tz_local()
    return datetime.now(tz) if tz else datetime.now().astimezone()


def noter(source, ok, message=""):
    STATUT[source] = {"ok": ok, "message": message, "heure": maintenant()}


def http_get(url, params=None, ttl_min=0, timeout=25):
    """GET avec cache disque. Si la source tombe, on ressert la dernière version en cache."""
    cle = hashlib.md5((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    fichier = DOSSIER_CACHE / cle
    if ttl_min and fichier.exists() and time.time() - fichier.stat().st_mtime < ttl_min * 60:
        return fichier.read_text(encoding="utf-8")
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        texte = r.text
        DOSSIER_CACHE.mkdir(exist_ok=True)
        fichier.write_text(texte, encoding="utf-8")
        return texte
    except Exception:
        if fichier.exists():  # donnée périmée mais utilisable
            return fichier.read_text(encoding="utf-8")
        raise


def normaliser_index(idx):
    idx = pd.to_datetime(idx)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def nb(x, d=2, signe=False, unite=""):
    """Format français : espace fine pour les milliers, virgule décimale, vrai signe moins."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n.d."
    x = round(float(x), d) + 0.0  # évite l'affichage de "−0"
    s = f"{abs(x):,.{d}f}".replace(",", "\u202f").replace(".", ",")
    if x < 0:
        s = "\u2212" + s
    elif signe and x > 0:
        s = "+" + s
    return s + (("\u00a0" + unite) if unite else "")


def date_fr(d, heure=False):
    if d is None:
        return "n.d."
    txt = f"{JOURS_FR[d.weekday()]} {d.day} {MOIS_FR[d.month - 1]}"
    if heure:
        txt += f" {d.hour:02d}:{d.minute:02d}"
    return txt


def esc(x):
    return html.escape(str(x), quote=True)


# ---------------------------------------------------------------------------
# COLLECTE DES DONNÉES
# ---------------------------------------------------------------------------

def extraire_close(df, tickers):
    """Récupère les cours de clôture d'un yf.download, quel que soit le format de colonnes."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        if "Close" in df.columns.get_level_values(0):
            c = df["Close"]
        elif "Close" in df.columns.get_level_values(1):
            c = df.xs("Close", axis=1, level=1)
        else:
            return pd.DataFrame()
    else:
        if "Close" not in df.columns:
            return pd.DataFrame()
        c = df[["Close"]].rename(columns={"Close": tickers[0]})
    c = c.copy()
    c.index = normaliser_index(c.index)
    return c


def telecharger_yahoo(tickers, periode):
    for essai in range(2):
        try:
            df = yf.download(tickers, period=periode, interval="1d", progress=False,
                             auto_adjust=False, threads=True)
            close = extraire_close(df, tickers)
            if not close.empty:
                return close
        except Exception:
            pass
        time.sleep(3)
    return pd.DataFrame()


def charger_yahoo():
    if yf is None:
        raise RuntimeError("module yfinance absent (pip install yfinance)")
    tickers = [t for t, _ in YAHOO.values()]
    close = telecharger_yahoo(tickers, "2y")
    out = {}
    for cle, (t, _) in YAHOO.items():
        if t in close.columns:
            s = close[t].dropna()
            if len(s):
                out[cle] = s
    for cle in ("us5", "us10", "us30"):  # certains flux cotent le taux x10
        if cle in out and out[cle].iloc[-1] > 20:
            out[cle] = out[cle] / 10
    if "or" not in out:
        raise RuntimeError("cours de l'or introuvable sur Yahoo")
    manquants = [YAHOO[k][1] for k in YAHOO if k not in out]
    noter("Yahoo Finance", True, ("manquants : " + ", ".join(manquants)) if manquants else "")
    return out


def charger_fedfunds(effr):
    """Trajectoire de la Fed pricée par les futures fed funds (CBOT ZQ). Taux implicite = 100 - prix."""
    if yf is None:
        return []
    codes = "FGHJKMNQUVXZ"
    now = maintenant()
    contrats = []
    for i in range(0, 9):
        m = (now.month - 1 + i) % 12 + 1
        a = now.year + (now.month - 1 + i) // 12
        contrats.append((f"ZQ{codes[m - 1]}{str(a)[2:]}.CBT", a, m))
    close = telecharger_yahoo([c[0] for c in contrats], "5d")
    lignes = []
    for t, a, m in contrats:
        if t in close.columns and close[t].dropna().size:
            taux = 100 - float(close[t].dropna().iloc[-1])
            ecart = (taux - effr) * 100 if effr is not None else None
            lignes.append({"mois": f"{MOIS_FR[m - 1]} {a}", "taux": taux, "ecart_pb": ecart})
    noter("Futures fed funds", bool(lignes), "" if lignes else "contrats introuvables, proxy 2 ans utilisé")
    return lignes


def charger_fred_serie(code, depuis):
    txt = http_get("https://fred.stlouisfed.org/graph/fredgraph.csv",
                   {"id": code, "cosd": depuis}, ttl_min=30)
    df = pd.read_csv(io.StringIO(txt))
    dates = pd.to_datetime(df[df.columns[0]], errors="coerce")
    vals = pd.to_numeric(df[df.columns[-1]], errors="coerce")
    s = pd.Series(vals.values, index=dates).dropna()
    s.index = normaliser_index(s.index)
    return s


def charger_fred():
    depuis = (datetime.now() - timedelta(days=4 * 365)).strftime("%Y-%m-%d")
    out, erreurs = {}, []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(charger_fred_serie, code, depuis): cle for cle, (code, _) in FRED.items()}
        for f in cf.as_completed(futs):
            cle = futs[f]
            try:
                s = f.result()
                if len(s):
                    out[cle] = s
            except Exception:
                erreurs.append(FRED[cle][0])
    if not out:
        raise RuntimeError("aucune série FRED récupérée")
    noter("FRED (Fed de St. Louis)", True, ("séries manquantes : " + ", ".join(erreurs)) if erreurs else "")
    return out


def _champ(cles, doit, exclure=()):
    cand = [k for k in cles if all(d in k for d in doit) and not any(e in k for e in exclure)]
    cand.sort(key=len)
    return cand[0] if cand else None


def charger_cot():
    """Positionnement des fonds sur l'or COMEX (code CFTC 088691), rapport désagrégé."""
    sources = [
        ("72hh-3qpy", ("m_money", "long"), ("m_money", "short"), "managed money"),
        ("6dca-aqww", ("noncomm", "long"), ("noncomm", "short"), "non commerciaux"),
    ]
    derniere_erreur = None
    for dataset, cl_long, cl_short, categorie in sources:
        url = f"https://publicreporting.cftc.gov/resource/{dataset}.json"
        for params in (
            {"cftc_contract_market_code": "088691", "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": "400"},
            {"cftc_contract_market_code": "088691", "$limit": "5000"},
        ):
            try:
                lignes = json.loads(http_get(url, params, ttl_min=180))
                if not lignes:
                    continue
                cles = set().union(*[l.keys() for l in lignes])
                exclure = ("change", "pct", "traders", "spread", "_old", "_other", "conc")
                f_long = _champ(cles, cl_long, exclure)
                f_short = _champ(cles, cl_short, exclure)
                f_date = _champ(cles, ("report_date",))
                f_oi = _champ(cles, ("open_interest",), ("change", "pct", "_old", "_other"))
                if not (f_long and f_short and f_date):
                    continue
                df = pd.DataFrame({
                    "date": pd.to_datetime([l.get(f_date) for l in lignes], errors="coerce"),
                    "long": pd.to_numeric([l.get(f_long) for l in lignes], errors="coerce"),
                    "short": pd.to_numeric([l.get(f_short) for l in lignes], errors="coerce"),
                    "oi": pd.to_numeric([l.get(f_oi) for l in lignes], errors="coerce") if f_oi else float("nan"),
                }).dropna(subset=["date", "long", "short"])
                df = df.drop_duplicates("date").sort_values("date").tail(400).reset_index(drop=True)
                df["net"] = df["long"] - df["short"]
                df.attrs["categorie"] = categorie
                noter("CFTC (COT)", True, f"catégorie : {categorie}")
                return df
            except Exception as e:
                derniere_erreur = e
    raise RuntimeError(f"COT indisponible ({derniere_erreur})")


def charger_gld():
    """Avoirs en tonnes du plus gros ETF or (SPDR GLD)."""
    txt = http_get("https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv", ttl_min=120)
    lignes = txt.splitlines()
    debut = next(i for i, l in enumerate(lignes) if "Tonnes" in l)
    lecteur = csv.reader(lignes[debut:])
    entete = next(lecteur)
    col = next(i for i, h in enumerate(entete) if "Tonnes" in h)
    dates, vals = [], []
    for r in lecteur:
        if len(r) > col:
            dates.append(r[0].strip())
            vals.append(r[col].replace(",", "").strip())
    try:
        index = pd.to_datetime(dates, errors="coerce", format="mixed", dayfirst=True)
    except (TypeError, ValueError):
        index = pd.to_datetime(dates, errors="coerce", dayfirst=True)
    s = pd.Series(pd.to_numeric(vals, errors="coerce"), index=index)
    s = s[s.index.notna()].dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    if s.empty:
        raise RuntimeError("fichier GLD illisible")
    noter("SPDR Gold Shares (GLD)", True)
    return s


def charger_calendrier():
    """Calendrier économique de la semaine (ForexFactory) : annonces USD à impact moyen et fort."""
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
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        try:
            d = datetime.fromisoformat(str(e["date"]))
            d = d.astimezone(tz) if tz else d.astimezone()
        except Exception:
            continue
        cle = (e.get("title"), d.isoformat())
        if cle in vus:
            continue
        vus.add(cle)
        out.append({
            "date": d, "titre": e.get("title", ""), "impact": e.get("impact"),
            "prevision": e.get("forecast") or "", "precedent": e.get("previous") or "",
            "reel": e.get("actual") or "",
        })
    out.sort(key=lambda x: x["date"])
    noter("ForexFactory (calendrier)", True, f"{len(out)} annonces USD")
    return out


def charger_news():
    out = {}
    tz = tz_local()
    for theme, requete in THEMES_NEWS:
        try:
            txt = http_get("https://news.google.com/rss/search",
                           {"q": requete + " when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"}, ttl_min=10)
            racine = ET.fromstring(txt.encode("utf-8"))
            items = []
            for it in racine.findall("./channel/item")[:7]:
                titre = it.findtext("title") or ""
                source = it.findtext("source") or ""
                if source and titre.endswith(" - " + source):
                    titre = titre[: -len(" - " + source)]
                try:
                    d = parsedate_to_datetime(it.findtext("pubDate"))
                    d = d.astimezone(tz) if tz else d
                except Exception:
                    d = None
                items.append({"titre": titre, "lien": it.findtext("link") or "#", "source": source, "date": d})
            out[theme] = items
        except Exception:
            out[theme] = []
    noter("Google News", any(out.values()))
    return out


def collecter():
    """Lance toutes les collectes en parallèle. Une source en panne n'empêche pas les autres."""
    data = {"yahoo": {}, "fred": {}, "cot": None, "gld": None, "cal": [], "news": {}, "ff": []}
    taches = {
        "yahoo": (charger_yahoo, "Yahoo Finance"),
        "fred": (charger_fred, "FRED (Fed de St. Louis)"),
        "cot": (charger_cot, "CFTC (COT)"),
        "gld": (charger_gld, "SPDR Gold Shares (GLD)"),
        "cal": (charger_calendrier, "ForexFactory (calendrier)"),
        "news": (charger_news, "Google News"),
    }
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(fn): (cle, nom) for cle, (fn, nom) in taches.items()}
        for f in cf.as_completed(futs):
            cle, nom = futs[f]
            try:
                data[cle] = f.result()
            except Exception as e:
                noter(nom, False, str(e)[:160])
    effr = derniere(data["fred"].get("effr"))
    try:
        data["ff"] = charger_fedfunds(effr)
    except Exception as e:
        noter("Futures fed funds", False, str(e)[:160])
    return data


# ---------------------------------------------------------------------------
# ANALYSE
# ---------------------------------------------------------------------------

def derniere(s):
    if s is None or len(s) == 0:
        return None
    return float(s.dropna().iloc[-1])


def variation(s, n, mode="pct"):
    """Variation sur n observations. mode 'pct' = %, 'pb' = points de base (séries en %), 'abs' = absolue."""
    if s is None:
        return None
    s = s.dropna()
    if len(s) <= n:
        return None
    a, b = float(s.iloc[-1 - n]), float(s.iloc[-1])
    if mode == "pct":
        return (b / a - 1) * 100 if a else None
    if mode == "pb":
        return (b - a) * 100
    return b - a


def glissement_annuel(s):
    """Inflation sur un an à partir d'un indice mensuel."""
    if s is None or len(s) < 13:
        return None, None
    ga = (s / s.shift(12) - 1) * 100
    ga = ga.dropna()
    return float(ga.iloc[-1]), float(ga.iloc[-2]) if len(ga) > 1 else None


def correlation(or_s, autre, mode, fenetre):
    if or_s is None or autre is None:
        return None
    r_or = or_s.pct_change()
    r_x = autre.pct_change() if mode == "pct" else autre.diff()
    df = pd.concat([r_or, r_x], axis=1, join="inner").dropna().tail(fenetre)
    if len(df) < fenetre * 0.7:
        return None
    c = df.corr().iloc[0, 1]
    return None if pd.isna(c) else float(c)


def signal(nom, score, valeur, lecture):
    return {"nom": nom, "score": score, "valeur": valeur, "lecture": lecture}


def biais_fondamental(y, f, cot, gld):
    """Chaque règle vote -1 (baissier), 0 ou +1 (haussier). Les seuils sont dans SEUILS."""
    S = SEUILS
    sig = []

    d = variation(f.get("reel10"), 5, "pb")
    if d is not None:
        sc = -1 if d >= S["reel_pb"] else (1 if d <= -S["reel_pb"] else 0)
        lect = {-1: "Taux réels en hausse : détenir de l'or coûte plus cher",
                1: "Taux réels en baisse : l'or redevient attractif",
                0: "Taux réels stables"}[sc]
        sig.append(signal("Taux réels 10 ans", sc, f"{nb(d, 0, True)} pb sur 5 j", lect))

    d = variation(y.get("dxy"), 5, "pct")
    if d is not None:
        sc = -1 if d >= S["dxy_pct"] else (1 if d <= -S["dxy_pct"] else 0)
        lect = {-1: "Dollar en hausse : pression directe sur l'or",
                1: "Dollar en baisse : soutien direct à l'or",
                0: "Dollar sans direction nette"}[sc]
        sig.append(signal("Dollar (DXY)", sc, f"{nb(d, 2, True)} % sur 5 j", lect))

    d = variation(f.get("us2"), 5, "pb")
    if d is not None:
        sc = -1 if d >= S["us2_pb"] else (1 if d <= -S["us2_pb"] else 0)
        lect = {-1: "Le marché price une Fed plus dure",
                1: "Le marché price une Fed plus accommodante",
                0: "Anticipations Fed stables"}[sc]
        sig.append(signal("Anticipations Fed (taux 2 ans)", sc, f"{nb(d, 0, True)} pb sur 5 j", lect))

    d10, ddxy = variation(y.get("us10"), 5, "pb"), variation(y.get("dxy"), 5, "pct")
    if d10 is not None and ddxy is not None:
        defiance = d10 >= S["us10_pb"] and ddxy <= -S["dxy_pct"] / 2
        sig.append(signal("Défiance envers les actifs US", 1 if defiance else 0,
                          f"10 ans {nb(d10, 0, True)} pb, DXY {nb(ddxy, 2, True)} %",
                          "Taux longs en hausse ET dollar en baisse : thème dévaluation, favorable à l'or"
                          if defiance else "Pas de signal : taux et dollar évoluent dans le même sens"))

    d = variation(y.get("brent"), 5, "pct")
    if d is not None:
        brut = -1 if d >= S["brent_pct"] else (1 if d <= -S["brent_pct"] else 0)
        sc = brut if REGIME_PETROLE == "inflation" else -brut
        if REGIME_PETROLE == "inflation":
            lect = {-1: "Pétrole en hausse : plus d'inflation, Fed plus dure",
                    1: "Pétrole en baisse : moins d'inflation, pression Fed qui se relâche",
                    0: "Pétrole sans mouvement marqué"}[sc]
        else:
            lect = {1: "Pétrole en hausse : prime de risque géopolitique",
                    -1: "Pétrole en baisse : la prime de risque se dégonfle",
                    0: "Pétrole sans mouvement marqué"}[sc]
        sig.append(signal("Pétrole (Brent)", sc, f"{nb(d, 1, True)} % sur 5 j", lect))

    if cot is not None and len(cot) > 20:
        hist = cot["net"].tail(156)
        pct = float((hist <= hist.iloc[-1]).mean() * 100)
        sc = -1 if pct >= S["cot_haut"] else (1 if pct <= S["cot_bas"] else 0)
        lect = {-1: "Fonds très longs : risque de liquidation si ça casse",
                1: "Fonds peu exposés : de la marge pour racheter",
                0: "Positionnement des fonds dans la moyenne"}[sc]
        sig.append(signal("Positionnement des fonds (COT)", sc, f"percentile 3 ans : {nb(pct, 0)}", lect))

    if gld is not None and len(gld) > 6:
        d = variation(gld, 5, "abs")
        sc = 1 if d >= S["gld_t"] else (-1 if d <= -S["gld_t"] else 0)
        lect = {1: "Entrées dans les ETF : les investisseurs achètent",
                -1: "Sorties des ETF : les investisseurs allègent",
                0: "Flux ETF calmes"}[sc]
        sig.append(signal("Flux ETF (GLD)", sc, f"{nb(d, 1, True)} t sur 5 j", lect))

    total = sum(s["score"] for s in sig)
    if total >= 2:
        verdict = "Haussier"
    elif total <= -2:
        verdict = "Baissier"
    else:
        verdict = "Neutre"
    return {"signaux": sig, "total": total, "n": len(sig), "verdict": verdict}


def divergence(y, f):
    """Or qui monte malgré taux réels et dollar en hausse = demande sous-jacente forte (et l'inverse)."""
    g, dx, rr = variation(y.get("or"), 5), variation(y.get("dxy"), 5), variation(f.get("reel10"), 5, "pb")
    if None in (g, dx, rr):
        return None
    if g > 0.5 and dx > 0.2 and rr > 3:
        return ("Or en hausse malgré des taux réels et un dollar en hausse : la demande de fond "
                "(banques centrales, physique, couverture) absorbe la pression macro. Signe de force.")
    if g < -0.5 and dx < -0.2 and rr < -3:
        return ("Or en baisse alors que taux réels et dollar reculent : faiblesse anormale, "
                "souvent des liquidations de positions. Signe de fragilité.")
    return None


def prochaine_annonce(cal):
    now = maintenant()
    avant, apres = FENETRE_NEWS
    hauts = [e for e in cal if e["impact"] == "High"]
    for e in hauts:
        minutes = (e["date"] - now).total_seconds() / 60
        if minutes >= -apres:
            return e, (-apres <= minutes <= avant), minutes
    return None, False, None


def analyser(data):
    y, f, cot, gld = data["yahoo"], data["fred"], data["cot"], data["gld"]
    a = {}
    a["biais"] = biais_fondamental(y, f, cot, gld)
    a["divergence"] = divergence(y, f)

    or_s = y.get("or")
    a["or"] = {
        "prix": derniere(or_s), "d1": variation(or_s, 1), "d5": variation(or_s, 5), "d20": variation(or_s, 20),
        "sma50": float(or_s.tail(50).mean()) if or_s is not None and len(or_s) >= 50 else None,
        "sma200": float(or_s.tail(200).mean()) if or_s is not None and len(or_s) >= 200 else None,
        "haut52": float(or_s.tail(252).max()) if or_s is not None else None,
        "bas52": float(or_s.tail(252).min()) if or_s is not None else None,
    }
    gvz = derniere(y.get("gvz"))
    a["range_jour"] = (a["or"]["prix"] * gvz / 100 / math.sqrt(252)) if gvz and a["or"]["prix"] else None
    ag = derniere(y.get("argent"))
    a["ratio_or_argent"] = a["or"]["prix"] / ag if ag and a["or"]["prix"] else None

    # Or dans d'autres devises : si l'or baisse en USD mais pas en EUR, c'est une histoire de dollar
    a["devises"] = []
    for nom, cle, op in (("Euro", "eurusd", "div"), ("Yen", "usdjpy", "mul"),
                         ("Yuan", "usdcny", "mul"), ("Roupie", "usdinr", "mul")):
        fx = y.get(cle)
        if or_s is not None and fx is not None:
            df = pd.concat([or_s, fx], axis=1, join="inner").dropna()
            if len(df) > 6:
                serie = df.iloc[:, 0] / df.iloc[:, 1] if op == "div" else df.iloc[:, 0] * df.iloc[:, 1]
                a["devises"].append({"nom": nom, "prix": float(serie.iloc[-1]),
                                     "d5": variation(serie, 5), "d20": variation(serie, 20)})

    # Qui mène l'or : corrélations des variations quotidiennes
    paires = [("Dollar (DXY)", y.get("dxy"), "pct"), ("Taux 10 ans", y.get("us10"), "diff"),
              ("Taux réel 10 ans", f.get("reel10"), "diff"), ("Brent", y.get("brent"), "pct"),
              ("S&P 500", y.get("spx"), "pct"), ("USD/JPY", y.get("usdjpy"), "pct")]
    a["correlations"] = [{"nom": n, "c20": correlation(or_s, s, m, 20), "c60": correlation(or_s, s, m, 60)}
                         for n, s, m in paires if s is not None]
    valides = [c for c in a["correlations"] if c["c20"] is not None]
    a["moteur"] = max(valides, key=lambda c: abs(c["c20"])) if valides else None

    # Fed
    effr, us2 = derniere(f.get("effr")), derniere(f.get("us2"))
    a["fed"] = {"effr": effr, "us2": us2, "spread_pb": (us2 - effr) * 100 if effr is not None and us2 is not None else None,
                "futures": data.get("ff") or []}

    # COT
    if cot is not None and len(cot) > 20:
        hist = cot["net"].tail(156)
        der = cot.iloc[-1]
        a["cot"] = {"date": der["date"], "net": float(der["net"]), "long": float(der["long"]),
                    "short": float(der["short"]), "chg": float(cot["net"].iloc[-1] - cot["net"].iloc[-2]),
                    "pct3a": float((hist <= hist.iloc[-1]).mean() * 100),
                    "pct_oi": float(der["net"] / der["oi"] * 100) if der["oi"] == der["oi"] and der["oi"] else None,
                    "p15": float(hist.quantile(SEUILS["cot_bas"] / 100)),
                    "p85": float(hist.quantile(SEUILS["cot_haut"] / 100)),
                    "categorie": cot.attrs.get("categorie", "")}
    else:
        a["cot"] = None

    a["gld"] = ({"t": derniere(gld), "d5": variation(gld, 5, "abs"), "d20": variation(gld, 20, "abs"),
                 "date": gld.index[-1]} if gld is not None and len(gld) > 21 else None)

    # Macro US
    macro = []
    for cle, lib in (("cpi", "Inflation CPI (sur 1 an)"), ("cpi_core", "Inflation CPI core (sur 1 an)"),
                     ("pce_core", "PCE core (sur 1 an)")):
        s = f.get(cle)
        v, p = glissement_annuel(s)
        if v is not None:
            macro.append({"nom": lib, "val": nb(v, 1, unite="%"), "prec": nb(p, 1, unite="%"),
                          "date": s.index[-1], "hausse": v > p if p is not None else None})
    s = f.get("chomage")
    if s is not None and len(s) > 1:
        macro.append({"nom": "Taux de chômage", "val": nb(s.iloc[-1], 1, unite="%"),
                      "prec": nb(s.iloc[-2], 1, unite="%"), "date": s.index[-1], "hausse": s.iloc[-1] > s.iloc[-2]})
    s = f.get("nfp")
    if s is not None and len(s) > 2:
        d = s.diff().dropna()
        macro.append({"nom": "Créations d'emplois (NFP)", "val": nb(d.iloc[-1], 0, True, "k"),
                      "prec": nb(d.iloc[-2], 0, True, "k"), "date": s.index[-1], "hausse": d.iloc[-1] > d.iloc[-2]})
    s = f.get("claims")
    if s is not None and len(s) > 1:
        macro.append({"nom": "Inscriptions hebdo au chômage", "val": nb(s.iloc[-1] / 1000, 0, unite="k"),
                      "prec": nb(s.iloc[-2] / 1000, 0, unite="k"), "date": s.index[-1],
                      "hausse": s.iloc[-1] > s.iloc[-2]})
    a["macro"] = macro

    a["annonce"], a["zone_news"], a["minutes_annonce"] = prochaine_annonce(data["cal"])
    return a


# ---------------------------------------------------------------------------
# GRAPHIQUES (SVG générés en Python : la page reste un seul fichier autonome)
# ---------------------------------------------------------------------------

def sparkline(s, n=60, w=88, h=22):
    if s is None:
        return ""
    s = s.dropna().tail(n)
    if len(s) < 5:
        return ""
    lo, hi = float(s.min()), float(s.max())
    rng = (hi - lo) or 1
    pts = " ".join(f"{1 + i * (w - 4) / (len(s) - 1):.1f},{h - 2 - (float(v) - lo) / rng * (h - 4):.1f}"
                   for i, v in enumerate(s))
    lx, ly = pts.split(" ")[-1].split(",")
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="var(--mute)" stroke-width="1.2"/>'
            f'<circle cx="{lx}" cy="{ly}" r="2" fill="var(--ink)"/></svg>')


def _axe_x(dates, x_of, y_pos):
    out = []
    n = len(dates)
    for i in (0, n // 2, n - 1):
        d = dates[i]
        anchor = "start" if i == 0 else ("end" if i == n - 1 else "middle")
        out.append(f'<text x="{x_of(i):.1f}" y="{y_pos}" text-anchor="{anchor}" class="ax">'
                   f'{d.day} {MOIS_FR[d.month - 1]} {str(d.year)[2:]}</text>')
    return "".join(out)


def graphique_double(s1, s2, jours=365, inverser2=True, d1=0, d2=2, w=720, h=240):
    if s1 is None or s2 is None:
        return '<p class="vide">Données indisponibles pour ce graphique.</p>'
    df = pd.concat([s1, s2], axis=1, join="inner").dropna()
    df = df[df.index >= df.index[-1] - pd.Timedelta(days=jours)]
    if len(df) < 10:
        return '<p class="vide">Données insuffisantes pour ce graphique.</p>'
    pl, pr, pt, pb = 54, 54, 14, 26
    W, H = w - pl - pr, h - pt - pb
    a, b = df.iloc[:, 0].astype(float), df.iloc[:, 1].astype(float)
    lo1, hi1, lo2, hi2 = a.min(), a.max(), b.min(), b.max()
    x = lambda i: pl + i * W / (len(df) - 1)
    y1 = lambda v: pt + (hi1 - v) / ((hi1 - lo1) or 1) * H
    y2 = lambda v: pt + (((v - lo2) if inverser2 else (hi2 - v)) / ((hi2 - lo2) or 1)) * H
    p1 = "M" + " L".join(f"{x(i):.1f},{y1(v):.1f}" for i, v in enumerate(a))
    p2 = "M" + " L".join(f"{x(i):.1f},{y2(v):.1f}" for i, v in enumerate(b))
    grille = "".join(f'<line x1="{pl}" x2="{pl + W}" y1="{pt + k * H / 4:.1f}" y2="{pt + k * H / 4:.1f}" class="gr"/>'
                     for k in range(5))
    haut2, bas2 = (lo2, hi2) if inverser2 else (hi2, lo2)
    labels = (f'<text x="{pl - 8}" y="{pt + 4}" text-anchor="end" class="ax c1">{nb(hi1, d1)}</text>'
              f'<text x="{pl - 8}" y="{pt + H}" text-anchor="end" class="ax c1">{nb(lo1, d1)}</text>'
              f'<text x="{pl + W + 8}" y="{pt + 4}" class="ax c2">{nb(haut2, d2)}</text>'
              f'<text x="{pl + W + 8}" y="{pt + H}" class="ax c2">{nb(bas2, d2)}</text>')
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Graphique comparatif">'
            f'{grille}<path d="{p2}" class="l2"/><path d="{p1}" class="l1"/>{labels}'
            f'{_axe_x(list(df.index), x, h - 6)}</svg>')


def graphique_cot(cot, info, w=720, h=240):
    if cot is None or info is None:
        return '<p class="vide">Données COT indisponibles.</p>'
    df = cot.tail(156)
    net = df["net"].astype(float)
    pl, pr, pt, pb = 58, 20, 14, 26
    W, H = w - pl - pr, h - pt - pb
    lo, hi = min(net.min(), 0), max(net.max(), 0)
    x = lambda i: pl + i * W / (len(df) - 1)
    y = lambda v: pt + (hi - v) / ((hi - lo) or 1) * H
    p = "M" + " L".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(net))
    aire = p + f" L{x(len(df) - 1):.1f},{y(0):.1f} L{x(0):.1f},{y(0):.1f} Z"
    lignes = ""
    for val, lib in ((info["p85"], f"{SEUILS['cot_haut']}e pct"), (info["p15"], f"{SEUILS['cot_bas']}e pct")):
        lignes += (f'<line x1="{pl}" x2="{pl + W}" y1="{y(val):.1f}" y2="{y(val):.1f}" class="seuil"/>'
                   f'<text x="{pl + W}" y="{y(val) - 4:.1f}" text-anchor="end" class="ax">{lib}</text>')
    zero = f'<line x1="{pl}" x2="{pl + W}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="gr"/>'
    lx, ly = x(len(df) - 1), y(net.iloc[-1])
    labels = (f'<text x="{pl - 8}" y="{pt + 4}" text-anchor="end" class="ax">{nb(hi / 1000, 0)}k</text>'
              f'<text x="{pl - 8}" y="{pt + H}" text-anchor="end" class="ax">{nb(lo / 1000, 0)}k</text>')
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Position nette des fonds sur 3 ans">'
            f'{zero}<path d="{aire}" class="aire"/><path d="{p}" class="l1"/>{lignes}'
            f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="3.5" class="pt"/>{labels}'
            f'{_axe_x(list(df["date"]), x, h - 6)}</svg>')


# ---------------------------------------------------------------------------
# PAGE HTML
# ---------------------------------------------------------------------------

CSS = """
:root{
  --bg:#17212b; --band:#1c2833; --line:#2b3a48; --ink:#e9e3d5; --mute:#8d9dab;
  --brass:#cfa54b; --steel:#86a9c8; --up:#5dbb8d; --down:#e3685b; --amber:#f0a94b;
  --cond:"Barlow Semi Condensed","Arial Narrow","Roboto Condensed",system-ui,sans-serif;
  --text:"Barlow",system-ui,-apple-system,"Segoe UI",sans-serif;
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink)}
body{font:15px/1.45 var(--text);-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
a:hover{text-decoration:underline;text-decoration-color:var(--mute)}
.wrap{max-width:1480px;margin:0 auto;padding:18px 28px 40px}
.num,.m td.v,.m td.d{font-family:var(--cond);font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--mute)}

header.top{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;
  padding-bottom:14px;border-bottom:1px solid var(--line)}
header.top h1{font:600 24px/1 var(--cond);margin:0;letter-spacing:.01em}
header.top h1 span{display:inline-block;width:10px;height:18px;background:var(--brass);margin-right:10px;
  vertical-align:-2px;border-radius:1px}
header.top .meta{color:var(--mute);font-size:13.5px}
#age.perime{color:var(--amber);font-weight:600}
button.copier{font:500 14px var(--cond);color:var(--bg);background:var(--brass);border:0;border-radius:3px;
  padding:8px 14px;cursor:pointer}
button.copier:focus-visible,a:focus-visible{outline:2px solid var(--ink);outline-offset:2px}

.hero{display:grid;grid-template-columns:290px minmax(0,1fr) 300px;gap:32px;padding:22px 0 26px;
  border-bottom:1px solid var(--line)}
.label{color:var(--mute);font-size:13.5px}
.prix{font:600 52px/1 var(--cond);font-variant-numeric:tabular-nums;color:var(--brass);margin:6px 0 10px}
.prix small{font-size:20px;color:var(--mute);font-weight:500;margin-left:4px}
.stats{display:flex;gap:18px;font:500 15px var(--cond)}
.stats div span{display:block;color:var(--mute);font:400 12.5px var(--text)}
.sous{margin-top:14px;color:var(--mute);font-size:13.5px;line-height:1.55}
.sous b{color:var(--ink);font-weight:500;font-family:var(--cond)}

.biais h2{font:500 15px var(--text);color:var(--mute);margin:0}
.verdict{display:flex;align-items:baseline;gap:14px;margin:4px 0 12px}
.verdict strong{font:700 44px/1 var(--cond)}
.verdict span{color:var(--mute);font-family:var(--cond);font-size:16px}
.balance{position:relative;display:flex;height:30px;background:var(--band);border-radius:2px}
.balance .moitie{flex:1;display:flex;gap:3px;padding:4px}
.balance .g{justify-content:flex-end}
.balance .axe{position:absolute;left:50%;top:-5px;bottom:-5px;width:2px;background:var(--ink);opacity:.5}
.bloc{height:100%;border-radius:1px}
.bloc.b{background:var(--down)} .bloc.h{background:var(--up)}
.balance-leg{display:flex;justify-content:space-between;color:var(--mute);font-size:12.5px;margin-top:5px}
.signaux{display:grid;grid-template-columns:18px minmax(150px,max-content) minmax(110px,max-content) 1fr;
  gap:5px 12px;margin-top:14px;font-size:14px;align-items:baseline}
.signaux .ic{font-size:11px}
.signaux .n{font-weight:500}
.signaux .val{font-family:var(--cond);color:var(--mute);font-variant-numeric:tabular-nums}
.signaux .lec{color:var(--mute)}
.note{margin-top:14px;padding:10px 12px;border-left:3px solid var(--amber);background:rgba(240,169,75,.08);
  font-size:14px}

.evt{align-self:start;padding:14px 16px;background:var(--band);border-radius:3px;border-top:3px solid var(--line)}
.evt.zone{border-top-color:var(--amber);background:rgba(240,169,75,.10)}
.evt .titre{font:600 21px/1.15 var(--cond);margin:6px 0 4px}
.evt .quand{font-family:var(--cond);font-size:16px}
.evt .cpt{color:var(--brass);font:600 17px var(--cond);margin-top:2px}
.evt .pp{display:flex;gap:18px;margin-top:10px;font-family:var(--cond)}
.evt .pp span{display:block;color:var(--mute);font:400 12.5px var(--text)}
.evt .alerte{display:none;margin-top:12px;font-weight:600;color:var(--amber)}
.evt.zone .alerte{display:block}

section{padding:22px 0;border-bottom:1px solid var(--line)}
section h2{font:600 18px var(--cond);margin:0 0 12px}
section h3{font:500 14.5px var(--text);color:var(--mute);margin:18px 0 8px}
.grille3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:36px}
.grille2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:36px}
.grille4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:28px}
.grille-cal{grid-template-columns:minmax(0,1.6fr) minmax(0,1fr)}
.defile{overflow-x:auto}
.legende{color:var(--mute);font-size:13px;margin:-6px 0 12px}

table{border-collapse:collapse;width:100%}
.m td{padding:5px 0;border-bottom:1px solid rgba(43,58,72,.6);vertical-align:middle}
.m td.l{padding-right:10px}
.m td.v{text-align:right;font-size:16px;font-weight:500;padding-right:4px;white-space:nowrap}
.m td.d{text-align:right;width:62px;font-size:14px;padding-left:12px;white-space:nowrap}
.m td.s{width:96px;text-align:right}
.m th{font:400 12.5px var(--text);color:var(--mute);text-align:right;padding:0 0 4px 12px;white-space:nowrap}
.m th:first-child{text-align:left;padding-left:0}
.spark{display:inline-block;vertical-align:middle}

.pctbar{position:relative;height:8px;background:var(--band);border-radius:4px;margin:8px 0 4px}
.pctbar i{position:absolute;top:-3px;width:2px;height:14px;background:var(--mute)}
.pctbar b{position:absolute;top:-4px;width:10px;height:16px;margin-left:-5px;background:var(--brass);border-radius:2px}
.pctleg{display:flex;justify-content:space-between;color:var(--mute);font-size:12px}
.kv{display:grid;grid-template-columns:1fr auto;gap:4px 12px;font-size:14.5px}
.kv span:nth-child(even){font-family:var(--cond);font-variant-numeric:tabular-nums;text-align:right}

.corr{display:grid;grid-template-columns:130px 1fr 54px;gap:6px 12px;align-items:center;font-size:14px}
.cbar{position:relative;height:12px;background:var(--band)}
.cbar::after{content:"";position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--mute)}
.cbar b{position:absolute;top:0;bottom:0;background:var(--steel)}
.cbar i{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink)}
.corr .cv{font-family:var(--cond);text-align:right;font-variant-numeric:tabular-nums}

.chart{width:100%;height:auto;display:block}
.chart .gr{stroke:var(--line);stroke-width:1}
.chart .l1{fill:none;stroke:var(--brass);stroke-width:1.8}
.chart .l2{fill:none;stroke:var(--steel);stroke-width:1.5}
.chart .aire{fill:var(--brass);opacity:.12}
.chart .seuil{stroke:var(--mute);stroke-dasharray:4 4;stroke-width:1}
.chart .pt{fill:var(--brass)}
.chart .ax{fill:var(--mute);font:12px var(--cond)}
.chart .c1{fill:var(--brass)} .chart .c2{fill:var(--steel)}
.cle{display:flex;gap:18px;font-size:13px;color:var(--mute);margin-bottom:6px}
.cle i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:6px}

.cal td,.cal th{padding:6px 10px 6px 0;border-bottom:1px solid rgba(43,58,72,.6);text-align:left;font-size:14px}
.cal th{font-weight:400;color:var(--mute);font-size:12.5px}
.cal td.num{font-size:15px}
.cal tr.passe td{color:var(--mute)}
.cal .imp{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}
.cal .imp.High{background:var(--down)} .cal .imp.Medium{background:var(--amber)}

.routine{margin:0;padding-left:22px;font-size:14.5px;line-height:1.5}
.routine li{padding:5px 0 5px 4px}
.routine li::marker{font-family:var(--cond);color:var(--brass);font-weight:600}
.routine b{font-weight:600}
.news ul{list-style:none;margin:0;padding:0}
.news li{padding:7px 0;border-bottom:1px solid rgba(43,58,72,.6);font-size:14px;line-height:1.35}
.news li small{display:block;color:var(--mute);font-size:12.5px;margin-top:2px}
.vide{color:var(--mute);font-size:14px}

footer{padding-top:18px;color:var(--mute);font-size:13px}
footer ul{list-style:none;padding:0;margin:8px 0 12px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:4px 24px}
footer .ok::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--up);margin-right:8px}
footer .ko::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--down);margin-right:8px}

@media (max-width:1100px){
  .hero{grid-template-columns:1fr 1fr}.hero .biais{grid-column:1/-1;order:3}
  .grille3,.grille4{grid-template-columns:1fr 1fr}
}
@media (max-width:720px){
  .wrap{padding:14px 16px 32px}
  .hero,.grille3,.grille2,.grille4,.grille-cal{grid-template-columns:1fr}
  .m td.s{display:none}
  .signaux{grid-template-columns:18px 1fr}.signaux .val,.signaux .lec{grid-column:2}
  .prix{font-size:44px}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
"""

JS = """
function fmtDelai(m){
  if (m >= 1440){ var j = Math.floor(m/1440), h = Math.floor((m%1440)/60); return 'dans ' + j + ' j ' + h + ' h'; }
  if (m >= 60){ return 'dans ' + Math.floor(m/60) + ' h ' + (m%60) + ' min'; }
  if (m > 0){ return 'dans ' + m + ' min'; }
  if (m === 0){ return 'maintenant'; }
  return 'publiée il y a ' + (-m) + ' min';
}
function majCompteurs(){
  document.querySelectorAll('[data-cible]').forEach(function(el){
    var t = new Date(el.getAttribute('data-cible')).getTime();
    var m = Math.round((t - Date.now())/60000);
    el.textContent = fmtDelai(m);
    var bloc = el.closest('.evt');
    if (bloc){
      var avant = +bloc.getAttribute('data-avant'), apres = +bloc.getAttribute('data-apres');
      bloc.classList.toggle('zone', m <= avant && m >= -apres);
    }
  });
}
majCompteurs(); setInterval(majCompteurs, 30000);
function majAge(){
  var el = document.getElementById('age'); if (!el) return;
  var m = Math.max(0, Math.round((Date.now() - new Date(el.getAttribute('data-maj')).getTime())/60000));
  var j = new Date().getDay(), seuil = (j === 0 || j === 6) ? 240 : 45;
  var txt = m < 60 ? m + ' min' : Math.floor(m/60) + ' h ' + (m%60) + ' min';
  el.textContent = '(il y a ' + txt + (m > seuil ? ', données anciennes' : '') + ')';
  el.classList.toggle('perime', m > seuil);
}
majAge(); setInterval(majAge, 30000);
document.getElementById('copier').addEventListener('click', function(){
  var zone = document.getElementById('brief'), bouton = this;
  function fait(){ bouton.textContent = 'Brief copié'; setTimeout(function(){ bouton.textContent = 'Copier le brief pour Claude'; }, 2500); }
  if (navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(zone.value).then(fait, function(){ zone.select(); document.execCommand('copy'); fait(); });
  } else { zone.style.display='block'; zone.select(); document.execCommand('copy'); zone.style.display='none'; fait(); }
});
"""


def classe_effet(val, effet):
    """Couleur selon l'effet sur l'or : vert = favorable à l'or, rouge = défavorable."""
    if val is None or effet == 0 or abs(val) < 1e-9:
        return "flat"
    return "up" if val * effet > 0 else "down"


def ligne_metrique(lib, s, mode, dec, effet, unite=""):
    if s is None or len(s) == 0:
        return f'<tr><td class="l">{esc(lib)}</td><td class="v flat" colspan="4">n.d.</td></tr>'
    der = derniere(s)
    dec_d = 0 if mode == "pb" else (2 if mode == "pct" else 1)
    d1, d5 = variation(s, 1, mode), variation(s, 5, mode)
    d1, d5 = (round(v, dec_d) if v is not None else None for v in (d1, d5))
    return (f'<tr><td class="l">{esc(lib)}</td><td class="v">{nb(der, dec)}{unite}</td>'
            f'<td class="d {classe_effet(d1, effet)}">{nb(d1, dec_d, True)}</td>'
            f'<td class="d {classe_effet(d5, effet)}">{nb(d5, dec_d, True)}</td>'
            f'<td class="s">{sparkline(s)}</td></tr>')


def entete_metriques(mode_lib=""):
    return '<tr><th></th><th>Dernier</th><th>1 j</th><th>5 j</th><th>60 j</th></tr>'


def bloc_hero(a):
    o = a["or"]
    stats = "".join(f'<div><span>{lib}</span><b class="{classe_effet(v, 1)}">{nb(v, 2, True)} %</b></div>'
                    for lib, v in (("1 jour", o["d1"]), ("5 jours", o["d5"]), ("20 jours", o["d20"])))
    tendance = ""
    if o["sma50"] and o["sma200"] and o["prix"]:
        if o["prix"] > o["sma50"] > o["sma200"]:
            tendance = "au-dessus de ses moyennes 50 et 200 jours (tendance haussière)"
        elif o["prix"] < o["sma50"] < o["sma200"]:
            tendance = "sous ses moyennes 50 et 200 jours (tendance baissière)"
        else:
            tendance = "entre ses moyennes 50 et 200 jours (tendance indécise)"
    rng = f'<b>±{nb(a["range_jour"], 0)} $</b>' if a["range_jour"] else "n.d."
    m = a["moteur"]
    moteur = (f'Moteur dominant sur 20 j : <b>{esc(m["nom"])}</b> ({nb(m["c20"], 2, True)})<br>' if m else "")
    or_html = (f'<div><div class="label">Or, future COMEX (quelques $ d\'écart avec ton XAU/USD spot)</div>'
               f'<div class="prix">{nb(o["prix"], 1)}<small>$</small></div><div class="stats">{stats}</div>'
               f'<div class="sous">Amplitude attendue aujourd\'hui (1 écart-type, GVZ) : {rng}<br>'
               f'Plus haut 52 sem. <b>{nb(o["haut52"], 0)}</b>, plus bas <b>{nb(o["bas52"], 0)}</b><br>'
               f'{moteur}{"Prix " + tendance if tendance else ""}</div></div>')

    b = a["biais"]
    coul = {"Haussier": "up", "Baissier": "down"}.get(b["verdict"], "flat")
    n = max(b["n"], 1)
    largeur = f"calc((100% - {3 * (n - 1)}px) / {n})"
    baiss = "".join(f'<div class="bloc b" style="width:{largeur}" title="{esc(s["nom"])}"></div>'
                    for s in b["signaux"] if s["score"] < 0)
    hauss = "".join(f'<div class="bloc h" style="width:{largeur}" title="{esc(s["nom"])}"></div>'
                    for s in b["signaux"] if s["score"] > 0)
    lignes = ""
    for s in b["signaux"]:
        ic = {1: '<span class="ic up">▲</span>', -1: '<span class="ic down">▼</span>'}.get(s["score"], '<span class="ic flat">●</span>')
        lignes += (f'{ic}<span class="n">{esc(s["nom"])}</span><span class="val">{esc(s["valeur"])}</span>'
                   f'<span class="lec">{esc(s["lecture"])}</span>')
    div = f'<div class="note">{esc(a["divergence"])}</div>' if a["divergence"] else ""
    biais_html = (f'<div class="biais"><h2>Biais fondamental du jour</h2>'
                  f'<div class="verdict"><strong class="{coul}">{b["verdict"]}</strong>'
                  f'<span>score {nb(b["total"], 0, True) if b["total"] else "0"} sur {b["n"]} signaux</span></div>'
                  f'<div class="balance"><div class="moitie g">{baiss}</div><div class="moitie">{hauss}</div>'
                  f'<div class="axe"></div></div>'
                  f'<div class="balance-leg"><span>Défavorable à l\'or</span><span>Favorable à l\'or</span></div>'
                  f'<div class="signaux">{lignes}</div>{div}</div>')

    e = a["annonce"]
    avant, apres = FENETRE_NEWS
    if e:
        evt_html = (f'<div class="evt{" zone" if a["zone_news"] else ""}" data-avant="{avant}" data-apres="{apres}">'
                    f'<div class="label">Prochaine annonce USD à fort impact</div>'
                    f'<div class="titre">{esc(e["titre"])}</div>'
                    f'<div class="quand">{date_fr(e["date"], True)} (Paris)</div>'
                    f'<div class="cpt" data-cible="{e["date"].isoformat()}"></div>'
                    f'<div class="pp"><div><span>Prévision</span>{esc(e["prevision"] or "n.d.")}</div>'
                    f'<div><span>Précédent</span>{esc(e["precedent"] or "n.d.")}</div></div>'
                    f'<div class="alerte">Zone news : pas de nouvelle position ({avant} min avant, {apres} min après)</div></div>')
    else:
        evt_html = ('<div class="evt"><div class="label">Prochaine annonce USD à fort impact</div>'
                    '<p class="vide">Aucune annonce à fort impact trouvée dans le calendrier.</p></div>')
    return f'<div class="hero">{or_html}{biais_html}{evt_html}</div>'


def bloc_taux(y, f, a):
    rows = [
        ("Taux réel 10 ans", f.get("reel10"), "pb", 2, -1, " %"),
        ("Taux US 2 ans", f.get("us2"), "pb", 2, -1, " %"),
        ("Taux US 10 ans", y.get("us10"), "pb", 2, -1, " %"),
        ("Taux US 30 ans", y.get("us30"), "pb", 2, -1, " %"),
        ("Inflation anticipée 10 ans", f.get("be10"), "pb", 2, 1, " %"),
        ("Inflation 5 ans dans 5 ans", f.get("fwd5y5y"), "pb", 2, 1, " %"),
        ("Fed funds effectif", f.get("effr"), "pb", 2, -1, " %"),
    ]
    t = "".join(ligne_metrique(*r) for r in rows)
    fed = a["fed"]
    html_fed = '<h3>Ce que le marché price pour la Fed</h3><div class="kv">'
    html_fed += f'<span>Écart taux 2 ans et fed funds</span><span>{nb(fed["spread_pb"], 0, True)} pb</span></div>'
    if fed["futures"]:
        html_fed += ('<table class="m" style="margin-top:8px"><tr><th>Futures fed funds</th><th>Taux implicite</th>'
                     '<th>Écart</th><th>Hausses</th></tr>')
        for r in fed["futures"][:7]:
            ec = r["ecart_pb"]
            html_fed += (f'<tr><td class="l">{esc(r["mois"])}</td><td class="v">{nb(r["taux"], 2)} %</td>'
                         f'<td class="d {classe_effet(ec, -1)}">{nb(ec, 0, True)} pb</td>'
                         f'<td class="d">{nb(ec / 25 if ec is not None else None, 1, True)}</td></tr>')
        html_fed += '</table>'
    else:
        html_fed += '<p class="vide">Futures fed funds indisponibles : fie-toi à l\'écart 2 ans ci-dessus.</p>'
    return f'<div><h2>Taux et Fed</h2><table class="m">{entete_metriques("")}{t}</table>{html_fed}</div>'


def bloc_marches(y, f, a):
    e_petrole = -1 if REGIME_PETROLE == "inflation" else 1
    rows = [
        ("Dollar index (DXY)", y.get("dxy"), "pct", 2, -1, ""),
        ("EUR/USD", y.get("eurusd"), "pct", 4, 1, ""),
        ("USD/JPY", y.get("usdjpy"), "pct", 2, -1, ""),
        ("USD/CNY", y.get("usdcny"), "pct", 4, -1, ""),
        ("Brent", y.get("brent"), "pct", 2, e_petrole, " $"),
        ("WTI", y.get("wti"), "pct", 2, e_petrole, " $"),
        ("S&P 500", y.get("spx"), "pct", 0, 0, ""),
        ("VIX", y.get("vix"), "abs", 1, 0, ""),
        ("Volatilité or (GVZ)", y.get("gvz"), "abs", 1, 0, ""),
        ("Argent", y.get("argent"), "pct", 2, 1, " $"),
        ("Spread high yield", f.get("hy"), "pb", 2, 0, " %"),
    ]
    t = "".join(ligne_metrique(*r) for r in rows)
    ratio = f'<div class="kv" style="margin-top:12px"><span>Ratio or / argent</span><span>{nb(a["ratio_or_argent"], 1)}</span></div>'
    return f'<div><h2>Dollar et marchés</h2><table class="m">{entete_metriques("")}{t}</table>{ratio}</div>'


def bloc_flux(a):
    c = a["cot"]
    if c:
        pos = max(0, min(100, c["pct3a"]))
        cot_html = (f'<h3 style="margin-top:0">Position des fonds sur le COMEX (rapport du {date_fr(c["date"])})</h3>'
                    f'<div class="kv"><span>Position nette ({esc(c["categorie"])})</span><span>{nb(c["net"], 0)} contrats</span>'
                    f'<span>Variation sur la semaine</span><span class="{classe_effet(c["chg"], 1)}">{nb(c["chg"], 0, True)}</span>'
                    f'<span>Longs / shorts</span><span>{nb(c["long"], 0)} / {nb(c["short"], 0)}</span>'
                    f'<span>Part de l\'open interest</span><span>{nb(c["pct_oi"], 1)} %</span></div>'
                    f'<div class="pctbar"><i style="left:{SEUILS["cot_bas"]}%"></i><i style="left:{SEUILS["cot_haut"]}%"></i>'
                    f'<b style="left:{pos:.1f}%"></b></div>'
                    f'<div class="pctleg"><span>Peu exposés</span><span>percentile 3 ans : {nb(c["pct3a"], 0)}</span>'
                    f'<span>Surchargés</span></div>')
    else:
        cot_html = '<h3 style="margin-top:0">Position des fonds sur le COMEX</h3><p class="vide">COT indisponible.</p>'
    g = a["gld"]
    if g:
        gld_html = (f'<h3>ETF GLD, avoirs physiques (au {date_fr(g["date"])})</h3>'
                    f'<div class="kv"><span>Avoirs</span><span>{nb(g["t"], 1)} t</span>'
                    f'<span>Sur 5 jours</span><span class="{classe_effet(g["d5"], 1)}">{nb(g["d5"], 1, True)} t</span>'
                    f'<span>Sur 20 jours</span><span class="{classe_effet(g["d20"], 1)}">{nb(g["d20"], 1, True)} t</span></div>')
    else:
        gld_html = '<h3>ETF GLD, avoirs physiques</h3><p class="vide">Données GLD indisponibles.</p>'
    dev = ""
    if a["devises"]:
        dev = ('<h3>L\'or dans d\'autres devises</h3><table class="m"><tr><th>Or en</th><th>Prix</th><th>5 j</th><th>20 j</th></tr>'
               + "".join(f'<tr><td class="l">{esc(d["nom"])}</td><td class="v">{nb(d["prix"], 0)}</td>'
                         f'<td class="d {classe_effet(d["d5"], 1)}">{nb(d["d5"], 2, True)}</td>'
                         f'<td class="d {classe_effet(d["d20"], 1)}">{nb(d["d20"], 2, True)}</td></tr>' for d in a["devises"])
               + '</table><p class="legende" style="margin-top:8px">Si l\'or baisse en dollars mais tient en euros, '
                 'le mouvement vient du dollar, pas de l\'or.</p>')
    return f'<div><h2>Flux et positionnement</h2>{cot_html}{gld_html}{dev}</div>'


def bloc_correlations(a):
    if not a["correlations"]:
        return '<div><h2>Qui mène l\'or en ce moment</h2><p class="vide">Données insuffisantes.</p></div>'
    lignes = ""
    for c in a["correlations"]:
        v20, v60 = c["c20"], c["c60"]
        barre = ""
        if v20 is not None:
            larg = abs(v20) * 50
            gauche = 50 - larg if v20 < 0 else 50
            barre += f'<b style="left:{gauche:.1f}%;width:{larg:.1f}%"></b>'
        if v60 is not None:
            barre += f'<i style="left:{50 + v60 * 50:.1f}%" title="60 jours : {nb(v60, 2)}"></i>'
        lignes += (f'<span>{esc(c["nom"])}</span><div class="cbar">{barre}</div>'
                   f'<span class="cv">{nb(v20, 2, True)}</span>')
    m = a["moteur"]
    phrase = ""
    if m:
        sens = "à l'inverse de" if m["c20"] < 0 else "dans le même sens que"
        phrase = (f'<p class="sous" style="margin-top:14px">Moteur dominant sur 20 jours : <b>{esc(m["nom"])}</b> '
                  f'(corrélation {nb(m["c20"], 2, True)}). L\'or bouge {sens} lui. Surveille-le en priorité pendant ta session.</p>')
    return (f'<div><h2>Qui mène l\'or en ce moment</h2>'
            f'<p class="legende">Corrélation des variations quotidiennes avec l\'or. Barre : 20 jours. Trait fin : 60 jours.</p>'
            f'<div class="corr">{lignes}</div>{phrase}</div>')


def bloc_routine():
    etapes = [
        ("Lis le verdict du biais.", "Il te dit dans quel sens privilégier tes setups, pas quand entrer."),
        ("Repère le moteur dominant.", "Garde ce marché ouvert à côté de ton graphique de l'or pendant la session."),
        ("Vérifie la prochaine annonce.", "Bloc orange = zone news : aucune nouvelle position."),
        ("Regarde le positionnement des fonds.", "Surchargés : les cassures baissières vont plus loin. Peu exposés : les creux se font racheter."),
        ("Note le biais dans ton journal.", "Colonne « biais du jour » et « trade aligné oui/non » pour mesurer si le filtre t'aide."),
    ]
    li = "".join(f'<li><b>{esc(t)}</b> {esc(d)}</li>' for t, d in etapes)
    return (f'<div><h2>Routine avant session, 2 minutes</h2>'
            f'<ol class="routine">{li}</ol></div>')


def bloc_graphiques(y, f, cot, a):
    g1 = graphique_double(y.get("or"), f.get("reel10"))
    g2 = graphique_cot(cot, a["cot"])
    return (f'<section class="grille2"><div><h2>Or et taux réel 10 ans, 12 mois</h2>'
            f'<div class="cle"><span><i style="background:var(--brass)"></i>Or ($, axe gauche)</span>'
            f'<span><i style="background:var(--steel)"></i>Taux réel 10 ans (%, axe droit inversé)</span></div>{g1}'
            f'<p class="legende" style="margin-top:8px">Axe inversé : quand les deux courbes montent ensemble, '
            f'l\'or suit bien la baisse des taux réels. Quand elles s\'écartent, un autre moteur a pris la main.</p></div>'
            f'<div><h2>Position nette des fonds (COT), 3 ans</h2>'
            f'<div class="cle"><span><i style="background:var(--brass)"></i>Position nette en contrats</span>'
            f'<span><i style="background:var(--mute)"></i>Seuils de percentile</span></div>{g2}'
            f'<p class="legende" style="margin-top:8px">Au-dessus du seuil haut, les fonds sont très chargés : '
            f'une mauvaise nouvelle peut déclencher des ventes en cascade.</p></div></section>')


def bloc_calendrier(cal, a):
    now = maintenant()
    if not cal:
        cal_html = '<p class="vide">Calendrier indisponible.</p>'
    else:
        lignes = ""
        for e in cal:
            passe = e["date"] < now - timedelta(minutes=FENETRE_NEWS[1])
            lignes += (f'<tr class="{"passe" if passe else ""}"><td class="num">{date_fr(e["date"], True)}</td>'
                       f'<td><span class="imp {e["impact"]}" title="Impact {e["impact"]}"></span>{esc(e["titre"])}</td>'
                       f'<td class="num">{esc(e["reel"])}</td><td class="num">{esc(e["prevision"])}</td>'
                       f'<td class="num">{esc(e["precedent"])}</td></tr>')
        cal_html = ('<div class="defile"><table class="cal"><tr><th>Heure de Paris</th><th>Annonce USD</th><th>Réel</th><th>Prévision</th>'
                    f'<th>Précédent</th></tr>{lignes}</table></div>')
    macro = "".join(f'<tr><td class="l">{esc(m["nom"])}<br><small class="flat">{MOIS_FR[m["date"].month - 1]} {m["date"].year}'
                    f'</small></td><td class="v">{m["val"]}</td><td class="d flat">{m["prec"]}</td></tr>' for m in a["macro"])
    macro_html = (f'<table class="m"><tr><th></th><th>Dernier</th><th>Précédent</th></tr>{macro}</table>'
                  if macro else '<p class="vide">Données macro indisponibles.</p>')
    return (f'<section class="grille2 grille-cal">'
            f'<div><h2>Calendrier économique</h2><p class="legende">Point rouge : fort impact. Point orange : impact moyen. '
            f'Règle d\'or en scalping : aucune position ouverte à l\'approche d\'un point rouge.</p>{cal_html}</div>'
            f'<div><h2>Macro US</h2>{macro_html}<p class="legende" style="margin-top:10px">Aujourd\'hui : données fortes '
            f'= Fed plus dure = pression sur l\'or. Données faibles = l\'inverse.</p></div></section>')


def bloc_news(news):
    cols = ""
    for theme, _ in THEMES_NEWS:
        items = news.get(theme) or []
        if items:
            li = "".join(f'<li><a href="{esc(i["lien"])}" target="_blank" rel="noopener">{esc(i["titre"])}</a>'
                         f'<small>{esc(i["source"])}{", " + date_fr(i["date"], True) if i["date"] else ""}</small></li>'
                         for i in items)
            cols += f'<div><h3 style="margin-top:0">{esc(theme)}</h3><ul>{li}</ul></div>'
        else:
            cols += f'<div><h3 style="margin-top:0">{esc(theme)}</h3><p class="vide">Pas de titre récupéré.</p></div>'
    return f'<section class="news"><h2>Dernières actualités (48 h)</h2><div class="grille4">{cols}</div></section>'


def bloc_sources():
    items = "".join(f'<li class="{"ok" if s["ok"] else "ko"}">{esc(nom)}'
                    f'{" : " + esc(s["message"]) if s["message"] else ""}</li>' for nom, s in sorted(STATUT.items()))
    return (f'<footer><b>Sources</b><ul>{items}</ul>Données indicatives : Yahoo peut avoir 10 à 15 min de décalage, '
            f'la FRED un jour ouvré, le COT reflète les positions du mardi précédent. '
            f'Ce tableau est un outil d\'aide à la lecture, pas un conseil en investissement.</footer>')


def rendre_html(data, a, brief, watch_min=None, site=False):
    y, f = data["yahoo"], data["fred"]
    maj = maintenant()
    if site:
        refresh = '<meta http-equiv="refresh" content="600">'
        auto = ". Données actualisées toutes les 15 min environ"
    else:
        refresh = f'<meta http-equiv="refresh" content="{int(watch_min * 60) + 60}">' if watch_min else ""
        auto = f", actualisation automatique toutes les {watch_min} min" if watch_min else ""
    corps = (
        f'<header class="top"><div><h1><span></span>Terminal or</h1>'
        f'<div class="meta">Fondamentaux XAU/USD, mis à jour le {date_fr(maj, True)} '
        f'<span id="age" data-maj="{maj.isoformat()}"></span>{auto}</div></div>'
        f'<button class="copier" id="copier" type="button">Copier le brief pour Claude</button></header>'
        f'{bloc_hero(a)}'
        f'<section><p class="legende">Couleurs des variations : vert = mouvement favorable à l\'or, '
        f'rouge = défavorable, gris = neutre.</p><div class="grille3">{bloc_taux(y, f, a)}{bloc_marches(y, f, a)}'
        f'{bloc_flux(a)}</div></section>'
        f'<section class="grille2">{bloc_correlations(a)}{bloc_routine()}</section>'
        f'{bloc_graphiques(y, f, data["cot"], a)}'
        f'{bloc_calendrier(data["cal"], a)}'
        f'{bloc_news(data["news"])}'
        f'{bloc_sources()}'
    )
    return (f'<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">{refresh}'
            f'<title>Terminal or, {nb(a["or"]["prix"], 0)} $, {a["biais"]["verdict"]}</title>'
            f'<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&'
            f'family=Barlow+Semi+Condensed:wght@400;500;600;700&display=swap" rel="stylesheet">'
            f'<style>{CSS}</style></head><body><div class="wrap">{corps}</div>'
            f'<textarea id="brief" style="display:none" aria-hidden="true">{esc(brief)}</textarea>'
            f'<script>{JS}</script></body></html>')


# ---------------------------------------------------------------------------
# BRIEF POUR CLAUDE ET RÉSUMÉ TERMINAL
# ---------------------------------------------------------------------------

def construire_brief(data, a):
    y, f = data["yahoo"], data["fred"]
    o, b = a["or"], a["biais"]
    L = [f"BRIEF TERMINAL OR ({date_fr(maintenant(), True)}, heure de Paris)", ""]
    L.append(f"Or : {nb(o['prix'], 1)} $ (1 j {nb(o['d1'], 2, True)} %, 5 j {nb(o['d5'], 2, True)} %, "
             f"20 j {nb(o['d20'], 2, True)} %). Amplitude attendue du jour : ±{nb(a['range_jour'], 0)} $.")
    L.append(f"Biais fondamental : {b['verdict']} (score {nb(b['total'], 0, True)} sur {b['n']})")
    for s in b["signaux"]:
        L.append(f"  - {s['nom']} : {s['valeur']} -> {s['lecture']}")
    if a["divergence"]:
        L.append(f"Divergence : {a['divergence']}")
    L.append("")
    fed = a["fed"]
    L.append("Taux : " + ", ".join(
        f"{lib} {nb(derniere(s), 2)} % ({nb(variation(s, 5, 'pb'), 0, True)} pb/5 j)"
        for lib, s in (("réel 10 a", f.get("reel10")), ("2 a", f.get("us2")), ("10 a", y.get("us10")),
                       ("30 a", y.get("us30")), ("breakeven 10 a", f.get("be10"))) if s is not None))
    L.append(f"Fed funds effectif {nb(fed['effr'], 2)} %, écart 2 ans - fed funds {nb(fed['spread_pb'], 0, True)} pb")
    if fed["futures"]:
        L.append("Futures fed funds : " + " ; ".join(f"{r['mois']} {nb(r['taux'], 2)} % ({nb(r['ecart_pb'], 0, True)} pb)"
                                                     for r in fed["futures"][:6]))
    L.append("Marchés (5 j) : " + ", ".join(
        f"{lib} {nb(derniere(s), 2)} ({nb(variation(s, 5), 2, True)} %)"
        for lib, s in (("DXY", y.get("dxy")), ("EUR/USD", y.get("eurusd")), ("USD/JPY", y.get("usdjpy")),
                       ("Brent", y.get("brent")), ("S&P 500", y.get("spx")), ("Argent", y.get("argent"))) if s is not None))
    L.append(f"VIX {nb(derniere(y.get('vix')), 1)}, GVZ {nb(derniere(y.get('gvz')), 1)}, "
             f"ratio or/argent {nb(a['ratio_or_argent'], 1)}")
    c = a["cot"]
    if c:
        L.append(f"COT ({date_fr(c['date'])}, {c['categorie']}) : net {nb(c['net'], 0)} contrats, "
                 f"variation hebdo {nb(c['chg'], 0, True)}, percentile 3 ans {nb(c['pct3a'], 0)}")
    g = a["gld"]
    if g:
        L.append(f"GLD : {nb(g['t'], 1)} t (5 j {nb(g['d5'], 1, True)} t, 20 j {nb(g['d20'], 1, True)} t)")
    if a["correlations"]:
        L.append("Corrélations 20 j de l'or : " + ", ".join(f"{c['nom']} {nb(c['c20'], 2, True)}"
                                                            for c in a["correlations"]))
    if a["devises"]:
        L.append("Or dans d'autres devises (5 j) : " + ", ".join(f"{d['nom']} {nb(d['d5'], 2, True)} %"
                                                                 for d in a["devises"]))
    if a["macro"]:
        L.append("Macro US : " + " ; ".join(f"{m['nom']} {m['val']} (préc. {m['prec']})" for m in a["macro"]))
    now = maintenant()
    a_venir = [e for e in data["cal"] if e["date"] >= now and e["impact"] == "High"][:6]
    if a_venir:
        L.append("Annonces USD à fort impact à venir : " + " ; ".join(
            f"{date_fr(e['date'], True)} {e['titre']} (prév. {e['prevision'] or 'n.d.'}, préc. {e['precedent'] or 'n.d.'})"
            for e in a_venir))
    L.append("")
    L.append("Titres récents :")
    for theme, _ in THEMES_NEWS:
        for it in (data["news"].get(theme) or [])[:3]:
            L.append(f"  [{theme}] {it['titre']} ({it['source']})")
    L.append("")
    L.append("Question : analyse le régime actuel de l'or à partir de ces données, dis-moi quel moteur domine, "
             "ce qui pourrait inverser le biais, et les scénarios pour les prochaines annonces.")
    return "\n".join(L)


def couleur(txt, code):
    return f"\033[{code}m{txt}\033[0m" if sys.stdout.isatty() else txt


def resume_terminal(a):
    o, b = a["or"], a["biais"]
    c = {"Haussier": "32", "Baissier": "31"}.get(b["verdict"], "37")
    print()
    print(couleur("TERMINAL OR", "1;33"), f"  {date_fr(maintenant(), True)}")
    print(f"Or {nb(o['prix'], 1)} $   1 j {nb(o['d1'], 2, True)} %   5 j {nb(o['d5'], 2, True)} %")
    print("Biais fondamental :", couleur(f"{b['verdict']} ({nb(b['total'], 0, True)} sur {b['n']})", "1;" + c))
    for s in b["signaux"]:
        sym = {1: couleur("▲", "32"), -1: couleur("▼", "31")}.get(s["score"], "•")
        print(f"  {sym} {s['nom']:<34} {s['valeur']}")
    if a["annonce"]:
        e = a["annonce"]
        zone = couleur("  ZONE NEWS", "1;33") if a["zone_news"] else ""
        print(f"Prochaine annonce forte : {e['titre']}, {date_fr(e['date'], True)}{zone}")
    ko = [n for n, s in STATUT.items() if not s["ok"]]
    if ko:
        print(couleur("Sources en panne : " + ", ".join(ko), "33"))
    print()


# ---------------------------------------------------------------------------
# DONNÉES DE DÉMONSTRATION (pour tester l'affichage sans connexion)
# ---------------------------------------------------------------------------

def donnees_demo():
    import random
    random.seed(7)
    fin = pd.Timestamp(datetime.now().date())
    idx = pd.bdate_range(end=fin, periods=500)

    def marche(debut, vol, derive=0.0, n=None):
        v, out = debut, []
        for _ in range(n or len(idx)):
            v *= 1 + random.gauss(derive, vol)
            out.append(v)
        return pd.Series(out, index=idx[-(n or len(idx)):])

    def taux(debut, vol, derive=0.0):
        v, out = debut, []
        for _ in idx:
            v += random.gauss(derive, vol)
            out.append(v)
        return pd.Series(out, index=idx)

    y = {"or": marche(2650, 0.011, 0.0012), "argent": marche(31, 0.02, 0.0013), "dxy": marche(106, 0.004, -0.0001),
         "us5": taux(4.0, 0.04, 0.001), "us10": taux(4.4, 0.04, 0.0015), "us30": taux(4.7, 0.035, 0.0018),
         "brent": marche(75, 0.02, 0.0007), "wti": marche(71, 0.021, 0.0006), "spx": marche(5800, 0.009, 0.0006),
         "vix": taux(17, 0.8).clip(lower=11), "gvz": taux(19, 0.6).clip(lower=12), "usdjpy": marche(150, 0.005, 0.0001),
         "eurusd": marche(1.08, 0.004, 0.0001), "usdcny": marche(7.2, 0.002, -0.0001), "usdinr": marche(84, 0.002, 0.0002)}
    f = {"reel10": taux(1.9, 0.035, 0.001), "be10": taux(2.3, 0.02, 0.0008), "fwd5y5y": taux(2.25, 0.02, 0.0006),
         "us2": taux(3.9, 0.04, 0.0009), "effr": pd.Series([3.58] * 420 + [3.83] * 80, index=idx),
         "hy": taux(3.0, 0.03)}
    mois = pd.date_range(end=fin, periods=48, freq="MS")
    nm = len(mois)
    f["cpi"] = pd.Series([300 * (1.0025 ** i) for i in range(nm)], index=mois)
    f["cpi_core"] = pd.Series([310 * (1.0021 ** i) for i in range(nm)], index=mois)
    f["pce_core"] = pd.Series([120 * (1.0022 ** i) for i in range(nm)], index=mois)
    f["chomage"] = pd.Series([4.1 + 0.05 * math.sin(i) for i in range(nm)], index=mois)
    f["nfp"] = pd.Series([158000 + 140 * i + random.gauss(0, 60) for i in range(nm)], index=mois)
    semaines = pd.date_range(end=fin, periods=200, freq="W-TUE")
    f["claims"] = pd.Series([215000 + random.gauss(0, 9000) for _ in semaines], index=semaines)
    ns = len(semaines)
    net = taux(180000, 9000).iloc[-ns:].values
    cot = pd.DataFrame({"date": semaines, "long": [n + 60000 for n in net], "short": [60000] * ns,
                        "oi": [520000] * ns})
    cot["net"] = cot["long"] - cot["short"]
    cot.attrs["categorie"] = "managed money"
    gld = marche(880, 0.002, 0.0002)
    now = maintenant()
    cal = [{"date": now + timedelta(hours=h), "titre": t, "impact": imp, "prevision": pv, "precedent": pr, "reel": ""}
           for h, t, imp, pv, pr in ((-30, "Final GDP q/q", "Medium", "2.1%", "2.1%"),
                                     (5, "Core PCE Price Index m/m", "High", "0.3%", "0.2%"),
                                     (28, "ISM Manufacturing PMI", "High", "52.4", "51.8"),
                                     (74, "Non-Farm Employment Change", "High", "95K", "142K"),
                                     (74, "Unemployment Rate", "High", "4.1%", "4.1%"))]
    news = {t: [{"titre": f"Titre d'exemple sur le thème {t.lower()} numéro {i + 1}", "lien": "#",
                 "source": "Reuters", "date": now - timedelta(hours=i * 3)} for i in range(5)] for t, _ in THEMES_NEWS}
    ff = [{"mois": f"{MOIS_FR[(now.month - 1 + i) % 12]} {now.year + (now.month - 1 + i) // 12}",
           "taux": 3.88 + 0.09 * i, "ecart_pb": 5 + 9 * i} for i in range(7)]
    for nom in ("Yahoo Finance", "FRED (Fed de St. Louis)", "CFTC (COT)", "SPDR Gold Shares (GLD)",
                "ForexFactory (calendrier)", "Google News", "Futures fed funds"):
        noter(nom, True, "données de démonstration")
    return {"yahoo": y, "fred": f, "cot": cot, "gld": gld, "cal": cal, "news": news, "ff": ff}


# ---------------------------------------------------------------------------
# PROGRAMME PRINCIPAL
# ---------------------------------------------------------------------------

def generer(demo=False, watch_min=None, site_dir=None):
    STATUT.clear()
    data = donnees_demo() if demo else collecter()
    if site_dir is not None and "or" not in data["yahoo"]:
        # Sans le prix de l'or, on ne publie pas : la version précédente du site reste en ligne.
        print("Prix de l'or indisponible : publication annulée, la page précédente reste en ligne.")
        for nom, st in STATUT.items():
            print(f"  {nom} : {'ok' if st['ok'] else 'EN PANNE'} {st['message']}")
        sys.exit(1)
    a = analyser(data)
    brief = construire_brief(data, a)
    page = rendre_html(data, a, brief, watch_min, site=site_dir is not None)
    if site_dir is not None:
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "index.html").write_text(page, encoding="utf-8")
        (site_dir / "brief_claude.txt").write_text(brief, encoding="utf-8")
    else:
        FICHIER_HTML.write_text(page, encoding="utf-8")
        FICHIER_BRIEF.write_text(brief, encoding="utf-8")
    resume_terminal(a)
    return a


def main():
    if os.name == "nt":
        os.system("")  # active les couleurs dans le terminal Windows
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    p = argparse.ArgumentParser(description="Terminal fondamental de l'or (XAU/USD)")
    p.add_argument("--watch", type=float, metavar="MIN", help="rafraîchir toutes les MIN minutes")
    p.add_argument("--no-browser", action="store_true", help="ne pas ouvrir le navigateur")
    p.add_argument("--demo", action="store_true", help="données fictives pour tester l'affichage")
    p.add_argument("--site", metavar="DOSSIER", help="génère un site statique (index.html) dans DOSSIER")
    args = p.parse_args()

    if args.site:
        print("Collecte des données en cours (mode site)...")
        generer(args.demo, None, site_dir=Path(args.site))
        print(f"Site généré : {Path(args.site).resolve() / 'index.html'}")
        for nom, st in sorted(STATUT.items()):
            print(f"  {nom} : {'ok' if st['ok'] else 'EN PANNE'} {st['message']}")
        return

    print("Collecte des données en cours...")
    generer(args.demo, args.watch)
    print(f"Tableau de bord : {FICHIER_HTML}")
    print(f"Brief pour Claude : {FICHIER_BRIEF}")
    if not args.no_browser:
        webbrowser.open(FICHIER_HTML.as_uri())
    if args.watch:
        print(f"Actualisation toutes les {args.watch} min. Ctrl+C pour arrêter.")
        try:
            while True:
                time.sleep(args.watch * 60)
                try:
                    generer(args.demo, args.watch)
                except Exception as e:
                    print(f"Erreur pendant l'actualisation : {e}")
        except KeyboardInterrupt:
            print("Arrêt du terminal.")


if __name__ == "__main__":
    main()
