# -*- coding: utf-8 -*-
"""
POSTE DE TRAVAIL PLEIN ÉCRAN : la Vue d'ensemble tient sur un seul écran
=======================================================================

Une grille fixe qui remplit exactement la fenêtre : aucun défilement de page, chaque panneau défile à l'intérieur.
Uniquement des données, pas de paragraphes : les explications sont dans les bulles « ? » et dans les autres écrans.

  Squawk          | Décision du plan        | Graphique 15 min du plan | Niveaux en direct
                  | Pourquoi l'or bouge     | Risque · Force · Fed     | Agenda · Sessions
  ---------------------------- barre d'état (données, contrat, marché) ----------------------------
"""

import html
import json
import math
from datetime import timedelta

import numpy as np

import plan as P

JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOT = {1: "ACHAT", -1: "VENTE", 0: "RIEN"}


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def panneau(cle, titre, corps, droite="", aide="", zone=True, cl=""):
    z = f' data-zone="ws-{cle}"' if zone else ""
    return (f'<section class="ws-p ws-{cle} {cl}" aria-label="{esc(titre)}"><header class="ws-h"><span class="ws-t">{esc(titre)}</span>'
            + (f'<span class="ws-i" tabindex="0" data-aide="{esc(aide)}">?</span>' if aide else "")
            + f'<span class="ws-d">{droite}</span></header><div class="ws-b"{z}>{corps}</div></section>')


# ---------------------------------------------------------------------------
# PANNEAUX
# ---------------------------------------------------------------------------

def p_decision(dz, statut, compte, note_ia):
    v, o = dz["verdict"], dz.get("ordre")
    m = dz.get("marche") or {}
    t = dz.get("tend") or {}
    sens = t.get("D", 0) if (t.get("D") and t.get("D") == t.get("H4") and t.get("H1") != -t.get("D")) else 0
    if o:
        mot, cl, sous = MOT[v], ("up" if v > 0 else "down"), {"attente": "ordre limite à placer", "en cours": "position en cours",
                                                             "tp1": "TP1 touché · stop à l'entrée"}.get(dz.get("etat"), "")
    elif not m.get("ouvert", True):
        mot, cl, sous = "FERMÉ", "flat", f"réouverture {JOURS[m['reouverture'].weekday()]} {m['reouverture']:%H:%M}" if m.get("reouverture") else "marché fermé"
    elif sens and not any(f[2] == "ko" for f in dz["filtres"]):
        mot, cl, sous = MOT[sens], ("up" if sens > 0 else "down"), "en préparation"
    else:
        mot, cl, sous = "RIEN", "flat", "pas de trade"
    demo = (f'<span class="ws-tag warn" title="Stratégie « {esc(statut["nom"])} » {esc(statut["statut"])} au backtest">DÉMO</span>'
            if statut and statut.get("statut") != "validée" else "")
    ico = {"ok": "✓", "ko": "✕", "wait": "·"}
    filtres = "".join(f'<li class="f-{s}"><i>{ico[s]}</i><b>{esc(n)}</b><span title="{esc(x)}">{esc(x)}</span></li>' for n, x, s in dz["filtres"])
    nb_ok = sum(1 for f in dz["filtres"] if f[2] == "ok")
    h = (f'<div class="dc-v {cl}"><b>{mot}</b><div><span>{esc(sous)}</span>{demo}<em>{nb_ok}/5 filtres</em></div></div>'
         f'<ul class="dc-f">{filtres}</ul>')
    if o:
        h += (f'<div class="dc-o"><div><span>Entrée</span><b>{fr(o["entree"])}</b></div><div><span>Stop</span><b class="down">{fr(o["stop"])}</b></div>'
              f'<div><span>TP1</span><b class="up">{fr(o["tp1"])}</b></div><div><span>TP2</span><b class="up">{fr(o["tp2"])}</b></div>'
              f'<div><span>Taille</span><b><span id="dz-lots" data-risque="{o["risque"]:.4f}">{fr(o["lots"])}</span> lot</b></div></div>'
              f'<p class="dc-dist" id="dz-dist" data-entree="{o["entree"]:.2f}" data-sens="{v}"></p>')
    else:
        deb = dz.get("debloque") or []
        deb = [deb] if isinstance(deb, str) else deb
        if deb:
            h += f'<div class="dc-deb"><span>Ce qui débloque</span><p>{esc(deb[0])}</p></div>'
    if note_ia:
        h += f'<div class="dc-ia"><span>Analyste IA</span><p>{esc(note_ia)}</p></div>'
    h += (f'<div class="ps-verrou" id="jr-verrou" hidden></div>'
          f'<div class="dc-j"><div><span id="jr-auj-k">Aujourd\'hui (plan)</span><b id="jr-auj-n" data-plan="{dz["nb_jour"]} / {P.MAX_TRADES_JOUR}">{dz["nb_jour"]} / {P.MAX_TRADES_JOUR}</b></div>'
          f'<div><span>Résultat</span><b id="jr-auj-r" data-plan="{fr(dz["r_jour"], 1, True)} R">{fr(dz["r_jour"], 1, True)} R</b></div>'
          f'<div><span>Risque</span><b>{fr(compte.get("risque_pct", 1), 1)} %</b></div><div><span>Arrêt</span><b>{fr(P.PERTE_MAX_JOUR_R, 0)} R</b></div></div>')
    return panneau("dec", "Décision du plan", h, f'bougie {dz["maj"]:%H:%M}', "Les 5 filtres de ton plan, dans l'ordre. Le verdict ne passe "
                   "en ACHAT ou VENTE que lorsque les cinq sont verts. Détail complet dans l'écran Technique.")


def p_graphique(dz, decision):
    t = dz.get("tech")
    if not t or t.get("erreur"):
        return panneau("gr", "XAU/USD · 15 min", '<p class="ws-vide">Graphique indisponible.</p>')
    zones = [z for z in t["zones"] if z["ut"] in ("H1", "H4", "M30")][:6]
    svg = decision.svg_bougies(t["m15"].tail(96), t["ajust"], decision.niveaux_graphique(dz), zones, w=760, h=470,
                               titre="Graphique 15 min avec les zones et les niveaux du plan")
    puces = "".join(f'<span class="{ {1: "up", -1: "down"}.get(l["tend"], "flat") }">{l["ut"]} {"▲" if l["tend"] > 0 else "▼" if l["tend"] < 0 else "–"}</span>'
                    for l in t["lignes"])
    mom = (f'<span>RSI 15 {fr(t.get("rsi15"), 0)}</span><span>RSI 1h {fr(t.get("rsi_h1"), 0)}</span><span>ATR 15 {fr(t.get("atr15"), 1)} $</span>')
    return panneau("gr", "XAU/USD · 15 min · plan", f'<div class="gr-svg">{svg}</div><div class="gr-pied"><div class="gr-tf">{puces}</div><div class="gr-m">{mom}</div></div>',
                   '<a href="#analyse" data-aller="technique">détail ›</a>',
                   "Les 24 dernières heures en bougies de 15 minutes, avec les zones OB / FVG du plan (vert : achat, rouge : vente), "
                   "les sommets et creux H1 / H4 à casser et, s'il existe, l'ordre du plan.")


def p_pourquoi(P_):
    if not P_:
        return panneau("pq", "Pourquoi l'or bouge", '<p class="ws-vide">Données insuffisantes.</p>')
    lig = ""
    for X in P_:
        cl = "flat" if X["calme"] else ("up" if X["sens"] > 0 else "down")
        causes = [x["txt"] for x in X["fonda"] if x["explique"]][:2] + [t[0] for t in X["tech"] if t[2] == X["sens"]][:1]
        if X.get("evts"):
            causes.append(X["evts"][0]["titre"])
        tete = X["titres"][0]["titre"] if X.get("titres") else ""
        lig += (f'<li class="{cl}{" fort" if X["fort"] else ""}"><div class="pq-l1"><time>{JOURS[X["date"].weekday()]} {X["date"]:%d/%m}</time>'
                f'<b>{fr(X["pct"], 2, True)} %</b><em>{fr(X["var"], 0, True)} $</em></div>'
                f'<div class="pq-l2">{"".join(f"<span>{esc(c)}</span>" for c in causes) or "<span>flux, pas de cause visible</span>"}</div>'
                + (f'<p>{esc(tete)}</p>' if tete else "") + f'<details><summary>explication</summary><p>{esc(X["clair"])}</p></details></li>')
    return panneau("pq", "Pourquoi l'or bouge", f'<ul class="pq-ws">{lig}</ul>', '<a href="#analyse" data-aller="pourquoi">détail ›</a>',
                   "Pour chaque séance : ce qui a bougé en même temps que l'or (dollar, taux, peur), l'annonce du jour, la technique et le titre "
                   "de presse qui donne une cause. Ce sont des causes probables, pas des certitudes.")


def p_marche(salle, y, f, fw):
    S = salle.sentiment(y, f)
    F = salle.force(y)
    onglets = '<div class="mk-o" role="tablist"><button type="button" data-mk="risque" class="on">Risque</button><button type="button" data-mk="force">Force</button><button type="button" data-mk="fed">Fed</button></div>'
    if S:
        cl = {"RISK-OFF": "down", "RISK-ON": "up", "NEUTRE": "neutre"}[S["etat"]]
        risque = (f'<div class="mk-r"><div class="mk-g">{salle.svg_jauge(S["score"], 220, 156)}<b class="sl-et {cl}">{S["etat"]}</b></div>'
                  f'<div class="mk-h">{salle.svg_histo(S["hist"], 460, 150)}</div></div>'
                  f'<div class="mk-c"><span class="mk-ct">Ce qui fait le score</span>'
                  + "".join(f'<div class="mk-cl"><span>{salle.esc(k)}</span><div class="mk-cb"><i class="{"up" if z > 0 else "down"}" '
                            f'style="{"left:50%" if z > 0 else "right:50%"};width:{min(50, abs(z) / 3 * 50):.0f}%"></i></div>'
                            f'<b class="{"up" if z > 0 else "down"}">{"+" if z > 0 else "−"}{abs(z):.1f}</b></div>'
                            for k, z in sorted(S["comp"].items(), key=lambda x: -abs(x[1])))
                  + f'<p>{salle.esc(S["lecture"])}</p></div>')
    else:
        risque = '<p class="ws-vide">Données insuffisantes.</p>'
    force = "".join(f'<div class="sl-fv" data-fen="{k}"{"" if k == "1m" else " hidden"}>{salle.svg_force(L, 640, 250)}</div>' for k, L in (F or {}).items())
    force = ((f'<div class="sl-btns">' + "".join(f'<button type="button" data-fen="{k}" class="{"on" if k == "1m" else ""}">{k.upper()}</button>' for k in (F or {}))
              + f'</div>{force}') if F else '<p class="ws-vide">Données insuffisantes.</p>')
    if fw and fw.get("reunions"):
        fed = salle.lignes_fed(fw)
    else:
        fed = '<p class="ws-vide">Futures fed funds indisponibles.</p>'
    corps = (f'{onglets}<div class="mk-v sl-p-nu" data-mk="risque">{risque}</div><div class="mk-v sl-p-nu" data-mk="force" hidden>{force}</div>'
             f'<div class="mk-v" data-mk="fed" hidden>{fed}</div>')
    droite = (f'<b class="sl-et {cl}">{S["score"]:.0f}</b>' if S else "")
    return panneau("mk", "Marchés · risque, force, Fed", corps, droite,
                   "Risque : score de 0 (peur) à 100 (appétit pour le risque) sur 7 marchés. Force : performance comparée de l'or et des marchés "
                   "qui le font bouger. Fed : probabilités de décision à chaque réunion.")


def p_agenda(cal, now):
    prochaine = next((e for e in cal or [] if e.get("impact") == "High" and e.get("date") and e["date"] >= now - timedelta(minutes=30)), None)
    h = ""
    if prochaine:
        d = prochaine["date"]
        h += (f'<div class="ag-n"><span>Prochaine annonce forte</span><b>{esc(prochaine["titre"])}</b>'
              f'<em>{JOURS[d.weekday()]} {d:%d/%m %H:%M} · prévu {esc(prochaine.get("prevision") or "—")} · précédent {esc(prochaine.get("precedent") or "—")}</em>'
              f'<i>pas d\'entrée {d - timedelta(minutes=P.NEWS_MIN):%H:%M} – {d + timedelta(minutes=P.NEWS_MIN):%H:%M}</i></div>')
    lst = [e for e in cal or [] if e.get("date") is not None and now - timedelta(hours=12) <= e["date"] <= now + timedelta(days=2)][:12]
    lig = "".join(f'<tr class="{"passe" if e["date"] <= now else ""}"><td>{JOURS[e["date"].weekday()]} {e["date"]:%H:%M}</td>'
                  f'<td><i class="{"fort" if e["impact"] == "High" else ""}"></i>{esc(e["titre"])}</td><td class="num">{esc(e.get("reel") or e.get("prevision") or "—")}</td></tr>'
                  for e in lst)
    h += f'<table class="ag-t">{lig}</table>' if lig else '<p class="ws-vide">Rien dans les deux prochains jours.</p>'
    h += ('<div class="mh" id="mh"><div class="mh-l" id="mh-l"></div><p class="sl-sous" id="mh-info"></p></div>')
    return panneau("ag", "Agenda · sessions", h, '<a href="#analyse" data-aller="annonces">agenda ›</a>',
                   "Annonces américaines d'impact moyen et fort (point doré : fort), puis les sessions de marché à l'heure de Paris.")


def barre_etat(sante_html):
    return f'<footer class="ws-etat" data-zone="ws-etat">{sante_html}<span class="ws-src">Données : Yahoo, FRED, CFTC, ForexFactory, Google Actualités · prix en direct XAUS</span></footer>'


def bloc(a, data, ajust, compte, statut, note_ia, echelle, sante_html, salle, decision, squawk_html, now):
    dz = a.get("decision")
    if not dz or dz.get("erreur"):
        return ""
    y, f = data["yahoo"], data["fred"]
    niv = panneau("niv", "Niveaux en direct", echelle, "", "Les niveaux du plan, de la séance et tes alertes, triés autour "
                  "du prix en direct. Une alerte sonne quand le prix en franchit un.", zone=False)
    return (f'<div class="ws" id="ws">'
            f'<div class="ws-c1"><div class="ws-sq" data-zone="ws-sq">{squawk_html}</div></div>'
            f'<div class="ws-c2">{p_decision(dz, statut, compte, note_ia)}{p_pourquoi(a.get("pourquoi"))}</div>'
            f'<div class="ws-c3">{p_graphique(dz, decision)}{p_marche(salle, y, f, a.get("fedwatch"))}</div>'
            f'<div class="ws-c4">{niv}{p_agenda(data.get("cal"), now)}</div>'
            f'</div>{barre_etat(sante_html)}')


CSS = """
/* Poste de travail plein écran */
#tab-accueil{padding-top:8px}
.ws{--hws:calc(100vh - var(--haut-barre,57px) - 58px - 44px);display:grid;gap:8px;height:var(--hws);min-height:560px;
  grid-template-columns:minmax(0,1fr) minmax(0,1.05fr) minmax(0,1.45fr) minmax(0,1fr);grid-template-rows:minmax(0,1fr)}
.ws>div{display:grid;gap:8px;min-height:0;min-width:0}
.ws-c1{grid-template-rows:minmax(0,1fr)} .ws-c2{grid-template-rows:minmax(0,1.05fr) minmax(0,1fr)}
.ws-c3{grid-template-rows:minmax(0,1.1fr) minmax(0,1fr)} .ws-c4{grid-template-rows:minmax(0,1.15fr) minmax(0,1fr)}
.ws-p{position:relative;display:flex;flex-direction:column;min-height:0;background:linear-gradient(180deg,#0d0f13,#090a0d);
  border:1px solid var(--line);border-radius:2px;padding:0;overflow:hidden}
.ws-p::before,.ws-p::after{content:"";position:absolute;width:10px;height:10px;border-color:var(--brass);border-style:solid;opacity:.75;pointer-events:none;z-index:2}
.ws-p::before{top:-1px;left:-1px;border-width:1px 0 0 1px} .ws-p::after{bottom:-1px;right:-1px;border-width:0 1px 1px 0}
.ws-h{flex:none;display:flex;align-items:center;gap:8px;padding:7px 10px;border-bottom:1px solid var(--band2);background:rgba(126,166,214,.03)}
.ws-t{font:700 11.5px var(--titre);letter-spacing:.16em;text-transform:uppercase;color:var(--brass);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ws-t::before{content:"◆ ";font-size:8px;vertical-align:2px}
.ws-i{flex:none;font:600 9.5px var(--mono);color:var(--mute);border:1px solid var(--line);border-radius:50%;width:14px;height:14px;display:inline-flex;align-items:center;justify-content:center;cursor:help;position:relative}
.ws-i:hover::after,.ws-i:focus::after{content:attr(data-aide);position:absolute;top:20px;left:-10px;z-index:30;width:260px;padding:8px 10px;background:#12151b;
  border:1px solid var(--brass);color:var(--ink);font:500 12px/1.45 var(--text);letter-spacing:0;text-transform:none;white-space:normal}
.ws-d{margin-left:auto;font:500 11px var(--mono);color:var(--mute);white-space:nowrap} .ws-d a{color:var(--brass);text-decoration:none}
.ws-b{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:8px 10px;scrollbar-width:thin;scrollbar-color:#2a2f3a transparent}
.ws-b::-webkit-scrollbar{width:6px} .ws-b::-webkit-scrollbar-thumb{background:#2a2f3a}
.ws-vide{color:var(--mute);font-size:12.5px}
/* squawk dans la grille */
.ws-sq .sq-f button{padding:2px 6px;font-size:10px}
.contenu:has(#tab-accueil:not([hidden]))>[data-zone="sources"]{display:none}
.contenu:has(#tab-accueil:not([hidden])){max-width:none}
.ws-sq{display:grid;grid-template-rows:minmax(0,1fr);min-height:0}
.ws-sq>.sl-p{position:relative;display:flex;flex-direction:column;min-height:0;background:linear-gradient(180deg,#0d0f13,#090a0d);border:1px solid var(--line);border-radius:2px;padding:0;overflow:hidden}
.ws-c1 .sl-h{flex:none;margin:0;padding:7px 10px;background:rgba(126,166,214,.03)} .ws-c1 .sq-f{flex:none;padding:6px 10px 0}
.ws-c1 .sq-l{flex:1;min-height:0;max-height:none;padding:0 10px} .ws-c1 .sl-squawk{min-height:0}
/* décision */
.dc-v{display:flex;align-items:center;gap:12px;margin-bottom:8px}
.dc-v>b{font:700 44px/1 var(--titre);letter-spacing:.02em} .dc-v.up>b{color:var(--up);text-shadow:0 0 18px rgba(47,210,125,.35)}
.dc-v.down>b{color:var(--down);text-shadow:0 0 18px rgba(255,90,95,.35)} .dc-v.flat>b{color:var(--steel)}
.dc-v div{display:flex;flex-direction:column;gap:2px;font-size:12px;color:var(--mute)} .dc-v em{font:600 11px var(--mono);font-style:normal;color:var(--brass)}
.ws-tag{align-self:flex-start;font:700 9.5px var(--mono);letter-spacing:.08em;border:1px solid currentColor;padding:1px 5px} .ws-tag.warn{color:var(--amber)}
.dc-f{list-style:none;margin:0 0 8px;padding:0;display:flex;flex-direction:column;gap:3px}
.dc-f li{display:grid;grid-template-columns:16px 82px minmax(0,1fr);align-items:center;gap:6px;font-size:12px;padding:3px 0;border-bottom:1px solid var(--band2)}
.dc-f i{font-style:normal;font:700 10px var(--mono);text-align:center} .dc-f b{font-weight:600}
.dc-f span{color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.dc-f .f-ok i{color:var(--up)} .dc-f .f-ko i,.dc-f .f-ko b{color:var(--down)} .dc-f .f-wait i,.dc-f .f-wait b{color:var(--mute)}
.dc-deb,.dc-ia{border-left:2px solid var(--brass);padding:4px 8px;margin-bottom:8px;background:rgba(126,166,214,.04)}
.dc-ia{border-left-color:#7c8cff;background:rgba(124,140,255,.05)}
.dc-deb span,.dc-ia span{font:700 9.5px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--brass)} .dc-ia span{color:#9aa6ff}
.dc-deb p,.dc-ia p{margin:2px 0 0;font-size:12.5px;line-height:1.45}
.dc-o{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:4px;margin-bottom:6px}
.dc-o div{display:flex;flex-direction:column;background:#101217;border:1px solid var(--band2);padding:4px 6px} .dc-o span{font-size:10px;color:var(--mute)} .dc-o b{font:700 13px var(--mono)}
.dc-dist{margin:0 0 6px;font:500 11.5px var(--mono);color:var(--mute)}
.dc-j{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:4px;border-top:1px solid var(--line);padding-top:6px}
.dc-j div{display:flex;flex-direction:column} .dc-j span{font:600 9.5px var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--mute)} .dc-j b{font:700 13px var(--mono)}
/* graphique */
.ws-gr .ws-b{display:flex;flex-direction:column;padding:6px 8px;overflow:hidden}
.gr-svg{flex:1;min-height:0;display:flex;overflow:hidden} .gr-svg svg{width:100%;height:100%;display:block;min-width:0!important}
.gr-pied{flex:none;display:flex;justify-content:space-between;gap:8px;padding-top:6px;font:600 11px var(--mono)}
.gr-tf,.gr-m{display:flex;gap:8px;flex-wrap:wrap} .gr-m{color:var(--mute)}
.gr-tf .up{color:var(--up)} .gr-tf .down{color:var(--down)} .gr-tf .flat{color:var(--mute)}
/* pourquoi */
.pq-ws{list-style:none;margin:0;padding:0;display:flex;flex-direction:column}
.pq-ws li{padding:7px 0;border-bottom:1px solid var(--band2)}
.pq-l1{display:flex;align-items:baseline;gap:8px} .pq-l1 time{font:600 11px var(--mono);color:var(--mute);width:72px;text-transform:uppercase}
.pq-l1 b{font:700 15px var(--mono)} .pq-l1 em{font:500 11px var(--mono);font-style:normal;color:var(--mute)}
.pq-ws li.up .pq-l1 b{color:var(--up)} .pq-ws li.down .pq-l1 b{color:var(--down)} .pq-ws li.flat .pq-l1 b{color:var(--steel)}
.pq-ws li.fort{border-left:2px solid currentColor;padding-left:6px} .pq-ws li.fort.up{color:var(--up)} .pq-ws li.fort.down{color:var(--down)}
.pq-ws li.fort>*{color:var(--ink)} .pq-ws li.fort .pq-l1 b{color:inherit}
.pq-l2{display:flex;flex-wrap:wrap;gap:4px;margin-top:4px} .pq-l2 span{font:600 10.5px var(--text);border:1px solid var(--line);padding:1px 6px;color:var(--steel)}
.pq-ws p{margin:4px 0 0;font-size:12px;line-height:1.4;color:var(--mute)}
.pq-ws details summary{font:600 10.5px var(--mono);color:var(--brass);cursor:pointer;margin-top:3px;list-style:none} .pq-ws details summary::before{content:"› "}
.pq-ws details p{color:var(--ink)}
/* marchés */
.mk-o{display:flex;gap:3px;margin-bottom:8px} .mk-o button{font:700 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;padding:3px 10px;border-radius:2px;background:transparent;border:1px solid var(--line);color:var(--mute)}
.mk-o button.on{background:var(--brass);border-color:var(--brass);color:#000000}
.mk-r{display:grid;grid-template-columns:minmax(120px,36%) minmax(0,1fr);gap:10px;align-items:center}
.mk-g{display:flex;flex-direction:column;align-items:center} .mk-g svg{width:100%;max-width:190px;height:auto}
.ws-mk .sl-btns{margin-bottom:6px}
.mk-c{margin-top:10px;border-top:1px solid var(--band2);padding-top:8px}
.mk-ct{display:block;font:700 9.5px var(--mono);letter-spacing:.12em;text-transform:uppercase;color:var(--mute);margin-bottom:5px}
.mk-cl{display:grid;grid-template-columns:150px minmax(0,1fr) 38px;align-items:center;gap:8px;font-size:11.5px;padding:2px 0}
.mk-cl span{color:var(--steel);white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .mk-cl b{font:600 11px var(--mono);text-align:right}
.mk-cb{position:relative;height:6px;background:#15181e} .mk-cb::after{content:"";position:absolute;left:50%;top:-2px;bottom:-2px;border-left:1px solid #3a404c}
.mk-cb i{position:absolute;top:0;bottom:0} .mk-cb i.up{background:var(--up)} .mk-cb i.down{background:var(--down)}
.mk-c p{margin:8px 0 0;font-size:12px;line-height:1.45;color:var(--mute)}
/* agenda */
.ag-n{border:1px solid var(--line);background:#101217;padding:7px 9px;margin-bottom:8px;display:flex;flex-direction:column;gap:2px}
.ag-n span{font:700 9.5px var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--brass)} .ag-n b{font:700 14px var(--text)}
.ag-n em{font-style:normal;font-size:11.5px;color:var(--mute)} .ag-n i{font-style:normal;font:600 11px var(--mono);color:var(--amber)}
.ag-t{width:100%;border-collapse:collapse;font-size:11.5px;margin-bottom:10px} .ag-t td{padding:4px 2px;border-bottom:1px solid var(--band2)}
.ag-t td:first-child{font:600 10.5px var(--mono);color:var(--mute);white-space:nowrap;width:70px} .ag-t td.num{text-align:right;font-family:var(--mono)}
.ag-t i{display:inline-block;width:6px;height:6px;border-radius:50%;background:#2a2f3a;margin-right:6px;vertical-align:1px} .ag-t i.fort{background:var(--brass)}
.ag-t tr.passe td{color:#5c606b}
/* niveaux */
.ws-niv .dx-ech{border:0;background:none;padding:0} .ws-niv .dx-opts button{white-space:normal;text-align:left} .ws-niv .dx-ech>.ps-tete{display:none}
.ws-niv .dx-ech::before,.ws-niv .dx-ech::after{display:none}
/* barre d'état */
.ws-etat{display:flex;align-items:center;gap:14px;margin-top:8px;padding:6px 10px;border:1px solid var(--line);background:#090a0d;font:500 11px var(--mono);color:var(--mute);min-height:30px;overflow:hidden}
.ws-etat .dx-sante{display:flex;gap:16px;border:0;background:none;padding:0;margin:0;font:500 11px var(--mono);flex-wrap:nowrap;overflow:hidden;white-space:nowrap;min-width:0;flex:1}
.ws-etat .dx-sante span{min-width:0;overflow:hidden;text-overflow:ellipsis;flex-shrink:1} .ws-etat .dx-sante i{flex:none}
.ws-src{margin-left:auto;white-space:nowrap}
/* écrans plus petits : la grille repasse en colonnes qui défilent avec la page */
@media (max-width:1440px){.ws{grid-template-columns:minmax(0,1fr) minmax(0,1fr) minmax(0,1.3fr) minmax(0,.95fr)}
  .dc-v>b{font-size:36px}.dc-f li{grid-template-columns:16px 74px minmax(0,1fr)}}
@media (max-width:1100px),(max-height:620px){.ws{height:auto;grid-template-columns:minmax(0,1fr) minmax(0,1fr);grid-template-rows:auto}
  .ws>div{grid-template-rows:auto} .ws-p{height:auto;max-height:640px} .ws-gr{height:460px!important;max-height:none!important}
  .ws-c1{grid-column:1/-1} .ws-sq>.sl-p{max-height:420px}}
@media (max-width:760px){.ws{grid-template-columns:minmax(0,1fr)} .mk-r{grid-template-columns:minmax(0,1fr)} .ws-gr{height:360px!important}
  .ws-etat{flex-wrap:wrap}.ws-etat .dx-sante{flex-wrap:wrap;white-space:normal}.ws-src{margin-left:0}}
"""

JS = r"""
(function(){
document.addEventListener('click', function(ev){
  var b = ev.target.closest && ev.target.closest('.mk-o button'); if (!b) return;
  var k = b.getAttribute('data-mk'), p = b.closest('.ws-p');
  p.querySelectorAll('.mk-o button').forEach(function(x){ x.classList.toggle('on', x === b); });
  p.querySelectorAll('.mk-v').forEach(function(v){ v.hidden = v.getAttribute('data-mk') !== k; });
});
})();
"""
