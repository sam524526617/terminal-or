#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Diagnostic des données Yahoo utilisées par le terminal (à lancer sur GitHub Actions).
- GC=F : écarts aux reprises de cotation (changement de contrat ?), retard de la dernière bougie.
- Or spot sur Yahoo (plusieurs symboles possibles) : disponibilité et qualité en 15 min et en 1 h.
- Base future - spot jour par jour : un changement de contrat apparaît comme une marche dans la base."""
import sys
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf


def charge(t, period, interval):
    try:
        df = yf.download(t, period=period, interval=interval, progress=False, auto_adjust=False, threads=False)
    except Exception as e:
        print(f"  {t} {interval} {period} : ERREUR {type(e).__name__} {str(e)[:100]}")
        return None
    if df is None or not len(df):
        print(f"  {t} {interval} {period} : vide")
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
    idx = pd.to_datetime(df.index)
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df


def resume(nom, df):
    if df is None:
        return
    now = datetime.now(timezone.utc)
    plats = float((df["high"] == df["low"]).mean() * 100)
    print(f"  {nom} : {len(df)} bougies, {df.index[0]:%Y-%m-%d %H:%M} -> {df.index[-1]:%Y-%m-%d %H:%M} UTC, "
          f"dernière bougie il y a {(now - df.index[-1]).total_seconds() / 60:.0f} min, "
          f"amplitude médiane {float((df['high'] - df['low']).median()):.2f}, bougies plates {plats:.1f} %")


def ecarts(df, nom, n=14):
    """Écarts entre la clôture d'une bougie et l'ouverture de la suivante quand la cotation a été interrompue."""
    dt = df.index.to_series().diff().dt.total_seconds() / 60
    pas = float(dt.median())
    g = pd.DataFrame({"dt": dt, "ecart": df["open"] - df["close"].shift(1), "prix": df["close"].shift(1)})
    g = g[g["dt"] >= max(45, 2.5 * pas)].dropna()
    g["pct"] = g["ecart"] / g["prix"] * 100
    med = float(g["ecart"].abs().median())
    print(f"  {nom} : {len(g)} reprises de cotation, écart absolu médian {med:.2f} $, "
          f"90e centile {float(g['ecart'].abs().quantile(.9)):.2f} $")
    gros = g.reindex(g["ecart"].abs().sort_values(ascending=False).index).head(n).sort_index()
    for t, r in gros.iterrows():
        apres = df[df.index >= t].head(8)
        comble = bool(((apres["low"] <= r["prix"]) & (apres["high"] >= r["prix"])).any())
        print(f"    {t:%Y-%m-%d %a %H:%M} UTC  écart {r['ecart']:+8.2f} $ ({r['pct']:+.2f} %)  pause {r['dt']:.0f} min  "
              f"{'comblé en 8 bougies' if comble else 'NON comblé'}")


print("=== GC=F (future COMEX)")
gc15, gc1h, gcd = charge("GC=F", "60d", "15m"), charge("GC=F", "730d", "1h"), charge("GC=F", "2y", "1d")
resume("15 min / 60 j", gc15)
resume("1 h / 730 j", gc1h)
if gc15 is not None:
    ecarts(gc15, "15 min")
if gc1h is not None:
    ecarts(gc1h, "1 h", 30)

print("\n=== Or spot sur Yahoo")
spots = {}
for t in ("XAUUSD=X", "XAU=X", "XAUUSD", "GC=F"):
    for period, interval in (("60d", "15m"), ("730d", "1h")):
        if t == "GC=F":
            continue
        df = charge(t, period, interval)
        resume(f"{t} {interval}", df)
        if df is not None:
            spots[(t, interval)] = df

print("\n=== Base future - spot (médiane par jour), si un spot est disponible")
for (t, interval), sp in spots.items():
    fut = gc15 if interval == "15m" else gc1h
    if fut is None:
        continue
    j = pd.concat({"f": fut["close"], "s": sp["close"]}, axis=1, join="inner").dropna()
    if len(j) < 50:
        print(f"  {t} {interval} : trop peu de bougies communes ({len(j)})")
        continue
    base = (j["f"] - j["s"])
    parjour = base.groupby(base.index.date).median()
    saut = parjour.diff()
    print(f"  {t} {interval} : {len(j)} bougies communes, base médiane {float(base.median()):+.2f} $, "
          f"écart-type intrajournalier médian {float(base.groupby(base.index.date).std().median()):.2f} $")
    marches = saut[saut.abs() > 4].dropna()
    print(f"    marches de plus de 4 $ d'un jour à l'autre ({len(marches)}) : "
          + ", ".join(f"{d} {v:+.1f}" for d, v in marches.items()))
    if interval == "15m":
        print("    base par jour (60 j) : " + " ".join(f"{d:%m-%d}:{v:+.0f}" for d, v in parjour.items()))
    else:
        mens = parjour.groupby([pd.DatetimeIndex(parjour.index).year, pd.DatetimeIndex(parjour.index).month]).median()
        print("    base médiane par mois : " + " ".join(f"{a}-{m:02d}:{v:+.0f}" for (a, m), v in mens.items()))
sys.exit(0)
