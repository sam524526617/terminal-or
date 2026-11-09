# -*- coding: utf-8 -*-
"""
AGENDA ÉCONOMIQUE (fonction ECO) : la semaine jour par jour, surprise et réaction de l'or pour chaque chiffre
===========================================================================================================

- En tête : la prochaine annonce forte, le compte à rebours, la fenêtre où le plan interdit d'entrer, l'état actuel.
- La semaine jour par jour, comme un écran ECO : heure, annonce, impact, prévu, précédent, réel, surprise (écart et
  sens pour l'or), puis pour les chiffres déjà publiés la réaction de l'or à 15 min et à 1 h et une mini-courbe de
  l'or de 1 h avant à 2 h après (bougies 15 min).
- Le détail de la prochaine annonce forte : pourquoi elle compte, scénarios, ce que le marché anticipe, et comment
  l'or a réagi aux publications précédentes du même type (dès qu'il y en a assez).
- Les statistiques de réaction par type d'annonce, à partir des annonces mémorisées par le terminal.
"""

import html
import re
from datetime import timedelta

import numpy as np
import pandas as pd

import plan as P

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if signe and abs(x) < 0.5 * 10 ** (-d):
        x = 0.0
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def nombre(txt):
    """'221K' -> (221000, 'K'), '0.3%' -> (0.3, '%'), '-1.2B' -> (-1.2e9, 'B')."""
    m = re.match(r"^\s*[<>]?\s*(-?\d+(?:[.,]\d+)?)\s*([KMBT%]?)", str(txt or ""))
    if not m:
        return None, ""
    v = float(m.group(1).replace(",", "."))
    u = m.group(2)
    return v * {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(u, 1), u


def surprise(e):
    """Écart réel - prévu, dans l'unité de l'annonce, et sens pour l'or (+1 soutien, -1 pression, 0 conforme)."""
    r, u = nombre(e.get("reel"))
    p, _ = nombre(e.get("prevision"))
    if r is None or p is None:
        return None, None, 0
    ecart = r - p
    if abs(ecart) < 1e-12:
        return 0.0, u, 0
    inverse = bool(re.search(r"unemployment|claims|jobless", e.get("titre", ""), re.I))
    dur = (ecart > 0) != inverse          # chiffre « dur » : économie forte ou inflation haute -> Fed plus stricte
    return ecart, u, (-1 if dur else 1)


def fmt_ecart(ecart, u):
    if ecart is None:
        return "—"
    div = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}.get(u, 1)
    v = ecart / div
    d = 0 if (u in ("K", "M", "B") and abs(v) >= 10) else (2 if u == "%" else 1)
    return fr(v, d, True) + (u if u != "%" else " pt")


def reaction(barres, t, ajust):
    """Mouvement de l'or (bougies 15 min) après une publication, et mini-courbe de -1 h à +2 h."""
    if barres is None or not len(barres):
        return None
    ts = pd.Timestamp(t)
    try:
        idx = barres.index
        if idx.tz is not None and ts.tzinfo is None:
            ts = ts.tz_localize(idx.tz)
        elif idx.tz is not None:
            ts = ts.tz_convert(idx.tz)
    except Exception:
        return None
    av = barres[barres.index < ts]
    ap = barres[(barres.index >= ts) & (barres.index < ts + pd.Timedelta(hours=2))]
    if not len(av) or not len(ap) or (ap.index[0] - ts) > pd.Timedelta(minutes=20):
        return None
    pre = float(av["close"].iloc[-1])
    m15 = float(ap["close"].iloc[0]) - pre
    p60 = ap[ap.index < ts + pd.Timedelta(hours=1)]
    m60 = float(p60["close"].iloc[-1]) - pre if len(p60) >= 4 else None
    fen = barres[(barres.index >= ts - pd.Timedelta(hours=1)) & (barres.index < ts + pd.Timedelta(hours=2))]["close"]
    return {"m15": m15, "m60": m60, "serie": fen.to_numpy() + ajust, "pos0": int((fen.index < ts).sum()),
            "amp": float(ap.iloc[:4]["high"].max() - ap.iloc[:4]["low"].min())}


def mini(serie, pos0, w=120, h=28):
    if serie is None or len(serie) < 3:
        return ""
    lo, hi = float(np.min(serie)), float(np.max(serie))
    r = (hi - lo) or 1
    x = lambda i: 2 + i * (w - 4) / (len(serie) - 1)
    pts = " ".join(f"{x(i):.1f},{h - 3 - (float(v) - lo) / r * (h - 6):.1f}" for i, v in enumerate(serie))
    cl = "up" if serie[-1] >= serie[min(pos0, len(serie) - 1)] else "down"
    return (f'<svg class="ec-mini {cl}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<line x1="{x(pos0):.1f}" x2="{x(pos0):.1f}" y1="0" y2="{h}" class="t0"/><polyline points="{pts}"/></svg>')


def dans(delta):
    m = int(delta.total_seconds() // 60)
    if m < 0:
        return "publiée"
    if m < 60:
        return f"dans {m} min"
    if m < 1440:
        return f"dans {m // 60} h {m % 60:02d}"
    return f"dans {m // 1440} j {(m % 1440) // 60} h"


# ---------------------------------------------------------------------------
# BLOCS
# ---------------------------------------------------------------------------

def entete(cal, fiche, now, reactions_types):
    forts = [e for e in cal or [] if e.get("impact") == "High"]
    proche = next((e for e in forts if abs((e["date"] - now).total_seconds()) <= P.NEWS_MIN * 60), None)
    semaine = [e for e in forts if now - timedelta(days=now.weekday()) <= e["date"] <= now + timedelta(days=7 - now.weekday())]
    etat = (f'<span class="ec-etat ko">ZONE INTERDITE · {esc(proche["titre"])} à {proche["date"]:%H:%M}</span>' if proche else
            '<span class="ec-etat ok">Aucune annonce forte à ±30 min : entrées autorisées</span>')
    if not fiche:
        return (f'<section class="ec-hero"><div class="ec-g"><span class="k">Prochaine annonce forte</span><b>Aucune cette semaine</b>{etat}</div>'
                f'</section>')
    d = fiche["date"]
    histo = next((r for r in reactions_types or [] if r["type"] == fiche.get("type_nom")), None)
    hist_txt = (f'Sur les {histo["n"]} dernières publications : amplitude médiane de {fr(histo["med_rng"], 0)} $ dans l\'heure, '
                f'premier mouvement retourné dans {fr(histo["retour_pct"], 0)} % des cas.' if histo and histo["n"] >= 3 else
                "Historique de réaction en cours de constitution (le terminal mémorise chaque publication).")
    nu = "".join(f"<li>{esc(n)}</li>" for n in fiche.get("nuances") or [])
    return (f'<section class="ec-hero"><div class="ec-g"><span class="k">Prochaine annonce forte</span><b>{esc(fiche["nom"])}</b>'
            f'<small>{esc(fiche["titre"])} · {JOURS[d.weekday()].lower()} {d.day} {MOIS[d.month - 1]} à {d:%H:%M}</small>'
            f'<div class="ec-cd">{dans(d - now)}</div>'
            f'<div class="ec-pp"><div><span class="k">Prévu</span><b>{esc(fiche.get("prevision") or "—")}</b></div>'
            f'<div><span class="k">Précédent</span><b>{esc(fiche.get("precedent") or "—")}</b></div>'
            f'<div><span class="k">Pas d\'entrée</span><b class="warn">{(d - timedelta(minutes=30)):%H:%M} → {(d + timedelta(minutes=30)):%H:%M}</b></div></div>'
            f'{etat}<p class="ec-semaine">{len(semaine)} annonce(s) forte(s) cette semaine.</p></div>'
            f'<div class="ec-d">{f"<p>{esc(fiche["pourquoi"])}</p>" if fiche.get("pourquoi") else ""}'
            f'<div class="ec-scen"><div class="down"><span>Au-dessus de la prévision</span>{esc(fiche.get("haut") or "")}</div>'
            f'<div class="up"><span>En dessous</span>{esc(fiche.get("bas") or "")}</div></div>'
            f'{f"<ul class=ec-nu>{nu}</ul>" if nu else ""}<p class="ec-histo">{esc(hist_txt)}</p></div></section>')


def semaine(cal, barres, ajust, now, type_fn):
    evts = [e for e in cal or [] if e.get("date") is not None
            and now - timedelta(days=now.weekday() + 1) <= e["date"] <= now + timedelta(days=10)]
    if not evts:
        return ""
    par_jour = {}
    for e in sorted(evts, key=lambda x: x["date"]):
        par_jour.setdefault(e["date"].date(), []).append(e)
    corps = ""
    for j, lst in par_jour.items():
        aujourd = j == now.date()
        corps += (f'<tr class="ec-jour{" ec-auj" if aujourd else ""}"><th colspan="11">{JOURS[j.weekday()]} {j.day} {MOIS[j.month - 1]}'
                  f'{" · aujourd&#39;hui" if aujourd else ""}</th></tr>')
        for e in lst:
            passe = e["date"] <= now
            ec, u, sens = surprise(e) if e.get("reel") else (None, None, 0)
            rx = reaction(barres, e["date"], ajust) if passe and e.get("reel") else None
            lect = ""
            if rx and sens and rx.get("m60") is not None:
                conforme = (rx["m60"] > 0) == (sens > 0)
                lect = ("conforme" if conforme else "inverse") if abs(rx["m60"]) >= 2 else "sans réaction"
            nom = type_fn(e["titre"]) or ""
            sous = f"<small>{esc(e['titre'])}</small>" if nom else ""
            cls = ("ec-fort " if e["impact"] == "High" else "") + ("ec-passe" if passe else "")
            corps += (f'<tr class="{cls}"><td class="num">{e["date"]:%H:%M}</td>'
                      f'<td><i class="ec-imp {"fort" if e["impact"] == "High" else ""}" title="{"impact fort" if e["impact"] == "High" else "impact moyen"}"></i></td>'
                      f'<td><b>{esc(nom or e["titre"])}</b>{sous}</td>'
                      f'<td class="num">{esc(e.get("prevision") or "—")}</td><td class="num">{esc(e.get("precedent") or "—")}</td>'
                      f'<td class="num ec-reel">{esc(e.get("reel") or ("" if passe else "à venir"))}</td>'
                      f'<td class="num {"up" if sens > 0 else "down" if sens < 0 else ""}">{fmt_ecart(ec, u) if ec is not None else ""}</td>'
                      f'<td class="{"up" if sens > 0 else "down" if sens < 0 else "flat"}">'
                      f'{"" if ec is None else ("soutien" if sens > 0 else "pression" if sens < 0 else "conforme")}</td>'
                      f'<td class="num {"up" if rx and rx["m15"] > 0 else "down" if rx and rx["m15"] < 0 else ""}">{fr(rx["m15"], 1, True) if rx else ""}</td>'
                      f'<td class="num {"up" if rx and (rx["m60"] or 0) > 0 else "down" if rx and (rx["m60"] or 0) < 0 else ""}">'
                      f'{fr(rx["m60"], 1, True) if rx and rx["m60"] is not None else ""}</td>'
                      f'<td>{mini(rx["serie"], rx["pos0"]) if rx else ""}{f"<small class=ec-lect>{lect}</small>" if lect else ""}</td></tr>')
    return (f'<section><h2>Agenda de la semaine</h2><p class="pourquoi">Annonces USD (ForexFactory), heure de Paris. Surprise = réel − prévu ; '
            f'« soutien » ou « pression » = effet habituel sur l\'or (un chiffre fort ou une inflation haute rendent la Fed plus stricte). '
            f'Or 15 min et 1 h : mouvement en $ après la publication ; courbe : de 1 h avant à 2 h après (trait = publication). '
            f'« Inverse » = l\'or a réagi à l\'opposé de la surprise, souvent l\'information la plus utile de la journée.</p>'
            f'<div class="tbl"><table class="ec-tab"><thead><tr><th>Heure</th><th></th><th>Annonce</th><th>Prévu</th><th>Précédent</th>'
            f'<th>Réel</th><th>Surprise</th><th>Pour l\'or</th><th>Or 15 min</th><th>Or 1 h</th><th>Réaction</th></tr></thead>'
            f'<tbody>{corps}</tbody></table></div></section>')


def stats_types(reactions_types):
    if not reactions_types:
        return ""
    lig = ""
    for r in reactions_types:
        assez = r["n"] >= 3
        lig += (f'<tr><th scope="row">{esc(r["type"])}</th><td class="num">{r["n"]}</td>'
                + (f'<td class="num">{fr(r["med_rng"], 0)} $</td><td class="num">{fr(r["max_rng"], 0)} $</td>'
                   f'<td class="num">{fr(r["retour_pct"], 0)} %</td><td class="num">{fr(r["hausse_pct"], 0)} %</td>' if assez else
                   '<td colspan="4" class="flat">pas assez de publications mesurées (minimum 3)</td>') + '</tr>')
    return (f'<section><h2>Comment l\'or réagit, par type d\'annonce</h2><p class="pourquoi">Mesuré sur les publications mémorisées par '
            f'le terminal (et les décisions de la Fed). Amplitude = plus haut − plus bas dans l\'heure ; retournement = le mouvement à 1 h '
            f'va à l\'inverse des 15 premières minutes. Les statistiques ne s\'affichent qu\'à partir de 3 publications.</p>'
            f'<div class="tbl"><table class="ec-tab"><thead><tr><th>Annonce</th><th>Mesures</th><th>Amplitude 1 h (médiane)</th>'
            f'<th>Amplitude max</th><th>Retournements</th><th>Or en hausse</th></tr></thead><tbody>{lig}</tbody></table></div></section>')


def bloc_eco(cal, barres, ajust, fiche, reactions_types, now, type_fn, plus=""):
    return (entete(cal, fiche, now, reactions_types) + semaine(cal, barres, ajust, now, type_fn)
            + stats_types(reactions_types) + plus)


CSS = """
/* Agenda économique (ECO) */
.ec-hero{display:flex;flex-wrap:wrap;gap:16px 28px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:24px}
.ec-g{flex:1 1 320px;display:flex;flex-direction:column;gap:4px}
.ec-g>b{font:700 26px/1.15 var(--text)} .ec-g>small{color:var(--mute);font-size:13px}
.ec-cd{font:700 30px var(--mono);color:var(--brass);margin:4px 0}
.ec-pp{display:flex;flex-wrap:wrap;gap:10px 24px} .ec-pp div{display:flex;flex-direction:column} .ec-pp b{font:600 17px var(--mono)}
.ec-etat{align-self:flex-start;margin-top:8px;font:700 11.5px var(--text);letter-spacing:.04em;padding:4px 9px;border-radius:5px;border:1px solid currentColor}
.ec-etat.ok{color:var(--up)} .ec-etat.ko{color:var(--down);background:rgba(255,90,95,.1)}
.ec-semaine{margin:6px 0 0;font-size:12.5px;color:var(--mute)}
.ec-d{flex:2 1 420px;display:flex;flex-direction:column;gap:10px;font-size:14px;line-height:1.5} .ec-d p{margin:0}
.ec-scen{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}
.ec-scen div{background:var(--band);border-radius:8px;padding:8px 10px;font-size:13.5px}
.ec-scen span{display:block;font:700 10.5px var(--text);letter-spacing:.06em;text-transform:uppercase;margin-bottom:2px}
.ec-scen .down span{color:var(--down)} .ec-scen .up span{color:var(--up)}
.ec-nu{margin:0;padding-left:18px;color:var(--amber);font-size:13px} .ec-histo{color:var(--mute);font-size:13px}
.ec-tab{width:100%;border-collapse:collapse;font-size:13px}
.ec-tab th{text-align:left;font:600 10.5px var(--text);letter-spacing:.06em;text-transform:uppercase;color:var(--mute);padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
.ec-tab tbody th{font:600 13px var(--text);text-transform:none;letter-spacing:0;color:var(--ink)}
.ec-tab td{padding:6px 8px;border-bottom:1px solid var(--band2);vertical-align:middle}
.ec-tab td.num{font-family:var(--mono);white-space:nowrap}
.ec-tab td b{font-weight:600} .ec-tab td small{display:block;color:var(--mute);font-size:11.5px}
.ec-tab tr.ec-jour th{font:700 11px var(--text);letter-spacing:.1em;text-transform:uppercase;color:var(--brass);padding:14px 8px 6px;border-bottom:1px solid var(--brass)}
.ec-tab tr.ec-auj th{color:#000;background:var(--brass)}
.ec-tab tr.ec-passe td{color:var(--mute)} .ec-tab tr.ec-passe td b{color:var(--ink)}
.ec-tab tr.ec-fort td b{color:var(--ink)}
.ec-tab td.ec-reel{color:var(--ink);font-weight:700}
.ec-imp{display:inline-block;width:9px;height:9px;border-radius:50%;background:#55555e} .ec-imp.fort{background:var(--amber)}
.ec-mini{display:inline-block;vertical-align:middle} .ec-mini polyline{fill:none;stroke-width:1.5}
.ec-mini.up polyline{stroke:var(--up)} .ec-mini.down polyline{stroke:var(--down)} .ec-mini .t0{stroke:#55555e;stroke-dasharray:2 2}
.ec-lect{display:inline-block !important;margin-left:6px;font-size:11.5px;color:var(--mute)}
@media (max-width:640px){.ec-scen{grid-template-columns:minmax(0,1fr)}}
"""
