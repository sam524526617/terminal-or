#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TERMINAL OR v3 : poste de marché XAU/USD
=========================================

Ce que fait ce programme, à chaque exécution :
  1. Collecte : Yahoo Finance (prix quotidiens et intraday), FRED (taux, inflation, liquidité, stress),
     CFTC (positionnement), SPDR (ETF GLD), ForexFactory (calendrier), Réserve fédérale (communiqués,
     discours), Google News (actualité).
  2. Analyse : biais fondamental, régime de marché, décomposition du mouvement de l'or, moteurs du jour,
     probabilités Fed par réunion, niveaux de séance, structure du marché de l'or, positionnement.
  3. Explications : un moteur de texte rédige la lecture du marché et les scénarios avant chaque annonce.
     En option, un analyste IA (API Claude) rédige une analyse complète toutes les 2 heures.
  4. Publication : un cockpit HTML façon salle de marché. Cotations et graphique en direct (widgets TradingView),
     alertes sonores avant les annonces, rafraîchissement des analyses sans recharger la page,
     analyse approfondie en onglets, et un brief texte à coller dans Claude.

Utilisation :
  python terminal_or.py              -> génère le tableau une fois et l'ouvre
  python terminal_or.py --watch 10   -> rafraîchit toutes les 10 minutes
  python terminal_or.py --site site  -> génère un site statique (site/index.html) pour GitHub Pages
  python terminal_or.py --demo       -> données fictives, pour tester l'affichage sans internet

Analyste IA (optionnel) : définir la variable d'environnement ANTHROPIC_API_KEY.
Dépendances : pip install yfinance pandas numpy requests tzdata
"""

import argparse
import calendar
import concurrent.futures as cf
import csv
import hashlib
import html
import io
import json
import logging
import math
import os
import re
import sys
import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, date
from email.utils import parsedate_to_datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

import numpy as np
import pandas as pd
import requests

try:
    import yfinance as yf
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
except ImportError:
    yf = None

VERSION = "3.0"

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
    "cuivre": ("HG=F", "Cuivre"),
    "gdx": ("GDX", "Mines d'or (GDX)"),
    "dxy": ("DX-Y.NYB", "Dollar index (DXY)"),
    "us3m": ("^IRX", "Taux US 3 mois"),
    "us5": ("^FVX", "Taux US 5 ans"),
    "us10": ("^TNX", "Taux US 10 ans"),
    "us30": ("^TYX", "Taux US 30 ans"),
    "brent": ("BZ=F", "Brent"),
    "wti": ("CL=F", "WTI"),
    "spx": ("^GSPC", "S&P 500"),
    "vix": ("^VIX", "VIX"),
    "move": ("^MOVE", "Volatilité obligataire (MOVE)"),
    "gvz": ("^GVZ", "Volatilité implicite de l'or (GVZ)"),
    "usdjpy": ("JPY=X", "USD/JPY"),
    "eurusd": ("EURUSD=X", "EUR/USD"),
    "usdcny": ("CNY=X", "USD/CNY"),
    "usdinr": ("INR=X", "USD/INR"),
}

# Séries de la FRED (téléchargement public, sans clé) : clé -> (code, libellé)
FRED = {
    "reel5": ("DFII5", "Taux réel 5 ans"),
    "reel10": ("DFII10", "Taux réel 10 ans"),
    "reel30": ("DFII30", "Taux réel 30 ans"),
    "be5": ("T5YIE", "Inflation anticipée 5 ans"),
    "be10": ("T10YIE", "Inflation anticipée 10 ans"),
    "fwd5y5y": ("T5YIFR", "Inflation anticipée 5 ans dans 5 ans"),
    "mich": ("MICH", "Inflation attendue par les ménages (Michigan, 1 an)"),
    "us2": ("DGS2", "Taux US 2 ans"),
    "pente2s10s": ("T10Y2Y", "Pente 2 ans / 10 ans"),
    "pente3m10a": ("T10Y3M", "Pente 3 mois / 10 ans"),
    "prime_terme": ("THREEFYTP10", "Prime de terme 10 ans"),
    "effr": ("DFF", "Fed funds effectif"),
    "sofr": ("SOFR", "SOFR"),
    "cible_bas": ("DFEDTARL", "Cible Fed, borne basse"),
    "cible_haut": ("DFEDTARU", "Cible Fed, borne haute"),
    "bilan_fed": ("WALCL", "Bilan de la Fed"),
    "tga": ("WTREGEN", "Compte du Trésor à la Fed (TGA)"),
    "rrp": ("RRPONTSYD", "Reverse repo de la Fed"),
    "hy": ("BAMLH0A0HYM2", "Spread crédit high yield"),
    "ig": ("BAMLC0A0CM", "Spread crédit investment grade"),
    "nfci": ("NFCI", "Conditions financières (Chicago Fed)"),
    "dollar_large": ("DTWEXBGS", "Dollar large (Fed)"),
    "cpi": ("CPIAUCSL", "CPI"),
    "cpi_core": ("CPILFESL", "CPI core"),
    "pce_core": ("PCEPILFE", "PCE core"),
    "chomage": ("UNRATE", "Taux de chômage"),
    "nfp": ("PAYEMS", "Emplois non agricoles"),
    "claims": ("ICSA", "Inscriptions hebdo au chômage"),
}

# Réunions de la Fed : jour de la décision (20h, heure de Paris). Calendrier officiel 2026-2027.
FOMC = ["2026-10-28", "2026-12-09", "2027-01-27", "2027-03-17", "2027-04-28",
        "2027-06-09", "2027-07-28", "2027-09-15", "2027-10-27", "2027-12-08"]

# Séances (heure de Paris) : (nom, heure de début, heure de fin), en heures décimales
SEANCES = [("Asie", 0.0, 9.0), ("Londres", 9.0, 14.5), ("New York", 14.5, 23.0)]

# Les niveaux de séance sont calculés sur le future COMEX puis convertis en XAU/USD spot
# (l'écart future-spot est mesuré à chaque mise à jour). DECALAGE_CFD ajoute un petit ajustement
# propre à ton broker : ex. -0.5 si ton CFD cote 0,50 $ sous le spot. 0 = spot.
DECALAGE_CFD = 0.0

# Flux en direct (widgets TradingView). Si ton broker a un flux sur TradingView, tu peux le mettre
# à la place (ex. "PEPPERSTONE:XAUUSD", "ICMARKETS:XAUUSD", "FOREXCOM:XAUUSD").
TV_OR = "OANDA:XAUUSD"
TV_BANDEAU = [("OANDA:XAUUSD", "XAU/USD"), ("OANDA:XAGUSD", "XAG/USD"), ("TVC:DXY", "Dollar index"),
              ("TVC:US02Y", "US 2 ans"), ("TVC:US10Y", "US 10 ans"), ("TVC:UKOIL", "Brent"),
              ("FOREXCOM:SPXUSD", "S&P 500"), ("TVC:VIX", "VIX"), ("FX:EURUSD", "EUR/USD"), ("FX:USDJPY", "USD/JPY")]
TV_MINIS = [("TVC:DXY", "Dollar index"), ("TVC:US10Y", "US 10 ans"), ("TVC:UKOIL", "Brent"),
            ("FOREXCOM:SPXUSD", "S&P 500")]
TV_LANGUE_ACTUS = "en"   # le fil d'actualité en direct est plus fourni en anglais

# Alertes avant chaque annonce USD à fort impact (minutes avant) et vérification des nouvelles analyses
ALERTES_MIN = [15, 5, 1]
RAFRAICHISSEMENT_MIN = 3

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
    "liquidite_pct": 1.5,  # variation sur 4 semaines de la liquidité nette, en %
    "tension_pct": 1.0,  # écart entre coût de portage de l'or et SOFR, en points de %
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

# Analyste IA (optionnel, API Claude). Sans clé ANTHROPIC_API_KEY, rien n'est appelé.
IA_MODELE = "claude-sonnet-5"
IA_INTERVALLE_MIN = 120          # une nouvelle analyse au plus toutes les 2 heures
IA_HEURES = (7, 23)              # seulement entre 7 h et 23 h (Paris), du lundi au vendredi

MOIS_FR = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]
JOURS_FR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
ONCES_PAR_TONNE = 32150.7466
AJUST = 0.0  # conversion future COMEX -> prix affiché (spot + DECALAGE_CFD), calculée à chaque analyse

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
    try:
        x = round(float(x), d) + 0.0  # évite l'affichage de "−0"
    except (TypeError, ValueError):
        return "n.d."
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


def derniere(s):
    if s is None or len(s) == 0:
        return None
    s = s.dropna()
    return float(s.iloc[-1]) if len(s) else None


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


def changements(s, mode):
    """Série des variations quotidiennes dans l'unité d'affichage."""
    s = s.dropna()
    if mode == "pct":
        return s.pct_change() * 100
    if mode == "pb":
        return s.diff() * 100
    return s.diff()


def percentile(s, valeur, n=None):
    s = s.dropna()
    if n:
        s = s.tail(n)
    if len(s) < 10 or valeur is None:
        return None
    return float((s <= valeur).mean() * 100)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


# ---------------------------------------------------------------------------
# COLLECTE DES DONNÉES
# ---------------------------------------------------------------------------

def _niveau(df, champ):
    """Sous-tableau d'un champ (Close, High...) quel que soit le format de colonnes de yfinance."""
    if isinstance(df.columns, pd.MultiIndex):
        for lvl in (0, 1):
            if champ in df.columns.get_level_values(lvl):
                sub = df.xs(champ, axis=1, level=lvl)
                return sub if isinstance(sub, pd.DataFrame) else sub.to_frame()
        return None
    return df[[champ]] if champ in df.columns else None


def extraire_close(df, tickers):
    """Cours de clôture d'un yf.download (plusieurs tickers), index = dates."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    c = _niveau(df, "Close")
    if c is None:
        return pd.DataFrame()
    c = c.copy()
    if not isinstance(df.columns, pd.MultiIndex):
        c.columns = [tickers[0]]
    c.index = normaliser_index(c.index)
    return c


def extraire_ohlcv(df, ticker):
    """Open/High/Low/Close/Volume d'un seul ticker, index conservé tel quel."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    cols = {}
    for champ in ("Open", "High", "Low", "Close", "Volume"):
        sub = _niveau(df, champ)
        if sub is None:
            continue
        cols[champ.lower()] = sub[ticker] if ticker in sub.columns else sub.iloc[:, 0]
    if "close" not in cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).dropna(subset=["close"])


VERROU_YAHOO = threading.Lock()  # yfinance n'aime pas les téléchargements simultanés


def telecharger_yahoo(tickers, periode, intervalle="1d"):
    for _ in range(2):
        try:
            with VERROU_YAHOO:
                df = yf.download(tickers, period=periode, interval=intervalle, progress=False,
                                 auto_adjust=False, threads=True)
            if df is not None and len(df):
                return df
        except Exception:
            pass
        time.sleep(3)
    return None


def charger_yahoo():
    if yf is None:
        raise RuntimeError("module yfinance absent (pip install yfinance)")
    tickers = [t for t, _ in YAHOO.values()]
    close = extraire_close(telecharger_yahoo(tickers, "2y"), tickers)
    out = {}
    for cle, (t, _) in YAHOO.items():
        if t in close.columns:
            s = close[t].dropna()
            if len(s):
                out[cle] = s
    for cle in ("us3m", "us5", "us10", "us30"):  # certains flux cotent le taux x10
        if cle in out and out[cle].iloc[-1] > 20:
            out[cle] = out[cle] / 10
    if "or" not in out:
        raise RuntimeError("cours de l'or introuvable sur Yahoo")
    manquants = [YAHOO[k][1] for k in YAHOO if k not in out]
    noter("Yahoo Finance", True, ("manquants : " + ", ".join(manquants)) if manquants else "")
    return out


def charger_intraday():
    """Bougies 15 min de l'or sur 5 jours (heure de Paris) + bougies quotidiennes pour l'ATR."""
    if yf is None:
        return None
    ib = extraire_ohlcv(telecharger_yahoo("GC=F", "5d", "15m"), "GC=F")
    jb = extraire_ohlcv(telecharger_yahoo("GC=F", "6mo", "1d"), "GC=F")
    if ib.empty:
        noter("Yahoo intraday", False, "bougies 15 min indisponibles")
        return None
    idx = pd.to_datetime(ib.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    tz = tz_local()
    ib.index = idx.tz_convert(tz) if tz else idx
    if not jb.empty:
        jb.index = normaliser_index(jb.index)
    noter("Yahoo intraday", True, f"{len(ib)} bougies 15 min")
    return {"barres": ib, "jours": jb}


CODES_MOIS = "FGHJKMNQUVXZ"


def charger_zq():
    """Futures fed funds (CBOT ZQ) : taux moyen attendu pour chacun des 16 prochains mois."""
    if yf is None:
        return {}
    now = maintenant()
    contrats = []
    for i in range(0, 16):
        m = (now.month - 1 + i) % 12 + 1
        a = now.year + (now.month - 1 + i) // 12
        contrats.append((f"ZQ{CODES_MOIS[m - 1]}{str(a)[2:]}.CBT", a, m))
    close = extraire_close(telecharger_yahoo([c[0] for c in contrats], "5d"), [c[0] for c in contrats])
    out = {}
    for t, a, m in contrats:
        if t in close.columns and close[t].dropna().size:
            out[(a, m)] = 100 - float(close[t].dropna().iloc[-1])
    noter("Futures fed funds", bool(out), f"{len(out)} échéances" if out else "contrats introuvables")
    return out


def charger_courbe_or():
    """Prix des 4 prochaines échéances actives de l'or COMEX (févr., avr., juin, août, oct., déc.)."""
    if yf is None:
        return []
    now = maintenant()
    actifs = [2, 4, 6, 8, 10, 12]
    contrats, a, m = [], now.year, now.month
    while len(contrats) < 4:
        if m > 12:
            m, a = 1, a + 1
        # on saute l'échéance qui expire dans moins de 35 jours : peu liquide, prix peu fiable
        if m in actifs and (date(a, m, 27) - now.date()).days > 35:
            contrats.append((f"GC{CODES_MOIS[m - 1]}{str(a)[2:]}.CMX", a, m))
        m += 1
    close = extraire_close(telecharger_yahoo([c[0] for c in contrats], "5d"), [c[0] for c in contrats])
    out = []
    for t, a, m in contrats:
        if t in close.columns and close[t].dropna().size:
            out.append({"libelle": f"{MOIS_FR[m - 1]} {a}", "prix": float(close[t].dropna().iloc[-1]),
                        "echeance": date(a, m, 27)})
    noter("Courbe des futures or", len(out) >= 2, f"{len(out)} échéances" if out else "indisponible")
    return out


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
    noter("FRED (Fed de St. Louis)", True, ("séries manquantes : " + ", ".join(sorted(erreurs))) if erreurs else
          f"{len(out)} séries")
    return out


def _champ(cles, doit, exclure=()):
    cand = [k for k in cles if all(d in k for d in doit) and not any(e in k for e in exclure)]
    cand.sort(key=len)
    return cand[0] if cand else None


def charger_cot():
    """Positionnement sur l'or COMEX (code CFTC 088691) : fonds, swap dealers, producteurs."""
    sources = [
        ("72hh-3qpy", {"mm": "m_money", "swap": "swap", "prod": "prod_merc"}, "managed money"),
        ("6dca-aqww", {"mm": "noncomm"}, "non commerciaux"),
    ]
    exclure = ("change", "pct", "traders", "spread", "_old", "_other", "conc")
    derniere_erreur = None
    for dataset, groupes, categorie in sources:
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
                f_date = _champ(cles, ("report_date",))
                f_oi = _champ(cles, ("open_interest",), ("change", "pct", "_old", "_other"))
                if not f_date:
                    continue
                cols = {"date": pd.to_datetime([l.get(f_date) for l in lignes], errors="coerce")}
                cols["oi"] = pd.to_numeric([l.get(f_oi) for l in lignes], errors="coerce") if f_oi else np.nan
                for g, motif in groupes.items():
                    fl, fs = _champ(cles, (motif, "long"), exclure), _champ(cles, (motif, "short"), exclure)
                    if fl and fs:
                        cols[g + "_long"] = pd.to_numeric([l.get(fl) for l in lignes], errors="coerce")
                        cols[g + "_short"] = pd.to_numeric([l.get(fs) for l in lignes], errors="coerce")
                if "mm_long" not in cols:
                    continue
                df = pd.DataFrame(cols).dropna(subset=["date", "mm_long", "mm_short"])
                df = df.drop_duplicates("date").sort_values("date").tail(400).reset_index(drop=True)
                for g in groupes:
                    if g + "_long" in df:
                        df[g + "_net"] = df[g + "_long"] - df[g + "_short"]
                df["long"], df["short"], df["net"] = df["mm_long"], df["mm_short"], df["mm_net"]
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
    """Calendrier économique (ForexFactory) : annonces USD à impact moyen et fort, cette semaine et la suivante."""
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


def lire_rss(url, params=None, ttl_min=10, n=7):
    tz = tz_local()
    txt = http_get(url, params, ttl_min=ttl_min)
    racine = ET.fromstring(txt.encode("utf-8"))
    items = []
    for it in racine.findall("./channel/item")[:n]:
        titre = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and titre.endswith(" - " + source):
            titre = titre[: -len(" - " + source)]
        try:
            d = parsedate_to_datetime(it.findtext("pubDate"))
            d = d.astimezone(tz) if tz else d
        except Exception:
            d = None
        items.append({"titre": titre, "lien": (it.findtext("link") or "#").strip(), "source": source, "date": d})
    return items


def charger_news():
    out = {}
    for theme, requete in THEMES_NEWS:
        try:
            out[theme] = lire_rss("https://news.google.com/rss/search",
                                  {"q": requete + " when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})
        except Exception:
            out[theme] = []
    noter("Google News", any(out.values()))
    return out


def charger_fed_officiel():
    """Communiqués et discours officiels de la Réserve fédérale (flux RSS du Board)."""
    out = {}
    for cle, url in (("communiques", "https://www.federalreserve.gov/feeds/press_all.xml"),
                     ("discours", "https://www.federalreserve.gov/feeds/speeches.xml")):
        try:
            out[cle] = lire_rss(url, ttl_min=30, n=6)
        except Exception:
            out[cle] = []
    noter("Réserve fédérale (RSS)", any(out.values()))
    return out


def charger_spot():
    """Cours spot XAU/USD (Stooq), pour convertir les niveaux du future COMEX en prix spot."""
    txt = http_get("https://stooq.com/q/l/?s=xauusd&f=sd2t2ohlc&h&e=csv", ttl_min=5)
    lignes = [l for l in txt.strip().splitlines() if l.strip()]
    entete = [h.strip().lower() for h in lignes[0].split(",")]
    vals = [v.strip() for v in lignes[1].split(",")]
    prix = float(vals[entete.index("close")])
    if not 100 < prix < 100000:
        raise RuntimeError("cours spot incohérent")
    noter("Stooq (or spot)", True, f"XAU/USD {nb(prix, 2)}")
    return {"prix": prix}


def collecter():
    """Lance toutes les collectes en parallèle. Une source en panne n'empêche pas les autres."""
    data = {"yahoo": {}, "fred": {}, "cot": None, "gld": None, "cal": [], "news": {}, "zq": {},
            "intraday": None, "courbe": [], "fed_off": {}, "spot": None}
    taches = {
        "yahoo": (charger_yahoo, "Yahoo Finance"),
        "fred": (charger_fred, "FRED (Fed de St. Louis)"),
        "cot": (charger_cot, "CFTC (COT)"),
        "gld": (charger_gld, "SPDR Gold Shares (GLD)"),
        "cal": (charger_calendrier, "ForexFactory (calendrier)"),
        "news": (charger_news, "Google News"),
        "fed_off": (charger_fed_officiel, "Réserve fédérale (RSS)"),
        "intraday": (charger_intraday, "Yahoo intraday"),
        "zq": (charger_zq, "Futures fed funds"),
        "courbe": (charger_courbe_or, "Courbe des futures or"),
        "spot": (charger_spot, "Stooq (or spot)"),
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
                noter(nom, False, str(e)[:160])
    return data


# ---------------------------------------------------------------------------
# ANALYSE : BRIQUES DE CALCUL
# ---------------------------------------------------------------------------

def glissement_annuel(s):
    """Inflation sur un an à partir d'un indice mensuel."""
    if s is None or len(s) < 13:
        return None, None
    ga = ((s / s.shift(12) - 1) * 100).dropna()
    return float(ga.iloc[-1]), (float(ga.iloc[-2]) if len(ga) > 1 else None)


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


def effet_petrole():
    return -1 if REGIME_PETROLE == "inflation" else 1


def moteurs_liste(y, f):
    """Les moteurs suivis : (clé, série, libellé, mode de variation, effet sur l'or, unité)."""
    return [
        ("reel10", f.get("reel10"), "Taux réel 10 ans", "pb", -1, "%"),
        ("us2", f.get("us2"), "Taux US 2 ans", "pb", -1, "%"),
        ("us10", y.get("us10"), "Taux US 10 ans", "pb", -1, "%"),
        ("be10", f.get("be10"), "Inflation anticipée 10 ans", "pb", 1, "%"),
        ("dxy", y.get("dxy"), "Dollar (DXY)", "pct", -1, ""),
        ("eurusd", y.get("eurusd"), "EUR/USD", "pct", 1, ""),
        ("usdjpy", y.get("usdjpy"), "USD/JPY", "pct", -1, ""),
        ("brent", y.get("brent"), "Brent", "pct", effet_petrole(), "$"),
        ("spx", y.get("spx"), "S&P 500", "pct", 0, ""),
        ("vix", y.get("vix"), "VIX", "abs", 0, ""),
        ("move", y.get("move"), "Volatilité obligataire (MOVE)", "abs", 0, ""),
        ("gvz", y.get("gvz"), "Volatilité de l'or (GVZ)", "abs", 0, ""),
        ("argent", y.get("argent"), "Argent", "pct", 1, "$"),
        ("gdx", y.get("gdx"), "Mines d'or (GDX)", "pct", 1, "$"),
    ]


def mouvements(y, f):
    """Variations et z-scores (variation divisée par l'écart-type habituel sur 60 jours)."""
    out = []
    for cle, s, lib, mode, effet, unite in moteurs_liste(y, f):
        if s is None or len(s.dropna()) < 30:
            continue
        ch = changements(s, mode).dropna()
        sigma = float(ch.iloc[-61:-1].std()) if len(ch) > 20 else None
        d1, d5, d20 = variation(s, 1, mode), variation(s, 5, mode), variation(s, 20, mode)
        z = lambda d, n: (d / (sigma * math.sqrt(n))) if (d is not None and sigma) else None
        out.append({"cle": cle, "lib": lib, "mode": mode, "effet": effet, "unite": unite, "serie": s,
                    "dernier": derniere(s), "d1": d1, "d5": d5, "d20": d20,
                    "z1": z(d1, 1), "z5": z(d5, 5), "z20": z(d20, 20), "sigma": sigma})
    return out


def decomposition(y):
    """Régression sur 60 jours : variation de l'or expliquée par le dollar, le taux 10 ans et le pétrole."""
    series = {"dxy": (y.get("dxy"), "pct"), "us10": (y.get("us10"), "pb"), "brent": (y.get("brent"), "pct")}
    if y.get("or") is None or any(s is None for s, _ in series.values()):
        return None
    cols = {"or": changements(y["or"], "pct")}
    for k, (s, mode) in series.items():
        cols[k] = changements(s, mode)
    df = pd.concat(cols, axis=1, join="inner").dropna()
    if len(df) < 50:
        return None
    hist = df.iloc[-61:-1]
    facteurs = list(series)
    A = np.column_stack([np.ones(len(hist))] + [hist[k].values for k in facteurs])
    coef, *_ = np.linalg.lstsq(A, hist["or"].values, rcond=None)
    pred = A @ coef
    ss_tot = float(((hist["or"] - hist["or"].mean()) ** 2).sum())
    r2 = 1 - float(((hist["or"] - pred) ** 2).sum()) / ss_tot if ss_tot else 0.0

    def bilan(lignes):
        contrib = {k: float(coef[i + 1] * lignes[k].sum()) for i, k in enumerate(facteurs)}
        reel = float(lignes["or"].sum())
        attendu = sum(contrib.values())
        return {"contrib": contrib, "attendu": attendu, "reel": reel, "residu": reel - attendu}

    return {"r2": r2, "betas": {k: float(coef[i + 1]) for i, k in enumerate(facteurs)},
            "jour": bilan(df.iloc[-1:]), "semaine": bilan(df.iloc[-5:]), "date": df.index[-1]}


def regime_marche(y, corr):
    c = {x["cle"]: x["c20"] for x in corr}
    g5, g20 = variation(y.get("or"), 5), variation(y.get("or"), 20)
    spx5, vix, vix5 = variation(y.get("spx"), 5), derniere(y.get("vix")), variation(y.get("vix"), 5, "abs")
    d10_20, dxy20 = variation(y.get("us10"), 20, "pb"), variation(y.get("dxy"), 20)
    c_dxy, c_taux = c.get("dxy"), c.get("us10")
    ok = lambda *v: all(x is not None for x in v)

    if ok(vix, g5, spx5) and vix >= 25 and g5 <= -2 and spx5 <= -2:
        return {"nom": "Liquidation", "ton": "down",
                "resume": "Stress de marché : l'or est vendu avec le reste.",
                "explication": "Quand les marchés chutent brutalement, les investisseurs vendent ce qui est liquide et en gain, "
                               "souvent l'or, pour couvrir des pertes ou des appels de marge. Ce n'est pas un rejet de l'or, "
                               "c'est un besoin de liquidités.",
                "consequence": "Mouvements violents et corrélés. L'or rebondit souvent avant les actions une fois la vague "
                               "de ventes passée. Réduis la taille et élargis les stops."}
    if ok(g5, spx5, vix5) and g5 > 0.5 and spx5 < -1.5 and vix5 > 2:
        return {"nom": "Refuge", "ton": "up",
                "resume": "Demande refuge : l'or monte pendant que les actions baissent.",
                "explication": "La peur pousse les capitaux vers les actifs sans risque de contrepartie. L'or profite de "
                               "la fuite hors des actions.",
                "consequence": "Les creux sont achetés tant que le stress dure. Une détente soudaine (accord, bonne nouvelle) "
                               "peut effacer la prime de risque très vite."}
    if ok(d10_20, dxy20, g20) and d10_20 >= 15 and dxy20 <= -1 and g20 > 0:
        return {"nom": "Défiance envers les actifs US", "ton": "up",
                "resume": "Taux longs en hausse, dollar en baisse, or en hausse.",
                "explication": "Les investisseurs exigent plus de rendement sur la dette américaine tout en vendant le dollar : "
                               "c'est un doute sur la trajectoire budgétaire ou sur l'indépendance de la Fed. L'or joue alors "
                               "son rôle d'actif hors système.",
                "consequence": "Régime très favorable à l'or : la hausse des taux ne le pénalise plus. Surveille les "
                               "adjudications du Trésor et les annonces budgétaires."}
    if c_dxy is not None and c_dxy >= 0.3:
        return {"nom": "Fuite vers la qualité", "ton": "up",
                "resume": "L'or et le dollar montent ensemble.",
                "explication": "Quand l'or et le dollar progressent en même temps, la demande vient d'une recherche de sécurité "
                               "globale plutôt que d'un mouvement de change. C'est typique des phases de tension géopolitique.",
                "consequence": "Le dollar n'est plus un bon indicateur avancé : suis plutôt l'actualité géopolitique et le VIX."}
    if (c_dxy is not None and c_dxy <= -0.4) or (c_taux is not None and c_taux <= -0.35):
        return {"nom": "Taux et dollar aux commandes", "ton": "flat",
                "resume": "Régime macro classique : l'or suit les taux et le dollar.",
                "explication": "L'or réagit mécaniquement au coût d'opportunité (taux réels) et au prix du dollar. Chaque "
                               "donnée US qui modifie les anticipations de Fed se transmet directement à l'or.",
                "consequence": "Les annonces US (inflation, emploi) sont tes plus gros risques. Garde le DXY et le taux "
                               "10 ans ouverts pendant ta session : ils bougent souvent quelques instants avant l'or."}
    if c_dxy is not None and c_taux is not None and abs(c_dxy) < 0.25 and abs(c_taux) < 0.25:
        return {"nom": "Flux et banques centrales", "ton": "flat",
                "resume": "L'or est découplé de la macro de court terme.",
                "explication": "Les corrélations habituelles sont faibles : ce sont les flux (banques centrales, demande "
                               "physique asiatique, ETF) ou la géopolitique qui dictent le prix.",
                "consequence": "Les signaux taux et dollar sont moins fiables. Privilégie la structure de marché et la "
                               "lecture de l'actualité."}
    return {"nom": "Mixte", "ton": "flat", "resume": "Aucun moteur ne domine nettement.",
            "explication": "Les relations entre l'or, les taux et le dollar sont moyennes : plusieurs forces se compensent.",
            "consequence": "Conviction réduite : laisse ta technique décider et attends qu'un moteur prenne la main."}


def seance(intra):
    """Niveaux de la séance : veille, pivots, VWAP, ATR, séances Asie / Londres / New York."""
    if not intra or intra.get("barres") is None or intra["barres"].empty:
        return None
    b, jb = intra["barres"], intra.get("jours")
    jours = sorted(set(b.index.date))
    auj = jours[-1]
    veille = jours[-2] if len(jours) > 1 else None
    bj = b[b.index.date == auj]
    prix = float(b["close"].iloc[-1])
    ouv = float(bj["open"].iloc[0]) if "open" in bj else float(bj["close"].iloc[0])
    haut = float(bj["high"].max()) if "high" in bj else float(bj["close"].max())
    bas = float(bj["low"].min()) if "low" in bj else float(bj["close"].min())
    res = {"prix": prix, "date": auj, "maj": b.index[-1], "ouverture": ouv, "haut": haut, "bas": bas,
           "var_jour": (prix / ouv - 1) * 100 if ouv else None}

    heures = bj.index.hour + bj.index.minute / 60
    res["seances"] = []
    for nom, h0, h1 in SEANCES:
        sb = bj[(heures >= h0) & (heures < h1)]
        if len(sb):
            res["seances"].append({"nom": nom, "haut": float(sb["high"].max()), "bas": float(sb["low"].min()),
                                   "var": float(sb["close"].iloc[-1] - sb["open"].iloc[0]),
                                   "en_cours": sb.index[-1] == bj.index[-1]})
        else:
            res["seances"].append({"nom": nom, "haut": None, "bas": None, "var": None, "en_cours": False})

    niveaux = []
    if veille is not None:
        bv = b[b.index.date == veille]
        pdh, pdl, pdc = float(bv["high"].max()), float(bv["low"].min()), float(bv["close"].iloc[-1])
        p = (pdh + pdl + pdc) / 3
        res.update({"pdh": pdh, "pdl": pdl, "pdc": pdc, "pivot": p})
        niveaux += [("Plus haut de la veille", pdh), ("Plus bas de la veille", pdl), ("Clôture de la veille", pdc),
                    ("Pivot", p), ("R1", 2 * p - pdl), ("S1", 2 * p - pdh), ("R2", p + (pdh - pdl)),
                    ("S2", p - (pdh - pdl))]

    if "volume" in bj and float(bj["volume"].sum()) > 0:
        tp = (bj["high"] + bj["low"] + bj["close"]) / 3
        vwap = (tp * bj["volume"]).cumsum() / bj["volume"].cumsum().replace(0, np.nan)
        res["vwap"] = float(vwap.dropna().iloc[-1]) if vwap.notna().any() else None
        res["vwap_serie"] = vwap
        if res["vwap"]:
            niveaux.append(("VWAP du jour", res["vwap"]))
    else:
        res["vwap"] = None

    res["atr"] = None
    if jb is not None and len(jb) > 16 and {"high", "low", "close"} <= set(jb.columns):
        jc = jb[jb.index.date < auj] if len(jb[jb.index.date < auj]) > 15 else jb
        tr = pd.concat([jc["high"] - jc["low"], (jc["high"] - jc["close"].shift()).abs(),
                        (jc["low"] - jc["close"].shift()).abs()], axis=1).max(axis=1)
        res["atr"] = float(tr.tail(14).mean())
        lundi = auj - timedelta(days=auj.weekday())
        sem = jb[(jb.index.date >= lundi) & (jb.index.date < auj)]
        res["haut_sem"] = max([haut] + list(sem["high"].values))
        res["bas_sem"] = min([bas] + list(sem["low"].values))
        mois = jb[(jb.index.month == auj.month) & (jb.index.year == auj.year)]
        if len(mois) and "open" in mois:
            niveaux.append(("Ouverture du mois", float(mois["open"].iloc[0])))
        niveaux += [("Plus haut de la semaine", res["haut_sem"]), ("Plus bas de la semaine", res["bas_sem"])]
    res["pct_atr"] = ((haut - bas) / res["atr"] * 100) if res["atr"] else None

    pas = 50 if prix > 2000 else 10
    niveaux += [("Chiffre rond", math.floor(prix / pas) * pas), ("Chiffre rond", math.ceil(prix / pas) * pas)]
    vus, propres = set(), []
    for lib, v in sorted(niveaux, key=lambda x: -x[1]):
        cle = round(v, 1)
        if cle in vus:
            continue
        vus.add(cle)
        propres.append({"lib": lib, "niveau": v, "cfd": v + AJUST, "dist": v - prix,
                        "dist_atr": ((v - prix) / res["atr"]) if res["atr"] else None})
    res["niveaux"] = propres
    res["barres_graph"] = b[b.index.date >= (veille or auj)]
    return res


def fedwatch(zq, effr, cible_bas, cible_haut):
    """Probabilités par réunion à partir des futures fed funds (méthode proche de celle du CME)."""
    if not zq or effr is None:
        return None
    auj = maintenant().date()
    reunions = [date.fromisoformat(d) for d in FOMC if date.fromisoformat(d) >= auj][:6]
    r_pre, lignes = effr, []
    for i, d in enumerate(reunions):
        a, m = d.year, d.month
        if (a, m) not in zq:
            break
        n_jours = calendar.monthrange(a, m)[1]
        suiv = (a + (m == 12), m % 12 + 1)
        reunion_suiv = any((r.year, r.month) == suiv for r in reunions)
        if (n_jours - d.day) < 10 and suiv in zq and not reunion_suiv:
            r_post = zq[suiv]  # réunion en fin de mois : le contrat du mois suivant est plus propre
        else:
            r_post = (zq[(a, m)] - r_pre * d.day / n_jours) / ((n_jours - d.day) / n_jours)
        delta = (r_post - r_pre) * 100
        lignes.append({"date": d, "taux": r_post, "delta_pb": delta, "cumul_pb": (r_post - effr) * 100,
                       "p_hausse": clamp(delta / 25, 0, 1), "p_baisse": clamp(-delta / 25, 0, 1)})
        lignes[-1]["p_statu"] = max(0.0, 1 - lignes[-1]["p_hausse"] - lignes[-1]["p_baisse"])
        r_pre = r_post
    if not lignes:
        return None
    fin_annee = [l for l in lignes if l["date"].year == auj.year]
    return {"reunions": lignes, "effr": effr, "cible": (cible_bas, cible_haut),
            "cumul_fin_annee": fin_annee[-1]["cumul_pb"] if fin_annee else None,
            "cumul_12m": lignes[-1]["cumul_pb"]}


def liquidite(f):
    """Liquidité nette = bilan de la Fed - compte du Trésor - reverse repo (en milliers de milliards $)."""
    b, t, r = f.get("bilan_fed"), f.get("tga"), f.get("rrp")
    if b is None or t is None or len(b) < 10:
        return None

    def en_millions(s, seuil):  # FRED publie certaines séries en milliards, d'autres en millions
        return s * 1000 if float(s.dropna().iloc[-1]) <= seuil else s

    b, t = en_millions(b, 100000), en_millions(t, 5000)
    cols = {"b": b.resample("W-WED").last(), "t": t.resample("W-WED").last()}
    if r is not None and len(r):
        cols["r"] = en_millions(r, 3000).resample("W-WED").last()
    h = pd.concat(cols, axis=1).ffill().dropna(subset=["b", "t"])
    if "r" not in h:
        h["r"] = 0.0
    h["r"] = h["r"].fillna(0.0)
    net = (h["b"] - h["t"] - h["r"]) / 1e6
    if len(net) < 14:
        return None
    return {"serie": net, "niveau": float(net.iloc[-1]), "d4": variation(net, 4), "d13": variation(net, 13),
            "bilan": float(h["b"].iloc[-1]) / 1e6, "tga": float(h["t"].iloc[-1]) / 1e6,
            "rrp": float(h["r"].iloc[-1]) / 1e6, "date": net.index[-1]}


def structure_or(courbe, sofr):
    """Coût de portage entre échéances comparé au SOFR : un portage anormalement bas signale une tension physique."""
    if not courbe or len(courbe) < 2 or sofr is None:
        return None
    f1, lignes = courbe[0], []
    for c in courbe[1:]:
        jours = (c["echeance"] - f1["echeance"]).days
        if jours <= 0 or f1["prix"] <= 0:
            continue
        portage = ((c["prix"] / f1["prix"]) ** (365 / jours) - 1) * 100
        lignes.append({"libelle": f"{f1['libelle']} vers {c['libelle']}", "ecart": c["prix"] - f1["prix"],
                       "portage": portage, "vs_sofr": portage - sofr})
    if not lignes:
        return None
    ref = lignes[0]
    return {"lignes": lignes, "courbe": courbe, "sofr": sofr, "location_implicite": sofr - ref["portage"],
            "tension": ref["vs_sofr"] <= -SEUILS["tension_pct"], "ref": ref}


def ratios(y):
    or_s = y.get("or")
    if or_s is None:
        return []
    defs = [("Or / argent", y.get("argent"), "div", 1, "Plus il est haut, plus l'argent est bon marché face à l'or ; "
             "une baisse rapide accompagne souvent les phases haussières saines des métaux."),
            ("Or / cuivre", y.get("cuivre"), "div", 1, "L'or (défensif) contre le cuivre (cyclique) : sa hausse traduit "
             "une inquiétude sur la croissance mondiale."),
            ("Or / Brent", y.get("brent"), "div", 1, "Combien de barils achète une once : utile pour voir si l'or suit "
             "l'inflation énergétique ou s'en détache."),
            ("Or / S&P 500", y.get("spx"), "div", 1, "Performance de l'or face aux actions : sa hausse signale une "
             "préférence pour les actifs défensifs."),
            ("Mines / or (GDX)", y.get("gdx"), "mines", 1000, "Les actions minières anticipent souvent le métal : si elles "
             "sous-performent pendant une hausse de l'or, la hausse est moins solide.")]
    out = []
    for lib, s, op, mult, expl in defs:
        if s is None:
            continue
        df = pd.concat([or_s, s], axis=1, join="inner").dropna()
        if len(df) < 30:
            continue
        r = (df.iloc[:, 1] / df.iloc[:, 0] * mult) if op == "mines" else (df.iloc[:, 0] / df.iloc[:, 1])
        out.append({"lib": lib, "val": float(r.iloc[-1]), "d20": variation(r, 20),
                    "pct1a": percentile(r, float(r.iloc[-1]), 252), "expl": expl, "serie": r})
    return out


def volatilite(y):
    or_s, gvz = y.get("or"), derniere(y.get("gvz"))
    if or_s is None or len(or_s) < 25:
        return None
    rv = float(or_s.pct_change().tail(20).std() * math.sqrt(252) * 100)
    return {"realisee": rv, "implicite": gvz, "prime": (gvz - rv) if gvz is not None else None}


def cot_etendu(cot, or_s):
    if cot is None or len(cot) < 20:
        return None
    h, der, prec = cot.tail(156), cot.iloc[-1], cot.iloc[-2]
    res = {"date": der["date"], "net": float(der["net"]), "long": float(der["long"]), "short": float(der["short"]),
           "chg": float(der["net"] - prec["net"]), "pct3a": percentile(h["net"], float(der["net"])),
           "pct_oi": float(der["net"] / der["oi"] * 100) if der["oi"] == der["oi"] and der["oi"] else None,
           "p15": float(h["net"].quantile(SEUILS["cot_bas"] / 100)),
           "p85": float(h["net"].quantile(SEUILS["cot_haut"] / 100)),
           "categorie": cot.attrs.get("categorie", ""),
           "pct_long": percentile(h["long"], float(der["long"])),
           "pct_short": percentile(h["short"], float(der["short"])), "groupes": []}
    for g, lib in (("swap", "Banques (swap dealers)"), ("prod", "Producteurs et négociants")):
        if g + "_net" in cot:
            res["groupes"].append({"lib": lib, "net": float(der[g + "_net"]),
                                   "chg": float(der[g + "_net"] - prec[g + "_net"]),
                                   "pct3a": percentile(h[g + "_net"], float(der[g + "_net"]))})
    d_long, d_short = float(der["long"] - prec["long"]), float(der["short"] - prec["short"])
    dp = None
    if or_s is not None:
        try:
            p1, p0 = or_s.asof(pd.Timestamp(der["date"])), or_s.asof(pd.Timestamp(prec["date"]))
            dp = float(p1 - p0) if pd.notna(p1) and pd.notna(p0) else None
        except Exception:
            dp = None
    res.update({"d_long": d_long, "d_short": d_short, "d_prix": dp})
    if abs(d_long) >= abs(d_short):
        flux = "achats" if d_long > 0 else "liquidation"
    else:
        flux = "ventes" if d_short > 0 else "rachats"
    hausse = dp is None or dp >= 0
    textes = {
        ("achats", True): ("Nouveaux achats des fonds", "Les fonds ouvrent des positions acheteuses pendant la hausse : "
                           "mouvement porté par de la conviction."),
        ("achats", False): ("Achats sur repli", "Les fonds achètent la baisse : ils considèrent le recul comme une "
                            "opportunité, signe de soutien sous les prix."),
        ("liquidation", True): ("Allègement malgré la hausse", "Les fonds réduisent leurs achats alors que le prix monte : "
                                "la hausse est portée par d'autres acteurs, les fonds restent méfiants."),
        ("liquidation", False): ("Liquidation de positions acheteuses", "Les fonds soldent leurs achats : la baisse vient "
                                 "de prises de bénéfices ou de capitulation."),
        ("ventes", True): ("Vendeurs contre la hausse", "Les fonds ouvrent des ventes à découvert malgré la hausse : "
                           "ils parient sur un retournement."),
        ("ventes", False): ("Nouvelles ventes à découvert", "Les fonds ouvrent des ventes à découvert : pari actif sur "
                            "la baisse."),
        ("rachats", True): ("Rachats de ventes à découvert", "La hausse vient surtout de vendeurs qui se couvrent : "
                            "mécanique, souvent moins durable."),
        ("rachats", False): ("Rachats de ventes malgré la baisse", "Les vendeurs prennent leurs bénéfices : la pression "
                             "vendeuse s'épuise."),
    }
    res["lecture"] = textes[(flux, hausse)]
    return res


MOTS_BAROMETRE = {
    "hawk": ["hike", "hikes", "hawkish", "tighten", "tightening", "higher for longer", "raise rates", "rate increase",
             "sticky inflation", "hot inflation"],
    "dove": ["rate cut", "rate cuts", "dovish", "easing", "pause", "slowdown", "cooling inflation", "cut rates"],
    "esc": ["attack", "strike", "strikes", "missile", "escalat", "blockade", "sanction", "invasion", "threat", "clash"],
    "desc": ["ceasefire", "truce", "peace", "agreement", "de-escalat", "reopen", "talks", "deal"],
}


def barometre(news):
    titres, vus = [], set()
    for items in (news or {}).values():
        for it in items:
            t = it["titre"].strip()
            if t.lower() not in vus:
                vus.add(t.lower())
                titres.append(t)
    if not titres:
        return None
    res = {"n": len(titres)}
    for cat, mots in MOTS_BAROMETRE.items():
        touches = [t for t in titres if any(re.search(r"\b" + re.escape(m), t.lower()) for m in mots)]
        res[cat], res[cat + "_ex"] = len(touches), touches[:2]
    return res


def _nombre(txt):
    m = re.match(r"^\s*[<>]?\s*(-?\d+(?:[.,]\d+)?)\s*([KMBT%]?)", str(txt or ""))
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    return v * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(m.group(2), 1)


def surprise_macro(cal):
    """Indice de surprise maison : les données US publiées sortent-elles au-dessus ou en dessous des prévisions ?"""
    lignes = []
    for e in cal or []:
        a, p = _nombre(e.get("reel")), _nombre(e.get("prevision"))
        if a is None or p is None:
            continue
        sens = 0 if a == p else (1 if a > p else -1)
        if re.search(r"unemployment|claims|jobless", e["titre"], re.I):
            sens = -sens  # plus de chômeurs = économie plus faible
        lignes.append({"titre": e["titre"], "reel": e["reel"], "prevision": e["prevision"], "sens": sens,
                       "date": e["date"]})
    if len(lignes) < 3:
        return None
    score = sum(l["sens"] for l in lignes) / len(lignes)
    return {"score": score, "lignes": lignes[-8:], "n": len(lignes)}


def prochaine_annonce(cal):
    now = maintenant()
    avant, apres = FENETRE_NEWS
    for e in [e for e in cal if e["impact"] == "High"]:
        minutes = (e["date"] - now).total_seconds() / 60
        if minutes >= -apres:
            return e, (-apres <= minutes <= avant), minutes
    return None, False, None


# ---------------------------------------------------------------------------
# BIAIS FONDAMENTAL (règles explicites, chacune vote -1, 0 ou +1)
# ---------------------------------------------------------------------------

def signal(nom, score, valeur, lecture):
    return {"nom": nom, "score": score, "valeur": valeur, "lecture": lecture}


def biais_fondamental(y, f, cot, gld, liq, struct):
    S, sig = SEUILS, []

    d = variation(f.get("reel10"), 5, "pb")
    if d is not None:
        sc = -1 if d >= S["reel_pb"] else (1 if d <= -S["reel_pb"] else 0)
        sig.append(signal("Taux réels 10 ans", sc, f"{nb(d, 0, True)} pb sur 5 j",
                          {-1: "Taux réels en hausse : détenir de l'or coûte plus cher",
                           1: "Taux réels en baisse : l'or redevient attractif", 0: "Taux réels stables"}[sc]))

    d = variation(y.get("dxy"), 5, "pct")
    if d is not None:
        sc = -1 if d >= S["dxy_pct"] else (1 if d <= -S["dxy_pct"] else 0)
        sig.append(signal("Dollar (DXY)", sc, f"{nb(d, 2, True)} % sur 5 j",
                          {-1: "Dollar en hausse : pression directe sur l'or",
                           1: "Dollar en baisse : soutien direct à l'or", 0: "Dollar sans direction nette"}[sc]))

    d = variation(f.get("us2"), 5, "pb")
    if d is not None:
        sc = -1 if d >= S["us2_pb"] else (1 if d <= -S["us2_pb"] else 0)
        sig.append(signal("Anticipations Fed (taux 2 ans)", sc, f"{nb(d, 0, True)} pb sur 5 j",
                          {-1: "Le marché price une Fed plus dure", 1: "Le marché price une Fed plus accommodante",
                           0: "Anticipations Fed stables"}[sc]))

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
                    -1: "Pétrole en baisse : la prime de risque se dégonfle", 0: "Pétrole sans mouvement marqué"}[sc]
        sig.append(signal("Pétrole (Brent)", sc, f"{nb(d, 1, True)} % sur 5 j", lect))

    if cot is not None and len(cot) > 20:
        pct = percentile(cot["net"].tail(156), float(cot["net"].iloc[-1]))
        sc = -1 if pct >= S["cot_haut"] else (1 if pct <= S["cot_bas"] else 0)
        sig.append(signal("Positionnement des fonds (COT)", sc, f"percentile 3 ans : {nb(pct, 0)}",
                          {-1: "Fonds très longs : risque de liquidation si ça casse",
                           1: "Fonds peu exposés : de la marge pour racheter",
                           0: "Positionnement des fonds dans la moyenne"}[sc]))

    if gld is not None and len(gld) > 6:
        d = variation(gld, 5, "abs")
        sc = 1 if d >= S["gld_t"] else (-1 if d <= -S["gld_t"] else 0)
        sig.append(signal("Flux ETF (GLD)", sc, f"{nb(d, 1, True)} t sur 5 j",
                          {1: "Entrées dans les ETF : les investisseurs achètent",
                           -1: "Sorties des ETF : les investisseurs allègent", 0: "Flux ETF calmes"}[sc]))

    if liq and liq.get("d4") is not None:
        d = liq["d4"]
        sc = 1 if d >= S["liquidite_pct"] else (-1 if d <= -S["liquidite_pct"] else 0)
        sig.append(signal("Liquidité nette (Fed et Trésor)", sc, f"{nb(d, 1, True)} % sur 4 sem.",
                          {1: "Plus de liquidité dans le système : carburant pour les actifs, or compris",
                           -1: "Liquidité qui se retire : pression sur les actifs", 0: "Liquidité stable"}[sc]))

    if struct:
        sc = 1 if struct["tension"] else 0
        sig.append(signal("Tension sur l'or physique", sc,
                          f"portage {nb(struct['ref']['portage'], 2)} % contre SOFR {nb(struct['sofr'], 2)} %",
                          "Portage très inférieur au SOFR : l'or physique est rare et cher à emprunter"
                          if sc else "Courbe normale : pas de tension sur le physique"))

    total = sum(s["score"] for s in sig)
    verdict = "Haussier" if total >= 2 else ("Baissier" if total <= -2 else "Neutre")
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


# ---------------------------------------------------------------------------
# MOTEUR D'EXPLICATIONS (texte rédigé automatiquement à partir des données)
# ---------------------------------------------------------------------------

MECANISMES = {
    "reel10": "Le taux réel est le rendement d'une obligation après inflation : c'est ce que l'on sacrifie en détenant "
              "de l'or, qui ne rapporte rien.",
    "us2": "Le taux 2 ans résume ce que le marché attend de la Fed : il monte quand le marché anticipe des taux "
           "directeurs plus hauts.",
    "us10": "Le taux 10 ans reflète croissance, inflation et risque budgétaire à long terme.",
    "be10": "L'inflation anticipée mesure la protection que recherchent les investisseurs : à taux nominal égal, plus "
            "elle monte, plus l'or est attractif.",
    "dxy": "L'or est coté en dollars : un dollar plus fort renchérit l'or pour le reste du monde et freine la demande.",
    "eurusd": "Miroir du dollar : un euro qui monte, c'est un dollar qui baisse.",
    "usdjpy": "Le yen est l'autre grande valeur refuge ; un yen qui se renforce accompagne souvent un or recherché.",
    "brent": ("Le pétrole alimente l'inflation : en choc pétrolier, sa hausse pousse la Fed à rester dure."
              if REGIME_PETROLE == "inflation" else
              "Le pétrole porte la prime de risque géopolitique : sa hausse attire les achats refuge."),
    "spx": "Les actions mesurent l'appétit pour le risque : une chute peut créer une demande refuge, ou au contraire des "
           "ventes forcées d'or pour couvrir des pertes.",
    "vix": "La peur sur les actions : un VIX qui s'envole signale du stress, souvent favorable à l'or sauf en cas de "
           "liquidations générales.",
    "move": "La volatilité des obligations US : quand elle grimpe, les taux deviennent erratiques et l'or avec.",
    "gvz": "La volatilité attendue de l'or : sa hausse annonce des mouvements plus amples, donc des stops à élargir.",
    "argent": "L'argent suit l'or avec plus d'amplitude ; une divergence entre les deux signale un mouvement moins solide.",
    "gdx": "Les mines d'or anticipent souvent le métal : leur force confirme une hausse, leur faiblesse la fragilise.",
}


def intensite(z):
    if z is None:
        return ""
    a = abs(z)
    return "exceptionnel" if a >= 3 else ("fort" if a >= 2 else ("marqué" if a >= 1 else "normal"))


def fmt_var(m, d):
    if d is None:
        return "n.d."
    if m["mode"] == "pb":
        return f"{nb(d, 0, True)} pb"
    if m["mode"] == "pct":
        return f"{nb(d, 2, True)} %"
    return f"{nb(d, 1, True)} pt"


def moteurs_du_jour(a):
    mv = [m for m in a["mouvements"] if m["z1"] is not None]
    mv.sort(key=lambda m: -abs(m["z1"]))
    choisis = [m for m in mv if abs(m["z1"]) >= 0.8][:5] or mv[:3]
    out = []
    for m in choisis:
        sens = m["d1"] * m["effet"] if m["d1"] is not None else 0
        effet = ("favorable à l'or" if sens > 0 else "défavorable à l'or") if m["effet"] and sens else \
            "effet indirect sur l'or"
        out.append({"lib": m["lib"], "var": fmt_var(m, m["d1"]), "z": m["z1"], "intensite": intensite(m["z1"]),
                    "effet": effet, "ton": ("up" if sens > 0 else "down") if m["effet"] and sens else "flat",
                    "mecanisme": MECANISMES.get(m["cle"], "")})
    return out


KB_ANNONCES = [
    (r"core pce|pce price", "Inflation PCE", "inflation",
     "L'indicateur d'inflation préféré de la Fed. C'est lui qu'elle vise à 2 %."),
    (r"average hourly earnings", "Salaires horaires", "inflation",
     "La hausse des salaires entretient l'inflation des services, la plus difficile à faire baisser."),
    (r"\bcpi\b", "Inflation CPI", "inflation",
     "L'inflation des prix à la consommation, publiée avant le PCE : le marché y réagit le plus violemment."),
    (r"\bppi\b", "Prix à la production", "inflation",
     "Les prix payés par les entreprises, qui se répercutent ensuite sur les consommateurs."),
    (r"inflation expectations", "Anticipations d'inflation des ménages", "inflation",
     "Si les ménages anticipent plus d'inflation, la Fed craint qu'elle s'installe."),
    (r"non-farm|nfp", "Créations d'emplois (NFP)", "activite",
     "Le rapport le plus suivi du mois : il dit si l'économie crée assez d'emplois pour tenir la consommation."),
    (r"unemployment claims|jobless", "Inscriptions hebdo au chômage", "chomage",
     "Le thermomètre hebdomadaire du marché du travail : une hausse durable signale un ralentissement."),
    (r"unemployment rate", "Taux de chômage", "chomage",
     "Le second mandat de la Fed : un chômage qui monte la pousse à assouplir."),
    (r"ism manufacturing", "ISM manufacturier", "activite",
     "L'enquête auprès des directeurs d'achat de l'industrie ; sa composante prix renseigne aussi sur l'inflation."),
    (r"ism services|non-manufacturing", "ISM services", "activite",
     "Les services pèsent plus de 70 % de l'économie US ; la composante prix est surveillée de près."),
    (r"retail sales", "Ventes au détail", "activite", "La santé du consommateur américain, moteur de la croissance."),
    (r"\bgdp\b", "PIB", "activite", "La croissance de l'économie sur le trimestre."),
    (r"jolts", "Offres d'emploi (JOLTS)", "activite", "La demande de travail des entreprises."),
    (r"\badp\b", "Emplois privés (ADP)", "activite", "Un avant-goût du NFP, publié deux jours avant."),
    (r"press conference|fed chair|powell|warsh", "Conférence du président de la Fed", "fed",
     "Le ton du président fait souvent plus bouger les marchés que la décision elle-même."),
    (r"federal funds rate|fomc statement|rate decision", "Décision de la Fed", "fed",
     "La décision de taux et le communiqué. Aux réunions de mars, juin, septembre et décembre s'ajoutent les projections "
     "des membres (dot plot)."),
    (r"minutes", "Compte rendu de la Fed", "fed", "Le détail des débats de la dernière réunion."),
    (r"consumer sentiment|consumer confidence", "Confiance des consommateurs", "activite",
     "Le moral des ménages, indicateur avancé de leurs dépenses."),
    (r"durable goods", "Commandes de biens durables", "activite", "L'investissement des entreprises."),
    (r"\bpmi\b", "PMI (S&P Global)", "activite", "Enquête d'activité auprès des entreprises."),
    (r"empire state|philly fed|philadelphia", "Enquête régionale", "activite", "Un indicateur avancé de l'industrie."),
    (r"auction|bond", "Adjudication du Trésor", "adjudication",
     "La demande des investisseurs pour la dette américaine ; une adjudication ratée peut secouer les taux."),
]

REACTIONS = {
    "inflation": ("Au-dessus de la prévision : la Fed doit rester dure, taux réels et dollar montent, l'or baisse.",
                  "En dessous : moins de pression sur la Fed, taux et dollar reculent, l'or monte."),
    "activite": ("Au-dessus de la prévision : économie solide, peu de raisons pour la Fed d'assouplir, "
                 "taux et dollar montent, pression sur l'or.",
                 "En dessous : le marché anticipe une Fed plus souple, taux et dollar baissent, l'or monte."),
    "chomage": ("Au-dessus de la prévision : l'économie ralentit, le marché anticipe une Fed plus souple, l'or monte.",
                "En dessous : marché du travail solide, Fed dure, l'or baisse."),
    "fed": ("Ton plus dur que prévu (hausses supplémentaires, inflation jugée persistante) : l'or baisse.",
            "Ton plus souple (pause, inquiétude sur la croissance) : l'or monte."),
    "adjudication": ("Demande faible (taux qui montent à l'adjudication) : effet double, la hausse des taux pèse sur "
                     "l'or mais le doute sur les finances américaines le soutient.",
                     "Demande solide : taux en baisse, léger soutien pour l'or."),
}


def fiche_annonce(e, a):
    titre = e["titre"]
    kb = next((k for k in KB_ANNONCES if re.search(k[0], titre, re.I)), None)
    nom, typ, pourquoi = (kb[1], kb[2], kb[3]) if kb else (titre, None, "")
    haut, bas = REACTIONS.get(typ, ("", ""))
    nuances = []
    fw = a.get("fedwatch")
    if fw and fw["reunions"] and typ:
        r0 = fw["reunions"][0]
        dur = {"inflation": ("un chiffre au-dessus de la prévision", "une surprise en dessous"),
               "activite": ("un chiffre au-dessus de la prévision", "une surprise en dessous"),
               "chomage": ("un chiffre en dessous de la prévision", "une surprise au-dessus"),
               "fed": ("un ton dur", "un ton souple"),
               "adjudication": ("des taux en hausse", "une adjudication solide")}.get(typ)
        if dur and r0["p_hausse"] >= 0.5:
            nuances.append(f"Le marché price déjà {nb(r0['p_hausse'] * 100, 0)} % de chances de hausse le "
                           f"{date_fr(r0['date'])} : {dur[0]} est en partie attendu, {dur[1]} aurait plus "
                           f"d'impact sur l'or (débouclage des paris sur une Fed dure).")
        elif dur and r0["p_baisse"] >= 0.5:
            nuances.append(f"Le marché price déjà {nb(r0['p_baisse'] * 100, 0)} % de chances de baisse le "
                           f"{date_fr(r0['date'])} : une donnée faible est en partie attendue, une surprise dans "
                           f"l'autre sens aurait plus d'impact sur l'or.")
    reg = a.get("regime") or {}
    if reg.get("nom") == "Flux et banques centrales":
        nuances.append("Dans le régime actuel, l'or réagit moins aux données US : la réaction risque d'être courte.")
    elif reg.get("nom") == "Taux et dollar aux commandes":
        nuances.append("Dans le régime actuel, l'or suit de près taux et dollar : attends-toi à une réaction franche.")
    return {"titre": titre, "nom": nom, "type": typ, "pourquoi": pourquoi, "haut": haut, "bas": bas,
            "nuances": nuances, "date": e["date"], "prevision": e["prevision"], "precedent": e["precedent"],
            "amplitude": a.get("range_jour")}


def point_30s(a, y):
    o, b, reg = a["or"], a["biais"], a["regime"]
    phr = []
    if o["prix"] is not None:
        sens = "en hausse" if (o["d5"] or 0) > 0 else "en baisse"
        phr.append(f"L'or cote {nb(o['prix'], 0)} $, {sens} de {nb(abs(o['d5'] or 0), 1)} % sur 5 jours. "
                   f"Le biais fondamental est {b['verdict'].lower()} ({nb(b['total'], 0, True) if b['total'] else '0'} "
                   f"sur {b['n']} signaux).")
    phr.append(f"Régime de marché : {reg['nom'].lower()}. {reg['resume']}")
    md = a["moteurs_jour"]
    if md:
        m = md[0]
        phr.append(f"Mouvement le plus notable du jour : {m['lib']}, {m['var']} (mouvement {m['intensite']}), "
                   f"{m['effet']}.")
    dc = a.get("decomp")
    if dc:
        j = dc["jour"]
        ecart = j["residu"]
        if abs(ecart) >= 0.3:
            qual = "surperforme" if ecart > 0 else "sous-performe"
            phr.append(f"Le dollar, les taux et le pétrole expliquaient {nb(j['attendu'], 2, True)} % pour l'or "
                       f"sur la dernière séance ; il a fait {nb(j['reel'], 2, True)} %. Il {qual} donc de "
                       f"{nb(abs(ecart), 2)} point, un écart qui vient des flux ou de la géopolitique.")
        else:
            phr.append(f"Le mouvement de l'or ({nb(j['reel'], 2, True)} %) est cohérent avec le dollar, les taux et "
                       f"le pétrole : pas de force cachée aujourd'hui.")
    e = a.get("annonce")
    if e:
        rng = f", amplitude journalière normale ±{nb(a['range_jour'], 0)} $" if a.get("range_jour") else ""
        phr.append(f"Prochain risque : {e['titre']} le {date_fr(e['date'], True)}{rng}.")
    return phr


def expliquer(data, a):
    a["moteurs_jour"] = moteurs_du_jour(a)
    a["point"] = point_30s(a, data["yahoo"])
    now = maintenant()
    a["fiches"] = [fiche_annonce(e, a) for e in data["cal"] if e["impact"] == "High" and e["date"] >= now][:4]


# ---------------------------------------------------------------------------
# ANALYSE : ASSEMBLAGE
# ---------------------------------------------------------------------------

def analyser(data):
    y, f, cot, gld = data["yahoo"], data["fred"], data["cot"], data["gld"]
    a = {"mouvements": mouvements(y, f), "liquidite": liquidite(f),
         "structure": structure_or(data.get("courbe"), derniere(f.get("sofr")))}
    a["biais"] = biais_fondamental(y, f, cot, gld, a["liquidite"], a["structure"])
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

    a["devises"] = []
    for nom, cle, op in (("Euro", "eurusd", "div"), ("Yen", "usdjpy", "mul"),
                         ("Yuan", "usdcny", "mul"), ("Roupie", "usdinr", "mul")):
        fx = y.get(cle)
        if or_s is not None and fx is not None:
            df = pd.concat([or_s, fx], axis=1, join="inner").dropna()
            if len(df) > 21:
                serie = df.iloc[:, 0] / df.iloc[:, 1] if op == "div" else df.iloc[:, 0] * df.iloc[:, 1]
                a["devises"].append({"nom": nom, "prix": float(serie.iloc[-1]),
                                     "d5": variation(serie, 5), "d20": variation(serie, 20)})

    paires = [("dxy", "Dollar (DXY)", y.get("dxy"), "pct"), ("us10", "Taux 10 ans", y.get("us10"), "diff"),
              ("reel10", "Taux réel 10 ans", f.get("reel10"), "diff"), ("brent", "Brent", y.get("brent"), "pct"),
              ("spx", "S&P 500", y.get("spx"), "pct"), ("usdjpy", "USD/JPY", y.get("usdjpy"), "pct"),
              ("vix", "VIX", y.get("vix"), "diff")]
    a["correlations"] = [{"cle": k, "nom": n, "c20": correlation(or_s, s, m, 20), "c60": correlation(or_s, s, m, 60)}
                         for k, n, s, m in paires if s is not None]
    valides = [c for c in a["correlations"] if c["c20"] is not None and c["cle"] != "vix"]
    a["moteur"] = max(valides, key=lambda c: abs(c["c20"])) if valides else None
    a["regime"] = regime_marche(y, a["correlations"])
    a["decomp"] = decomposition(y)

    global AJUST
    base = None
    ref = derniere(data["intraday"]["barres"]["close"]) if data.get("intraday") else a["or"]["prix"]
    spot = (data.get("spot") or {}).get("prix")
    if ref and spot and abs(ref - spot) < ref * 0.03:
        base = ref - spot
    a["base"] = base
    AJUST = (-base if base is not None else 0.0) + DECALAGE_CFD
    a["seance"] = seance(data.get("intraday"))

    effr, us2 = derniere(f.get("effr")), derniere(f.get("us2"))
    a["fed"] = {"effr": effr, "us2": us2,
                "spread_pb": (us2 - effr) * 100 if effr is not None and us2 is not None else None}
    a["fedwatch"] = fedwatch(data.get("zq"), effr, derniere(f.get("cible_bas")), derniere(f.get("cible_haut")))
    a["ratios"] = ratios(y)
    a["vol"] = volatilite(y)
    a["cot"] = cot_etendu(cot, or_s)
    if gld is not None and len(gld) > 21:
        px = a["or"]["prix"] or 0
        d5, d20 = variation(gld, 5, "abs"), variation(gld, 20, "abs")
        a["gld"] = {"t": derniere(gld), "d5": d5, "d20": d20, "date": gld.index[-1],
                    "usd5": d5 * ONCES_PAR_TONNE * px / 1e9 if d5 is not None else None,
                    "usd20": d20 * ONCES_PAR_TONNE * px / 1e9 if d20 is not None else None}
    else:
        a["gld"] = None

    macro = []
    for cle, lib in (("cpi", "Inflation CPI (sur 1 an)"), ("cpi_core", "Inflation CPI core (sur 1 an)"),
                     ("pce_core", "PCE core (sur 1 an)")):
        s = f.get(cle)
        v, p = glissement_annuel(s)
        if v is not None:
            macro.append({"nom": lib, "val": nb(v, 1, unite="%"), "prec": nb(p, 1, unite="%"), "date": s.index[-1]})
    s = f.get("chomage")
    if s is not None and len(s) > 1:
        macro.append({"nom": "Taux de chômage", "val": nb(s.iloc[-1], 1, unite="%"),
                      "prec": nb(s.iloc[-2], 1, unite="%"), "date": s.index[-1]})
    s = f.get("nfp")
    if s is not None and len(s) > 2:
        d = s.diff().dropna()
        macro.append({"nom": "Créations d'emplois (NFP)", "val": nb(d.iloc[-1], 0, True, "k"),
                      "prec": nb(d.iloc[-2], 0, True, "k"), "date": s.index[-1]})
    s = f.get("claims")
    if s is not None and len(s) > 1:
        macro.append({"nom": "Inscriptions hebdo au chômage", "val": nb(s.iloc[-1] / 1000, 0, unite="k"),
                      "prec": nb(s.iloc[-2] / 1000, 0, unite="k"), "date": s.index[-1]})
    s = f.get("mich")
    if s is not None and len(s) > 1:
        macro.append({"nom": "Inflation attendue par les ménages", "val": nb(s.iloc[-1], 1, unite="%"),
                      "prec": nb(s.iloc[-2], 1, unite="%"), "date": s.index[-1]})
    a["macro"] = macro

    a["barometre"] = barometre(data.get("news"))
    a["surprise"] = surprise_macro(data.get("cal"))
    a["annonce"], a["zone_news"], a["minutes_annonce"] = prochaine_annonce(data.get("cal") or [])
    expliquer(data, a)
    return a


# ---------------------------------------------------------------------------
# ANALYSTE IA (optionnel) : API Claude, clé dans la variable ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------

CONSIGNE_IA = """Tu es l'analyste macro principal d'un desk de trading spécialisé sur l'or (XAU/USD).
Ton lecteur est un trader intraday qui veut comprendre le contexte fondamental avant sa séance.
Tu reçois un relevé chiffré complet, produit automatiquement. Rédige en français une analyse précise et chiffrée.
N'invente aucune donnée absente du relevé ; utilise les titres d'actualité uniquement comme contexte.

Structure exacte, avec ces titres précédés de ### :
### Lecture du marché
3 à 4 phrases : ce qui se passe sur l'or et pourquoi, en reliant les chiffres entre eux.
### Les forces en présence
Puces (lignes commençant par "- ") : ce qui soutient l'or, ce qui le pénalise, avec les chiffres.
### Scénarios pour les prochaines annonces
Pour chaque annonce majeure à venir : réaction probable si le chiffre sort au-dessus ou en dessous de la prévision.
### Ce qui invaliderait cette lecture
2 à 3 puces.
### Points de vigilance pour la séance
2 à 3 puces concrètes (niveaux, horaires, volatilité attendue).

Contraintes : 350 mots au maximum. Aucun ordre d'achat ou de vente, aucun conseil en investissement.
Quand les signaux se contredisent, dis-le clairement plutôt que de trancher artificiellement."""


def analyste_ia(brief):
    cle = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not cle:
        return None
    fichier = DOSSIER_CACHE / "analyse_ia.json"
    cache = None
    if fichier.exists():
        try:
            cache = json.loads(fichier.read_text(encoding="utf-8"))
        except Exception:
            cache = None
    now = maintenant()
    frais = cache and (time.time() - cache.get("ts", 0)) < IA_INTERVALLE_MIN * 60
    dans_fenetre = now.weekday() < 5 and IA_HEURES[0] <= now.hour < IA_HEURES[1]
    if frais or (cache and not dans_fenetre):
        noter("Analyste IA", True, "analyse en cache")
        return cache
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
            "x-api-key": cle, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": IA_MODELE, "max_tokens": 1400, "system": CONSIGNE_IA,
                  "messages": [{"role": "user", "content": "Relevé du terminal :\n\n" + brief}]})
        r.raise_for_status()
        texte = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
        if not texte:
            raise RuntimeError("réponse vide")
        cache = {"ts": time.time(), "texte": texte, "modele": IA_MODELE, "heure": now.isoformat()}
        DOSSIER_CACHE.mkdir(exist_ok=True)
        fichier.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        noter("Analyste IA", True, f"nouvelle analyse ({IA_MODELE})")
    except Exception as e:
        noter("Analyste IA", False, str(e)[:160])
    return cache


def mini_markdown(txt):
    """Convertit le texte de l'analyste (titres ###, puces -, **gras**) en HTML sûr."""
    def en_ligne(t):
        return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc(t))
    out, liste = [], False
    for l in (txt or "").splitlines():
        l = l.rstrip()
        if re.match(r"^\s*[-*•]\s+", l):
            if not liste:
                out.append("<ul>")
                liste = True
            out.append("<li>" + en_ligne(re.sub(r"^\s*[-*•]\s+", "", l)) + "</li>")
            continue
        if liste:
            out.append("</ul>")
            liste = False
        if l.startswith("#"):
            out.append("<h4>" + en_ligne(l.lstrip("#").strip()) + "</h4>")
        elif l.strip():
            out.append("<p>" + en_ligne(l) + "</p>")
    if liste:
        out.append("</ul>")
    return "".join(out)


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
    out, n = [], len(dates)
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


def graphique_ligne(s, d=2, unite="", w=720, h=200, n=None):
    if s is None:
        return '<p class="vide">Données indisponibles.</p>'
    s = s.dropna()
    if n:
        s = s.tail(n)
    if len(s) < 5:
        return '<p class="vide">Données insuffisantes.</p>'
    pl, pr, pt, pb = 54, 20, 14, 26
    W, H = w - pl - pr, h - pt - pb
    lo, hi = float(s.min()), float(s.max())
    x = lambda i: pl + i * W / (len(s) - 1)
    y = lambda v: pt + (hi - v) / ((hi - lo) or 1) * H
    p = "M" + " L".join(f"{x(i):.1f},{y(float(v)):.1f}" for i, v in enumerate(s))
    aire = p + f" L{x(len(s) - 1):.1f},{pt + H} L{x(0):.1f},{pt + H} Z"
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Graphique">'
            f'<path d="{aire}" class="aire"/><path d="{p}" class="l1"/>'
            f'<text x="{pl - 8}" y="{pt + 4}" text-anchor="end" class="ax">{nb(hi, d)}{unite}</text>'
            f'<text x="{pl - 8}" y="{pt + H}" text-anchor="end" class="ax">{nb(lo, d)}{unite}</text>'
            f'<circle cx="{x(len(s) - 1):.1f}" cy="{y(float(s.iloc[-1])):.1f}" r="3.5" class="pt"/>'
            f'{_axe_x(list(s.index), x, h - 6)}</svg>')


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


COURTS = {"Plus haut de la veille": "Haut veille", "Plus bas de la veille": "Bas veille"}


def graphique_bougies(s, w=1000, h=340):
    """Bougies 15 min de la veille et du jour, avec niveaux clés et VWAP."""
    df = s.get("barres_graph") if s else None
    if df is None or len(df) < 5 or not {"open", "high", "low", "close"} <= set(df.columns):
        return '<p class="vide">Bougies intraday indisponibles.</p>'
    pl, pr, pt, pb = 8, 150, 14, 24
    W, H = w - pl - pr, h - pt - pb
    lo, hi = float(df["low"].min()), float(df["high"].max())
    marge = (hi - lo) * 0.25
    niv = [(n["lib"], n["niveau"]) for n in s["niveaux"]
           if n["lib"] in ("Plus haut de la veille", "Plus bas de la veille", "Pivot", "R1", "S1", "VWAP du jour")
           and lo - marge <= n["niveau"] <= hi + marge]
    lo = min([lo] + [v for _, v in niv]) - (hi - lo) * 0.03
    hi = max([hi] + [v for _, v in niv]) + (hi - lo) * 0.03
    n = len(df)
    cw = W / n
    y = lambda v: pt + (hi - v) / ((hi - lo) or 1) * H
    out = []
    prec_date = None
    for i, (t, r) in enumerate(df.iterrows()):
        xc = pl + (i + 0.5) * cw
        if t.date() != prec_date:
            if prec_date is not None:
                out.append(f'<line x1="{pl + i * cw:.1f}" x2="{pl + i * cw:.1f}" y1="{pt}" y2="{pt + H}" class="sep"/>')
            out.append(f'<text x="{pl + i * cw + 4:.1f}" y="{pt + 12}" class="ax">{date_fr(t)}</text>')
            prec_date = t.date()
        if t.minute == 0 and t.hour % 4 == 0 and t.hour != 0:
            out.append(f'<text x="{xc:.1f}" y="{h - 6}" text-anchor="middle" class="ax">{t.hour} h</text>')
        hausse = r["close"] >= r["open"]
        c = "var(--up)" if hausse else "var(--down)"
        yo, yc = y(r["open"]), y(r["close"])
        out.append(f'<line x1="{xc:.1f}" x2="{xc:.1f}" y1="{y(r["high"]):.1f}" y2="{y(r["low"]):.1f}" '
                   f'stroke="{c}" stroke-width="1"/>')
        out.append(f'<rect x="{xc - cw * 0.32:.1f}" y="{min(yo, yc):.1f}" width="{max(cw * 0.64, 1):.1f}" '
                   f'height="{max(abs(yc - yo), 0.8):.1f}" fill="{c}"/>')
    vw = s.get("vwap_serie")
    if vw is not None and vw.notna().sum() > 1:
        pts = [(pl + (list(df.index).index(t) + 0.5) * cw, y(v)) for t, v in vw.dropna().items() if t in df.index]
        if len(pts) > 1:
            out.append('<polyline points="' + " ".join(f"{a:.1f},{b:.1f}" for a, b in pts) +
                       '" fill="none" stroke="var(--steel)" stroke-width="1.4" stroke-dasharray="5 3"/>')
    for lib, v in niv:
        if lib == "VWAP du jour":
            continue
        out.append(f'<line x1="{pl}" x2="{pl + W}" y1="{y(v):.1f}" y2="{y(v):.1f}" class="niv"/>'
                   f'<text x="{pl + W + 6}" y="{y(v) + 4:.1f}" class="ax niv-l">{esc(COURTS.get(lib, lib))} '
                   f'{nb(v + AJUST, 1)}</text>')
    px = s["prix"]
    out.append(f'<line x1="{pl}" x2="{pl + W}" y1="{y(px):.1f}" y2="{y(px):.1f}" class="prix-l"/>'
               f'<rect x="{pl + W + 2}" y="{y(px) - 9:.1f}" width="{pr - 6}" height="18" rx="2" fill="var(--brass)"/>'
               f'<text x="{pl + W + 8}" y="{y(px) + 4:.1f}" class="prix-t">{nb(px + AJUST, 1)}</text>')
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Bougies 15 minutes avec niveaux clés">'
            + "".join(out) + '</svg>')


# ---------------------------------------------------------------------------
# PAGE HTML : STYLE ET SCRIPT
# ---------------------------------------------------------------------------

CSS = """
:root{
  --bg:#0c1319; --panel:#121b23; --band:#172230; --band2:#1f2c39; --line:#223242; --ink:#e7e1d3; --mute:#8594a2;
  --brass:#cfa54b; --steel:#86a9c8; --up:#4fb884; --down:#e0605a; --amber:#f0a94b;
  --cond:"Barlow Semi Condensed","Arial Narrow","Roboto Condensed",system-ui,sans-serif;
  --text:"Barlow",system-ui,-apple-system,"Segoe UI",sans-serif;
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
html,body{margin:0;background:var(--bg);color:var(--ink)}
body{font:14.5px/1.45 var(--text);-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
a:hover{text-decoration:underline;text-decoration-color:var(--mute)}
.num,.m td.v,.m td.d{font-family:var(--cond);font-variant-numeric:tabular-nums}
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--mute)} .brass{color:var(--brass)}
button{font:500 13px var(--cond);color:var(--ink);background:var(--band);border:1px solid var(--line);border-radius:3px;
  padding:5px 10px;cursor:pointer;white-space:nowrap}
button:hover{border-color:var(--mute)}
button.or{color:var(--bg);background:var(--brass);border-color:var(--brass)}
button.actif-on{border-color:var(--up);color:var(--up)}
button.attente{border-color:var(--amber);color:var(--amber)}
button:focus-visible,a:focus-visible,summary:focus-visible{outline:2px solid var(--ink);outline-offset:2px}

/* Barre du haut */
.barre{position:sticky;top:0;z-index:30;background:#081016;border-bottom:1px solid var(--line)}
.barre .ligne{display:flex;align-items:center;gap:22px;padding:6px 14px;min-height:46px;flex-wrap:wrap}
.logo{font:600 16px var(--cond);letter-spacing:.07em;white-space:nowrap}
.logo i{display:inline-block;width:9px;height:16px;background:var(--brass);margin-right:9px;vertical-align:-2px;border-radius:1px}
.logo small{font:500 12px var(--cond);color:var(--mute);margin-left:6px;letter-spacing:0}
.horloges{display:flex;gap:16px}
.horloges div{display:flex;flex-direction:column;line-height:1.1}
.k{font:600 10.5px var(--cond);letter-spacing:.09em;text-transform:uppercase;color:var(--mute)}
.horloges b{font:500 15px var(--cond);font-variant-numeric:tabular-nums}
.marche{display:flex;flex-direction:column;line-height:1.15}
.marche b{font:500 14px var(--cond)}
.led{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:var(--mute);vertical-align:1px}
.led.on{background:var(--up);box-shadow:0 0 6px rgba(79,184,132,.7)} .led.off{background:var(--down)}
.fraicheur{display:flex;flex-direction:column;line-height:1.15}
.fraicheur b{font:500 14px var(--cond)}
.fraicheur.perime b{color:var(--amber)}
.actions{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
.alerte{display:none;padding:8px 14px;font:600 15px var(--cond);letter-spacing:.03em;text-align:center}
.alerte.on{display:block}
.alerte.z15{background:rgba(240,169,75,.18);color:var(--amber);border-top:1px solid rgba(240,169,75,.5)}
.alerte.z2{background:var(--down);color:#fff;animation:pulse 1s infinite}
.alerte.pub{background:rgba(224,96,90,.22);color:#ffb3ae;border-top:1px solid var(--down)}
@keyframes pulse{50%{opacity:.55}}
.tape{min-height:46px;border-bottom:1px solid var(--line);background:#0a1117}

/* Bandeau fondamental */
.strip{display:flex;overflow-x:auto;border-bottom:1px solid var(--line);scrollbar-width:thin}
.strip .it{display:flex;flex-direction:column;padding:7px 16px;border-right:1px solid var(--line);white-space:nowrap;line-height:1.2}
.strip .v{font:600 17px var(--cond);font-variant-numeric:tabular-nums;margin-top:2px}
.strip .s{font:500 12.5px var(--cond);color:var(--mute)}

/* Cockpit */
.cockpit{display:grid;gap:10px;padding:10px 14px;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr) minmax(0,.95fr);
  grid-template-areas:"g c d";height:calc(100vh - 170px);min-height:680px}
.col{display:flex;flex-direction:column;gap:10px;min-height:0;min-width:0}
.col-g{grid-area:g}.col-c{grid-area:c}.col-d{grid-area:d}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:4px;display:flex;flex-direction:column;min-height:0}
.ph{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:7px 12px;border-bottom:1px solid var(--line)}
.ph .k{color:var(--ink)} .ph small{font:500 11.5px var(--cond);color:var(--mute)}
.pb{padding:10px 12px;overflow:auto;flex:1;min-height:0}
.pb.nopad{padding:0;overflow:hidden}
#p-graph{flex:1}
.minis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;height:118px;flex:none}
.minis .panel{overflow:hidden}
#p-cmd{flex:1.35} #p-evt{flex:1}
#p-niv{flex:1.15} #p-mot{flex:.85} #p-news{flex:1.1}
.tv{position:relative;width:100%;height:100%}
.tv::before{content:attr(data-lib);position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  color:var(--mute);font:500 12.5px var(--cond);text-align:center;padding:8px}
.tv iframe{position:relative;z-index:1}
.flash{animation:flash 1.6s ease-out}
@keyframes flash{0%{box-shadow:inset 0 0 0 2px var(--brass)}100%{box-shadow:inset 0 0 0 2px transparent}}

.cmd-top{display:flex;align-items:center;gap:14px;margin-bottom:8px}
.verdict-s{font:700 30px/1 var(--cond);letter-spacing:.02em}
.balance-s{flex:1;position:relative;display:flex;height:20px;background:var(--band);border-radius:2px}
.balance-s .moitie{flex:1;display:flex;gap:2px;padding:3px}
.balance-s .g{justify-content:flex-end}
.balance-s .axe{position:absolute;left:50%;top:-4px;bottom:-4px;width:2px;background:var(--ink);opacity:.5}
.bloc{height:100%;border-radius:1px}
.bloc.b{background:var(--down)} .bloc.h{background:var(--up)}
.sig{display:grid;grid-template-columns:14px minmax(0,1fr) auto;gap:3px 8px;font-size:13px;align-items:baseline;margin-bottom:10px}
.sig .ic{font-size:10px}
.sig .val{font-family:var(--cond);color:var(--mute);font-variant-numeric:tabular-nums;text-align:right;white-space:nowrap}
.bloc-txt{margin:3px 0 10px;font-size:13.5px}
.bloc-txt b{font:600 16px var(--cond)}
.bloc-txt p{margin:2px 0 0;color:var(--mute)}

.evt-nom{font:600 19px/1.15 var(--cond)}
.evt-titre{color:var(--mute);font-size:13px;margin-top:2px}
.cpt{font:600 38px/1.1 var(--cond);font-variant-numeric:tabular-nums;color:var(--brass);margin:6px 0}
body.zone-news .cpt{color:var(--amber)}
.pp{display:flex;gap:18px;font-family:var(--cond);font-size:15px;margin-bottom:6px}
.pp span{display:block;color:var(--mute);font:400 11.5px var(--text)}
.sc{display:grid;grid-template-columns:18px 1fr;gap:4px 6px;font-size:13px;margin-top:6px}
.sc .num{color:var(--brass)}
.nu{color:var(--amber);font-size:12.5px;margin:6px 0 0}
.suite{list-style:none;margin:4px 0 0;padding:0;font-size:13px}
.suite li{padding:4px 0;border-bottom:1px solid rgba(34,50,66,.7)}
.suite .num{color:var(--mute);margin-right:6px}

.niv-c{width:100%;border-collapse:collapse;font-size:13px}
.niv-c td{padding:3px 0;border-bottom:1px solid rgba(34,50,66,.7)}
.niv-c td.num{text-align:right;font-size:14px}
.niv-c tr.ici td{color:var(--brass);font-weight:600;border-bottom:1px solid var(--brass)}
.pied{color:var(--mute);font-size:12.5px;margin-top:8px}
.pied b{color:var(--ink);font-family:var(--cond);font-weight:500}
.mot-c{list-style:none;margin:0;padding:0}
.mot-c li{display:grid;grid-template-columns:minmax(0,1fr) auto auto 16px;gap:8px;align-items:baseline;padding:4px 0;
  border-bottom:1px solid rgba(34,50,66,.7);font-size:13px}
.mot-c .num{font-size:14px}
.z{font:500 11.5px var(--cond);padding:0 6px;border-radius:8px;background:var(--band2);color:var(--mute)}

/* Onglets d'analyse */
.onglets{position:sticky;top:var(--haut-barre,46px);scroll-margin-top:var(--haut-barre,46px);z-index:20;display:flex;gap:2px;overflow-x:auto;background:var(--bg);
  border-top:1px solid var(--line);border-bottom:1px solid var(--line);padding:0 14px;scrollbar-width:none}
.onglets button{background:none;border:0;border-bottom:2px solid transparent;border-radius:0;padding:10px 12px;
  font:500 14.5px var(--cond);color:var(--mute)}
.onglets button:hover{color:var(--ink)}
.onglets button.actif{color:var(--ink);border-bottom-color:var(--brass)}
.onglets kbd{font:500 10.5px var(--cond);color:var(--mute);border:1px solid var(--line);border-radius:3px;padding:0 4px;margin-left:6px}
.contenu{max-width:1520px;margin:0 auto;padding:0 20px 40px}
.tabpanel[hidden]{display:none}
.tv-cal{height:620px;border:1px solid var(--line);border-radius:4px;background:var(--panel)}

/* Composants des onglets (analyse approfondie) */
section{padding:22px 0;border-bottom:1px solid var(--line)}
section h2{font:600 19px var(--cond);margin:0 0 12px}
section h3{font:500 14.5px var(--text);color:var(--mute);margin:18px 0 8px}
.grille3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:36px}
.grille2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:36px}
.grille4{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:28px}
.grille-cal{grid-template-columns:minmax(0,1.6fr) minmax(0,1fr)}
.grille-lec{grid-template-columns:minmax(0,1.5fr) minmax(0,1fr)}
.defile{overflow-x:auto}
.label{color:var(--mute);font-size:13.5px}
.legende{color:var(--mute);font-size:13px;margin:-6px 0 12px}
.pourquoi{color:var(--mute);font-size:13.5px;margin:-4px 0 14px;max-width:78ch}
.sous{margin-top:14px;color:var(--mute);font-size:13.5px;line-height:1.55}
.sous b{color:var(--ink);font-weight:500;font-family:var(--cond)}
.note{margin-top:14px;padding:10px 12px;border-left:3px solid var(--amber);background:rgba(240,169,75,.08);font-size:14px}
.lecture p{font-size:18px;line-height:1.55;margin:0 0 12px;max-width:68ch}
.lecture p:first-child{font-size:20px;line-height:1.45}
.regime{padding:16px 18px;background:var(--band);border-radius:3px}
.regime .nom{font:600 26px/1.1 var(--cond);margin:4px 0 8px}
.regime p{margin:0 0 10px;font-size:14.5px}
.regime p.cons{color:var(--mute)}
.decomp{display:grid;grid-template-columns:130px 1fr 76px;gap:6px 10px;align-items:center;font-size:14px;margin-top:14px}
.decomp .v{font-family:var(--cond);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.decomp .total{font-weight:600}
.ia{max-width:92ch}
.ia h4{font:600 16px var(--cond);color:var(--brass);margin:16px 0 6px}
.ia p,.ia li{font-size:15.5px;line-height:1.55}
.ia ul{margin:0;padding-left:20px}
.moteurs{list-style:none;margin:0;padding:0}
.moteurs li{padding:10px 0;border-bottom:1px solid rgba(34,50,66,.7)}
.moteurs .t{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}
.moteurs .t b{font:600 16px var(--cond)}
.moteurs .t .var{font-family:var(--cond);font-size:16px}
.moteurs .z{font:500 12.5px var(--cond);padding:1px 7px;border-radius:9px;background:var(--band2);color:var(--mute)}
.moteurs .mec{color:var(--mute);font-size:13.5px;margin-top:3px}
.heat td,.heat th{padding:5px 8px;font-size:14px;border-bottom:1px solid rgba(34,50,66,.7)}
.heat th{font-weight:400;color:var(--mute);font-size:12.5px;text-align:center}
.heat th:first-child{text-align:left}
.heat td.c{text-align:center;font-family:var(--cond);font-variant-numeric:tabular-nums;width:74px}
table{border-collapse:collapse;width:100%}
.m td{padding:5px 0;border-bottom:1px solid rgba(34,50,66,.7);vertical-align:middle}
.m td.l{padding-right:10px}
.m td.v{text-align:right;font-size:16px;font-weight:500;padding-right:4px;white-space:nowrap}
.m td.d{text-align:right;width:62px;font-size:14px;padding-left:12px;white-space:nowrap}
.m td.s{width:96px;text-align:right}
.m th{font:400 12.5px var(--text);color:var(--mute);text-align:right;padding:0 0 4px 12px;white-space:nowrap}
.m th:first-child{text-align:left;padding-left:0}
.spark{display:inline-block;vertical-align:middle}
.kv{display:grid;grid-template-columns:1fr auto;gap:4px 12px;font-size:14.5px}
.kv span:nth-child(even){font-family:var(--cond);font-variant-numeric:tabular-nums;text-align:right}
.pctbar{position:relative;height:8px;background:var(--band);border-radius:4px;margin:8px 0 4px}
.pctbar i{position:absolute;top:-3px;width:2px;height:14px;background:var(--mute)}
.pctbar b{position:absolute;top:-4px;width:10px;height:16px;margin-left:-5px;background:var(--brass);border-radius:2px}
.pctleg{display:flex;justify-content:space-between;color:var(--mute);font-size:12px}
.proba{display:flex;height:14px;border-radius:2px;overflow:hidden;background:var(--band);min-width:70px}
.proba i{display:block;height:100%}
.proba .pr-b{background:var(--up)} .proba .pr-s{background:var(--band2)} .proba .pr-h{background:var(--down)}
.corr{display:grid;grid-template-columns:130px 1fr 54px;gap:6px 12px;align-items:center;font-size:14px}
.cbar{position:relative;height:12px;background:var(--band)}
.cbar::after{content:"";position:absolute;left:50%;top:-3px;bottom:-3px;width:1px;background:var(--mute)}
.cbar b{position:absolute;top:0;bottom:0;background:var(--steel)}
.cbar b.up{background:var(--up)} .cbar b.down{background:var(--down)}
.cbar i{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink)}
.corr .cv{font-family:var(--cond);text-align:right;font-variant-numeric:tabular-nums}
.chart{width:100%;height:auto;display:block}
.chart .gr{stroke:var(--line);stroke-width:1}
.chart .sep{stroke:var(--line);stroke-width:1;stroke-dasharray:3 4}
.chart .l1{fill:none;stroke:var(--brass);stroke-width:1.8}
.chart .l2{fill:none;stroke:var(--steel);stroke-width:1.5}
.chart .aire{fill:var(--brass);opacity:.12}
.chart .seuil{stroke:var(--mute);stroke-dasharray:4 4;stroke-width:1}
.chart .niv{stroke:var(--mute);stroke-width:1;stroke-dasharray:2 3;opacity:.8}
.chart .prix-l{stroke:var(--brass);stroke-width:1;opacity:.7}
.chart .prix-t{fill:var(--bg);font:600 12.5px var(--cond)}
.chart .pt{fill:var(--brass)}
.chart .ax{fill:var(--mute);font:12px var(--cond)}
.chart .niv-l{fill:var(--ink)}
.chart .c1{fill:var(--brass)} .chart .c2{fill:var(--steel)}
.cle{display:flex;gap:18px;font-size:13px;color:var(--mute);margin-bottom:6px;flex-wrap:wrap}
.cle i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:6px}
.niveaux td{padding:5px 10px 5px 0;border-bottom:1px solid rgba(34,50,66,.7);font-size:14px}
.niveaux td.num{text-align:right;font-size:15px}
.niveaux tr.ici td{border-bottom:2px solid var(--brass);color:var(--brass);font-weight:600}
.fiches{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:20px}
.fiche{padding:14px 16px;background:var(--band);border-radius:3px}
.fiche .t{font:600 18px/1.2 var(--cond)}
.fiche .q{color:var(--brass);font:500 14.5px var(--cond);margin:3px 0 8px}
.fiche .pp{display:flex;gap:18px;font-family:var(--cond);margin-bottom:8px}
.fiche .pp span{display:block;color:var(--mute);font:400 12px var(--text)}
.fiche p{margin:6px 0;font-size:14px}
.fiche .sc{display:grid;grid-template-columns:22px 1fr;gap:4px 6px;font-size:14px;margin-top:8px}
.fiche .nu{color:var(--amber);font-size:13.5px}
.cal td,.cal th{padding:6px 10px 6px 0;border-bottom:1px solid rgba(34,50,66,.7);text-align:left;font-size:14px}
.cal th{font-weight:400;color:var(--mute);font-size:12.5px}
.cal td.num{font-size:15px}
.cal tr.passe td{color:var(--mute)}
.cal .imp{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}
.cal .imp.High{background:var(--down)} .cal .imp.Medium{background:var(--amber)}
.baro{display:grid;grid-template-columns:130px 1fr 130px;gap:8px 12px;align-items:center;font-size:14px;margin-bottom:10px}
.baro .r{text-align:right}
.baro .jauge{display:flex;height:12px;border-radius:2px;overflow:hidden;background:var(--band)}
.baro .jauge i{display:block;height:100%}
.exemples{color:var(--mute);font-size:13px;margin:0 0 14px}
.news ul{list-style:none;margin:0;padding:0}
.news li{padding:7px 0;border-bottom:1px solid rgba(34,50,66,.7);font-size:14px;line-height:1.35}
.news li small{display:block;color:var(--mute);font-size:12.5px;margin-top:2px}
.vide{color:var(--mute);font-size:14px}
.routine{margin:0;padding-left:22px;font-size:14.5px;line-height:1.5}
.routine li{padding:5px 0 5px 4px}
.routine li::marker{font-family:var(--cond);color:var(--brass);font-weight:600}
details.methode{border-bottom:1px solid rgba(34,50,66,.7)}
details.methode summary{cursor:pointer;padding:10px 0;font:600 16px var(--cond)}
details.methode div{padding:0 0 14px;color:var(--mute);font-size:14px;max-width:90ch}
details.methode div p{margin:0 0 8px}
footer{padding:18px 0 0;color:var(--mute);font-size:13px}
footer ul{list-style:none;padding:0;margin:8px 0 12px;display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:4px 24px}
footer .ok::before,footer .ko::before{content:"";display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:8px}
footer .ok::before{background:var(--up)} footer .ko::before{background:var(--down)}

/* MacBook plein écran : graphique + commandes en haut, niveaux / moteurs / actus en dessous */
@media (max-width:1499px){
  .cockpit{grid-template-columns:minmax(0,1.35fr) minmax(0,1fr);grid-template-areas:"g c" "d d";height:auto;min-height:0}
  .col-g,.col-c{height:calc(100vh - 170px);min-height:600px}
  .col-d{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));height:400px}
}
/* Demi-écran à côté de la plateforme : une seule colonne, l'essentiel d'abord */
@media (max-width:1099px){
  .cockpit{grid-template-columns:minmax(0,1fr);grid-template-areas:"c" "d" "g"}
  .col-g,.col-c{height:auto;min-height:0}
  #p-graph{height:430px;flex:none} .minis{grid-template-columns:repeat(2,minmax(0,1fr));height:236px}
  .col-d{display:flex;height:auto} #p-news{height:420px;flex:none}
  .pb{overflow:visible}
  .onglets{position:static}
  .horloges div:nth-child(n+3){display:none}
  .grille3,.grille4{grid-template-columns:1fr 1fr}
}
@media (max-width:720px){
  .grille3,.grille2,.grille4,.grille-cal,.grille-lec{grid-template-columns:1fr}
  .contenu{padding:0 14px 32px}
  .m td.s{display:none}
  .lecture p{font-size:16.5px}.lecture p:first-child{font-size:18px}
  .baro{grid-template-columns:90px 1fr 90px}
  .actions button.opt{display:none}
}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}*{animation:none!important;transition:none!important}}
"""


JS = """
(function(){
var CFG = __CFG__;
function $(s){ return document.querySelector(s); }
function pad(n){ return (n < 10 ? '0' : '') + n; }
function esc(t){ return String(t == null ? '' : t).replace(/[&<>"]/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
function lire(k, d){ try { var v = localStorage.getItem(k); return v === null ? d : v; } catch(e){ return d; } }
function ecrire(k, v){ try { localStorage.setItem(k, v); } catch(e){} }

/* Horloges et état du marché */
var HORLOGES = [['h-paris','Europe/Paris',true],['h-londres','Europe/London',false],
                ['h-ny','America/New_York',false],['h-tokyo','Asia/Tokyo',false]];
var FMT = {};
HORLOGES.forEach(function(h){
  var o = {hour:'2-digit', minute:'2-digit', timeZone:h[1]}; if (h[2]) o.second = '2-digit';
  FMT[h[0]] = new Intl.DateTimeFormat('fr-FR', o);
});
function parties(tz){
  var o = {}; new Intl.DateTimeFormat('en-US', {timeZone:tz, hour12:false, weekday:'short', hour:'2-digit', minute:'2-digit'})
    .formatToParts(new Date()).forEach(function(x){ o[x.type] = x.value; });
  return {j:o.weekday, h:(+o.hour % 24) + (+o.minute) / 60};
}
function ouvert(){
  var n = parties('America/New_York');
  if (n.j === 'Sat') return false;
  if (n.j === 'Sun') return n.h >= 18;
  if (n.j === 'Fri') return n.h < 17;
  return !(n.h >= 17 && n.h < 18);
}
function horloges(){
  var d = new Date();
  HORLOGES.forEach(function(h){ var el = document.getElementById(h[0]); if (el) el.textContent = FMT[h[0]].format(d); });
  var o = ouvert(), led = $('#led-marche'), txt = $('#txt-marche');
  if (led) led.className = 'led ' + (o ? 'on' : 'off');
  if (txt){
    var hp = parties('Europe/Paris').h, actives = CFG.seances.filter(function(s){ return hp >= s[1] && hp < s[2]; })
      .map(function(s){ return s[0]; });
    txt.textContent = o ? ('Ouvert' + (actives.length ? ' · ' + actives.join(' + ') : '')) : 'Fermé';
  }
}

/* Âge des analyses */
function age(){
  var el = $('#maj'); if (!el) return;
  var m = Math.max(0, Math.round((Date.now() - new Date(el.getAttribute('data-maj')).getTime()) / 60000));
  var j = new Date().getDay(), seuil = (j === 0 || j === 6) ? 240 : 45;
  el.textContent = 'il y a ' + (m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + pad(m % 60));
  el.parentNode.classList.toggle('perime', m > seuil);
}

/* Annonces, compte à rebours et alertes */
var EV = [], titreBase = document.title;
function chargerEv(){
  try { EV = JSON.parse(document.getElementById('evts').textContent)
          .map(function(e){ e.ts = new Date(e.t).getTime(); return e; }); }
  catch(e){ EV = []; }
}
function prochain(now){
  for (var i = 0; i < EV.length; i++){ if (EV[i].ts >= now - 10 * 60000) return EV[i]; }
  return null;
}
function hms(ms){
  var s = Math.max(0, Math.round(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return (h > 0 ? h + ':' + pad(m) : pad(m)) + ':' + pad(s % 60);
}
var FMT_EV = new Intl.DateTimeFormat('fr-FR', {weekday:'short', day:'numeric', month:'short', hour:'2-digit',
                                              minute:'2-digit', timeZone:'Europe/Paris'});
function rendreCarte(){
  var c = $('#evt-carte'), s = $('#evt-suite'); if (!c) return;
  var e = prochain(Date.now());
  if (!e){ c.innerHTML = '<p class="vide">Aucune annonce USD à fort impact dans le calendrier.</p>';
           c.setAttribute('data-cle', ''); if (s) s.innerHTML = ''; return; }
  c.innerHTML = '<div class="evt-nom">' + esc(e.nom) + '</div><div class="evt-titre">' + esc(e.titre) + ' · ' +
    esc(FMT_EV.format(new Date(e.ts))) + '</div><div class="cpt" id="cpt">--:--</div>' +
    '<div class="pp"><div><span>Prévision</span>' + esc(e.fcst || 'n.d.') + '</div><div><span>Précédent</span>' +
    esc(e.prev || 'n.d.') + '</div>' + (e.reel ? '<div><span>Réel</span><b>' + esc(e.reel) + '</b></div>' : '') + '</div>' +
    (e.haut ? '<div class="sc"><span class="num">▲</span><span>' + esc(e.haut) + '</span><span class="num">▼</span><span>' +
      esc(e.bas) + '</span></div>' : '') + (e.nuance ? '<p class="nu">' + esc(e.nuance) + '</p>' : '');
  c.setAttribute('data-cle', e.t + e.titre);
  if (s){
    var suite = EV.filter(function(x){ return x.ts > e.ts; }).slice(0, 4);
    s.innerHTML = suite.length ? suite.map(function(x){ return '<li><span class="num">' + esc(FMT_EV.format(new Date(x.ts))) +
      '</span>' + esc(x.nom) + '</li>'; }).join('') : '<li class="vide">Rien d’autre au calendrier.</li>';
  }
}
var faites = {};
try { faites = JSON.parse(lire('alertes-or', '{}')) || {}; } catch(e){ faites = {}; }
function marquer(cle){ faites[cle] = Date.now(); ecrire('alertes-or', JSON.stringify(faites)); }
function tick(){
  horloges(); age();
  var barre = $('.barre'); if (barre) document.documentElement.style.setProperty('--haut-barre', barre.offsetHeight + 'px');
  var now = Date.now(), e = prochain(now), b = $('#alerte');
  var carte = $('#evt-carte');
  if (carte && (e ? e.t + e.titre : '') !== carte.getAttribute('data-cle')) rendreCarte();
  if (!e){ if (b) b.className = 'alerte'; document.body.classList.remove('zone-news'); document.title = titreBase; return; }
  var d = e.ts - now, cpt = $('#cpt');
  if (cpt) cpt.textContent = d > 0 ? (d > 86400000 ? Math.floor(d / 86400000) + ' j ' + hms(d % 86400000) : hms(d))
                                   : 'publiée il y a ' + Math.floor(-d / 60000) + ' min';
  var etat = '', txt = '';
  if (d <= 0){ etat = 'pub'; txt = 'PUBLIÉE · ' + e.nom + ' · il y a ' + Math.floor(-d / 60000) +
               ' min · volatilité maximale, attends que le marché se stabilise'; }
  else if (d <= 2 * 60000){ etat = 'z2'; txt = 'ANNONCE IMMINENTE · ' + e.nom + ' · ' + hms(d) + ' · aucune nouvelle position'; }
  else if (d <= CFG.zone * 60000){ etat = 'z15'; txt = 'ZONE NEWS · ' + e.nom + ' dans ' + hms(d) + ' · pas de nouvelle position'; }
  if (b){ b.className = 'alerte' + (etat ? ' on ' + etat : ''); if (etat) b.textContent = txt; }
  document.body.classList.toggle('zone-news', !!etat);
  document.title = etat ? '[' + (etat === 'pub' ? 'PUBLIÉE' : hms(d)) + '] ' + titreBase : titreBase;
  CFG.alertes.forEach(function(m){
    var cle = e.t + '|' + m;
    if (d <= m * 60000 && d > m * 60000 - 90000 && !faites[cle]){ marquer(cle); alerter(e, m); }
  });
  if (d <= 0 && d > -90000 && !faites[e.t + '|0']){ marquer(e.t + '|0'); alerter(e, 0); }
}

/* Son et notifications */
var actx = null, sonOn = lire('son-or', '0') === '1';
function initAudio(){
  if (!actx){ var C = window.AudioContext || window.webkitAudioContext; if (C) actx = new C(); }
  if (actx && actx.state === 'suspended') actx.resume();
  majBoutons();
}
function bip(n, f){
  if (!sonOn || !actx) return;
  for (var i = 0; i < n; i++){
    var o = actx.createOscillator(), g = actx.createGain(), t = actx.currentTime + i * 0.3;
    o.type = 'sine'; o.frequency.value = f;
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.3, t + 0.02);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.24);
    o.connect(g); g.connect(actx.destination); o.start(t); o.stop(t + 0.26);
  }
}
function alerter(e, m){
  bip(m === 0 ? 3 : (m <= 1 ? 2 : 1), m <= 1 ? 1175 : 880);
  if ('Notification' in window && Notification.permission === 'granted'){
    try { new Notification('Terminal or', {body: m ? e.nom + ' dans ' + m + ' min : pas de nouvelle position'
                                                  : e.nom + ' publiée : attends la stabilisation', tag: e.t + m}); } catch(x){}
  }
}
function majBoutons(){
  var bs = $('#btn-son'), bn = $('#btn-notif');
  if (bs){
    var attente = sonOn && (!actx || actx.state !== 'running');
    bs.textContent = sonOn ? (attente ? 'Son : clique pour réactiver' : 'Son : activé') : 'Son : coupé';
    bs.className = sonOn ? (attente ? 'attente' : 'actif-on') : '';
  }
  if (bn){
    if (!('Notification' in window)){ bn.style.display = 'none'; }
    else {
      var p = Notification.permission;
      bn.textContent = p === 'granted' ? 'Alertes bureau : activées' : (p === 'denied' ? 'Alertes bureau : bloquées' : 'Activer les alertes bureau');
      bn.className = 'opt' + (p === 'granted' ? ' actif-on' : '');
    }
  }
}
document.addEventListener('pointerdown', function(){ if (sonOn) initAudio(); });
var bSon = $('#btn-son');
if (bSon) bSon.addEventListener('click', function(ev){
  ev.stopPropagation();
  if (sonOn && actx && actx.state === 'running'){ sonOn = false; }
  else { sonOn = true; initAudio(); setTimeout(function(){ bip(1, 660); }, 60); }
  ecrire('son-or', sonOn ? '1' : '0'); majBoutons();
});
var bNotif = $('#btn-notif');
if (bNotif) bNotif.addEventListener('click', function(){
  if ('Notification' in window && Notification.permission === 'default'){
    Notification.requestPermission().then(majBoutons);
  }
});

/* Onglets (touches 1 à 9) */
var onglet = lire('onglet-or', 'lecture');
function activerOnglet(id){
  var trouve = false;
  document.querySelectorAll('.onglets [role=tab]').forEach(function(b){
    var on = b.getAttribute('data-tab') === id; if (on) trouve = true;
    b.setAttribute('aria-selected', on ? 'true' : 'false'); b.classList.toggle('actif', on);
  });
  if (!trouve){ id = 'lecture'; if (arguments.length < 2) return activerOnglet(id, true); }
  document.querySelectorAll('.tabpanel').forEach(function(p){ p.hidden = p.id !== 'tab-' + id; });
  onglet = id; ecrire('onglet-or', id);
}
document.querySelectorAll('.onglets [role=tab]').forEach(function(b){
  b.addEventListener('click', function(){ activerOnglet(b.getAttribute('data-tab')); });
});
document.addEventListener('keydown', function(ev){
  var t = ev.target.tagName; if (t === 'INPUT' || t === 'TEXTAREA' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  var n = parseInt(ev.key, 10), tabs = document.querySelectorAll('.onglets [role=tab]');
  if (n >= 1 && n <= tabs.length){ activerOnglet(tabs[n - 1].getAttribute('data-tab'));
    document.getElementById('analyse').scrollIntoView({block:'start'}); }
});
document.addEventListener('click', function(ev){
  var a = ev.target.closest('[data-aller]'); if (!a) return;
  ev.preventDefault(); activerOnglet(a.getAttribute('data-aller'));
  document.getElementById('analyse').scrollIntoView({block:'start'});
});

/* Brief pour Claude */
var bCopie = $('#copier');
if (bCopie) bCopie.addEventListener('click', function(){
  var zone = document.getElementById('brief'), bouton = this;
  function fait(){ bouton.textContent = 'Brief copié'; setTimeout(function(){ bouton.textContent = 'Copier le brief'; }, 2500); }
  if (navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(zone.value).then(fait, function(){ zone.style.display = 'block'; zone.select();
      document.execCommand('copy'); zone.style.display = 'none'; fait(); });
  } else { zone.style.display = 'block'; zone.select(); document.execCommand('copy'); zone.style.display = 'none'; fait(); }
});

/* Nouvelles analyses sans recharger la page (les flux en direct continuent de tourner) */
function rafraichir(){
  if (location.protocol.indexOf('http') !== 0 || !window.fetch) return;
  fetch(location.pathname + '?t=' + Date.now(), {cache:'no-store'})
    .then(function(r){ return r.ok ? r.text() : null; })
    .then(function(html){
      if (!html) return;
      var doc = new DOMParser().parseFromString(html, 'text/html');
      var nm = doc.getElementById('maj'), cur = $('#maj');
      if (!nm || !cur || nm.getAttribute('data-maj') === cur.getAttribute('data-maj')) return;
      doc.querySelectorAll('[data-zone]').forEach(function(z){
        var c = document.querySelector('[data-zone="' + z.getAttribute('data-zone') + '"]');
        if (!c) return;
        c.innerHTML = z.innerHTML; c.classList.remove('flash'); void c.offsetWidth; c.classList.add('flash');
      });
      cur.setAttribute('data-maj', nm.getAttribute('data-maj'));
      titreBase = doc.title; chargerEv(); rendreCarte(); activerOnglet(onglet); tick();
    })
    .catch(function(){});
}
if (CFG.site){ setInterval(rafraichir, CFG.refresh * 60000); }

chargerEv(); activerOnglet(onglet); rendreCarte(); majBoutons(); tick(); setInterval(tick, 1000);
})();
"""


# ---------------------------------------------------------------------------
# PAGE HTML : BLOCS
# ---------------------------------------------------------------------------

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


ENTETE_M = '<tr><th></th><th>Dernier</th><th>1 j</th><th>5 j</th><th>60 j</th></tr>'


def barre_centree(v, echelle):
    if v is None or not echelle:
        return '<div class="cbar"></div>'
    larg = min(abs(v) / echelle, 1) * 50
    gauche = 50 - larg if v < 0 else 50
    return f'<div class="cbar"><b class="{"up" if v > 0 else "down"}" style="left:{gauche:.1f}%;width:{larg:.1f}%"></b></div>'


def bloc_lecture(a):
    paras = "".join(f"<p>{esc(p)}</p>" for p in a["point"])
    r = a["regime"]
    reg = (f'<div class="regime"><div class="label">Régime de marché</div><div class="nom {r["ton"]}">{esc(r["nom"])}</div>'
           f'<p>{esc(r["explication"])}</p><p class="cons">{esc(r["consequence"])}</p>')
    dc = a.get("decomp")
    if dc:
        j = dc["jour"]
        vals = list(j["contrib"].values()) + [j["attendu"], j["reel"], j["residu"]]
        ech = max(abs(v) for v in vals) or 1
        noms = {"dxy": "Dollar", "us10": "Taux 10 ans", "brent": "Pétrole"}
        lignes = "".join(f'<span>{noms[k]}</span>{barre_centree(v, ech)}<span class="v">{nb(v, 2, True)} %</span>'
                         for k, v in j["contrib"].items())
        lignes += (f'<span class="total">Attendu</span>{barre_centree(j["attendu"], ech)}'
                   f'<span class="v total">{nb(j["attendu"], 2, True)} %</span>'
                   f'<span class="total">Réel</span>{barre_centree(j["reel"], ech)}'
                   f'<span class="v total">{nb(j["reel"], 2, True)} %</span>'
                   f'<span>Écart (flux, géopolitique)</span>{barre_centree(j["residu"], ech)}'
                   f'<span class="v">{nb(j["residu"], 2, True)} %</span>')
        reg += (f'<h3>Décomposition du dernier mouvement de l\'or</h3>'
                f'<p class="legende" style="margin:0">Modèle sur 60 jours : le dollar, les taux et le pétrole expliquent '
                f'{nb(dc["r2"] * 100, 0)} % des variations de l\'or.</p><div class="decomp">{lignes}</div>')
    reg += "</div>"
    return (f'<section id="lecture"><h2>Le point en 30 secondes</h2><div class="grille2 grille-lec">'
            f'<div class="lecture">{paras}</div>{reg}</div></section>')


def bloc_ia(ia):
    if not ia or not ia.get("texte"):
        return ""
    try:
        heure = datetime.fromisoformat(ia["heure"])
        quand = date_fr(heure, True)
    except Exception:
        quand = ""
    return (f'<section id="analyste"><h2>Analyse du jour</h2><p class="legende">Rédigée par un analyste IA '
            f'({esc(ia.get("modele", ""))}) à partir des données de cette page, le {quand}. '
            f'Relis-la avec ton propre jugement.</p><div class="ia">{mini_markdown(ia["texte"])}</div></section>')


def couleur_z(z, effet):
    if z is None:
        return ""
    alpha = min(abs(z), 3) / 3 * 0.55 + 0.05
    rgb = "134,169,200" if effet == 0 else ("93,187,141" if z * effet > 0 else "227,104,91")
    return f"background:rgba({rgb},{alpha:.2f})"


def bloc_moteurs(a):
    items = ""
    for m in a["moteurs_jour"]:
        items += (f'<li><div class="t"><b>{esc(m["lib"])}</b><span class="var {m["ton"]}">{esc(m["var"])}</span>'
                  f'<span class="z">{nb(m["z"], 1, True)} σ, {esc(m["intensite"])}</span>'
                  f'<span class="{m["ton"]}">{esc(m["effet"])}</span></div>'
                  f'<div class="mec">{esc(m["mecanisme"])}</div></li>')
    lignes = ""
    for m in a["mouvements"]:
        cells = "".join(f'<td class="c" style="{couleur_z(m[z], m["effet"])}">{nb(m[z], 1, True)}</td>'
                        for z in ("z1", "z5", "z20"))
        lignes += f'<tr><td>{esc(m["lib"])}</td>{cells}</tr>'
    return (f'<section id="moteurs"><div class="grille2"><div><h2>Ce qui bouge l\'or aujourd\'hui</h2>'
            f'<p class="pourquoi">Les mouvements du jour classés par intensité. σ = combien de fois le mouvement habituel '
            f'(écart-type sur 60 jours) : au-delà de 2, c\'est un mouvement fort.</p><ul class="moteurs">{items}</ul></div>'
            f'<div><h2>Carte de chaleur des moteurs</h2><p class="pourquoi">Intensité des mouvements sur 1, 5 et 20 jours, '
            f'en σ. Vert : favorable à l\'or. Rouge : défavorable. Bleu : sans effet direct.</p>'
            f'<table class="heat"><tr><th></th><th>1 j</th><th>5 j</th><th>20 j</th></tr>{lignes}</table></div></div>'
            f'</section>')


def bloc_seance(a):
    s = a.get("seance")
    if not s:
        return ('<section id="seance"><h2>Séance du jour</h2><p class="vide">Données intraday indisponibles.</p>'
                '</section>')
    dec = (f" ; prix convertis en XAU/USD spot, écart future-spot de {nb(-AJUST + AJUST, 1)} $ retiré"
           if AJUST != DECALAGE_CFD else " ; prix en future COMEX")
    ici_fait, lignes = False, ""
    for n in s["niveaux"]:
        if not ici_fait and n["niveau"] < s["prix"]:
            lignes += (f'<tr class="ici"><td>Prix actuel</td><td class="num">{nb(s["prix"] + AJUST, 1)}</td>'
                       f'<td></td><td></td></tr>')
            ici_fait = True
        lignes += (f'<tr><td>{esc(n["lib"])}</td><td class="num">{nb(n["cfd"], 1)}</td>'
                   f'<td class="num {"up" if n["dist"] > 0 else "down"}">{nb(n["dist"], 1, True)} $</td>'
                   f'<td class="num flat">{nb(n["dist_atr"], 2, True)} ATR</td></tr>')
    if not ici_fait:
        lignes += (f'<tr class="ici"><td>Prix actuel</td><td class="num">{nb(s["prix"] + AJUST, 1)}</td>'
                   f'<td></td><td></td></tr>')
    ses = "".join(f'<tr><td class="l">{esc(x["nom"])}{" (en cours)" if x["en_cours"] else ""}</td>'
                  f'<td class="v">{nb((x["haut"] or 0) + AJUST, 1) if x["haut"] else "n.d."}</td>'
                  f'<td class="v">{nb((x["bas"] or 0) + AJUST, 1) if x["bas"] else "n.d."}</td>'
                  f'<td class="d {classe_effet(x["var"], 1)}">{nb(x["var"], 1, True)}</td></tr>' for x in s["seances"])
    vw = s.get("vwap")
    lect_vwap = ""
    if vw:
        lect_vwap = ("Prix au-dessus du VWAP : les acheteurs contrôlent la séance." if s["prix"] > vw
                     else "Prix sous le VWAP : les vendeurs contrôlent la séance.")
    stats = (f'<div class="kv"><span>Ouverture du jour</span><span>{nb(s["ouverture"] + AJUST, 1)}</span>'
             f'<span>Plus haut / plus bas du jour</span><span>{nb(s["haut"] + AJUST, 1)} / {nb(s["bas"] + AJUST, 1)}</span>'
             f'<span>Variation du jour</span><span class="{classe_effet(s["var_jour"], 1)}">{nb(s["var_jour"], 2, True)} %</span>'
             f'<span>ATR 14 jours</span><span>{nb(s["atr"], 1)} $</span>'
             f'<span>Amplitude du jour consommée</span><span>{nb(s["pct_atr"], 0)} % de l\'ATR</span>'
             f'<span>VWAP du jour</span><span>{nb((vw or 0) + AJUST, 1) if vw else "n.d."}</span></div>'
             f'<p class="sous">{esc(lect_vwap)} '
             f'{"L’amplitude habituelle est presque atteinte : les extensions deviennent moins probables." if (s["pct_atr"] or 0) >= 85 else ""}</p>')
    return (f'<section id="seance"><h2>Séance du jour</h2><p class="pourquoi">Bougies 15 minutes de la veille et du jour '
            f'(données Yahoo, environ 10 min de décalage){esc(dec)}. Pointillés : niveaux de la veille et pivots. '
            f'Tirets bleus : VWAP du jour.</p>{graphique_bougies(s)}'
            f'<div class="grille3" style="margin-top:18px"><div><h3 style="margin-top:0">Niveaux clés, du plus haut au plus bas</h3>'
            f'<table class="niveaux">{lignes}</table></div>'
            f'<div><h3 style="margin-top:0">Séances (heure de Paris)</h3><table class="m"><tr><th></th><th>Haut</th>'
            f'<th>Bas</th><th>Var. $</th></tr>{ses}</table></div>'
            f'<div><h3 style="margin-top:0">Repères de volatilité</h3>{stats}</div></div></section>')


def bloc_fed(y, f, a):
    fw = a.get("fedwatch")
    if fw:
        lignes = ""
        for r in fw["reunions"]:
            pb_, ps_, ph_ = r["p_baisse"] * 100, r["p_statu"] * 100, r["p_hausse"] * 100
            lignes += (f'<tr><td class="l" style="white-space:nowrap">{r["date"].day} {MOIS_FR[r["date"].month - 1]} '
                       f'{str(r["date"].year)[2:]}</td><td class="v">{nb(r["taux"], 2)} %</td>'
                       f'<td class="d {classe_effet(r["delta_pb"], -1)}">{nb(r["delta_pb"], 0, True)}</td>'
                       f'<td style="padding-left:12px"><div class="proba" title="Baisse {nb(pb_, 0)} %, statu quo '
                       f'{nb(ps_, 0)} %, hausse {nb(ph_, 0)} %"><i class="pr-b" style="width:{pb_:.0f}%"></i>'
                       f'<i class="pr-s" style="width:{ps_:.0f}%"></i><i class="pr-h" style="width:{ph_:.0f}%"></i></div></td>'
                       f'<td class="d">{nb(ph_ if ph_ >= pb_ else -pb_, 0, True)} %</td></tr>')
        cible = fw["cible"]
        cible_txt = f'{nb(cible[0], 2)} à {nb(cible[1], 2)} %' if cible[0] is not None and cible[1] is not None else "n.d."
        cumul = fw.get("cumul_fin_annee")
        phrase = ""
        if cumul is not None:
            sens = "de hausse" if cumul > 0 else "de baisse"
            phrase = (f'D\'ici fin {maintenant().year}, le marché price {nb(abs(cumul), 0)} pb {sens} '
                      f'(environ {nb(abs(cumul) / 25, 1)} mouvement de 25 pb). ')
        fed_html = (f'<p class="sous" style="margin-top:0">Fourchette actuelle : <b>{cible_txt}</b>, fed funds effectif '
                    f'<b>{nb(fw["effr"], 2)} %</b>. {phrase}</p>'
                    f'<div class="defile"><table class="m" style="margin-top:8px"><tr><th>Réunion</th><th>Taux</th>'
                    f'<th>Var. pb</th><th>Probabilités</th><th>Proba.</th></tr>{lignes}</table></div>'
                    f'<p class="legende" style="margin-top:8px">Calcul maison à partir des futures fed funds, proche de '
                    f'la méthode du CME FedWatch. Barre : baisse en vert, statu quo, hausse en rouge. '
                    f'Proba. positive = hausse, négative = baisse.</p>')
    else:
        fed = a["fed"]
        fed_html = (f'<p class="vide">Futures fed funds indisponibles. Écart taux 2 ans et fed funds : '
                    f'{nb(fed["spread_pb"], 0, True)} pb (positif = hausses attendues).</p>')
    taux = [
        ("Taux réel 5 ans", f.get("reel5"), "pb", 2, -1, " %"), ("Taux réel 10 ans", f.get("reel10"), "pb", 2, -1, " %"),
        ("Taux réel 30 ans", f.get("reel30"), "pb", 2, -1, " %"), ("Taux US 3 mois", y.get("us3m"), "pb", 2, -1, " %"),
        ("Taux US 2 ans", f.get("us2"), "pb", 2, -1, " %"), ("Taux US 10 ans", y.get("us10"), "pb", 2, -1, " %"),
        ("Taux US 30 ans", y.get("us30"), "pb", 2, -1, " %"),
        ("Inflation anticipée 5 ans", f.get("be5"), "pb", 2, 1, " %"),
        ("Inflation anticipée 10 ans", f.get("be10"), "pb", 2, 1, " %"),
        ("Inflation 5 ans dans 5 ans", f.get("fwd5y5y"), "pb", 2, 1, " %"),
        ("Pente 2 ans / 10 ans", f.get("pente2s10s"), "pb", 2, 0, " %"),
        ("Pente 3 mois / 10 ans", f.get("pente3m10a"), "pb", 2, 0, " %"),
        ("Prime de terme 10 ans", f.get("prime_terme"), "pb", 2, 0, " %"),
        ("SOFR", f.get("sofr"), "pb", 2, -1, " %"),
    ]
    t = "".join(ligne_metrique(*r) for r in taux)
    liq = a.get("liquidite")
    if liq:
        liq_html = (f'<div class="kv"><span>Liquidité nette</span><span>{nb(liq["niveau"], 2)} T$</span>'
                    f'<span>Sur 4 semaines</span><span class="{classe_effet(liq["d4"], 1)}">{nb(liq["d4"], 1, True)} %</span>'
                    f'<span>Sur 13 semaines</span><span class="{classe_effet(liq["d13"], 1)}">{nb(liq["d13"], 1, True)} %</span>'
                    f'<span>Bilan de la Fed</span><span>{nb(liq["bilan"], 2)} T$</span>'
                    f'<span>Compte du Trésor (TGA)</span><span>{nb(liq["tga"] * 1000, 0)} Md$</span>'
                    f'<span>Reverse repo</span><span>{nb(liq["rrp"] * 1000, 0)} Md$</span></div>'
                    f'{graphique_ligne(liq["serie"], 2, " T", w=520, h=170, n=104)}')
    else:
        liq_html = '<p class="vide">Données de liquidité indisponibles.</p>'
    stress = [("Spread high yield", f.get("hy"), "pb", 2, 0, " %"), ("Spread investment grade", f.get("ig"), "pb", 2, 0, " %"),
              ("Conditions financières (NFCI)", f.get("nfci"), "abs", 2, 0, ""),
              ("Volatilité obligataire (MOVE)", y.get("move"), "abs", 1, 0, ""), ("VIX", y.get("vix"), "abs", 1, 0, "")]
    st = "".join(ligne_metrique(*r) for r in stress)
    return (f'<section id="fed"><h2>Fed, taux et liquidité</h2><p class="pourquoi">L\'or ne rapporte rien : son premier '
            f'concurrent est le rendement sans risque. Tout ce qui fait monter les taux réels ou le dollar le pénalise ; '
            f'tout ce qui injecte de la liquidité ou fait douter de la dette américaine le soutient.</p>'
            f'<div class="grille3"><div><h3 style="margin-top:0">Ce que le marché price pour la Fed</h3>{fed_html}</div>'
            f'<div><h3 style="margin-top:0">Courbe des taux</h3><table class="m">{ENTETE_M}{t}</table>'
            f'<p class="legende" style="margin-top:8px">Pente qui se redresse par le long terme et prime de terme en hausse : '
            f'le marché exige plus pour détenir la dette US longue, thème favorable à l\'or.</p></div>'
            f'<div><h3 style="margin-top:0">Liquidité (bilan Fed - Trésor - reverse repo)</h3>{liq_html}'
            f'<h3>Stress financier</h3><table class="m">{ENTETE_M}{st}</table></div></div></section>')


def bloc_marches(y, f, a):
    e_p = effet_petrole()
    rows = [("Dollar index (DXY)", y.get("dxy"), "pct", 2, -1, ""), ("Dollar large (Fed)", f.get("dollar_large"), "pct", 2, -1, ""),
            ("EUR/USD", y.get("eurusd"), "pct", 4, 1, ""), ("USD/JPY", y.get("usdjpy"), "pct", 2, -1, ""),
            ("USD/CNY", y.get("usdcny"), "pct", 4, -1, ""), ("Brent", y.get("brent"), "pct", 2, e_p, " $"),
            ("WTI", y.get("wti"), "pct", 2, e_p, " $"), ("S&P 500", y.get("spx"), "pct", 0, 0, ""),
            ("Argent", y.get("argent"), "pct", 2, 1, " $"), ("Cuivre", y.get("cuivre"), "pct", 3, 0, " $"),
            ("Mines d'or (GDX)", y.get("gdx"), "pct", 2, 1, " $"), ("Volatilité or (GVZ)", y.get("gvz"), "abs", 1, 0, "")]
    t = "".join(ligne_metrique(*r) for r in rows)
    rat = "".join(f'<tr><td class="l">{esc(r["lib"])}<br><small class="flat">{esc(r["expl"])}</small></td>'
                  f'<td class="v">{nb(r["val"], 2)}</td><td class="d">{nb(r["d20"], 1, True)} %</td>'
                  f'<td class="d">{nb(r["pct1a"], 0)}</td></tr>' for r in a["ratios"])
    v = a.get("vol")
    vol = ""
    if v:
        lect = ""
        if v["prime"] is not None:
            lect = ("Le marché des options anticipe plus de mouvement que ce que l'or réalise : il se prépare à un "
                    "événement." if v["prime"] > 3 else
                    ("L'or bouge plus que ce que les options anticipaient : la volatilité surprend, prudence sur les stops."
                     if v["prime"] < -3 else "Volatilité réalisée et anticipée alignées."))
        vol = (f'<h3>Volatilité de l\'or</h3><div class="kv"><span>Réalisée sur 20 jours</span><span>{nb(v["realisee"], 1)} %</span>'
               f'<span>Anticipée par les options (GVZ)</span><span>{nb(v["implicite"], 1)} %</span>'
               f'<span>Écart</span><span>{nb(v["prime"], 1, True)} pt</span></div><p class="sous">{esc(lect)}</p>')
    return (f'<section id="marches"><h2>Dollar et marchés</h2><div class="grille2"><div><table class="m">{ENTETE_M}{t}</table>'
            f'</div><div><h3 style="margin-top:0">Ratios clés</h3><table class="m"><tr><th></th><th>Valeur</th><th>20 j</th>'
            f'<th>Percentile 1 an</th></tr>{rat}</table>{vol}</div></div></section>')


def bloc_flux(a):
    c = a["cot"]
    if c:
        pos = clamp(c["pct3a"] or 0, 0, 100)
        grp = "".join(f'<span>{esc(g["lib"])}</span><span>{nb(g["net"], 0)} ({nb(g["chg"], 0, True)}), pct {nb(g["pct3a"], 0)}</span>'
                      for g in c["groupes"])
        lect = (f'<div class="note" style="border-left-color:var(--steel);background:rgba(134,169,200,.08)">'
                f'<b>{esc(c["lecture"][0])}.</b> {esc(c["lecture"][1])}</div>') if c.get("lecture") else ""
        cot_html = (f'<h3 style="margin-top:0">Positionnement COMEX (rapport du {date_fr(c["date"])})</h3>'
                    f'<div class="kv"><span>Fonds, position nette ({esc(c["categorie"])})</span><span>{nb(c["net"], 0)} contrats</span>'
                    f'<span>Variation sur la semaine</span><span class="{classe_effet(c["chg"], 1)}">{nb(c["chg"], 0, True)}</span>'
                    f'<span>Achats / ventes des fonds</span><span>{nb(c["long"], 0)} / {nb(c["short"], 0)}</span>'
                    f'<span>Percentile des achats / ventes</span><span>{nb(c["pct_long"], 0)} / {nb(c["pct_short"], 0)}</span>'
                    f'<span>Part de l\'open interest</span><span>{nb(c["pct_oi"], 1)} %</span>{grp}</div>'
                    f'<div class="pctbar"><i style="left:{SEUILS["cot_bas"]}%"></i><i style="left:{SEUILS["cot_haut"]}%"></i>'
                    f'<b style="left:{pos:.1f}%"></b></div><div class="pctleg"><span>Peu exposés</span>'
                    f'<span>percentile 3 ans : {nb(c["pct3a"], 0)}</span><span>Surchargés</span></div>{lect}')
    else:
        cot_html = '<h3 style="margin-top:0">Positionnement COMEX</h3><p class="vide">COT indisponible.</p>'
    g = a["gld"]
    gld_html = (f'<h3>ETF GLD, avoirs physiques (au {date_fr(g["date"])})</h3>'
                f'<div class="kv"><span>Avoirs</span><span>{nb(g["t"], 1)} t</span>'
                f'<span>Sur 5 jours</span><span class="{classe_effet(g["d5"], 1)}">{nb(g["d5"], 1, True)} t '
                f'({nb(g["usd5"], 2, True)} Md$)</span>'
                f'<span>Sur 20 jours</span><span class="{classe_effet(g["d20"], 1)}">{nb(g["d20"], 1, True)} t '
                f'({nb(g["usd20"], 2, True)} Md$)</span></div>') if g else \
        '<h3>ETF GLD, avoirs physiques</h3><p class="vide">Données GLD indisponibles.</p>'
    st = a.get("structure")
    if st:
        lignes = "".join(f'<tr><td class="l">{esc(l["libelle"])}</td><td class="v">{nb(l["ecart"], 1, True)} $</td>'
                         f'<td class="d">{nb(l["portage"], 2)} %</td><td class="d {classe_effet(-l["vs_sofr"], -1)}">'
                         f'{nb(l["vs_sofr"], 2, True)}</td></tr>' for l in st["lignes"])
        lect = ("Portage nettement sous le SOFR : l'or physique est rare, ceux qui en ont besoin paient cher pour "
                "l'emprunter. Signal de demande physique forte." if st["tension"] else
                "Courbe normale : le portage reflète le coût de l'argent, pas de tension sur le physique.")
        st_html = (f'<h3>Structure du marché de l\'or</h3><table class="m"><tr><th>Échéances</th><th>Écart</th>'
                   f'<th>Portage annuel</th><th>vs SOFR</th></tr>{lignes}</table>'
                   f'<p class="sous">Taux de location implicite de l\'or : <b>{nb(st["location_implicite"], 2)} %</b>. '
                   f'{esc(lect)}</p>')
    else:
        st_html = ""
    dev = ""
    if a["devises"]:
        dev = ('<h3>L\'or dans d\'autres devises</h3><table class="m"><tr><th>Or en</th><th>Prix</th><th>5 j</th>'
               '<th>20 j</th></tr>' + "".join(
                   f'<tr><td class="l">{esc(d["nom"])}</td><td class="v">{nb(d["prix"], 0)}</td>'
                   f'<td class="d {classe_effet(d["d5"], 1)}">{nb(d["d5"], 2, True)}</td>'
                   f'<td class="d {classe_effet(d["d20"], 1)}">{nb(d["d20"], 2, True)}</td></tr>' for d in a["devises"])
               + '</table><p class="legende" style="margin-top:8px">Si l\'or baisse en dollars mais tient en euros, '
                 'le mouvement vient du dollar, pas de l\'or.</p>')
    return (f'<section id="flux"><h2>Flux et positionnement</h2><p class="pourquoi">Qui achète, qui vend. Les fonds '
            f'spéculatifs amplifient les mouvements ; les ETF reflètent les investisseurs occidentaux ; la structure '
            f'des futures révèle la tension sur l\'or physique.</p><div class="grille3"><div>{cot_html}</div>'
            f'<div>{gld_html}{st_html}</div><div>{dev}</div></div></section>')


def bloc_correlations(a):
    if not a["correlations"]:
        return '<div><h2>Qui mène l\'or en ce moment</h2><p class="vide">Données insuffisantes.</p></div>'
    lignes = ""
    for c in a["correlations"]:
        v20, v60 = c["c20"], c["c60"]
        barre = ""
        if v20 is not None:
            larg = abs(v20) * 50
            barre += f'<b style="left:{50 - larg if v20 < 0 else 50:.1f}%;width:{larg:.1f}%"></b>'
        if v60 is not None:
            barre += f'<i style="left:{50 + v60 * 50:.1f}%" title="60 jours : {nb(v60, 2)}"></i>'
        lignes += f'<span>{esc(c["nom"])}</span><div class="cbar">{barre}</div><span class="cv">{nb(v20, 2, True)}</span>'
    m = a["moteur"]
    phrase = ""
    if m:
        sens = "à l'inverse de" if m["c20"] < 0 else "dans le même sens que"
        phrase = (f'<p class="sous" style="margin-top:14px">Moteur dominant sur 20 jours : <b>{esc(m["nom"])}</b> '
                  f'(corrélation {nb(m["c20"], 2, True)}). L\'or bouge {sens} lui. Surveille-le en priorité pendant ta séance.</p>')
    return (f'<div><h2>Qui mène l\'or en ce moment</h2>'
            f'<p class="legende">Corrélation des variations quotidiennes avec l\'or. Barre : 20 jours. Trait fin : 60 jours.</p>'
            f'<div class="corr">{lignes}</div>{phrase}</div>')


def bloc_routine():
    etapes = [
        ("Lis le point en 30 secondes.", "Régime, biais et prochain risque : le contexte de ta séance."),
        ("Repère le moteur dominant.", "Garde ce marché ouvert à côté de ton graphique de l'or pendant la séance."),
        ("Vérifie la prochaine annonce.", "Bloc orange = zone news : aucune nouvelle position. Relis la fiche de scénarios."),
        ("Place les niveaux de la séance.", "Plus haut et plus bas de la veille, pivot, VWAP : ce sont les zones où le prix réagit."),
        ("Note le biais dans ton journal.", "Colonne « biais du jour » et « trade aligné oui/non » pour mesurer si le filtre t'aide."),
    ]
    li = "".join(f'<li><b>{esc(t)}</b> {esc(d)}</li>' for t, d in etapes)
    return f'<div><h2>Routine avant séance, 2 minutes</h2><ol class="routine">{li}</ol></div>'


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
    fiches = ""
    for fi in a["fiches"]:
        nu = "".join(f'<p class="nu">{esc(n)}</p>' for n in fi["nuances"])
        amp = f'<p class="flat">Amplitude journalière normale de l\'or : ±{nb(fi["amplitude"], 0)} $.</p>' if fi["amplitude"] else ""
        scen = ""
        if fi["haut"]:
            scen = (f'<div class="sc"><span class="num">▲</span><span>{esc(fi["haut"])}</span>'
                    f'<span class="num">▼</span><span>{esc(fi["bas"])}</span></div>')
        fiches += (f'<div class="fiche"><div class="t">{esc(fi["nom"])}</div>'
                   f'<div class="q">{date_fr(fi["date"], True)} (Paris)</div>'
                   f'<div class="pp"><div><span>Prévision</span>{esc(fi["prevision"] or "n.d.")}</div>'
                   f'<div><span>Précédent</span>{esc(fi["precedent"] or "n.d.")}</div></div>'
                   f'<p>{esc(fi["pourquoi"])}</p>{scen}{nu}{amp}</div>')
    fiches_html = (f'<h2>Scénarios avant les prochaines annonces</h2><p class="pourquoi">Pour chaque annonce à fort '
                   f'impact : ce qu\'elle mesure et la réaction probable de l\'or selon que le chiffre sort au-dessus ou '
                   f'en dessous de la prévision, dans le contexte actuel de la Fed.</p><div class="fiches">{fiches}</div>'
                   ) if fiches else '<h2>Scénarios avant les prochaines annonces</h2><p class="vide">Aucune annonce à fort impact à venir dans le calendrier.</p>'
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
        cal_html = ('<div class="defile"><table class="cal"><tr><th>Heure de Paris</th><th>Annonce USD</th><th>Réel</th>'
                    f'<th>Prévision</th><th>Précédent</th></tr>{lignes}</table></div>')
    macro = "".join(f'<tr><td class="l">{esc(m["nom"])}<br><small class="flat">{MOIS_FR[m["date"].month - 1]} '
                    f'{m["date"].year}</small></td><td class="v">{m["val"]}</td><td class="d flat">{m["prec"]}</td></tr>'
                    for m in a["macro"])
    macro_html = (f'<table class="m"><tr><th></th><th>Dernier</th><th>Précédent</th></tr>{macro}</table>'
                  if macro else '<p class="vide">Données macro indisponibles.</p>')
    su = a.get("surprise")
    su_html = ""
    if su:
        sens = ("plus fortes que prévu : argument pour une Fed dure, pression sur l'or" if su["score"] > 0.2 else
                ("plus faibles que prévu : argument pour une Fed souple, soutien à l'or" if su["score"] < -0.2 else
                 "conformes aux attentes dans l'ensemble"))
        su_html = (f'<h3>Indice de surprise maison</h3><p class="sous" style="margin-top:0">Sur les {su["n"]} dernières '
                   f'publications US : score <b>{nb(su["score"], 2, True)}</b>, données {esc(sens)}.</p>')
    return (f'<section id="calendrier">{fiches_html}</section><section class="grille2 grille-cal">'
            f'<div><h2>Calendrier économique</h2><p class="legende">Point rouge : fort impact. Point orange : impact moyen. '
            f'Règle en scalping : aucune position ouverte à l\'approche d\'un point rouge.</p>{cal_html}</div>'
            f'<div><h2>Macro US</h2>{macro_html}{su_html}<p class="legende" style="margin-top:10px">Aujourd\'hui : '
            f'données fortes = Fed plus dure = pression sur l\'or. Données faibles = l\'inverse.</p></div></section>')


def jauge(g, d, lib_g, lib_d, coul_g, coul_d):
    tot = (g + d) or 1
    return (f'<div class="baro"><span>{esc(lib_g)} ({g})</span><div class="jauge">'
            f'<i style="width:{g / tot * 100:.0f}%;background:{coul_g}"></i>'
            f'<i style="width:{d / tot * 100:.0f}%;background:{coul_d}"></i></div>'
            f'<span class="r">{esc(lib_d)} ({d})</span></div>')


def bloc_actus(news, fed_off, a):
    b = a.get("barometre")
    if b:
        ex = lambda cat: "".join(f"« {esc(t)} » " for t in b[cat + "_ex"])
        baro = (f'<p class="pourquoi">Comptage de mots-clés dans les {b["n"]} titres récents. Indicatif : un titre peut '
                f'utiliser un mot dans un autre sens.</p>'
                f'{jauge(b["dove"], b["hawk"], "Fed souple", "Fed dure", "var(--up)", "var(--down)")}'
                f'<p class="exemples">{ex("hawk") or ex("dove")}</p>'
                f'{jauge(b["desc"], b["esc"], "Détente", "Escalade", "var(--steel)", "var(--amber)")}'
                f'<p class="exemples">{ex("esc") or ex("desc")}</p>'
                f'<p class="legende">Avec le pétrole comme canal d\'inflation, une escalade pèse sur l\'or et une détente '
                f'le soutient ; en régime refuge classique, c\'est l\'inverse.</p>')
    else:
        baro = '<p class="vide">Pas assez de titres pour le baromètre.</p>'
    fo = ""
    for cle, lib in (("communiques", "Communiqués"), ("discours", "Discours")):
        items = (fed_off or {}).get(cle) or []
        li = "".join(f'<li><a href="{esc(i["lien"])}" target="_blank" rel="noopener">{esc(i["titre"])}</a>'
                     f'<small>{date_fr(i["date"]) if i["date"] else ""}</small></li>' for i in items[:5])
        fo += f'<h3>{lib}</h3>' + (f'<ul>{li}</ul>' if li else '<p class="vide">Indisponible.</p>')
    cols = ""
    for theme, _ in THEMES_NEWS:
        items = (news or {}).get(theme) or []
        li = "".join(f'<li><a href="{esc(i["lien"])}" target="_blank" rel="noopener">{esc(i["titre"])}</a>'
                     f'<small>{esc(i["source"])}{", " + date_fr(i["date"], True) if i["date"] else ""}</small></li>'
                     for i in items)
        cols += f'<div><h3 style="margin-top:0">{esc(theme)}</h3>' + (f'<ul>{li}</ul>' if li else
                                                                     '<p class="vide">Pas de titre récupéré.</p>') + '</div>'
    return (f'<section id="actus" class="news"><div class="grille2"><div><h2>Baromètre des titres</h2>{baro}</div>'
            f'<div><h2>Réserve fédérale, sources officielles</h2>{fo}</div></div></section>'
            f'<section class="news"><h2>Dernières actualités (48 h)</h2><div class="grille4">{cols}</div></section>')


METHODE = [
    ("Le biais fondamental", [
        "Neuf règles votent chacune -1, 0 ou +1 à partir des variations sur 5 séances : taux réel 10 ans, dollar, "
        "taux 2 ans, défiance envers les actifs US, pétrole, positionnement des fonds, flux ETF, liquidité nette et "
        "tension sur l'or physique. Score de +2 ou plus : haussier. -2 ou moins : baissier. Sinon : neutre.",
        "Le biais indique dans quel sens le vent souffle. Il ne donne ni le point d'entrée ni le moment : c'est le rôle "
        "de ton analyse technique."]),
    ("Le régime de marché", [
        "Le terminal compare les corrélations sur 20 jours et les mouvements récents pour identifier ce qui pilote l'or : "
        "taux et dollar, flux et banques centrales, refuge, fuite vers la qualité, défiance envers les actifs US ou "
        "liquidation. Le régime dit quels indicateurs écouter en priorité."]),
    ("La décomposition du mouvement", [
        "Une régression sur 60 jours estime la sensibilité de l'or au dollar, au taux 10 ans et au pétrole. Appliquée "
        "à la dernière séance, elle donne le mouvement que ces trois facteurs expliquent. L'écart restant vient de ce "
        "que le modèle ne voit pas : achats physiques, banques centrales, géopolitique, positionnement."]),
    ("Les σ (écarts-types)", [
        "Un mouvement de 2 σ est deux fois plus grand que la variation quotidienne habituelle des 60 derniers jours. "
        "C'est la façon la plus simple de comparer un mouvement de taux et un mouvement de pétrole."]),
    ("Les probabilités Fed", [
        "Les futures fed funds cotent le taux moyen attendu chaque mois. En comparant les mois avant et après chaque "
        "réunion, on en déduit le changement de taux attendu, puis une probabilité de hausse ou de baisse de 25 pb. "
        "Méthode proche de celle du CME FedWatch, donc des écarts de quelques points sont normaux."]),
    ("La liquidité nette", [
        "Bilan de la Fed moins le compte du Trésor et le reverse repo : c'est l'argent réellement disponible dans le "
        "système financier. Sa hausse soutient les actifs, sa baisse les pénalise, avec un effet plus lent que les taux."]),
    ("La structure du marché de l'or", [
        "Normalement, un contrat lointain coûte plus cher qu'un contrat proche, de l'ordre du taux d'intérêt (SOFR). "
        "Si l'écart est nettement plus faible, c'est que l'or physique est rare et cher à emprunter : signe de demande "
        "physique forte, souvent liée aux banques centrales ou à l'Asie."]),
    ("Les niveaux de séance", [
        "Plus haut, plus bas et clôture de la veille ; pivots classiques (P = (haut + bas + clôture) / 3) ; VWAP, le "
        "prix moyen pondéré par les volumes du jour ; ATR 14 jours, l'amplitude quotidienne moyenne. Ces niveaux sont en "
        "future COMEX, puis convertis en XAU/USD spot grâce à l'écart future-spot mesuré à chaque mise à jour. "
        "DECALAGE_CFD permet d'ajouter le petit écart propre à ton broker."]),
    ("Les limites", [
        "Yahoo a environ 10 min de décalage, la FRED un jour ouvré, le COT reflète le mardi précédent. Les achats des "
        "banques centrales et la prime de Shanghai ne sont pas disponibles gratuitement. Ce terminal est un outil "
        "d'aide à la lecture, pas un conseil en investissement."]),
]


def bloc_methode():
    items = "".join(f'<details class="methode"><summary>{esc(t)}</summary><div>'
                    + "".join(f"<p>{esc(p)}</p>" for p in paras) + '</div></details>' for t, paras in METHODE)
    return f'<section id="methode"><h2>Méthode : comment lire ce terminal</h2>{items}</section>'


def bloc_sources():
    items = "".join(f'<li class="{"ok" if s["ok"] else "ko"}">{esc(nom)}'
                    f'{" : " + esc(s["message"]) if s["message"] else ""}</li>' for nom, s in sorted(STATUT.items()))
    return (f'<footer><b>Sources</b><ul>{items}</ul>Terminal or v{VERSION}. Données indicatives. '
            f'Cotations, graphiques, actualités et calendrier en direct : '
            f'<a href="https://www.tradingview.com/" target="_blank" rel="noopener">TradingView</a>. '
            f'Outil d\'aide à la lecture, pas un conseil en investissement.</footer>')


def tv(script, config, lib, style=""):
    """Widget TradingView (flux en direct). Le texte lib reste visible si le flux ne charge pas."""
    return (f'<div class="tradingview-widget-container tv" data-lib="{esc(lib)}" style="{style}">'
            f'<div class="tradingview-widget-container__widget" style="height:100%;width:100%"></div>'
            f'<script type="text/javascript" src="https://s3.tradingview.com/external-embedding/{script}" async>'
            f'{json.dumps(config, ensure_ascii=False)}</script></div>')


def bloc_barre(a):
    maj = maintenant()
    horl = "".join(f'<div><span class="k">{v}</span><b id="{i}">--:--</b></div>'
                   for i, v in (("h-paris", "Paris"), ("h-londres", "Londres"), ("h-ny", "New York"), ("h-tokyo", "Tokyo")))
    return (f'<header class="barre"><div class="ligne">'
            f'<div class="logo"><i></i>TERMINAL OR<small>v{VERSION}</small></div>'
            f'<div class="horloges">{horl}</div>'
            f'<div class="marche"><span class="k">Marché de l\'or</span><b><span class="led" id="led-marche"></span>'
            f'<span id="txt-marche">...</span></b></div>'
            f'<div class="fraicheur"><span class="k">Analyses</span><b id="maj" data-maj="{maj.isoformat()}">'
            f'{date_fr(maj, True)}</b></div>'
            f'<div class="actions"><button type="button" id="btn-son">Son : coupé</button>'
            f'<button type="button" id="btn-notif" class="opt">Activer les alertes bureau</button>'
            f'<button type="button" id="copier" class="or">Copier le brief</button></div></div>'
            f'<div class="alerte" id="alerte" role="status" aria-live="assertive"></div></header>')


def bloc_bandeau_tv():
    conf = {"symbols": [{"proName": s, "description": lib} for s, lib in TV_BANDEAU], "showSymbolLogo": False,
            "isTransparent": True, "displayMode": "compact", "colorTheme": "dark", "locale": "fr"}
    return f'<div class="tape">{tv("embed-widget-ticker-tape.js", conf, "Cotations en direct (TradingView)", "height:46px")}</div>'


def bloc_strip(a, y, f):
    it = []
    b = a["biais"]
    it.append(("Biais", f'<span class="{ {"Haussier": "up", "Baissier": "down"}.get(b["verdict"], "flat") }">'
                        f'{b["verdict"]}</span>', f'score {nb(b["total"], 0, True) if b["total"] else "0"} / {b["n"]}'))
    it.append(("Régime", esc(a["regime"]["nom"]), esc(a["regime"]["resume"][:48] + ("…" if len(a["regime"]["resume"]) > 48 else ""))))
    for lib, s in (("Taux réel 10 a", f.get("reel10")), ("US 2 ans", f.get("us2"))):
        d = variation(s, 5, "pb")
        it.append((lib, f'{nb(derniere(s), 2)} %', f'<span class="{classe_effet(d, -1)}">{nb(d, 0, True)} pb / 5 j</span>'))
    fw = a.get("fedwatch")
    if fw and fw["reunions"]:
        r0 = fw["reunions"][0]
        if r0["p_hausse"] >= 0.5 or r0["p_baisse"] >= 0.5:
            txt = (f'Hausse {nb(r0["p_hausse"] * 100, 0)} %' if r0["p_hausse"] >= r0["p_baisse"]
                   else f'Baisse {nb(r0["p_baisse"] * 100, 0)} %')
        else:
            txt = f'Statu quo {nb(r0["p_statu"] * 100, 0)} %'
        it.append((f'Fed {r0["date"].day} {MOIS_FR[r0["date"].month - 1]}', txt, f'taux attendu {nb(r0["taux"], 2)} %'))
    d = variation(y.get("dxy"), 5)
    it.append(("Dollar DXY", nb(derniere(y.get("dxy")), 2), f'<span class="{classe_effet(d, -1)}">{nb(d, 2, True)} % / 5 j</span>'))
    it.append(("Vol. or GVZ", nb(derniere(y.get("gvz")), 1),
               f'±{nb(a["range_jour"], 0)} $ / jour' if a.get("range_jour") else ""))
    if a.get("cot"):
        it.append(("Fonds (COT)", f'pct {nb(a["cot"]["pct3a"], 0)}', f'{nb(a["cot"]["chg"], 0, True)} contrats'))
    if a.get("gld"):
        g = a["gld"]
        it.append(("ETF GLD", f'<span class="{classe_effet(g["d5"], 1)}">{nb(g["d5"], 1, True)} t</span>',
                   f'{nb(g["usd5"], 2, True)} Md$ / 5 j'))
    if a.get("liquidite"):
        l = a["liquidite"]
        it.append(("Liquidité Fed", f'{nb(l["niveau"], 2)} T$',
                   f'<span class="{classe_effet(l["d4"], 1)}">{nb(l["d4"], 1, True)} % / 4 sem.</span>'))
    if a.get("base") is not None:
        it.append(("Écart future-spot", f'{nb(a["base"], 1, True)} $', "retiré des niveaux"))
    corps = "".join(f'<div class="it"><span class="k">{lib}</span><span class="v">{v}</span><span class="s">{sub}</span></div>'
                    for lib, v, sub in it)
    return f'<div class="strip" data-zone="strip">{corps}</div>'


def bloc_commande(a):
    b = a["biais"]
    coul = {"Haussier": "up", "Baissier": "down"}.get(b["verdict"], "flat")
    n = max(b["n"], 1)
    larg = f"calc((100% - {2 * (n - 1)}px) / {n})"
    baiss = "".join(f'<div class="bloc b" style="width:{larg}"></div>' for s in b["signaux"] if s["score"] < 0)
    hauss = "".join(f'<div class="bloc h" style="width:{larg}"></div>' for s in b["signaux"] if s["score"] > 0)
    sig = ""
    for s in b["signaux"]:
        ic = {1: '<span class="ic up">▲</span>', -1: '<span class="ic down">▼</span>'}.get(s["score"], '<span class="ic flat">●</span>')
        sig += f'{ic}<span title="{esc(s["lecture"])}">{esc(s["nom"])}</span><span class="val">{esc(s["valeur"])}</span>'
    r = a["regime"]
    lect = [p for p in a["point"][2:] if not p.startswith("Prochain risque")]
    div = f'<p class="nu">{esc(a["divergence"])}</p>' if a.get("divergence") else ""
    return (f'<div class="cmd-top"><div><div class="k">Biais fondamental</div>'
            f'<div class="verdict-s {coul}">{b["verdict"].upper()}</div>'
            f'<div class="k">score {nb(b["total"], 0, True) if b["total"] else "0"} sur {b["n"]}</div></div>'
            f'<div class="balance-s"><div class="moitie g">{baiss}</div><div class="moitie">{hauss}</div>'
            f'<div class="axe"></div></div></div><div class="sig">{sig}</div>'
            f'<div class="k">Régime de marché</div><div class="bloc-txt"><b class="{r["ton"]}">{esc(r["nom"])}</b>'
            f'<p>{esc(r["consequence"])}</p></div>'
            f'<div class="k">Lecture</div><div class="bloc-txt">' + "".join(f"<p>{esc(p)}</p>" for p in lect) +
            f'{div}<p><a href="#analyse" data-aller="lecture">Lecture complète et analyste IA</a></p></div>')


def bloc_niveaux_compact(a):
    s = a.get("seance")
    if not s:
        return '<p class="vide">Données intraday indisponibles.</p>'
    lignes, fait = "", False
    choisis = [n for n in s["niveaux"] if abs(n["dist"]) <= 3 * (s["atr"] or 1e9)]
    for n in choisis:
        if not fait and n["niveau"] < s["prix"]:
            lignes += (f'<tr class="ici"><td>Dernier relevé</td><td class="num">{nb(s["prix"] + AJUST, 1)}</td>'
                       f'<td></td></tr>')
            fait = True
        lignes += (f'<tr><td>{esc(n["lib"])}</td><td class="num">{nb(n["cfd"], 1)}</td>'
                   f'<td class="num {"up" if n["dist"] > 0 else "down"}">{nb(n["dist"], 1, True)}</td></tr>')
    if not fait:
        lignes += f'<tr class="ici"><td>Dernier relevé</td><td class="num">{nb(s["prix"] + AJUST, 1)}</td><td></td></tr>'
    vw = s.get("vwap")
    pos = ("au-dessus du VWAP : acheteurs aux commandes" if s["prix"] > vw else "sous le VWAP : vendeurs aux commandes") if vw else ""
    ses = " · ".join(f'{x["nom"]} <b>{nb(x["haut"] + AJUST, 1)}</b>/<b>{nb(x["bas"] + AJUST, 1)}</b>'
                     for x in s["seances"] if x["haut"] is not None)
    return (f'<table class="niv-c">{lignes}</table>'
            f'<p class="pied">ATR 14 j <b>{nb(s["atr"], 1)} $</b> · consommé <b>{nb(s["pct_atr"], 0)} %</b>'
            f'{" · prix " + esc(pos) if pos else ""}</p><p class="pied">{ses}</p>'
            f'<p class="pied"><a href="#analyse" data-aller="seance">Graphique des niveaux et détail des séances</a></p>')


def bloc_moteurs_compact(a):
    li = ""
    for m in a["moteurs_jour"]:
        fl = {"up": '<span class="up">▲</span>', "down": '<span class="down">▼</span>'}.get(m["ton"], '<span class="flat">●</span>')
        li += (f'<li title="{esc(m["mecanisme"])}"><span>{esc(m["lib"])}</span><span class="num {m["ton"]}">{esc(m["var"])}</span>'
               f'<span class="z">{nb(m["z"], 1, True)} σ</span>{fl}</li>')
    return (f'<ul class="mot-c">{li}</ul><p class="pied">▲ favorable à l\'or · ▼ défavorable · σ = intensité du '
            f'mouvement. <a href="#analyse" data-aller="moteurs">Carte de chaleur</a></p>')


def evenements_json(data, a):
    now = maintenant()
    out = []
    for e in data.get("cal") or []:
        if e["impact"] != "High" or e["date"] < now - timedelta(hours=1):
            continue
        fi = fiche_annonce(e, a)
        out.append({"t": e["date"].isoformat(), "titre": e["titre"], "nom": fi["nom"], "prev": e["precedent"],
                    "fcst": e["prevision"], "reel": e["reel"], "haut": fi["haut"], "bas": fi["bas"],
                    "nuance": fi["nuances"][0] if fi["nuances"] else ""})
    return json.dumps(out[:16], ensure_ascii=False).replace("</", "<\\/")


def bloc_cockpit(a, y, f):
    chart = tv("embed-widget-advanced-chart.js", {
        "autosize": True, "symbol": TV_OR, "interval": "15", "timezone": FUSEAU, "theme": "dark", "style": "1",
        "locale": "fr", "allow_symbol_change": True, "calendar": False, "hide_volume": True,
        "backgroundColor": "#121b23", "gridColor": "rgba(34,50,66,0.45)", "support_host": "https://www.tradingview.com"},
        "Graphique XAU/USD en direct (TradingView)")
    minis = "".join(
        f'<div class="panel">{tv("embed-widget-mini-symbol-overview.js", {"symbol": sym, "width": "100%", "height": "100%", "locale": "fr", "dateRange": "1D", "colorTheme": "dark", "isTransparent": True, "autosize": True, "largeChartUrl": "", "noTimeScale": True}, lib)}</div>'
        for sym, lib in TV_MINIS)
    news = tv("embed-widget-timeline.js", {"feedMode": "symbol", "symbol": TV_OR, "colorTheme": "dark", "isTransparent": True,
                                           "displayMode": "compact", "width": "100%", "height": "100%",
                                           "locale": TV_LANGUE_ACTUS}, "Fil d'actualité en direct (TradingView)")
    return (f'<main class="cockpit">'
            f'<div class="col col-g"><div class="panel" id="p-graph"><div class="ph"><span class="k">XAU/USD en direct</span>'
            f'<small>{esc(TV_OR)} · TradingView</small></div><div class="pb nopad">{chart}</div></div>'
            f'<div class="minis">{minis}</div></div>'
            f'<div class="col col-c"><div class="panel" id="p-cmd"><div class="ph"><span class="k">Poste de commandement</span>'
            f'<small><a href="#analyse" data-aller="lecture">analyse complète</a></small></div>'
            f'<div class="pb" data-zone="cmd">{bloc_commande(a)}</div></div>'
            f'<div class="panel" id="p-evt"><div class="ph"><span class="k">Prochaine annonce à fort impact</span>'
            f'<small><a href="#analyse" data-aller="annonces">scénarios</a></small></div>'
            f'<div class="pb"><div id="evt-carte" data-cle="x"><p class="vide">Chargement...</p></div>'
            f'<div class="k" style="margin-top:10px">Ensuite</div><ul class="suite" id="evt-suite"></ul></div></div></div>'
            f'<div class="col col-d"><div class="panel" id="p-niv"><div class="ph"><span class="k">Niveaux de séance</span>'
            f'<small>{"XAU/USD spot" if a.get("base") is not None else "future COMEX"}, écart au prix</small></div>'
            f'<div class="pb" data-zone="niv">{bloc_niveaux_compact(a)}</div></div>'
            f'<div class="panel" id="p-mot"><div class="ph"><span class="k">Ce qui bouge l\'or</span><small>séance précédente</small></div>'
            f'<div class="pb" data-zone="mot">{bloc_moteurs_compact(a)}</div></div>'
            f'<div class="panel" id="p-news"><div class="ph"><span class="k">Fil d\'actualité en direct</span>'
            f'<small>TradingView</small></div><div class="pb nopad">{news}</div></div></div></main>')


ONGLETS = [("lecture", "Lecture"), ("moteurs", "Moteurs"), ("seance", "Séance"), ("fed", "Fed et taux"),
           ("marches", "Marchés"), ("flux", "Flux"), ("annonces", "Annonces"), ("actus", "Actualités"),
           ("methode", "Méthode")]


def bloc_onglets(data, a, ia):
    y, f = data["yahoo"], data["fred"]
    contenus = {
        "lecture": bloc_lecture(a) + bloc_ia(ia) + f'<section class="grille2">{bloc_correlations(a)}{bloc_routine()}</section>',
        "moteurs": bloc_moteurs(a),
        "seance": bloc_seance(a),
        "fed": bloc_fed(y, f, a),
        "marches": bloc_marches(y, f, a),
        "flux": bloc_flux(a) + bloc_graphiques(y, f, data["cot"], a),
        "annonces": bloc_calendrier(data["cal"], a),
        "actus": bloc_actus(data.get("news"), data.get("fed_off"), a),
        "methode": bloc_methode(),
    }
    nav = "".join(f'<button type="button" role="tab" data-tab="{i}" aria-selected="false">{esc(t)}<kbd>{k + 1}</kbd></button>'
                  for k, (i, t) in enumerate(ONGLETS))
    cal_tv = tv("embed-widget-events.js", {"colorTheme": "dark", "isTransparent": True, "width": "100%", "height": "100%",
                                           "locale": "fr", "importanceFilter": "0,1", "countryFilter": "us"},
                "Calendrier économique en direct (TradingView)")
    panneaux = ""
    for i, t in ONGLETS:
        extra = ""
        if i == "annonces":
            extra = (f'<section><h2>Calendrier en direct</h2><p class="pourquoi">Les chiffres réels s\'affichent ici dès '
                     f'leur publication (TradingView).</p><div class="tv-cal">{cal_tv}</div></section>')
        panneaux += (f'<div class="tabpanel" id="tab-{i}" role="tabpanel" aria-label="{esc(t)}">'
                     f'<div data-zone="t-{i}">{contenus[i]}</div>{extra}</div>')
    return (f'<nav class="onglets" id="analyse" role="tablist" aria-label="Analyse approfondie">{nav}</nav>'
            f'<div class="contenu">{panneaux}<div data-zone="sources">{bloc_sources()}</div></div>')


def rendre_html(data, a, brief, ia=None, watch_min=None, site=False):
    y, f = data["yahoo"], data["fred"]
    refresh = f'<meta http-equiv="refresh" content="{int(watch_min * 60) + 60}">' if (watch_min and not site) else ""
    cfg = json.dumps({"alertes": ALERTES_MIN, "zone": FENETRE_NEWS[0], "refresh": RAFRAICHISSEMENT_MIN,
                      "site": bool(site), "seances": [[n, d, fn] for n, d, fn in SEANCES]})
    corps = (f'{bloc_barre(a)}{bloc_bandeau_tv()}{bloc_strip(a, y, f)}{bloc_cockpit(a, y, f)}'
             f'{bloc_onglets(data, a, ia)}'
             f'<script type="application/json" id="evts" data-zone="evts">{evenements_json(data, a)}</script>'
             f'<div data-zone="brief" hidden><textarea id="brief" style="display:none" aria-hidden="true">'
             f'{esc(brief)}</textarea></div>')
    return (f'<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">{refresh}'
            f'<title>Or {nb(a["or"]["prix"], 0)} · {a["biais"]["verdict"]} · Terminal or</title>'
            f'<link rel="preconnect" href="https://fonts.googleapis.com">'
            f'<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link href="https://fonts.googleapis.com/css2?family=Barlow:wght@400;500;600&'
            f'family=Barlow+Semi+Condensed:wght@400;500;600;700&display=swap" rel="stylesheet">'
            f'<style>{CSS}</style></head><body>{corps}'
            f'<script>{JS.replace("__CFG__", cfg)}</script></body></html>')


# ---------------------------------------------------------------------------
# BRIEF POUR CLAUDE ET RÉSUMÉ TERMINAL
# ---------------------------------------------------------------------------

def construire_brief(data, a):
    y, f = data["yahoo"], data["fred"]
    o, b = a["or"], a["biais"]
    L = [f"BRIEF TERMINAL OR v{VERSION} ({date_fr(maintenant(), True)}, heure de Paris)", ""]
    L.append(f"Or : {nb(o['prix'], 1)} $ (1 j {nb(o['d1'], 2, True)} %, 5 j {nb(o['d5'], 2, True)} %, "
             f"20 j {nb(o['d20'], 2, True)} %). Amplitude attendue du jour : ±{nb(a['range_jour'], 0)} $.")
    L.append(f"Régime : {a['regime']['nom']} ({a['regime']['resume']})")
    L.append(f"Biais fondamental : {b['verdict']} (score {nb(b['total'], 0, True)} sur {b['n']})")
    for s in b["signaux"]:
        L.append(f"  - {s['nom']} : {s['valeur']} -> {s['lecture']}")
    if a["divergence"]:
        L.append(f"Divergence : {a['divergence']}")
    if a["moteurs_jour"]:
        L.append("Moteurs du jour : " + " ; ".join(f"{m['lib']} {m['var']} ({nb(m['z'], 1, True)} σ, {m['effet']})"
                                                   for m in a["moteurs_jour"]))
    dc = a.get("decomp")
    if dc:
        j = dc["jour"]
        L.append(f"Décomposition dernière séance (R² 60 j {nb(dc['r2'] * 100, 0)} %) : attendu {nb(j['attendu'], 2, True)} % "
                 f"(dollar {nb(j['contrib']['dxy'], 2, True)}, taux {nb(j['contrib']['us10'], 2, True)}, "
                 f"pétrole {nb(j['contrib']['brent'], 2, True)}), réel {nb(j['reel'], 2, True)} %, "
                 f"écart {nb(j['residu'], 2, True)} %")
    L.append("")
    fw = a.get("fedwatch")
    if fw:
        L.append("Probabilités Fed (futures fed funds) : " + " ; ".join(
            f"{date_fr(r['date'])} : taux {nb(r['taux'], 2)} %, {nb(r['delta_pb'], 0, True)} pb, "
            f"hausse {nb(r['p_hausse'] * 100, 0)} % / baisse {nb(r['p_baisse'] * 100, 0)} %" for r in fw["reunions"][:4]))
    L.append("Taux : " + ", ".join(
        f"{lib} {nb(derniere(s), 2)} % ({nb(variation(s, 5, 'pb'), 0, True)} pb/5 j)"
        for lib, s in (("réel 10 a", f.get("reel10")), ("2 a", f.get("us2")), ("10 a", y.get("us10")),
                       ("30 a", y.get("us30")), ("breakeven 10 a", f.get("be10")),
                       ("prime de terme", f.get("prime_terme"))) if s is not None))
    liq = a.get("liquidite")
    if liq:
        L.append(f"Liquidité nette : {nb(liq['niveau'], 2)} T$ (4 sem. {nb(liq['d4'], 1, True)} %, "
                 f"13 sem. {nb(liq['d13'], 1, True)} %)")
    L.append("Marchés (5 j) : " + ", ".join(
        f"{lib} {nb(derniere(s), 2)} ({nb(variation(s, 5), 2, True)} %)"
        for lib, s in (("DXY", y.get("dxy")), ("EUR/USD", y.get("eurusd")), ("USD/JPY", y.get("usdjpy")),
                       ("Brent", y.get("brent")), ("S&P 500", y.get("spx")), ("Argent", y.get("argent")),
                       ("GDX", y.get("gdx"))) if s is not None))
    L.append(f"VIX {nb(derniere(y.get('vix')), 1)}, MOVE {nb(derniere(y.get('move')), 1)}, "
             f"GVZ {nb(derniere(y.get('gvz')), 1)}, spread HY {nb(derniere(f.get('hy')), 2)} %")
    c = a["cot"]
    if c:
        lect = f", lecture : {c['lecture'][0]}" if c.get("lecture") else ""
        L.append(f"COT ({date_fr(c['date'])}, {c['categorie']}) : net {nb(c['net'], 0)} contrats, "
                 f"variation hebdo {nb(c['chg'], 0, True)}, percentile 3 ans {nb(c['pct3a'], 0)}{lect}")
    g = a["gld"]
    if g:
        L.append(f"GLD : {nb(g['t'], 1)} t (5 j {nb(g['d5'], 1, True)} t, 20 j {nb(g['d20'], 1, True)} t)")
    st = a.get("structure")
    if st:
        L.append(f"Structure or : portage {nb(st['ref']['portage'], 2)} % vs SOFR {nb(st['sofr'], 2)} %, "
                 f"location implicite {nb(st['location_implicite'], 2)} %{', TENSION PHYSIQUE' if st['tension'] else ''}")
    if a.get("base") is not None:
        L.append(f"Écart future COMEX - spot : {nb(a['base'], 1, True)} $ (niveaux de séance donnés en spot)")
    if a["correlations"]:
        L.append("Corrélations 20 j de l'or : " + ", ".join(f"{c['nom']} {nb(c['c20'], 2, True)}"
                                                            for c in a["correlations"]))
    s = a.get("seance")
    if s:
        L.append(f"Séance : prix {nb(s['prix'], 1)}, jour {nb(s['bas'], 1)}-{nb(s['haut'], 1)}, ATR14 {nb(s['atr'], 1)} $ "
                 f"({nb(s['pct_atr'], 0)} % consommé), VWAP {nb(s.get('vwap'), 1)}, veille haut {nb(s.get('pdh'), 1)} / "
                 f"bas {nb(s.get('pdl'), 1)}, pivot {nb(s.get('pivot'), 1)}")
    if a["macro"]:
        L.append("Macro US : " + " ; ".join(f"{m['nom']} {m['val']} (préc. {m['prec']})" for m in a["macro"]))
    su = a.get("surprise")
    if su:
        L.append(f"Indice de surprise macro : {nb(su['score'], 2, True)} sur {su['n']} publications")
    bb = a.get("barometre")
    if bb:
        L.append(f"Baromètre des titres : Fed dure {bb['hawk']} / souple {bb['dove']}, escalade {bb['esc']} / détente {bb['desc']}")
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
    for it in ((data.get("fed_off") or {}).get("discours") or [])[:2]:
        L.append(f"  [Discours Fed] {it['titre']}")
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
    print(couleur(f"TERMINAL OR v{VERSION}", "1;33"), f"  {date_fr(maintenant(), True)}")
    print(f"Or {nb(o['prix'], 1)} $   1 j {nb(o['d1'], 2, True)} %   5 j {nb(o['d5'], 2, True)} %")
    print(f"Régime : {a['regime']['nom']}")
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

    def marche(debut, vol, derive=0.0):
        v, out = debut, []
        for _ in idx:
            v *= 1 + random.gauss(derive, vol)
            out.append(v)
        return pd.Series(out, index=idx)

    def taux(debut, vol, derive=0.0):
        v, out = debut, []
        for _ in idx:
            v += random.gauss(derive, vol)
            out.append(v)
        return pd.Series(out, index=idx)

    y = {"or": marche(2650, 0.011, 0.0010), "argent": marche(31, 0.02, 0.0011), "cuivre": marche(4.3, 0.015),
         "gdx": marche(38, 0.02, 0.001), "dxy": marche(106, 0.004, -0.0001), "us3m": taux(4.1, 0.01),
         "us5": taux(4.0, 0.04, 0.001), "us10": taux(4.4, 0.04, 0.0015), "us30": taux(4.7, 0.035, 0.0018),
         "brent": marche(75, 0.02, 0.0007), "wti": marche(71, 0.021, 0.0006), "spx": marche(5800, 0.009, 0.0006),
         "vix": taux(17, 0.8).clip(lower=11), "move": taux(95, 3).clip(lower=60), "gvz": taux(19, 0.6).clip(lower=12),
         "usdjpy": marche(150, 0.005, 0.0001), "eurusd": marche(1.08, 0.004, 0.0001),
         "usdcny": marche(7.2, 0.002, -0.0001), "usdinr": marche(84, 0.002, 0.0002)}
    f = {"reel5": taux(1.7, 0.035, 0.001), "reel10": taux(1.9, 0.035, 0.001), "reel30": taux(2.2, 0.03, 0.001),
         "be5": taux(2.3, 0.02, 0.0006), "be10": taux(2.3, 0.02, 0.0008), "fwd5y5y": taux(2.25, 0.02, 0.0006),
         "us2": taux(3.9, 0.04, 0.0009), "pente2s10s": taux(0.4, 0.03), "pente3m10a": taux(0.3, 0.03),
         "effr": pd.Series([3.58] * 420 + [3.83] * 80, index=idx), "sofr": pd.Series([3.60] * 420 + [3.86] * 80, index=idx),
         "cible_bas": pd.Series([3.5] * 420 + [3.75] * 80, index=idx),
         "cible_haut": pd.Series([3.75] * 420 + [4.0] * 80, index=idx),
         "hy": taux(3.0, 0.03), "ig": taux(0.9, 0.01), "dollar_large": marche(125, 0.003)}
    semaines = pd.date_range(end=fin, periods=200, freq="W-WED")
    f["bilan_fed"] = pd.Series([6_700_000 - 3000 * i for i in range(len(semaines))], index=semaines)
    f["tga"] = pd.Series([750_000 + random.gauss(0, 40_000) for _ in semaines], index=semaines)
    f["rrp"] = pd.Series([max(5.0, 400 - 2 * i + random.gauss(0, 10)) for i in range(len(idx))], index=idx)
    f["nfci"] = pd.Series([-0.5 + random.gauss(0, 0.03) for _ in semaines], index=semaines)
    f["prime_terme"] = pd.Series([0.6 + 0.004 * i for i in range(len(semaines))], index=semaines)
    mois = pd.date_range(end=fin, periods=48, freq="MS")
    nm = len(mois)
    f["cpi"] = pd.Series([300 * (1.0025 ** i) for i in range(nm)], index=mois)
    f["cpi_core"] = pd.Series([310 * (1.0021 ** i) for i in range(nm)], index=mois)
    f["pce_core"] = pd.Series([120 * (1.0022 ** i) for i in range(nm)], index=mois)
    f["chomage"] = pd.Series([4.1 + 0.05 * math.sin(i) for i in range(nm)], index=mois)
    f["nfp"] = pd.Series([158000 + 140 * i + random.gauss(0, 60) for i in range(nm)], index=mois)
    f["mich"] = pd.Series([3.1 + 0.1 * math.sin(i / 3) for i in range(nm)], index=mois)
    sem_t = pd.date_range(end=fin, periods=200, freq="W-TUE")
    f["claims"] = pd.Series([215000 + random.gauss(0, 9000) for _ in sem_t], index=sem_t)
    ns = len(sem_t)
    mm = taux(180000, 9000).iloc[-ns:].values
    cot = pd.DataFrame({"date": sem_t, "mm_long": [n + 60000 for n in mm], "mm_short": [60000.0] * ns,
                        "swap_long": [90000.0] * ns, "swap_short": [260000.0 + random.gauss(0, 5000) for _ in range(ns)],
                        "prod_long": [30000.0] * ns, "prod_short": [70000.0 + random.gauss(0, 3000) for _ in range(ns)],
                        "oi": [520000] * ns})
    for g in ("mm", "swap", "prod"):
        cot[g + "_net"] = cot[g + "_long"] - cot[g + "_short"]
    cot["long"], cot["short"], cot["net"] = cot["mm_long"], cot["mm_short"], cot["mm_net"]
    cot.attrs["categorie"] = "managed money"
    gld = marche(880, 0.002, 0.0002)

    tz = tz_local()
    now = maintenant()
    debut = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    ib_idx = pd.date_range(debut, now, freq="15min", tz=tz)
    ib_idx = ib_idx[(ib_idx.hour < 23)]
    px, lignes = float(y["or"].iloc[-1]) * 0.995, []
    for _ in ib_idx:
        o = px
        c = o * (1 + random.gauss(0.00005, 0.0012))
        h, l = max(o, c) * (1 + abs(random.gauss(0, 0.0006))), min(o, c) * (1 - abs(random.gauss(0, 0.0006)))
        lignes.append((o, h, l, c, random.randint(800, 5000)))
        px = c
    barres = pd.DataFrame(lignes, index=ib_idx, columns=["open", "high", "low", "close", "volume"])
    jidx = y["or"].index[-120:]
    cl = y["or"].iloc[-120:].values
    jours = pd.DataFrame({"open": cl * 0.998, "high": cl * 1.009, "low": cl * 0.991, "close": cl}, index=jidx)
    intraday = {"barres": barres, "jours": jours}

    zq = {}
    for i in range(16):
        m = (now.month - 1 + i) % 12 + 1
        a = now.year + (now.month - 1 + i) // 12
        zq[(a, m)] = 3.83 + min(i, 4) * 0.07
    courbe = [{"libelle": l, "prix": p, "echeance": e} for l, p, e in (
        ("déc. 2026", 4930.0, date(2026, 12, 27)), ("févr. 2027", 4958.0, date(2027, 2, 27)),
        ("avr. 2027", 4985.0, date(2027, 4, 27)), ("juin 2027", 5011.0, date(2027, 6, 27)))]

    cal = [{"date": now + timedelta(hours=h), "titre": t, "impact": imp, "prevision": pv, "precedent": pr, "reel": re_}
           for h, t, imp, pv, pr, re_ in (
               (-70, "Unemployment Claims", "High", "221K", "218K", "209K"),
               (-50, "Final GDP q/q", "Medium", "2.1%", "2.1%", "2.4%"),
               (-30, "Core Durable Goods Orders m/m", "Medium", "0.2%", "0.1%", "0.5%"),
               (5, "Core PCE Price Index m/m", "High", "0.3%", "0.2%", ""),
               (28, "ISM Manufacturing PMI", "High", "52.4", "51.8", ""),
               (74, "Non-Farm Employment Change", "High", "95K", "142K", ""),
               (74, "Unemployment Rate", "High", "4.1%", "4.1%", ""))]
    titres = ["Gold slips as Treasury yields climb on hawkish Fed bets", "Fed officials signal another rate hike",
              "Oil jumps after missile strike near Hormuz", "Iran and US resume talks on ceasefire deal",
              "Central bank gold buying stays strong", "Dollar firm ahead of PCE data", "Gold ETF outflows continue"]
    news = {t: [{"titre": titres[(k + i) % len(titres)], "lien": "#", "source": "Reuters",
                 "date": now - timedelta(hours=i * 3)} for i in range(5)] for k, (t, _) in enumerate(THEMES_NEWS)}
    fed_off = {"communiques": [{"titre": "Federal Reserve issues FOMC statement", "lien": "#", "source": "",
                                "date": now - timedelta(days=9)}],
               "discours": [{"titre": "Governor speech on the economic outlook", "lien": "#", "source": "",
                             "date": now - timedelta(days=1)}]}
    for nom in ("Yahoo Finance", "Yahoo intraday", "FRED (Fed de St. Louis)", "CFTC (COT)", "SPDR Gold Shares (GLD)",
                "ForexFactory (calendrier)", "Google News", "Futures fed funds", "Courbe des futures or",
                "Réserve fédérale (RSS)"):
        noter(nom, True, "données de démonstration")
    noter("Stooq (or spot)", True, "données de démonstration")
    return {"yahoo": y, "fred": f, "cot": cot, "gld": gld, "cal": cal, "news": news, "zq": zq,
            "intraday": intraday, "courbe": courbe, "fed_off": fed_off,
            "spot": {"prix": float(barres["close"].iloc[-1]) - 31.6}}


IA_DEMO = """### Lecture du marché
L'or recule de **1,2 %** sur la semaine, pénalisé par la remontée des taux réels et un dollar ferme.
### Les forces en présence
- Taux réel 10 ans en hausse de 12 pb : pression directe.
- Achats physiques toujours solides : plancher sous les prix.
### Scénarios pour les prochaines annonces
- PCE au-dessus de 0,3 % : pression supplémentaire sur l'or.
### Ce qui invaliderait cette lecture
- Un accord qui rouvre le détroit d'Ormuz.
### Points de vigilance pour la séance
- Zone news à 14 h 30."""


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
    ia = ({"texte": IA_DEMO, "modele": "démonstration", "heure": maintenant().isoformat()} if demo
          else analyste_ia(brief))
    page = rendre_html(data, a, brief, ia, watch_min, site=site_dir is not None)
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
