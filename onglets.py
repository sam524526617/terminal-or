# -*- coding: utf-8 -*-
"""
ONGLETS ANNONCES, ACTUALITÉS ET SUIVI (v7) : tout est relié au plan
===================================================================

Annonces    : la prochaine annonce forte et la fenêtre où le plan interdit d'entrer, toutes les fenêtres interdites
              de la semaine, les surprises récentes et la réaction de l'or, l'historique des réactions par type,
              le calendrier complet.
Actualités  : un seul fil, sans doublons, du plus récent au plus ancien, étiqueté (Fed dure ou souple,
              escalade ou détente), le baromètre et les communications officielles de la Fed.
Suivi       : les règles du plan, le plan rejoué sur les 60 derniers jours, le journal de tes trades
              (pris ou ignorés, mémorisé dans ce navigateur) et la méthode.
"""

import html
import re
from datetime import timedelta

import numpy as np
import pandas as pd

import plan as P

JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n.d."
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def jour_heure(d):
    return f"{JOURS[d.weekday()]} {d:%d/%m %H:%M}"


def dans(delta):
    m = int(delta.total_seconds() // 60)
    if m < 0:
        return "publiée"
    if m < 60:
        return f"dans {m} min"
    if m < 24 * 60:
        return f"dans {m // 60} h {m % 60:02d}"
    return f"dans {m // 1440} j {(m % 1440) // 60} h"


# ---------------------------------------------------------------------------
# ANNONCES
# ---------------------------------------------------------------------------

def fenetres_interdites(cal, now, jours=7):
    """Annonces USD fortes des 7 prochains jours, fenêtres de ±30 min fusionnées quand elles se chevauchent."""
    evts = sorted((e for e in cal or [] if e.get("impact") == "High" and e.get("date") is not None
                   and now - timedelta(minutes=P.NEWS_MIN) <= e["date"] <= now + timedelta(days=jours)),
                  key=lambda e: e["date"])
    out = []
    for e in evts:
        d0, d1 = e["date"] - timedelta(minutes=P.NEWS_MIN), e["date"] + timedelta(minutes=P.NEWS_MIN)
        if out and d0 <= out[-1]["fin"]:
            out[-1]["fin"] = max(out[-1]["fin"], d1)
            out[-1]["noms"].append(e["titre"])
        else:
            out.append({"debut": d0, "fin": d1, "noms": [e["titre"]]})
    return out


def bloc_annonces(fiches, cal, reactions_evt, reactions, now, adjudications_html="", extra=""):
    h = []
    # 1. prochaine annonce forte
    if fiches:
        fi = fiches[0]
        d = fi["date"]
        win = f"{(d - timedelta(minutes=P.NEWS_MIN)):%H:%M} → {(d + timedelta(minutes=P.NEWS_MIN)):%H:%M}"
        nu = "".join(f"<li>{esc(n)}</li>" for n in fi.get("nuances") or [])
        h.append(
            f'<section class="on-hero"><div class="on-hero-g"><span class="k">Prochaine annonce forte</span>'
            f'<b>{esc(fi["nom"])}</b><small>{esc(fi["titre"])} · {jour_heure(d)} · <span class="brass">{dans(d - now)}</span></small>'
            f'<div class="on-pp"><div><span class="k">Prévu</span><b>{esc(fi.get("prevision") or "—")}</b></div>'
            f'<div><span class="k">Précédent</span><b>{esc(fi.get("precedent") or "—")}</b></div>'
            f'<div class="on-interdit"><span class="k">Ton plan : pas d\'entrée</span><b>{win}</b></div></div></div>'
            f'<div class="on-hero-d">{f"<p>{esc(fi["pourquoi"])}</p>" if fi.get("pourquoi") else ""}'
            f'<ul class="on-scen"><li class="down"><span>Au-dessus de la prévision</span>{esc(fi.get("haut") or "")}</li>'
            f'<li class="up"><span>En dessous</span>{esc(fi.get("bas") or "")}</li></ul>'
            f'{f"<ul class=on-nu>{nu}</ul>" if nu else ""}'
            f'{f"<p class=on-histo>{esc(fi["histo"])}</p>" if fi.get("histo") else ""}'
            f'<p class="on-regle">Si une position du plan est déjà ouverte : on ne touche à rien, le stop est là pour ça. '
            f'Pas de nouvel ordre pendant la fenêtre ; un ordre limite en attente qui tomberait dedans est annulé.</p></div></section>')
        if len(fiches) > 1:
            cartes = "".join(
                f'<div class="on-carte"><span class="k">{jour_heure(x["date"])} · {dans(x["date"] - now)}</span><b>{esc(x["nom"])}</b>'
                f'<small>prévu {esc(x.get("prevision") or "—")} · précédent {esc(x.get("precedent") or "—")}</small>'
                f'<p><span class="down">▲ au-dessus :</span> {esc(x.get("haut") or "")}</p>'
                f'<p><span class="up">▼ en dessous :</span> {esc(x.get("bas") or "")}</p></div>' for x in fiches[1:4])
            h.append(f'<section><h2>Ensuite</h2><div class="on-cartes">{cartes}</div></section>')
    else:
        h.append('<section class="on-hero"><div class="on-hero-g"><span class="k">Prochaine annonce forte</span>'
                 '<b>Aucune cette semaine</b><small>Rien ne bloque le plan côté annonces.</small></div></section>')
    # 2. fenêtres interdites
    fen = fenetres_interdites(cal, now)
    if fen:
        lig = "".join(f'<tr class="{"on-now" if f["debut"] <= now <= f["fin"] else ""}"><th scope="row">{jour_heure(f["debut"])[:-6]}</th>'
                      f'<td class="num">{f["debut"]:%H:%M} → {f["fin"]:%H:%M}</td><td>{esc(" · ".join(f["noms"]))}</td></tr>' for f in fen)
        h.append(f'<section><h2>Fenêtres interdites de la semaine</h2><p class="pourquoi">Annonces USD à fort impact : '
                 f'pas d\'entrée {P.NEWS_MIN} min avant et après (heure de Paris). Les fenêtres qui se chevauchent sont regroupées.</p>'
                 f'<div class="tbl"><table class="on-tab"><thead><tr><th>Jour</th><th>Fenêtre</th><th>Annonces</th></tr></thead>'
                 f'<tbody>{lig}</tbody></table></div></section>')
    # 3. surprises récentes et réaction
    if reactions_evt:
        def mv(v, d=2, u=" %"):
            return f'<td class="num {"up" if (v or 0) > 0 else "down" if (v or 0) < 0 else ""}">{fr(v, d, True, u)}</td>'
        lig = "".join(
            f'<tr><th scope="row">{esc(r["nom"])}<small>{jour_heure(r["date"])}</small></th>'
            f'<td class="num">{esc(r.get("prevision") or "—")}</td><td class="num"><b>{esc(r.get("reel") or "—")}</b></td>'
            f'<td class="{"down" if r["sens_surprise"] > 0 else "up" if r["sens_surprise"] < 0 else "flat"}">'
            f'{"dure" if r["sens_surprise"] > 0 else "souple" if r["sens_surprise"] < 0 else "conforme"}</td>'
            + mv(r["reac"].get("or_15")) + mv(r["reac"].get("or_60")) + mv(r["reac"].get("dxy_60")) + mv(r["reac"].get("us2_60"), 1, " pb")
            + f'<td class="on-lect">{esc(r["interpretation"])}</td></tr>' for r in reactions_evt)
        h.append(f'<section><h2>Publiées récemment : surprise et réaction</h2><p class="pourquoi">Surprise « dure » = chiffre '
                 f'qui pousse la Fed à rester stricte (bon pour le dollar, mauvais pour l\'or). Une réaction de l\'or à '
                 f'l\'inverse de la surprise est souvent l\'information la plus utile de la journée.</p>'
                 f'<div class="tbl"><table class="on-tab"><thead><tr><th>Annonce</th><th>Prévu</th><th>Réel</th><th>Surprise</th>'
                 f'<th>Or 15 min</th><th>Or 1 h</th><th>Dollar 1 h</th><th>2 ans 1 h</th><th>Lecture</th></tr></thead>'
                 f'<tbody>{lig}</tbody></table></div></section>')
    # 4. historique des réactions par type
    if reactions:
        lig = "".join(f'<tr><th scope="row">{esc(r["type"])}</th><td class="num">{r["n"]}</td><td class="num">{fr(r["med_rng"], 0)} $</td>'
                      f'<td class="num">{fr(r["max_rng"], 0)} $</td><td class="num">{fr(r["med_mv"], 0)} $</td>'
                      f'<td class="num">{fr(r["retour_pct"], 0)} %</td><td class="num">{fr(r["hausse_pct"], 0)} %</td></tr>' for r in reactions)
        h.append(f'<section><h2>Comment l\'or réagit, par type d\'annonce</h2><p class="pourquoi">Mesuré sur les annonces '
                 f'passées enregistrées par le terminal. Amplitude = écart entre plus haut et plus bas dans l\'heure ; '
                 f'retournement = le mouvement à 1 h va dans le sens inverse des 15 premières minutes. Plus il est fréquent, '
                 f'plus il est risqué d\'entrer sur le premier mouvement.</p>'
                 f'<div class="tbl"><table class="on-tab"><thead><tr><th>Annonce</th><th>Publications</th><th>Amplitude 1 h (médiane)</th>'
                 f'<th>Amplitude max</th><th>Mouvement 1 h (médiane)</th><th>Retournements</th><th>Or en hausse</th></tr></thead>'
                 f'<tbody>{lig}</tbody></table></div></section>')
    # 5. calendrier complet
    sem = [e for e in cal or [] if e.get("date") is not None and now - timedelta(days=4) <= e["date"] <= now + timedelta(days=8)]
    if sem:
        def surprise(e):
            try:
                n = lambda t: float(re.match(r"^\s*[<>]?\s*(-?\d+(?:[.,]\d+)?)", str(t)).group(1).replace(",", "."))
                return n(e["reel"]) - n(e["prevision"])
            except Exception:
                return None
        lig = ""
        for e in sem:
            sp = surprise(e) if e.get("reel") and e.get("prevision") else None
            cl = "" if sp is None or sp == 0 else ("on-plus" if sp > 0 else "on-moins")
            lig += (f'<tr class="{"on-passe" if e["date"] < now else ""}"><th scope="row">{jour_heure(e["date"])}</th>'
                    f'<td><i class="on-imp {"fort" if e["impact"] == "High" else ""}"></i>{esc(e["titre"])}</td>'
                    f'<td class="num">{esc(e.get("prevision") or "—")}</td><td class="num">{esc(e.get("precedent") or "—")}</td>'
                    f'<td class="num {cl}"><b>{esc(e.get("reel") or "")}</b></td></tr>')
        h.append(f'<section><h2>Calendrier USD</h2><p class="pourquoi">Point orange : impact fort (fenêtre interdite). '
                 f'Réel en vert : au-dessus de la prévision ; en rouge : en dessous (le sens « bon pour l\'or » dépend de '
                 f'l\'annonce, voir les scénarios).</p><div class="tbl"><table class="on-tab"><thead><tr><th>Heure (Paris)</th>'
                 f'<th>Annonce</th><th>Prévu</th><th>Précédent</th><th>Réel</th></tr></thead><tbody>{lig}</tbody></table></div></section>')
    if adjudications_html or extra:
        h.append(f'<details class="on-plus"><summary>Adjudications du Trésor et calendrier en direct</summary>{adjudications_html}{extra}</details>')
    return "".join(h)


# ---------------------------------------------------------------------------
# ACTUALITÉS
# ---------------------------------------------------------------------------

ETIQ = {"hawk": ("Fed dure", "down"), "dove": ("Fed souple", "up"), "esc": ("escalade", "up"), "desc": ("détente", "down")}


def bloc_actus(news, fed_off, baro, mots, now):
    h = []
    if baro:
        def jauge(g, d, lg, ld, cg, cd, lect):
            tot = (baro.get(g, 0) + baro.get(d, 0)) or 1
            pg = baro.get(g, 0) / tot * 100
            return (f'<div class="on-baro"><div class="on-baro-t"><span class="{cg}">{lg} ({baro.get(g, 0)})</span>'
                    f'<span class="{cd}">{ld} ({baro.get(d, 0)})</span></div>'
                    f'<div class="on-baro-j"><i class="{cg}" style="width:{pg:.0f}%"></i><i class="{cd}" style="width:{100 - pg:.0f}%"></i></div>'
                    f'<small>{esc(lect)}</small></div>')
        fed = ("Ton dominant : souple, plutôt favorable à l'or." if baro.get("dove", 0) > baro.get("hawk", 0) else
               "Ton dominant : dur, plutôt défavorable à l'or." if baro.get("hawk", 0) > baro.get("dove", 0) else "Pas de ton dominant.")
        geo = ("Plus d'escalade que de détente : la prime de risque soutient l'or." if baro.get("esc", 0) > baro.get("desc", 0) else
               "Plus de détente que d'escalade : la prime de risque se dégonfle." if baro.get("desc", 0) > baro.get("esc", 0) else
               "Géopolitique sans direction nette.")
        h.append(f'<section><h2>Baromètre des titres</h2><p class="pourquoi">Mots-clés dans les {baro.get("n", 0)} titres récents, '
                 f'sans doublons. Indicatif : un titre peut utiliser un mot dans un autre sens. N\'entre pas dans la décision.</p>'
                 f'<div class="on-baros">{jauge("dove", "hawk", "Fed souple", "Fed dure", "up", "down", fed)}'
                 f'{jauge("esc", "desc", "Escalade", "Détente", "up", "down", geo)}</div></section>')
    # fil unique
    vus, fil = {}, []
    for theme, items in (news or {}).items():
        for it in items:
            cle = re.sub(r"\W+", " ", it["titre"].lower()).strip()
            if cle in vus:
                vus[cle]["themes"].add(theme)
                continue
            x = dict(it, themes={theme})
            vus[cle] = x
            fil.append(x)
    fil.sort(key=lambda x: x.get("date") or pd.Timestamp.min.tz_localize("UTC"), reverse=True)
    if fil:
        lig = ""
        for it in fil[:40]:
            t = it["titre"].lower()
            etq = [ETIQ[c] for c in ETIQ if any(re.search(r"\b" + re.escape(m), t) for m in (mots or {}).get(c, []))]
            age = ""
            if it.get("date") is not None:
                m = int((now - it["date"]).total_seconds() // 60)
                age = f"il y a {m} min" if m < 60 else (f"il y a {m // 60} h" if m < 1440 else jour_heure(it["date"]))
            lig += (f'<li><span class="on-age">{esc(age)}</span><div><a href="{esc(it.get("lien") or "#")}" target="_blank" rel="noopener">'
                    f'{esc(it["titre"])}</a><small>{esc(it.get("source") or "")} · {esc(", ".join(sorted(it["themes"])))}</small></div>'
                    f'<span class="on-etq">{"".join(f"<em class={c}>{esc(l)}</em>" for l, c in etq)}</span></li>')
        h.append(f'<section><h2>Fil d\'actualité</h2><p class="pourquoi">Les titres des quatre thèmes suivis, fusionnés, du plus '
                 f'récent au plus ancien. Étiquettes : effet habituel sur l\'or (vert = soutien, rouge = pression).</p>'
                 f'<ul class="on-fil">{lig}</ul></section>')
    if fed_off:
        cols = ""
        for cle, titre in (("communiques", "Communiqués"), ("discours", "Discours")):
            its = (fed_off or {}).get(cle) or []
            li = "".join(f'<li><a href="{esc(i.get("lien") or "#")}" target="_blank" rel="noopener">{esc(i["titre"])}</a>'
                         f'<small>{jour_heure(i["date"]) if i.get("date") is not None else ""}</small></li>' for i in its[:6])
            cols += f'<div><h3>{titre}</h3><ul class="on-fed">{li or "<li>Rien de récent.</li>"}</ul></div>'
        h.append(f'<section><h2>Réserve fédérale : communications officielles</h2><div class="on-2col">{cols}</div></section>')
    return "".join(h) or '<section><p class="vide">Aucune actualité disponible.</p></section>'


# ---------------------------------------------------------------------------
# SUIVI ET MÉTHODE
# ---------------------------------------------------------------------------

def regles_plan(compte):
    return [
        ("Annonces", f"Aucune entrée {P.NEWS_MIN} min avant et après une annonce USD à fort impact."),
        ("Fondamental", "Bloc Fed / dollar / taux réels (un seul signal) : on n'achète pas s'il est baissier, on ne vend pas s'il est haussier."),
        ("Tendance", "Daily et H4 dans le même sens (dernière cassure de structure), H1 qui ne les contredit pas. Weekly : bonus de note."),
        ("Zone", "Retour du prix dans un OB, au 50 % d'un FVG (M30, H1, H4) ou sur un support / une résistance à 3 contacts (H1, H4)."),
        ("Déclencheur", "Cassure de structure M15 dans le sens de la tendance qui laisse un FVG, dans les 2 h qui suivent le contact avec la zone."),
        ("Entrée", f"Ordre limite au 50 % du FVG M15. Annulé s'il n'est pas exécuté en {P.ORDRE_VIE_MIN // 60} h ou si le prix atteint TP1 sans nous."),
        ("Stop", "Sous l'OB d'entrée (au-dessus pour une vente), plus une marge de 0,1 ATR M15."),
        ("Objectifs", f"50 % fermé à {fr(P.TP1_R, 0)} R, stop au point d'entrée, le reste sur la prochaine liquidité H1 (3 R par défaut)."),
        ("Note", "A : Weekly aligné et (zone H4 ou divergence RSI M15). B sinon. RSI M15 extrême : un cran de moins. On ne prend que A et B."),
        ("Risque", f"{fr(compte.get('risque_pct', P.RISQUE_PCT), 1)} % du compte par trade, {P.MAX_TRADES_JOUR} trades par jour, "
                   f"arrêt de la journée à {fr(P.PERTE_MAX_JOUR_R, 0)} R, une position à la fois, sortie avant la clôture du vendredi."),
    ]


def courbe_svg(cum, w=640, h=150):
    if not cum or len(cum) < 2:
        return ""
    v = [0.0] + list(cum)
    lo, hi = min(v + [0]), max(v + [0])
    r = (hi - lo) or 1
    x = lambda i: 6 + i * (w - 12) / (len(v) - 1)
    y = lambda val: 8 + (hi - val) / r * (h - 16)
    pts = " ".join(f"{x(i):.1f},{y(val):.1f}" for i, val in enumerate(v))
    return (f'<svg viewBox="0 0 {w} {h}" class="on-courbe" role="img" aria-label="Résultat cumulé du plan en R">'
            f'<line x1="6" x2="{w - 6}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="z"/>'
            f'<polyline points="{pts}" class="{"up" if v[-1] >= 0 else "down"}"/></svg>')


def bloc_suivi(histo, compte, methode=""):
    h = []
    regles = "".join(f'<div><dt>{esc(k)}</dt><dd>{esc(v)}</dd></div>' for k, v in regles_plan(compte))
    h.append(f'<section><h2>Ton plan</h2><p class="pourquoi">Les règles exactes appliquées par la décision en haut de page, '
             f'par le rejeu ci-dessous et par le backtest. Elles ne changent qu\'après un backtest.</p><dl class="on-regles">{regles}</dl></section>')
    st = (histo or {}).get("stats")
    trades = (histo or {}).get("trades") or []
    if histo and histo.get("erreur"):
        h.append(f'<section><h2>Le plan sur les 60 derniers jours</h2><p class="vide">Indisponible : {esc(histo["erreur"])}.</p></section>')
    elif st:
        pf = "∞" if st["pf"] is None else fr(st["pf"], 2)
        h.append(
            f'<section><h2>Le plan sur les 60 derniers jours</h2><p class="pourquoi">Ce que le plan aurait fait depuis le '
            f'{st["debut"]:%d/%m}, rejoué sur les bougies 15 min du terminal avec les mêmes règles que la décision en direct. '
            f'Approximatif : bougies 15 min (pas minute par minute) et, pour les jours passés, fenêtre d\'annonces de secours. '
            f'Échantillon court : c\'est un contrôle, le backtest sur plusieurs années fait foi.</p>'
            f'<div class="on-kpis"><div><span class="k">Trades</span><b>{st["n"]}</b></div>'
            f'<div><span class="k">Réussite</span><b>{fr(st["reussite"], 0)} %</b></div>'
            f'<div><span class="k">Gain moyen</span><b class="{"up" if st["esp"] > 0 else "down"}">{fr(st["esp"], 2, True)} R</b></div>'
            f'<div><span class="k">Total</span><b class="{"up" if st["total"] > 0 else "down"}">{fr(st["total"], 1, True)} R</b>'
            f'<small>{fr(st["total"] * compte.get("risque_pct", 1), 1, True)} % du compte</small></div>'
            f'<div><span class="k">Profit factor</span><b>{pf}</b></div>'
            f'<div><span class="k">Pire baisse</span><b>{fr(st["dd"], 1)} R</b><small>pire série : {st["pire"]} pertes</small></div></div>'
            f'{courbe_svg(st["courbe"])}</section>')
    elif histo is not None:
        h.append('<section><h2>Le plan sur les 60 derniers jours</h2><p class="vide">Aucun trade du plan sur la période : '
                 'les filtres n\'ont jamais été tous verts en même temps.</p></section>')
    if trades:
        lig = "".join(
            f'<tr data-cle="{t["cle"]}" data-r="{"" if t["R"] is None or t["etat"] != "fini" else round(t["R"], 3)}">'
            f'<th scope="row">{P.depuis_horloge(t["t_sig"]).strftime("%d/%m %H:%M")}</th>'
            f'<td class="{"up" if t["sens"] > 0 else "down"}">{"Achat" if t["sens"] > 0 else "Vente"}</td><td>{esc(t["note"])}</td>'
            f'<td>{esc(t["zone"])}</td><td class="num">{fr(t["entree"])}</td><td class="num">{fr(t["stop"])}</td>'
            f'<td>{esc(t["issue"])}</td>'
            f'<td class="num {"up" if (t["R"] or 0) > 0 else "down" if (t["R"] or 0) < 0 else ""}">'
            f'{fr(t["R"], 2, True, " R") if t["R"] is not None and t["etat"] == "fini" else "en cours"}</td>'
            f'<td><div class="on-jr" role="group" aria-label="Ton trade"><button type="button" data-v="pris">Pris</button>'
            f'<button type="button" data-v="ignore">Ignoré</button></div></td></tr>' for t in trades[:40])
        h.append(
            f'<section><h2>Journal : le plan et toi</h2><p class="pourquoi">Pour chaque trade du plan, indique si tu l\'as pris '
            f'ou ignoré (mémorisé dans ce navigateur). Le bilan compare ce que tu as fait à ce que le plan a fait : si tes '
            f'trades pris font moins bien que le plan, le problème est l\'exécution, pas la stratégie.</p>'
            f'<div class="on-kpis" id="jr-bilan"><div><span class="k">Trades marqués</span><b id="jr-n">0</b></div>'
            f'<div><span class="k">Pris</span><b id="jr-pris">0</b><small id="jr-pris-r"></small></div>'
            f'<div><span class="k">Ignorés</span><b id="jr-ign">0</b><small id="jr-ign-r"></small></div>'
            f'<div><span class="k">Suivi du plan</span><b id="jr-taux">—</b></div></div>'
            f'<div class="tbl"><table class="on-tab on-journal"><thead><tr><th>Signal</th><th>Sens</th><th>Note</th><th>Zone</th>'
            f'<th>Entrée</th><th>Stop</th><th>Issue</th><th>Résultat</th><th>Toi</th></tr></thead><tbody>{lig}</tbody></table></div></section>')
    if methode:
        h.append(methode)
    return "".join(h)


METHODE = [
    ("Comment lire la décision", "En haut de page, une seule réponse à la clôture de chaque bougie 15 min : ACHAT ou VENTE si les cinq "
     "filtres sont verts et qu'un signal est actif, RIEN sinon, avec le filtre qui bloque et ce qui le débloquerait. Le ticket "
     "donne l'ordre complet ; la taille est calculée sur ton compte (Réglages)."),
    ("Structure de marché", "Un sommet (ou creux) est confirmé quand les deux bougies de chaque côté sont plus basses (plus hautes). "
     "La tendance d'une unité de temps est le sens de la dernière clôture au-delà du dernier sommet ou creux confirmé."),
    ("Zones OB, FVG et S/R", "FVG : trou laissé entre la bougie n-2 et la bougie n par un mouvement rapide ; contact au 50 %. "
     "OB : dernière bougie opposée avant ce mouvement ; contact en haut de l'OB (en bas pour une vente). S/R : niveau touché "
     "au moins 3 fois. Une zone vit 5 jours et meurt si une bougie clôture au-delà."),
    ("Le fondamental du plan", "Taux réels, dollar et taux 2 ans bougent ensemble : ils comptent pour un seul signal. Sur 5 jours, "
     "chacun vote si son mouvement dépasse son seuil ; il faut deux votes du même côté pour que le bloc penche. Le reste de "
     "l'onglet Fondamental est du contexte."),
    ("Pourquoi un backtest", "Une règle qui a l'air logique peut perdre de l'argent. Le backtest rejoue le plan minute par minute "
     "sur plusieurs années, avec l'écart achat-vente, et compare des variantes (sans tel filtre, avec tel autre). On ne change "
     "une règle que si le backtest le justifie."),
    ("Les limites", "Les données gratuites ont du retard (Yahoo, environ 10 min) et le terminal se met à jour toutes les 15 min : "
     "vérifie toujours le prix sur ta plateforme. Le prix est celui du future COMEX converti en spot ; l'écart avec ton broker est "
     "réglable. Aucun outil ne garantit un résultat : le plan donne un cadre, le risque reste le tien."),
]


def bloc_methode():
    items = "".join(f'<details><summary>{esc(t)}</summary><p>{esc(x)}</p></details>' for t, x in METHODE)
    return f'<section><h2>Méthode</h2><div class="on-methode">{items}</div></section>'


CSS = """
/* Onglets Annonces, Actualités, Suivi (v7) */
.on-hero{display:flex;flex-wrap:wrap;gap:16px 28px;padding:18px 20px;border:1px solid var(--line);border-radius:8px;background:var(--panel);margin-bottom:24px}
.on-hero-g{flex:1 1 320px;display:flex;flex-direction:column;gap:4px}
.on-hero-g>b{font:700 32px/1.1 var(--cond)} .on-hero-g>small{font-size:13.5px;color:var(--mute)}
.on-pp{display:flex;flex-wrap:wrap;gap:12px 26px;margin-top:10px}
.on-pp div{display:flex;flex-direction:column;gap:2px} .on-pp b{font:600 20px var(--cond)}
.on-interdit b{color:var(--amber)}
.on-hero-d{flex:2 1 420px;display:flex;flex-direction:column;gap:10px;font-size:14px;line-height:1.5}
.on-hero-d p{margin:0}
.on-scen{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.on-scen li{padding:8px 10px;border-radius:6px;background:var(--band)} .on-scen li span{display:block;font:600 12px var(--cond);text-transform:uppercase;letter-spacing:.05em}
.on-scen li.down span{color:var(--down)} .on-scen li.up span{color:var(--up)}
.on-nu{margin:0;padding-left:18px;color:var(--amber)} .on-histo{color:var(--mute)}
.on-regle{padding:8px 10px;border:1px dashed var(--line);border-radius:6px;color:var(--mute)}
.on-cartes{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:10px}
.on-carte{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:5px;font-size:13.5px}
.on-carte b{font:600 17px var(--cond)} .on-carte small{color:var(--mute)} .on-carte p{margin:0;line-height:1.45}
.on-tab{width:100%;border-collapse:collapse;font-size:14px}
.on-tab th{text-align:left;font:600 11px var(--cond);letter-spacing:.07em;text-transform:uppercase;color:var(--mute);padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.on-tab tbody th{font:600 14px var(--cond);letter-spacing:0;text-transform:none;color:var(--ink)}
.on-tab tbody th small{display:block;font:500 12px var(--cond);color:var(--mute)}
.on-tab td{padding:9px 10px;border-bottom:1px solid var(--band2);vertical-align:top}
.on-tab td.num{font-family:var(--cond);font-variant-numeric:tabular-nums;white-space:nowrap}
.on-tab td.on-lect{font-size:13px;color:var(--mute);min-width:240px}
.on-tab tr.on-now td,.on-tab tr.on-now th{background:rgba(240,169,75,.12)}
.on-tab tr.on-passe{opacity:.6}
.on-tab td.on-plus b{color:var(--up)} .on-tab td.on-moins b{color:var(--down)}
.on-imp{display:inline-block;width:8px;height:8px;border-radius:50%;background:#56636f;margin-right:8px} .on-imp.fort{background:var(--amber)}
.on-plus summary{cursor:pointer;font:600 15px var(--cond);padding:10px 0;color:var(--brass)}
.on-baros{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.on-baro{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:8px}
.on-baro-t{display:flex;justify-content:space-between;font:600 14px var(--cond)}
.on-baro-j{display:flex;height:10px;border-radius:5px;overflow:hidden;background:var(--band2)}
.on-baro-j i.up{background:var(--up)} .on-baro-j i.down{background:var(--down)}
.on-baro small{color:var(--mute);font-size:13px}
.on-fil{list-style:none;margin:0;padding:0}
.on-fil li{display:grid;grid-template-columns:96px minmax(0,1fr) auto;gap:12px;padding:10px 0;border-bottom:1px solid var(--band2);align-items:baseline}
.on-fil a{font-size:14.5px;color:var(--ink)} .on-fil small{display:block;color:var(--mute);font-size:12.5px;margin-top:2px}
.on-age{font:500 12.5px var(--cond);color:var(--mute)}
.on-etq{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.on-etq em{font:600 11.5px var(--cond);font-style:normal;padding:2px 7px;border-radius:4px;border:1px solid currentColor}
.on-etq em.up{color:var(--up)} .on-etq em.down{color:var(--down)}
.on-2col{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:20px}
.on-2col h3{font:600 15px var(--cond);margin:0 0 8px}
.on-fed{list-style:none;margin:0;padding:0} .on-fed li{padding:8px 0;border-bottom:1px solid var(--band2);font-size:14px}
.on-fed small{display:block;color:var(--mute);font-size:12.5px} .on-fed a{color:var(--ink)}
.on-regles{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:8px 24px;margin:0}
.on-regles div{display:grid;grid-template-columns:110px minmax(0,1fr);gap:10px;padding:8px 0;border-bottom:1px solid var(--band2)}
.on-regles dt{font:600 14px var(--cond);color:var(--brass)} .on-regles dd{margin:0;font-size:14px;line-height:1.45}
.on-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:10px 0}
.on-kpis div{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:3px}
.on-kpis b{font:600 22px var(--cond);font-variant-numeric:tabular-nums} .on-kpis small{font-size:12.5px;color:var(--mute)}
.on-courbe{display:block;width:100%;max-width:900px;height:auto;background:#0d151c;border:1px solid var(--band2);border-radius:6px}
.on-courbe .z{stroke:#2a3946;stroke-dasharray:4 4} .on-courbe polyline{fill:none;stroke-width:2}
.on-courbe polyline.up{stroke:var(--up)} .on-courbe polyline.down{stroke:var(--down)}
.on-jr{display:flex;gap:4px} .on-jr button{min-height:32px;padding:0 10px;font-size:12.5px}
.on-jr button.on{border-color:var(--brass);color:var(--brass)}
.on-methode details{border-bottom:1px solid var(--band2);padding:4px 0}
.on-methode summary{cursor:pointer;font:600 15px var(--cond);padding:8px 0}
.on-methode p{margin:0 0 10px;font-size:14px;line-height:1.55;max-width:90ch;color:var(--mute)}
@media (max-width:640px){.on-fil li{grid-template-columns:minmax(0,1fr)}.on-etq{justify-content:flex-start}.on-regles div{grid-template-columns:minmax(0,1fr)}}
"""

JS = r"""
(function(){
var CLE = 'journal-or';
function lireJ(){ try { return JSON.parse(localStorage.getItem(CLE) || '{}') || {}; } catch(e){ return {}; } }
function ecrireJ(j){ try { localStorage.setItem(CLE, JSON.stringify(j)); } catch(e){} }
function nbFr(x, d){ return (x > 0 ? '+' : '') + x.toLocaleString('fr-FR', {minimumFractionDigits: d, maximumFractionDigits: d}); }
function majJournal(){
  var j = lireJ(), n = 0, np = 0, ni = 0, rp = 0, ri = 0, lignes = document.querySelectorAll('.on-journal tr[data-cle]');
  lignes.forEach(function(tr){
    var v = j[tr.getAttribute('data-cle')], r = parseFloat(tr.getAttribute('data-r'));
    tr.querySelectorAll('.on-jr button').forEach(function(b){ var on = b.getAttribute('data-v') === v; b.classList.toggle('on', on); b.setAttribute('aria-pressed', on ? 'true' : 'false'); });
    if (!v) return; n++;
    if (v === 'pris'){ np++; if (!isNaN(r)) rp += r; } else { ni++; if (!isNaN(r)) ri += r; }
  });
  var s = function(id, t){ var el = document.getElementById(id); if (el) el.textContent = t; };
  s('jr-n', n); s('jr-pris', np); s('jr-ign', ni);
  s('jr-pris-r', np ? nbFr(rp, 1) + ' R encaissés' : ''); s('jr-ign-r', ni ? nbFr(ri, 1) + ' R laissés de côté' : '');
  s('jr-taux', n ? Math.round(np / n * 100) + ' %' : '—');
}
document.addEventListener('click', function(ev){
  var b = ev.target.closest && ev.target.closest('.on-jr button'); if (!b) return;
  var tr = b.closest('tr'), cle = tr.getAttribute('data-cle'), j = lireJ(), v = b.getAttribute('data-v');
  if (j[cle] === v) delete j[cle]; else j[cle] = v;
  ecrireJ(j); majJournal();
});
majJournal(); setInterval(majJournal, 5000);
})();
"""
