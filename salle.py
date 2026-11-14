# -*- coding: utf-8 -*-
"""
SALLE DES MARCHÉS : les modules d'un terminal institutionnel, appliqués à l'or
=============================================================================

  - Squawk : le fil chronologique qui dit pourquoi le prix bouge (titres de presse, chiffres publiés, mouvements brusques) ;
  - Sentiment de risque : un score de 0 à 100 calculé sur 7 marchés, avec son historique sur un an ;
  - Force des actifs : performance comparée de l'or et des marchés qui le font bouger ;
  - Probabilités de taux : la Fed, réunion par réunion ;
  - Horaires des marchés : Sydney, Tokyo, Londres, New York, avec le compte à rebours ;
  - Saisonnalité : chaque mois de l'année sur 20 ans ;
  - Heatmap technique : tendance, momentum et volatilité de chaque marché sur une seule grille.
"""

import html
import json
import math
import re
from datetime import timedelta

import numpy as np
import pandas as pd

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
MOIS_C = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=1, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def propre(s):
    if s is None:
        return None
    s = pd.to_numeric(s, errors="coerce").dropna()
    if not len(s):
        return None
    idx = pd.DatetimeIndex(s.index)
    s.index = (idx.tz_localize(None) if idx.tz is not None else idx).normalize()
    return s[~s.index.duplicated(keep="last")].sort_index()


def panneau(titre, corps, aide="", droite="", cl=""):
    return (f'<section class="sl-p {cl}"><div class="sl-h"><span class="sl-t">{esc(titre)}</span>'
            + (f'<span class="sl-i" title="{esc(aide)}">?</span>' if aide else "") + f'<span class="sl-d">{droite}</span></div>{corps}</section>')


# ---------------------------------------------------------------------------
# SENTIMENT DE RISQUE
# ---------------------------------------------------------------------------

COMPOSANTES = [("spx", "pct", 1, "Actions (S&P 500)"), ("vix", "pts", -1, "Peur (VIX)"), ("hy", "pts", -1, "Crédit risqué"),
               ("usdjpy", "pct", 1, "Yen (USD/JPY)"), ("cuivre_or", "pct", 1, "Cuivre / or"), ("move", "pts", -1, "Volatilité des taux (MOVE)"),
               ("brent", "pct", 1, "Pétrole")]


def sentiment(y, f):
    """Score 0 (peur) – 100 (appétit pour le risque), chaque jour sur un an : moyenne des variations sur 10 jours de 7 marchés,
    chacune rapportée à sa variation habituelle."""
    series = {}
    for cle, mode, signe, lib in COMPOSANTES:
        if cle == "cuivre_or":
            c, o = propre(y.get("cuivre")), propre(y.get("or"))
            s = (c / o).dropna() if c is not None and o is not None else None
        else:
            s = propre(y.get(cle) if cle in y else f.get(cle))
        if s is None or len(s) < 120:
            continue
        ch = (s / s.shift(10) - 1) * 100 if mode == "pct" else s - s.shift(10)
        z = (ch / ch.rolling(250, min_periods=120).std()).clip(-3, 3) * signe
        series[lib] = z
    if len(series) < 3:
        return None
    Z = pd.concat(series, axis=1).sort_index().ffill(limit=3)
    sc = (50 + 50 * np.tanh(Z.mean(axis=1, skipna=True) / 1.2)).dropna()
    sc = sc[sc.index >= sc.index[-1] - pd.Timedelta(days=365)]
    if len(sc) < 30:
        return None
    v = float(sc.iloc[-1])
    prec = lambda j: float(sc[sc.index <= sc.index[-1] - pd.Timedelta(days=j)].iloc[-1]) if (sc.index <= sc.index[-1] - pd.Timedelta(days=j)).any() else None
    etat = "RISK-OFF" if v < 40 else ("RISK-ON" if v > 60 else "NEUTRE")
    der = {lib: float(z.dropna().iloc[-1]) for lib, z in series.items() if len(z.dropna())}
    lecture = {"RISK-OFF": "Les marchés ont peur : la demande refuge soutient l'or, sauf si la panique force des ventes pour trouver du cash.",
               "RISK-ON": "Les marchés prennent du risque : peu de demande refuge, l'or dépend surtout des taux et du dollar.",
               "NEUTRE": "Pas de mouvement de fond sur l'appétit pour le risque."}[etat]
    return {"score": v, "etat": etat, "s1": prec(7), "s4": prec(30), "hist": sc, "comp": der, "lecture": lecture}


def svg_jauge(v, w=220, h=150):
    cx, cy, r = w / 2, 118, 92
    pt = lambda a, rr: (cx + rr * math.cos(math.pi * (1 - a)), cy - rr * math.sin(math.pi * (1 - a)))
    out = [f'<svg viewBox="0 0 {w} {h}" class="sl-jauge" role="img" aria-label="Sentiment de risque {v:.0f} sur 100">']
    for k in range(40):
        a0, a1 = k / 40 + 0.004, (k + 1) / 40 - 0.004
        col = "#ff5a5f" if k < 16 else ("#e8c35a" if k < 24 else "#35c9d6")
        op = 1.0 if (k + 0.5) / 40 <= v / 100 else 0.18
        x0, y0 = pt(a0, r)
        x1, y1 = pt(a1, r)
        x2, y2 = pt(a1, r - 16)
        x3, y3 = pt(a0, r - 16)
        out.append(f'<path d="M{x0:.1f},{y0:.1f} L{x1:.1f},{y1:.1f} L{x2:.1f},{y2:.1f} L{x3:.1f},{y3:.1f} Z" fill="{col}" opacity="{op}"/>')
    xa, ya = pt(v / 100, r - 26)
    col = "#ff5a5f" if v < 40 else ("#e8c35a" if v <= 60 else "#35c9d6")
    out.append(f'<line x1="{cx}" y1="{cy}" x2="{xa:.1f}" y2="{ya:.1f}" stroke="{col}" stroke-width="3" stroke-linecap="round"/>'
               f'<circle cx="{cx}" cy="{cy}" r="6" fill="{col}"/>'
               f'<text x="{cx}" y="{cy + 26}" text-anchor="middle" class="sl-jv" fill="{col}">{v:.0f}</text></svg>')
    return "".join(out)


def svg_histo(sc, w=520, h=170):
    v = sc.to_numpy()
    g, d, ht, b = 26, 6, 8, 18
    x = lambda i: g + i / max(1, len(v) - 1) * (w - g - d)
    y = lambda val: ht + (100 - val) / 100 * (h - ht - b)
    out = [f'<svg viewBox="0 0 {w} {h}" class="sl-svg" preserveAspectRatio="none" role="img" aria-label="Historique du sentiment sur un an">',
           f'<rect x="{g}" y="{y(100):.1f}" width="{w - g - d}" height="{y(60) - y(100):.1f}" fill="rgba(53,201,214,.05)"/>',
           f'<rect x="{g}" y="{y(40):.1f}" width="{w - g - d}" height="{y(0) - y(40):.1f}" fill="rgba(255,90,95,.05)"/>']
    for lv in (20, 40, 60, 80):
        out.append(f'<line x1="{g}" x2="{w - d}" y1="{y(lv):.1f}" y2="{y(lv):.1f}" class="gr"/><text x="{g - 4}" y="{y(lv) + 3:.1f}" class="ax" text-anchor="end">{lv}</text>')
    for k in range(1, len(v)):
        col = "#ff5a5f" if v[k] < 40 else ("#e8c35a" if v[k] <= 60 else "#35c9d6")
        out.append(f'<line x1="{x(k - 1):.1f}" y1="{y(v[k - 1]):.1f}" x2="{x(k):.1f}" y2="{y(v[k]):.1f}" stroke="{col}" stroke-width="1.6"/>')
    mois = [i for i in range(1, len(sc)) if sc.index[i].month != sc.index[i - 1].month]
    for i in mois:
        out.append(f'<text x="{x(i):.1f}" y="{h - 4}" class="ax" text-anchor="middle">{MOIS_C[sc.index[i].month - 1]}</text>')
    out.append("</svg>")
    return "".join(out)


def bloc_sentiment(S):
    if not S:
        return panneau("Sentiment de risque", '<p class="sl-vide">Données insuffisantes.</p>')
    cl = {"RISK-OFF": "down", "RISK-ON": "up", "NEUTRE": "neutre"}[S["etat"]]
    ev = lambda p, lib: (f'<div><span>{lib}</span><b class="{"up" if S["score"] > p else "down"}">{"▲" if S["score"] > p else "▼"} '
                         f'{abs(S["score"] - p):.0f}</b><i>(était {p:.0f})</i></div>' if p is not None else "")
    comp = "".join(f'<div class="sl-cp"><span>{esc(k)}</span><i class="{"up" if z > 0 else "down"}" style="width:{min(100, abs(z) / 3 * 100):.0f}%"></i></div>'
                   for k, z in sorted(S["comp"].items(), key=lambda x: -abs(x[1])))
    corps = (f'<div class="sl-sent"><div class="sl-sg">{svg_jauge(S["score"])}<b class="sl-et {cl}">{S["etat"]}</b>'
             f'<div class="sl-ev">{ev(S["s1"], "1 sem.")}{ev(S["s4"], "1 mois")}</div></div>'
             f'<div class="sl-sh"><div class="sl-leg"><span class="down">■ risk-off &lt; 40</span><span class="neutre">■ neutre 40–60</span>'
             f'<span class="up">■ risk-on &gt; 60</span></div>{svg_histo(S["hist"])}</div></div>'
             f'<p class="sl-lect">{esc(S["lecture"])}</p><div class="sl-cps">{comp}</div>')
    return panneau("Sentiment de risque", corps, "Score de 0 à 100 : moyenne des variations sur 10 jours de 7 marchés (actions, VIX, crédit, "
                   "yen, cuivre/or, MOVE, pétrole), chacune rapportée à sa variation habituelle. Sous 40 : peur. Au-dessus de 60 : appétit pour le risque.",
                   "historique 1 an")


# ---------------------------------------------------------------------------
# FORCE DES ACTIFS
# ---------------------------------------------------------------------------

ACTIFS = [("or", "OR", "#e8c35a", 1), ("argent", "ARGENT", "#c7ccd6", 1), ("dxy", "DOLLAR", "#35c9d6", 1), ("spx", "S&P 500", "#7c8cff", 1),
          ("wti", "PÉTROLE", "#b07cff", 1), ("cuivre", "CUIVRE", "#ff8f6b", 1), ("usdjpy", "YEN", "#5fd18a", -1), ("eurusd", "EURO", "#ff6fae", 1)]
FENETRES = [("5j", 5), ("1m", 21), ("3m", 63)]


def force(y):
    out = {}
    for cle, n in FENETRES:
        lignes = []
        for k, lib, col, signe in ACTIFS:
            s = propre(y.get(k))
            if s is None or len(s) <= n:
                continue
            s = s.iloc[-(n + 1):]
            perf = ((s / s.iloc[0]) ** signe - 1) * 100
            lignes.append({"lib": lib, "col": col, "perf": perf})
        if lignes:
            out[cle] = lignes
    return out or None


def svg_force(L, w=640, h=250):
    g, d, ht, b = 8, 92, 10, 18
    vals = [float(v) for l in L for v in l["perf"].to_numpy()] + [0.0]
    mn, mx = min(vals), max(vals)
    et = (mx - mn) or 1.0
    n = max(len(l["perf"]) for l in L)
    x = lambda i: g + i / max(1, n - 1) * (w - g - d)
    y = lambda v: ht + (mx - v) / et * (h - ht - b)
    out = [f'<svg viewBox="0 0 {w} {h}" class="sl-svg" role="img" aria-label="Force des actifs">',
           f'<line x1="{g}" x2="{w - d}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="z"/>']
    fins = []
    for l in sorted(L, key=lambda l: l["lib"] != "OR"):
        p = l["perf"].to_numpy()
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(p))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{l["col"]}" stroke-width="{2.6 if l["lib"] == "OR" else 1.5}" stroke-linejoin="round"/>')
        fins.append((float(p[-1]), l))
    fins.sort(key=lambda t: -t[0])
    ys, prec = [], -99
    for v, l in fins:
        yy = max(y(v), prec + 15)
        ys.append((yy, v, l))
        prec = yy
    for yy, v, l in ys:
        out.append(f'<rect x="{w - d + 6}" y="{yy - 7:.1f}" width="{d - 8}" height="14" rx="2" fill="{l["col"]}"/>'
                   f'<text x="{w - d + 10}" y="{yy + 3.5:.1f}" class="tg">{esc(l["lib"])} {fr(v, 2, True)} %</text>')
    out.append("</svg>")
    return "".join(out)


def bloc_force(F):
    if not F:
        return panneau("Force des actifs", '<p class="sl-vide">Données insuffisantes.</p>')
    boutons = "".join(f'<button type="button" data-fen="{k}" class="{"on" if k == "1m" else ""}">{k.upper()}</button>' for k, _ in FENETRES if k in F)
    vues = "".join(f'<div class="sl-fv" data-fen="{k}"{"" if k == "1m" else " hidden"}>{svg_force(L)}</div>' for k, L in F.items())
    return panneau("Force des actifs · performance comparée", f'<p class="sl-sous">Variation en % depuis le début de la période. Yen : '
                   f'courbe qui monte = yen qui se renforce.</p>{vues}', "", f'<span class="sl-btns">{boutons}</span>')


# ---------------------------------------------------------------------------
# SQUAWK
# ---------------------------------------------------------------------------

def squawk(data, intra, ajust, now):
    """Fil chronologique : titres, chiffres publiés et mouvements brusques de l'or, du plus récent au plus ancien."""
    ev = []
    vus = set()
    titres = list(data.get("news_7j") or []) + [it for l in (data.get("news") or {}).values() for it in l]
    for it in titres:
        d = it.get("date")
        if d is None or (now - d).days > 3:
            continue
        cle = re.sub(r"\W+", " ", it["titre"].lower())[:50]
        if cle in vus:
            continue
        vus.add(cle)
        ev.append({"t": d, "type": "actu", "txt": it["titre"], "src": it.get("source") or "", "lien": it.get("lien") or ""})
    for e in data.get("cal") or []:
        d = e.get("date")
        if d is None or d > now or (now - d).days > 3 or e.get("impact") not in ("High", "Medium"):
            continue
        r = e.get("reel")
        txt = f"{e['titre']} : " + (f"{r} publié (prévu {e.get('prevision') or 'n.d.'}, précédent {e.get('precedent') or 'n.d.'})" if r else "publication attendue")
        ev.append({"t": d, "type": "chiffre", "txt": txt, "fort": e.get("impact") == "High"})
    b = (intra or {}).get("barres")
    if b is not None and len(b) > 200:
        r = b[b.index >= b.index[-1] - pd.Timedelta(days=3)]
        corps = (b["close"] - b["open"]).abs()
        med = float(corps.tail(96 * 20).median()) or 1.0
        for t, row in r.iterrows():
            v = float(row["close"] - row["open"])
            if abs(v) >= 3.5 * med:
                ev.append({"t": t.to_pydatetime(), "type": "mouvement", "txt": f"Or {fr(v, 1, True)} $ en 15 min · maintenant {fr(float(row['close']) + ajust, 2)}",
                           "sens": 1 if v > 0 else -1})
    ev.sort(key=lambda x: x["t"], reverse=True)
    return ev[:160]


def bloc_squawk(E, now):
    n = {k: sum(1 for e in E if e["type"] == k) for k in ("actu", "chiffre", "mouvement")}
    filtres = (f'<div class="sq-f" role="group" aria-label="Filtrer le fil"><button type="button" data-sq="tout" class="on">TOUT {len(E)}</button>'
               f'<button type="button" data-sq="actu">ACTUS {n["actu"]}</button><button type="button" data-sq="chiffre">CHIFFRES {n["chiffre"]}</button>'
               f'<button type="button" data-sq="mouvement">MOUVEMENTS {n["mouvement"]}</button></div>')
    lig = ""
    for e in E:
        m = int((now - e["t"]).total_seconds() // 60)
        age = "maint." if m < 2 else (f"{m} min" if m < 60 else (f"{m // 60} h" if m < 1440 else f"{m // 1440} j"))
        cl = e["type"] + (" fort" if e.get("fort") else "") + (" up" if e.get("sens") == 1 else " down" if e.get("sens") == -1 else "")
        ic = {"actu": "●", "chiffre": "◆", "mouvement": "▲" if e.get("sens") == 1 else "▼"}[e["type"]]
        txt = (f'<a href="{esc(e["lien"])}" target="_blank" rel="noopener">{esc(e["txt"])}</a>' if e.get("lien") and e["lien"] != "#" else esc(e["txt"]))
        lig += (f'<li class="sq-{cl}" data-sq="{e["type"]}"><time>{e["t"]:%H:%M}</time><small>{age}</small><i>{ic}</i>'
                f'<span>{txt}{(" <em>" + esc(e["src"]) + "</em>") if e.get("src") else ""}</span></li>')
    corps = f'{filtres}<ul class="sq-l">{lig or "<li class=sl-vide>Rien sur les trois derniers jours.</li>"}</ul>'
    return panneau("Squawk", corps, "Le fil qui dit pourquoi le prix bouge : titres de presse, chiffres économiques publiés et mouvements brusques "
                   "de l'or (bougie de 15 min plus de 3,5 fois l'habitude), sur trois jours.", '<b class="sl-live">LIVE</b>', "sl-squawk")


# ---------------------------------------------------------------------------
# PROBABILITÉS DE TAUX
# ---------------------------------------------------------------------------

def bloc_taux(fw):
    if not fw or not fw.get("reunions"):
        return panneau("Probabilités de taux · Fed", '<p class="sl-vide">Futures fed funds indisponibles.</p>')
    r0 = fw["reunions"][0]
    barres = ""
    for lib, p, cl in (("Baisse", r0["p_baisse"], "down"), ("Statu quo", r0["p_statu"], "neutre"), ("Hausse", r0["p_hausse"], "up")):
        barres += (f'<div class="tx-b"><b>{p * 100:.0f} %</b><i class="{cl}" style="height:{max(2, p * 100):.0f}%"></i><span>{lib}</span></div>')
    lig = "".join(f'<tr><td>{r["date"]:%d/%m/%y}</td><td class="num">{fr(r["taux"], 2)} %</td><td class="num down">{r["p_baisse"] * 100:.0f} %</td>'
                  f'<td class="num">{r["p_statu"] * 100:.0f} %</td><td class="num up">{r["p_hausse"] * 100:.0f} %</td>'
                  f'<td class="num">{fr(r["cumul_pb"], 0, True)}</td></tr>' for r in fw["reunions"][:6])
    lecture = ("Une hausse des taux pèse sur l'or (il ne rapporte rien) ; une baisse le soutient. Ce qui compte, c'est la surprise par rapport "
               "à ces probabilités.")
    corps = (f'<div class="tx"><div class="tx-g"><span class="sl-sous">Prochaine réunion · {r0["date"]:%d/%m}</span><div class="tx-bs">{barres}</div></div>'
             f'<div class="tx-t"><table class="sl-tab"><thead><tr><th>Réunion</th><th>Taux</th><th>Baisse</th><th>Statu</th><th>Hausse</th>'
             f'<th>Cumul pb</th></tr></thead><tbody>{lig}</tbody></table></div></div><p class="sl-lect">{lecture}</p>')
    return panneau("Probabilités de taux · Fed", corps, "Calculées à partir des futures fed funds, comme l'outil FedWatch du CME.",
                   f'taux effectif {fr(fw["effr"], 2)} %')


# ---------------------------------------------------------------------------
# HORAIRES DES MARCHÉS (rempli par le navigateur)
# ---------------------------------------------------------------------------

def bloc_horaires():
    corps = ('<div class="mh" id="mh"><div class="mh-l" id="mh-l"></div><div class="mh-axe"><span>0 h</span><span>6 h</span><span>12 h</span>'
             '<span>18 h</span><span>24 h</span></div><p class="sl-sous" id="mh-info">heure de Paris</p><p class="sl-sous">Durée : temps avant la fermeture (marché ouvert) ou avant l\'ouverture.</p></div>')
    return panneau("Horaires des marchés", corps, "Sessions de Sydney, Tokyo, Londres et New York converties à l'heure de Paris. Le chevauchement "
                   "Londres – New York est le moment le plus liquide pour l'or.", '<span id="mh-h">--:--</span>')


# ---------------------------------------------------------------------------
# SAISONNALITÉ
# ---------------------------------------------------------------------------

def saison(mensuel, now):
    s = propre(mensuel)
    if s is None or len(s) < 60:
        return None
    m = s.resample("ME").last().dropna()
    r = (m / m.shift(1) - 1).dropna() * 100
    r = r[r.index >= r.index[-1] - pd.DateOffset(years=20)]
    r = r[~((r.index.year == now.year) & (r.index.month == now.month))]
    lignes = []
    for k in range(1, 13):
        x = r[r.index.month == k]
        if len(x) < 5:
            continue
        lignes.append({"mois": k, "moy": float(x.mean()), "med": float(x.median()), "hausse": float((x > 0).mean()), "n": int(len(x)),
                       "pire": float(x.min()), "meilleur": float(x.max())})
    if not lignes:
        return None
    return {"lignes": lignes, "annees": int(len(r) / 12), "mois": now.month, "suivant": now.month % 12 + 1}


def bloc_saison(S, compact=False):
    if not S:
        return panneau("Saisonnalité de l'or", '<p class="sl-vide">Historique mensuel indisponible.</p>')
    mx = max(abs(l["moy"]) for l in S["lignes"]) or 1
    cel = ""
    for l in S["lignes"]:
        cl = "up" if l["moy"] > 0 else "down"
        actuel = " sa-now" if l["mois"] == S["mois"] else (" sa-next" if l["mois"] == S["suivant"] else "")
        cel += (f'<div class="sa-c{actuel}" title="{MOIS[l["mois"] - 1]} : moyenne {fr(l["moy"], 2, True)} %, médiane {fr(l["med"], 2, True)} %, '
                f'en hausse {l["hausse"] * 100:.0f} % des années ({l["n"]} ans), pire {fr(l["pire"], 1, True)} %, meilleur {fr(l["meilleur"], 1, True)} %">'
                f'<span>{MOIS_C[l["mois"] - 1]}</span><div class="sa-bar"><i class="{cl}" style="height:{abs(l["moy"]) / mx * 50:.0f}%;'
                f'{"bottom:50%" if l["moy"] > 0 else "top:50%"}"></i></div><b class="{cl}">{fr(l["moy"], 1, True)}</b>'
                f'<small>{l["hausse"] * 100:.0f} %</small></div>')
    act = next((l for l in S["lignes"] if l["mois"] == S["mois"]), None)
    suiv = next((l for l in S["lignes"] if l["mois"] == S["suivant"]), None)
    phr = lambda l: (f'{MOIS[l["mois"] - 1].capitalize()} : en moyenne {fr(l["moy"], 2, True)} %, en hausse {l["hausse"] * 100:.0f} % des années.' if l else "")
    corps = (f'<div class="sa">{cel}</div><p class="sl-lect"><b>Ce mois-ci.</b> {phr(act)} <b>Le mois prochain.</b> {phr(suiv)} '
             f'Une tendance saisonnière n\'est qu\'un contexte : elle ne remplace pas les filtres du plan.</p>')
    return panneau(f"Saisonnalité de l'or · {S['annees']} ans", corps, "Variation moyenne de l'or chaque mois, et part des années où ce mois a "
                   "fini en hausse (petit chiffre). Encadré : le mois en cours ; pointillé : le mois prochain.")


# ---------------------------------------------------------------------------
# HEATMAP TECHNIQUE
# ---------------------------------------------------------------------------

HM_MARCHES = [("or", "Or"), ("argent", "Argent"), ("cuivre", "Cuivre"), ("dxy", "Dollar DXY"), ("eurusd", "EUR/USD"), ("usdjpy", "USD/JPY"),
              ("us10", "Taux 10 ans"), ("spx", "S&P 500"), ("wti", "Pétrole WTI"), ("vix", "VIX")]


def heatmap(y):
    out = []
    for k, lib in HM_MARCHES:
        s = propre(y.get(k))
        if s is None or len(s) < 220:
            continue
        c = float(s.iloc[-1])
        m20, m50, m200 = (float(s.rolling(n).mean().iloc[-1]) for n in (20, 50, 200))
        d = s.diff()
        h_, b_ = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean(), (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        rsi = float(100 - 100 / (1 + h_.iloc[-1] / b_.iloc[-1])) if b_.iloc[-1] > 0 else 100.0
        rend = s.pct_change()
        vol = float(rend.tail(20).std() / rend.tail(250).std()) if rend.tail(250).std() > 0 else 1.0
        h52, l52 = float(s.tail(252).max()), float(s.tail(252).min())
        pos = (c - l52) / (h52 - l52) * 100 if h52 > l52 else 50
        m5 = float((s.iloc[-1] / s.iloc[-6] - 1) * 100)
        out.append({"lib": lib, "cle": k, "cells": [
            ("Court terme", 1 if c > m20 else -1, "au-dessus" if c > m20 else "sous", "moyenne 20 jours"),
            ("Moyen terme", 1 if c > m50 else -1, "au-dessus" if c > m50 else "sous", "moyenne 50 jours"),
            ("Long terme", 1 if c > m200 else -1, "au-dessus" if c > m200 else "sous", "moyenne 200 jours"),
            ("Momentum", 1 if rsi > 55 else (-1 if rsi < 45 else 0), f"RSI {rsi:.0f}", "70 suracheté, 30 survendu"),
            ("5 jours", 1 if m5 > 0.3 else (-1 if m5 < -0.3 else 0), fr(m5, 1, True, " %"), "variation"),
            ("Volatilité", 2 if vol > 1.3 else (-2 if vol < 0.75 else 0), f"{vol:.1f}×", "20 jours / habitude"),
            ("Range 1 an", 1 if pos > 66 else (-1 if pos < 33 else 0), f"{pos:.0f} %", "position entre plus bas et plus haut 52 sem."),
        ]})
    return out or None


def bloc_heatmap(H):
    if not H:
        return panneau("Heatmap technique", '<p class="sl-vide">Données insuffisantes.</p>')
    tete = "".join(f'<th>{esc(c[0])}</th>' for c in H[0]["cells"])
    lig = ""
    for l in H:
        cel = ""
        for nom, v, txt, aide in l["cells"]:
            cl = {1: "hm-u", -1: "hm-d", 0: "hm-n", 2: "hm-hv", -2: "hm-lv"}[v]
            cel += f'<td class="{cl}" title="{esc(nom)} : {esc(txt)} ({esc(aide)})">{esc(txt)}</td>'
        lig += f'<tr class="{"hm-or" if l["cle"] == "or" else ""}"><th scope="row">{esc(l["lib"])}</th>{cel}</tr>'
    corps = (f'<div class="tbl"><table class="hm"><thead><tr><th></th>{tete}</tr></thead><tbody>{lig}</tbody></table></div>'
             f'<p class="sl-sous">Bleu : haussier · rouge : baissier · gris : neutre. Volatilité : clair = plus agitée que d\'habitude. '
             f'Données quotidiennes. Pour l\'or, le dollar et les taux en rouge sont plutôt favorables.</p>')
    return panneau("Heatmap technique", corps, "Tendance (prix par rapport aux moyennes 20, 50 et 200 jours), momentum (RSI 14), variation sur 5 jours, "
                   "volatilité récente et position dans le range d'un an, pour chaque marché.")


# ---------------------------------------------------------------------------
# ASSEMBLAGE
# ---------------------------------------------------------------------------

def bloc_salle(data, a, ajust, now):
    y, f = data["yahoo"], data["fred"]
    E = squawk(data, data.get("intraday"), ajust, now)
    return (f'<div class="sl" data-zone="salle"><div class="sl-col">{bloc_squawk(E, now)}</div>'
            f'<div class="sl-col">{bloc_sentiment(sentiment(y, f))}{bloc_force(force(y))}</div>'
            f'<div class="sl-col">{bloc_taux(a.get("fedwatch"))}{bloc_horaires()}</div></div>')


CSS = """
/* Salle des marchés */
.sl{display:grid;grid-template-columns:minmax(0,.95fr) minmax(0,1.5fr) minmax(0,1fr);gap:10px;margin:0 0 14px;align-items:stretch}
.sl-col{display:flex;flex-direction:column;gap:10px;min-width:0}
.sl-p{position:relative;background:var(--panel);border:1px solid var(--line);border-radius:2px;padding:10px 12px;min-width:0}
.sl-p::before,.sl-p::after{content:"";position:absolute;width:9px;height:9px;border-color:var(--brass);border-style:solid;opacity:.7}
.sl-p::before{top:-1px;left:-1px;border-width:1px 0 0 1px} .sl-p::after{bottom:-1px;right:-1px;border-width:0 1px 1px 0}
.sl-h{display:flex;align-items:center;gap:8px;margin-bottom:8px;padding-bottom:7px;border-bottom:1px solid var(--band2)}
.sl-t{font:700 11.5px var(--titre);letter-spacing:.16em;text-transform:uppercase;color:var(--brass)}
.sl-t::before{content:"◆ ";font-size:8px;vertical-align:2px}
.sl-i{font:600 10px var(--mono);color:var(--mute);border:1px solid var(--line);border-radius:50%;width:14px;height:14px;display:inline-flex;align-items:center;justify-content:center;cursor:help}
.sl-d{margin-left:auto;font:500 11px var(--mono);color:var(--mute)}
.sl-live{font:700 10px var(--mono);letter-spacing:.08em;color:#000;background:var(--brass);padding:2px 6px;border-radius:2px}
.sl-vide{color:var(--mute);font-size:13px} .sl-sous{margin:0 0 6px;font-size:11.5px;color:var(--mute)}
.sl-lect{margin:8px 0 0;font-size:12.5px;line-height:1.5;color:var(--ink)}
.sl-svg{display:block;width:100%;height:auto} .sl-svg .gr{stroke:#1f1c14} .sl-svg .z{stroke:#3a3424;stroke-dasharray:3 3}
.sl-svg .ax{fill:var(--mute);font:500 9.5px var(--mono)} .sl-svg .tg{fill:#050402;font:700 9.5px var(--mono)}
/* sentiment */
.sl-sent{display:grid;grid-template-columns:200px minmax(0,1fr);gap:12px;align-items:center}
.sl-sg{display:flex;flex-direction:column;align-items:center} .sl-jauge{width:200px;height:auto} .sl-jv{font:700 30px var(--mono)}
.sl-et{font:800 13px var(--titre);letter-spacing:.14em} .sl-et.up{color:var(--up)} .sl-et.down{color:var(--down)} .sl-et.neutre{color:var(--brass)}
.sl-ev{display:flex;gap:14px;margin-top:6px;font:500 11px var(--mono);color:var(--mute)} .sl-ev div{display:flex;flex-wrap:wrap;gap:0 4px;align-items:baseline;max-width:90px}
.sl-ev i{font-style:normal;font-size:10px}
.sl-leg{display:flex;gap:12px;font:600 9.5px var(--mono);letter-spacing:.06em;text-transform:uppercase;margin-bottom:4px}
.sl-leg .down{color:var(--down)} .sl-leg .neutre{color:var(--brass)} .sl-leg .up{color:var(--up)}
.sl-cps{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:3px 14px;margin-top:8px}
.sl-cp{display:grid;grid-template-columns:110px minmax(0,1fr);align-items:center;gap:6px;font-size:11px;color:var(--mute)}
.sl-cp i{display:block;height:5px;border-radius:1px} .sl-cp i.up{background:var(--up)} .sl-cp i.down{background:var(--down)}
/* force */
.sl-btns{display:inline-flex;gap:3px} .sl-btns button{font:600 10.5px var(--mono);padding:2px 7px;border-radius:2px;background:transparent;border:1px solid var(--line);color:var(--mute)}
.sl-btns button.on{background:var(--brass);border-color:var(--brass);color:#050402}
/* squawk */
.sl-squawk{display:flex;flex-direction:column;height:100%;min-height:640px}
.sq-f{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px}
.sq-f button{font:600 10.5px var(--mono);letter-spacing:.04em;padding:3px 8px;border-radius:2px;background:transparent;border:1px solid var(--line);color:var(--mute)}
.sq-f button.on{background:rgba(232,195,90,.14);border-color:var(--brass);color:var(--brass)}
.sq-l{list-style:none;margin:0;padding:0;overflow-y:auto;flex:1;max-height:760px}
.sq-l li{display:grid;grid-template-columns:42px 14px minmax(0,1fr);grid-template-rows:auto auto;column-gap:6px;padding:7px 2px;border-bottom:1px solid var(--band2);font-size:12.5px;line-height:1.45}
.sq-l li[hidden]{display:none}
.sq-l time{grid-row:1;grid-column:1;font:600 11.5px var(--mono);color:var(--ink)} .sq-l small{grid-row:2;grid-column:1;font:500 9.5px var(--mono);color:var(--mute)}
.sq-l i{grid-row:1/3;grid-column:2;font-style:normal;color:var(--brass);font-size:10px;padding-top:2px}
.sq-l span{grid-row:1/3;grid-column:3} .sq-l span a{color:var(--ink);text-decoration:none} .sq-l span a:hover{text-decoration:underline}
.sq-l em{font-style:normal;color:var(--mute);font-size:11px}
.sq-l .sq-chiffre i{color:#7c8cff} .sq-l .sq-chiffre.fort span{color:#fff;font-weight:600}
.sq-l .sq-mouvement.up i,.sq-l .sq-mouvement.up span{color:var(--up)} .sq-l .sq-mouvement.down i,.sq-l .sq-mouvement.down span{color:var(--down)}
/* taux */
.tx{display:grid;grid-template-columns:minmax(0,1fr);gap:8px}
.tx-g{max-width:320px}
.tx-bs{display:flex;align-items:flex-end;gap:10px;height:110px;padding-top:16px}
.tx-b{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:flex-end;height:100%;gap:3px}
.tx-b b{font:700 11px var(--mono)} .tx-b i{display:block;width:100%;border-radius:1px} .tx-b span{font-size:10px;color:var(--mute)}
.tx-b i.down{background:var(--down)} .tx-b i.neutre{background:#8b8f9c} .tx-b i.up{background:var(--up)}
.sl-tab{width:100%;border-collapse:collapse;font-size:11.5px}
.sl-tab th{font:600 9.5px var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--mute);text-align:right;padding:4px 5px;border-bottom:1px solid var(--line)}
.sl-tab th:first-child,.sl-tab td:first-child{text-align:left} .sl-tab td{padding:5px;border-bottom:1px solid var(--band2);font-family:var(--mono);white-space:nowrap}
.tx-t{overflow-x:auto}
.sl-tab td.num{text-align:right}
/* horaires */
.mh-l{display:flex;flex-direction:column;gap:5px;position:relative}
.mh-r{display:grid;grid-template-columns:76px minmax(0,1fr) 100px;align-items:center;gap:8px;font:600 11px var(--mono)}
.mh-r .mh-n{color:var(--ink)} .mh-r .mh-n i{display:inline-block;width:7px;height:7px;border-radius:50%;background:#3a3424;margin-right:6px}
.mh-r.ouvert .mh-n i{background:var(--up);box-shadow:0 0 6px var(--up)}
.mh-tr{position:relative;height:12px;background:#0f0d09;border:1px solid var(--band2)}
.mh-tr b{position:absolute;top:0;bottom:0;background:rgba(232,195,90,.28)} .mh-r.ouvert .mh-tr b{background:rgba(53,201,214,.45)}
.mh-tr u{position:absolute;top:-3px;bottom:-3px;width:2px;background:#fff;text-decoration:none}
.mh-r .mh-c{text-align:right;color:var(--mute);font-weight:500}
.mh-axe{display:flex;justify-content:space-between;margin:4px 108px 0 84px;font:500 9.5px var(--mono);color:var(--mute)}
/* saison */
.sa{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:4px}
.sa-c{display:flex;flex-direction:column;align-items:center;gap:3px;padding:6px 2px;border:1px solid var(--band2);background:#0c0b08;cursor:help}
.sa-c span{font:600 10px var(--mono);color:var(--mute);text-transform:uppercase} .sa-c b{font:700 11.5px var(--mono)} .sa-c small{font:500 9.5px var(--mono);color:var(--mute)}
.sa-bar{position:relative;width:60%;height:70px} .sa-bar i{position:absolute;left:0;right:0} .sa-bar i.up{background:var(--up)} .sa-bar i.down{background:var(--down)}
.sa-bar::after{content:"";position:absolute;left:-4px;right:-4px;top:50%;border-top:1px solid #3a3424}
.sa-c.sa-now{border-color:var(--brass);background:rgba(232,195,90,.08)} .sa-c.sa-next{border-style:dashed;border-color:var(--brass)}
/* heatmap */
.hm{width:100%;border-collapse:separate;border-spacing:3px;font:600 11.5px var(--mono)}
.hm th{font:600 9.5px var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--mute);padding:3px 4px;text-align:center}
.hm tbody th{text-align:left;font:600 12px var(--text);letter-spacing:0;text-transform:none;color:var(--ink);white-space:nowrap}
.hm td{text-align:center;padding:7px 4px;white-space:nowrap}
.hm .hm-u{background:rgba(53,201,214,.22);color:#9feef5} .hm .hm-d{background:rgba(255,90,95,.2);color:#ffb3b5}
.hm .hm-n{background:#15130e;color:var(--mute)} .hm .hm-hv{background:rgba(232,195,90,.22);color:#f3dc9a} .hm .hm-lv{background:#100f0b;color:#77715f}
.hm tr.hm-or th{color:var(--brass)}
.ec-salle{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px}
@media (max-width:1400px){.sl{grid-template-columns:minmax(0,1fr) minmax(0,1.4fr)}.sl-col:nth-child(3){grid-column:1/-1;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
@media (max-width:1000px){.sl,.ec-salle{grid-template-columns:minmax(0,1fr)}.sl-col:nth-child(3){grid-template-columns:minmax(0,1fr)}.sl-squawk{min-height:0}.sq-l{max-height:420px}}
@media (max-width:640px){.sl-sent{grid-template-columns:minmax(0,1fr)}.tx{grid-template-columns:minmax(0,1fr)}.sa{grid-template-columns:repeat(6,minmax(0,1fr))}
  .mh-axe{margin:4px 70px 0 70px}.mh-r{grid-template-columns:62px minmax(0,1fr) 64px}}
"""

JS = r"""
(function(){
/* Filtres du Squawk et fenêtres de la force des actifs (délégation : survit aux rafraîchissements) */
document.addEventListener('click', function(ev){
  var b = ev.target.closest && ev.target.closest('.sq-f button');
  if (b){ var f = b.getAttribute('data-sq'), p = b.closest('.sl-p');
    p.querySelectorAll('.sq-f button').forEach(function(x){ x.classList.toggle('on', x === b); });
    p.querySelectorAll('.sq-l li[data-sq]').forEach(function(li){ li.hidden = !(f === 'tout' || li.getAttribute('data-sq') === f); }); }
  var c = ev.target.closest && ev.target.closest('.sl-btns button');
  if (c){ var w = c.getAttribute('data-fen'), q = c.closest('.sl-p');
    q.querySelectorAll('.sl-btns button').forEach(function(x){ x.classList.toggle('on', x === c); });
    q.querySelectorAll('.sl-fv').forEach(function(v){ v.hidden = v.getAttribute('data-fen') !== w; }); }
});
/* Horaires des marchés : sessions locales converties à l'heure de Paris */
var S = [['Sydney','Australia/Sydney',7,16],['Tokyo','Asia/Tokyo',9,18],['Londres','Europe/London',8,17],['New York','America/New_York',8,17]];
function heureDans(tz, d){ var o = {}; new Intl.DateTimeFormat('en-GB', {timeZone: tz, hour12: false, weekday: 'short', hour: '2-digit', minute: '2-digit'})
  .formatToParts(d).forEach(function(p){ o[p.type] = p.value; }); return {j: o.weekday, h: (+o.hour % 24) + (+o.minute) / 60}; }
function decalage(tz, d){ var a = heureDans(tz, d).h, b = heureDans('Europe/Paris', d).h, x = a - b; if (x > 12) x -= 24; if (x < -12) x += 24; return x; }
function hm(x){ x = ((x % 24) + 24) % 24; var h = Math.floor(x), m = Math.round((x - h) * 60); if (m === 60){ h = (h + 1) % 24; m = 0; } return ('0' + h).slice(-2) + ':' + ('0' + m).slice(-2); }
function dur(x){ var h = Math.floor(x), m = Math.round((x - h) * 60); if (m === 60){ h++; m = 0; } return (h >= 24 ? Math.floor(h / 24) + 'j ' + (h % 24) : h) + 'h' + ('0' + m).slice(-2); }
function tour(){
  var L = document.getElementById('mh-l'); if (!L) return;
  var d = new Date(), p = heureDans('Europe/Paris', d), h = '';
  S.forEach(function(s){
    var dec = decalage(s[1], d), a = s[2] - dec, b = s[3] - dec, loc = heureDans(s[1], d);
    var estOuvert = function(t){ var l = heureDans(s[1], t); return l.j !== 'Sat' && l.j !== 'Sun' && l.h >= s[2] && l.h < s[3]; };
    var ouvert = estOuvert(d), reste = 0;
    for (var k = 1; k <= 4 * 24 * 12; k++){        /* prochain changement d’état, par pas de 5 min, week-end compris */
      if (estOuvert(new Date(d.getTime() + k * 300000)) !== ouvert){ reste = k / 12; break; } }
    var seg = function(x0, x1){ return '<b style="left:' + (x0 / 24 * 100).toFixed(2) + '%;width:' + ((x1 - x0) / 24 * 100).toFixed(2) + '%"></b>'; };
    var a2 = ((a % 24) + 24) % 24, b2 = a2 + (s[3] - s[2]);
    var barres = b2 <= 24 ? seg(a2, b2) : seg(a2, 24) + seg(0, b2 - 24);
    h += '<div class="mh-r' + (ouvert ? ' ouvert' : '') + '"><span class="mh-n"><i></i>' + s[0] + '</span><div class="mh-tr">' + barres +
         '<u style="left:' + (p.h / 24 * 100).toFixed(2) + '%"></u></div><span class="mh-c">' + (ouvert ? '<b class="up">ouvert</b> · ' : '') + dur(reste) + '</span></div>';
  });
  L.innerHTML = h;
  var e = document.getElementById('mh-h'); if (e) e.textContent = hm(p.h) + ' Paris';
  var i = document.getElementById('mh-info');
  if (i){ var lo = decalage('Europe/London', d), ny = decalage('America/New_York', d);
    i.textContent = 'Chevauchement Londres – New York : ' + hm(8 - ny) + ' – ' + hm(17 - lo) + ' (heure de Paris), le plus liquide pour l’or.'; }
}
tour(); setInterval(tour, 30000);
})();
"""
