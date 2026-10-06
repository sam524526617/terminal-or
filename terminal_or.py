#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TERMINAL OR v4 : poste de marché XAU/USD
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

VERSION = "6.2"

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
# Les widgets gratuits refusent certains symboles (TVC:DXY, TVC:US10Y, TVC:VIX...) : on utilise des équivalents CFD.
# T-Note = prix de l'obligation américaine : il MONTE quand les taux BAISSENT.
TV_BANDEAU = [("OANDA:XAUUSD", "XAU/USD"), ("OANDA:XAGUSD", "XAG/USD"), ("CAPITALCOM:DXY", "Dollar index"),
              ("OANDA:USB02YUSD", "T-Note 2 ans"), ("OANDA:USB10YUSD", "T-Note 10 ans"), ("TVC:UKOIL", "Brent"),
              ("FOREXCOM:SPXUSD", "S&P 500"), ("CAPITALCOM:VIX", "VIX"), ("FX:EURUSD", "EUR/USD"),
              ("FX:USDJPY", "USD/JPY")]
TV_MINIS = [("CAPITALCOM:DXY", "Dollar index"), ("OANDA:USB10YUSD", "T-Note 10 ans (monte si les taux baissent)"),
            ("TVC:UKOIL", "Brent"), ("FOREXCOM:SPXUSD", "S&P 500")]
TV_LANGUE_ACTUS = "en"   # le fil d'actualité en direct est plus fourni en anglais

# Taille de position, utilisée par le contrôle Achat / Vente. Valeurs par défaut : tu peux les changer
# directement dans la page (bouton « Réglages »), sans toucher à ce fichier.
COMPTE = {
    "capital": 50000,            # taille du compte, en $
    "risque_pct": 0.5,           # risque par trade, en % du compte
    "spread": 0.30,              # écart achat-vente moyen de ton broker sur XAU/USD, en $ (compté dans le risque)
    "once_par_lot": 100,         # XAU/USD : 1 lot = 100 onces, donc 1 $ de mouvement = 100 $ par lot
}

# Vue de marché : poids des composantes (fondamental, tendance multi-unités, momentum du jour, sentiment)
POIDS_VUE = {"fondamental": 0.35, "tendance": 0.35, "momentum": 0.15, "sentiment": 0.15}
HORIZON_H = 4  # horizon des scénarios et du suivi des prévisions, en heures

# Réunions passées de la Fed (décision à 14 h, heure de New York), pour mesurer la réaction de l'or
FOMC_PASSES = ["2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12", "2024-07-31", "2024-09-18", "2024-11-07",
               "2024-12-18", "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18", "2025-07-30", "2025-09-17",
               "2025-10-29", "2025-12-10", "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29",
               "2026-09-16"]

# Carnet d'ordres et de positions OANDA (optionnel, compte démo gratuit) : secret GitHub OANDA_TOKEN.
# Données des clients particuliers d'OANDA sur XAU/USD, mises à jour toutes les 20 minutes environ.
OANDA_INSTRUMENT = "XAU_USD"
OANDA_HOTES = ("https://api-fxpractice.oanda.com", "https://api-fxtrade.oanda.com")  # démo d'abord, puis réel

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

# Analyste IA (optionnel). Deux fournisseurs possibles, choisis selon la clé présente dans les secrets GitHub :
#   GEMINI_API_KEY    -> Gemini de Google, offre gratuite (quelques dizaines d'analyses par jour)
#   ANTHROPIC_API_KEY -> Claude, payant à l'usage (quelques dollars par mois)
# Si les deux sont présentes, Claude est utilisé. Sans clé, rien n'est appelé.
IA_MODELE = "claude-sonnet-5"
GEMINI_MODELE = "auto"           # "auto" = le meilleur modèle Flash disponible sur ton compte, sinon Flash-Lite
IA_MAX_JOUR = 15                 # plafond d'analyses par jour, pour rester dans le quota gratuit de Gemini
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
UA_NAVIGATEUR = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
# Depuis les serveurs de GitHub, la FRED et les sites de la Fed laissent en attente les requêtes qui se présentent
# comme un navigateur, mais répondent aussitôt à un client de type script. On essaie donc les deux, dans cet ordre.
UA_SCRIPT = "curl/8.5.0"
AGENTS_OFFICIELS = (UA_SCRIPT, UA_NAVIGATEUR)
SESSION.headers.update({"Accept-Language": "en-US,en;q=0.9"})

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


def http_get(url, params=None, ttl_min=0, timeout=20, agents=(UA_NAVIGATEUR,), valider=None):
    """GET avec cache disque et plusieurs identités de client. Si la source tombe, on ressert la dernière
    version en cache (et on le signale via AGE_CACHE). valider(texte) peut rejeter une page d'erreur."""
    cle = hashlib.md5((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
    fichier = DOSSIER_CACHE / cle
    if ttl_min and fichier.exists() and time.time() - fichier.stat().st_mtime < ttl_min * 60:
        return fichier.read_text(encoding="utf-8")
    erreur = None
    for ua in agents:
        try:
            r = SESSION.get(url, params=params, timeout=timeout, headers={"User-Agent": ua})
            r.raise_for_status()
            texte = r.text
            if valider and not valider(texte):
                raise RuntimeError("réponse inattendue (page d'erreur ou format changé)")
            DOSSIER_CACHE.mkdir(exist_ok=True)
            fichier.write_text(texte, encoding="utf-8")
            return texte
        except Exception as e:
            erreur = e
    if fichier.exists():  # donnée périmée mais utilisable... si elle passe la même vérification
        ancien = fichier.read_text(encoding="utf-8")
        if valider is None or valider(ancien):
            AGE_CACHE[url] = (time.time() - fichier.stat().st_mtime) / 3600
            return ancien
        try:
            fichier.unlink()  # vieux fichier invalide (page d'erreur enregistrée par une ancienne version)
        except Exception:
            pass
    raise erreur


AGE_CACHE = {}  # url -> âge en heures d'une donnée resservie depuis le cache


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
    ib = extraire_ohlcv(telecharger_yahoo("GC=F", "60d", "15m"), "GC=F")
    if ib.empty:
        ib = extraire_ohlcv(telecharger_yahoo("GC=F", "5d", "15m"), "GC=F")
    jb = extraire_ohlcv(telecharger_yahoo("GC=F", "1y", "1d"), "GC=F")
    hb = extraire_ohlcv(telecharger_yahoo("GC=F", "730d", "1h"), "GC=F")
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
    if not hb.empty:
        hidx = pd.to_datetime(hb.index)
        if hidx.tz is None:
            hidx = hidx.tz_localize("UTC")
        hb.index = hidx.tz_convert(tz) if tz else hidx
    noter("Yahoo intraday", True, f"{len(ib)} bougies 15 min, {len(hb)} bougies horaires")
    return {"barres": ib, "jours": jb, "heures": hb}


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
    while len(contrats) < 5:
        if m > 12:
            m, a = 1, a + 1
        if m in actifs and (date(a, m, 27) - now.date()).days > 3:
            contrats.append((f"GC{CODES_MOIS[m - 1]}{str(a)[2:]}.CMX", a, m))
        m += 1
    close = extraire_close(telecharger_yahoo([c[0] for c in contrats], "5d"), [c[0] for c in contrats])
    out = []
    for t, a, m in contrats:
        if t in close.columns and close[t].dropna().size:
            ech = date(a, m, 27)
            out.append({"libelle": f"{MOIS_FR[m - 1]} {a}", "prix": float(close[t].dropna().iloc[-1]),
                        "echeance": ech, "proche": (ech - now.date()).days <= 35})
    noter("Courbe des futures or", len(out) >= 2, f"{len(out)} échéances" if out else "indisponible")
    return out


def charger_fred_serie(code, depuis):
    txt = http_get("https://fred.stlouisfed.org/graph/fredgraph.csv", {"id": code, "cosd": depuis}, ttl_min=30,
                   timeout=15, agents=AGENTS_OFFICIELS, valider=lambda t: t.lstrip().lower().startswith(("date", "observation")))
    df = pd.read_csv(io.StringIO(txt))
    dates = pd.to_datetime(df[df.columns[0]], errors="coerce")
    vals = pd.to_numeric(df[df.columns[-1]], errors="coerce")
    s = pd.Series(vals.values, index=dates).dropna()
    s.index = normaliser_index(s.index)
    return s


def charger_fred():
    depuis = (datetime.now() - timedelta(days=4 * 365)).strftime("%Y-%m-%d")
    out, erreurs = {}, []
    with cf.ThreadPoolExecutor(max_workers=4) as ex:
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
    url = "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv"
    try:
        txt = http_get(url, ttl_min=120, agents=(UA_NAVIGATEUR, UA_SCRIPT), valider=lambda t: "tonne" in t[:8000].lower())
    except Exception:
        # pour le diagnostic : que renvoie le site exactement ?
        try:
            brut = SESSION.get(url, timeout=20, headers={"User-Agent": UA_NAVIGATEUR})
            debut_txt = re.sub(r"\s+", " ", brut.text[:70])
            raise RuntimeError(f"fichier GLD indisponible (HTTP {brut.status_code}, début : « {debut_txt} »)")
        except RuntimeError:
            raise
        except Exception as e2:
            raise RuntimeError(f"fichier GLD indisponible ({type(e2).__name__})")
    lignes = txt.splitlines()
    debut = next((i for i, l in enumerate(lignes) if "tonne" in l.lower()), None)
    if debut is None:
        raise RuntimeError("fichier GLD : colonne des tonnes introuvable")
    lecteur = csv.reader(lignes[debut:])
    entete = next(lecteur)
    col = next(i for i, h in enumerate(entete) if "tonne" in h.lower())
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


def lire_rss(url, params=None, ttl_min=10, n=7, agents=(UA_NAVIGATEUR,)):
    tz = tz_local()
    txt = http_get(url, params, ttl_min=ttl_min, agents=agents, valider=lambda t: "<rss" in t[:2000] or "<?xml" in t[:200])
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
            out[cle] = lire_rss(url, ttl_min=30, n=6, agents=AGENTS_OFFICIELS)
        except Exception:
            out[cle] = []
    if any(out.values()):
        noter("Réserve fédérale (RSS)", True)
        return out
    # Secours : les mêmes informations via Google News
    for cle, q in (("communiques", "Federal Reserve statement OR FOMC decision"),
                   ("discours", "Fed governor OR Fed chair speech")):
        try:
            out[cle] = lire_rss("https://news.google.com/rss/search",
                                {"q": q + " when:7d", "hl": "en-US", "gl": "US", "ceid": "US:en"}, n=6)
        except Exception:
            out[cle] = []
    noter("Réserve fédérale (RSS)", any(out.values()), "via Google News (flux officiel indisponible)"
          if any(out.values()) else "flux officiel et secours indisponibles")
    return out


def _csv_tresor(annee, typ):
    url = (f"https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
           f"{annee}/all?type={typ}&field_tdr_date_value={annee}&page&_format=csv")
    txt = http_get(url, ttl_min=60, agents=AGENTS_OFFICIELS, valider=lambda t: t.lstrip().lower().startswith("date"))
    df = pd.read_csv(io.StringIO(txt))
    df.index = normaliser_index(pd.DatetimeIndex(pd.to_datetime(df[df.columns[0]], errors="coerce", format="%m/%d/%Y")))
    df = df[df.index.notna()]
    df.columns = [str(c).strip().lower() for c in df.columns]
    return df.sort_index()


def charger_tresor():
    """Courbes des taux nominaux et réels publiées par le Trésor américain (secours de la FRED)."""
    annee = maintenant().year
    out = {}
    for typ, champs in (("daily_treasury_yield_curve", {"tr_3m": "3 mo", "tr_2a": "2 yr", "tr_5a": "5 yr",
                                                         "tr_10a": "10 yr", "tr_30a": "30 yr"}),
                        ("daily_treasury_real_yield_curve", {"tr_reel5": "5 yr", "tr_reel10": "10 yr",
                                                              "tr_reel30": "30 yr"})):
        morceaux = []
        for an in (annee - 1, annee):
            try:
                morceaux.append(_csv_tresor(an, typ))
            except Exception:
                pass
        if not morceaux:
            continue
        df = pd.concat(morceaux).sort_index()
        df = df[~df.index.duplicated(keep="last")]
        for cle, col in champs.items():
            if col in df.columns:
                s = pd.to_numeric(df[col], errors="coerce").dropna()
                if len(s):
                    out[cle] = s
    noter("Trésor américain (courbes)", bool(out), f"{len(out)} maturités" if out else "indisponible")
    return out


def charger_nyfed():
    """Taux EFFR (avec la fourchette cible de la Fed) et SOFR, API publique de la Fed de New York."""
    out = {}
    for cle, url in (("effr", "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/500.json"),
                     ("sofr", "https://markets.newyorkfed.org/api/rates/secured/sofr/last/500.json")):
        try:
            lignes = json.loads(http_get(url, ttl_min=60, agents=AGENTS_OFFICIELS)).get("refRates", [])
        except Exception:
            continue
        dates = normaliser_index(pd.to_datetime([l.get("effectiveDate") for l in lignes], errors="coerce"))
        out[cle] = pd.Series(pd.to_numeric([l.get("percentRate") for l in lignes], errors="coerce"),
                             index=dates).dropna().sort_index()
        if cle == "effr":
            for champ, nom in (("targetRateFrom", "cible_bas"), ("targetRateTo", "cible_haut")):
                s = pd.Series(pd.to_numeric([l.get(champ) for l in lignes], errors="coerce"), index=dates)
                if s.notna().any():
                    out[nom] = s.dropna().sort_index()
    noter("Fed de New York (EFFR, SOFR)", bool(out), "" if out else "indisponible")
    return out


def charger_adjudications():
    """Adjudications de dette américaine : calendrier à venir et résultats récents (TreasuryDirect)."""
    base = "https://www.treasurydirect.gov/TA_WS/securities/"
    ok_json = lambda t: t.lstrip().startswith("[")
    avenir = json.loads(http_get(base + "upcoming?format=json", ttl_min=120, agents=AGENTS_OFFICIELS, valider=ok_json))
    try:
        passees = json.loads(http_get(base + "auctioned?format=json&days=400", ttl_min=120,
                                      agents=AGENTS_OFFICIELS, valider=ok_json))
    except Exception:
        passees = []
    noter("TreasuryDirect (adjudications)", True, f"{len(avenir)} à venir")
    return {"avenir": avenir, "passees": passees}


def completer_taux(data):
    """Complète les séries FRED manquantes avec le Trésor et la Fed de New York."""
    f, tr, ny = data["fred"], data.get("tresor") or {}, data.get("nyfed") or {}
    rempl = []
    for cle, src in (("reel5", tr.get("tr_reel5")), ("reel10", tr.get("tr_reel10")), ("reel30", tr.get("tr_reel30")),
                     ("us2", tr.get("tr_2a")), ("effr", ny.get("effr")), ("sofr", ny.get("sofr")),
                     ("cible_bas", ny.get("cible_bas")), ("cible_haut", ny.get("cible_haut"))):
        if cle not in f and src is not None and len(src):
            f[cle] = src
            rempl.append(cle)
    for cle, nom, reel in (("be5", "tr_5a", "tr_reel5"), ("be10", "tr_10a", "tr_reel10")):
        if cle not in f and tr.get(nom) is not None and tr.get(reel) is not None:
            be = (tr[nom] - tr[reel]).dropna()
            if len(be):
                f[cle] = be
                rempl.append(cle)
    if "pente2s10s" not in f and tr.get("tr_2a") is not None and tr.get("tr_10a") is not None:
        f["pente2s10s"] = (tr["tr_10a"] - tr["tr_2a"]).dropna()
        rempl.append("pente2s10s")
    if "pente3m10a" not in f and tr.get("tr_3m") is not None and tr.get("tr_10a") is not None:
        f["pente3m10a"] = (tr["tr_10a"] - tr["tr_3m"]).dropna()
        rempl.append("pente3m10a")
    if rempl:
        st = STATUT.get("FRED (Fed de St. Louis)", {})
        noter("FRED (Fed de St. Louis)", bool(f), (st.get("message", "") + " ; " if st.get("message") else "") +
              "complété par le Trésor et la Fed de New York : " + ", ".join(rempl))


XAUS_SPOT = "https://xaus.com/api/v1/spot"
XAUS_INTRADAY = "https://xaus.com/api/v1/intraday"


def charger_spot_xaus():
    """Spot XAU/USD (XAUS, gratuit, sans clé) et son historique échantillonné toutes les 2 minutes. Sert de prix de
    référence unique : la page lit la même source en direct."""
    j = json.loads(http_get(XAUS_SPOT, {"compact": "1"}, ttl_min=0, agents=(UA_NAVIGATEUR, UA_SCRIPT),
                            valider=lambda t: "spot_usd_oz" in t))
    spot = float(j["spot_usd_oz"])
    etat = (j.get("data_state") or {}).get("status", "fresh")
    quand = (j.get("data_state") or {}).get("as_of") or j.get("price_as_of") or j.get("updated_at")
    serie = None
    try:
        ji = json.loads(http_get(XAUS_INTRADAY, {"symbol": "xau", "hours": "48"}, ttl_min=0,
                                 agents=(UA_NAVIGATEUR, UA_SCRIPT), valider=lambda t: "points" in t))
        pts = ji.get("points") or []
        if pts:
            idx = pd.to_datetime([p_.get("t") for p_ in pts], utc=True, errors="coerce")
            serie = pd.Series([float(p_.get("p")) for p_ in pts], index=idx).dropna().sort_index()
            tz = tz_local()
            if tz:
                serie.index = serie.index.tz_convert(tz)
    except Exception:
        serie = None
    noter("XAUS (spot XAU/USD)", etat != "unavailable", f"spot {nb(spot, 2)} ({etat})"
          + (f", {len(serie)} points intraday" if serie is not None else ""))
    return {"spot": spot, "quand": quand, "etat": etat, "serie": serie}


def charger_oanda():
    """Carnet d'ordres et carnet de positions des clients OANDA sur XAU/USD (jeton démo gratuit)."""
    jeton = os.environ.get("OANDA_TOKEN", "").strip()
    if not jeton:
        return None
    erreurs = []
    for hote in OANDA_HOTES:
        out = {}
        try:
            for nom in ("orderBook", "positionBook"):
                r = SESSION.get(f"{hote}/v3/instruments/{OANDA_INSTRUMENT}/{nom}", timeout=20,
                                headers={"Authorization": f"Bearer {jeton}", "Accept-Datetime-Format": "RFC3339",
                                         "User-Agent": UA_SCRIPT})
                if r.status_code in (401, 403):
                    raise PermissionError(f"jeton refusé (HTTP {r.status_code})")
                r.raise_for_status()
                livre = r.json().get(nom) or {}
                if not livre.get("buckets"):
                    raise RuntimeError(f"{nom} vide")
                out[nom] = livre
            t = out["orderBook"].get("time", "")
            noter("OANDA (carnets d'ordres et de positions)", True,
                  ("compte démo" if "practice" in hote else "compte réel") + (f", instantané {t[11:16]} UTC" if t else ""))
            return out
        except Exception as e:
            erreurs.append(f"{'démo' if 'practice' in hote else 'réel'} : {str(e)[:60]}")
    raise RuntimeError(" ; ".join(erreurs))


def charger_intermarche():
    """Bougies 15 min de l'or, du dollar, du taux 10 ans et de l'argent (5 jours), heure de Paris."""
    if yf is None:
        return None
    tick = ["GC=F", "DX-Y.NYB", "^TNX", "SI=F", "2YY=F", "ZT=F"]
    df = telecharger_yahoo(tick, "5d", "15m")
    c = _niveau(df, "Close") if df is not None and len(df) else None
    if c is None or c.empty:
        noter("Yahoo intermarché 15 min", False, "indisponible")
        return None
    idx = pd.to_datetime(c.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    tz = tz_local()
    c = c.copy()
    c.index = idx.tz_convert(tz) if tz else idx
    if "^TNX" in c and c["^TNX"].dropna().size and c["^TNX"].dropna().iloc[-1] > 20:
        c["^TNX"] = c["^TNX"] / 10
    # US 2 ans en % : le future micro cote le taux ; à défaut, on déduit la variation du prix du T-Note 2 ans
    if "2YY=F" in c and c["2YY=F"].dropna().size and 0 < c["2YY=F"].dropna().iloc[-1] < 20:
        c["US2Y"] = c["2YY=F"]
    elif "ZT=F" in c and c["ZT=F"].dropna().size:
        zt = c["ZT=F"]
        c["US2Y"] = 4.0 - (zt / zt.dropna().iloc[-1] - 1) * 100 / 1.9  # duration ~1,9 : 1 % de prix ≈ 53 pb
    noter("Yahoo intermarché 15 min", True, ", ".join(t for t in tick if t in c and c[t].dropna().size))
    return c


def gld_par_yahoo(data):
    """Secours si le fichier SPDR est bloqué : actifs du fonds GLD (Yahoo) convertis en tonnes, un relevé par jour,
    conservés dans l'historique. Les flux sur 5 jours apparaissent après une semaine de relevés."""
    hist = data.setdefault("hist", {})
    snaps = hist.setdefault("gld_snap", {})
    prix = derniere((data.get("yahoo") or {}).get("or"))
    if yf is not None and prix:
        try:
            with VERROU_YAHOO:
                actifs = yf.Ticker("GLD").info.get("totalAssets")
            if actifs:
                snaps[maintenant().date().isoformat()] = round(float(actifs) / prix / ONCES_PAR_TONNE, 2)
        except Exception:
            pass
    if len(snaps) > 60:
        for k in sorted(snaps)[:-60]:
            del snaps[k]
    s = pd.Series(snaps, dtype=float)
    if s.empty:
        return None
    s.index = normaliser_index(pd.to_datetime(s.index))
    s = s.sort_index()
    if len(s) >= 4 and s.tail(4).nunique() == 1:
        noter("SPDR Gold Shares (GLD)", False, "secours Yahoo figé (valeur identique depuis 4 jours) : ignoré")
        return None
    noter("SPDR Gold Shares (GLD)", True, f"estimé via Yahoo (actifs du fonds), {len(s)} relevé(s) quotidien(s) : "
          f"flux disponibles après 6 relevés" if len(s) < 6 else f"estimé via Yahoo (actifs du fonds), {len(s)} relevés")
    return s


def collecter():
    """Lance toutes les collectes en parallèle. Une source en panne n'empêche pas les autres."""
    data = {"yahoo": {}, "fred": {}, "cot": None, "gld": None, "cal": [], "news": {}, "zq": {},
            "intraday": None, "courbe": [], "fed_off": {}, "tresor": {}, "nyfed": {}, "adjudic": None,
            "oanda": None, "intermarche": None, "xaus": None}
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
        "tresor": (charger_tresor, "Trésor américain (courbes)"),
        "nyfed": (charger_nyfed, "Fed de New York (EFFR, SOFR)"),
        "adjudic": (charger_adjudications, "TreasuryDirect (adjudications)"),
        "oanda": (charger_oanda, "OANDA (carnets d'ordres et de positions)"),
        "xaus": (charger_spot_xaus, "XAUS (spot XAU/USD)"),
        "intermarche": (charger_intermarche, "Yahoo intermarché 15 min"),
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
    completer_taux(data)
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
        pvv = profil_volume(bv)
        if pvv:
            res["profil_veille"] = pvv
            niveaux += [("POC de la veille", pvv["poc"]), ("VAH de la veille", pvv["vah"]), ("VAL de la veille", pvv["val"])]

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
    lundi = auj - timedelta(days=auj.weekday())
    res["profil_semaine"] = profil_volume(b[(b.index.date >= lundi)])
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
    liquides = [c for c in courbe or [] if not c.get("proche")]
    courbe = liquides if len(liquides) >= 2 else courbe
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
    histo = ""
    ta = type_annonce(titre)
    r = next((x for x in a.get("reactions") or [] if x["type"] == ta), None)
    if r:
        histo = (f"Historique ({r['n']} publications) : amplitude médiane de {nb(r['med_rng'], 0)} $ dans l'heure, "
                 f"premier mouvement retourné dans {nb(r['retour_pct'], 0)} % des cas.")
    return {"titre": titre, "nom": nom, "type": typ, "pourquoi": pourquoi, "haut": haut, "bas": bas,
            "nuances": nuances, "date": e["date"], "prevision": e["prevision"], "precedent": e["precedent"],
            "amplitude": a.get("range_jour"), "histo": histo}


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
# ANALYSE v4 : ÉCART FUTURE-SPOT, TENDANCE, RISQUE, PROFILS, VUE DE MARCHÉ
# ---------------------------------------------------------------------------

def tz_ny():
    try:
        return ZoneInfo("America/New_York") if ZoneInfo else None
    except Exception:
        return None


def ecart_future_spot(prix_ref, courbe, sofr):
    """Écart entre le future suivi par Yahoo et l'or spot, estimé par le coût de portage de la courbe."""
    if not prix_ref or not courbe:
        return None
    c = min(courbe, key=lambda x: abs(x["prix"] - prix_ref))
    if abs(c["prix"] - prix_ref) > prix_ref * 0.005:
        return None
    liquides = [x for x in courbe if not x.get("proche")]
    if len(liquides) >= 2 and (liquides[1]["echeance"] - liquides[0]["echeance"]).days > 0:
        a, b = liquides[0], liquides[1]
        taux = (b["prix"] / a["prix"]) ** (365 / (b["echeance"] - a["echeance"]).days) - 1
    elif sofr:
        taux = sofr / 100
    else:
        return None
    jours = max((c["echeance"] - maintenant().date()).days, 0)
    base = prix_ref - prix_ref / (1 + taux) ** (jours / 365)
    if not 0 <= base < prix_ref * 0.03:
        return None
    return {"base": base, "contrat": c["libelle"], "taux": taux * 100, "jours": jours}


def _ohlc(df, regle):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
    if "volume" in df:
        agg["volume"] = "sum"
    return df.resample(regle).agg(agg).dropna(subset=["close"])


def _tendance(df, nom):
    if df is None or len(df) < 60 or not {"high", "low", "close"} <= set(df.columns):
        return None
    df = df.tail(400)
    c = df["close"].astype(float)
    e20, e50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    pente = float(e20.iloc[-1] - e20.iloc[-6])
    h, l = df["high"].values[-150:], df["low"].values[-150:]
    hauts = [h[i] for i in range(2, len(h) - 2) if h[i] == max(h[i - 2:i + 3])][-2:]
    creux = [l[i] for i in range(2, len(l) - 2) if l[i] == min(l[i - 2:i + 3])][-2:]
    struct = 0
    if len(hauts) == 2 and len(creux) == 2:
        if hauts[1] > hauts[0] and creux[1] > creux[0]:
            struct = 1
        elif hauts[1] < hauts[0] and creux[1] < creux[0]:
            struct = -1
    px = float(c.iloc[-1])
    ema = 1 if (px > e20.iloc[-1] > e50.iloc[-1] and pente > 0) else (
        -1 if (px < e20.iloc[-1] < e50.iloc[-1] and pente < 0) else 0)
    score = ema + struct
    label = {2: "Haussière", 1: "Haussière, fragile", 0: "Range", -1: "Baissière, fragile", -2: "Baissière"}[score]
    det = []
    det.append("prix au-dessus des moyennes 20 et 50" if ema > 0 else
               ("prix sous les moyennes 20 et 50" if ema < 0 else "moyennes sans ordre net"))
    det.append({1: "sommets et creux ascendants", -1: "sommets et creux descendants",
                0: "structure sans direction"}[struct])
    return {"nom": nom, "score": score, "sens": (score > 0) - (score < 0), "label": label, "ema20": float(e20.iloc[-1]),
            "ema50": float(e50.iloc[-1]), "detail": ", ".join(det)}


def matrice_tendance(intra):
    if not intra or intra.get("barres") is None or intra["barres"].empty:
        return []
    b, hb, jb = intra["barres"], intra.get("heures"), intra.get("jours")
    h1 = hb if hb is not None and len(hb) > 100 else _ohlc(b, "1h")
    out = [_tendance(b, "15 min"), _tendance(h1, "1 heure"), _tendance(_ohlc(h1, "4h"), "4 heures"),
           _tendance(jb, "Journalier")]
    return [t for t in out if t]


def jauge_risque(a, f):
    """Appétit pour le risque : actions, volatilité, crédit, yen, cuivre (z-scores sur 5 jours)."""
    z = {m["cle"]: m["z5"] for m in a["mouvements"] if m["z5"] is not None}
    comp = []
    for cle, signe, lib in (("spx", 1, "Actions"), ("vix", -1, "VIX"), ("usdjpy", 1, "Yen (USD/JPY)"),
                            ("move", -1, "Volatilité obligataire")):
        if cle in z:
            comp.append((lib, clamp(z[cle] * signe, -3, 3)))
    hy = f.get("hy")
    if hy is not None and len(hy.dropna()) > 70:
        ch = hy.dropna().diff() * 100
        sig = float(ch.iloc[-61:-1].std()) or None
        d5 = variation(hy, 5, "pb")
        if sig and d5 is not None:
            comp.append(("Crédit high yield", clamp(-d5 / (sig * math.sqrt(5)), -3, 3)))
    if not comp:
        return None
    score = sum(v for _, v in comp) / len(comp) * 33
    score = clamp(score, -100, 100)
    etat = "Risk-on" if score > 25 else ("Risk-off" if score < -25 else "Neutre")
    lecture = {"Risk-on": "Les investisseurs prennent du risque : la demande refuge pour l'or est faible, il dépend "
                          "surtout des taux et du dollar.",
               "Risk-off": "Les investisseurs cherchent la sécurité : soutien pour l'or, sauf si la chute des marchés "
                           "force des ventes pour lever des liquidités.",
               "Neutre": "Pas de mouvement de fond sur l'appétit pour le risque."}[etat]
    return {"score": score, "etat": etat, "lecture": lecture, "composantes": comp}


def profil_volatilite(intra, horizon_h=HORIZON_H):
    """Amplitude moyenne de l'or par tranche de 15 min (heure de Paris) et volatilité attendue sur l'horizon."""
    if not intra or intra.get("barres") is None or len(intra["barres"]) < 400:
        return None
    b = intra["barres"]
    b = b[b.index.dayofweek < 5]
    auj = b.index[-1].date()
    hist = b[b.index.date < auj]
    slot = lambda idx: idx.hour * 4 + idx.minute // 15
    rng = (hist["high"] - hist["low"]).groupby(slot(hist.index)).mean()
    ret = hist["close"].pct_change()
    var = ret.groupby(slot(hist.index)).var()
    bj = b[b.index.date == auj]
    auj_rng = (bj["high"] - bj["low"]).groupby(slot(bj.index)).mean()
    commun = [s_ for s_ in auj_rng.index if s_ in rng.index]
    ratio = float(auj_rng[commun].mean() / rng[commun].mean()) if commun and rng[commun].mean() else None
    now = maintenant()
    s0 = now.hour * 4 + now.minute // 15
    prochains = [(s0 + k) % 96 for k in range(1, horizon_h * 4 + 1)]
    v = sum(float(var.get(k, 0) or 0) for k in prochains)
    px = float(b["close"].iloc[-1])
    sigma = px * math.sqrt(v) if v > 0 else None
    moy_jour = float(rng.mean()) if len(rng) else None
    return {"moy": rng.reindex(range(96)), "auj": auj_rng.reindex(range(96)), "ratio": ratio, "sigma": sigma,
            "slot": s0, "moy_slot": float(rng.get(s0, float("nan"))) if len(rng) else None, "moy_jour": moy_jour,
            "jours": len(set(hist.index.date))}


def profil_volume(barres, pas=1.0):
    """Profil de volume approximatif (volume réparti sur la plage de chaque bougie) : POC et zone de valeur 70 %."""
    if barres is None or len(barres) < 8 or "volume" not in barres or float(barres["volume"].sum()) <= 0:
        return None
    lo, hi = float(barres["low"].min()), float(barres["high"].max())
    n = int((hi - lo) / pas) + 1
    if n < 3 or n > 5000:
        return None
    vol = np.zeros(n)
    for h, l, v in zip(barres["high"].values, barres["low"].values, barres["volume"].values):
        i0, i1 = int((l - lo) / pas), int((h - lo) / pas)
        vol[i0:i1 + 1] += v / (i1 - i0 + 1)
    poc = int(vol.argmax())
    tot, cible = vol.sum(), vol.sum() * 0.7
    b_, h_, acc = poc, poc, vol[poc]
    while acc < cible and (b_ > 0 or h_ < n - 1):
        bas_v = vol[b_ - 1] if b_ > 0 else -1
        haut_v = vol[h_ + 1] if h_ < n - 1 else -1
        if haut_v >= bas_v:
            h_ += 1
            acc += vol[h_]
        else:
            b_ -= 1
            acc += vol[b_]
    return {"poc": lo + poc * pas + pas / 2, "vah": lo + h_ * pas + pas, "val": lo + b_ * pas, "total": float(tot)}


def niveaux_autour(a, prix):
    s = a.get("seance")
    if not s:
        return [], []
    niv = [(n["lib"], n["cfd"]) for n in s["niveaux"]]
    dessus = sorted([n for n in niv if n[1] > prix + 0.05], key=lambda x: x[1])
    dessous = sorted([n for n in niv if n[1] < prix - 0.05], key=lambda x: -x[1])
    return dessus, dessous


def vue_marche(a, data):
    """La vue de marché : direction, conviction, scénarios à quelques heures, sentiment et catalyseurs."""
    s = a.get("seance")
    b = a["biais"]
    comp = {}
    comp["fondamental"] = (b["total"] / b["n"]) if b["n"] else 0.0
    poids_tf = {"15 min": 0.15, "1 heure": 0.30, "4 heures": 0.35, "Journalier": 0.20}
    mt = a.get("matrice") or []
    tot_p = sum(poids_tf[t["nom"]] for t in mt) or 1
    comp["tendance"] = sum(poids_tf[t["nom"]] * t["score"] / 2 for t in mt) / tot_p if mt else 0.0
    if a.get("technique"):  # score technique complet : tendance, momentum multi-horizons, structure, position au VWAP
        comp["tendance"] = a["technique"]["score"]
    mom = 0.0
    if s:
        if s.get("vwap"):
            mom += 0.5 if s["prix"] > s["vwap"] else -0.5
        if s.get("var_jour") is not None:
            mom += 0.5 * clamp(s["var_jour"] / 0.5, -1, 1)
    comp["momentum"] = mom

    sent, sent_det = 0.0, []
    c = a.get("cot")
    if c:
        if c["pct3a"] is not None and c["pct3a"] >= SEUILS["cot_haut"]:
            sent -= 0.4
            sent_det.append(("Fonds (COT)", f"très chargés à l'achat (percentile {nb(c['pct3a'], 0)})", "down"))
        elif c["pct3a"] is not None and c["pct3a"] <= SEUILS["cot_bas"]:
            sent += 0.4
            sent_det.append(("Fonds (COT)", f"peu exposés (percentile {nb(c['pct3a'], 0)}) : marge pour racheter", "up"))
        if c.get("lecture"):
            nom_l = c["lecture"][0]
            v = {"Nouveaux achats des fonds": 0.3, "Achats sur repli": 0.3, "Rachats de ventes à découvert": 0.15,
                 "Rachats de ventes malgré la baisse": 0.1, "Liquidation de positions acheteuses": -0.15,
                 "Allègement malgré la hausse": -0.15, "Nouvelles ventes à découvert": -0.3,
                 "Vendeurs contre la hausse": -0.3}.get(nom_l, 0)
            sent += v
            sent_det.append(("Flux des fonds", nom_l.lower(), "up" if v > 0 else ("down" if v < 0 else "flat")))
    g = a.get("gld")
    if g and g.get("d5") is not None:
        v = 0.4 if g["d5"] >= 3 else (-0.4 if g["d5"] <= -3 else 0)
        sent += v
        sent_det.append(("ETF or", f"{nb(g['d5'], 1, True)} t sur 5 jours", "up" if v > 0 else ("down" if v < 0 else "flat")))
    br = a.get("barometre")
    if br:
        fed_net = br["hawk"] - br["dove"]
        geo_net = br["esc"] - br["desc"]
        v = -0.1 * clamp(fed_net, -3, 3) + effet_petrole() * 0.07 * clamp(geo_net, -3, 3)
        sent += v
        ton = "Fed dure" if fed_net > 0 else ("Fed souple" if fed_net < 0 else "Fed neutre")
        geo = "escalade" if geo_net > 0 else ("détente" if geo_net < 0 else "géopolitique calme")
        sent_det.append(("Titres de presse", f"{ton}, {geo}", "up" if v > 0.05 else ("down" if v < -0.05 else "flat")))
    dc = a.get("decomp")
    if dc and abs(dc["semaine"]["residu"]) >= 0.5:
        v = 0.3 if dc["semaine"]["residu"] > 0 else -0.3
        sent += v
        sent_det.append(("Demande de fond", ("l'or fait mieux que ce que la macro explique" if v > 0 else
                                            "l'or fait moins bien que ce que la macro explique") + " sur 5 jours",
                         "up" if v > 0 else "down"))
    cn = a.get("carnets")
    if cn and cn.get("part_acheteurs") is not None:
        pa = cn["part_acheteurs"]
        v = -0.35 if pa >= 65 else (0.35 if pa <= 35 else 0)
        sent += v
        sent_det.append(("Foule (OANDA)", f"{nb(pa, 0)} % d'acheteurs" + (" : contrarien" if v else ""),
                         "up" if v > 0 else ("down" if v < 0 else "flat")))
    comp["sentiment"] = clamp(sent, -1, 1)
    im = a.get("intermarche")
    if im:
        l4 = next((l for l in im["lignes"] if l["lib"].startswith("4")), im["lignes"][-1])
        if l4["signal"] != "neutre":
            comp["momentum"] = clamp(comp["momentum"] + (0.3 if l4["signal"] == "force" else -0.3), -1, 1)
            sent_det.append(("Intermarché", ("force" if l4["signal"] == "force" else "faiblesse") + " relative sur 4 h",
                             "up" if l4["signal"] == "force" else "down"))

    score = sum(POIDS_VUE[k] * comp[k] for k in POIDS_VUE)
    sens = 1 if score >= 0.2 else (-1 if score <= -0.2 else 0)
    niveaux_conv = ["faible", "moyenne", "forte"]
    ic = 2 if abs(score) >= 0.55 else (1 if abs(score) >= 0.35 else 0)
    alertes = []
    if abs(comp["fondamental"]) >= 0.2 and abs(comp["tendance"]) >= 0.2 and comp["fondamental"] * comp["tendance"] < 0:
        ic = max(0, ic - 1)
        alertes.append("fondamental et tendance se contredisent")
    now = maintenant()
    proche = [e for e in data.get("cal") or [] if e["impact"] == "High" and now <= e["date"] <= now + timedelta(hours=HORIZON_H)]
    if proche:
        ic = max(0, ic - 1)
        alertes.append(f"{proche[0]['titre']} à {proche[0]['date'].hour:02d}:{proche[0]['date'].minute:02d} "
                       f"peut tout changer")
    conviction = niveaux_conv[ic] if sens else "faible"
    direction = {1: "Haussière", -1: "Baissière", 0: "Neutre"}[sens]

    prix = (s["prix"] + AJUST) if s else ((a["or"]["prix"] or 0) + AJUST)
    pv = a.get("profil_vol")
    sigma = pv["sigma"] if pv and pv.get("sigma") else (
        (s["atr"] * math.sqrt(HORIZON_H / 23)) if s and s.get("atr") else None)
    fourchette = (prix - sigma, prix + sigma) if sigma else None
    dessus, dessous = niveaux_autour(a, prix)

    def article(lib):
        speciaux = {"Pivot": "le pivot", "VWAP du jour": "le VWAP du jour", "Chiffre rond": "le chiffre rond",
                    "Ouverture du mois": "l'ouverture du mois", "Clôture de la veille": "la clôture de la veille",
                    "POC de la veille": "le POC de la veille",
                    "VAH de la veille": "le haut de la zone de valeur de la veille",
                    "VAL de la veille": "le bas de la zone de valeur de la veille",
                    "Stops acheteurs (liquidité)": "la poche de stops acheteurs",
                    "Stops vendeurs (liquidité)": "la poche de stops vendeurs",
                    "Mur d'ordres vendeurs": "le mur d'ordres vendeurs", "Mur d'ordres acheteurs": "le mur d'ordres acheteurs",
                    "Acheteurs piégés": "la zone des acheteurs piégés", "Vendeurs piégés": "la zone des vendeurs piégés"}
        if lib in speciaux:
            return speciaux[lib]
        if lib[:1] in "RS" and lib[1:].isdigit():
            return ("la résistance " if lib[0] == "R" else "le support ") + lib
        return "le " + lib[0].lower() + lib[1:]

    def de(x):
        return ("du " + x[3:]) if x.startswith("le ") else ("de " + x)


    def lieu(n, prep=""):
        txt = article(n[0])
        if prep == "de":
            txt = de(txt)
        elif prep:
            txt = prep + " " + txt
        return f"{txt} ({nb(n[1], 1)})"

    ecart_min = max((sigma or 0) * 0.3, (s["atr"] * 0.08) if s and s.get("atr") else 0.5)

    def espaces(liste, depart):
        """Niveaux successifs suffisamment écartés les uns des autres (et du prix)."""
        out, ref = [], depart
        for n in liste:
            if abs(n[1] - ref) >= ecart_min:
                out.append(n)
                ref = n[1]
        return out

    haut_e, bas_e = espaces(dessus, prix), espaces(dessous, prix)
    inv_bas = bas_e[0] if bas_e else None
    inv_haut = haut_e[0] if haut_e else None
    portee = (sigma or 1e9) * 1.4
    if sens > 0:
        cibles = [n for n in haut_e if n[1] - prix <= portee][:2] or haut_e[:1]
        central = (f"Tant que le prix tient au-dessus {lieu(inv_bas, 'de')}, le scénario privilégié est une progression vers "
                   + " puis vers ".join(lieu(n) for n in cibles) + ".") if inv_bas and cibles else \
            "Scénario privilégié : poursuite de la hausse, sans niveau technique proche pour la freiner."
        suivant = bas_e[1] if len(bas_e) > 1 else None
        alternatif = (f"Sous {lieu(inv_bas)}, la lecture haussière ne tient plus : repli probable vers "
                      + (lieu(suivant) if suivant else "le bas de la fourchette") + ".") if inv_bas else ""
        invalidation = inv_bas
    elif sens < 0:
        cibles = [n for n in bas_e if prix - n[1] <= portee][:2] or bas_e[:1]
        central = (f"Tant que le prix reste sous {lieu(inv_haut)}, le scénario privilégié est un repli vers "
                   + " puis vers ".join(lieu(n) for n in cibles) + ".") if inv_haut and cibles else \
            "Scénario privilégié : poursuite de la baisse, sans niveau technique proche pour la freiner."
        suivant = haut_e[1] if len(haut_e) > 1 else None
        alternatif = (f"Au-dessus {lieu(inv_haut, 'de')}, la lecture baissière ne tient plus : rebond probable vers "
                      + (lieu(suivant) if suivant else "le haut de la fourchette") + ".") if inv_haut else ""
        invalidation = inv_haut
    else:
        central = ("Pas de direction claire : range probable "
                   + (f"entre {nb(fourchette[0], 0)} et {nb(fourchette[1], 0)}" if fourchette else "autour du prix actuel")
                   + ". Les bornes sont des zones de réaction plutôt que de cassure.")
        alternatif = (f"Une sortie franche au-dessus {lieu(inv_haut, 'de')} ou sous {lieu(inv_bas)} donnerait la direction."
                      if inv_haut and inv_bas else "")
        invalidation = None

    pourquoi = []
    f_txt = {"Haussier": "le fondamental pousse à la hausse", "Baissier": "le fondamental pèse à la baisse",
             "Neutre": "le fondamental est neutre"}[b["verdict"]]
    pourquoi.append(f"{f_txt} ({nb(b['total'], 0, True) if b['total'] else '0'} sur {b['n']} signaux)")
    if mt:
        hausse = [t["nom"] for t in mt if t["sens"] > 0]
        baisse = [t["nom"] for t in mt if t["sens"] < 0]
        morceaux = []
        if hausse:
            morceaux.append("tendance haussière en " + ", ".join(h.lower() for h in hausse))
        if baisse:
            morceaux.append("tendance baissière en " + ", ".join(h.lower() for h in baisse))
        pourquoi.append(", ".join(morceaux) if morceaux else "pas de tendance sur les unités de temps suivies")
    if s and s.get("vwap"):
        pourquoi.append("prix " + ("au-dessus" if s["prix"] > s["vwap"] else "en dessous") + " du VWAP du jour")

    catal = []
    for e in data.get("cal") or []:
        if e["date"] >= now and e["date"] <= now + timedelta(hours=36) and (
                e["impact"] == "High" or e["date"] <= now + timedelta(hours=HORIZON_H)):
            catal.append(f"{date_fr(e['date'], True)} : {e['titre']}")
    fw = a.get("fedwatch")
    if fw and fw["reunions"] and (fw["reunions"][0]["date"] - now.date()).days <= 7:
        catal.append(f"Décision de la Fed le {date_fr(fw['reunions'][0]['date'])}")
    for adj in (a.get("adjudic") or {}).get("avenir", [])[:6]:
        if now.date() <= adj["date"].date() <= (now + timedelta(days=2)).date():
            catal.append(f"{date_fr(adj['date'], True)} : adjudication {adj['terme']}")

    return {"score": score, "composantes": comp, "direction": direction, "sens": sens, "conviction": conviction,
            "alertes": alertes, "prix": prix, "sigma": sigma, "fourchette": fourchette, "central": central,
            "alternatif": alternatif, "invalidation": invalidation, "pourquoi": pourquoi, "sentiment": sent_det,
            "catalyseurs": catal[:6]}


# ---------------------------------------------------------------------------
# SUIVI DES PRÉVISIONS ET RÉACTIONS AUX ANNONCES (mémoire entre deux mises à jour)
# ---------------------------------------------------------------------------

FICHIER_HIST = "historique.json"


def charger_historique():
    f = DOSSIER_CACHE / FICHIER_HIST
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            pass
    depot = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in depot:  # secours : la copie publiée avec le site
        proprio, nom = depot.split("/", 1)
        try:
            return json.loads(http_get(f"https://{proprio.lower()}.github.io/{nom}/{FICHIER_HIST}", timeout=15))
        except Exception:
            pass
    return {"previsions": [], "annonces": []}


def sauver_historique(hist, site_dir=None):
    hist["previsions"] = hist.get("previsions", [])[-3000:]
    hist["annonces"] = hist.get("annonces", [])[-400:]
    txt = json.dumps(hist, ensure_ascii=False)
    try:
        DOSSIER_CACHE.mkdir(exist_ok=True)
        (DOSSIER_CACHE / FICHIER_HIST).write_text(txt, encoding="utf-8")
        if site_dir is not None:
            (site_dir / FICHIER_HIST).write_text(txt, encoding="utf-8")
    except Exception:
        pass


def suivre_previsions(hist, a, data):
    """Enregistre la vue du moment (au plus une par heure, marché ouvert) et évalue celles arrivées à échéance."""
    intra = data.get("intraday")
    vue = a.get("vue")
    prev = hist.setdefault("previsions", [])
    if not intra or intra.get("barres") is None or intra["barres"].empty or not vue:
        return
    b = intra["barres"]
    now = maintenant()
    dernier = b.index[-1]
    ouvert = (now - dernier.to_pydatetime()).total_seconds() < 3 * 3600
    if ouvert and vue.get("sigma"):
        der_ts = datetime.fromisoformat(prev[-1]["ts"]) if prev else None
        if der_ts is None or (dernier.to_pydatetime() - der_ts).total_seconds() >= 55 * 60:
            prev.append({"ts": dernier.isoformat(), "prix": float(b["close"].iloc[-1]), "sens": vue["sens"],
                         "conv": vue["conviction"], "score": round(vue["score"], 3), "sigma": round(vue["sigma"], 2)})
    for p in prev:
        if "res" in p:
            continue
        t0 = datetime.fromisoformat(p["ts"])
        cible = t0 + timedelta(hours=HORIZON_H)
        if cible > dernier.to_pydatetime():
            continue
        apres = b[b.index >= pd.Timestamp(cible)]
        if apres.empty or (apres.index[0].to_pydatetime() - cible).total_seconds() > 2 * 3600:
            p["res"] = "na"  # échéance tombée pendant une fermeture du marché
            continue
        mv = float(apres["close"].iloc[0]) - p["prix"]
        p["mv"] = round(mv, 2)
        p["dans"] = abs(mv) <= p["sigma"]
        p["ok"] = (mv * p["sens"] > 0) if p["sens"] else (abs(mv) <= 0.5 * p["sigma"])
        p["res"] = "ok" if p["ok"] else "ko"


def stats_previsions(hist, jours=30):
    lim = maintenant() - timedelta(days=jours)
    ev = [p for p in hist.get("previsions", []) if p.get("res") in ("ok", "ko")
          and datetime.fromisoformat(p["ts"]) >= lim]
    dirs = [p for p in ev if p["sens"]]
    res = {"n": len(ev), "n_dir": len(dirs), "taux_dir": (sum(p["ok"] for p in dirs) / len(dirs) * 100) if dirs else None,
           "taux_fourchette": (sum(p["dans"] for p in ev) / len(ev) * 100) if ev else None, "par_conv": [],
           "derniers": [p for p in hist.get("previsions", []) if "res" in p][-12:],
           "en_attente": sum(1 for p in hist.get("previsions", []) if "res" not in p)}
    for cv in ("forte", "moyenne", "faible"):
        sub = [p for p in dirs if p["conv"] == cv]
        if sub:
            res["par_conv"].append((cv, len(sub), sum(p["ok"] for p in sub) / len(sub) * 100))
    neutres = [p for p in ev if not p["sens"]]
    res["n_neutre"] = len(neutres)
    res["taux_neutre"] = (sum(p["ok"] for p in neutres) / len(neutres) * 100) if neutres else None
    return res


def type_annonce(titre):
    for motif, nom in ((r"federal funds rate|fomc statement|rate decision", "Décision de la Fed"),
                       (r"core pce|pce price", "Inflation PCE"), (r"\bcpi\b", "Inflation CPI"),
                       (r"non-farm|nfp", "Créations d'emplois (NFP)"), (r"\bppi\b", "Prix à la production"),
                       (r"ism manufacturing", "ISM manufacturier"), (r"ism services", "ISM services"),
                       (r"retail sales", "Ventes au détail"), (r"unemployment claims", "Inscriptions au chômage"),
                       (r"\bgdp\b", "PIB")):
        if re.search(motif, titre, re.I):
            return nom
    return None


def memoriser_annonces(hist, cal):
    connues = {(x["t"], x["type"]) for x in hist.setdefault("annonces", [])}
    now = maintenant()
    for e in cal or []:
        typ = type_annonce(e["titre"])
        if typ and e["impact"] == "High" and e["date"] < now:
            cle = (e["date"].isoformat(), typ)
            if cle not in connues:
                hist["annonces"].append({"t": e["date"].isoformat(), "type": typ, "reel": e["reel"],
                                         "prev": e["prevision"]})
                connues.add(cle)


def reactions_annonces(intra, hist):
    """Mouvement de l'or autour des annonces passées : 15 min et 1 h après, amplitude, retournements."""
    if not intra:
        return []
    b, hb = intra.get("barres"), intra.get("heures")
    tz, tzn = tz_local(), tz_ny()
    evts = []
    for d in FOMC_PASSES:
        try:
            t = datetime.combine(date.fromisoformat(d), datetime.min.time()).replace(hour=14, tzinfo=tzn)
            evts.append(("Décision de la Fed", t.astimezone(tz) if tz else t))
        except Exception:
            pass
    for x in hist.get("annonces", []):
        try:
            evts.append((x["type"], datetime.fromisoformat(x["t"])))
        except Exception:
            pass
    lignes = {}
    for typ, t in evts:
        ts = pd.Timestamp(t)
        mesure = None
        if b is not None and len(b) and b.index[0] <= ts - pd.Timedelta(minutes=15) and b.index[-1] >= ts + pd.Timedelta(minutes=60):
            av = b[b.index < ts]
            ap = b[(b.index >= ts) & (b.index < ts + pd.Timedelta(minutes=60))]
            if len(av) and len(ap) >= 3:
                pre = float(av["close"].iloc[-1])
                m15, m60 = float(ap["close"].iloc[0]) - pre, float(ap["close"].iloc[-1]) - pre
                mesure = (m15, m60, float(ap["high"].max() - ap["low"].min()), "15 min")
        elif hb is not None and len(hb) and hb.index[0] <= ts - pd.Timedelta(hours=1) and hb.index[-1] >= ts + pd.Timedelta(hours=1):
            h0 = ts.floor("h")
            av = hb[hb.index < h0]
            ap = hb[(hb.index >= h0) & (hb.index < h0 + pd.Timedelta(hours=2))]
            if len(av) and len(ap) >= 2:
                pre = float(av["close"].iloc[-1])
                mesure = (float(ap["close"].iloc[0]) - pre, float(ap["close"].iloc[-1]) - pre,
                          float(ap["high"].max() - ap["low"].min()), "1 h")
        if mesure:
            lignes.setdefault(typ, []).append((t, *mesure))
    out = []
    for typ, l in lignes.items():
        l.sort(key=lambda x: x[0])
        m60 = [abs(x[2]) for x in l]
        rng = [x[3] for x in l]
        retour = [1 for x in l if x[1] * x[2] < 0 and abs(x[1]) > 1]
        out.append({"type": typ, "n": len(l), "med_mv": float(np.median(m60)), "med_rng": float(np.median(rng)),
                    "max_rng": float(max(rng)), "retour_pct": len(retour) / len(l) * 100,
                    "hausse_pct": sum(1 for x in l if x[2] > 0) / len(l) * 100,
                    "precision": "15 min" if all(x[4] == "15 min" for x in l) else "horaire",
                    "dernier": l[-1]})
    out.sort(key=lambda x: -x["n"])
    return out


def terme_lisible(terme):
    """'9-Year 10-Month' (réouverture) devient '10 ans (réouverture)'."""
    m = re.match(r"(\d+)-Year(?:\s+(\d+)-Month)?", terme or "")
    if not m:
        return (terme or "").replace("-Month", " mois")
    ans, mois = int(m.group(1)), int(m.group(2) or 0)
    return f"{ans + 1} ans (réouverture)" if mois >= 9 else f"{ans} ans"


def analyser_adjudications(adj):
    if not adj:
        return None
    tz, tzn = tz_local(), tz_ny()

    def quand(x):
        try:
            d = datetime.fromisoformat(str(x.get("auctionDate", ""))[:19])
            m = re.match(r"(\d+):(\d+)\s*(AM|PM)", str(x.get("closingTimeCompetitive") or "01:00 PM"))
            h, mi = (int(m.group(1)) % 12 + (12 if m.group(3) == "PM" else 0), int(m.group(2))) if m else (13, 0)
            d = d.replace(hour=h, minute=mi, tzinfo=tzn)
            return d.astimezone(tz) if tz else d
        except Exception:
            return None

    garder = lambda x: x.get("securityType") in ("Note", "Bond") and x.get("securityTerm")
    avenir = []
    for x in adj.get("avenir") or []:
        if garder(x) and quand(x):
            mt = pd.to_numeric(x.get("offeringAmount"), errors="coerce")
            avenir.append({"date": quand(x), "terme": f"obligation {terme_lisible(x['securityTerm'])}",
                           "montant": (float(mt) / 1e9) if mt == mt else None,
                           "reouv": str(x.get("reopening", "")).lower() == "yes"})
    avenir.sort(key=lambda x: x["date"])
    passees = []
    hist = [x for x in adj.get("passees") or [] if garder(x) and pd.notna(pd.to_numeric(x.get("bidToCoverRatio"), errors="coerce"))]
    hist.sort(key=lambda x: str(x.get("auctionDate")))
    for i, x in enumerate(hist):
        d = quand(x)
        if not d or d < maintenant() - timedelta(days=45):
            continue
        terme = x["securityTerm"]
        prec = [pd.to_numeric(y.get("bidToCoverRatio"), errors="coerce") for y in hist[:i] if y["securityTerm"] == terme][-6:]
        btc = float(pd.to_numeric(x.get("bidToCoverRatio"), errors="coerce"))
        moy = float(np.nanmean(prec)) if prec else None
        ind = pd.to_numeric(x.get("indirectBidderAccepted"), errors="coerce")
        tot = pd.to_numeric(x.get("totalAccepted") or x.get("offeringAmount"), errors="coerce")
        passees.append({"date": d, "terme": terme_lisible(terme),
                        "rendement": pd.to_numeric(x.get("highYield"), errors="coerce"), "btc": btc, "btc_moy": moy,
                        "indirect": float(ind / tot * 100) if ind == ind and tot and tot == tot else None,
                        "qualite": (None if moy is None else ("faible" if btc < moy - 0.1 else
                                                              ("solide" if btc > moy + 0.1 else "normale")))})
    passees.sort(key=lambda x: x["date"], reverse=True)
    return {"avenir": avenir[:8], "passees": passees[:8]}


# ---------------------------------------------------------------------------
# CARNETS OANDA ET CONFIRMATION INTERMARCHÉ
# ---------------------------------------------------------------------------

def _seaux(livre):
    try:
        px = float(livre.get("price"))
        lignes = [(float(b["price"]), float(b["longCountPercent"]), float(b["shortCountPercent"]))
                  for b in livre.get("buckets", [])]
        return px, lignes
    except Exception:
        return None, []


def _zones(lignes, largeur, cote, champ, prix):
    """Regroupe les seaux en zones de 'largeur' $ du côté demandé ('dessus' ou 'dessous') et les classe."""
    z = {}
    for p, lg, sh in lignes:
        if (cote == "dessus" and p <= prix) or (cote == "dessous" and p >= prix):
            continue
        cle = math.floor(p / largeur)
        z[cle] = z.get(cle, 0.0) + (lg if champ == "long" else sh)
    out = [{"prix": (k + 0.5) * largeur, "poids": v} for k, v in z.items() if v > 0]
    return sorted(out, key=lambda x: -x["poids"])


def analyser_carnets(oanda, atr):
    """Lecture du carnet d'ordres (où sont les stops et les ordres en attente) et du carnet de positions (où la
    foule est entrée, qui est piégé). Prix en XAU/USD spot, comme ton broker."""
    if not oanda:
        return None
    px_o, ordres = _seaux(oanda.get("orderBook") or {})
    px_p, positions = _seaux(oanda.get("positionBook") or {})
    if not px_o or not ordres or not positions:
        return None
    fen = min(2.5 * atr, px_o * 0.03) if atr else px_o * 0.02
    ordres = [x for x in ordres if abs(x[0] - px_o) <= fen]
    pos_f = [x for x in positions if abs(x[0] - px_p) <= fen]
    largeur = max(2.0, round((atr or px_o * 0.01) * 0.06, 1))
    res = {"prix": px_o, "fenetre": fen, "largeur": largeur, "temps": (oanda.get("orderBook") or {}).get("time", "")}

    def notables(liste):
        if not liste:
            return []
        moy = sum(x["poids"] for x in liste) / len(liste)
        return [dict(x, force=x["poids"] / moy if moy else 0) for x in liste[:3] if moy and x["poids"] >= 1.6 * moy]

    # Au-dessus du prix : ordres d'achat = stops acheteurs (cassures, stops des vendeurs) ; ordres de vente = ventes limites
    # En dessous : ordres de vente = stops vendeurs (stops des acheteurs) ; ordres d'achat = achats limites
    res["stops_acheteurs"] = notables(_zones(ordres, largeur, "dessus", "long", px_o))
    res["stops_vendeurs"] = notables(_zones(ordres, largeur, "dessous", "short", px_o))
    res["murs_vente"] = notables(_zones(ordres, largeur, "dessus", "short", px_o))
    res["murs_achat"] = notables(_zones(ordres, largeur, "dessous", "long", px_o))

    tl, ts = sum(x[1] for x in positions), sum(x[2] for x in positions)
    res["part_acheteurs"] = tl / (tl + ts) * 100 if tl + ts else None
    res["longs_pieges"] = (sum(x[1] for x in positions if x[0] > px_p) / tl * 100) if tl else None
    res["shorts_pieges"] = (sum(x[2] for x in positions if x[0] < px_p) / ts * 100) if ts else None
    res["entree_longs"] = sum(x[0] * x[1] for x in positions) / tl if tl else None
    res["entree_shorts"] = sum(x[0] * x[2] for x in positions) / ts if ts else None
    pl = _zones(pos_f, largeur, "dessus", "long", px_p)
    ps = _zones(pos_f, largeur, "dessous", "short", px_p)
    res["zone_longs_pieges"] = pl[0] if pl else None
    res["zone_shorts_pieges"] = ps[0] if ps else None
    pa = res["part_acheteurs"]
    if pa is None:
        res["lecture"] = ""
    elif pa >= 65:
        res["lecture"] = (f"La foule est très acheteuse ({nb(pa, 0)} %) : signal contrarien baissier, les achats "
                          f"manquent de carburant et une purge des acheteurs est possible.")
    elif pa <= 35:
        res["lecture"] = (f"La foule est très vendeuse ({nb(100 - pa, 0)} % de vendeurs) : signal contrarien haussier, "
                          f"un rachat des vendeurs peut accélérer la hausse.")
    else:
        res["lecture"] = f"Positionnement de la foule équilibré ({nb(pa, 0)} % d'acheteurs) : pas de signal contrarien."
    res["histo_ordres"] = ordres
    res["histo_positions"] = pos_f
    return res


def niveaux_carnets(c):
    """Niveaux issus du carnet, en prix spot."""
    if not c:
        return []
    out = []
    for cle, lib in (("stops_acheteurs", "Stops acheteurs (liquidité)"), ("stops_vendeurs", "Stops vendeurs (liquidité)"),
                     ("murs_vente", "Mur d'ordres vendeurs"), ("murs_achat", "Mur d'ordres acheteurs")):
        for z in c.get(cle, [])[:2]:
            out.append((lib, z["prix"]))
    if c.get("zone_longs_pieges"):
        out.append(("Acheteurs piégés", c["zone_longs_pieges"]["prix"]))
    if c.get("zone_shorts_pieges"):
        out.append(("Vendeurs piégés", c["zone_shorts_pieges"]["prix"]))
    return out


def confirmation_intermarche(im, decomp):
    """L'or suit-il ce que le dollar et les taux lui dictent ? Mouvement attendu (bêtas sur 60 jours) contre réel."""
    if im is None or "GC=F" not in im or not decomp:
        return None
    g = im["GC=F"].dropna()
    if len(g) < 20:
        return None
    t1 = g.index[-1]
    betas = decomp["betas"]
    out = []
    for lib, n, seuil in (("Dernière heure", 4, 0.10), ("4 dernières heures", 16, 0.20)):
        t0 = t1 - pd.Timedelta(minutes=15 * n)
        if g.index[0] > t0:
            continue
        g0, g1 = float(g.asof(t0)), float(g.iloc[-1])
        ligne = {"lib": lib, "or": (g1 / g0 - 1) * 100}
        attendu, parts = 0.0, []
        for col, cle, mode in (("DX-Y.NYB", "dxy", "pct"), ("^TNX", "us10", "pb")):
            if col in im and im[col].dropna().size:
                s = im[col].dropna()
                if s.index[-1] < t1 - pd.Timedelta(minutes=45) or s.index[0] > t0:
                    ligne[cle] = None
                    continue
                a0, a1 = float(s.asof(t0)), float(s.asof(t1))
                v = (a1 / a0 - 1) * 100 if mode == "pct" else (a1 - a0) * 100
                ligne[cle] = v
                attendu += betas.get(cle, 0) * v
                parts.append(cle)
            else:
                ligne[cle] = None
        if "SI=F" in im and im["SI=F"].dropna().size:
            s = im["SI=F"].dropna()
            ligne["argent"] = (float(s.asof(t1)) / float(s.asof(t0)) - 1) * 100 if s.index[0] <= t0 else None
        else:
            ligne["argent"] = None
        if not parts:
            continue
        ligne["attendu"], ligne["residu"] = attendu, ligne["or"] - attendu
        ligne["signal"] = ("force" if ligne["residu"] >= seuil else ("faiblesse" if ligne["residu"] <= -seuil else "neutre"))
        morceaux = []
        if ligne.get("dxy") is not None:
            morceaux.append(f"dollar {nb(ligne['dxy'], 2, True)} %")
        if ligne.get("us10") is not None:
            morceaux.append(f"10 ans {nb(ligne['us10'], 1, True)} pb")
        txt = (f"{lib} : {', '.join(morceaux)}, la macro poussait l'or de {nb(attendu, 2, True)} % ; il a fait "
               f"{nb(ligne['or'], 2, True)} %.")
        txt += {"force": " Force relative : l'or monte plus (ou baisse moins) que ce que la macro explique, des acheteurs sont là.",
                "faiblesse": " Faiblesse relative : l'or ne profite pas de la macro, des vendeurs pèsent.",
                "neutre": " L'or suit la macro."}[ligne["signal"]]
        if ligne.get("argent") is not None and abs(ligne["or"]) > 0.1 and ligne["argent"] * ligne["or"] < 0:
            txt += " L'argent part dans l'autre sens : mouvement de l'or moins solide."
        ligne["texte"] = txt
        out.append(ligne)
    return {"lignes": out, "maj": t1} if out else None


# ---------------------------------------------------------------------------
# ANALYSE TECHNIQUE PROFESSIONNELLE ET PLAYBOOK TECHNICO-FONDAMENTAL
# ---------------------------------------------------------------------------
# Ce qui est retenu (effets documentés et niveaux réellement surveillés par les desks) :
#   - tendance et momentum multi-horizons (time-series momentum), structure de marché (sommets / creux, cassures)
#   - régime : tendance ou range (ratio d'efficacité), compression de volatilité (largeur de Bollinger, NR7)
#   - niveaux d'enchères : VWAP et ses bandes, profil de volume, veille, semaine, chiffres ronds, liquidité
#   - timing : range asiatique (cassure à Londres), fixings de Londres (10 h 30 et 15 h, heure de Londres)
# Les indicateurs classiques (RSI) servent de contexte d'extension, pas de signal d'entrée.

def rsi(c, n=14):
    d = c.diff()
    hausse = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    baisse = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + hausse / baisse.replace(0, np.nan))


def efficacite(c, n=20):
    """Ratio d'efficacité de Kaufman : 1 = mouvement en ligne droite (tendance), proche de 0 = aller-retour (range)."""
    c = c.dropna()
    if len(c) <= n:
        return None
    chemin = float(c.diff().abs().tail(n).sum())
    return abs(float(c.iloc[-1] - c.iloc[-1 - n])) / chemin if chemin else None


def pct_largeur_bollinger(c, n=20, histo=120):
    """Percentile de la largeur des bandes de Bollinger : bas = volatilité comprimée, expansion probable."""
    c = c.dropna()
    if len(c) < n + histo:
        return None
    m, sd = c.rolling(n).mean(), c.rolling(n).std()
    bw = (4 * sd / m).dropna().tail(histo)
    return percentile(bw, float(bw.iloc[-1])) if len(bw) > 20 else None


def sommets_creux(df, k=2, n=200):
    d = df.tail(n)
    h, l, t = d["high"].values, d["low"].values, d.index
    hauts = [(t[i], float(h[i])) for i in range(k, len(h) - k) if h[i] == max(h[i - k:i + k + 1])]
    bas = [(t[i], float(l[i])) for i in range(k, len(l) - k) if l[i] == min(l[i - k:i + k + 1])]
    return hauts, bas


# Par unité : (bougies de chaque côté pour valider un sommet ou un creux, mouvement minimal en amplitudes moyennes de bougie)
PARAMS_STRUCTURE = {"15 min": (3, 0.6), "1 heure": (2, 1.0), "4 heures": (2, 1.0)}


def swings_etiquetes(df, k=2, seuil=None, n=300, mult=0.5):
    """Sommets et creux significatifs, alternés, étiquetés HH / LH (sommets) et HL / LL (creux).
    Un mouvement plus petit que 'seuil' (en $) est considéré comme du bruit et ignoré."""
    if df is None or len(df) < 2 * k + 5:
        return []
    d = df.tail(n)
    if seuil is None:
        seuil = mult * float((d["high"] - d["low"]).tail(14).mean())
    hauts, bas = sommets_creux(d, k, n)
    pts = sorted([(t, p, "H") for t, p in hauts] + [(t, p, "L") for t, p in bas], key=lambda x: x[0])
    alt = []
    for t, p, typ in pts:
        if alt and alt[-1][2] == typ:  # deux sommets (ou deux creux) de suite : on garde le plus extrême
            if (typ == "H" and p > alt[-1][1]) or (typ == "L" and p < alt[-1][1]):
                alt[-1] = (t, p, typ)
            continue
        if alt and abs(p - alt[-1][1]) < seuil:
            continue
        alt.append((t, p, typ))
    out, der_h, der_l = [], None, None
    for t, p, typ in alt:
        if typ == "H":
            lab = None if der_h is None or abs(p - der_h) < 1e-6 else ("HH" if p > der_h else "LH")
            der_h = p
        else:
            lab = None if der_l is None or abs(p - der_l) < 1e-6 else ("HL" if p > der_l else "LL")
            der_l = p
        out.append({"t": t, "prix": p, "type": typ, "label": lab})
    return out


ETATS_STRUCTURE = {
    ("HH", "HL"): (1, "Haussière", "sommets et creux de plus en plus hauts"),
    ("LH", "LL"): (-1, "Baissière", "sommets et creux de plus en plus bas"),
    ("HH", "LL"): (0, "Élargissement", "sommet plus haut et creux plus bas : volatilité sans direction"),
    ("LH", "HL"): (0, "Compression", "sommet plus bas et creux plus haut : triangle, cassure à venir"),
}


def structure_tf(df, nom):
    """Structure de marché : dernière séquence HH / HL / LH / LL, cassures BOS (continuation) et CHoCH (retournement)."""
    if df is None or len(df) < 40 or not {"high", "low", "close"} <= set(df.columns):
        return None
    k, mult = PARAMS_STRUCTURE.get(nom, (2, 1.0))
    sw = swings_etiquetes(df, k, mult=mult)
    hs = [x for x in sw if x["type"] == "H"]
    ls = [x for x in sw if x["type"] == "L"]
    if len(hs) < 2 or len(ls) < 2:
        return None
    lh_, ll_ = hs[-1]["label"], ls[-1]["label"]
    sens, etat, detail = ETATS_STRUCTURE.get((lh_, ll_), (0, "Indécise", "structure incomplète"))
    c = float(df["close"].iloc[-1])
    evt = None
    if c > hs[-1]["prix"]:
        evt = ("CHoCH haussier" if sens < 0 else "BOS haussier", 1)
    elif c < ls[-1]["prix"]:
        evt = ("CHoCH baissier" if sens > 0 else "BOS baissier", -1)
    return {"nom": nom, "tendance": sens, "etat": etat, "detail": detail, "sommet": hs[-1]["prix"], "creux": ls[-1]["prix"],
            "label_sommet": lh_, "label_creux": ll_, "sequence": [x for x in sw if x["label"]][-6:],
            "swings": sw[-40:], "evenement": evt[0] if evt else None, "sens_evt": evt[1] if evt else 0}


def bandes_vwap(bj):
    """VWAP du jour et bandes à 1 et 2 écarts-types (pondérés par le volume), référence des desks d'exécution."""
    if bj is None or len(bj) < 3 or "volume" not in bj or float(bj["volume"].sum()) <= 0:
        return None
    tp = (bj["high"] + bj["low"] + bj["close"]) / 3
    v = bj["volume"]
    cv = v.cumsum().replace(0, np.nan)
    vw = (tp * v).cumsum() / cv
    var = ((tp - vw) ** 2 * v).cumsum() / cv
    sd = float(np.sqrt(var.iloc[-1])) if var.notna().any() else None
    if not sd:
        return None
    px, m = float(bj["close"].iloc[-1]), float(vw.iloc[-1])
    return {"vwap": m, "sigma": sd, "z": (px - m) / sd, "bandes": {k: m + k * sd for k in (-2, -1, 1, 2)}}


def heures_fixing(jour):
    """Fixings LBMA de l'or : 10 h 30 et 15 h, heure de Londres, convertis en heure de Paris."""
    try:
        lon, tz = ZoneInfo("Europe/London"), tz_local()
        return [datetime.combine(jour, datetime.min.time()).replace(hour=h, minute=m, tzinfo=lon).astimezone(tz)
                for h, m in ((10, 30), (15, 0))]
    except Exception:
        return []


def analyse_technique(a, data):
    intra = data.get("intraday")
    s = a.get("seance")
    if not intra or intra.get("barres") is None or intra["barres"].empty or not s:
        return None
    b, hb, jb = intra["barres"], intra.get("heures"), intra.get("jours")
    h1 = hb if hb is not None and len(hb) > 100 else _ohlc(b, "1h")
    h4 = _ohlc(h1, "4h")
    atr = s.get("atr") or 1.0
    res = {}

    # 1. Régime : tendance ou range, compression de volatilité
    er1, er4 = efficacite(h1["close"]), efficacite(h4["close"])
    ers = [e for e in (er1, er4) if e is not None]
    er = sum(ers) / len(ers) if ers else None
    bw1 = pct_largeur_bollinger(h1["close"])
    nr7 = inside = False
    if jb is not None and len(jb) > 8:
        jc = jb[jb.index.date < s["date"]].tail(7)
        if len(jc) == 7:
            rng = (jc["high"] - jc["low"])
            nr7 = float(rng.iloc[-1]) <= float(rng.min()) + 1e-9
            inside = bool(jc["high"].iloc[-1] < jc["high"].iloc[-2] and jc["low"].iloc[-1] > jc["low"].iloc[-2])
    compression = (bw1 is not None and bw1 <= 15) or nr7
    regime = "tendance" if er is not None and er >= 0.35 else ("range" if er is not None and er <= 0.2 else "transition")
    res["regime"] = {"nom": regime, "er1": er1, "er4": er4, "bw1": bw1, "nr7": nr7, "inside": inside,
                     "compression": compression}

    # 2. Momentum multi-horizons (effet le mieux documenté) et extension
    or_s = (data.get("yahoo") or {}).get("or")
    mom = []
    if or_s is not None and len(or_s) > 130:
        for n, p in ((5, 0.1), (20, 0.3), (60, 0.3), (120, 0.3)):
            r = variation(or_s, n)
            mom.append({"n": n, "r": r, "poids": p})
    score_mom = sum(m["poids"] * (1 if m["r"] > 0 else -1) for m in mom if m["r"] is not None) if mom else 0.0
    ext = {}
    if or_s is not None and len(or_s) > 200:
        c = float(or_s.iloc[-1])
        datr = float((jb["high"] - jb["low"]).tail(14).mean()) if jb is not None and len(jb) > 14 else atr
        for n in (20, 50, 200):
            ext[n] = (c - float(or_s.tail(n).mean())) / datr if datr else None
    rsis = {}
    for nom, serie in (("1 h", h1["close"]), ("4 h", h4["close"]), ("Jour", or_s)):
        if serie is not None and len(serie.dropna()) > 30:
            rsis[nom] = float(rsi(serie.dropna()).iloc[-1])
    res["momentum"] = {"lignes": mom, "score": score_mom, "extension": ext, "rsi": rsis}

    # 3. Structure de marché
    res["structure"] = [x for x in (structure_tf(b, "15 min"), structure_tf(h1, "1 heure"), structure_tf(h4, "4 heures")) if x]
    res["_h1"], res["_h4"] = h1, h4

    # 4. Niveaux d'enchères du jour : bandes VWAP, range asiatique, fixings
    auj = s["date"]
    bj = b[b.index.date == auj]
    res["vwap"] = bandes_vwap(bj)
    hr = bj.index.hour + bj.index.minute / 60 if len(bj) else []
    asie = bj[(hr < 9)] if len(bj) else bj
    res["asie"] = None
    if len(asie) >= 4:
        ah, al = float(asie["high"].max()), float(asie["low"].min())
        apres = bj[hr >= 9]
        etat = "en cours" if not len(apres) else ("cassé par le haut" if float(apres["high"].max()) > ah and float(apres["close"].iloc[-1]) > ah else
                                                 ("cassé par le bas" if float(apres["low"].min()) < al and float(apres["close"].iloc[-1]) < al else
                                                  ("fausse cassure puis retour dans le range" if float(apres["high"].max()) > ah or float(apres["low"].min()) < al
                                                   else "intact")))
        res["asie"] = {"haut": ah, "bas": al, "etat": etat}
    res["fixings"] = heures_fixing(auj)

    # 5. Score technique (-1 à +1)
    mt = a.get("matrice") or []
    poids_tf = {"15 min": 0.15, "1 heure": 0.30, "4 heures": 0.35, "Journalier": 0.20}
    tot = sum(poids_tf[t["nom"]] for t in mt) or 1
    t_tend = sum(poids_tf[t["nom"]] * t["score"] / 2 for t in mt) / tot if mt else 0.0
    st = {x["nom"]: x for x in res["structure"]}
    t_struct = 0.0
    for nom, p in (("1 heure", 0.5), ("4 heures", 0.5)):
        x = st.get(nom)
        if x:
            t_struct += p * (x["sens_evt"] if x["sens_evt"] else x["tendance"])
    t_pos = clamp(res["vwap"]["z"] / 2, -1, 1) if res["vwap"] else 0.0
    score = 0.35 * t_tend + 0.30 * score_mom + 0.20 * t_struct + 0.15 * t_pos
    res["composantes"] = {"tendance": t_tend, "momentum": score_mom, "structure": t_struct, "position": t_pos}
    res["score"] = score
    res["sens"] = 1 if score >= 0.2 else (-1 if score <= -0.2 else 0)

    # 6. Zones de confluence (en prix affiché, XAU/USD spot)
    poids = {"Plus haut de la veille": 2, "Plus bas de la veille": 2, "Clôture de la veille": 1, "Pivot": 1, "R1": 1,
             "S1": 1, "R2": 1, "S2": 1, "VWAP du jour": 2, "POC de la veille": 2, "VAH de la veille": 1.5,
             "VAL de la veille": 1.5, "Plus haut de la semaine": 2, "Plus bas de la semaine": 2, "Ouverture du mois": 1,
             "Chiffre rond": 1, "Stops acheteurs (liquidité)": 2, "Stops vendeurs (liquidité)": 2,
             "Mur d'ordres vendeurs": 1.5, "Mur d'ordres acheteurs": 1.5, "Acheteurs piégés": 1, "Vendeurs piégés": 1}
    cand = [(n["lib"], n["cfd"], poids.get(n["lib"], 1)) for n in s["niveaux"]]
    if res["vwap"]:
        for k, lib in ((-2, "VWAP -2σ"), (-1, "VWAP -1σ"), (1, "VWAP +1σ"), (2, "VWAP +2σ")):
            cand.append((lib, res["vwap"]["bandes"][k] + AJUST, 1.5 if abs(k) == 2 else 1))
    for t in mt:
        if t["nom"] in ("1 heure", "4 heures"):
            cand.append((f"EMA 20 {t['nom'].replace('heure', 'h').replace('heures', 'h').replace(' hs', ' h')}",
                         t["ema20"] + AJUST, 1))
            cand.append((f"EMA 50 {t['nom'].replace('heure', 'h').replace('heures', 'h').replace(' hs', ' h')}",
                         t["ema50"] + AJUST, 1.5 if t["nom"] == "4 heures" else 1))
    for x in res["structure"]:
        if x["nom"] in ("1 heure", "4 heures"):
            cand.append((f"Sommet {x['nom'].replace(' heures', ' h').replace(' heure', ' h')}", x["sommet"] + AJUST, 2))
            cand.append((f"Creux {x['nom'].replace(' heures', ' h').replace(' heure', ' h')}", x["creux"] + AJUST, 2))
    if res["asie"]:
        cand.append(("Haut du range asiatique", res["asie"]["haut"] + AJUST, 1.5))
        cand.append(("Bas du range asiatique", res["asie"]["bas"] + AJUST, 1.5))
    res["avances"] = concepts_avances(a, data, res)
    cand += [(lib, v + AJUST, pds) for lib, v, pds in niveaux_avances(res["avances"])]
    prix = s["prix"] + AJUST
    tol = max(1.0, 0.06 * atr)             # écart maximal entre deux niveaux voisins d'une même zone
    largeur_max = max(2.0, 0.12 * atr)     # une zone ne dépasse pas 12 % de l'ATR, pour rester exploitable en scalping
    cand = sorted([c_ for c_ in cand if abs(c_[1] - prix) <= 1.6 * atr], key=lambda x: x[1])
    zones, cur = [], []
    for c_ in cand:
        if cur and (c_[1] - cur[-1][1] > tol or c_[1] - cur[0][1] > largeur_max):
            zones.append(cur)
            cur = []
        cur.append(c_)
    if cur:
        zones.append(cur)
    conf = []
    for z in zones:
        sc = sum(x[2] for x in z)
        libs = []
        for x in z:
            if x[0] not in libs:
                libs.append(x[0])
        if sc >= 3 and len(libs) >= 2:
            centre = sum(x[1] * x[2] for x in z) / sc
            conf.append({"bas": min(x[1] for x in z) - tol / 3, "haut": max(x[1] for x in z) + tol / 3, "centre": centre,
                         "score": sc, "elements": libs, "dist": centre - prix})
    conf.sort(key=lambda z: abs(z["dist"]))
    res["confluences"] = conf[:8]
    res["prix"] = prix
    res["tol"] = tol
    return res


# ---------------------------------------------------------------------------
# CONCEPTS AVANCÉS : LIQUIDITÉ, FVG, ORDER BLOCKS, OTE, PREMIUM / DISCOUNT, DÉCLENCHEURS
# ---------------------------------------------------------------------------
# Outils de localisation très utilisés par les traders intraday (approche « smart money »). Leur efficacité n'est pas
# démontrée par la recherche académique : ils servent ici à situer les zones où agir, jamais seuls, et entrent dans les
# zones de confluence avec un poids modéré.

KILL_ZONES = [("Asie", 2.0, 5.0), ("Londres", 8.0, 11.0), ("New York", 13.5, 16.0)]  # heure de Paris


def fvg_liste(df, n=160, min_taille=None, max_nb=6):
    """Fair value gaps : trou entre la mèche de la bougie 1 et celle de la bougie 3, non comblé depuis."""
    if df is None or len(df) < 5:
        return []
    d = df.tail(n)
    h, l, t = d["high"].values, d["low"].values, d.index
    if min_taille is None:
        min_taille = 0.3 * float((d["high"] - d["low"]).mean())
    out = []
    for i in range(2, len(d)):
        if l[i] > h[i - 2]:
            bas_, haut_, sens = float(h[i - 2]), float(l[i]), 1
        elif h[i] < l[i - 2]:
            bas_, haut_, sens = float(h[i]), float(l[i - 2]), -1
        else:
            continue
        if haut_ - bas_ < min_taille:
            continue
        ap_l, ap_h = l[i + 1:], h[i + 1:]
        if sens > 0:
            comble = len(ap_l) and ap_l.min() <= bas_
            partiel = len(ap_l) and ap_l.min() < haut_
        else:
            comble = len(ap_h) and ap_h.max() >= haut_
            partiel = len(ap_h) and ap_h.max() > bas_
        if not comble:
            out.append({"t": t[i - 1], "bas": bas_, "haut": haut_, "sens": sens, "partiel": bool(partiel)})
    return out[-max_nb:]


def jambe_fibo(st):
    """Dernière impulsion entre deux sommets / creux et ses retracements (50, 61,8, 70,5, 78,6 %)."""
    if not st or len(st.get("swings", [])) < 2:
        return None
    a_, b_ = st["swings"][-2], st["swings"][-1]
    if a_["type"] == b_["type"]:
        return None
    depart, arrivee = a_["prix"], b_["prix"]
    sens = 1 if arrivee > depart else -1
    amp = abs(arrivee - depart)
    niv = {r: arrivee - sens * amp * r for r in (0.5, 0.618, 0.705, 0.786)}
    return {"sens": sens, "depart": depart, "arrivee": arrivee, "niveaux": niv,
            "ote": (min(niv[0.618], niv[0.786]), max(niv[0.618], niv[0.786]))}


def egaux(swings, typ, tol, px_actuel):
    """Sommets (ou creux) égaux non cassés : liquidité évidente, les stops s'y accumulent."""
    pts = [x for x in swings if x["type"] == typ][-12:]
    out = []
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            if abs(pts[i]["prix"] - pts[j]["prix"]) <= tol:
                niveau = max(pts[i]["prix"], pts[j]["prix"]) if typ == "H" else min(pts[i]["prix"], pts[j]["prix"])
                intact = (px_actuel < niveau) if typ == "H" else (px_actuel > niveau)
                if intact and not any(abs(niveau - o) <= tol for o in out):
                    out.append(niveau)
    return out[-3:]


def order_blocks(df, st, n=4):
    """Order block : dernière bougie de sens opposé avant l'impulsion qui a cassé la structure, non revisitée depuis."""
    if df is None or not st:
        return []
    sw = st.get("swings", [])
    out = []
    for i in range(1, len(sw)):
        a_, b_ = sw[i - 1], sw[i]
        if b_["label"] not in ("HH", "LL") or a_["type"] == b_["type"]:
            continue
        sens = 1 if b_["label"] == "HH" else -1
        avant = df[df.index <= a_["t"]].tail(4)
        cand = avant[(avant["close"] < avant["open"])] if sens > 0 else avant[(avant["close"] > avant["open"])]
        if cand.empty:
            continue
        ob = cand.iloc[-1]
        bas_, haut_ = float(ob["low"]), float(ob["high"])
        apres = df[df.index > b_["t"]]
        revisite = (apres["low"].min() <= haut_) if sens > 0 else (apres["high"].max() >= bas_) if len(apres) else False
        if not revisite:
            out.append({"t": cand.index[-1], "bas": bas_, "haut": haut_, "sens": sens})
    return out[-n:]


def divergence_rsi(df, st):
    """Divergence : le prix fait un sommet plus haut mais le RSI un sommet plus bas (et inversement sur les creux)."""
    if df is None or not st or len(df) < 40:
        return None
    r = rsi(df["close"])
    hs = [x for x in st["swings"] if x["type"] == "H"][-2:]
    ls = [x for x in st["swings"] if x["type"] == "L"][-2:]
    val = lambda x: float(r.asof(x["t"])) if x["t"] >= r.index[0] else None
    if len(hs) == 2 and hs[1]["prix"] > hs[0]["prix"]:
        a_, b_ = val(hs[0]), val(hs[1])
        if a_ and b_ and b_ < a_ - 2 and hs[1]["t"] >= df.index[-30]:
            return {"sens": -1, "texte": f"divergence baissière (sommet plus haut, RSI {nb(b_, 0)} contre {nb(a_, 0)})"}
    if len(ls) == 2 and ls[1]["prix"] < ls[0]["prix"]:
        a_, b_ = val(ls[0]), val(ls[1])
        if a_ and b_ and b_ > a_ + 2 and ls[1]["t"] >= df.index[-30]:
            return {"sens": 1, "texte": f"divergence haussière (creux plus bas, RSI {nb(b_, 0)} contre {nb(a_, 0)})"}
    return None


def bougie_declencheur(df):
    """Motif sur la dernière bougie 15 min clôturée : avalement ou pin bar (rejet)."""
    if df is None or len(df) < 3:
        return None
    now = maintenant()
    d = df
    if (now - d.index[-1].to_pydatetime()).total_seconds() < 15 * 60:  # dernière bougie encore en cours
        d = d.iloc[:-1]
    p, c = d.iloc[-2], d.iloc[-1]
    corps = abs(c["close"] - c["open"])
    rng = max(float(c["high"] - c["low"]), 1e-9)
    mb, mh = min(c["open"], c["close"]) - c["low"], c["high"] - max(c["open"], c["close"])
    motif = None
    if c["close"] > c["open"] and p["close"] < p["open"] and c["close"] >= p["open"] and c["open"] <= p["close"]:
        motif = ("Avalement haussier", 1)
    elif c["close"] < c["open"] and p["close"] > p["open"] and c["close"] <= p["open"] and c["open"] >= p["close"]:
        motif = ("Avalement baissier", -1)
    elif mb >= 2 * corps and mb >= 0.55 * rng:
        motif = ("Pin bar haussière (rejet par le bas)", 1)
    elif mh >= 2 * corps and mh >= 0.55 * rng:
        motif = ("Pin bar baissière (rejet par le haut)", -1)
    if not motif:
        return None
    return {"nom": motif[0], "sens": motif[1], "t": d.index[-1], "haut": float(c["high"]), "bas": float(c["low"])}


def balayages(df, niveaux, n=8):
    """Prise de liquidité : une bougie 15 min perce par la mèche un niveau où dorment des stops, puis clôture de l'autre
    côté. Au-dessus d'un sommet (H) : liquidité acheteuse prise, rejet baissier. Sous un creux (L) : l'inverse."""
    if df is None or len(df) < n + 2:
        return []
    out = []
    recent = df.tail(n)
    for k, (t, c) in enumerate(recent.iterrows()):
        for lib, v, typ in niveaux:
            if typ == "H" and c["high"] > v and c["close"] < v and c["open"] <= v:
                out.append({"t": t, "lib": lib, "niveau": v, "sens": -1, "age": n - 1 - k})
            elif typ == "L" and c["low"] < v and c["close"] > v and c["open"] >= v:
                out.append({"t": t, "lib": lib, "niveau": v, "sens": 1, "age": n - 1 - k})
    out.sort(key=lambda x: x["age"])
    vus, propres = set(), []
    for x in out:
        if x["lib"] not in vus:
            vus.add(x["lib"])
            propres.append(x)
    return propres[:4]


def concepts_avances(a, data, t):
    """Calcule tous les concepts avancés à partir des bougies (prix en future, convertis à l'affichage)."""
    intra = data.get("intraday")
    s = a.get("seance")
    if not t or not intra or not s:
        return None
    b, jb = intra["barres"], intra.get("jours")
    h1 = t.get("_h1")
    st = {x["nom"]: x for x in t["structure"]}
    atr = s.get("atr") or 1.0
    px = s["prix"]
    res = {}
    res["fvg"] = {"15 min": fvg_liste(b, n=120), "1 heure": fvg_liste(h1, n=120) if h1 is not None else []}
    res["fibo"] = {nom: jambe_fibo(st.get(nom)) for nom in ("15 min", "1 heure")}
    s1 = st.get("1 heure")
    res["pd"] = None
    if s1:
        hi_, lo_ = s1["sommet"], s1["creux"]
        if hi_ > lo_:
            pos = (px - lo_) / (hi_ - lo_) * 100
            res["pd"] = {"haut": hi_, "bas": lo_, "milieu": (hi_ + lo_) / 2, "pos": pos,
                         "zone": "premium" if pos > 55 else ("discount" if pos < 45 else "équilibre")}
    tol = max(0.5, 0.05 * atr)
    sw_all = (st.get("15 min") or {}).get("swings", []) + (s1 or {}).get("swings", [])
    res["egaux_hauts"] = egaux(sw_all, "H", tol, px)
    res["egaux_bas"] = egaux(sw_all, "L", tol, px)
    res["ob"] = {"15 min": order_blocks(b, st.get("15 min")), "1 heure": order_blocks(h1, s1) if h1 is not None else []}
    res["divergences"] = {nom: divergence_rsi(df_, st.get(nom)) for nom, df_ in (("15 min", b), ("1 heure", h1))}
    res["declencheur"] = bougie_declencheur(b)
    # niveaux de la semaine et du mois précédents
    res["periodes"] = {}
    if jb is not None and len(jb) > 40:
        auj = s["date"]
        lundi = auj - timedelta(days=auj.weekday())
        sp = jb[(jb.index.date < lundi) & (jb.index.date >= lundi - timedelta(days=7))]
        if len(sp):
            res["periodes"]["Plus haut de la semaine précédente"] = float(sp["high"].max())
            res["periodes"]["Plus bas de la semaine précédente"] = float(sp["low"].min())
        premier = auj.replace(day=1)
        mp = jb[(jb.index.date < premier) & (jb.index.date >= (premier - timedelta(days=1)).replace(day=1))]
        if len(mp):
            res["periodes"]["Plus haut du mois précédent"] = float(mp["high"].max())
            res["periodes"]["Plus bas du mois précédent"] = float(mp["low"].min())
    # projections d'amplitude journalière (ATR)
    res["adr"] = {"haut": s["bas"] + atr, "bas": s["haut"] - atr, "restant": max(atr - (s["haut"] - s["bas"]), 0)}
    # balayages de liquidité récents sur les niveaux clés
    typ_ = lambda lib: "H" if ("haut" in lib.lower() or "sommet" in lib.lower()) else "L"
    cles = [(n["lib"], n["niveau"], typ_(n["lib"])) for n in s["niveaux"] if n["lib"] in (
        "Plus haut de la veille", "Plus bas de la veille", "Plus haut de la semaine", "Plus bas de la semaine")]
    cles += [(k, v, typ_(k)) for k, v in res["periodes"].items()]
    if t.get("asie"):
        cles += [("Haut du range asiatique", t["asie"]["haut"], "H"), ("Bas du range asiatique", t["asie"]["bas"], "L")]
    s15 = st.get("15 min")
    if s15:
        cles += [("Dernier sommet 15 min", s15["sommet"], "H"), ("Dernier creux 15 min", s15["creux"], "L")]
    cles += [("Sommets égaux", v, "H") for v in res["egaux_hauts"]] + [("Creux égaux", v, "L") for v in res["egaux_bas"]]
    res["balayages"] = balayages(b, cles)
    # kill zones
    now = maintenant()
    hp = now.hour + now.minute / 60
    res["kill_zone"] = next((k for k in KILL_ZONES if k[1] <= hp < k[2]), None) if now.weekday() < 5 else None
    return res


def niveaux_avances(c):
    """Candidats pour les zones de confluence (prix future) : (libellé, niveau, poids)."""
    if not c:
        return []
    out = []
    for nom, poids in (("15 min", 1.0), ("1 heure", 1.5)):
        for f in c["fvg"].get(nom, []):
            out.append((f"FVG {'haussier' if f['sens'] > 0 else 'baissier'} {nom.replace(' heure', ' h')}",
                        (f["bas"] + f["haut"]) / 2, poids))
        for o in c["ob"].get(nom, []):
            out.append((f"Order block {'haussier' if o['sens'] > 0 else 'baissier'} {nom.replace(' heure', ' h')}",
                        (o["bas"] + o["haut"]) / 2, poids))
        fb = c["fibo"].get(nom)
        if fb:
            out.append((f"OTE {nom.replace(' heure', ' h')} (70,5 %)", fb["niveaux"][0.705], 2.0 if nom == "1 heure" else 1.0))
            out.append((f"Fibo {nom.replace(' heure', ' h')} 61,8 %", fb["niveaux"][0.618], 1.0))
            out.append((f"Fibo {nom.replace(' heure', ' h')} 50 %", fb["niveaux"][0.5], 0.5))
    for v in c["egaux_hauts"]:
        out.append(("Sommets égaux (liquidité)", v, 2.0))
    for v in c["egaux_bas"]:
        out.append(("Creux égaux (liquidité)", v, 2.0))
    for k, v in c["periodes"].items():
        out.append((k, v, 2.0))
    out.append(("Projection d'amplitude haute", c["adr"]["haut"], 1.0))
    out.append(("Projection d'amplitude basse", c["adr"]["bas"], 1.0))
    return out


PLAYBOOKS = {
    "achat_replis": ("Acheter les replis", 1,
                     "Le fondamental et la technique vont dans le même sens, à la hausse : c'est la configuration où les "
                     "tendances durent le plus. On n'achète pas la cassure en extension, on attend un repli sur une zone "
                     "de confluence."),
    "vente_rebonds": ("Vendre les rebonds", -1,
                      "Le fondamental et la technique vont dans le même sens, à la baisse. On ne vend pas le plus bas en "
                      "panique, on attend un rebond sur une zone de confluence."),
    "divergence_haussiere": ("Divergence : fondamental porteur, technique baissière", 0,
                             "Le fondamental soutient l'or mais le prix baisse : soit des flux de court terme dominent, "
                             "soit le marché anticipe un changement. Pas de vente de fond ; on attend un changement de "
                             "structure à la hausse (CHoCH 1 h) pour acheter, et les ventes restent des scalps courts."),
    "divergence_baissiere": ("Divergence : fondamental pesant, technique haussière", 0,
                             "Le prix monte contre un fondamental défavorable : hausse fragile, souvent portée par des "
                             "rachats ou de la géopolitique. Pas d'achat de fond ; on attend un changement de structure à "
                             "la baisse (CHoCH 1 h) pour vendre, et les achats restent des scalps courts."),
    "range": ("Jouer le range", 0,
              "Pas de tendance exploitable : le prix fait des allers-retours. On achète le bas de la zone de valeur et on "
              "vend le haut, avec des cibles vers le POC ou le VWAP ; les cassures sans élan échouent souvent."),
    "compression_annonce": ("Attendre la cassure après l'annonce", 0,
                            "La volatilité est comprimée et une annonce forte arrive : l'expansion est probable, mais sa "
                            "direction dépend du chiffre. Pas de position avant ; après la publication, on suit la cassure "
                            "si elle va dans le sens du fondamental et que le retour sur le niveau cassé tient."),
    "fond_seul": ("Positions sélectives dans le sens du fondamental", 0,
                  "Le fondamental a une direction mais la technique ne l'a pas encore confirmée : on ne prend que les "
                  "meilleures zones de confluence dans le sens du fondamental, avec un rejet net et des cibles courtes."),
    "technique_seul": ("Suivre la technique, taille réduite", 0,
                       "La technique a une direction mais le fondamental est neutre : le mouvement manque de carburant de "
                       "fond. On suit la structure avec des cibles courtes et on sort vite si elle casse."),
    "attente": ("Patience", 0,
                "Ni le fondamental ni la technique ne donnent d'avantage net. On ne trade que les zones de confluence "
                "les plus fortes, avec un signal propre, ou on attend."),
}


def playbook(a, data):
    t = a.get("technique")
    if not t:
        return None
    fv = {"Haussier": 1, "Baissier": -1}.get(a["biais"]["verdict"], 0)
    ts = t["sens"]
    now = maintenant()
    evt = next((e for e in data.get("cal") or [] if e["impact"] == "High" and now <= e["date"] <= now + timedelta(hours=3)), None)
    reg = t["regime"]
    if reg["compression"] and evt:
        cas = "compression_annonce"
    elif reg["nom"] == "range" and abs(t["score"]) < 0.35:
        cas = "range"
    elif fv > 0 and ts > 0:
        cas = "achat_replis"
    elif fv < 0 and ts < 0:
        cas = "vente_rebonds"
    elif fv > 0 and ts < 0:
        cas = "divergence_haussiere"
    elif fv < 0 and ts > 0:
        cas = "divergence_baissiere"
    elif fv != 0 and ts == 0:
        cas = "fond_seul"
    elif fv == 0 and abs(t["score"]) >= 0.35:
        cas = "technique_seul"
    else:
        cas = "attente"
    titre, sens, expl = PLAYBOOKS[cas]
    faible = False
    if cas == "technique_seul":
        sens, faible = ts, True
        titre = f"Suivre la technique {'haussière' if ts > 0 else 'baissière'}, taille réduite"
    if cas == "fond_seul":
        sens, faible = fv, True
        titre = "Achats sélectifs : la technique n'a pas encore confirmé" if fv > 0 else \
            "Ventes sélectives : la technique n'a pas encore confirmé"
    prix = t["prix"]
    conf = t["confluences"]
    dessous = sorted([z for z in conf if z["haut"] < prix], key=lambda z: -z["centre"])
    dessus = sorted([z for z in conf if z["bas"] > prix], key=lambda z: z["centre"])
    st = {x["nom"]: x for x in t["structure"]}
    s1 = st.get("1 heure")
    etapes, zones_entree, invalid, cibles = [], [], None, []
    fmt_z = lambda z: f"{nb(z['bas'], 1)}–{nb(z['haut'], 1)} ({', '.join(z['elements'][:3])})"
    pdz = ((t.get("avances") or {}).get("pd") or {})
    if sens > 0:
        zones_entree = dessous[:2]
        cibles = dessus[:2]
        invalid = (s1["creux"] + AJUST) if s1 else None
        etapes = ["Attendre un repli vers " + (fmt_z(zones_entree[0]) if zones_entree else "une zone de confluence") + ".",
                  "Déclencheur : rejet de la zone (mèche, puis clôture 5 ou 15 min qui repart dans le sens de la tendance), "
                  "idéalement après un balayage de liquidité sous la zone.",
                  "Objectifs : " + (", puis ".join(fmt_z(z) for z in cibles) if cibles else "le dernier sommet 1 h") + ".",
                  "Invalidation : clôture 1 h sous le dernier creux 1 h" + (f" ({nb(invalid, 1)})" if invalid else "") + "."]
        if pdz.get("zone") == "premium":
            etapes.insert(1, f"Le prix est en premium ({nb(pdz['pos'], 0)} % du range 1 h) : attendre son retour sous le "
                             f"milieu du range avant d'acheter.")
    elif sens < 0:
        zones_entree = dessus[:2]
        cibles = dessous[:2]
        invalid = (s1["sommet"] + AJUST) if s1 else None
        etapes = ["Attendre un rebond vers " + (fmt_z(zones_entree[0]) if zones_entree else "une zone de confluence") + ".",
                  "Déclencheur : rejet de la zone (mèche, puis clôture 5 ou 15 min qui repart dans le sens de la tendance), "
                  "idéalement après un balayage de liquidité au-dessus de la zone.",
                  "Objectifs : " + (", puis ".join(fmt_z(z) for z in cibles) if cibles else "le dernier creux 1 h") + ".",
                  "Invalidation : clôture 1 h au-dessus du dernier sommet 1 h" + (f" ({nb(invalid, 1)})" if invalid else "") + "."]
        if pdz.get("zone") == "discount":
            etapes.insert(1, f"Le prix est en discount ({nb(pdz['pos'], 0)} % du range 1 h) : attendre son retour au-dessus "
                             f"du milieu du range avant de vendre.")
    elif cas == "range":
        bas_z = dessous[0] if dessous else None
        haut_z = dessus[0] if dessus else None
        etapes = [("Acheter le bas : " + fmt_z(bas_z)) if bas_z else "Pas de zone basse nette.",
                  ("Vendre le haut : " + fmt_z(haut_z)) if haut_z else "Pas de zone haute nette.",
                  "Cibles vers le POC ou le VWAP ; sortie si une clôture 15 min s'installe hors du range."]
    elif cas in ("divergence_haussiere", "divergence_baissiere"):
        niv = s1["sommet"] if (s1 and cas == "divergence_haussiere") else (s1["creux"] if s1 else None)
        etapes = [("Niveau clé : une clôture 1 h au-" + ("dessus" if cas == "divergence_haussiere" else "dessous") +
                   f" de {nb(niv + AJUST, 1)} (dernier {'sommet' if cas == 'divergence_haussiere' else 'creux'} 1 h) "
                   f"réaligne la technique sur le fondamental.") if niv is not None else "Attendre un changement de structure 1 h.",
                  "Avant cela : seulement des scalps courts dans le sens de la technique, sur zones de confluence.",
                  "Après le changement de structure : trades dans le sens du fondamental, sur le premier repli."]
    elif cas == "compression_annonce":
        etapes = [f"Annonce : {evt['titre']} à {evt['date'].hour:02d}:{evt['date'].minute:02d}. Aucune position avant.",
                  "Après la publication : repérer la direction de la cassure du range de la matinée.",
                  "Entrer sur le retour vers le niveau cassé s'il tient, dans le sens du fondamental de préférence."]
    else:
        etapes = ["Ne trader que les zones de confluence au score le plus élevé, avec un rejet net.",
                  "Réduire la taille et viser des cibles courtes."]
    return {"cas": cas, "titre": titre, "sens": sens, "faible": faible, "explication": expl, "etapes": etapes,
            "fond": fv, "tech": ts,
            "zones_entree": zones_entree, "cibles": cibles, "invalidation": invalid, "annonce": evt}


# ---------------------------------------------------------------------------
# VERSION 6 : INTERMARCHÉ, FORCE RELATIVE, RÉGIMES, RÉACTIONS AUX ANNONCES, PLAN DU TRADER
# ---------------------------------------------------------------------------

def _mv(s, t0, t1, mode):
    """Variation d'une série entre deux instants (pct ou points de base)."""
    if s is None:
        return None
    s = s.dropna()
    if not len(s) or s.index[0] > t0 or s.index[-1] < t1 - pd.Timedelta(minutes=45):
        return None
    a0, a1 = float(s.asof(t0)), float(s.asof(t1))
    if a0 != a0 or a1 != a1 or a0 == 0:
        return None
    return (a1 / a0 - 1) * 100 if mode == "pct" else (a1 - a0) * 100


def module_intermarche(a, data):
    """XAU/USD face au dollar, au taux 2 ans et au taux réel 10 ans, sur 1 h, 4 h, 1 jour et 5 jours.
    CONFIRMED : l'or bouge dans le sens que dictent les moteurs. DIVERGENCE : dans le sens inverse. MIXED : moteurs
    partagés ou or sans réaction."""
    im, y, f = data.get("intermarche"), data.get("yahoo") or {}, data.get("fred") or {}
    lignes = []
    seuils = {"1 h": (0.05, 0.05, 1.0, None), "4 h": (0.10, 0.10, 2.0, None),
              "1 jour": (0.20, 0.20, 3.0, 2.0), "5 jours": (0.50, 0.40, 8.0, 5.0)}
    for hz, (s_or, s_dxy, s_2a, s_reel) in seuils.items():
        if hz in ("1 h", "4 h"):
            if im is None or "GC=F" not in im:
                continue
            g = im["GC=F"].dropna()
            if len(g) < 20:
                continue
            t1 = g.index[-1]
            t0 = t1 - pd.Timedelta(hours=1 if hz == "1 h" else 4)
            mv_or = _mv(g, t0, t1, "pct")
            mv_dxy = _mv(im.get("DX-Y.NYB"), t0, t1, "pct")
            mv_2a = _mv(im.get("US2Y"), t0, t1, "pb")
            mv_reel = None
        else:
            n = 1 if hz == "1 jour" else 5
            mv_or, mv_dxy = variation(y.get("or"), n), variation(y.get("dxy"), n)
            mv_2a, mv_reel = variation(f.get("us2"), n, "pb"), variation(f.get("reel10"), n, "pb")
        if mv_or is None:
            continue
        impl = []
        for v, sl in ((mv_dxy, s_dxy), (mv_2a, s_2a), (mv_reel, s_reel)):
            impl.append(0 if (v is None or sl is None or abs(v) < sl) else (-1 if v > 0 else 1))
        actifs = [x for x in impl if x]
        consensus = (actifs[0] if actifs and all(x == actifs[0] for x in actifs) else 0)
        g_sens = 0 if abs(mv_or) < s_or else (1 if mv_or > 0 else -1)
        if consensus and g_sens == consensus:
            statut, lect = "CONFIRMED", ("L'or monte avec un dollar et des taux qui baissent : mouvement sain."
                                         if consensus > 0 else "L'or baisse avec un dollar et des taux qui montent : mouvement sain.")
        elif consensus and g_sens == -consensus:
            statut, lect = "DIVERGENCE", ("Les moteurs poussent l'or à la hausse mais il baisse : vendeurs de fond ou "
                                          "liquidation." if consensus > 0 else
                                          "Les moteurs pèsent sur l'or mais il monte : demande de fond (flux, refuge, "
                                          "banques centrales).")
        elif consensus:
            statut, lect = "MIXED", "Les moteurs donnent une direction mais l'or ne réagit pas encore."
        else:
            statut, lect = "MIXED", ("Moteurs partagés : pas de signal intermarché." if actifs else
                                     "Moteurs sans mouvement notable.")
        lignes.append({"horizon": hz, "or": mv_or, "dxy": mv_dxy, "us2": mv_2a, "reel": mv_reel, "impl": impl,
                       "consensus": consensus, "sens_or": g_sens, "statut": statut, "lecture": lect})
    if not lignes:
        return None
    # statut de synthèse : priorité aux horizons courts pour le trading intraday
    poids = {"1 h": 1, "4 h": 2, "1 jour": 2, "5 jours": 1}
    sc = {"CONFIRMED": 0, "DIVERGENCE": 0, "MIXED": 0}
    for l in lignes:
        sc[l["statut"]] += poids[l["horizon"]]
    global_ = max(sc, key=sc.get)
    sens_g = next((l["consensus"] for l in lignes if l["statut"] == "CONFIRMED"), 0) if global_ == "CONFIRMED" else 0
    return {"lignes": lignes, "statut": global_, "sens": sens_g}


def force_relative(a, data):
    """Gold Relative Strength (-100 à +100) : l'or fait-il mieux ou moins bien que ce que le dollar et les taux
    expliquent ? Écarts au modèle sur 1 h, 4 h (intraday), 1 jour et 5 jours, normalisés par leur volatilité."""
    zs = []
    dc = a.get("decomp")
    if dc and data.get("yahoo", {}).get("or") is not None:
        r = changements(data["yahoo"]["or"], "pct").dropna().tail(60)
        sd = float(r.std()) if len(r) > 20 else None
        if sd:
            zs.append(("1 jour", dc["jour"]["residu"] / sd))
            zs.append(("5 jours", dc["semaine"]["residu"] / (sd * math.sqrt(5))))
    im = a.get("intermarche")
    intra = data.get("intraday")
    if im and intra and intra.get("barres") is not None:
        r15 = intra["barres"]["close"].pct_change().dropna().tail(400) * 100
        sd15 = float(r15.std()) if len(r15) > 50 else None
        for l in im["lignes"]:
            n = 4 if l["lib"].startswith("Dernière") else 16
            if sd15:
                zs.append(("1 h" if n == 4 else "4 h", l["residu"] / (sd15 * math.sqrt(n))))
    if not zs:
        return None
    score = clamp(sum(z for _, z in zs) / len(zs) * 40, -100, 100)
    etat = ("plus forte que prévu" if score > 25 else ("plus faible que prévu" if score < -25 else "en ligne avec le dollar et les taux"))
    return {"score": score, "etat": etat, "composantes": zs}


def regimes_v2(a, data):
    """Six dimensions du marché de l'or, chacune avec son intensité (0-100) et son effet sur l'or."""
    y, f = data.get("yahoo") or {}, data.get("fred") or {}
    out = []
    c = {x["cle"]: x["c20"] for x in a.get("correlations") or []}
    # 1. Taux et dollar
    cd, ct = abs(c.get("dxy") or 0), abs(c.get("us10") or 0)
    intens = clamp(max(cd, ct) * 140, 0, 100)
    d_dxy, d_reel = variation(y.get("dxy"), 5), variation(f.get("reel10"), 5, "pb")
    press = 0
    if d_dxy is not None and d_reel is not None:
        press = -1 if (d_dxy > 0.3 or d_reel > 5) and not (d_dxy < -0.3 or d_reel < -5) else (
            1 if (d_dxy < -0.3 or d_reel < -5) and not (d_dxy > 0.3 or d_reel > 5) else 0)
    out.append({"nom": "Taux et dollar", "intensite": intens, "effet": press,
                "etat": f"corrélation 20 j : dollar {nb(c.get('dxy'), 2, True)}, 10 ans {nb(c.get('us10'), 2, True)} ; "
                        f"5 j : DXY {nb(d_dxy, 2, True)} %, taux réel {nb(d_reel, 0, True)} pb"})
    # 2. Risk-off
    r = a.get("risque")
    if r:
        out.append({"nom": "Appétit pour le risque", "intensite": clamp(abs(r["score"]), 0, 100),
                    "effet": 1 if r["etat"] == "Risk-off" else (-1 if r["etat"] == "Risk-on" else 0),
                    "etat": f"{r['etat']} (score {nb(r['score'], 0, True)})"})
    # 3. Inflation
    d_be, d_br = variation(f.get("be10"), 20, "pb"), variation(y.get("brent"), 20)
    if d_be is not None or d_br is not None:
        sc = (d_be or 0) / 15 + (d_br or 0) / 10
        out.append({"nom": "Inflation", "intensite": clamp(abs(sc) * 50, 0, 100),
                    # choc pétrolier : plus d'inflation = Fed plus dure = négatif pour l'or ; sinon, l'or protège de l'inflation
                    "effet": (0 if abs(sc) < 0.5 else (1 if sc > 0 else -1)) * (-1 if REGIME_PETROLE == "inflation" else 1),
                    "etat": f"inflation anticipée 10 ans {nb(d_be, 0, True)} pb et Brent {nb(d_br, 1, True)} % sur 20 j"
                            + (" : pression inflationniste en hausse" if sc > 0.5 else (" : en reflux" if sc < -0.5 else ""))})
    # 4. Liquidité
    liq = a.get("liquidite")
    if liq and liq.get("d4") is not None:
        out.append({"nom": "Liquidité", "intensite": clamp(abs(liq["d4"]) * 40, 0, 100),
                    "effet": 1 if liq["d4"] >= 1 else (-1 if liq["d4"] <= -1 else 0),
                    "etat": f"liquidité nette {nb(liq['niveau'], 2)} T$, {nb(liq['d4'], 1, True)} % sur 4 semaines"})
    # 5. Stress financier
    hy, nfci, move, vix = derniere(f.get("hy")), derniere(f.get("nfci")), derniere(y.get("move")), derniere(y.get("vix"))
    pts = []
    if hy is not None:
        pts.append(clamp((hy - 3.0) / 3.0, 0, 1))
    if nfci is not None:
        pts.append(clamp((nfci + 0.3) / 0.8, 0, 1))
    if vix is not None:
        pts.append(clamp((vix - 15) / 20, 0, 1))
    if move is not None:
        pts.append(clamp((move - 90) / 60, 0, 1))
    if pts:
        st = sum(pts) / len(pts) * 100
        out.append({"nom": "Stress financier", "intensite": st, "effet": 2 if st >= 50 else 0,
                    "etat": f"spread HY {nb(hy, 2)} %, NFCI {nb(nfci, 2, True)}, VIX {nb(vix, 1)}, MOVE {nb(move, 0)}"
                            + (" : stress élevé, risque de liquidations puis de demande refuge" if st >= 50 else "")})
    # 6. Flux
    sc, morceaux = 0.0, []
    cot = a.get("cot")
    if cot and cot.get("pct3a") is not None:
        sc += 0.5 if cot["pct3a"] <= 20 else (-0.5 if cot["pct3a"] >= 80 else 0)
        morceaux.append(f"fonds percentile {nb(cot['pct3a'], 0)}")
    g = a.get("gld")
    if g and g.get("d5") is not None:
        sc += 0.5 if g["d5"] > 2 else (-0.5 if g["d5"] < -2 else 0)
        morceaux.append(f"ETF {nb(g['d5'], 1, True)} t / 5 j")
    dc = a.get("decomp")
    if dc:
        sc += 0.5 if dc["semaine"]["residu"] > 0.5 else (-0.5 if dc["semaine"]["residu"] < -0.5 else 0)
        morceaux.append(f"demande de fond {nb(dc['semaine']['residu'], 2, True)} % / 5 j")
    if morceaux:
        out.append({"nom": "Flux", "intensite": clamp(abs(sc) * 70, 0, 100), "effet": 1 if sc > 0.2 else (-1 if sc < -0.2 else 0),
                    "etat": ", ".join(morceaux)})
    if not out:
        return None
    dom = max(out, key=lambda d: d["intensite"])
    return {"dimensions": out, "dominant": dom}


def type_reaction(titre):
    for motif, typ in ((r"federal funds rate|rate decision", "fed"), (r"core pce|pce price", "inflation"),
                       (r"\bcpi\b", "inflation"), (r"non-farm|nfp", "activite"), (r"unemployment rate", "chomage"),
                       (r"average hourly earnings", "inflation"), (r"\bppi\b", "inflation"), (r"ism ", "activite"),
                       (r"retail sales", "activite"), (r"\bgdp\b", "activite")):
        if re.search(motif, titre, re.I):
            return typ
    return None


def moteur_reactions(a, data):
    """Après chaque annonce majeure publiée : consensus, réel, surprise, réaction du dollar, du 2 ans et de l'or,
    puis interprétation (réaction conforme, inverse, ou absente)."""
    im = data.get("intermarche")
    out = []
    for e in data.get("cal") or []:
        if e["impact"] != "High" or not e.get("reel"):
            continue
        typ = type_reaction(e["titre"])
        if not typ:
            continue
        reel, prev = _nombre(e["reel"]), _nombre(e["prevision"])
        surprise = (reel - prev) if (reel is not None and prev is not None) else None
        sens_s = 0 if not surprise else (1 if surprise > 0 else -1)
        if typ == "chomage":
            sens_s = -sens_s
        # surprise « dure » (bonne pour le dollar, mauvaise pour l'or) = +1
        attendu_or = -sens_s
        t = pd.Timestamp(e["date"])
        reac = {}
        if im is not None:
            for cle, col, mode in (("or", "GC=F", "pct"), ("dxy", "DX-Y.NYB", "pct"), ("us2", "US2Y", "pb")):
                s = im.get(col) if col in im else None
                reac[cle + "_15"] = _mv(s, t - pd.Timedelta(minutes=1), t + pd.Timedelta(minutes=15), mode) if s is not None else None
                reac[cle + "_60"] = _mv(s, t - pd.Timedelta(minutes=1), t + pd.Timedelta(minutes=60), mode) if s is not None else None
            if im.get("GC=F") is not None and reac.get("or_60") is not None:
                g0 = float(im["GC=F"].dropna().asof(t - pd.Timedelta(minutes=1)))
                reac["or_usd_60"] = g0 * reac["or_60"] / 100
        mv = reac.get("or_60")
        if mv is None:
            interp = "Réaction pas encore mesurable (moins d'une heure, ou données intraday indisponibles)."
        elif sens_s == 0:
            interp = "Chiffre conforme au consensus : la réaction vient du détail ou des révisions."
        elif abs(mv) < 0.1:
            interp = "Surprise sans réaction de l'or : l'information était déjà dans les prix, ou un autre moteur domine."
        elif (mv > 0) == (attendu_or > 0):
            interp = ("Réaction conforme : surprise " + ("dure (dollar et taux en hausse), l'or baisse." if sens_s > 0
                                                         else "souple (dollar et taux en baisse), l'or monte."))
        else:
            interp = ("Réaction inverse : " + ("malgré une surprise dure, l'or monte. Signe de demande de fond, à "
                                               "surveiller comme force relative." if sens_s > 0 else
                                               "malgré une surprise souple, l'or baisse. Signe de faiblesse ou de "
                                               "prises de bénéfices."))
        out.append({"titre": e["titre"], "nom": type_annonce(e["titre"]) or e["titre"], "date": e["date"], "prevision": e["prevision"],
                    "reel": e["reel"], "surprise": surprise, "sens_surprise": sens_s, "reac": reac, "interpretation": interp})
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:8]


def plan_trader(a, data):
    """Fiche du mode Trader : biais, setup, zone d'entrée, confirmation, stop, objectifs, R:R. Le signal (prix dans la
    zone et confirmation présente) est recalculé en direct dans la page avec le prix XAUS."""
    pb, t = a.get("playbook"), a.get("technique")
    b = a["biais"]
    biais = {"Haussier": 1, "Baissier": -1}.get(b["verdict"], 0)
    res = {"biais": biais, "biais_txt": b["verdict"], "setup": pb["titre"] if pb else "Pas de playbook",
           "sens": pb["sens"] if pb else 0, "cas": pb["cas"] if pb else None, "ref": t["prix"] if t else None}
    if not pb or not t:
        return res
    atr = (a.get("seance") or {}).get("atr") or 1.0
    sp = COMPTE["spread"]
    sens = pb["sens"]
    z = (pb["zones_entree"] or [None])[0]
    if not sens or not z:
        return res
    entree = z["centre"]
    marge = max(0.08 * atr, 1.0) + sp
    stop = (z["bas"] - marge) if sens > 0 else (z["haut"] + marge)
    inv = pb.get("invalidation")
    if inv is not None and ((sens > 0 and stop > inv > stop - 0.5 * atr) or (sens < 0 and stop < inv < stop + 0.5 * atr)):
        stop = inv - sens * (0.05 * atr + sp)  # le dernier sommet / creux 1 h est juste derrière : stop structurel
    cibles = [c["centre"] for c in pb["cibles"]][:2]
    sigma = (a.get("vue") or {}).get("sigma") or atr * 0.4
    while len(cibles) < 2:
        cibles.append((cibles[-1] if cibles else entree) + sens * sigma)
    risque = abs(entree - stop) + sp
    rr = [(abs(c - entree) - sp) / risque if risque else None for c in cibles]
    av = (t.get("avances") or {})
    dec = av.get("declencheur")
    bal = [x for x in av.get("balayages", []) if x["sens"] == sens and x["age"] <= 3]
    s15 = next((x for x in t["structure"] if x["nom"] == "15 min"), None)
    conf_ok = []
    if dec and dec["sens"] == sens:
        conf_ok.append(dec["nom"].lower())
    if bal:
        conf_ok.append(f"balayage {'sous' if sens > 0 else 'au-dessus de'} {bal[0]['lib'].lower()}")
    if s15 and s15.get("evenement") and s15["sens_evt"] == sens:
        conf_ok.append(s15["evenement"] + " 15 min")
    res.update({"zone": z, "entree": entree, "stop": stop, "cibles": cibles, "rr": rr, "risque": risque,
                "confirmation": ("rejet de la zone : mèche puis clôture 15 min " + ("haussière" if sens > 0 else "baissière")
                                 + " (avalement ou pin bar), idéalement après un balayage de liquidité "
                                 + ("sous la zone" if sens > 0 else "au-dessus de la zone") + ", ou nouveau "
                                 + ("HL" if sens > 0 else "LH") + " en 15 min"),
                "confirmations_presentes": conf_ok, "invalidation": inv})
    return res


# ---------------------------------------------------------------------------
# NOTES DE DESK : PLAN DE SÉANCE, BILAN, SEMAINE À VENIR
# ---------------------------------------------------------------------------

def _hm(d):
    return f"{d.hour:02d}:{d.minute:02d}"


def plan_du_jour(a, data):
    now = maintenant()
    v, s, pv = a.get("vue") or {}, a.get("seance"), a.get("profil_vol")
    jour = now.date() if now.weekday() < 5 else (now + timedelta(days=7 - now.weekday())).date()
    titre = "Plan de séance du jour" if jour == now.date() else f"Plan de séance pour {date_fr(jour)}"
    contexte = (f"Vue {v.get('direction', 'n.d.').lower()} à {HORIZON_H} heures (conviction {v.get('conviction', 'n.d.')}), "
                f"biais fondamental {a['biais']['verdict'].lower()}, régime : {a['regime']['nom'].lower()}.")
    prix = v.get("prix") or 0
    dessus, dessous = niveaux_autour(a, prix)
    seances = []
    for nom, h0, h1 in SEANCES:
        act = "n.d."
        if pv and pv.get("moy") is not None and pv.get("moy_jour"):
            m = pv["moy"].iloc[int(h0 * 4):int(h1 * 4)].mean() / pv["moy_jour"]
            act = ("calme" if m < 0.85 else ("active" if m > 1.15 else "moyenne")) + f" ({nb(m, 1)} × la moyenne)"
        evts = [e for e in data.get("cal") or [] if e["date"].date() == jour and h0 <= e["date"].hour + e["date"].minute / 60 < h1]
        seances.append({"nom": nom, "debut": h0, "fin": h1, "activite": act,
                        "annonces": [(e["date"], e["titre"], e["impact"]) for e in evts]})
    regles = []
    pb = a.get("playbook")
    if pb:
        regles.append(f"Playbook : {pb['titre']}. " + (pb["etapes"][0] if pb["etapes"] else ""))
    if v.get("sens") and v.get("conviction") in ("moyenne", "forte"):
        regles.append(f"Sens privilégié : {'achats' if v['sens'] > 0 else 'ventes'}. Un trade dans l'autre sens demande "
                      f"un signal technique très propre et une cible courte.")
    elif v.get("sens"):
        regles.append(f"Léger avantage aux {'achats' if v['sens'] > 0 else 'ventes'} (conviction faible) : pas de quoi "
                      f"forcer un sens, laisse ta structure technique décider.")
    elif v:
        regles.append("Pas de sens privilégié : travaille les extrémités du range avec des cibles courtes.")
    for e in [e for e in data.get("cal") or [] if e["date"].date() == jour and e["impact"] == "High"]:
        av, ap = FENETRE_NEWS
        regles.append(f"{e['titre']} à {_hm(e['date'])} : aucune position de {_hm(e['date'] - timedelta(minutes=av))} "
                      f"à {_hm(e['date'] + timedelta(minutes=ap))}.")
    if pv and pv.get("ratio") and pv["ratio"] > 1.3:
        regles.append(f"Séance {nb(pv['ratio'], 1)} fois plus agitée que d'habitude : élargis les stops ou réduis la taille.")
    if a.get("risque") and a["risque"]["etat"] == "Risk-off":
        regles.append("Marché en mode risk-off : mouvements brusques possibles dans les deux sens.")
    if s and (s.get("pct_atr") or 0) >= 80:
        regles.append(f"{nb(s['pct_atr'], 0)} % de l'amplitude habituelle déjà faite : les extensions deviennent moins probables.")
    return {"titre": titre, "contexte": contexte, "dessus": dessus[:4], "dessous": dessous[:4], "seances": seances,
            "regles": regles}


def bilan_seance(a, data):
    intra = data.get("intraday")
    if not intra or intra.get("barres") is None or intra["barres"].empty:
        return None
    b = intra["barres"]
    now = maintenant()
    jours = sorted(set(b.index.date))
    fini = now.weekday() >= 5 or now.hour >= 23 or jours[-1] < now.date()
    j = jours[-1] if fini else (jours[-2] if len(jours) > 1 else jours[-1])
    bj = b[b.index.date == j]
    o, c, h, l = float(bj["open"].iloc[0]), float(bj["close"].iloc[-1]), float(bj["high"].max()), float(bj["low"].min())
    atr = (a.get("seance") or {}).get("atr")
    phr = [f"L'or a {'gagné' if c >= o else 'perdu'} {nb(abs(c / o - 1) * 100, 2)} % "
           f"({nb(c - o, 1, True)} $), entre {nb(l + AJUST, 1)} et {nb(h + AJUST, 1)}"
           + (f", soit {nb((h - l) / atr * 100, 0)} % de l'amplitude habituelle." if atr else ".")]
    annonces = []
    for e in data.get("cal") or []:
        if e["date"].date() == j and e["reel"]:
            ap = b[(b.index >= pd.Timestamp(e["date"])) & (b.index < pd.Timestamp(e["date"]) + pd.Timedelta(minutes=60))]
            av = b[b.index < pd.Timestamp(e["date"])]
            mv = (float(ap["close"].iloc[-1]) - float(av["close"].iloc[-1])) if len(ap) and len(av) else None
            annonces.append({"titre": e["titre"], "reel": e["reel"], "prev": e["prevision"], "mv": mv,
                             "heure": _hm(e["date"])})
    prev = [p for p in (data.get("hist") or {}).get("previsions", [])
            if p.get("res") in ("ok", "ko") and datetime.fromisoformat(p["ts"]).date() == j]
    if prev:
        phr.append(f"Prévisions du terminal ce jour-là : {sum(p['ok'] for p in prev)} justes sur {len(prev)}.")
    return {"date": j, "phrases": phr, "annonces": annonces}


def semaine_a_venir(a, data):
    now = maintenant()
    jours = {}
    for e in data.get("cal") or []:
        if e["impact"] == "High" and e["date"] >= now:
            jours.setdefault(e["date"].date(), []).append((e["date"], e["titre"]))
    fw = a.get("fedwatch")
    if fw and fw["reunions"] and (fw["reunions"][0]["date"] - now.date()).days <= 14:
        d = fw["reunions"][0]["date"]
        jours.setdefault(d, []).append((datetime.combine(d, datetime.min.time()).replace(hour=20, tzinfo=tz_local()),
                                        f"Décision de la Fed (hausse pricée à {nb(fw['reunions'][0]['p_hausse'] * 100, 0)} %)"))
    for adj in (a.get("adjudic") or {}).get("avenir", []):
        if adj["date"] <= now + timedelta(days=10):
            jours.setdefault(adj["date"].date(), []).append((adj["date"], f"Adjudication {adj['terme']}"
                                                                          + (f" ({nb(adj['montant'], 0)} Md$)" if adj['montant'] else "")))
    s = a.get("seance") or {}
    return {"jours": sorted(jours.items())[:10], "haut_sem": s.get("haut_sem"), "bas_sem": s.get("bas_sem")}


def contexte_controle(a):
    """Données transmises au contrôle Achat / Vente (calculé dans la page, avec le prix que tu saisis)."""
    s, v, pv = a.get("seance") or {}, a.get("vue") or {}, a.get("profil_vol") or {}

    def propre(x):
        if isinstance(x, float):
            return None if (math.isnan(x) or math.isinf(x)) else round(x, 3)
        if isinstance(x, dict):
            return {k: propre(val) for k, val in x.items()}
        if isinstance(x, (list, tuple)):
            return [propre(val) for val in x]
        return x

    moy = pv.get("moy")
    return propre({
        "prix": v.get("prix"), "maj": s["maj"].isoformat() if s.get("maj") is not None else None,
        "niveaux": [{"lib": n["lib"], "v": n["cfd"]} for n in s.get("niveaux", [])],
        "atr": s.get("atr"), "pct_atr": s.get("pct_atr"), "vwap": (s["vwap"] + AJUST) if s.get("vwap") else None,
        "biais": {"verdict": a["biais"]["verdict"], "total": a["biais"]["total"], "n": a["biais"]["n"]},
        "vue": {"sens": v.get("sens", 0), "direction": v.get("direction"), "conv": v.get("conviction")},
        "tend": {t["nom"]: t["sens"] for t in a.get("matrice") or []},
        "slots": [float(x) if x == x else None for x in moy.tolist()] if moy is not None else None,
        "moy_jour": pv.get("moy_jour"), "compte": COMPTE, "zone": list(FENETRE_NEWS), "sigma": v.get("sigma"),
        "horizon": HORIZON_H,
        "foule": (a.get("carnets") or {}).get("part_acheteurs"),
        "confluences": [{"bas": z["bas"], "haut": z["haut"], "centre": z["centre"], "score": z["score"],
                         "elements": z["elements"][:4]} for z in (a.get("technique") or {}).get("confluences", [])],
        "playbook": ({"sens": a["playbook"]["sens"], "titre": a["playbook"]["titre"], "cas": a["playbook"]["cas"],
                      "faible": a["playbook"]["faible"], "invalidation": a["playbook"]["invalidation"]}
                     if a.get("playbook") else None),
        "vwap_sigma": ((a.get("technique") or {}).get("vwap") or {}).get("sigma"),
        "trader": (lambda tr: dict({k: tr.get(k) for k in ("sens", "biais", "setup", "cas", "stop", "cibles", "rr",
                                                              "confirmations_presentes", "invalidation", "ref")},
                                   zone=({"bas": tr["zone"]["bas"], "haut": tr["zone"]["haut"]} if tr.get("zone") else None)))(a.get("trader") or {}),
        "avert": 30,
        "avances": (lambda c: {
            "pd": ({"zone": c["pd"]["zone"], "pos": c["pd"]["pos"]} if c.get("pd") else None),
            "balayages": [{"sens": x["sens"], "lib": x["lib"], "niveau": x["niveau"] + AJUST, "age": x["age"]} for x in c["balayages"]],
            "declencheur": ({"nom": c["declencheur"]["nom"], "sens": c["declencheur"]["sens"]} if c.get("declencheur") else None),
            "divergences": [{"nom": k, "sens": d["sens"], "texte": d["texte"]} for k, d in c["divergences"].items() if d],
            "kz": c["kill_zone"][0] if c.get("kill_zone") else None} if c else None)((a.get("technique") or {}).get("avances")),
        "structure": {x["nom"]: {"sens": x["tendance"], "etat": x["etat"], "h": x["label_sommet"], "l": x["label_creux"],
                                 "sommet": x["sommet"] + AJUST, "creux": x["creux"] + AJUST, "evt": x["evenement"]}
                      for x in (a.get("technique") or {}).get("structure", [])},
        "poches": [{"lib": l, "v": v_} for l, v_ in niveaux_carnets(a.get("carnets")) if "Stops" in l],
        "inter": [{"lib": l["lib"], "signal": l["signal"], "residu": l["residu"]} for l in (a.get("intermarche") or {}).get("lignes", [])],
        "ia": ({"achat": (a.get("ia") or {}).get("json", {}).get("achat"), "vente": (a.get("ia") or {}).get("json", {}).get("vente"),
                "quand": ia_quand(a["ia"])} if (a.get("ia") or {}).get("json") else None),
    })


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
    ref = derniere(data["intraday"]["barres"]["close"]) if data.get("intraday") else a["or"]["prix"]
    ecart = None
    xs = data.get("xaus") or {}
    if data.get("intraday") and xs.get("serie") is not None and len(xs["serie"]) > 10:
        t_bar = data["intraday"]["barres"].index[-1] + pd.Timedelta(minutes=15)
        t_bar = min(t_bar, pd.Timestamp(maintenant()))
        se = xs["serie"]
        if se.index[0] <= t_bar and (t_bar - se.index[se.index <= t_bar][-1]) <= pd.Timedelta(minutes=10):
            spot_t = float(se.asof(t_bar))
            if ref and 0 <= ref - spot_t < ref * 0.03:
                ecart = {"base": ref - spot_t, "contrat": "mesuré au même instant (XAUS)", "taux": None,
                         "jours": None, "mesure": True, "t": t_bar}
    if ecart is None:
        ecart = ecart_future_spot(ref, data.get("courbe"), derniere(f.get("sofr")))
    a["ecart"] = ecart
    base = ecart["base"] if ecart else None
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
    a["carnets"] = analyser_carnets(data.get("oanda"), (a.get("seance") or {}).get("atr"))
    s_ = a.get("seance")
    if s_ and a["carnets"]:
        deja = []
        for lib, v_spot in niveaux_carnets(a["carnets"]):
            if any(abs(v_spot - x) < 1.0 for x in deja):
                continue  # même zone déjà ajoutée (ex. poche de stops et vendeurs piégés au même prix)
            deja.append(v_spot)
            v = v_spot - AJUST
            s_["niveaux"].append({"lib": lib, "niveau": v, "cfd": v_spot, "dist": v - s_["prix"],
                                  "dist_atr": ((v - s_["prix"]) / s_["atr"]) if s_.get("atr") else None, "carnet": True})
        s_["niveaux"].sort(key=lambda n: -n["niveau"])
    a["intermarche"] = confirmation_intermarche(data.get("intermarche"), a.get("decomp"))
    a["matrice"] = matrice_tendance(data.get("intraday"))
    a["technique"] = analyse_technique(a, data)
    a["playbook"] = playbook(a, data)
    a["risque"] = jauge_risque(a, f)
    a["profil_vol"] = profil_volatilite(data.get("intraday"))
    a["adjudic"] = analyser_adjudications(data.get("adjudic"))
    hist = data.setdefault("hist", {"previsions": [], "annonces": []})
    memoriser_annonces(hist, data.get("cal"))
    a["reactions"] = reactions_annonces(data.get("intraday"), hist)
    expliquer(data, a)
    a["vue"] = vue_marche(a, data)
    a["intermarket"] = module_intermarche(a, data)
    a["grs"] = force_relative(a, data)
    a["regimes"] = regimes_v2(a, data)
    a["reactions_evt"] = moteur_reactions(a, data)
    a["trader"] = plan_trader(a, data)
    a["plan"] = plan_du_jour(a, data)
    a["bilan"] = bilan_seance(a, data)
    a["semaine"] = semaine_a_venir(a, data)
    return a


# ---------------------------------------------------------------------------
# ANALYSTE IA (optionnel) : API Claude, clé dans la variable ANTHROPIC_API_KEY
# ---------------------------------------------------------------------------

CONSIGNE_IA = """Tu es l'analyste macro principal d'un desk de trading spécialisé sur l'or (XAU/USD).
Ton lecteur est un trader intraday (scalping) qui prépare sa séance. Tu reçois le relevé complet
de son terminal : prix, biais fondamental, vue de marché, tendance, niveaux de séance, Fed, flux, annonces, actualités.

Réponds UNIQUEMENT avec un objet JSON valide, sans texte autour et sans balises de code, avec exactement ces clés :
{
  "titre": "une phrase qui résume la situation de l'or maintenant",
  "lecture": "3 à 5 phrases : ce qui se passe et pourquoi, en reliant les chiffres entre eux",
  "direction": "haussière, baissière ou neutre (ta propre lecture à quelques heures)",
  "accord": "d'accord, nuancé ou en désaccord (avec la vue de marché du terminal)",
  "accord_explication": "1 à 2 phrases : pourquoi",
  "forces": ["3 à 5 éléments chiffrés, chacun commençant par « Soutien : » ou « Pression : »"],
  "annonces": ["pour chaque annonce majeure à venir : réaction probable de l'or si le chiffre sort au-dessus ou en dessous de la prévision"],
  "invalidation": ["2 à 3 éléments qui invalideraient ta lecture"],
  "achat": "2 à 3 phrases : dans quelles conditions un achat serait cohérent aujourd'hui (niveaux, timing, ce qu'il faut voir sur le prix), ou pourquoi l'éviter",
  "vente": "même chose pour une vente",
  "vigilance": ["2 à 3 points concrets pour la séance : niveaux, horaires, volatilité attendue"]
}

Règles : écris en français. Utilise les niveaux du relevé tels quels (ils sont exprimés en XAU/USD spot). N'invente
aucun chiffre absent du relevé ; les titres d'actualité servent seulement de contexte. Quand les signaux se contredisent,
dis-le au lieu de trancher artificiellement. Pas de conseil en investissement ni d'ordre ferme : décris des conditions,
pas des injonctions. 450 mots au maximum au total."""

CLES_IA = ("titre", "lecture", "direction", "accord", "accord_explication", "forces", "annonces", "invalidation",
           "achat", "vente", "vigilance")


def lire_json_ia(texte):
    """Extrait l'objet JSON de la réponse (tolère des balises de code ou du texte autour)."""
    if not texte:
        return None
    t = texte.strip()
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(t[i:j + 1])
    except Exception:
        return None
    if not isinstance(d, dict) or not d.get("lecture"):
        return None
    for k in ("forces", "annonces", "invalidation", "vigilance"):
        v = d.get(k)
        d[k] = [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])
    for k in ("titre", "lecture", "direction", "accord", "accord_explication", "achat", "vente"):
        d[k] = str(d.get(k) or "")
    return d


def cle_ia():
    """Retourne (fournisseur, clé). Une clé Google (AIza...) rangée par erreur sous l'autre nom est aussi reconnue."""
    ant = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    gem = os.environ.get("GEMINI_API_KEY", "").strip()
    if ant.startswith("AIza") and not gem:
        gem, ant = ant, ""
    if ant:
        return "claude", ant
    if gem:
        return "gemini", gem
    return None, None


def appeler_claude(cle, brief):
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=120, headers={
        "x-api-key": cle, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": IA_MODELE, "max_tokens": 2000, "system": CONSIGNE_IA,
              "messages": [{"role": "user", "content": "Relevé du terminal :\n\n" + brief}]})
    r.raise_for_status()
    texte = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
    return texte, IA_MODELE


def modeles_gemini(cle):
    """Modèles Gemini à essayer, du meilleur au plus économe : Flash le plus récent, puis Flash-Lite."""
    if GEMINI_MODELE != "auto":
        return [GEMINI_MODELE]
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models", timeout=30,
                         headers={"x-goog-api-key": cle}, params={"pageSize": 200})
        r.raise_for_status()
        noms = [m.get("name", "").split("/", 1)[-1] for m in r.json().get("models", [])
                if "generateContent" in (m.get("supportedGenerationMethods") or [])]
    except Exception:
        noms = []
    exclus = ("image", "tts", "audio", "live", "embedding", "exp", "robotics", "computer", "native", "thinking")

    def version(n):
        m = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
        return float(m.group(1)) if m else 0.0

    tri = lambda l: sorted(l, key=lambda n: (version(n), "preview" not in n, -len(n)), reverse=True)
    flash = tri([n for n in noms if "flash" in n and "lite" not in n and not any(e in n for e in exclus)])
    lite = tri([n for n in noms if "flash-lite" in n and not any(e in n for e in exclus)])
    choix = flash[:2] + lite[:2]
    for secours in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):  # modèles stables en dernier recours
        if secours in noms and secours not in choix:
            choix.append(secours)
    return choix or ["gemini-2.5-flash", "gemini-2.5-flash-lite"]


def appeler_gemini(cle, brief):
    """Essaie les modèles un par un. Serveur surchargé (500, 503) : jusqu'à deux nouvelles tentatives espacées.
    Quota du jour atteint (429) ou modèle absent (404) : modèle suivant."""
    corps = {"systemInstruction": {"parts": [{"text": CONSIGNE_IA}]},
             "contents": [{"role": "user", "parts": [{"text": "Relevé du terminal :\n\n" + brief}]}],
             "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4, "maxOutputTokens": 8192}}
    echecs = []
    for modele in modeles_gemini(cle):
        for essai in range(3):
            r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{modele}:generateContent",
                              timeout=120, headers={"x-goog-api-key": cle, "content-type": "application/json"}, json=corps)
            if r.status_code in (500, 503) and essai < 2:
                time.sleep(8 * (essai + 1))
                continue
            break
        if r.status_code >= 400:
            try:
                msg = str((r.json().get("error") or {}).get("message", ""))[:60]
            except Exception:
                msg = ""
            motif = {404: "absent", 429: "quota atteint", 500: "erreur serveur", 503: "surchargé",
                     401: "clé refusée", 403: "accès refusé"}.get(r.status_code, f"HTTP {r.status_code}")
            echecs.append(f"{modele} {motif}" + (f" ({msg})" if r.status_code in (400, 401, 403) and msg else ""))
            if r.status_code in (401, 403):
                break  # clé invalide : inutile d'essayer d'autres modèles
            continue
        cand = (r.json().get("candidates") or [{}])[0]
        texte = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts", []) if not p.get("thought")).strip()
        if texte:
            return texte, modele
        echecs.append(f"{modele} réponse vide ({cand.get('finishReason', 'n.d.')})")
    raise RuntimeError(" ; ".join(echecs) or "aucun modèle Gemini disponible")


def analyste_ia(brief, a=None, data=None):
    """Analyse rédigée par Claude. Régénérée toutes les IA_INTERVALLE_MIN minutes en séance, et aussitôt après une
    annonce forte ou un changement de direction de la vue de marché. Sans clé ANTHROPIC_API_KEY : rien n'est appelé."""
    fournisseur, cle = cle_ia()
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
    dans_fenetre = now.weekday() < 5 and IA_HEURES[0] <= now.hour < IA_HEURES[1]
    direction = ((a or {}).get("vue") or {}).get("direction")
    raison = None
    if cache is None:
        raison = "première analyse"
    elif dans_fenetre:
        age_min = (now.timestamp() - cache.get("ts", 0)) / 60
        dernier = datetime.fromtimestamp(cache.get("ts", 0), tz=now.tzinfo)
        publiees = [e for e in (data or {}).get("cal") or [] if e["impact"] == "High"
                    and dernier < e["date"] <= now - timedelta(minutes=5)]
        if age_min >= IA_INTERVALLE_MIN:
            raison = "mise à jour programmée"
        elif publiees:
            raison = f"après {publiees[-1]['titre']}"
        elif direction and cache.get("vue") and direction != cache.get("vue"):
            raison = "changement de la vue de marché"
    compteur = (cache or {}).get("compteur") or {}
    if raison and cache is not None and compteur.get("date") == now.date().isoformat() and compteur.get("n", 0) >= IA_MAX_JOUR:
        raison = None  # plafond du jour atteint : on garde la dernière analyse
    if raison is None:
        noter("Analyste IA", True, "analyse en cache")
        return cache
    try:
        texte, modele = (appeler_claude if fournisseur == "claude" else appeler_gemini)(cle, brief)
        if not texte:
            raise RuntimeError("réponse vide")
        n = compteur.get("n", 0) + 1 if compteur.get("date") == now.date().isoformat() else 1
        cache = {"ts": now.timestamp(), "texte": texte, "json": lire_json_ia(texte), "modele": modele,
                 "fournisseur": "Claude" if fournisseur == "claude" else "Gemini", "heure": now.isoformat(),
                 "vue": direction, "raison": raison, "compteur": {"date": now.date().isoformat(), "n": n}}
        DOSSIER_CACHE.mkdir(exist_ok=True)
        fichier.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        noter("Analyste IA", True, f"nouvelle analyse {cache['fournisseur']} ({raison}), {n} aujourd'hui")
    except Exception as e:
        noter("Analyste IA", False, ((str(e) or type(e).__name__)[:230]
                                     + (" ; nouvel essai au prochain passage du robot" if cache is None else
                                        " ; dernière analyse conservée")))
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


def _etiquettes_svg(swings, x_of, y, lo, hi):
    """Étiquettes HH / HL / LH / LL et ligne en zigzag reliant les sommets et creux."""
    out, pts = [], []
    for sw in swings:
        x = x_of(sw["t"])
        if x is None or not (lo <= sw["prix"] <= hi):
            continue
        yy = y(sw["prix"])
        pts.append(f"{x:.1f},{yy:.1f}")
        if not sw["label"]:
            continue
        coul = "var(--up)" if sw["label"] in ("HH", "HL") else "var(--down)"
        dy = -8 if sw["type"] == "H" else 16
        out.append(f'<circle cx="{x:.1f}" cy="{yy:.1f}" r="2.6" fill="{coul}"/>'
                   f'<text x="{x:.1f}" y="{yy + dy:.1f}" text-anchor="middle" class="swl" fill="{coul}">{sw["label"]}</text>')
    zig = (f'<polyline points="{" ".join(pts)}" fill="none" stroke="var(--mute)" stroke-width="1" '
           f'stroke-dasharray="4 3" opacity=".7"/>') if len(pts) > 1 else ""
    return zig + "".join(out)


def graphique_structure(df, swings, titre, w=640, h=300):
    """Bougies avec la structure de marché : zigzag des sommets et creux, étiquettes HH / HL / LH / LL."""
    if df is None or len(df) < 10:
        return '<p class="vide">Données insuffisantes.</p>'
    pl, pr, pt, pb = 8, 62, 16, 22
    W, H = w - pl - pr, h - pt - pb
    lo, hi = float(df["low"].min()), float(df["high"].max())
    marge = (hi - lo) * 0.06
    lo, hi = lo - marge, hi + marge
    n = len(df)
    cw = W / n
    y = lambda v: pt + (hi - v) / ((hi - lo) or 1) * H
    idx = {t: i for i, t in enumerate(df.index)}
    x_of = lambda t: (pl + (idx[t] + 0.5) * cw) if t in idx else None
    out = []
    for i, (t, r) in enumerate(df.iterrows()):
        xc = pl + (i + 0.5) * cw
        c = "var(--up)" if r["close"] >= r["open"] else "var(--down)"
        yo, yc = y(r["open"]), y(r["close"])
        out.append(f'<line x1="{xc:.1f}" x2="{xc:.1f}" y1="{y(r["high"]):.1f}" y2="{y(r["low"]):.1f}" stroke="{c}" '
                   f'stroke-width="1" opacity=".75"/><rect x="{xc - cw * 0.32:.1f}" y="{min(yo, yc):.1f}" '
                   f'width="{max(cw * 0.64, 1):.1f}" height="{max(abs(yc - yo), 0.8):.1f}" fill="{c}" opacity=".75"/>')
    for k in range(0, n, max(1, n // 6)):
        t = df.index[k]
        out.append(f'<text x="{pl + (k + 0.5) * cw:.1f}" y="{h - 6}" text-anchor="middle" class="ax">'
                   f'{t.day}/{t.month} {t.hour:02d}h</text>')
    for v in (hi - marge, lo + marge):
        out.append(f'<text x="{pl + W + 6}" y="{y(v) + 4:.1f}" class="ax">{nb(v + AJUST, 0)}</text>')
    px = float(df["close"].iloc[-1])
    out.append(f'<line x1="{pl}" x2="{pl + W}" y1="{y(px):.1f}" y2="{y(px):.1f}" class="prix-l"/>'
               f'<text x="{pl + W + 6}" y="{y(px) + 4:.1f}" class="ax c1">{nb(px + AJUST, 1)}</text>')
    out.append(_etiquettes_svg(swings, x_of, y, lo, hi))
    return f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(titre)}">' + "".join(out) + '</svg>'



COURTS = {"Plus haut de la veille": "Haut veille", "Plus bas de la veille": "Bas veille"}


def graphique_bougies(s, w=1000, h=340, zones=None, fvg=None):
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
    for z in zones or []:
        zb, zh = z["bas"] - AJUST, z["haut"] - AJUST
        if zh < lo or zb > hi:
            continue
        y1_, y2_ = y(min(zh, hi)), y(max(zb, lo))
        out.insert(0, f'<rect x="{pl}" y="{y1_:.1f}" width="{W}" height="{max(y2_ - y1_, 1):.1f}" fill="var(--steel)" '
                      f'opacity="{min(0.08 + z["score"] / 100, 0.22):.2f}"/>')
    for f in fvg or []:
        if f["haut"] < lo or f["bas"] > hi:
            continue
        x0 = pl + (list(df.index).index(f["t"]) * cw if f["t"] in df.index else 0)
        y1_, y2_ = y(min(f["haut"], hi)), y(max(f["bas"], lo))
        coul = "var(--up)" if f["sens"] > 0 else "var(--down)"
        out.insert(0, f'<rect x="{x0:.1f}" y="{y1_:.1f}" width="{pl + W - x0:.1f}" height="{max(y2_ - y1_, 1):.1f}" '
                      f'fill="{coul}" opacity=".10"/>')
    sw = swings_etiquetes(df, PARAMS_STRUCTURE["15 min"][0], n=len(df), mult=PARAMS_STRUCTURE["15 min"][1])
    pos = {t: i for i, t in enumerate(df.index)}
    out.append(_etiquettes_svg(sw, lambda t: (pl + (pos[t] + 0.5) * cw) if t in pos else None, y, lo, hi))
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
  --brass:#cfa54b; --steel:#98a3ae; --up:#4fb884; --down:#e0605a; --amber:#f0a94b;
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
.up{color:var(--up)} .down{color:var(--down)} .flat{color:var(--mute)} .warn{color:var(--amber)} .brass{color:var(--brass)}
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
.prochaine b{font-variant-numeric:tabular-nums;color:var(--brass)}
body.zone-news .prochaine b{color:var(--amber)}
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
  grid-template-areas:"g c d";align-items:start}
.col{display:flex;flex-direction:column;gap:10px;min-height:0;min-width:0}
.col-g{grid-area:g}.col-c{grid-area:c}.col-d{grid-area:d}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:4px;display:flex;flex-direction:column;min-height:0}
.ph{display:flex;justify-content:space-between;align-items:baseline;gap:10px;padding:7px 12px;border-bottom:1px solid var(--line)}
.ph .k{color:var(--ink)} .ph small{font:500 11.5px var(--cond);color:var(--mute)}
.pb{padding:10px 12px;overflow:auto;flex:1;min-height:0}
.pb.nopad{padding:0;overflow:hidden}
#p-graph{flex:none;height:calc(100vh - 330px);min-height:520px}
.minis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;height:118px;flex:none}
.minis .panel{overflow:hidden}
#p-vue,#p-ctl,#p-evt,#p-niv{flex:none}
#p-vue .pb,#p-ctl .pb,#p-evt .pb,#p-niv .pb{overflow:visible}
#p-news{flex:none;height:460px}
.tv{position:relative;width:100%;height:100%}
.tv::before{content:attr(data-lib);position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  color:var(--mute);font:500 12.5px var(--cond);text-align:center;padding:8px}
.tv iframe{position:relative;z-index:1}
.tv.charge::before,.tv:has(iframe)::before{display:none}
.flash{animation:flash 1.6s ease-out}
@keyframes flash{0%{box-shadow:inset 0 0 0 2px var(--brass)}100%{box-shadow:inset 0 0 0 2px transparent}}

.cmd-top{display:flex;align-items:center;gap:14px;margin-bottom:8px}
.vue-top{display:flex;align-items:center;gap:16px;margin-bottom:8px}
.vue-score{flex:1}
.tfs{display:flex;gap:6px;flex-wrap:wrap;margin:4px 0 8px}
.tf{font:500 12.5px var(--cond);padding:2px 8px;border-radius:10px;background:var(--band);border:1px solid var(--line)}
.fourch{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;padding:6px 0;border-top:1px solid rgba(34,50,66,.7);
  border-bottom:1px solid rgba(34,50,66,.7);margin-bottom:6px}
.fourch b{font:600 18px var(--cond);font-variant-numeric:tabular-nums;color:var(--brass)}
.fourch small{color:var(--mute);font-family:var(--cond)}
.scen{margin:6px 0;font-size:13.5px}
.scen b{font-family:var(--cond);font-weight:600}
.scen.alt{color:var(--mute)}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{font-size:12px;padding:2px 8px;border-radius:10px;background:var(--band);border:1px solid var(--line);color:var(--mute)}
.chip.up{border-color:rgba(79,184,132,.5);color:var(--up)} .chip.down{border-color:rgba(224,96,90,.5);color:var(--down)}
.ctl-boutons{display:flex;gap:8px;margin-bottom:8px}
.ctl-boutons button{flex:1;font:600 16px var(--cond);padding:8px}
.ctl-boutons .achat.on{background:var(--up);border-color:var(--up);color:#06110b}
.ctl-boutons .vente.on{background:var(--down);border-color:var(--down);color:#1a0706}
.ctl-champs{display:flex;gap:8px;margin-bottom:8px}
.ctl-champs label{flex:1;display:flex;flex-direction:column;font:600 10.5px var(--cond);letter-spacing:.08em;
  text-transform:uppercase;color:var(--mute);gap:3px}
.ctl-champs input{font:500 15px var(--cond);color:var(--ink);background:var(--band);border:1px solid var(--line);
  border-radius:3px;padding:5px 8px;width:100%;font-variant-numeric:tabular-nums;text-transform:none;letter-spacing:0}
.ctl-champs input:focus{outline:2px solid var(--brass);outline-offset:0}
.verdict-ctl{font:700 24px/1.1 var(--cond);letter-spacing:.03em;padding:6px 10px;border-radius:3px;display:block;margin-bottom:6px}
.verdict-ctl small{font:500 14px var(--cond);letter-spacing:0;margin-left:6px;opacity:.85}
.verdict-ctl.oui{background:rgba(79,184,132,.18);color:var(--up)}
.verdict-ctl.att{background:rgba(240,169,75,.16);color:var(--amber)}
.verdict-ctl.non{background:rgba(224,96,90,.18);color:var(--down)}
.raisons{list-style:none;margin:0;padding:0;font-size:13px}
.raisons li{display:grid;grid-template-columns:18px 1fr;gap:6px;padding:3px 0;border-bottom:1px solid rgba(34,50,66,.7)}
.raisons .i-ok{color:var(--up)} .raisons .i-att{color:var(--amber)} .raisons .i-non{color:var(--down)} .raisons .i-info{color:var(--mute)}
.taille{margin-top:6px;padding:6px 8px;background:var(--band);border-radius:3px;font-size:13px}
.ctl-boutons kbd{font:500 10.5px var(--cond);border:1px solid currentColor;border-radius:3px;padding:0 4px;margin-left:6px;opacity:.6}
.plan-trade{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;margin:6px 0}
.pt{background:var(--band);border-radius:3px;padding:6px 8px;display:flex;flex-direction:column;min-width:0}
.pt span{font:600 10.5px var(--cond);letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
.pt b{font:600 17px var(--cond);font-variant-numeric:tabular-nums}
.pt small{color:var(--mute);font-size:11.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.repere{font-size:12.5px;color:var(--mute);margin:6px 0;padding:6px 8px;border-left:2px solid var(--steel);background:rgba(152,163,174,.06)}
.repere b{color:var(--ink);font-weight:600}
.groupe{margin-top:6px}
.ctl-actions{margin-top:8px}
dialog#reglages{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:18px 20px;
  max-width:520px;width:calc(100% - 32px)}
dialog#reglages::backdrop{background:rgba(0,0,0,.55)}
dialog#reglages h2{font:600 19px var(--cond);margin:0 0 6px}
.reg-champs{display:grid;gap:8px}
.reg-champs label{display:grid;grid-template-columns:1fr 120px;align-items:center;gap:10px;font-size:14px}
.reg-champs input{font:500 15px var(--cond);color:var(--ink);background:var(--band);border:1px solid var(--line);
  border-radius:3px;padding:5px 8px;font-variant-numeric:tabular-nums}
.reg-actions{display:flex;gap:8px;justify-content:flex-end;margin-top:14px}
.matrice-pb{display:grid;grid-template-columns:120px repeat(3,minmax(0,1fr));gap:4px;margin-bottom:12px}
.mx-h{font:600 11px var(--cond);letter-spacing:.06em;text-transform:uppercase;color:var(--mute);text-align:center;padding:4px}
.mx-l{font:600 11px var(--cond);letter-spacing:.06em;text-transform:uppercase;color:var(--mute);display:flex;align-items:center}
.mx-c{background:var(--band);border:1px solid var(--line);border-radius:3px;padding:10px 8px;font:500 13.5px/1.25 var(--cond);
  text-align:center;display:flex;align-items:center;justify-content:center;min-height:58px;opacity:.55}
.mx-c.actif{opacity:1;border:2px solid var(--brass);box-shadow:0 0 0 3px rgba(207,165,75,.15)}
.pb-titre{font:700 24px/1.15 var(--cond);margin:4px 0 8px}
.jauge-s{height:8px;background:var(--band);border-radius:4px;min-width:80px;overflow:hidden}
.jauge-s i{display:block;height:100%;background:var(--brass)}
.cal tr.ici td{color:var(--brass)}
.pb-ligne{margin:8px 0 0;padding:8px 10px;background:var(--band);border-radius:3px;font-size:13px}
.pb-ligne b{font:600 15px var(--cond)}
/* Fraîcheur des données */
.fr{display:inline-block;font:600 10px var(--cond);letter-spacing:.08em;padding:1px 6px;border-radius:8px;vertical-align:1px;
  border:1px solid var(--line);color:var(--mute);text-transform:uppercase;white-space:nowrap}
.fr.live{color:var(--up);border-color:rgba(79,184,132,.5)}
.fr.live::before{content:"";display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--up);margin-right:4px;vertical-align:1px}
.fr.differe{color:var(--amber);border-color:rgba(240,169,75,.5)}
.fr.differe::before{background:var(--amber)}
/* Prix en direct dans la barre */
.prix-live{display:flex;flex-direction:column;line-height:1.1}
.prix-live b{font:700 21px var(--cond);color:var(--brass);font-variant-numeric:tabular-nums}
.prix-live small{font:500 11px var(--cond);color:var(--mute)}
.mode-btn{border-color:var(--brass);color:var(--brass)}
body.mode-trader .analyst{display:none!important}
/* Fiche Trader */
.trader{padding:14px 14px 4px;border-bottom:1px solid var(--line)}
.tr-tete{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:8px}
.tr-tete h2{font:600 13px var(--cond);letter-spacing:.1em;text-transform:uppercase;color:var(--mute);margin:0}
.tr-tete span{font:500 13px var(--cond);color:var(--mute)}
.tr-tete b{font:600 16px var(--cond);color:var(--brass)}
.tr-l1,.tr-l2,.tr-l3{display:grid;gap:8px;margin-bottom:8px}
.tr-l1{grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1fr) minmax(0,2fr)}
.tr-l2{grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) minmax(0,1fr)}
.tr-l3{grid-template-columns:repeat(6,minmax(0,1fr))}
.tu{background:var(--panel);border:1px solid var(--line);border-left:4px solid var(--line);border-radius:4px;padding:8px 12px;
  display:flex;flex-direction:column;min-width:0}
.tu .k{color:var(--mute)}
.tu b{font:700 22px/1.15 var(--cond);margin-top:2px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.tr-l1 .tu b{font-size:28px}
.tu small{font:500 12.5px/1.3 var(--text);color:var(--mute);margin-top:3px}
.tu.up{border-left-color:var(--up)} .tu.up b{color:var(--up)}
.tu.down{border-left-color:var(--down)} .tu.down b{color:var(--down)}
.tu.warn{border-left-color:var(--amber)} .tu.warn b{color:var(--amber)}
.tu.flat b{color:var(--mute)}
.tu.large b{font-size:22px}
.statut{font:700 12px var(--cond);letter-spacing:.06em;padding:2px 8px;border-radius:3px;border:1px solid currentColor}
.jauge-s i.up{background:var(--up)} .jauge-s i.down{background:var(--down)} .jauge-s i.warn{background:var(--amber)} .jauge-s i.flat{background:var(--mute)}
.regimes{display:grid;grid-template-columns:150px 160px 40px 170px minmax(0,1fr);gap:8px 12px;align-items:center;font-size:14px}
.regimes.compact{grid-template-columns:150px minmax(0,1fr) 40px;max-width:560px}
.regimes .v{font-family:var(--cond);text-align:right}
@media (max-width:1099px){
  .tr-l1,.tr-l2{grid-template-columns:1fr}
  .tr-l3{grid-template-columns:repeat(3,minmax(0,1fr))}
  .regimes{grid-template-columns:120px 1fr 36px}
  .regimes span:nth-child(5n+4),.regimes span:nth-child(5n){display:none}
}
.avis-ia{margin-top:8px;padding:8px 10px;background:rgba(207,165,75,.07);border-left:2px solid var(--brass);border-radius:2px}
.avis-ia p{margin:4px 0 0;font-size:13px}
.ia-titre{font:600 18px/1.35 var(--cond);color:var(--ink)}
.taille b{font:600 16px var(--cond);color:var(--brass)}
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
.niv-c td.liq{color:var(--steel)}
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
.ia{max-width:none}
.ia > p{max-width:92ch}
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
.chart .swl{font:600 11px var(--cond);letter-spacing:.03em}
.chip.actif-kz{border-color:var(--brass);color:var(--brass)}
.lab{font:600 12.5px var(--cond);padding:1px 6px;border-radius:3px;margin-right:4px;display:inline-block}
.lab.hh,.lab.hl{background:rgba(79,184,132,.15);color:var(--up)} .lab.lh,.lab.ll{background:rgba(224,96,90,.15);color:var(--down)}
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
  .col-g,.col-c{height:auto}
  .col-d{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));height:auto;align-items:start}
  #p-news{height:420px}
}
/* Demi-écran à côté de la plateforme : une seule colonne, l'essentiel d'abord */
@media (max-width:1099px){
  .cockpit{grid-template-columns:minmax(0,1fr);grid-template-areas:"c" "d" "g"}
  .col-g,.col-c{height:auto;min-height:0}
  #p-graph{height:430px;flex:none} .minis{grid-template-columns:repeat(2,minmax(0,1fr));height:236px}
  .col-d{display:flex;height:auto} #p-news{height:420px;flex:none}
  .col-c .panel,.col-d .panel{flex:none}
  .prochaine{display:none}
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
      esc(e.bas) + '</span></div>' : '') + (e.nuance ? '<p class="nu">' + esc(e.nuance) + '</p>' : '') +
    (e.histo ? '<p class="pied">' + esc(e.histo) + '</p>' : '');
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
  horloges(); age(); majBadges(); majTrader();
  var barre = $('.barre'); if (barre) document.documentElement.style.setProperty('--haut-barre', barre.offsetHeight + 'px');
  var now = Date.now(), e = prochain(now), b = $('#alerte');
  var carte = $('#evt-carte');
  if (carte && (e ? e.t + e.titre : '') !== carte.getAttribute('data-cle')) rendreCarte();
  var cb = $('#cpt-barre');
  if (!e){ if (b) b.className = 'alerte'; if (cb) cb.textContent = 'aucune'; document.body.classList.remove('zone-news');
           document.title = titreBase; return; }
  var d = e.ts - now, cpt = $('#cpt');
  if (cb) cb.textContent = e.nom + ' · ' + (d > 0 ? (d > 86400000 ? Math.floor(d / 86400000) + ' j ' + hms(d % 86400000) : hms(d)) : 'publiée');
  if (cpt) cpt.textContent = d > 0 ? (d > 86400000 ? Math.floor(d / 86400000) + ' j ' + hms(d % 86400000) : hms(d))
                                   : 'publiée il y a ' + Math.floor(-d / 60000) + ' min';
  var etat = '', txt = '';
  if (d <= 0){ etat = 'pub'; txt = 'HIGH IMPACT EVENT PUBLIÉ · ' + e.nom + ' · il y a ' + Math.floor(-d / 60000) +
               ' min — VOLATILITÉ MAXIMALE, ATTENDRE LA STABILISATION'; }
  else if (d <= 2 * 60000){ etat = 'z2'; txt = 'HIGH IMPACT EVENT IN ' + hms(d) + ' — AUCUNE POSITION · ' + e.nom; }
  else if (d <= CFG.zone * 60000){ etat = 'z15'; txt = 'HIGH IMPACT EVENT IN ' + Math.ceil(d / 60000) + ' MIN — SETUP À ÉVITER · ' + e.nom; }
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
var onglet = lire('onglet-or', 'synthese');
function activerOnglet(id){
  var trouve = false;
  document.querySelectorAll('.onglets [role=tab]').forEach(function(b){
    var on = b.getAttribute('data-tab') === id; if (on) trouve = true;
    b.setAttribute('aria-selected', on ? 'true' : 'false'); b.classList.toggle('actif', on);
  });
  if (!trouve){ id = 'synthese'; if (arguments.length < 2) return activerOnglet(id, true); }
  document.querySelectorAll('.tabpanel').forEach(function(p){ p.hidden = p.id !== 'tab-' + id; });
  onglet = id; ecrire('onglet-or', id);
}
document.querySelectorAll('.onglets [role=tab]').forEach(function(b){
  b.addEventListener('click', function(){ activerOnglet(b.getAttribute('data-tab')); });
});
document.addEventListener('keydown', function(ev){
  var t = ev.target.tagName; if (t === 'INPUT' || t === 'TEXTAREA' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  var n = ev.key === '0' ? 10 : parseInt(ev.key, 10), tabs = document.querySelectorAll('.onglets [role=tab]');
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
      titreBase = doc.title; chargerEv(); chargerCtx(); if (!PX.live) PX.prix = (CTX.trader && CTX.trader.ref) || CTX.prix; prixParDefaut(); majRappel(); rendreCarte(); activerOnglet(onglet); tick(); controler();
    })
    .catch(function(){});
}
if (CFG.site){ setInterval(rafraichir, CFG.refresh * 60000); }

/* Contrôle avant l'entrée : OUI / ATTENTION / NON, plan de trade, taille et probabilités */
var CTX = {};
function chargerCtx(){ try { CTX = JSON.parse(document.getElementById('ctx').textContent) || {}; } catch(e){ CTX = {}; } }
function nbFr(x, d){ return (x == null || isNaN(x)) ? 'n.d.' : Number(x).toLocaleString('fr-FR', {minimumFractionDigits:d, maximumFractionDigits:d}); }
function lirePrix(id){ var v = ($(id) || {}).value; if (!v) return null; v = parseFloat(String(v).replace(/[ \u00a0\u202f]/g, '').replace(',', '.')); return isNaN(v) ? null : v; }
function Phi(x){
  var t = 1 / (1 + 0.2316419 * Math.abs(x)), d = 0.3989423 * Math.exp(-x * x / 2);
  var p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.781478 + t * (-1.821256 + t * 1.330274))));
  return x > 0 ? 1 - p : p;
}
function pToucher(dist, sigma){ return (!sigma || !(dist > 0)) ? null : Math.min(1, 2 * (1 - Phi(dist / sigma))); }
var sensCtl = 0, dernierPlan = '';
function reglagesPerso(){ try { return JSON.parse(lire('reglages-or', '{}')) || {}; } catch(e){ return {}; } }
function regles(){ var P = {}, d = CTX.compte || {}, u = reglagesPerso(); Object.keys(d).forEach(function(k){ P[k] = d[k]; });
  Object.keys(u).forEach(function(k){ if (u[k] !== '' && u[k] != null && !isNaN(u[k])) P[k] = Number(u[k]); }); return P; }
function majRappel(){}
var dlg = $('#reglages'), bReg = $('#btn-reglages');
if (dlg && bReg){
  bReg.addEventListener('click', function(){
    var u = reglagesPerso();
    dlg.querySelectorAll('input[data-cle]').forEach(function(i){ var k = i.getAttribute('data-cle'); i.value = (u[k] != null ? u[k] : ''); });
    if (dlg.showModal) dlg.showModal(); else dlg.setAttribute('open', '');
  });
  $('#reg-ok').addEventListener('click', function(){
    var u = {};
    dlg.querySelectorAll('input[data-cle]').forEach(function(i){ var v = String(i.value).replace(',', '.'); if (v !== '' && !isNaN(v)) u[i.getAttribute('data-cle')] = Number(v); });
    ecrire('reglages-or', JSON.stringify(u)); majRappel(); controler(); if (dlg.close) dlg.close(); else dlg.removeAttribute('open');
  });
  $('#reg-defaut').addEventListener('click', function(){ dlg.querySelectorAll('input[data-cle]').forEach(function(i){ i.value = ''; }); });
}
function controler(){
  var res = $('#ctl-res'); if (!res || !sensCtl) return;
  var sens = sensCtl, mot = sens > 0 ? 'achat' : 'vente', P = regles(), sp = P.spread || 0;
  var prix = lirePrix('#ctl-prix') || PX.prix || CTX.prix, stopU = lirePrix('#ctl-stop'), cibleU = lirePrix('#ctl-cible');
  if (!prix){ res.innerHTML = '<p class="pied">Prix indisponible : tape le prix de ta plateforme.</p>'; return; }
  var R = [], atr = CTX.atr || null, sigma = CTX.sigma || null;
  function ajout(niv, txt){ R.push([niv, txt]); }

  /* Niveaux utiles : ceux dans le sens du trade (objectifs) et derrière (protection) */
  var favor = [], prot = [];
  (CTX.niveaux || []).forEach(function(n){
    var d = (n.v - prix) * sens;
    if (d > 0.05) favor.push({lib:n.lib, v:n.v, d:d}); else if (d < -0.05) prot.push({lib:n.lib, v:n.v, d:-d});
  });
  favor.sort(function(x, y){ return x.d - y.d; }); prot.sort(function(x, y){ return x.d - y.d; });
  var minD = atr ? 0.15 * atr : 1;

  /* Stop : le tien, sinon un niveau de référence derrière la protection la plus proche */
  var stop, stopLib, stopPropose = false;
  var S15 = (CTX.structure || {})['15 min'];
  var pivotStruct = S15 ? (sens > 0 ? {v: S15.creux, lab: S15.l} : {v: S15.sommet, lab: S15.h}) : null;
  var dStruct = pivotStruct ? (prix - pivotStruct.v) * sens : null;
  if (stopU){ stop = stopU; stopLib = 'ton stop'; }
  else if (pivotStruct && dStruct >= minD && (!atr || dStruct <= 1.2 * atr)){
    stop = pivotStruct.v - sens * ((atr ? 0.05 * atr : 0.5) + sp);
    stopLib = (sens > 0 ? 'sous le dernier creux 15 min (' : 'au-dessus du dernier sommet 15 min (') + (pivotStruct.lab || '?') + ')';
    stopPropose = true;
  }
  else {
    var pr = prot.filter(function(n){ return n.d >= minD && (!atr || n.d <= 1.2 * atr); })[0];
    if (pr){ stop = pr.v - sens * ((atr ? 0.05 * atr : 0.5) + sp); stopLib = (sens > 0 ? 'sous ' : 'au-dessus de ') + pr.lib; }
    else if (atr){ stop = prix - sens * 0.35 * atr; stopLib = 'stop de volatilité (0,35 ATR)'; }
    stopPropose = true;
    /* Une poche de stops juste derrière le stop proposé sera balayée : on place le stop au-delà */
    (CTX.poches || []).forEach(function(pch){
      var dPoche = (prix - pch.v) * sens, dStop = stop != null ? (prix - stop) * sens : null;
      if (dStop != null && atr && dPoche > dStop && dPoche - dStop < 0.3 * atr){
        stop = pch.v - sens * (0.1 * atr + sp); stopLib = 'au-delà de la poche de stops (' + nbFr(pch.v, 1) + ')';
      }
    });
  }
  /* Objectif : le tien, sinon le prochain niveau dans le sens du trade */
  var cible, cibleLib, ciblePropose = false, cibles = [], ref = prix;
  favor.forEach(function(n){ if (n.d >= minD && Math.abs(n.v - ref) >= minD && cibles.length < 2){ cibles.push(n); ref = n.v; } });
  if (cibleU){ cible = cibleU; cibleLib = 'ton objectif'; }
  else if (cibles.length){ cible = cibles[0].v; cibleLib = cibles[0].lib; ciblePropose = true; }
  else if (sigma){ cible = prix + sens * sigma; cibleLib = 'haut de la fourchette probable'; ciblePropose = true; }

  /* Contexte de marché et calendrier */
  if (!ouvert()) ajout('non', 'Marché de l’or fermé.');
  var now = Date.now(), e = prochain(now);
  if (e){
    var m = (e.ts - now) / 60000, av = CTX.zone ? CTX.zone[0] : 15, ap = CTX.zone ? CTX.zone[1] : 10;
    if (m <= av && m >= -ap) ajout('non', 'Zone news : ' + e.nom + (m >= 0 ? ' dans ' + Math.ceil(m) + ' min.' : ' publiée il y a ' + Math.floor(-m) + ' min.'));
    else if (m > 0 && m <= 60) ajout('att', e.nom + ' dans ' + Math.round(m) + ' min : ton trade doit être clôturé avant, sinon attends.');
  }
  var age = CTX.maj ? Math.round((Date.now() - new Date(CTX.maj).getTime()) / 60000) : null;
  if (age != null && age > 45 && ouvert()) ajout('att', 'Analyses vieilles de ' + age + ' min : niveaux et tendance peut-être dépassés.');
  var b = CTX.biais || {}, bs = b.verdict === 'Haussier' ? 1 : (b.verdict === 'Baissier' ? -1 : 0);
  var bsc = (b.total > 0 ? '+' : '') + (b.total || 0) + ' sur ' + (b.n || 0);
  if (bs === sens) ajout('ok', 'Aligné avec le biais fondamental (' + b.verdict.toLowerCase() + ', ' + bsc + ').');
  else if (bs === -sens) ajout('att', 'Contre le biais fondamental (' + b.verdict.toLowerCase() + ', ' + bsc + ').');
  else ajout('info', 'Biais fondamental neutre : pas de soutien de fond.');
  var v = CTX.vue || {};
  if (v.sens === sens) ajout('ok', 'Dans le sens de la vue de marché (' + (v.direction || '').toLowerCase() + ', conviction ' + v.conv + ').');
  else if (v.sens === -sens) ajout(v.conv === 'faible' ? 'info' : 'att', 'Contre la vue de marché (' + (v.direction || '').toLowerCase() + ', conviction ' + v.conv + ').');
  else ajout('info', 'Vue de marché neutre : range probable, vise des cibles courtes.');
  var S = CTX.structure || {}, s1 = S['1 heure'], s4 = S['4 heures'], s15 = S['15 min'], t = CTX.tend || {};
  var t1 = s1 ? s1.sens : (t['1 heure'] || 0), t4 = s4 ? s4.sens : (t['4 heures'] || 0);
  function etatTxt(x, nom){ return x ? nom + ' ' + x.etat.toLowerCase() + ' (' + (x.h || '?') + ' + ' + (x.l || '?') + ')' : ''; }
  var resume = [etatTxt(s1, '1 h'), etatTxt(s4, '4 h')].filter(Boolean).join(', ');
  if (t1 === sens && t4 === sens) ajout('ok', 'Structure dans ton sens : ' + resume + '.');
  else if (t1 === -sens && t4 === -sens) ajout('att', 'Contre la structure : ' + resume + ' ; trade de retournement, cible courte.');
  else ajout('info', 'Structure partagée : ' + (resume || 'n.d.') + '.');
  if (s15){
    if (sens > 0 && s15.l === 'HL' && t1 >= 0) ajout('ok', '15 min : dernier creux HL (plus haut que le précédent) : le setup de continuation à l’achat.');
    else if (sens > 0 && s15.l === 'LL') ajout('att', '15 min : dernier creux LL (plus bas) : la structure courte baisse encore, attends un HL avant d’acheter.');
    else if (sens < 0 && s15.h === 'LH' && t1 <= 0) ajout('ok', '15 min : dernier sommet LH (plus bas que le précédent) : le setup de continuation à la vente.');
    else if (sens < 0 && s15.h === 'HH') ajout('att', '15 min : dernier sommet HH (plus haut) : la structure courte monte encore, attends un LH avant de vendre.');
    if (s15.etat === 'Compression') ajout('info', '15 min en compression (LH + HL) : attends la cassure du triangle.');
    if (s15.evt && s15.evt.indexOf('CHoCH') === 0) ajout(((s15.evt.indexOf('haussier') > 0) === (sens > 0)) ? 'ok' : 'att', '15 min : ' + s15.evt + ' (changement de caractère) ' + (((s15.evt.indexOf('haussier') > 0) === (sens > 0)) ? 'dans ton sens.' : 'contre ton trade.'));
  }
  if (bs === -sens && v.sens === -sens && t4 === -sens) ajout('non', 'Tout est contre ce trade : fondamental, vue de marché et tendance 4 h.');
  /* Playbook technico-fondamental */
  var pb = CTX.playbook;
  if (pb){
    if (pb.sens === sens) ajout('ok', 'Dans le sens du playbook : ' + pb.titre + '.');
    else if (pb.sens === -sens) ajout(pb.faible ? 'info' : 'att', 'Contre le playbook du moment : ' + pb.titre + '.');
    else if (pb.cas === 'range') ajout('info', 'Marché en range : vise le POC ou le VWAP, pas une cassure.');
    else if (pb.cas === 'compression_annonce') ajout('att', 'Compression avant une annonce forte : le playbook conseille d’attendre la cassure après la publication.');
    else if (pb.cas.indexOf('divergence') === 0) ajout('info', pb.titre + ' : scalp court seulement, cible proche.');
  }
  /* Zone de confluence : entrer là où le prix réagit, pas au milieu du mouvement */
  var confs = CTX.confluences || [], zDans = null, zProche = null;
  confs.forEach(function(z){
    var tolZ = atr ? 0.03 * atr : 0.5;
    if (prix >= z.bas - tolZ && prix <= z.haut + tolZ){ if (!zDans || z.score > zDans.score) zDans = z; }
    var d = Math.min(Math.abs(prix - z.bas), Math.abs(prix - z.haut));
    if (!zProche || d < zProche.d) zProche = {z: z, d: d};
  });
  if (zDans){
    ajout(zDans.score >= 5 ? 'ok' : 'info', 'Entrée sur une zone de confluence (score ' + nbFr(zDans.score, 1) + ' : ' + zDans.elements.join(', ') + ') : attends la réaction du prix sur la zone.');
  } else if (zProche && atr && zProche.d > 0.25 * atr){
    ajout('att', 'Entrée loin de toute zone de confluence (la plus proche à ' + nbFr(zProche.d, 1) + ' $, ' + nbFr(zProche.z.bas, 1) + '–' + nbFr(zProche.z.haut, 1) + ') : risque d’entrer au milieu du mouvement.');
  }
  /* Position par rapport au VWAP et à ses bandes (une seule lecture, selon le contexte) */
  if (CTX.vwap){
    var zv = CTX.vwap_sigma ? (prix - CTX.vwap) / CTX.vwap_sigma : ((prix - CTX.vwap) > 0 ? 0.5 : -0.5), zs = zv * sens;
    var ztxt = CTX.vwap_sigma ? ' (' + (zv > 0 ? '+' : '') + nbFr(zv, 1) + ' σ du VWAP)' : '';
    if (zs >= 2) ajout('att', (sens > 0 ? 'Achat' : 'Vente') + ' en extension' + ztxt + ' : zone où les desks prennent leurs profits.');
    else if (zs > 0) ajout('ok', 'Prix du bon côté du VWAP' + ztxt + ' : le flux du jour va dans ton sens.');
    else if (zs > -1) ajout('info', 'Prix de l’autre côté du VWAP' + ztxt + ' : ' + mot + ' contre le flux du jour ; attends une reprise du VWAP ou une zone forte.');
    else if (CTX.playbook && CTX.playbook.sens === sens) ajout('ok', (sens > 0 ? 'Achat en décote' : 'Vente en prime') + ztxt + ' dans le sens du playbook : l’entrée sur repli typique des desks.');
    else ajout('info', (sens > 0 ? 'Achat en décote' : 'Vente en prime') + ztxt + ' contre le flux du jour : rebond possible vers le VWAP, cible courte.');
  }
  if (CTX.foule != null){
    var avecFoule = sens > 0 ? CTX.foule : 100 - CTX.foule, verbe = sens > 0 ? 'achètes' : 'vends';
    if (avecFoule >= 65) ajout('att', 'Tu ' + verbe + ' avec la foule (' + nbFr(avecFoule, 0) + ' % des traders OANDA dans ton sens) : carburant limité, risque de purge.');
    else if (avecFoule <= 35) ajout('ok', 'Tu ' + verbe + ' contre la foule (' + nbFr(avecFoule, 0) + ' % des traders OANDA dans ton sens) : le positionnement joue pour toi.');
  }
  (CTX.inter || []).forEach(function(l){
    if (l.signal === 'neutre') return;
    var pour = (l.signal === 'force') === (sens > 0);
    ajout(pour ? 'ok' : 'att', l.lib + ' : ' + (l.signal === 'force' ? 'force' : 'faiblesse') + ' relative de l’or face au dollar et aux taux (' +
      (l.residu > 0 ? '+' : '') + nbFr(l.residu, 2) + ' %)' + (pour ? ', dans ton sens.' : ', contre ton trade.'));
  });
  if (CTX.pct_atr != null){
    if (CTX.pct_atr >= 100) ajout('att', 'Amplitude du jour déjà dépassée (' + nbFr(CTX.pct_atr, 0) + ' % de l’ATR) : extension moins probable.');
    else if (CTX.pct_atr >= 80) ajout('info', nbFr(CTX.pct_atr, 0) + ' % de l’amplitude habituelle déjà faite.');
  }
  if (CTX.slots && CTX.moy_jour){
    var hp = parties('Europe/Paris').h, sl = CTX.slots[Math.floor(hp * 4) % 96];
    if (sl != null && sl < 0.6 * CTX.moy_jour) ajout('info', 'Créneau habituellement calme : mouvement lent à attendre.');
    else if (sl != null && sl > 1.5 * CTX.moy_jour) ajout('info', 'Créneau habituellement très agité : stops à distance du bruit.');
  }
  if (favor.length && atr && favor[0].d < 0.25 * atr)
    ajout('att', favor[0].lib + ' à ' + nbFr(favor[0].d, 1) + ' $ seulement : zone de réaction juste devant toi.');

  /* Concepts avancés : premium / discount, balayages, déclencheur, divergences, kill zones */
  var AV = CTX.avances;
  if (AV){
    if (AV.pd){
      var bonne = sens > 0 ? 'discount' : 'premium', mauvaise = sens > 0 ? 'premium' : 'discount';
      if (AV.pd.zone === bonne) ajout('ok', (sens > 0 ? 'Achat en discount' : 'Vente en premium') + ' (' + nbFr(AV.pd.pos, 0) + ' % du range 1 h) : le bon côté du range.');
      else if (AV.pd.zone === mauvaise) ajout('att', (sens > 0 ? 'Achat en premium' : 'Vente en discount') + ' (' + nbFr(AV.pd.pos, 0) + ' % du range 1 h) : les pros ' + (sens > 0 ? 'achètent en discount' : 'vendent en premium') + '.');
    }
    (AV.balayages || []).slice(0, 2).forEach(function(bl){
      var txt = 'Balayage ' + (bl.sens > 0 ? 'sous ' : 'au-dessus de ') + bl.lib.toLowerCase() + ' (' + nbFr(bl.niveau, 1) + ') il y a ' + (bl.age + 1) + ' bougie(s) 15 min';
      if (bl.sens === sens) ajout('ok', txt + ' : liquidité prise dans le bon sens, setup de retournement en ta faveur.');
      else ajout('att', txt + ' : rejet contre ton trade.');
    });
    if (AV.declencheur){
      if (AV.declencheur.sens === sens) ajout('ok', 'Déclencheur : ' + AV.declencheur.nom.toLowerCase() + ' sur la dernière bougie 15 min.');
      else ajout('att', 'Dernière bougie 15 min contraire : ' + AV.declencheur.nom.toLowerCase() + '.');
    }
    (AV.divergences || []).forEach(function(dv){
      if (dv.sens === -sens) ajout('att', 'RSI ' + dv.nom + ' : ' + dv.texte + ', essoufflement contre ton trade.');
      else ajout('ok', 'RSI ' + dv.nom + ' : ' + dv.texte + ', dans ton sens.');
    });
    ajout('info', AV.kz ? 'En kill zone de ' + AV.kz + ' : liquidité et mouvements directionnels maximaux.' : 'Hors kill zone : mouvements souvent plus lents, cibles plus courtes.');
  }

  /* Poches de stops : chasse aux stops côté protection, aimant côté objectif */
  var aimant = false;
  (CTX.poches || []).slice().sort(function(x, y){ return Math.abs(x.v - prix) - Math.abs(y.v - prix); }).forEach(function(pch){
    var d = (pch.v - prix) * sens;
    if (d > 0 && atr && d <= 1.5 * atr && !aimant && (aimant = true)) ajout('info', pch.lib + ' à ' + nbFr(pch.v, 1) + ' (' + nbFr(d, 1) + ' $) : cible naturelle, le prix est souvent attiré vers la liquidité.');
    if (d < 0 && stop != null && atr){
      var dPoche = -d, dStop = (prix - stop) * sens;
      if (dStop < dPoche && dPoche - dStop < 0.3 * atr)
        ajout('att', 'Ton stop (' + nbFr(stop, 1) + ') est juste avant une poche de stops (' + nbFr(pch.v, 1) + ') : zone typique de chasse aux stops. Place-le au-delà (' + nbFr(pch.v - sens * 0.1 * atr, 1) + ') ou attends le balayage.');
      else if (dStop > dPoche && dStop - dPoche < 0.6 * atr)
        ajout('ok', 'Stop au-delà de la poche de stops (' + nbFr(pch.v, 1) + ') : protégé d’un balayage.');
    }
  });

  /* Plan de trade : risque, gain, taille, probabilités */
  var rDist = stop != null ? (prix - stop) * sens : null, gDist = cible != null ? (cible - prix) * sens : null;
  var plan = '', proba = '', taille = null, rr = null;
  if (rDist != null && rDist <= 0) ajout('non', 'Stop du mauvais côté du prix.');
  else if (gDist != null && gDist <= 0) ajout('non', 'Objectif du mauvais côté du prix.');
  else if (rDist != null){
    var rEff = rDist + sp, gEff = gDist != null ? gDist - sp : null;
    if (atr && rDist < 0.15 * atr) ajout('att', 'Stop très serré (' + nbFr(rDist, 1) + ' $, moins de 15 % de l’ATR) : risque d’être sorti par le bruit.');
    if (sigma && rDist > 1.5 * sigma) ajout('info', 'Stop large face à la volatilité attendue sur ' + (CTX.horizon || 4) + ' h (±' + nbFr(sigma, 0) + ' $).');
    var risque = (P.capital || 0) * (P.risque_pct || 0) / 100;
    taille = Math.floor(risque / (rEff * (P.once_par_lot || 100)) * 100) / 100;
    if (taille < 0.01) ajout('att', 'Stop trop large pour ton risque par trade : même 0,01 lot dépasse ' + nbFr(risque, 0) + ' $.');
    if (gEff != null){
      rr = gEff / rEff;
      if (rr < 1) ajout('att', 'Gain / risque de ' + nbFr(rr, 2) + ' (spread compris) : il faut gagner plus d’une fois sur deux.');
      else ajout('ok', 'Gain / risque de ' + nbFr(rr, 2) + ' (spread compris).');
      var pAvant = rEff / (rEff + gEff), pT = pToucher(gDist, sigma);
      proba = '<p class="repere"><b>Repère statistique</b> (marche au hasard, volatilité des 60 derniers jours) : sans avantage, l’objectif serait atteint avant le stop '
        + nbFr(pAvant * 100, 0) + ' % du temps' + (pT != null ? ', et touché dans les ' + (CTX.horizon || 4) + ' h ' + nbFr(pT * 100, 0) + ' % du temps' : '')
        + '. Ton setup doit donc gagner <b>plus de ' + nbFr(pAvant * 100, 0) + ' %</b> de ses trades à ce ratio pour être rentable.</p>';
    }
    plan = '<div class="plan-trade">' +
      '<div class="pt"><span>Stop' + (stopPropose ? ' proposé' : '') + '</span><b>' + nbFr(stop, 1) + '</b><small>' + esc(stopLib) + ' · ' + nbFr(rDist, 1) + ' $</small></div>' +
      '<div class="pt"><span>Objectif' + (ciblePropose ? ' proposé' : '') + '</span><b>' + (cible != null ? nbFr(cible, 1) : 'n.d.') + '</b><small>' + esc(cibleLib || '') + (gDist != null ? ' · ' + nbFr(gDist, 1) + ' $' : '') + '</small></div>' +
      '<div class="pt"><span>Gain / risque</span><b>' + (rr != null ? nbFr(rr, 2) : 'n.d.') + '</b><small>spread de ' + nbFr(sp, 2) + ' $ inclus</small></div>' +
      '<div class="pt"><span>Taille</span><b>' + nbFr(Math.max(taille, 0), 2) + ' lot</b><small>risque ' + nbFr(risque, 0) + ' $ (' + nbFr(P.risque_pct, 1) + ' %)</small></div>' +
      '</div>' + (cibles.length > 1 && !cibleU ? '<p class="pied">Objectif suivant : ' + esc(cibles[1].lib) + ' (' + nbFr(cibles[1].v, 1) + ').</p>' : '') +
      ((stopPropose || ciblePropose) ? '<p class="pied">Stop et objectif proposés = niveaux de référence du terminal ; ta structure technique décide.</p>' : '');
  }

  /* Verdict et note du setup */
  var n = {non:0, att:0, ok:0, info:0}; R.forEach(function(x){ n[x[0]]++; });
  var verdict = n.non ? ['non', 'NON'] : (n.att ? ['att', 'ATTENTION'] : ['oui', 'OUI']);
  var score = 60 + 8 * n.ok - 12 * n.att + (rr != null ? (rr >= 1.5 ? 10 : (rr < 1 ? -10 : 0)) : 0);
  var note = n.non ? 'D' : (score >= 80 ? 'A' : (score >= 60 ? 'B' : 'C'));
  var groupes = [['non', 'Bloquant', '✕'], ['att', 'Vigilance', '!'], ['ok', 'Favorable', '✓'], ['info', 'À savoir', '·']];
  var liste = groupes.map(function(g){
    var l = R.filter(function(x){ return x[0] === g[0]; }); if (!l.length) return '';
    return '<div class="groupe"><div class="k">' + g[1] + ' (' + l.length + ')</div><ul class="raisons">' +
      l.map(function(x){ return '<li><span class="i-' + x[0] + '">' + g[2] + '</span><span>' + esc(x[1]) + '</span></li>'; }).join('') + '</ul></div>';
  }).join('');
  var ia = CTX.ia && CTX.ia[mot] ? '<div class="avis-ia"><div class="k">L’analyste IA pour une ' + mot + ' · ' + esc(CTX.ia.quand || '') + '</div><p>' + esc(CTX.ia[mot]) + '</p></div>' : '';
  res.innerHTML = '<div class="verdict-ctl ' + verdict[0] + '">' + verdict[1] + ' <small>' + mot + ' à ' + nbFr(prix, 2) + ' · note ' + note + '</small></div>' +
    '<p class="pied">' + n.non + ' bloquant, ' + n.att + ' vigilance, ' + n.ok + ' favorable.' + (age != null ? ' Niveaux du relevé d’il y a ' + age + ' min.' : '') + '</p>' +
    plan + proba + liste + ia +
    '<div class="ctl-actions"><button type="button" id="copier-plan">Copier le plan</button></div>' +
    '<p class="pied">Contrôle du contexte, pas un conseil : la décision reste la tienne.</p>';
  dernierPlan = [mot.toUpperCase() + ' XAU/USD · contrôle ' + verdict[1] + ' (note ' + note + ') · ' + new Date().toLocaleString('fr-FR'),
    'Entrée ' + nbFr(prix, 2) + (stop != null ? ' · stop ' + nbFr(stop, 1) + ' (' + stopLib + ')' : '') + (cible != null ? ' · objectif ' + nbFr(cible, 1) + ' (' + cibleLib + ')' : ''),
    (rr != null ? 'Gain / risque ' + nbFr(rr, 2) : '') + (taille != null ? ' · taille ' + nbFr(Math.max(taille, 0), 2) + ' lot' : '')]
    .concat(R.map(function(x){ return '- ' + x[1]; })).join('\\n');
}
document.querySelectorAll('.ctl-boutons button').forEach(function(bt){
  bt.addEventListener('click', function(){
    sensCtl = parseInt(bt.getAttribute('data-sens'), 10);
    document.querySelectorAll('.ctl-boutons button').forEach(function(x){ x.classList.toggle('on', x === bt); });
    controler();
  });
});
['#ctl-prix', '#ctl-stop', '#ctl-cible'].forEach(function(id){ var el = $(id); if (el) el.addEventListener('input', controler); });
document.addEventListener('click', function(ev){
  if (!ev.target || ev.target.id !== 'copier-plan') return;
  var bt = ev.target;
  function fait(){ bt.textContent = 'Plan copié'; setTimeout(function(){ bt.textContent = 'Copier le plan'; }, 2000); }
  if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(dernierPlan).then(fait, function(){});
});
document.addEventListener('keydown', function(ev){
  var tg = ev.target.tagName; if (tg === 'INPUT' || tg === 'TEXTAREA' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  var k = (ev.key || '').toLowerCase();
  if (dlg && dlg.open) return;
  if (k === 'a' || k === 'v'){ var bt = $('.ctl-boutons .' + (k === 'a' ? 'achat' : 'vente')); if (bt) bt.click(); }
});
setInterval(function(){ if (sensCtl) controler(); }, 20000);
function prixParDefaut(){ var el = $('#ctl-prix'); if (el && (PX.prix || CTX.prix)){ el.placeholder = nbFr(PX.prix || CTX.prix, 2); el.title = 'Prix XAU/USD en direct (XAUS) : tu peux le remplacer par celui de ta plateforme'; } }

/* Prix XAU/USD en direct : même source (XAUS) que la référence de l'analyse */
var PX = {prix: (CTX.trader && CTX.trader.ref) || CTX.prix, t: null, live: false};
function affichePrix(){
  var el = $('#px-live'), bg = $('#px-badge'), ag = $('#px-age');
  if (el && PX.prix) el.textContent = nbFr(PX.prix, 2);
  var tp = $('#tr-px'); if (tp && PX.prix) tp.textContent = nbFr(PX.prix, 2);
  [bg, $('#tr-badge')].forEach(function(x){ if (x){ x.textContent = PX.live ? 'LIVE' : 'DIFFÉRÉ'; x.className = 'fr ' + (PX.live ? 'live' : 'differe'); } });
  if (ag) ag.textContent = PX.t ? 'il y a ' + Math.max(0, Math.round((Date.now() - PX.t) / 1000)) + ' s · XAUS' : 'référence de l’analyse';
  document.querySelectorAll('.niv-c td.dist').forEach(function(td){
    var v = parseFloat(td.getAttribute('data-v')), d = v - PX.prix;
    td.textContent = (d > 0 ? '+' : '') + nbFr(d, 1); td.className = 'num dist flat';
  });
  var cp = $('#ctl-prix'); if (cp) cp.placeholder = nbFr(PX.prix, 2) + (PX.live ? ' (live)' : '');
  majTrader();
}
function majPrix(){
  if (!window.fetch) return;
  fetch('https://xaus.com/api/v1/spot?compact=1', {cache: 'no-store'})
    .then(function(r){ if (!r.ok) throw 0; return r.json(); })
    .then(function(j){
      var p = Number(j.spot_usd_oz); if (!(p > 0)) throw 0;
      var st = j.data_state || {};
      PX = {prix: p, t: new Date(st.as_of || j.price_as_of || j.updated_at || Date.now()).getTime(), live: st.status !== 'stale'};
      affichePrix();
    })
    .catch(function(){
      fetch('https://api.goldprice.dev/v1/prices?symbol=XAU-USD-SPOT', {cache: 'no-store'})
        .then(function(r){ if (!r.ok) throw 0; return r.json(); })
        .then(function(j){ var p = Number(((j.symbols || [])[0] || {}).price); if (!(p > 0)) throw 0;
          PX = {prix: p, t: Date.now(), live: true}; affichePrix(); })
        .catch(function(){ PX.live = false; affichePrix(); });
    });
}

/* Fiche Trader : signal et action recalculés en direct */
function poseTuile(el, val, sub, cls){
  if (!el) return;
  el.className = 'tu ' + cls + (el.id === 'tr-action' ? ' large' : '');
  el.querySelector('b').textContent = val; el.querySelector('small').textContent = sub || '';
}
function majTrader(){
  var T = CTX.trader || {}, sig = $('#tr-signal'), act = $('#tr-action'), nw = $('#tr-news');
  if (!sig || !act) return;
  var px = PX.prix, now = Date.now(), e = prochain(now), A = CTX.avert || 30;
  if (nw){
    if (e){ var dn = e.ts - now; nw.querySelector('small').textContent = dn > 0 ? 'dans ' + (dn > 86400000 ? Math.floor(dn / 86400000) + ' j ' + hms(dn % 86400000) : hms(dn)) : 'publiée';
            nw.className = 'tu ' + (dn > 0 && dn <= A * 60000 ? 'warn' : ''); }
  }
  if (!ouvert()){
    var ny = parties('America/New_York'), weekend = ny.j === 'Sat' || (ny.j === 'Fri' && ny.h >= 17) || (ny.j === 'Sun' && ny.h < 18);
    poseTuile(sig, 'aucun', 'marché fermé', 'flat');
    poseTuile(act, 'Marché fermé', weekend ? 'réouverture dimanche soir (minuit, heure de Paris)' : 'pause quotidienne d’une heure : réouverture à minuit (heure de Paris)', 'flat');
    return;
  }
  if (e){
    var m = (e.ts - now) / 60000;
    if (m <= A && m >= -10){
      poseTuile(sig, 'aucun', 'annonce majeure', 'warn');
      poseTuile(act, m >= 0 ? 'HIGH IMPACT EVENT IN ' + Math.ceil(m) + ' MIN — SETUP À ÉVITER' : 'HIGH IMPACT EVENT PUBLIÉ — ATTENDRE', e.nom, 'warn');
      return;
    }
  }
  var sens = T.sens || 0, z = T.zone;
  if (!sens || !z){
    var msg = {attente: 'Attendre : pas d’avantage net', range: 'Jouer les extrémités du range', compression_annonce: 'Attendre la cassure après l’annonce',
               divergence_haussiere: 'Attendre un CHoCH haussier en 1 h', divergence_baissiere: 'Attendre un CHoCH baissier en 1 h'}[T.cas] || 'Attendre un setup clair';
    poseTuile(sig, 'aucun', '', 'flat'); poseTuile(act, msg, T.setup || '', 'warn'); return;
  }
  if ((px - T.stop) * sens < 0){
    poseTuile(sig, 'aucun', '', 'flat'); poseTuile(act, 'Setup invalidé', 'prix au-delà du stop ' + nbFr(T.stop, 1) + ' : attendre le prochain playbook', 'warn'); return;
  }
  var tol = 0.5;
  if (px >= z.bas - tol && px <= z.haut + tol){
    var c = T.confirmations_presentes || [];
    if (c.length){
      poseTuile(sig, sens > 0 ? 'ACHAT' : 'VENTE', 'confirmation : ' + c.join(', '), sens > 0 ? 'up' : 'down');
      poseTuile(act, 'Signal présent : vérifie le contrôle et exécute ton plan', 'confirmation sur la dernière bougie 15 min', sens > 0 ? 'up' : 'down');
    } else {
      poseTuile(sig, 'en attente', 'prix dans la zone', 'warn');
      poseTuile(act, 'Attendre la confirmation', 'bougie 15 min de rejet (avalement, pin bar) ou ' + (sens > 0 ? 'HL' : 'LH') + ' en 15 min', 'warn');
    }
    return;
  }
  var ecart = sens > 0 ? px - z.haut : z.bas - px;
  if (ecart > 0){
    poseTuile(sig, 'aucun', '', 'flat');
    poseTuile(act, 'Attendre un retour dans la zone ' + nbFr(z.bas, 1) + '–' + nbFr(z.haut, 1), 'encore ' + nbFr(ecart, 1) + ' $ · ne pas courir après le prix', 'warn');
  } else {
    poseTuile(sig, 'en attente', 'prix au-delà de la zone, stop intact', 'warn');
    poseTuile(act, 'Attendre une reprise de la zone', 'clôture 15 min de retour dans ' + nbFr(z.bas, 1) + '–' + nbFr(z.haut, 1), 'warn');
  }
}

/* Modes Trader (simple) et Analyst (complet) */
function appliquerMode(m){
  document.body.classList.toggle('mode-trader', m === 'trader');
  var bm = $('#btn-mode'); if (bm) bm.textContent = 'Mode : ' + (m === 'trader' ? 'Trader' : 'Analyst');
  ecrire('mode-or', m);
}
var bMode = $('#btn-mode');
if (bMode) bMode.addEventListener('click', function(){ appliquerMode(document.body.classList.contains('mode-trader') ? 'analyst' : 'trader'); });
appliquerMode(lire('mode-or', 'trader'));

/* Badges « il y a X min » */
function majBadges(){
  document.querySelectorAll('.fr.rel').forEach(function(el){
    var t = new Date(el.getAttribute('data-t')).getTime(); if (!t) return;
    var m = Math.max(0, Math.round((Date.now() - t) / 60000));
    el.textContent = 'il y a ' + (m < 60 ? m + ' min' : Math.floor(m / 60) + ' h ' + pad(m % 60));
    el.classList.toggle('differe', m > 45);
  });
}

/* Masque le texte d'attente des flux en direct une fois chargés */
function fluxCharges(){ document.querySelectorAll('.tv').forEach(function(el){ if (el.querySelector('iframe')) el.classList.add('charge'); }); }
setInterval(fluxCharges, 1500);

window.terminalOr = {ctx: function(){ return CTX; }, px: function(){ return PX; }};  /* accès de diagnostic */
chargerEv(); chargerCtx(); if (!PX.prix) PX.prix = (CTX.trader && CTX.trader.ref) || CTX.prix; prixParDefaut(); majRappel(); affichePrix(); majPrix(); setInterval(majPrix, 60000); activerOnglet(onglet); rendreCarte(); majBoutons(); tick(); setInterval(tick, 1000);
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


def ia_quand(ia):
    try:
        return date_fr(datetime.fromisoformat(ia["heure"]), True)
    except Exception:
        return ""


def bloc_ia(ia):
    if not ia or not ia.get("texte"):
        return ('<section id="analyste"><h2>Analyste IA</h2><p class="vide">Inactif : ajoute une clé Gemini gratuite '
                '(secret GitHub GEMINI_API_KEY) ou une clé Claude (ANTHROPIC_API_KEY) pour recevoir une analyse rédigée '
                'toutes les 2 heures.</p></section>')
    j = ia.get("json")
    tete = (f'<p class="legende">Rédigée par {esc(ia.get("fournisseur", "Claude"))} ({esc(ia.get("modele", ""))}) le {ia_quand(ia)}'
            f'{", " + esc(ia["raison"]) if ia.get("raison") else ""}, à partir des données de cette page. '
            f'Relis-la avec ton propre jugement.</p>')
    if not j:
        return f'<section id="analyste"><h2>Analyse du jour (IA)</h2>{tete}<div class="ia">{mini_markdown(ia["texte"])}</div></section>'
    ton = {"d'accord": "up", "en désaccord": "down"}.get(j["accord"].lower(), "flat")
    liste = lambda xs: "".join(f"<li>{esc(x)}</li>" for x in xs)
    return (f'<section id="analyste"><h2>Analyse du jour (IA)</h2>{tete}<div class="ia">'
            f'<p class="ia-titre">{esc(j["titre"])}</p><p>{esc(j["lecture"])}</p>'
            f'<p><span class="chip {ton}">Avis sur la vue du terminal : {esc(j["accord"])}</span> '
            f'<span class="chip">Sa lecture : {esc(j["direction"])}</span> {esc(j["accord_explication"])}</p>'
            f'<div class="grille2"><div><h4>Les forces en présence</h4><ul>{liste(j["forces"])}</ul>'
            f'<h4>Scénarios sur les annonces</h4><ul>{liste(j["annonces"])}</ul></div>'
            f'<div><h4>Pour un achat</h4><p>{esc(j["achat"])}</p><h4>Pour une vente</h4><p>{esc(j["vente"])}</p>'
            f'<h4>Ce qui invaliderait la lecture</h4><ul>{liste(j["invalidation"])}</ul>'
            f'<h4>Vigilance pour la séance</h4><ul>{liste(j["vigilance"])}</ul></div></div></div></section>')


def couleur_z(z, effet):
    if z is None:
        return ""
    alpha = min(abs(z), 3) / 3 * 0.55 + 0.05
    rgb = "152,163,174" if effet == 0 else ("93,187,141" if z * effet > 0 else "227,104,91")
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
    dec = (f" ; prix convertis en XAU/USD spot, écart future-spot de {nb(DECALAGE_CFD - AJUST, 1)} $ retiré"
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
            f'Tirets bleus : VWAP du jour. Bandes bleutées : zones de confluence. Bandes vertes ou rouges : FVG 15 min '
            f'non comblés. Étiquettes : structure HH, HL, LH, LL.</p>'
            f'{graphique_bougies(s, zones=(a.get("technique") or {}).get("confluences"), fvg=((((a.get("technique") or {}).get("avances") or {}).get("fvg") or {}).get("15 min") or []))}'
            f'<div class="grille3" style="margin-top:18px"><div><h3 style="margin-top:0">Niveaux clés, du plus haut au plus bas</h3>'
            f'<table class="niveaux">{lignes}</table></div>'
            f'<div><h3 style="margin-top:0">Séances (heure de Paris)</h3><table class="m"><tr><th></th><th>Haut</th>'
            f'<th>Bas</th><th>Var. $</th></tr>{ses}</table></div>'
            f'<div><h3 style="margin-top:0">Repères de volatilité</h3>{stats}</div></div></section>')


def derniere_date(s):
    try:
        return s.dropna().index[-1]
    except Exception:
        return None


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
    return (f'<section id="fed"><h2>Fed, taux et liquidité {fraicheur("daily", derniere_date(f.get("reel10")))}</h2><p class="pourquoi">L\'or ne rapporte rien : son premier '
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
        lect = (f'<div class="note" style="border-left-color:var(--steel);background:rgba(152,163,174,.08)">'
                f'<b>{esc(c["lecture"][0])}.</b> {esc(c["lecture"][1])}</div>') if c.get("lecture") else ""
        cot_html = (f'<h3 style="margin-top:0">Positionnement COMEX {fraicheur("weekly", c["date"])}</h3>'
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
    gld_html = (f'<h3>ETF GLD, avoirs physiques {fraicheur("daily", g["date"])}</h3>'
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
    ("Le mode Trader", [
        "La page principale résume tout en une fiche : Bias (le fondamental), Signal (y a-t-il une entrée maintenant ?), "
        "Action (ce qu'il faut faire), Setup (le playbook), Entry zone (la meilleure zone de confluence dans le sens du "
        "playbook), Confirmation à attendre, Stop (au-delà de la zone, ou derrière le dernier sommet ou creux 1 h), TP1 et "
        "TP2 (zones suivantes), R:R (spread compris) et la prochaine news.",
        "Le biais n'est pas un signal : un biais baissier avec le prix loin de la zone donne « Signal : aucun, Action : "
        "attendre un retour dans la zone ». Le signal n'apparaît que quand le prix est dans la zone et qu'une confirmation "
        "est présente (bougie de rejet, balayage de liquidité, CHoCH en 15 min). Trente minutes avant une annonce forte, "
        "tout setup est à éviter. Le bouton Mode passe de la vue Trader (simple) à la vue Analyst (complète)."]),
    ("Un seul prix de référence", [
        "Le prix XAU/USD affiché en haut vient de XAUS (spot, mis à jour chaque minute) et sert partout : fiche Trader, "
        "contrôle, distances aux niveaux. Les analyses sont calculées sur le future COMEX, puis converties en spot avec "
        "l'écart mesuré au même instant grâce à l'historique minute par minute de XAUS. Le graphique TradingView reste un "
        "flux séparé (OANDA), dont le prix peut différer de quelques centimes.",
        "Badges : LIVE = en direct ; « il y a X min » = relevé du robot (toutes les 15 min) ; DAILY = donnée publiée une "
        "fois par jour ; WEEKLY = une fois par semaine. DIFFÉRÉ signale que le direct est momentanément indisponible."]),
    ("Intermarket et Gold Relative Strength", [
        "L'or est comparé au dollar (DXY), au taux 2 ans et au taux réel 10 ans, sur 1 h, 4 h, 1 jour et 5 jours. Si les "
        "moteurs vont dans le même sens et que l'or suit : CONFIRMED. S'il va à l'opposé : DIVERGENCE, souvent le signal "
        "le plus intéressant (demande ou offre de fond). Sinon : MIXED.",
        "Le Gold Relative Strength (-100 à +100) mesure l'écart entre ce que l'or a fait et ce que le dollar et les taux "
        "expliquent, normalisé par la volatilité. Au-dessus de +25, l'or est plus fort que ses moteurs ; sous -25, plus faible."]),
    ("Le moteur de régimes et l'Event Reaction Engine", [
        "Six forces sont mesurées : taux et dollar, appétit pour le risque, inflation, liquidité, stress financier, flux. "
        "Chacune a une intensité de 0 à 100 et un effet sur l'or ; la plus intense est le régime dominant.",
        "Après chaque CPI, NFP, PCE ou décision de la Fed, le terminal affiche le consensus, le réel, le sens de la surprise "
        "et la réaction du dollar, du taux 2 ans et de l'or à 15 min et 1 h, puis indique si la réaction est conforme, "
        "inverse ou absente."]),
    ("L'analyse technique retenue, et pourquoi", [
        "Le terminal ne garde que ce qui a fait ses preuves ou que les professionnels surveillent réellement. La tendance "
        "et le momentum sur 1 à 6 mois sont l'effet technique le mieux documenté par la recherche, sur l'or comme sur les "
        "autres marchés. La volatilité est persistante : une période comprimée précède souvent une expansion.",
        "Les niveaux comptent parce que tout le monde les regarde : VWAP et ses bandes (référence d'exécution des "
        "institutions), profil de volume, plus haut et plus bas de la veille et de la semaine, chiffres ronds, poches de "
        "liquidité. La structure de marché (sommets et creux, cassures BOS et CHoCH) donne les niveaux d'invalidation. Le "
        "range asiatique et les fixings de Londres concentrent les flux propres à l'or.",
        "Les croisements d'indicateurs et les figures de chandeliers pris isolément n'ont pas d'avantage démontré : le "
        "RSI n'est utilisé que comme mesure d'extension."]),
    ("Liquidité, FVG, order blocks, OTE, premium / discount", [
        "Outils de localisation très utilisés en trading intraday. Les FVG sont les trous laissés par une bougie très "
        "directionnelle, que le prix revient souvent combler. Les order blocks sont la dernière bougie opposée avant une "
        "impulsion qui a cassé la structure. La zone OTE est le retracement de 61,8 à 78,6 % de la dernière impulsion. "
        "Premium et discount situent le prix dans le range 1 h. Les sommets et creux égaux sont de la liquidité évidente, "
        "et un balayage est une mèche qui perce un de ces niveaux puis se referme de l'autre côté.",
        "Leur efficacité n'est pas démontrée isolément : ils entrent dans les zones de confluence avec un poids modéré, "
        "et le contrôle avant l'entrée les utilise comme confirmations, jamais comme signal unique."]),
    ("Le playbook technico-fondamental", [
        "Le fondamental (biais) donne le sens du vent, la technique (score de -1 à +1 : tendance, momentum, structure, "
        "position au VWAP) dit si le prix le suit. Les neuf combinaisons de la matrice donnent le type de trade qui a un "
        "avantage : acheter les replis quand les deux sont haussiers, vendre les rebonds quand les deux sont baissiers, "
        "attendre un changement de structure en cas de divergence, jouer les extrémités en range, attendre la cassure "
        "quand la volatilité est comprimée avant une annonce.",
        "Chaque playbook donne une zone d'entrée, un déclencheur, des objectifs et un niveau d'invalidation. Le contrôle "
        "avant l'entrée vérifie que ton trade va dans son sens et part d'une zone de confluence."]),
    ("Les zones de confluence", [
        "Tous les niveaux proches du prix (veille, VWAP et bandes, profil de volume, pivots, moyennes 20 et 50 en 1 h et "
        "4 h, derniers sommets et creux, range asiatique, poches de liquidité, chiffres ronds) sont regroupés quand ils "
        "se superposent à moins de 6 % de l'ATR. Chaque niveau a un poids selon son importance ; une zone de score 5 ou "
        "plus rassemble plusieurs repères majeurs. Une entrée loin de toute zone déclenche une vigilance."]),
    ("La vue de marché (où va le marché)", [
        "Un score de -1 à +1 combine quatre composantes : le biais fondamental (35 %), le score technique (35 % : tendance "
        "multi-unités, momentum, structure, position au VWAP), le momentum du jour, c'est-à-dire la position face au VWAP et la variation depuis l'ouverture "
        "(15 %), et le sentiment : positionnement et flux des fonds, ETF, ton des titres, demande de fond (15 %).",
        "Au-dessus de +0,2 la vue est haussière, sous -0,2 baissière, entre les deux neutre. La conviction baisse d'un cran "
        "quand le fondamental et la tendance se contredisent, ou quand une annonce forte tombe dans l'horizon de 4 heures.",
        "La fourchette probable vient de la volatilité réellement observée à ces heures-là sur 60 jours : le prix y reste "
        "environ deux fois sur trois. Les scénarios s'appuient sur les niveaux de séance les plus proches."]),
    ("Le contrôle avant l'entrée", [
        "Il vérifie ton idée de trade contre le contexte : zone news (NON), trade contre tout "
        "(NON), contre le biais, la vue ou la tendance (ATTENTION), amplitude du jour déjà faite, niveau opposé trop proche, "
        "stop trop serré ou rapport gain / risque inférieur à 1 (ATTENTION). Avec un stop, il calcule la taille de position "
        "à partir de la taille de ton compte et de ton risque par trade, modifiables avec le bouton « Réglages » en haut de la page.",
        "Sans stop ni objectif saisis, il propose des niveaux de référence : stop derrière le niveau de protection le plus "
        "proche (au moins 0,15 ATR, marge de 0,05 ATR plus le spread), objectif sur le prochain niveau dans le sens du trade. "
        "Le spread de ton broker est compté dans le risque et retiré du gain.",
        "Le repère statistique suppose un marché sans avantage (marche au hasard) : l'objectif est alors atteint avant le "
        "stop dans une proportion égale à risque / (risque + gain). C'est le taux de réussite minimum que ton setup doit "
        "dépasser pour être rentable à ce ratio. La probabilité de toucher l'objectif dans les 4 heures vient de la "
        "volatilité observée à ces heures-là.",
        "La note (A, B, C, D) résume le contrôle : D dès qu'un point est bloquant, puis selon le nombre de points favorables "
        "et de vigilances, et le rapport gain / risque.",
        "Ce n'est pas un signal d'entrée : il te dit si les conditions sont réunies, ta stratégie décide du reste."]),
    ("L'analyste IA", [
        "Si une clé est configurée (Gemini gratuit ou Claude payant), l'IA reçoit le relevé complet du terminal et "
        "rédige une analyse : "
        "lecture du marché, avis sur la vue du terminal, forces en présence, scénarios sur les annonces, conditions pour un "
        "achat et pour une vente, points de vigilance. Nouvelle analyse toutes les 2 heures de 7 h à 23 h en semaine, et "
        "aussitôt après une annonce forte ou un changement de direction de la vue. Ses conditions d'achat ou de vente "
        "s'affichent dans le contrôle avant l'entrée. Au plus IA_MAX_JOUR analyses par jour, pour rester dans le "
        "quota gratuit de Gemini."]),
    ("La matrice de tendance", [
        "Pour chaque unité de temps : +1 si le prix est au-dessus de la moyenne 20, elle-même au-dessus de la 50 et en "
        "hausse ; +1 si les deux derniers sommets et creux montent. Le total va de -2 (baissière) à +2 (haussière)."]),
    ("Les profils de volatilité et de volume", [
        "Le profil de volatilité donne l'amplitude moyenne de chaque tranche de 15 minutes sur 60 jours : il montre quand "
        "l'or bouge assez pour scalper. Le profil de volume répartit le volume de chaque bougie sur sa plage de prix pour "
        "trouver le POC (prix le plus échangé) et la zone de valeur (70 % des échanges)."]),
    ("L'écart future-spot", [
        "Les données de marché gratuites viennent du future COMEX, qui cote au-dessus de l'or spot du coût de portage "
        "jusqu'à son échéance. Le terminal estime cet écart avec la courbe des futures et le retire de tous les niveaux, "
        "pour qu'ils correspondent à ton XAU/USD. DECALAGE_CFD corrige le petit écart restant propre à ton broker."]),
    ("Les carnets OANDA", [
        "OANDA publie, par tranche de prix, la répartition des ordres en attente et des positions ouvertes de ses clients "
        "sur XAU/USD. Au-dessus du prix, les ordres d'achat sont surtout des stops : ce sont des poches de liquidité, "
        "souvent visées par les mouvements rapides. En dessous, les ordres de vente sont les stops des acheteurs. Les "
        "ordres limites forment des murs qui freinent le prix.",
        "Le carnet de positions montre où la foule est entrée : quand plus de 65 % des traders sont d'un côté, le "
        "terminal y voit un signal contrarien. Les traders piégés en perte ont tendance à sortir à leur prix d'entrée, "
        "ce qui crée des zones de réaction. Le contrôle avant l'entrée t'avertit si ton stop est juste avant une poche "
        "de stops, là où les chasses aux stops frappent."]),
    ("La confirmation intermarché", [
        "Les sensibilités de l'or au dollar et au taux 10 ans, mesurées sur 60 jours, sont appliquées aux mouvements de "
        "la dernière heure et des 4 dernières heures. L'écart entre le mouvement réel et le mouvement attendu révèle une "
        "force ou une faiblesse que les graphiques seuls ne montrent pas."]),
    ("Le suivi des prévisions", [
        "Chaque heure de marché, la vue est enregistrée ; 4 heures plus tard, le terminal vérifie la direction et si le prix "
        "est resté dans la fourchette. L'historique est conservé entre deux mises à jour et publié avec le site "
        "(historique.json). Une vue qui ne dépasse pas nettement 50 % après 50 prévisions ne doit pas guider tes trades."]),
    ("Les limites", [
        "Les cotations TradingView sont en direct ; les analyses reposent sur Yahoo (environ 10 min de décalage) et sont "
        "recalculées toutes les 15 min. La FRED et le Trésor ont un jour ouvré de décalage, le COT reflète le mardi "
        "précédent. Les options sur l'or, le flux d'ordres du COMEX, les achats des banques centrales et la prime de "
        "Shanghai ne sont pas disponibles gratuitement.",
        "Ce terminal est un outil d'aide à la lecture du marché, pas un conseil en investissement."]),
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


# ---------------------------------------------------------------------------
# PAGE HTML : BLOCS v4 (vue de marché, contrôle, plan, technique, suivi)
# ---------------------------------------------------------------------------

def barre_score(v):
    """Barre centrée de -1 à +1."""
    if v is None:
        return '<div class="cbar"></div>'
    larg = min(abs(v), 1) * 50
    return (f'<div class="cbar"><b class="{"up" if v > 0 else "down"}" '
            f'style="left:{50 - larg if v < 0 else 50:.1f}%;width:{larg:.1f}%"></b></div>')


def ligne_fiabilite(a):
    st = a.get("suivi")
    if not st:
        return ""
    if st["n_dir"] < 20:
        return (f'<p class="pied">Fiabilité : {st["n_dir"]} prévision(s) directionnelle(s) évaluée(s) sur 30 jours. '
                f'Pas encore assez pour s\'y fier (il en faut au moins 20).</p>')
    return (f'<p class="pied">Fiabilité mesurée sur 30 jours : <b>{nb(st["taux_dir"], 0)} %</b> de bonnes directions '
            f'sur {st["n_dir"]} prévisions. <a href="#analyse" data-aller="suivi">Détail</a></p>')


def ligne_playbook(a):
    pb = a.get("playbook")
    if not pb:
        return ""
    ton = {1: "up", -1: "down"}.get(pb["sens"], "flat")
    z = (pb["zones_entree"] or [None])[0]
    zone = (f' Zone d\'entrée : <b>{nb(z["bas"], 1)}–{nb(z["haut"], 1)}</b> ({esc(", ".join(z["elements"][:2]))}).' if z else "")
    inv = f' Invalidation : {nb(pb["invalidation"], 1)}.' if pb.get("invalidation") else ""
    return (f'<div class="pb-ligne"><span class="k">Playbook technico-fondamental</span><br><b class="{ton}">'
            f'{esc(pb["titre"])}</b>.{zone}{inv} <a href="#analyse" data-aller="technique">Détail</a></div>')


def avis_ia(ia):
    j = (ia or {}).get("json")
    if not j:
        return ""
    ton = {"d'accord": "up", "en désaccord": "down"}.get(j["accord"].lower(), "flat")
    return (f'<div class="avis-ia"><div class="k">Analyste IA ({esc(ia.get("fournisseur", "Claude"))}) · {ia_quand(ia)}</div>'
            f'<p><span class="chip {ton}">{esc(j["accord"])}</span> {esc(j["titre"])} '
            f'<a href="#analyse" data-aller="fondamental">Lire</a></p></div>')


def bloc_vue_cockpit(a):
    v = a.get("vue")
    if not v:
        return '<p class="vide">Vue de marché indisponible.</p>'
    ton = {1: "up", -1: "down"}.get(v["sens"], "flat")
    st_ = {x["nom"]: x for x in (a.get("technique") or {}).get("structure", [])}

    def puce(t):
        x = st_.get(t["nom"])
        labs = f' {x["label_sommet"] or ""}·{x["label_creux"] or ""}' if x else ""
        return (f'<span class="tf {({1: "up", -1: "down"}).get(t["sens"], "flat")}" '
                f'title="{esc(t["detail"] + (" ; structure " + x["etat"].lower() if x else ""))}">'
                f'{esc(t["nom"].replace(" heures", " h").replace("1 heure", "1 h").replace("Journalier", "Jour"))} '
                f'{({1: "▲", -1: "▼"}).get(t["sens"], "●")}{labs}</span>')
    mt = "".join(puce(t) for t in a.get("matrice") or [])
    fourch = (f'<div class="fourch"><span class="k">Fourchette probable à {HORIZON_H} h (68 %)</span>'
              f'<b>{nb(v["fourchette"][0], 0)} – {nb(v["fourchette"][1], 0)}</b><small>±{nb(v["sigma"], 0)} $</small></div>'
              ) if v["fourchette"] else ""
    alert = "".join(f'<p class="nu">Conviction réduite : {esc(x)}.</p>' for x in v["alertes"])
    sent = "".join(f'<span class="chip {t}">{esc(l)} : {esc(d)}</span>' for l, d, t in v["sentiment"][:4])
    return (f'<div class="vue-top"><div><div class="k">Direction à {HORIZON_H} heures</div>'
            f'<div class="verdict-s {ton}">{v["direction"].upper()}</div>'
            f'<div class="k">conviction {esc(v["conviction"])}</div></div>'
            f'<div class="vue-score">{barre_score(v["score"])}<div class="pctleg"><span>Baissier</span>'
            f'<span>score {nb(v["score"], 2, True)}</span><span>Haussier</span></div></div></div>'
            f'<div class="tfs">{mt}</div>{fourch}'
            f'<p class="scen"><b>Scénario central.</b> {esc(v["central"])}</p>'
            + (f'<p class="scen alt"><b>Scénario alternatif.</b> {esc(v["alternatif"])}</p>' if v["alternatif"] else "")
            + f'{alert}{ligne_playbook(a)}<div class="chips">{sent}</div>{avis_ia(a.get("ia"))}{ligne_fiabilite(a)}'
            f'<p class="pied"><a href="#analyse" data-aller="synthese">Plan de séance, sentiment et catalyseurs</a></p>')


def bloc_controle():
    return ('<div class="ctl"><div class="ctl-boutons"><button type="button" class="achat" data-sens="1">Achat <kbd>A</kbd></button>'
            '<button type="button" class="vente" data-sens="-1">Vente <kbd>V</kbd></button></div>'
            '<div class="ctl-champs"><label>Prix d\'entrée <input id="ctl-prix" inputmode="decimal" autocomplete="off"></label>'
            '<label>Stop <input id="ctl-stop" inputmode="decimal" placeholder="proposé" autocomplete="off"></label>'
            '<label>Objectif <input id="ctl-cible" inputmode="decimal" placeholder="proposé" autocomplete="off"></label></div>'
            '<div id="ctl-res"><p class="pied">Choisis Achat ou Vente (touches A et V). Tape le prix de ta plateforme pour des '
            'distances exactes. Sans stop ni objectif, le terminal propose ses niveaux de référence et calcule la taille.</p></div></div>')


def bloc_biais_detail(a):
    b = a["biais"]
    lignes = ""
    for s in b["signaux"]:
        ic = {1: '<span class="ic up">▲</span>', -1: '<span class="ic down">▼</span>'}.get(s["score"], '<span class="ic flat">●</span>')
        lignes += f'<tr><td>{ic} {esc(s["nom"])}</td><td class="num">{esc(s["valeur"])}</td><td class="flat">{esc(s["lecture"])}</td></tr>'
    div = f'<div class="note">{esc(a["divergence"])}</div>' if a.get("divergence") else ""
    coul = {"Haussier": "up", "Baissier": "down"}.get(b["verdict"], "flat")
    return (f'<section><h2>Biais fondamental : <span class="{coul}">{b["verdict"]}</span> '
            f'<small class="flat">score {nb(b["total"], 0, True) if b["total"] else "0"} sur {b["n"]} signaux</small></h2>'
            f'<div class="defile"><table class="cal">{lignes}</table></div>{div}</section>')


def bloc_risque(a):
    r = a.get("risque")
    if not r:
        return ""
    comp = "".join(f'<span>{esc(l)}</span>{barre_score(v / 3)}<span class="v">{nb(v, 1, True)} σ</span>'
                   for l, v in r["composantes"])
    ton = {"Risk-on": "down", "Risk-off": "up"}.get(r["etat"], "flat")
    return (f'<section><h2>Appétit pour le risque : <span class="{ton}">{r["etat"]}</span> '
            f'<small class="flat">score {nb(r["score"], 0, True)} sur 100</small></h2>'
            f'<p class="pourquoi">{esc(r["lecture"])} Chaque barre montre le mouvement sur 5 jours, orienté pour que '
            f'la droite signifie « prise de risque ».</p><div class="decomp" style="max-width:640px">{comp}</div></section>')


def graphique_profil_vol(pv, cal, w=1000, h=190):
    if not pv or pv.get("moy") is None:
        return '<p class="vide">Profil de volatilité indisponible.</p>'
    moy, auj = pv["moy"], pv["auj"]
    mx = max([x for x in list(moy.values) + list(auj.values) if x == x] or [1])
    pl, pr, pt, pb = 8, 8, 12, 24
    W, H = w - pl - pr, h - pt - pb
    bw = W / 96
    out = []
    for i in range(96):
        x = pl + i * bw
        m = moy.iloc[i]
        if m == m:
            hh = m / mx * H
            out.append(f'<rect x="{x + 0.5:.1f}" y="{pt + H - hh:.1f}" width="{bw - 1:.1f}" height="{hh:.1f}" fill="var(--band2)"/>')
        t = auj.iloc[i]
        if t == t:
            hh = t / mx * H
            out.append(f'<rect x="{x + bw * 0.25:.1f}" y="{pt + H - hh:.1f}" width="{bw * 0.5:.1f}" height="{hh:.1f}" fill="var(--brass)"/>')
        if i % 8 == 0:
            out.append(f'<text x="{x:.1f}" y="{h - 6}" class="ax">{i // 4} h</text>')
    now = maintenant()
    for e in cal or []:
        if e["impact"] == "High" and e["date"].date() == now.date():
            i = e["date"].hour * 4 + e["date"].minute // 15
            out.append(f'<line x1="{pl + (i + 0.5) * bw:.1f}" x2="{pl + (i + 0.5) * bw:.1f}" y1="{pt}" y2="{pt + H}" '
                       f'stroke="var(--down)" stroke-dasharray="3 3"/>')
    xs = pl + (pv["slot"] + 0.5) * bw
    out.append(f'<line x1="{xs:.1f}" x2="{xs:.1f}" y1="{pt}" y2="{pt + H}" stroke="var(--ink)" stroke-width="1.5"/>')
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Amplitude moyenne par tranche de 15 minutes">'
            + "".join(out) + '</svg>')


def echelle_carnet(lignes, px, largeur, titre, lib_g, lib_d, w=470, h=430):
    """Échelle de prix : barres vertes à gauche (achats), rouges à droite (ventes), prix actuel en doré."""
    if not lignes:
        return '<p class="vide">Carnet indisponible.</p>'
    z = {}
    for p, lg, sh in lignes:
        k = math.floor(p / largeur)
        a_, b_ = z.get(k, (0.0, 0.0))
        z[k] = (a_ + lg, b_ + sh)
    cles = sorted(z, reverse=True)
    mx = max(max(v) for v in z.values()) or 1
    pl, pr, pt, pb = 64, 10, 26, 8
    W, H = w - pl - pr, h - pt - pb
    bh = H / len(cles)
    cx = pl + W / 2
    out = [f'<text x="{pl + W * 0.25:.0f}" y="16" text-anchor="middle" class="ax">{esc(lib_g)}</text>',
           f'<text x="{pl + W * 0.75:.0f}" y="16" text-anchor="middle" class="ax">{esc(lib_d)}</text>',
           f'<line x1="{cx:.1f}" x2="{cx:.1f}" y1="{pt}" y2="{pt + H}" class="gr"/>']
    pas = max(1, len(cles) // 9)
    for i, k in enumerate(cles):
        y = pt + i * bh
        lg, sh = z[k]
        if lg:
            ww = lg / mx * (W / 2 - 4)
            out.append(f'<rect x="{cx - ww:.1f}" y="{y + 0.5:.1f}" width="{ww:.1f}" height="{max(bh - 1, 0.8):.1f}" fill="var(--up)" opacity=".75"/>')
        if sh:
            ww = sh / mx * (W / 2 - 4)
            out.append(f'<rect x="{cx:.1f}" y="{y + 0.5:.1f}" width="{ww:.1f}" height="{max(bh - 1, 0.8):.1f}" fill="var(--down)" opacity=".75"/>')
        if i % pas == 0:
            out.append(f'<text x="{pl - 6}" y="{y + bh / 2 + 4:.1f}" text-anchor="end" class="ax">{nb((k + 0.5) * largeur, 0)}</text>')
    ypx = pt + (cles[0] + 1 - px / largeur) * bh
    out.append(f'<line x1="{pl}" x2="{pl + W}" y1="{ypx:.1f}" y2="{ypx:.1f}" stroke="var(--brass)" stroke-width="1.5"/>'
               f'<text x="{pl + W}" y="{ypx - 4:.1f}" text-anchor="end" class="ax c1">{nb(px, 1)}</text>')
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{esc(titre)}">' + "".join(out) + '</svg>')


def bloc_carnets(a):
    c = a.get("carnets")
    if not c:
        return ""
    zones = lambda cle: ", ".join(f'{nb(x["prix"], 1)} (×{nb(x["force"], 1)})' for x in c.get(cle, [])) or "aucune notable"
    try:
        quand = datetime.fromisoformat(c["temps"].replace("Z", "+00:00")).astimezone(tz_local())
        quand_txt = f"instantané de {quand.hour:02d}:{quand.minute:02d}"
    except Exception:
        quand_txt = ""
    pa = c.get("part_acheteurs")
    ton = "down" if pa and pa >= 65 else ("up" if pa and pa <= 35 else "flat")
    kv = (f'<div class="kv"><span>Part d\'acheteurs</span><span class="{ton}">{nb(pa, 0)} %</span>'
          f'<span>Acheteurs en perte (entrés plus haut)</span><span>{nb(c.get("longs_pieges"), 0)} %</span>'
          f'<span>Vendeurs en perte (entrés plus bas)</span><span>{nb(c.get("shorts_pieges"), 0)} %</span>'
          f'<span>Prix moyen d\'entrée des acheteurs</span><span>{nb(c.get("entree_longs"), 1)}</span>'
          f'<span>Prix moyen d\'entrée des vendeurs</span><span>{nb(c.get("entree_shorts"), 1)}</span>'
          f'<span>Poches de stops acheteurs (au-dessus)</span><span>{zones("stops_acheteurs")}</span>'
          f'<span>Poches de stops vendeurs (en dessous)</span><span>{zones("stops_vendeurs")}</span>'
          f'<span>Murs d\'ordres vendeurs (au-dessus)</span><span>{zones("murs_vente")}</span>'
          f'<span>Murs d\'ordres acheteurs (en dessous)</span><span>{zones("murs_achat")}</span></div>')
    return (f'<section><h2>Carnets d\'ordres et de positions (OANDA, XAU/USD spot)</h2><p class="pourquoi">Ordres en '
            f'attente et positions ouvertes des clients particuliers d\'OANDA, par tranche de {nb(c["largeur"], 1)} $, '
            f'{esc(quand_txt)}. Au-dessus du prix, les ordres d\'achat sont surtout des stops (cassures, stops des '
            f'vendeurs) : ce sont des poches de liquidité que le marché va souvent chercher. En dessous, les ordres de '
            f'vente sont les stops des acheteurs. Les « murs » sont des ordres limites qui freinent le prix. '
            f'(×2,0 = deux fois la moyenne de la zone.)</p>'
            f'<div class="grille3"><div>{echelle_carnet(c["histo_ordres"], c["prix"], c["largeur"], "Carnet d’ordres", "ordres d’achat", "ordres de vente")}</div>'
            f'<div>{echelle_carnet(c["histo_positions"], c["prix"], c["largeur"], "Carnet de positions", "acheteurs", "vendeurs")}</div>'
            f'<div>{kv}<p class="note" style="border-left-color:var(--steel);background:rgba(152,163,174,.08)">'
            f'{esc(c["lecture"])}</p><p class="legende" style="margin-top:8px">Échantillon des clients d\'OANDA, pas tout '
            f'le marché : un bon indicateur du comportement des particuliers, qui ont souvent tort aux extrêmes.</p></div>'
            f'</div></section>')


def bloc_intermarche(a):
    im = a.get("intermarche")
    if not im:
        return ""
    lignes = "".join(f'<tr><td>{esc(l["lib"])}</td><td class="num {classe_effet(l["or"], 1)}">{nb(l["or"], 2, True)} %</td>'
                     f'<td class="num">{nb(l.get("dxy"), 2, True) + " %" if l.get("dxy") is not None else "n.d."}</td>'
                     f'<td class="num">{nb(l.get("us10"), 1, True) + " pb" if l.get("us10") is not None else "n.d."}</td>'
                     f'<td class="num">{nb(l["attendu"], 2, True)} %</td>'
                     f'<td class="num {({"force": "up", "faiblesse": "down"}).get(l["signal"], "flat")}">{nb(l["residu"], 2, True)} %</td>'
                     f'<td class="{({"force": "up", "faiblesse": "down"}).get(l["signal"], "flat")}">{esc(l["signal"])}</td></tr>'
                     for l in im["lignes"])
    textes = "".join(f'<p class="bloc-txt">{esc(l["texte"])}</p>' for l in im["lignes"])
    return (f'<section><h2>Confirmation intermarché</h2><p class="pourquoi">L\'or suit-il ce que le dollar et le taux 10 ans '
            f'lui dictent ? Le mouvement attendu applique les sensibilités mesurées sur 60 jours aux mouvements récents. '
            f'Un or qui fait mieux que prévu montre des acheteurs cachés ; moins bien, des vendeurs. Bougies 15 min Yahoo, '
            f'environ 15 min de décalage.</p><div class="defile"><table class="cal"><tr><th>Période</th><th>Or</th>'
            f'<th>Dollar</th><th>10 ans</th><th>Attendu</th><th>Écart</th><th>Lecture</th></tr>{lignes}</table></div>'
            f'{textes}</section>')


MATRICE_PB = {(1, 1): "Acheter les replis", (1, 0): "Achats sélectifs", (1, -1): "Divergence : attendre le CHoCH haussier",
              (0, 1): "Suivre la technique, taille réduite", (0, 0): "Patience ou range",
              (0, -1): "Suivre la technique, taille réduite", (-1, 1): "Divergence : attendre le CHoCH baissier",
              (-1, 0): "Ventes sélectives", (-1, -1): "Vendre les rebonds"}


def bloc_synthese_tech(a):
    t, pb = a.get("technique"), a.get("playbook")
    if not t or not pb:
        return ('<section><h2>Synthèse technico-fondamentale</h2><p class="vide">Données intraday insuffisantes.</p>'
                '</section>')
    cellules = ""
    for fv, flib in ((1, "Fondamental haussier"), (0, "Fondamental neutre"), (-1, "Fondamental baissier")):
        cellules += f'<div class="mx-l">{flib}</div>'
        for tv_ in (1, 0, -1):
            actif = (fv, tv_) == (pb["fond"], pb["tech"])
            ton = {1: "up", -1: "down"}.get(fv if fv == tv_ else 0, "flat") if fv or tv_ else "flat"
            cellules += f'<div class="mx-c{" actif" if actif else ""} {ton}">{esc(MATRICE_PB[(fv, tv_)])}</div>'
    entete = ('<div></div><div class="mx-h">Technique haussière</div><div class="mx-h">Technique neutre</div>'
              '<div class="mx-h">Technique baissière</div>')
    reg = t["regime"]
    notes = []
    if reg["nom"] == "range":
        notes.append("Régime de range : les cassures échouent plus souvent, privilégie les extrémités.")
    if reg["compression"]:
        notes.append("Volatilité comprimée" + (" (NR7 hier)" if reg["nr7"] else "") + " : une expansion est probable, "
                     "attention aux faux départs avant les annonces.")
    etapes = "".join(f"<li>{esc(e)}</li>" for e in pb["etapes"])
    ton_pb = {1: "up", -1: "down"}.get(pb["sens"], "flat")
    b = a["biais"]
    scores = (f'<div class="kv"><span>Fondamental</span><span>{b["verdict"]} ({nb(b["total"], 0, True) if b["total"] else "0"} '
              f'sur {b["n"]})</span><span>Technique</span><span class="{({1: "up", -1: "down"}).get(t["sens"], "flat")}">'
              f'{nb(t["score"], 2, True)} ({({1: "haussière", -1: "baissière"}).get(t["sens"], "neutre")})</span>'
              f'<span>Régime</span><span>{esc(reg["nom"])}{", compression" if reg["compression"] else ""}</span></div>')
    conf = ""
    for z in sorted(t["confluences"], key=lambda z: -z["centre"]):  # du plus haut au plus bas, comme une échelle de prix
        dedans = z["bas"] <= t["prix"] <= z["haut"]
        pos = "prix dedans" if dedans else (f'{nb(z["dist"], 1, True)} $')
        larg = min(z["score"] / 12, 1) * 100
        conf += (f'<tr{" class=ici" if dedans else ""}><td class="num">{nb(z["bas"], 1)} – {nb(z["haut"], 1)}</td>'
                 f'<td><div class="jauge-s"><i style="width:{larg:.0f}%"></i></div></td><td class="num">{nb(z["score"], 1)}</td>'
                 f'<td class="num flat">{pos}</td>'
                 f'<td class="flat">{esc(", ".join(z["elements"]))}</td></tr>')
    return (f'<section id="synthese-tech"><h2>Synthèse technico-fondamentale</h2><p class="pourquoi">Le fondamental donne le '
            f'sens du vent, la technique dit si le prix le suit et où agir. Leur combinaison détermine le type de trade qui a '
            f'un avantage maintenant. Case dorée : la situation actuelle.</p>'
            f'<div class="grille2"><div><div class="matrice-pb">{entete}{cellules}</div>{scores}</div>'
            f'<div><div class="k">Playbook du moment</div><div class="pb-titre {ton_pb}">{esc(pb["titre"])}</div>'
            f'<p class="bloc-txt">{esc(pb["explication"])}</p><ol class="routine">{etapes}</ol>'
            + "".join(f'<p class="nu">{esc(n)}</p>' for n in notes) + '</div></div>'
            f'<h3>Zones de confluence</h3><p class="legende">Là où plusieurs niveaux surveillés par les professionnels se '
            f'superposent : c\'est là que le prix réagit le plus souvent. Plus le score est élevé, plus la zone est forte. '
            f'Prix en XAU/USD spot.</p><div class="defile"><table class="cal"><tr><th>Zone</th><th></th><th>Score</th>'
            f'<th>Distance</th><th>Niveaux superposés</th></tr>{conf or "<tr><td>Aucune zone forte à proximité</td></tr>"}'
            f'</table></div></section>')


def bloc_details_tech(a):
    t = a.get("technique")
    if not t:
        return ""
    reg, m = t["regime"], t["momentum"]
    lib_er = lambda e: "n.d." if e is None else (f'{nb(e, 2)} (' + ("tendance" if e >= 0.35 else ("range" if e <= 0.2 else "transition")) + ")")
    regime = (f'<div class="kv"><span>Efficacité 1 h</span><span>{lib_er(reg["er1"])}</span>'
              f'<span>Efficacité 4 h</span><span>{lib_er(reg["er4"])}</span>'
              f'<span>Largeur de Bollinger 1 h (percentile)</span><span>{nb(reg["bw1"], 0)}{" : compressée" if reg["bw1"] is not None and reg["bw1"] <= 15 else ""}</span>'
              f'<span>Journée d\'hier</span><span>{"NR7 (plus petite amplitude sur 7 jours)" if reg["nr7"] else ("inside day" if reg["inside"] else "normale")}</span></div>')
    mom = "".join(f'<span>Performance {x["n"]} jours</span><span class="{classe_effet(x["r"], 1)}">{nb(x["r"], 2, True)} %</span>'
                  for x in m["lignes"])
    ext = "".join(f'<span>Écart à la moyenne {n} jours</span><span>{nb(v, 1, True)} ATR</span>' for n, v in m["extension"].items() if v is not None)
    rsis = "".join(f'<span>RSI 14 {k}</span><span class="{"down" if v >= 70 else ("up" if v <= 30 else "flat")}">{nb(v, 0)}</span>'
                   for k, v in m["rsi"].items())
    lab = lambda l: f'<span class="lab {l.lower()}">{l}</span>' if l else ""
    struct = "".join(f'<tr><td>{esc(x["nom"])}</td><td class="{({1: "up", -1: "down"}).get(x["tendance"], "flat")}">'
                     f'<b>{esc(x["etat"])}</b><br><small class="flat">{esc(x["detail"])}</small></td>'
                     f'<td>{"".join(lab(y_["label"]) for y_ in x["sequence"])}</td>'
                     f'<td class="num">{lab(x["label_sommet"])}{nb(x["sommet"] + AJUST, 1)}</td>'
                     f'<td class="num">{lab(x["label_creux"])}{nb(x["creux"] + AJUST, 1)}</td>'
                     f'<td class="{({1: "up", -1: "down"}).get(x["sens_evt"], "flat")}">{esc(x["evenement"] or "aucune")}</td></tr>'
                     for x in t["structure"])
    graphes = ""
    st_ = {x["nom"]: x for x in t["structure"]}
    for nom, df_, nbar in (("1 heure", t.get("_h1"), 120), ("4 heures", t.get("_h4"), 90)):
        if df_ is not None and nom in st_:
            d_ = df_.tail(nbar)
            sw_ = [z for z in st_[nom]["swings"] if z["t"] >= d_.index[0]]
            graphes += (f'<div><h3 style="margin-top:0">Structure {nom} : <span class="{({1: "up", -1: "down"}).get(st_[nom]["tendance"], "flat")}">'
                        f'{esc(st_[nom]["etat"])}</span></h3>{graphique_structure(d_, sw_, "Structure " + nom)}</div>')
    vw, asie = t.get("vwap"), t.get("asie")
    seance_kv = ""
    if vw:
        seance_kv += (f'<span>VWAP du jour</span><span>{nb(vw["vwap"] + AJUST, 1)}</span>'
                      f'<span>Bandes -2σ / -1σ</span><span>{nb(vw["bandes"][-2] + AJUST, 1)} / {nb(vw["bandes"][-1] + AJUST, 1)}</span>'
                      f'<span>Bandes +1σ / +2σ</span><span>{nb(vw["bandes"][1] + AJUST, 1)} / {nb(vw["bandes"][2] + AJUST, 1)}</span>'
                      f'<span>Position du prix</span><span>{nb(vw["z"], 1, True)} σ</span>')
    if asie:
        seance_kv += (f'<span>Range asiatique</span><span>{nb(asie["bas"] + AJUST, 1)} – {nb(asie["haut"] + AJUST, 1)}</span>'
                      f'<span>État à Londres</span><span>{esc(asie["etat"])}</span>')
    if t.get("fixings"):
        seance_kv += f'<span>Fixings de Londres (Paris)</span><span>{" et ".join(f"{d.hour:02d}:{d.minute:02d}" for d in t["fixings"])}</span>'
    return (f'<section><h2>Les briques techniques</h2><div class="grille3">'
            f'<div><h3 style="margin-top:0">Régime et volatilité</h3>{regime}<p class="legende" style="margin-top:8px">'
            f'Efficacité = distance parcourue divisée par le chemin total : au-dessus de 0,35 le marché tend, sous 0,2 il '
            f'oscille. Une largeur de Bollinger très basse annonce une expansion.</p></div>'
            f'<div><h3 style="margin-top:0">Momentum et extension</h3><div class="kv">{mom}{ext}{rsis}</div>'
            f'<p class="legende" style="margin-top:8px">Le momentum sur 1 à 6 mois est l\'effet technique le mieux documenté. '
            f'Le RSI mesure l\'extension : au-dessus de 70 dans une forte tendance, c\'est de la force, pas un signal de vente.</p></div>'
            f'<div><h3 style="margin-top:0">Séance : VWAP, Asie, fixings</h3><div class="kv">{seance_kv}</div>'
            f'<p class="legende" style="margin-top:8px">Au-delà de ±2σ du VWAP, le prix est étiré : les desks y prennent '
            f'leurs profits. La cassure du range asiatique à l\'ouverture de Londres et les fixings concentrent les flux.</p></div>'
            f'</div><h3>Structure de marché : HH, HL, LH, LL</h3><p class="legende"><span class="lab hh">HH</span> sommet plus '
            f'haut que le précédent · <span class="lab hl">HL</span> creux plus haut · <span class="lab lh">LH</span> sommet '
            f'plus bas · <span class="lab ll">LL</span> creux plus bas. HH + HL = structure haussière, LH + LL = baissière, '
            f'LH + HL = compression, HH + LL = élargissement. Seuls les sommets et creux significatifs sont retenus '
            f'(mouvement d\'au moins une demi-amplitude moyenne de bougie).</p>'
            f'<div class="grille2">{graphes}</div>'
            f'<div class="defile" style="margin-top:14px"><table class="cal"><tr><th>Unité</th><th>État</th><th>Séquence (de la plus ancienne à la plus récente)</th>'
            f'<th>Dernier sommet</th><th>Dernier creux</th><th>Dernière cassure</th></tr>{struct}</table></div>'
            f'<p class="legende" style="margin-top:8px">BOS : le prix casse le dernier sommet (ou creux) dans le sens de la '
            f'structure, elle continue. CHoCH : il casse contre la structure, premier signe de retournement. Un achat sur un '
            f'nouveau HL dans une structure HH + HL, ou une vente sur un nouveau LH dans une structure LH + LL, est le setup '
            f'de continuation classique. Prix en XAU/USD spot.</p></section>')


def bloc_concepts(a):
    t = a.get("technique") or {}
    c = t.get("avances")
    if not c:
        return ""
    px = t["prix"]
    sp = lambda v: v + AJUST
    # Premium / discount
    pd_ = c.get("pd")
    pd_html = ""
    if pd_:
        pos = clamp(pd_["pos"], 0, 100)
        ton = {"premium": "down", "discount": "up"}.get(pd_["zone"], "flat")
        pd_html = (f'<div class="pctbar" style="margin-top:14px"><i style="left:50%"></i><b style="left:{pos:.1f}%"></b></div>'
                   f'<div class="pctleg"><span>Discount {nb(sp(pd_["bas"]), 1)}</span><span class="{ton}">'
                   f'{esc(pd_["zone"])} ({nb(pd_["pos"], 0)} %)</span><span>Premium {nb(sp(pd_["haut"]), 1)}</span></div>'
                   f'<p class="legende" style="margin-top:6px">Range 1 h entre le dernier creux et le dernier sommet. Les pros '
                   f'achètent en discount (sous le milieu, {nb(sp(pd_["milieu"]), 1)}) et vendent en premium.</p>')
    # OTE
    ote = ""
    for nom, fb in c["fibo"].items():
        if fb:
            ote += (f'<tr><td>{esc(nom)}</td><td class="{"up" if fb["sens"] > 0 else "down"}">{"hausse" if fb["sens"] > 0 else "baisse"} '
                    f'{nb(sp(fb["depart"]), 1)} → {nb(sp(fb["arrivee"]), 1)}</td>'
                    + "".join(f'<td class="num">{nb(sp(fb["niveaux"][r]), 1)}</td>' for r in (0.5, 0.618, 0.705, 0.786)) + '</tr>')
    # FVG et order blocks
    zl = lambda z, lib: (f'<tr><td>{esc(lib)}</td><td class="{"up" if z["sens"] > 0 else "down"}">{"haussier" if z["sens"] > 0 else "baissier"}</td>'
                         f'<td class="num">{nb(sp(z["bas"]), 1)} – {nb(sp(z["haut"]), 1)}</td>'
                         f'<td class="num flat">{nb((z["bas"] + z["haut"]) / 2 - (px - AJUST), 1, True)} $</td>'
                         f'<td class="flat">{"partiellement comblé" if z.get("partiel") else ("intact" if "partiel" in z else "non revisité")}</td></tr>')
    fvg = "".join(zl(z, f"FVG {nom}") for nom, lst in c["fvg"].items()
                  for z in sorted(lst, key=lambda z: abs((z["bas"] + z["haut"]) / 2 - (px - AJUST)))[:3])
    ob = "".join(zl(z, f"Order block {nom}") for nom, lst in c["ob"].items()
                 for z in sorted(lst, key=lambda z: abs((z["bas"] + z["haut"]) / 2 - (px - AJUST)))[:2])
    # Liquidité
    liq = "".join(f'<span>Sommets égaux (stops acheteurs au-dessus)</span><span>{nb(sp(v), 1)}</span>' for v in c["egaux_hauts"])
    liq += "".join(f'<span>Creux égaux (stops vendeurs en dessous)</span><span>{nb(sp(v), 1)}</span>' for v in c["egaux_bas"])
    liq += "".join(f'<span>{esc(k)}</span><span>{nb(sp(v), 1)}</span>' for k, v in c["periodes"].items())
    liq += (f'<span>Projection d\'amplitude haute (bas du jour + ATR)</span><span>{nb(sp(c["adr"]["haut"]), 1)}</span>'
            f'<span>Projection d\'amplitude basse (haut du jour - ATR)</span><span>{nb(sp(c["adr"]["bas"]), 1)}</span>'
            f'<span>Amplitude habituelle restante</span><span>{nb(c["adr"]["restant"], 1)} $</span>')
    bal = "".join(f'<li class="{"up" if x["sens"] > 0 else "down"}">{"Balayage sous" if x["sens"] > 0 else "Balayage au-dessus de"} '
                  f'{esc(x["lib"].lower())} ({nb(sp(x["niveau"]), 1)}), il y a {x["age"] + 1} bougie(s) 15 min : '
                  f'{"rejet haussier, liquidité vendeuse prise" if x["sens"] > 0 else "rejet baissier, liquidité acheteuse prise"}</li>'
                  for x in c["balayages"]) or '<li class="vide">Aucun balayage de niveau clé sur les 2 dernières heures.</li>'
    dec = c.get("declencheur")
    divs = [f'{nom} : {d["texte"]}' for nom, d in c["divergences"].items() if d]
    kz = "".join(f'<span class="chip{" actif-kz" if c["kill_zone"] and c["kill_zone"][0] == k[0] else ""}">{k[0]} '
                 f'{int(k[1]):02d}:{int(k[1] % 1 * 60):02d}–{int(k[2]):02d}:{int(k[2] % 1 * 60):02d}</span>' for k in KILL_ZONES)
    signaux = (f'<div class="kv"><span>Dernière bougie 15 min clôturée</span><span class="{({1: "up", -1: "down"}).get(dec["sens"], "flat") if dec else "flat"}">'
               f'{esc(dec["nom"]) if dec else "aucun motif"}</span>'
               f'<span>Divergences RSI</span><span>{esc(" ; ".join(divs)) if divs else "aucune"}</span></div>')
    return (f'<section><h2>Liquidité, FVG, order blocks, OTE</h2><p class="pourquoi">Les outils de localisation des traders '
            f'intraday : ils disent où le prix a des chances de réagir et où dorment les stops. Leur efficacité n\'est pas '
            f'démontrée seule ; ils prennent de la valeur quand ils se superposent aux autres niveaux, c\'est pourquoi ils '
            f'alimentent les zones de confluence. Prix en XAU/USD spot.</p>'
            f'<div class="grille3"><div><h3 style="margin-top:0">Premium / discount</h3>{pd_html or "<p class=vide>n.d.</p>"}'
            f'<h3>Kill zones (heure de Paris)</h3><div class="chips">{kz}</div><p class="legende" style="margin-top:6px">'
            f'Créneaux où la liquidité et les mouvements directionnels se concentrent. '
            f'{"En ce moment : " + c["kill_zone"][0] + "." if c["kill_zone"] else "Hors kill zone en ce moment."}</p></div>'
            f'<div><h3 style="margin-top:0">Liquidité et niveaux de période</h3><div class="kv">{liq}</div></div>'
            f'<div><h3 style="margin-top:0">Balayages récents</h3><ul class="suite">{bal}</ul><h3>Déclencheurs</h3>{signaux}</div></div>'
            f'<h3>Zone OTE : retracements de la dernière impulsion</h3><div class="defile"><table class="cal"><tr><th>Unité</th>'
            f'<th>Impulsion</th><th>50 %</th><th>61,8 %</th><th>70,5 %</th><th>78,6 %</th></tr>{ote or "<tr><td>n.d.</td></tr>"}</table></div>'
            f'<p class="legende" style="margin-top:6px">La zone OTE (entre 61,8 et 78,6 %) est la zone de retracement où l\'on '
            f'cherche à entrer dans le sens de l\'impulsion.</p>'
            f'<h3>FVG et order blocks proches</h3><div class="defile"><table class="cal"><tr><th>Type</th><th>Sens</th><th>Zone</th>'
            f'<th>Distance</th><th>État</th></tr>{fvg}{ob}</table></div><p class="legende" style="margin-top:6px">FVG : trou '
            f'laissé par une bougie très directionnelle, que le prix revient souvent combler. Order block : dernière bougie '
            f'opposée avant l\'impulsion qui a cassé la structure, souvent défendue au premier retour.</p></section>')


def bloc_technique(a, data):
    mt = a.get("matrice") or []
    lignes = "".join(f'<tr><td>{esc(t["nom"])}</td><td class="{({1: "up", -1: "down"}).get(t["sens"], "flat")}">'
                     f'<b>{esc(t["label"])}</b></td><td class="num">{nb(t["ema20"] + AJUST, 1)}</td>'
                     f'<td class="num">{nb(t["ema50"] + AJUST, 1)}</td><td class="flat">{esc(t["detail"])}</td></tr>' for t in mt)
    mat = (f'<section><h2>Matrice de tendance</h2><p class="pourquoi">Tendance de l\'or sur quatre unités de temps : '
           f'position face aux moyennes mobiles exponentielles 20 et 50, et structure des derniers sommets et creux. '
           f'Un scalp dans le sens des unités 1 h et 4 h a le vent dans le dos.</p><div class="defile"><table class="cal">'
           f'<tr><th>Unité</th><th>Tendance</th><th>EMA 20</th><th>EMA 50</th><th>Détail</th></tr>{lignes}</table></div>'
           f'</section>') if mt else ""
    pv = a.get("profil_vol")
    pv_txt = ""
    if pv:
        pv_txt = (f'<p class="sous">Sur {pv["jours"]} séances : amplitude moyenne de {nb(pv["moy_jour"], 1)} $ par tranche de '
                  f'15 min. ' + (f'Aujourd\'hui, l\'or bouge <b>{nb(pv["ratio"], 2)} fois</b> la normale à ces heures-là. '
                                 if pv.get("ratio") else "")
                  + (f'Volatilité attendue sur les {HORIZON_H} prochaines heures : ±{nb(pv["sigma"], 0)} $ (un écart-type).'
                     if pv.get("sigma") else "") + '</p>')
    vol = (f'<section><h2>Profil de volatilité par heure (Paris)</h2><p class="pourquoi">Barres grises : amplitude moyenne '
           f'de chaque tranche de 15 minutes. Barres dorées : aujourd\'hui. Trait blanc : maintenant. Pointillés rouges : '
           f'annonces fortes du jour. Scalpe quand le marché bouge, pas quand il dort.</p>'
           f'{graphique_profil_vol(pv, data.get("cal"))}{pv_txt}</section>')
    s = a.get("seance") or {}
    pvs = []
    for lib, p in (("Veille", s.get("profil_veille")), ("Semaine en cours", s.get("profil_semaine"))):
        if p:
            pvs.append(f'<span>{lib}</span><span>POC {nb(p["poc"] + AJUST, 1)} · zone de valeur {nb(p["val"] + AJUST, 1)} – '
                       f'{nb(p["vah"] + AJUST, 1)}</span>')
    prof = (f'<section><h2>Profil de volume</h2><p class="pourquoi">Le POC est le prix où il s\'est le plus échangé ; la zone '
            f'de valeur contient 70 % des échanges. Le prix a tendance à revenir vers le POC, et les bornes de la zone de '
            f'valeur servent souvent de support ou de résistance. Calcul approché à partir des bougies 15 min du future.</p>'
            f'<div class="kv" style="max-width:640px">{"".join(pvs)}</div></section>') if pvs else ""
    return bloc_details_tech(a) + bloc_concepts(a) + mat + bloc_intermarche(a) + bloc_carnets(a) + vol + prof + bloc_seance(a)


def bloc_reactions(a):
    r = a.get("reactions") or []
    if not r:
        return ('<section><h2>Réactions passées de l\'or aux annonces</h2><p class="vide">Pas encore assez de données : '
                'la base se remplit à chaque annonce publiée.</p></section>')
    lignes = "".join(f'<tr><td>{esc(x["type"])}</td><td class="num">{x["n"]}</td><td class="num">{nb(x["med_rng"], 0)} $</td>'
                     f'<td class="num">{nb(x["max_rng"], 0)} $</td><td class="num">{nb(x["med_mv"], 0)} $</td>'
                     f'<td class="num">{nb(x["retour_pct"], 0)} %</td><td class="num">{nb(x["hausse_pct"], 0)} %</td>'
                     f'<td class="flat">{esc(x["precision"])}</td></tr>' for x in r)
    return (f'<section><h2>Réactions passées de l\'or aux annonces</h2><p class="pourquoi">Ce que l\'or a fait dans '
            f'l\'heure qui a suivi chaque type d\'annonce. Amplitude = écart entre le plus haut et le plus bas ; retournement = '
            f'le mouvement à 1 h va dans le sens inverse des 15 premières minutes. Sert à placer tes stops et à décider si tu '
            f'trades après l\'annonce ou si tu attends.</p><div class="defile"><table class="cal"><tr><th>Annonce</th>'
            f'<th>Nombre</th><th>Amplitude médiane</th><th>Amplitude max</th><th>Mouvement médian à 1 h</th>'
            f'<th>Retournements</th><th>Hausse</th><th>Précision</th></tr>{lignes}</table></div></section>')


def bloc_adjudications(a):
    ad = a.get("adjudic")
    if not ad:
        return ""
    av = "".join(f'<tr><td class="num">{date_fr(x["date"], True)}</td><td>{esc(x["terme"])}{" (réouverture)" if x["reouv"] else ""}</td>'
                 f'<td class="num">{nb(x["montant"], 0) + " Md$" if x["montant"] else "n.d."}</td></tr>' for x in ad["avenir"])
    ps = "".join(f'<tr><td class="num">{date_fr(x["date"])}</td><td>{esc(x["terme"])}</td>'
                 f'<td class="num">{nb(x["rendement"], 3)} %</td><td class="num">{nb(x["btc"], 2)}</td>'
                 f'<td class="num">{nb(x["btc_moy"], 2)}</td><td class="num">{nb(x["indirect"], 0)} %</td>'
                 f'<td class="{({"faible": "down", "solide": "up"}).get(x["qualite"], "flat")}">{esc(x["qualite"] or "n.d.")}</td></tr>'
                 for x in ad["passees"])
    return (f'<section><h2>Adjudications du Trésor américain</h2><p class="pourquoi">Quand la demande pour la dette américaine '
            f'faiblit (ratio de couverture sous sa moyenne, peu d\'acheteurs étrangers), les taux longs montent : pression '
            f'sur l\'or à court terme, mais soutien au thème de défiance envers les actifs US. Résultats publiés vers 19 h '
            f'(heure de Paris).</p><div class="grille2"><div><h3 style="margin-top:0">À venir</h3><table class="cal">'
            f'<tr><th>Date</th><th>Titre</th><th>Montant</th></tr>{av or "<tr><td>Aucune</td></tr>"}</table></div>'
            f'<div><h3 style="margin-top:0">Résultats récents</h3><div class="defile"><table class="cal"><tr><th>Date</th>'
            f'<th>Titre</th><th>Rendement</th><th>Couverture</th><th>Moyenne</th><th>Étrangers</th><th>Demande</th></tr>'
            f'{ps or "<tr><td>Aucun</td></tr>"}</table></div></div></div></section>')


def bloc_plan(a):
    v, p, bi, se = a.get("vue"), a.get("plan"), a.get("bilan"), a.get("semaine")
    out = ""
    if v:
        comp = "".join(f'<span>{lib}</span>{barre_score(v["composantes"][k])}<span class="v">{nb(v["composantes"][k], 2, True)}</span>'
                       for k, lib in (("fondamental", "Fondamental"), ("tendance", "Technique"), ("momentum", "Momentum du jour"),
                                      ("sentiment", "Sentiment")))
        sent = "".join(f'<tr><td>{esc(l)}</td><td class="{t}">{esc(d)}</td></tr>' for l, d, t in v["sentiment"])
        cat = "".join(f"<li>{esc(c)}</li>" for c in v["catalyseurs"]) or '<li class="vide">Aucun catalyseur majeur identifié.</li>'
        out += (f'<section><h2>Où va le marché : <span class="{({1: "up", -1: "down"}).get(v["sens"], "flat")}">'
                f'{v["direction"]}</span> <small class="flat">conviction {esc(v["conviction"])}, horizon {HORIZON_H} h</small></h2>'
                f'<div class="grille2"><div><p class="scen"><b>Scénario central.</b> {esc(v["central"])}</p>'
                + (f'<p class="scen alt"><b>Scénario alternatif.</b> {esc(v["alternatif"])}</p>' if v["alternatif"] else "")
                + (f'<p class="sous">Fourchette probable à {HORIZON_H} h : <b>{nb(v["fourchette"][0], 0)} – {nb(v["fourchette"][1], 0)}</b> '
                   f'(deux chances sur trois), à partir du dernier relevé à {nb(v["prix"], 1)}.</p>' if v["fourchette"] else "")
                + f'<h3>Pourquoi</h3><p class="bloc-txt">{esc("Parce que " + " ; ".join(v["pourquoi"]) + ".")}</p>'
                + "".join(f'<p class="nu">Conviction réduite : {esc(x)}.</p>' for x in v["alertes"])
                + f'</div><div><h3 style="margin-top:0">Composantes du score</h3><div class="decomp">{comp}</div>'
                f'<h3>Sentiment</h3><table class="cal">{sent or "<tr><td>n.d.</td></tr>"}</table>'
                f'<h3>Catalyseurs à venir</h3><ul class="suite">{cat}</ul></div></div>'
                f'<p class="legende" style="margin-top:12px">Score = {nb(POIDS_VUE["fondamental"] * 100, 0)} % fondamental, '
                f'{nb(POIDS_VUE["tendance"] * 100, 0)} % tendance multi-unités, {nb(POIDS_VUE["momentum"] * 100, 0)} % momentum '
                f'du jour, {nb(POIDS_VUE["sentiment"] * 100, 0)} % sentiment. C\'est une lecture probabiliste, pas une '
                f'certitude : l\'onglet Suivi mesure son taux de réussite réel.</p>{ligne_fiabilite(a)}</section>')
    if p:
        ses, aucune = "", '<li class="vide">Pas d’annonce</li>'
        for x in p["seances"]:
            ann = "".join(f'<li><span class="num">{_hm(d)}</span> {esc(t)}{" (fort impact)" if imp == "High" else ""}</li>'
                          for d, t, imp in x["annonces"])
            ses += (f'<div class="fiche"><div class="t">{esc(x["nom"])} <small class="flat">{int(x["debut"]):02d}:'
                    f'{int(x["debut"] % 1 * 60):02d} – {int(x["fin"]):02d}:{int(x["fin"] % 1 * 60):02d}</small></div>'
                    f'<p>Activité habituelle : <b>{esc(x["activite"])}</b></p><ul class="suite">'
                    f'{ann or aucune}</ul></div>')
        niv_h = "".join(f'<li><span class="num">{nb(v_, 1)}</span> {esc(l)}</li>' for l, v_ in p["dessus"])
        niv_b = "".join(f'<li><span class="num">{nb(v_, 1)}</span> {esc(l)}</li>' for l, v_ in p["dessous"])
        regles = "".join(f"<li>{esc(r)}</li>" for r in p["regles"])
        out += (f'<section><h2>{esc(p["titre"])}</h2><p class="bloc-txt">{esc(p["contexte"])}</p>'
                f'<div class="grille3"><div><h3 style="margin-top:0">Au-dessus</h3><ul class="suite">{niv_h}</ul>'
                f'<h3>En dessous</h3><ul class="suite">{niv_b}</ul></div>'
                f'<div style="grid-column:span 2"><h3 style="margin-top:0">Règles du jour</h3><ol class="routine">{regles}</ol></div></div>'
                f'<h3>Agenda par séance</h3><div class="fiches">{ses}</div></section>')
    if bi:
        ann = "".join(f'<tr><td class="num">{esc(x["heure"])}</td><td>{esc(x["titre"])}</td><td class="num">{esc(x["reel"])}</td>'
                      f'<td class="num">{esc(x["prev"])}</td><td class="num {classe_effet(x["mv"], 1)}">'
                      f'{nb(x["mv"], 1, True) + " $" if x["mv"] is not None else "n.d."}</td></tr>' for x in bi["annonces"])
        out += (f'<section><h2>Bilan de la séance du {date_fr(bi["date"])}</h2>'
                + "".join(f'<p class="bloc-txt">{esc(t)}</p>' for t in bi["phrases"])
                + (f'<table class="cal"><tr><th>Heure</th><th>Annonce</th><th>Réel</th><th>Prévu</th><th>Or sur 1 h</th></tr>'
                   f'{ann}</table>' if ann else "") + '</section>')
    if se:
        jours = ""
        for d, evs in se["jours"]:
            li = "".join(f'<li><span class="num">{_hm(t)}</span> {esc(x)}</li>' for t, x in sorted(evs, key=lambda z: z[0]))
            jours += f'<div class="fiche"><div class="t">{date_fr(d)}</div><ul class="suite">{li}</ul></div>'
        out += (f'<section><h2>Les jours à venir</h2><p class="pourquoi">Annonces à fort impact, décision de la Fed et '
                f'adjudications du Trésor. Plus haut et plus bas de la semaine : {nb((se["haut_sem"] or 0) + AJUST, 1) if se["haut_sem"] else "n.d."} '
                f'et {nb((se["bas_sem"] or 0) + AJUST, 1) if se["bas_sem"] else "n.d."}.</p>'
                f'<div class="fiches">{jours or "<p class=vide>Rien de majeur au calendrier.</p>"}</div></section>')
    return out


def bloc_suivi(a):
    st = a.get("suivi")
    if not st:
        return ""
    conv = "".join(f'<span>Conviction {esc(c)}</span><span>{nb(t, 0)} % sur {n}</span>' for c, n, t in st["par_conv"])
    lignes = ""
    for p in reversed(st["derniers"]):
        if p.get("res") not in ("ok", "ko"):
            continue
        d = datetime.fromisoformat(p["ts"])
        lignes += (f'<tr><td class="num">{date_fr(d, True)}</td><td>{({1: "Haussière", -1: "Baissière"}).get(p["sens"], "Neutre")} '
                   f'<span class="flat">({esc(p["conv"])})</span></td><td class="num">{nb(p["prix"] + AJUST, 1)}</td>'
                   f'<td class="num {classe_effet(p.get("mv"), 1)}">{nb(p.get("mv"), 1, True)} $</td>'
                   f'<td class="{"up" if p["ok"] else "down"}">{"juste" if p["ok"] else "fausse"}</td></tr>')
    taux = (f'<b>{nb(st["taux_dir"], 0)} %</b> de bonnes directions sur {st["n_dir"]} prévisions haussières ou baissières'
            if st["taux_dir"] is not None else "pas encore de prévision directionnelle évaluée")
    return (f'<section><h2>Suivi des prévisions du terminal (30 jours)</h2><p class="pourquoi">Chaque heure de marché, le '
            f'terminal enregistre sa vue ; {HORIZON_H} heures plus tard, il vérifie si le prix est allé dans le sens annoncé. '
            f'Au-dessus de 55 % sur un échantillon d\'au moins 50 prévisions, la vue apporte une information utile ; autour de '
            f'50 %, elle ne vaut pas mieux que le hasard.</p>'
            f'<div class="grille2"><div><p class="bloc-txt">{taux}.</p><div class="kv">{conv}'
            f'<span>Vues neutres justes (marché resté calme)</span><span>{nb(st["taux_neutre"], 0) + " %" if st["taux_neutre"] is not None else "n.d."} '
            f'sur {st["n_neutre"]}</span><span>Prix resté dans la fourchette annoncée</span>'
            f'<span>{nb(st["taux_fourchette"], 0) + " %" if st["taux_fourchette"] is not None else "n.d."} (68 % attendu)</span>'
            f'<span>Prévisions en attente d\'évaluation</span><span>{st["en_attente"]}</span></div></div>'
            f'<div><h3 style="margin-top:0">Dernières prévisions évaluées</h3><table class="cal"><tr><th>Émise</th><th>Vue</th>'
            f'<th>Prix</th><th>Après {HORIZON_H} h</th><th>Résultat</th></tr>{lignes or "<tr><td>Aucune</td></tr>"}</table></div>'
            f'</div></section>')


# ---------------------------------------------------------------------------
# VERSION 6 : MODE TRADER, FRAÎCHEUR, INTERMARCHÉ, RÉGIMES, RÉACTIONS
# ---------------------------------------------------------------------------

COUL = {1: "up", -1: "down", 0: "flat", 2: "warn"}  # vert haussier, rouge baissier, gris neutre, orange attente / risque


def fraicheur(typ, quand=None):
    """Badge de fraîcheur : LIVE, il y a X min (relevé), DAILY, WEEKLY."""
    if typ == "live":
        return '<span class="fr live">LIVE</span>'
    if typ == "rel":
        iso = quand.isoformat() if hasattr(quand, "isoformat") else (quand or maintenant().isoformat())
        return f'<span class="fr rel" data-t="{esc(iso)}">relevé</span>'
    lib = {"daily": "DAILY", "weekly": "WEEKLY"}.get(typ, typ.upper())
    d = f" · {quand.day} {MOIS_FR[quand.month - 1]}" if hasattr(quand, "day") else ""
    return f'<span class="fr {typ}">{lib}{d}</span>'


def bloc_trader(a):
    tr, ev = a.get("trader") or {}, a.get("annonce")
    s = a.get("seance") or {}
    b = tr.get("biais", 0)
    tuile = lambda k, v, sub="", cls="", id_="": (f'<div class="tu {cls}"{(" id=" + chr(34) + id_ + chr(34)) if id_ else ""}>'
                                                 f'<span class="k">{k}</span><b>{v}</b><small>{sub}</small></div>')
    im, grs = a.get("intermarket"), a.get("grs")
    im_txt = im["statut"] if im else "n.d."
    im_cls = {"DIVERGENCE": "warn", "MIXED": "flat"}.get(im_txt, COUL.get(im["sens"] if im else 0, "flat"))
    der = (a.get("reactions_evt") or [None])[0]
    der_html = ""
    if der and der["date"] >= maintenant() - timedelta(hours=3) and der["reac"].get("or_60") is not None:
        der_html = (f'<p class="pied">Dernière annonce : <b>{esc(der["nom"])}</b> {esc(der["reel"])} contre {esc(der["prevision"])} '
                    f'prévu · or {nb(der["reac"].get("or_usd_60"), 1, True)} $ en 1 h. {esc(der["interpretation"])}</p>')
    sens = tr.get("sens", 0)
    t_ = a.get("technique") or {}
    bt = t_.get("sens", 0) if t_ else 0
    bt_txt = {1: "Haussier", -1: "Baissier", 0: "Neutre"}[bt] if t_ else "n.d."
    s1 = next((x for x in t_.get("structure", []) if x["nom"] == "1 heure"), None)
    bt_sub = (f'score {nb(t_["score"], 2, True)}' + (f' · 1 h {s1["etat"].lower()} ({s1["label_sommet"] or "?"} + {s1["label_creux"] or "?"})' if s1 else "")) if t_ else ""
    return (f'<section id="trader" class="trader"><div class="tr-tete"><h2>Mode Trader</h2>'
            f'<span>Analyse {fraicheur("rel", s.get("maj"))} · prix <span class="fr live" id="tr-badge">LIVE</span> <b id="tr-px">'
            f'{nb(tr.get("ref"), 2)}</b></span></div>'
            f'<div class="tr-l1">'
            + tuile("Bias fondamental", esc(tr.get("biais_txt", "n.d.")),
                    f'score {nb(a["biais"]["total"], 0, True) if a["biais"]["total"] else "0"} sur {a["biais"]["n"]}', COUL.get(b, "flat"))
            + tuile("Bias technique", esc(bt_txt), bt_sub, COUL.get(bt, "flat"))
            + tuile("Signal", "aucun", "", "flat", "tr-signal")
            + tuile("Action", "…", "", "warn large", "tr-action")
            + '</div><div class="tr-l2">'
            + tuile("Setup", esc(tr.get("setup", "n.d.")), "playbook technico-fondamental", COUL.get(sens, "warn") if sens else "warn")
            + tuile("Intermarket", im_txt, (f'Gold Relative Strength {nb(grs["score"], 0, True)}' if grs else ""), im_cls)
            + tuile("Prochaine news", esc(fiche_annonce(ev, a)["nom"]) if ev else "aucune", "", "", "tr-news")
            + f'</div>{der_html}</section>')


def bloc_intermarket(a):
    im, grs = a.get("intermarket"), a.get("grs")
    if not im:
        return ""
    fl = lambda v, impl, d, u: (f'<td class="num {COUL.get(impl, "flat")}">{nb(v, d, True)}{u} '
                                f'{({1: "▲", -1: "▼"}).get(impl, "·")}</td>') if v is not None else '<td class="num flat">n.d.</td>'
    lignes = ""
    for l in im["lignes"]:
        badge = l["statut"]
        cls = "warn" if badge == "DIVERGENCE" else ("flat" if badge == "MIXED" else COUL.get(l["consensus"], "flat"))
        frais = fraicheur("daily") if l["horizon"] in ("1 jour", "5 jours") else '<span class="fr rel15">15 MIN</span>'
        lignes += (f'<tr><td>{esc(l["horizon"])} {frais}</td><td class="num {COUL.get(l["sens_or"], "flat")}">{nb(l["or"], 2, True)} %</td>'
                   + fl(l["dxy"], l["impl"][0], 2, " %") + fl(l["us2"], l["impl"][1], 1, " pb") + fl(l["reel"], l["impl"][2], 1, " pb")
                   + f'<td><span class="statut {cls}">{badge}</span></td><td class="flat">{esc(l["lecture"])}</td></tr>')
    g = ""
    if grs:
        pos = (grs["score"] + 100) / 2
        g = (f'<h3>Gold Relative Strength : <span class="{COUL.get(1 if grs["score"] > 25 else (-1 if grs["score"] < -25 else 0))}">'
             f'{nb(grs["score"], 0, True)}</span> <small class="flat">({esc(grs["etat"])})</small></h3>'
             f'<div class="pctbar"><i style="left:50%"></i><b style="left:{pos:.1f}%"></b></div>'
             f'<div class="pctleg"><span>Plus faible que prévu</span><span>en ligne</span><span>Plus forte que prévu</span></div>'
             f'<p class="legende" style="margin-top:6px">Écart entre ce que l\'or a fait et ce que le dollar et les taux '
             f'expliquent, sur 1 h, 4 h, 1 jour et 5 jours. Un or plus fort que prévu signale des acheteurs de fond ; plus '
             f'faible, des vendeurs de fond.</p>')
    return (f'<section id="intermarket"><h2>Intermarket : <span class="statut {"warn" if im["statut"] == "DIVERGENCE" else "flat" if im["statut"] == "MIXED" else COUL.get(im["sens"], "flat")}">'
            f'{im["statut"]}</span></h2><p class="pourquoi">L\'or face au dollar (DXY), au taux 2 ans et au taux réel 10 ans. '
            f'Chaque flèche indique ce que le mouvement du moteur implique pour l\'or (▲ favorable, ▼ défavorable). '
            f'CONFIRMED : l\'or suit ses moteurs. DIVERGENCE : il va contre (signal fort, à surveiller). MIXED : pas de '
            f'message clair.</p><div class="defile"><table class="cal"><tr><th>Horizon</th><th>Or</th><th>DXY</th>'
            f'<th>US 2 ans</th><th>Réel 10 ans</th><th>Statut</th><th>Lecture</th></tr>{lignes}</table></div>{g}</section>')


def bloc_regimes(a, compact=False):
    rg = a.get("regimes")
    if not rg:
        return ""
    lignes = ""
    for d in sorted(rg["dimensions"], key=lambda x: -x["intensite"]):
        cls = COUL.get(d["effet"], "flat")
        eff = {1: "favorable à l'or", -1: "défavorable à l'or", 0: "neutre", 2: "risque (liquidations possibles)"}[d["effet"]]
        lignes += (f'<span>{esc(d["nom"])}</span><div class="jauge-s"><i class="{cls}" style="width:{d["intensite"]:.0f}%"></i></div>'
                   f'<span class="v {cls}">{nb(d["intensite"], 0)}</span>'
                   + ("" if compact else f'<span class="{cls}">{eff}</span><span class="flat">{esc(d["etat"])}</span>'))
    dom = rg["dominant"]
    return (f'<section id="regimes"><h2>Régime de marché : <span class="{COUL.get(dom["effet"], "flat")}">{esc(dom["nom"])}</span> '
            f'<small class="flat">domine (intensité {nb(dom["intensite"], 0)})</small></h2>'
            f'<p class="pourquoi">Six forces qui pilotent l\'or, classées par intensité. La plus forte dicte à quels '
            f'indicateurs donner la priorité aujourd\'hui. Vert : favorable à l\'or, rouge : défavorable, orange : risque.</p>'
            f'<div class="regimes{" compact" if compact else ""}">{lignes}</div></section>')


def bloc_reactions_evt(a):
    rs = a.get("reactions_evt") or []
    if not rs:
        return ('<section><h2>Event Reaction Engine</h2><p class="vide">Aucune annonce majeure publiée cette semaine pour '
                'l\'instant. Après chaque CPI, NFP, PCE ou décision de la Fed, la réaction du dollar, du 2 ans et de l\'or '
                's\'affiche ici.</p></section>')
    cartes = ""
    for r in rs:
        rc = r["reac"]
        sp = {1: "dure (dollar ▲)", -1: "souple (dollar ▼)", 0: "conforme"}[r["sens_surprise"]]
        def val(k, d, u):
            v = rc.get(k)
            if v is None:
                return '<span class="flat">n.d.</span>'
            effet = (1 if v > 0 else -1) * (1 if k.startswith("or") else -1) if v else 0
            return f'<span class="{COUL.get(effet)}">{nb(v, d, True)}{u}</span>'
        cartes += (f'<div class="fiche"><div class="t">{esc(r["nom"])}</div><div class="q">{date_fr(r["date"], True)}</div>'
                   f'<div class="pp"><div><span>Consensus</span>{esc(r["prevision"] or "n.d.")}</div><div><span>Réel</span>'
                   f'<b>{esc(r["reel"])}</b></div><div><span>Surprise</span>{esc(sp)}</div></div>'
                   f'<div class="kv"><span>DXY 15 min / 1 h</span><span>{val("dxy_15", 2, " %")} / {val("dxy_60", 2, " %")}</span>'
                   f'<span>US 2 ans 15 min / 1 h</span><span>{val("us2_15", 1, " pb")} / {val("us2_60", 1, " pb")}</span>'
                   f'<span>Or 15 min / 1 h</span><span>{val("or_15", 2, " %")} / {val("or_60", 2, " %")}</span></div>'
                   f'<p class="nu">{esc(r["interpretation"])}</p></div>')
    return (f'<section id="reactions"><h2>Event Reaction Engine</h2><p class="pourquoi">Après chaque annonce majeure : '
            f'consensus, chiffre réel, surprise, réaction du dollar, du taux 2 ans et de l\'or, puis lecture. Une réaction '
            f'inverse à la surprise est souvent le signal le plus intéressant de la journée.</p>'
            f'<div class="fiches">{cartes}</div></section>')


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
            f'<div class="prix-live"><span class="k">XAU/USD <span class="fr live" id="px-badge">LIVE</span></span>'
            f'<b id="px-live">{nb((a.get("trader") or {}).get("ref") or ((a.get("seance") or {}).get("prix", 0) + AJUST), 2)}</b>'
            f'<small id="px-age">référence de l\'analyse</small></div>'
            f'<div class="horloges">{horl}</div>'
            f'<div class="marche"><span class="k">Marché de l\'or</span><b><span class="led" id="led-marche"></span>'
            f'<span id="txt-marche">...</span></b></div>'
            f'<div class="fraicheur"><span class="k">Analyses</span><b id="maj" data-maj="{maj.isoformat()}">'
            f'{date_fr(maj, True)}</b></div>'
            f'<div class="fraicheur prochaine"><span class="k">Prochaine annonce forte</span><b id="cpt-barre">...</b></div>'
            f'<div class="actions"><button type="button" id="btn-mode" class="mode-btn">Mode : Analyst</button>'
            f'<button type="button" id="btn-reglages" class="opt">Réglages</button>'
            f'<button type="button" id="btn-son">Son : coupé</button>'
            f'<button type="button" id="btn-notif" class="opt">Activer les alertes bureau</button>'
            f'<button type="button" id="copier" class="or">Copier le brief</button></div></div>'
            f'<div class="alerte" id="alerte" role="status" aria-live="assertive"></div></header>')


def bloc_reglages():
    champs = [("capital", "Taille du compte ($)", "1000"), ("risque_pct", "Risque par trade (% du compte)", "0.1"),
              ("spread", "Spread moyen de ton broker sur XAU/USD ($)", "0.05")]
    lignes = "".join(f'<label>{esc(lib)}<input type="number" step="{pas}" min="0" data-cle="{cle}" '
                     f'placeholder="{COMPTE[cle]}"></label>' for cle, lib, pas in champs)
    return (f'<dialog id="reglages" aria-label="Réglages"><form method="dialog"><h2>Taille de position</h2>'
            f'<p class="pourquoi">Utilisés par le contrôle avant l\'entrée pour calculer le nombre de lots et le rapport '
            f'gain / risque. Mémorisés dans ce navigateur. Laisse un champ vide pour garder la valeur par défaut, '
            f'affichée en gris.</p><div class="reg-champs">{lignes}</div>'
            f'<div class="reg-actions"><button type="button" id="reg-defaut">Valeurs par défaut</button>'
            f'<button type="submit" value="annuler">Fermer</button>'
            f'<button type="button" id="reg-ok" class="or">Enregistrer</button></div></form></dialog>')


def bloc_bandeau_tv():
    conf = {"symbols": [{"proName": s, "description": lib} for s, lib in TV_BANDEAU], "showSymbolLogo": False,
            "isTransparent": True, "displayMode": "compact", "colorTheme": "dark", "locale": "fr"}
    return f'<div class="tape">{tv("embed-widget-ticker-tape.js", conf, "Cotations en direct (TradingView)", "height:46px")}</div>'


def bloc_strip(a, y, f):
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
        it.append((f"Vue {HORIZON_H} h", f'<span class="{ {1: "up", -1: "down"}.get(v["sens"], "flat") }">{v["direction"]}</span>',
                   f'conviction {v["conviction"]}'))
    r = a.get("risque")
    if r:
        it.append(("Appétit risque", r["etat"], f'score {nb(r["score"], 0, True)}'))
    pb = a.get("playbook")
    if pb:
        it.append(("Playbook", f'<span class="{ {1: "up", -1: "down"}.get(pb["sens"], "flat") }">'
                               f'{esc(MATRICE_PB[(pb["fond"], pb["tech"])])}</span>',
                   f'technique {nb(a["technique"]["score"], 2, True)}, {a["technique"]["regime"]["nom"]}'))
    cn = a.get("carnets")
    if cn and cn.get("part_acheteurs") is not None:
        pa = cn["part_acheteurs"]
        it.append(("Foule OANDA", f'<span class="{"down" if pa >= 65 else ("up" if pa <= 35 else "flat")}">{nb(pa, 0)} % ach.</span>',
                   "contrarien" if pa >= 65 or pa <= 35 else "équilibrée"))
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
        it.append(("Écart future-spot", f'{nb(a["base"], 1, True)} $',
                   "mesuré (XAUS)" if (a.get("ecart") or {}).get("mesure") else "estimé (courbe)"))
    corps = "".join(f'<div class="it"><span class="k">{lib}</span><span class="v">{v}</span><span class="s">{sub}</span></div>'
                    for lib, v, sub in it)
    return f'<div class="strip analyst" data-zone="strip">{corps}</div>'


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
            f'{div}<p><a href="#analyse" data-aller="fondamental">Lecture complète et analyste IA</a></p></div>')


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
        lignes += (f'<tr><td{" class=liq" if n.get("carnet") else ""}>{esc(n["lib"])}</td><td class="num">{nb(n["cfd"], 1)}</td>'
                   f'<td class="num dist flat" data-v="{n["cfd"]:.2f}">{nb(n["dist"], 1, True)}</td></tr>')
    if not fait:
        lignes += f'<tr class="ici"><td>Dernier relevé</td><td class="num">{nb(s["prix"] + AJUST, 1)}</td><td></td></tr>'
    vw = s.get("vwap")
    pos = ("au-dessus du VWAP : acheteurs aux commandes" if s["prix"] > vw else "sous le VWAP : vendeurs aux commandes") if vw else ""
    ses = " · ".join(f'{x["nom"]} <b>{nb(x["haut"] + AJUST, 1)}</b>/<b>{nb(x["bas"] + AJUST, 1)}</b>'
                     for x in s["seances"] if x["haut"] is not None)
    return (f'<table class="niv-c">{lignes}</table>'
            f'<p class="pied">ATR 14 j <b>{nb(s["atr"], 1)} $</b> · consommé <b>{nb(s["pct_atr"], 0)} %</b>'
            f'{" · prix " + esc(pos) if pos else ""}</p><p class="pied">{ses}</p>'
            f'<p class="pied"><a href="#analyse" data-aller="technique">Graphique des niveaux et détail des séances</a></p>')


def bloc_moteurs_compact(a):
    li = ""
    for m in a["moteurs_jour"]:
        fl = {"up": '<span class="up">▲</span>', "down": '<span class="down">▼</span>'}.get(m["ton"], '<span class="flat">●</span>')
        li += (f'<li title="{esc(m["mecanisme"])}"><span>{esc(m["lib"])}</span><span class="num {m["ton"]}">{esc(m["var"])}</span>'
               f'<span class="z">{nb(m["z"], 1, True)} σ</span>{fl}</li>')
    return (f'<ul class="mot-c">{li}</ul><p class="pied">▲ favorable à l\'or · ▼ défavorable · σ = intensité du '
            f'mouvement. <a href="#analyse" data-aller="fondamental">Carte de chaleur</a></p>')


def evenements_json(data, a):
    now = maintenant()
    out = []
    for e in data.get("cal") or []:
        if e["impact"] != "High" or e["date"] < now - timedelta(hours=1):
            continue
        fi = fiche_annonce(e, a)
        out.append({"t": e["date"].isoformat(), "titre": e["titre"], "nom": fi["nom"], "prev": e["precedent"],
                    "fcst": e["prevision"], "reel": e["reel"], "haut": fi["haut"], "bas": fi["bas"],
                    "nuance": fi["nuances"][0] if fi["nuances"] else "", "histo": fi.get("histo", "")})
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
            f'<small>{fraicheur("live")} {esc(TV_OR)} · TradingView</small></div><div class="pb nopad">{chart}</div></div>'
            f'<div class="minis analyst">{minis}</div></div>'
            f'<div class="col col-c"><div class="panel analyst" id="p-vue"><div class="ph"><span class="k">Où va le marché</span>'
            f'<small>{fraicheur("rel", (a.get("seance") or {}).get("maj"))} <a href="#analyse" data-aller="synthese">plan complet</a></small></div>'
            f'<div class="pb" data-zone="vue">{bloc_vue_cockpit(a)}</div></div>'
            f'<div class="panel" id="p-ctl"><div class="ph"><span class="k">Contrôle avant l\'entrée</span>'
            f'<small>touches A et V</small></div><div class="pb">{bloc_controle()}</div></div></div>'
            f'<div class="col col-d"><div class="panel" id="p-evt"><div class="ph"><span class="k">Prochaine annonce à fort impact</span>'
            f'<small><a href="#analyse" data-aller="annonces">scénarios</a></small></div>'
            f'<div class="pb"><div id="evt-carte" data-cle="x"><p class="vide">Chargement...</p></div>'
            f'<div class="k" style="margin-top:10px">Ensuite</div><ul class="suite" id="evt-suite"></ul></div></div>'
            f'<div class="panel analyst" id="p-niv"><div class="ph"><span class="k">Niveaux de séance</span>'
            f'<small>{fraicheur("rel", (a.get("seance") or {}).get("maj"))} {"XAU/USD spot" if a.get("base") is not None else "future COMEX"}, écart au prix</small></div>'
            f'<div class="pb" data-zone="niv">{bloc_niveaux_compact(a)}</div></div>'
            f'<div class="panel analyst" id="p-news"><div class="ph"><span class="k">Fil d\'actualité en direct</span>'
            f'<small>{fraicheur("live")} TradingView</small></div><div class="pb nopad">{news}</div></div></div></main>')


ONGLETS = [("synthese", "Synthèse"), ("fondamental", "Fondamental"), ("technique", "Technique"),
           ("annonces", "Annonces"), ("actus", "Actualités"), ("suivi", "Suivi et méthode")]


def bloc_onglets(data, a, ia):
    y, f = data["yahoo"], data["fred"]
    contenus = {
        # 1. Le lien entre les deux : ce qu'il faut faire maintenant
        "synthese": (bloc_synthese_tech(a) + bloc_intermarket(a) + bloc_regimes(a, compact=True) + bloc_plan(a) +
                     f'<section>{bloc_routine()}</section>'),
        # 2. Tout le fondamental
        "fondamental": (bloc_regimes(a) + bloc_lecture(a) + bloc_biais_detail(a) + bloc_ia(ia) + bloc_moteurs(a) + bloc_risque(a) +
                        f'<section>{bloc_correlations(a)}</section>' + bloc_fed(y, f, a) + bloc_marches(y, f, a) +
                        bloc_flux(a) + bloc_graphiques(y, f, data["cot"], a)),
        # 3. Toute la technique
        "technique": bloc_technique(a, data),
        "annonces": bloc_reactions_evt(a) + bloc_calendrier(data["cal"], a) + bloc_reactions(a) + bloc_adjudications(a),
        "actus": bloc_actus(data.get("news"), data.get("fed_off"), a),
        "suivi": bloc_suivi(a) + bloc_methode(),
    }
    nav = "".join(f'<button type="button" role="tab" data-tab="{i}" aria-selected="false">{esc(t)}<kbd>{(k + 1) % 10}</kbd></button>'
                  for k, (i, t) in enumerate(ONGLETS))
    ta_tv = tv("embed-widget-technical-analysis.js", {"interval": "15m", "width": "100%", "height": "100%",
                                                        "isTransparent": True, "symbol": TV_OR, "showIntervalTabs": True,
                                                        "displayMode": "multiple", "locale": "fr", "colorTheme": "dark"},
               "Consensus technique en direct (TradingView)")
    cal_tv = tv("embed-widget-events.js", {"colorTheme": "dark", "isTransparent": True, "width": "100%", "height": "100%",
                                           "locale": "fr", "importanceFilter": "0,1", "countryFilter": "us"},
                "Calendrier économique en direct (TradingView)")
    panneaux = ""
    for i, t in ONGLETS:
        extra = ""
        if i == "annonces":
            extra = (f'<section><h2>Calendrier en direct</h2><p class="pourquoi">Les chiffres réels s\'affichent ici dès '
                     f'leur publication (TradingView).</p><div class="tv-cal">{cal_tv}</div></section>')
        if i == "technique":
            extra = (f'<section><h2>Consensus technique en direct</h2><p class="pourquoi">Synthèse des moyennes mobiles et '
                     f'oscillateurs sur plusieurs unités de temps (TradingView). À utiliser comme confluence, pas comme '
                     f'signal d\'entrée.</p><div class="tv-cal" style="height:460px">{ta_tv}</div></section>')
        panneaux += (f'<div class="tabpanel" id="tab-{i}" role="tabpanel" aria-label="{esc(t)}">'
                     f'<div data-zone="t-{i}">{contenus[i]}</div>{extra}</div>')
    return (f'<nav class="onglets analyst" id="analyse" role="tablist" aria-label="Analyse approfondie">{nav}</nav>'
            f'<div class="contenu analyst">{panneaux}<div data-zone="sources">{bloc_sources()}</div></div>')


def rendre_html(data, a, brief, ia=None, watch_min=None, site=False):
    y, f = data["yahoo"], data["fred"]
    refresh = f'<meta http-equiv="refresh" content="{int(watch_min * 60) + 60}">' if (watch_min and not site) else ""
    cfg = json.dumps({"alertes": ALERTES_MIN, "zone": 30, "refresh": RAFRAICHISSEMENT_MIN,
                      "site": bool(site), "seances": [[n, d, fn] for n, d, fn in SEANCES]})
    ctx_json = json.dumps(contexte_controle(a), ensure_ascii=False).replace("</", "<\\/")
    corps = (f'{bloc_barre(a)}{bloc_reglages()}{bloc_bandeau_tv()}{bloc_trader(a)}{bloc_strip(a, y, f)}{bloc_cockpit(a, y, f)}'
             f'{bloc_onglets(data, a, ia)}'
             f'<script type="application/json" id="evts" data-zone="evts">{evenements_json(data, a)}</script>'
             f'<script type="application/json" id="ctx" data-zone="ctx">{ctx_json}</script>'
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
    v = a.get("vue")
    if v:
        L.append(f"Vue de marché à {HORIZON_H} h : {v['direction']} (conviction {v['conviction']}, score {nb(v['score'], 2, True)}). "
                 f"{v['central']} {v['alternatif']}")
        if v["fourchette"]:
            L.append(f"Fourchette probable à {HORIZON_H} h (68 %) : {nb(v['fourchette'][0], 0)} - {nb(v['fourchette'][1], 0)}")
    if a.get("matrice"):
        L.append("Tendance : " + " ; ".join(f"{t['nom']} {t['label'].lower()}" for t in a["matrice"]))
    if a.get("risque"):
        L.append(f"Appétit pour le risque : {a['risque']['etat']} (score {nb(a['risque']['score'], 0, True)})")
    st = a.get("suivi")
    if st and st["taux_dir"] is not None:
        L.append(f"Fiabilité mesurée de la vue : {nb(st['taux_dir'], 0)} % sur {st['n_dir']} prévisions (30 jours)")
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
    t_, pb_ = a.get("technique"), a.get("playbook")
    if t_ and pb_:
        L.append(f"Technique : score {nb(t_['score'], 2, True)}, régime {t_['regime']['nom']}"
                 + (", volatilité comprimée" if t_["regime"]["compression"] else "")
                 + " ; structure " + ", ".join(f"{x['nom']} {({1: 'haussière', -1: 'baissière'}).get(x['tendance'], 'neutre')}"
                                              + (f" ({x['evenement']})" if x['evenement'] else "") for x in t_["structure"])
                 + (f" ; prix à {nb(t_['vwap']['z'], 1, True)} σ du VWAP" if t_.get("vwap") else ""))
        L.append(f"Playbook technico-fondamental : {pb_['titre']}. " + " ".join(pb_["etapes"]))
        av_ = t_.get("avances")
        if av_:
            morceaux = []
            if av_.get("pd"):
                morceaux.append(f"prix en {av_['pd']['zone']} ({nb(av_['pd']['pos'], 0)} % du range 1 h)")
            for nom, fb in av_["fibo"].items():
                if fb:
                    morceaux.append(f"OTE {nom} {nb(fb['ote'][0] + AJUST, 1)}-{nb(fb['ote'][1] + AJUST, 1)}")
            if av_["balayages"]:
                x = av_["balayages"][0]
                morceaux.append(f"balayage récent {'sous' if x['sens'] > 0 else 'au-dessus de'} {x['lib'].lower()}")
            if av_.get("declencheur"):
                morceaux.append(f"dernière bougie 15 min : {av_['declencheur']['nom'].lower()}")
            morceaux += [f"RSI {k} : {d['texte']}" for k, d in av_["divergences"].items() if d]
            L.append("Concepts avancés : " + " ; ".join(morceaux))
        if t_["confluences"]:
            L.append("Zones de confluence : " + " ; ".join(f"{nb(z['bas'], 1)}-{nb(z['haut'], 1)} (score {nb(z['score'], 1)} : "
                                                          f"{', '.join(z['elements'][:3])})" for z in t_["confluences"][:5]))
    cn = a.get("carnets")
    if cn:
        L.append(f"Carnet OANDA : {nb(cn.get('part_acheteurs'), 0)} % d'acheteurs ; poches de stops acheteurs "
                 + (", ".join(nb(x['prix'], 1) for x in cn.get('stops_acheteurs', [])) or "aucune")
                 + " ; poches de stops vendeurs " + (", ".join(nb(x['prix'], 1) for x in cn.get('stops_vendeurs', [])) or "aucune")
                 + f" ; acheteurs en perte {nb(cn.get('longs_pieges'), 0)} %, vendeurs en perte {nb(cn.get('shorts_pieges'), 0)} %")
    im = a.get("intermarche")
    if im:
        L.append("Confirmation intermarché : " + " ".join(l["texte"] for l in im["lignes"]))
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
    v = a.get("vue")
    if v and a.get("seance"):
        dessus, dessous = niveaux_autour(a, v["prix"])
        L.append(f"Niveaux clés en XAU/USD spot (dernier relevé {nb(v['prix'], 1)}) : au-dessus "
                 + ", ".join(f"{l} {nb(x, 1)}" for l, x in dessus[:5]) + " ; en dessous "
                 + ", ".join(f"{l} {nb(x, 1)}" for l, x in dessous[:5]))
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
             "si tu es d'accord avec la vue de marché du terminal, ce qui pourrait l'inverser, "
             "et les scénarios pour les prochaines annonces.")
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
         "effr": pd.Series([3.58] * (len(idx) - 80) + [3.83] * 80, index=idx), "sofr": pd.Series([3.60] * (len(idx) - 80) + [3.86] * 80, index=idx),
         "cible_bas": pd.Series([3.5] * (len(idx) - 80) + [3.75] * 80, index=idx),
         "cible_haut": pd.Series([3.75] * (len(idx) - 80) + [4.0] * 80, index=idx),
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
    debut = (now - timedelta(days=61)).replace(hour=0, minute=0, second=0, microsecond=0)
    ib_idx = pd.date_range(debut, now, freq="15min", tz=tz)
    ib_idx = ib_idx[(ib_idx.hour < 23) & (ib_idx.dayofweek < 5)]
    profil = [0.5 if h < 8 else (1.2 if 9 <= h < 11 else (1.8 if 14 <= h < 17 else 0.9)) for h in range(24)]
    px, lignes = float(y["or"].iloc[-1]) * 0.94, []
    for t in ib_idx:
        o = px
        c = o * (1 + random.gauss(0.00004, 0.0010 * profil[t.hour]))
        h, l = max(o, c) * (1 + abs(random.gauss(0, 0.0005 * profil[t.hour]))), min(o, c) * (1 - abs(random.gauss(0, 0.0005 * profil[t.hour])))
        lignes.append((o, h, l, c, random.randint(800, 5000) * profil[t.hour]))
        px = c
    barres = pd.DataFrame(lignes, index=ib_idx, columns=["open", "high", "low", "close", "volume"])
    heures = barres.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last",
                                        "volume": "sum"}).dropna()
    jidx = y["or"].index[-250:]
    cl = y["or"].iloc[-250:].values
    jours = pd.DataFrame({"open": cl * 0.998, "high": cl * 1.009, "low": cl * 0.991, "close": cl}, index=jidx)
    intraday = {"barres": barres, "jours": jours, "heures": heures}

    zq = {}
    for i in range(16):
        m = (now.month - 1 + i) % 12 + 1
        a = now.year + (now.month - 1 + i) // 12
        zq[(a, m)] = 3.83 + min(i, 4) * 0.07
    fut = float(barres["close"].iloc[-1])
    courbe = [{"libelle": l, "prix": fut + dp, "echeance": e, "proche": pr} for l, dp, e, pr in (
        ("oct. 2026", -8.0, date(2026, 10, 27), True), ("déc. 2026", 0.0, date(2026, 12, 27), False),
        ("févr. 2027", 26.0, date(2027, 2, 27), False), ("avr. 2027", 52.0, date(2027, 4, 27), False),
        ("juin 2027", 77.0, date(2027, 6, 27), False))]
    adjudic = {"avenir": [{"securityType": "Note", "securityTerm": t, "auctionDate": (now + timedelta(days=d)).strftime("%Y-%m-%dT00:00:00"),
                           "closingTimeCompetitive": "01:00 PM", "offeringAmount": str(m * 1e9), "reopening": "No"}
                          for t, d, m in (("2-Year", 1, 69), ("5-Year", 2, 70), ("7-Year", 3, 44))],
               "passees": [{"securityType": "Note", "securityTerm": t, "auctionDate": (now - timedelta(days=d)).strftime("%Y-%m-%dT00:00:00"),
                            "closingTimeCompetitive": "01:00 PM", "highYield": str(y_), "bidToCoverRatio": str(b_),
                            "indirectBidderAccepted": str(ind * 1e9), "totalAccepted": str(42e9)}
                           for t, d, y_, b_, ind in (("10-Year", 40, 5.02, 2.55, 29), ("10-Year", 70, 4.91, 2.61, 30),
                                                    ("10-Year", 12, 5.11, 2.31, 24), ("30-Year", 11, 5.40, 2.28, 26),
                                                    ("30-Year", 41, 5.22, 2.45, 28))]}
    cal = [{"date": now + timedelta(hours=h), "titre": t, "impact": imp, "prevision": pv, "precedent": pr, "reel": re_}
           for h, t, imp, pv, pr, re_ in (
               (-70, "Unemployment Claims", "High", "221K", "218K", "209K"),
               (-50, "Final GDP q/q", "Medium", "2.1%", "2.1%", "2.4%"),
               (-30, "Core Durable Goods Orders m/m", "Medium", "0.2%", "0.1%", "0.5%"),
               (-20, "Core CPI m/m", "High", "0.3%", "0.2%", "0.4%"),
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
                "Réserve fédérale (RSS)", "Trésor américain (courbes)", "Fed de New York (EFFR, SOFR)",
                "TreasuryDirect (adjudications)", "Yahoo intermarché 15 min", "XAUS (spot XAU/USD)"):
        noter(nom, True, "données de démonstration")
    return {"yahoo": y, "fred": f, "cot": cot, "gld": gld, "cal": cal, "news": news, "zq": zq,
            "intraday": intraday, "courbe": courbe, "fed_off": fed_off, "tresor": {}, "nyfed": {}, "adjudic": adjudic,
            "oanda": None, "intermarche": intermarche_demo(barres), "xaus": xaus_demo(barres, 36.8)}


def oanda_demo(spot, now):
    import random
    random.seed(11)
    largeur = 1.0
    seaux_o, seaux_p = [], []
    for i in range(-400, 401):
        p = round(spot + i * largeur, 1)
        base = max(0.02, 0.35 - abs(i) / 1600)
        lg = base * random.uniform(0.6, 1.4)
        sh = base * random.uniform(0.6, 1.4)
        if 55 <= i <= 60:
            lg *= 4.5          # poche de stops acheteurs au-dessus
        if -48 <= i <= -44:
            sh *= 4.0          # poche de stops vendeurs en dessous
        if 25 <= i <= 28:
            sh *= 3.0          # mur d'ordres vendeurs
        seaux_o.append({"price": str(p), "longCountPercent": f"{lg:.4f}", "shortCountPercent": f"{sh:.4f}"})
        lp = base * random.uniform(0.8, 1.6) * (1.6 if 10 <= i <= 40 else 1.0)
        sp = base * random.uniform(0.5, 1.0)
        seaux_p.append({"price": str(p), "longCountPercent": f"{lp:.4f}", "shortCountPercent": f"{sp:.4f}"})
    t = now.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:00Z") if ZoneInfo else ""
    return {"orderBook": {"price": str(round(spot, 2)), "bucketWidth": "1.0", "time": t, "buckets": seaux_o},
            "positionBook": {"price": str(round(spot, 2)), "bucketWidth": "1.0", "time": t, "buckets": seaux_p}}


def xaus_demo(barres, base):
    b = barres.tail(1000)
    serie = (b["close"] - base).resample("2min").ffill().dropna()
    return {"spot": float(serie.iloc[-1]), "quand": maintenant().isoformat(), "etat": "fresh", "serie": serie}


def intermarche_demo(barres):
    b = barres.tail(400)
    c = pd.DataFrame({"GC=F": b["close"]})
    c["US2Y"] = 3.6 - (b["close"] / b["close"].iloc[0] - 1) * 3
    c["DX-Y.NYB"] = 101 * (1 - (b["close"] / b["close"].iloc[0] - 1) * 0.5) + np.linspace(0, -0.25, len(b))
    c["^TNX"] = 4.4 - (b["close"] / b["close"].iloc[0] - 1) * 2
    c["SI=F"] = b["close"] / 70
    return c


def previsions_demo(data):
    """Historique fictif de prévisions pour tester l'affichage du suivi."""
    import random
    random.seed(3)
    b = data["intraday"]["barres"]
    out = []
    for t in b.index[-900:-20:16]:
        sens = random.choice([1, 1, -1, 0])
        out.append({"ts": t.isoformat(), "prix": float(b.loc[t, "close"]), "sens": sens,
                    "conv": random.choice(["faible", "moyenne", "forte"]), "score": 0.3 * sens, "sigma": 14.0})
    return out


IA_DEMO = json.dumps({
    "titre": "L'or hésite sous 4 912 : la hausse des taux réels freine, les achats physiques amortissent.",
    "lecture": "Les taux réels 10 ans ont pris 11 pb en cinq séances, ce qui renchérit la détention d'or. Le dollar recule "
               "pourtant, et les fonds achètent les replis : la pression n'est pas unilatérale. Le marché price 56 % de "
               "chances de hausse de la Fed le 28 octobre, ce qui laisse de la place à une surprise souple.",
    "direction": "neutre", "accord": "nuancé",
    "accord_explication": "La tendance courte est baissière comme le dit le terminal, mais la demande de fond limite la baisse.",
    "forces": ["Pression : taux réels +11 pb sur 5 jours", "Soutien : dollar -0,4 % sur 5 jours",
               "Soutien : fonds acheteurs sur repli (COT)"],
    "annonces": ["PCE core au-dessus de 0,3 % : pression sur l'or ; en dessous : rebond probable vers 4 928"],
    "invalidation": ["Clôture horaire au-dessus de 4 928", "Taux réels qui repassent sous 2,55 %"],
    "achat": "Un achat n'a de sens qu'après un rejet net du support S1 (4 875) ou une reprise du VWAP, avec une cible "
             "courte vers 4 912 ; pas avant la publication du PCE.",
    "vente": "Une vente est cohérente sous 4 912 tant que les taux réels montent, avec 4 875 en ligne de mire ; "
             "éviter de vendre directement sur le support.",
    "vigilance": ["Zone news autour du PCE", "Amplitude normale ±61 $ : stops à adapter"]}, ensure_ascii=False)


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
    data["hist"] = {"previsions": [], "annonces": []} if demo else charger_historique()
    if data.get("gld") is None and not demo:
        data["gld"] = gld_par_yahoo(data)
    a = analyser(data)
    if demo:
        data["hist"]["previsions"] = previsions_demo(data)
    suivre_previsions(data["hist"], a, data)
    a["suivi"] = stats_previsions(data["hist"])
    brief = construire_brief(data, a)
    ia = ({"texte": IA_DEMO, "json": lire_json_ia(IA_DEMO), "modele": "démonstration",
           "heure": maintenant().isoformat(), "raison": "démonstration"} if demo else analyste_ia(brief, a, data))
    a["ia"] = ia
    page = rendre_html(data, a, brief, ia, watch_min, site=site_dir is not None)
    if site_dir is not None:
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "index.html").write_text(page, encoding="utf-8")
        (site_dir / "brief_claude.txt").write_text(brief, encoding="utf-8")
    if not demo:
        sauver_historique(data["hist"], site_dir)
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
