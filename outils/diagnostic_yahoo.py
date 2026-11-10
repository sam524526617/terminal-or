#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Contrôle, sur les vraies données Yahoo, de la correction du changement de contrat (contrat.py).
À lancer sur GitHub Actions : Yahoo n'est pas joignable depuis tous les postes."""
import os
import sys
from datetime import date

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import contrat as K


def charge(t, period, interval):
    try:
        df = yf.download(t, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
    except Exception as e:
        print(f"  {t} {interval} {period} : ERREUR {type(e).__name__} {str(e)[:100]}")
        return pd.DataFrame()
    if df is None or not len(df):
        print(f"  {t} {interval} {period} : vide")
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
    idx = pd.to_datetime(df.index)
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df


def gros_ecarts(df, nom, n=8):
    """Plus gros écarts entre la clôture d'une bougie et l'ouverture de la suivante (toutes pauses confondues)."""
    e = (df["open"] - df["close"].shift(1)).dropna()
    gros = e.reindex(e.abs().sort_values(ascending=False).index).head(n).sort_index()
    print(f"  {nom} : " + " | ".join(f"{t:%Y-%m-%d %H:%M} {v:+.1f}" for t, v in gros.items()))


print("=== Données")
g15, g1h, gj = charge("GC=F", "60d", "15m"), charge("GC=F", "730d", "1h"), charge("GC=F", "1y", "1d")
print(f"  GC=F : {len(g15)} bougies 15 min, {len(g1h)} bougies 1 h, {len(gj)} jours")

print("=== Correction")
a15, a1h, aj, info = K.corriger(charge, g15, g1h, gj, date.today())
for k, v in info.items():
    if isinstance(v, list):
        v = [(f"{t:%Y-%m-%d %H:%M}", round(s, 2)) for t, s in v]
    print(f"  {k} : {v}")

print("=== Plus gros écarts clôture -> ouverture, avant et après correction")
gros_ecarts(g1h, "1 h brut   ")
gros_ecarts(a1h, "1 h corrigé")
gros_ecarts(g15, "15 min brut   ")
gros_ecarts(a15, "15 min corrigé")
if len(gj) and len(aj):
    print(f"  quotidien : première clôture {float(gj['close'].iloc[0]):.1f} -> {float(aj['close'].iloc[0]):.1f} ; "
          f"dernière {float(gj['close'].iloc[-1]):.1f} -> {float(aj['close'].iloc[-1]):.1f}")

if info.get("actif"):
    x1h = charge(info["actif"], "730d", "1h")
    d, pg = K.ecart(g1h, x1h)
    print(f"=== Écart {info['actif']} - GC=F en 1 h : {len(d)} bougies communes")
    print("  sans garde-fou de calendrier :", [(f"{t:%Y-%m-%d %H:%M}", round(s, 1)) for t, s in K.reperer(g1h, x1h, n=12, garde=False)])
    jour = d.groupby(d.index.normalize()).agg(["median", "count", "min", "max"])
    print("  médiane par jour autour des fins de mois (jour : médiane [min ; max] nombre de bougies)")
    for t, r in jour.iterrows():
        if K.fenetre_ok(t) and t >= pd.Timestamp("2025-06-01"):
            print(f"    {t:%Y-%m-%d %a} : {r['median']:+8.1f}  [{r['min']:+8.1f} ; {r['max']:+8.1f}]  {int(r['count'])}")
    for t, s in info.get("sauts") or []:
        t = pd.Timestamp(t)
        autour = d[(d.index >= t - pd.Timedelta(hours=14)) & (d.index <= t + pd.Timedelta(hours=14))]
        print(f"  autour du changement du {t:%Y-%m-%d %H:%M} (saut {s:+.1f}) :")
        print("    " + " ".join(f"{u:%d %Hh}:{v:+.1f}" for u, v in autour.items()))
    mois = d.groupby(d.index.to_period("M")).agg(["median", "count"])
    print("  médiane par mois : " + " | ".join(f"{p} {r['median']:+.0f} ({int(r['count'])})" for p, r in mois.iterrows()))
