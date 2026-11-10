# -*- coding: utf-8 -*-
"""
COUCHE TEMPS RÉEL : ce qui vit dans le navigateur entre deux passages du robot
=============================================================================

Le robot recalcule l'analyse toutes les 15 minutes ; entre-temps le prix bouge. Cette couche utilise le prix en
direct (rafraîchi toutes les 20 secondes) pour :
  - l'échelle des niveaux : ordre du plan, zones, sommets et creux à casser, niveaux de séance, triés autour du
    prix avec l'écart en direct ;
  - les alertes : son, notification et bandeau quand le prix franchit un niveau important, plus tes propres
    alertes de prix (commande « AL 4100 ») ;
  - la fourchette du jour en direct ;
  - le calculateur de position (commande « CALC ») qui contrôle un trade contre les règles du plan ;
  - l'aide des commandes (« AIDE »).
Rien ici ne décide à la place du plan : la cassure d'un niveau ne compte qu'à la clôture de la bougie.
"""

import html
import json
import math

import pandas as pd

import plan as P

JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOIS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."]


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−")


# ---------------------------------------------------------------------------
# NIVEAUX ET CONTEXTE TRANSMIS À LA PAGE
# ---------------------------------------------------------------------------

def niveaux(a, dz, ajust):
    """Niveaux utiles autour du prix : [{v, lib, t, al}] ; al = 1 si le franchissement mérite une alerte."""
    t = dz.get("tech") or {}
    px = dz["px"]
    nv = []
    o = dz.get("ordre")
    if o:
        attente = dz.get("etat") == "attente"
        nv.append({"v": o["entree"], "lib": "Entrée du plan", "t": "ent", "al": 1 if attente else 0})
        nv.append({"v": o["stop"], "lib": "Stop du plan", "t": "sl", "al": 1})
        nv.append({"v": o["tp1"], "lib": "TP1 du plan (2 R)", "t": "tp", "al": 0 if dz.get("etat") == "tp1" else 1})
        nv.append({"v": o["tp2"], "lib": "TP2 du plan", "t": "tp", "al": 1})
    zs = t.get("zones") or []
    for sens, typ in ((1, "za"), (-1, "zv")):
        for z in [z for z in zs if z["sens"] == sens][:3]:
            nv.append({"v": z["niveau"], "lib": f"Zone {'achat' if sens > 0 else 'vente'} {z['type']} {z['ut']}",
                       "t": typ, "al": 1, "z": [z["bas"], z["haut"]], "plan": bool(z.get("plan"))})
    for l in t.get("lignes", []):
        if l["ut"] not in ("D", "H4", "H1", "M15"):
            continue
        nom = {"D": "Daily", "H4": "H4", "H1": "H1", "M15": "M15"}[l["ut"]]
        for cle, lib in (("sommet", "Sommet"), ("creux", "Creux")):
            x = l[cle]
            if x and not x["casse"]:
                nv.append({"v": x["prix"], "lib": f"{lib} {nom} à casser", "t": "str", "al": 0 if l["ut"] == "M15" else 1})
    s = a.get("seance") or {}
    for cle, lib, al in (("pdh", "Plus haut de la veille", 1), ("pdl", "Plus bas de la veille", 1), ("pdc", "Clôture de la veille", 0),
                         ("haut_sem", "Plus haut de la semaine", 1), ("bas_sem", "Plus bas de la semaine", 1),
                         ("vwap", "VWAP du jour", 0)):
        if s.get(cle) is not None:
            nv.append({"v": s[cle] + ajust, "lib": lib, "t": "sea", "al": al})
    for x in s.get("seances") or []:
        if x.get("haut") is not None and not x.get("en_cours"):
            nv.append({"v": x["haut"] + ajust, "lib": f"Plus haut {x['nom']}", "t": "sea", "al": 1})
            nv.append({"v": x["bas"] + ajust, "lib": f"Plus bas {x['nom']}", "t": "sea", "al": 1})
    pv = s.get("profil_veille")
    if pv and pv.get("poc") is not None:
        nv.append({"v": pv["poc"] + ajust, "lib": "POC de la veille (prix le plus traité)", "t": "ref", "al": 0})
    pas = 50 if px > 2000 else 10
    for k in (math.floor(px / pas), math.floor(px / pas) + 1):
        nv.append({"v": float(k * pas), "lib": "Chiffre rond", "t": "ref", "al": 0})
    portee = 8 * (t.get("atr_h1") or 50)
    vus, propres = set(), []
    ordre_t = {"ent": 0, "sl": 0, "tp": 0, "za": 1, "zv": 1, "str": 2, "sea": 3, "ref": 4}
    for n in sorted(nv, key=lambda n: ordre_t[n["t"]]):          # à prix égal, le niveau le plus important gagne
        cle = round(n["v"], 1)
        if cle in vus or not math.isfinite(n["v"]) or (abs(n["v"] - px) > portee and n["t"] not in ("ent", "sl", "tp")):
            continue
        vus.add(cle)
        n["v"] = round(float(n["v"]), 2)
        if "z" in n:
            n["z"] = [round(float(n["z"][0]), 2), round(float(n["z"][1]), 2)]
        propres.append(n)
    return sorted(propres, key=lambda n: -n["v"])


def contexte(a, dz, ajust, compte):
    """Tout ce dont la page a besoin entre deux analyses (JSON)."""
    t = dz.get("tech") or {}
    s = a.get("seance") or {}
    td = dz.get("tend") or {}
    sens = td.get("D", 0) if (td.get("D") and td.get("D") == td.get("H4") and td.get("H1") != -td.get("D")) else 0
    o = dz.get("ordre")
    f = lambda x: None if x is None or not math.isfinite(float(x)) else round(float(x), 2)
    ctx = {
        "maj": dz["maj"].isoformat() if dz.get("maj") is not None else None,
        "px": f(dz.get("px")), "atr15": f(t.get("atr15")), "atr_h1": f(t.get("atr_h1")), "adr": f(s.get("atr")),
        "jour": ({"date": str(s.get("date")), "haut": f(s["haut"] + ajust), "bas": f(s["bas"] + ajust),
                  "pdc": f(s["pdc"] + ajust) if s.get("pdc") is not None else None} if s.get("haut") is not None else None),
        "sens": int(sens), "tend": {k: int(v) for k, v in td.items()}, "fond": int(dz.get("fond") or 0),
        "ordre": ({"sens": int(o["sens"]), "entree": f(o["entree"]), "stop": f(o["stop"]), "tp1": f(o["tp1"]), "tp2": f(o["tp2"]),
                   "etat": dz.get("etat"), "expire": P.depuis_horloge(o["expire"]).isoformat()} if o else None),
        "niv": niveaux(a, dz, ajust),
        "zones": [{"sens": z["sens"], "bas": f(z["bas"]), "haut": f(z["haut"]), "ut": z["ut"], "type": z["type"]}
                  for z in (t.get("zones") or [])],
        "regles": {"max_trades": P.MAX_TRADES_JOUR, "perte_max_r": P.PERTE_MAX_JOUR_R, "news_min": P.NEWS_MIN,
                   "tp1_r": P.TP1_R, "risque_pct": compte.get("risque_pct", P.RISQUE_PCT)},
        "retard": bool(dz.get("en_retard")),
    }
    return json.dumps(ctx, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------------------
# BLOCS HTML
# ---------------------------------------------------------------------------

def carte_echelle():
    """Carte « Niveaux en direct » : remplie par le navigateur, hors des zones rafraîchies par le robot."""
    return ('<div class="ps-carte dx-ech" id="dx-ech"><div class="ps-tete"><span class="k">Niveaux en direct</span>'
            '<span class="ps-mini" id="dx-etat">écart au prix</span></div>'
            '<table class="ps-niv dx-niv" id="dx-niv"><tbody><tr><td class="ps-mini">Chargement du prix en direct…</td></tr></tbody></table>'
            '<form class="dx-al" id="dx-al-form" autocomplete="off"><label for="dx-al-prix" class="k">Alerte de prix</label>'
            '<div><input id="dx-al-prix" inputmode="decimal" placeholder="ex. 4 100" aria-label="Prix de l\'alerte">'
            '<button type="submit">Créer</button></div></form>'
            '<ul class="dx-al-liste" id="dx-al-liste"></ul>'
            '<div class="dx-opts"><button type="button" id="dx-son">Son : coupé</button>'
            '<button type="button" id="dx-notif">Notifications : désactivées</button></div>'
            '<div class="dx-fil-b"><span class="k">Dernières alertes</span><ul class="dx-fil" id="dx-fil">'
            '<li class="ps-mini">Aucune alerte pour l\'instant.</li></ul></div></div>')


def sante(dz, a, now):
    """Bandeau « état des données » : fraîcheur des bougies, contrat suivi, conversion future -> spot."""
    it = []
    retard = dz.get("retard_min")
    m = dz.get("marche") or {}
    if not m.get("ouvert", True):
        r = m.get("reouverture")
        quand = f"{JOURS[r.weekday()]} {r.day} {MOIS[r.month - 1]} à {r:%H:%M}" if r is not None else "bientôt"
        it.append(("warn", f"Marché fermé ({m.get('raison')}) · réouverture {quand}"))
    elif dz.get("en_retard"):
        it.append(("down", f"Bougies en retard de {retard:.0f} min : vérifie ton graphique avant d'agir"))
    else:
        it.append(("up", f"Bougies à jour · dernière clôture 15 min à {dz['maj']:%H:%M}"))
    c = dz.get("contrat") or {}
    if c.get("suspect") is not None:
        it.append(("down", "Changement de contrat du future en cours : niveaux à vérifier sur ton graphique"))
    if c.get("libelle"):
        txt = f"Future suivi : {c['libelle']}"
        ch = c.get("changement")
        if ch is not None and c.get("suivant"):
            jours = (ch - now.date()).days
            if -2 <= jours <= 21:
                ec = c.get("ecart_suivant")
                txt += (f" · passage à {c['suivant']} vers le {ch.day} {MOIS[ch.month - 1]}"
                        + (f" (saut de {fr(ec, 0, True)} $ corrigé automatiquement)" if ec is not None else ""))
        it.append(("", txt))
    elif c.get("erreur"):
        it.append(("warn", f"Contrat du future : {c['erreur']}"))
    if a.get("base") is not None:
        it.append(("", f"Future − spot : {fr(a['base'], 1, True)} $ ({'mesuré' if (a.get('ecart') or {}).get('mesure') else 'estimé'}), "
                       f"tous les niveaux sont en prix spot"))
    return ('<div class="dx-sante" data-zone="dx-sante">'
            + "".join(f'<span class="{cl}"><i></i>{esc(tx)}</span>' for cl, tx in it)
            + '<span id="dx-src"></span></div>')


def dialogues():
    """Calculateur de position et aide : fenêtres ouvertes par commande, hors des zones rafraîchies."""
    codes = [("GO", "Vue d'ensemble : décision, niveaux, agenda"), ("MKT", "Tous les marchés"), ("DATA", "Positionnement, volatilité, ratios"),
             ("TECH", "Structure, zones, graphiques du plan"), ("FA", "Fondamental"), ("ECO", "Agenda économique"),
             ("NEWS", "Actualités"), ("STAT", "Statistiques de séance"), ("LAB", "Stratégies testées"), ("JRN", "Journal de trading")]
    cmds = [("AL 4100", "Crée une alerte de prix à 4 100"), ("CALC", "Ouvre le calculateur de position"),
            ("CALC A 4061 4052 4079", "Achat à 4 061, stop 4 052, objectif 4 079 (V pour une vente)"), ("AIDE", "Cette fenêtre")]
    touches = [("/", "Aller à la barre de commande"), ("1 à 9, 0", "Changer d'écran"), ("C", "Calculateur"), ("?", "Aide"), ("Échap", "Fermer")]
    lig = lambda l: "".join(f'<tr><td><kbd>{esc(k)}</kbd></td><td>{esc(v)}</td></tr>' for k, v in l)
    return ('<dialog id="dx-calc" class="dx-dlg" aria-labelledby="dx-calc-t"><div class="dx-dlg-tete"><h2 id="dx-calc-t">Calculateur de position</h2>'
            '<button type="button" class="dx-x" data-fermer aria-label="Fermer">Fermer</button></div>'
            '<p class="dx-dlg-sous">Pour un trade que tu prends toi-même : la taille selon ton risque, et le contrôle contre les règles de ton plan.</p>'
            '<div class="dx-sens" role="group" aria-label="Sens"><button type="button" data-sens="1" class="achat">Achat</button>'
            '<button type="button" data-sens="-1" class="vente">Vente</button></div>'
            '<div class="dx-champs"><label>Entrée<input id="dx-c-entree" inputmode="decimal" autocomplete="off"></label>'
            '<label>Stop<input id="dx-c-stop" inputmode="decimal" autocomplete="off"></label>'
            '<label>Objectif<input id="dx-c-cible" inputmode="decimal" autocomplete="off" placeholder="facultatif"></label>'
            '<label>Risque, % du compte<input id="dx-c-risque" inputmode="decimal" autocomplete="off"></label></div>'
            '<div class="dx-c-actions"><button type="button" id="dx-c-prix">Entrée = prix en direct</button>'
            '<button type="button" id="dx-c-plan">Reprendre l\'ordre du plan</button></div>'
            '<div id="dx-c-res" aria-live="polite"></div></dialog>'
            '<dialog id="dx-aide" class="dx-dlg" aria-labelledby="dx-aide-t"><div class="dx-dlg-tete"><h2 id="dx-aide-t">Commandes du terminal</h2>'
            '<button type="button" class="dx-x" data-fermer aria-label="Fermer">Fermer</button></div>'
            '<p class="dx-dlg-sous">Tape un code dans la barre « Fonction » (touche /) puis Entrée.</p>'
            f'<div class="dx-aide-g"><div><span class="k">Écrans</span><table>{lig(codes)}</table></div>'
            f'<div><span class="k">Commandes</span><table>{lig(cmds)}</table><span class="k dx-k2">Clavier</span><table>{lig(touches)}</table></div></div>'
            '</dialog>')


CSS = """
/* Couche temps réel */
.ps-zone{display:contents}
.dx-sante{display:flex;flex-wrap:wrap;gap:4px 18px;margin:0 0 10px;padding:7px 12px;border:1px solid var(--line);border-radius:8px;
  background:var(--panel);font-size:12px;color:var(--mute);line-height:1.5}
.dx-sante span{display:inline-flex;align-items:center;gap:6px;min-width:0}
.dx-sante i{flex:none;width:7px;height:7px;border-radius:50%;background:#55555e}
.dx-sante .up{color:var(--ink)} .dx-sante .up i{background:var(--up)}
.dx-sante .down{color:var(--down);font-weight:600} .dx-sante .down i{background:var(--down)}
.dx-sante .warn{color:var(--amber)} .dx-sante .warn i{background:var(--amber)}
#dx-src:empty{display:none}
.dx-niv td{padding:5px 0}
.dx-niv td:first-child{padding-right:6px}
.dx-niv td small{display:block;font:500 11px var(--mono);color:var(--mute)}
.dx-niv tr.dx-ent td:first-child{color:var(--brass);font-weight:600} .dx-niv tr.dx-sl td:first-child{color:var(--down);font-weight:600}
.dx-niv tr.dx-tp td:first-child{color:var(--up);font-weight:600}
.dx-niv tr.dx-za td:first-child{color:var(--up)} .dx-niv tr.dx-zv td:first-child{color:var(--down)}
.dx-niv tr.dx-str td:first-child{color:#8fa8d4} .dx-niv tr.dx-ref td{color:var(--mute)}
.dx-niv tr.dx-perso td:first-child{color:var(--amber);font-weight:600}
.dx-niv tr.dx-proche td{background:rgba(255,159,26,.09)}
.dx-niv tr.ps-px td{color:var(--brass);font-weight:700;border-top:1px solid var(--brass);border-bottom:1px solid var(--brass)}
.dx-niv tr.ps-px small{display:inline;color:var(--mute);font-weight:500;margin-left:6px}
.dx-niv .dx-suppr{border:0;background:none;color:var(--mute);padding:0 4px;font-size:13px;line-height:1;cursor:pointer}
.dx-niv .dx-suppr:hover{color:var(--down);background:none}
.dx-al{margin-top:10px;padding-top:10px;border-top:1px solid var(--line);display:flex;flex-direction:column;gap:5px}
.dx-al div{display:flex;gap:6px}
.dx-al input{flex:1;min-width:0;background:#000;border:1px solid var(--line);border-radius:6px;color:var(--ink);font:500 13px var(--mono);padding:7px 9px}
.dx-al input:focus{outline:none;border-color:var(--brass);box-shadow:0 0 0 2px rgba(255,159,26,.25)}
.dx-al input.err{border-color:var(--down)}
.dx-al button{flex:none}
.dx-al-liste{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:6px}
.dx-al-liste:empty{display:none}
.dx-al-liste li{display:inline-flex;align-items:center;gap:4px;font:600 12px var(--mono);color:var(--amber);border:1px solid rgba(255,179,64,.45);
  border-radius:6px;padding:2px 4px 2px 8px}
.dx-al-liste button{border:0;background:none;color:var(--mute);padding:0 4px;font-size:13px}
.dx-opts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.dx-opts button{font-size:12px;padding:5px 9px}
.dx-opts button.on{border-color:rgba(47,210,125,.55);color:var(--up)}
.dx-fil-b{margin-top:10px;padding-top:8px;border-top:1px solid var(--line)}
.dx-fil{list-style:none;margin:4px 0 0;padding:0;display:flex;flex-direction:column;gap:5px;max-height:150px;overflow-y:auto}
.dx-fil li{font-size:12.5px;line-height:1.4} .dx-fil time{font:500 11.5px var(--mono);color:var(--mute);margin-right:6px}
.dx-fil li.fort{color:var(--amber)}
#dx-toasts{position:fixed;right:14px;bottom:14px;z-index:90;display:flex;flex-direction:column;gap:8px;max-width:min(420px,calc(100vw - 28px))}
.dx-toast{background:#111116;border:1px solid var(--line);border-left:3px solid var(--brass);border-radius:8px;padding:10px 12px;
  box-shadow:0 8px 24px rgba(0,0,0,.6);font-size:13.5px;line-height:1.4;cursor:pointer}
.dx-toast.fort{border-left-color:var(--amber);background:#1a1408}
.dx-toast b{display:block;font:700 10.5px var(--text);letter-spacing:.08em;text-transform:uppercase;color:var(--brass);margin-bottom:2px}
.dx-dlg{width:min(680px,calc(100vw - 24px));max-height:calc(100vh - 40px);overflow:auto;background:var(--panel);color:var(--ink);
  border:1px solid var(--line);border-radius:12px;padding:18px 20px}
.dx-dlg::backdrop{background:rgba(0,0,0,.7)}
.dx-dlg-tete{display:flex;align-items:center;justify-content:space-between;gap:12px}
.dx-dlg h2{font:700 18px var(--text);margin:0}
.dx-dlg-sous{margin:4px 0 14px;font-size:13px;color:var(--mute);line-height:1.5}
.dx-sens{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:12px}
.dx-sens button{min-height:42px;font:700 14px var(--text)}
.dx-sens button.achat.on{background:var(--up);border-color:var(--up);color:#06120b}
.dx-sens button.vente.on{background:var(--down);border-color:var(--down);color:#160606}
.dx-champs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}
.dx-champs label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--mute)}
.dx-champs input{background:#000;border:1px solid var(--line);border-radius:6px;color:var(--ink);font:600 15px var(--mono);padding:9px 10px;min-width:0;width:100%}
.dx-champs input:focus{outline:none;border-color:var(--brass);box-shadow:0 0 0 2px rgba(255,159,26,.25)}
.dx-c-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 14px}
.dx-c-actions button{font-size:12.5px}
.dx-kpis{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;margin-bottom:12px}
.dx-kpis div{background:var(--band);border-radius:8px;padding:9px 10px;display:flex;flex-direction:column;gap:2px;min-width:0}
.dx-kpis span{font-size:11.5px;color:var(--mute)} .dx-kpis b{font:700 17px var(--mono);letter-spacing:-.02em}
.dx-kpis small{font-size:11.5px;color:var(--mute)}
.dx-verdict{font:800 15px var(--text);letter-spacing:.04em;padding:9px 12px;border-radius:8px;margin-bottom:10px;border:1px solid currentColor}
.dx-verdict.ok{color:var(--up);background:rgba(47,210,125,.08)} .dx-verdict.att{color:var(--amber);background:rgba(255,179,64,.08)}
.dx-verdict.non{color:var(--down);background:rgba(255,90,95,.08)}
.dx-verdict small{display:block;font:500 12.5px var(--text);letter-spacing:0;color:var(--ink);margin-top:2px}
.dx-ctl{list-style:none;margin:0 0 12px;padding:0;display:flex;flex-direction:column;gap:6px}
.dx-ctl li{display:grid;grid-template-columns:20px minmax(0,1fr);gap:8px;font-size:13.5px;line-height:1.4}
.dx-ctl i{font-style:normal;width:20px;height:20px;border-radius:5px;display:flex;align-items:center;justify-content:center;font:700 11px var(--text)}
.dx-ctl .ok i{color:var(--up);background:rgba(47,210,125,.15)} .dx-ctl .att i{color:var(--amber);background:rgba(255,179,64,.15)}
.dx-ctl .non i{color:var(--down);background:rgba(255,90,95,.17)} .dx-ctl .non{color:var(--down)}
.dx-fin{display:flex;flex-wrap:wrap;gap:8px}
.dx-x{font-size:12.5px}
.dx-aide-g{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.dx-aide-g table{width:100%;border-collapse:collapse;margin-top:6px;font-size:13px}
.dx-aide-g td{padding:5px 0;border-bottom:1px solid var(--band2);vertical-align:top}
.dx-aide-g td:first-child{white-space:nowrap;padding-right:12px}
.dx-aide-g kbd{font:600 11.5px var(--mono);color:var(--brass);background:rgba(255,159,26,.1);border:1px solid rgba(255,159,26,.35);border-radius:4px;padding:1px 6px}
.dx-k2{display:block;margin-top:14px}
.dx-outils{display:flex;gap:6px}
@media (max-width:640px){.dx-champs,.dx-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.dx-aide-g{grid-template-columns:1fr}
  .dx-dlg{padding:14px}}
"""

JS = r"""
(function(){
var T = window.terminalOr || (window.terminalOr = {});
function $(s){ return document.querySelector(s); }
function nb(x, d){ return (x == null || isNaN(x)) ? '—' : Number(x).toLocaleString('fr-FR', {minimumFractionDigits: d, maximumFractionDigits: d}); }
function sg(x, d){ return (x > 0 ? '+' : (x < 0 ? '−' : '')) + nb(Math.abs(x), d); }
function esc(t){ return String(t == null ? '' : t).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
function lire(k, d){ try { var v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch(e){ return d; } }
function ecrire(k, v){ try { localStorage.setItem(k, JSON.stringify(v)); } catch(e){} }
function nombre(txt){ var v = parseFloat(String(txt == null ? '' : txt).replace(/[\s  ]/g, '').replace(',', '.')); return isNaN(v) ? null : v; }
function hm(ts){ var d = new Date(ts); return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2); }

/* Contexte envoyé par le robot (relu quand une nouvelle analyse arrive) */
var D = {}, brut = null;
function charger(){
  var el = document.getElementById('direct-ctx'); if (!el) return false;
  if (el.textContent === brut) return false;
  brut = el.textContent; try { D = JSON.parse(brut) || {}; } catch(e){ D = {}; }
  return true;
}
function px(){ var p = T.px && T.px(); return (p && p.prix > 0) ? p : null; }
function regles(){
  var c = (T.ctx && T.ctx().compte) || {}, P = {};
  Object.keys(c).forEach(function(k){ P[k] = c[k]; });
  var u = lire('reglages-or', {}) || {};
  Object.keys(u).forEach(function(k){ if (u[k] !== '' && u[k] != null && !isNaN(+u[k])) P[k] = +u[k]; });
  return P;
}
T.regles = regles; T.direct = function(){ return D; };

/* ---------- Alertes ---------- */
var AL = lire('alertes-prix-or', []) || [], FIL = lire('fil-alertes-or', []) || [], VUES = lire('alertes-niv-or', {}) || {};
function toasts(){
  var c = document.getElementById('dx-toasts');
  if (!c){ c = document.createElement('div'); c.id = 'dx-toasts'; c.setAttribute('role', 'status'); c.setAttribute('aria-live', 'polite'); document.body.appendChild(c); }
  return c;
}
function toast(txt, fort, titre){
  var t = document.createElement('div'); t.className = 'dx-toast' + (fort ? ' fort' : '');
  t.innerHTML = '<b>' + esc(titre || 'Alerte') + ' · ' + hm(Date.now()) + '</b>' + esc(txt);
  t.addEventListener('click', function(){ t.remove(); });
  toasts().appendChild(t);
  setTimeout(function(){ t.remove(); }, fort ? 45000 : 15000);
}
T.toast = toast;
function declencher(txt, fort){
  toast(txt, fort);
  if (T.bip) T.bip(fort ? 3 : 1, fort ? 1175 : 880);
  if ('Notification' in window && Notification.permission === 'granted'){
    try { new Notification('Terminal or', {body: txt, tag: txt.slice(0, 40)}); } catch(e){}
  }
  FIL.unshift({t: Date.now(), txt: txt, fort: fort ? 1 : 0}); FIL = FIL.slice(0, 30); ecrire('fil-alertes-or', FIL); fil();
}
function fil(){
  var el = $('#dx-fil'); if (!el) return;
  var rec = FIL.filter(function(x){ return Date.now() - x.t < 36 * 3600000; }).slice(0, 8);
  el.innerHTML = rec.length ? rec.map(function(x){ return '<li class="' + (x.fort ? 'fort' : '') + '"><time>' + hm(x.t) + '</time>' + esc(x.txt) + '</li>'; }).join('')
                            : '<li class="ps-mini">Aucune alerte pour l’instant.</li>';
}
function message(n, monte){
  var v = nb(n.v, 2), sens = monte ? 'à la hausse' : 'à la baisse';
  if (n.t === 'ent') return 'Entrée du plan touchée à ' + v + ' : vérifie que ton ordre est exécuté.';
  if (n.t === 'sl') return 'Stop du plan touché à ' + v + '.';
  if (n.t === 'tp') return n.lib + ' touché à ' + v + (n.lib.indexOf('TP1') === 0 ? ' : encaisse la moitié, stop au point d’entrée.' : '.');
  if (n.t === 'za' || n.t === 'zv') return 'Prix dans la ' + n.lib.toLowerCase() + ' (' + v + ')' + (n.plan ? ' : zone du plan, guette la cassure M15.' : '.');
  if (n.t === 'str') return n.lib.replace(' à casser', '') + ' franchi ' + sens + ' en direct (' + v + ') : la cassure ne compte qu’à la clôture de la bougie.';
  return n.lib + ' franchi ' + sens + ' (' + v + ').';
}
var precedent = null;                                   /* dernier prix en direct traité */
function croisements(p){
  var av = precedent; precedent = {prix: p.prix, t: Date.now(), live: p.live};
  if (!av || !p.live || !av.live || Date.now() - av.t > 180000 || av.prix === p.prix) return;
  if (T.ouvert && !T.ouvert()) return;
  var lo = Math.min(av.prix, p.prix), hi = Math.max(av.prix, p.prix), monte = p.prix > av.prix, now = Date.now();
  AL.slice().forEach(function(a){
    if (a.v >= lo && a.v <= hi){
      AL = AL.filter(function(x){ return x.id !== a.id; }); ecrire('alertes-prix-or', AL);
      declencher('Ton alerte de prix à ' + nb(a.v, 2) + ' est touchée (prix ' + nb(p.prix, 2) + ').', true);
    }
  });
  (D.niv || []).forEach(function(n){
    if (!n.al || !(n.v >= lo && n.v <= hi)) return;
    var cle = n.t + '|' + n.v.toFixed(1);
    if (VUES[cle] && now - VUES[cle] < 45 * 60000) return;
    VUES[cle] = now; declencher(message(n, monte), n.t === 'ent' || n.t === 'sl' || n.t === 'tp');
  });
  Object.keys(VUES).forEach(function(k){ if (now - VUES[k] > 86400000) delete VUES[k]; });
  ecrire('alertes-niv-or', VUES);
}
function ajouterAlerte(v){
  var p = px();
  if (v == null || !(v > 0) || (p && Math.abs(v / p.prix - 1) > 0.15)) return false;
  AL.push({id: Date.now() + '-' + Math.round(Math.random() * 1e6), v: Math.round(v * 100) / 100, cree: Date.now()});
  ecrire('alertes-prix-or', AL); listeAlertes(); echelle(true);
  toast('Alerte créée à ' + nb(v, 2) + (p ? ' (' + sg(v - p.prix, 1) + ' $ du prix). ' : '. ') + 'Elle sonne si cette page reste ouverte.', false, 'Alerte de prix');
  return true;
}
function supprimerAlerte(id){ AL = AL.filter(function(x){ return x.id !== id; }); ecrire('alertes-prix-or', AL); listeAlertes(); echelle(true); }
function listeAlertes(){
  var el = $('#dx-al-liste'); if (!el) return;
  el.innerHTML = AL.slice().sort(function(a, b){ return b.v - a.v; }).map(function(a){
    return '<li>' + nb(a.v, 2) + '<button type="button" data-suppr="' + esc(a.id) + '" aria-label="Supprimer l’alerte à ' + nb(a.v, 2) + '">✕</button></li>'; }).join('');
}
document.addEventListener('click', function(ev){
  var b = ev.target.closest && ev.target.closest('[data-suppr]'); if (b) supprimerAlerte(b.getAttribute('data-suppr'));
});
document.addEventListener('submit', function(ev){
  if (!ev.target || ev.target.id !== 'dx-al-form') return;
  ev.preventDefault();
  var i = $('#dx-al-prix'), ok = ajouterAlerte(nombre(i.value));
  i.classList.toggle('err', !ok); if (ok) i.value = '';
});

/* Son et notifications : mêmes réglages que le bouton de la barre du haut */
function options(){
  var bs = $('#dx-son'), bn = $('#dx-notif'), src = document.getElementById('btn-son');
  if (bs && src){ var on = /activé/.test(src.textContent); bs.textContent = on ? 'Son : activé' : (/réactiver/.test(src.textContent) ? 'Son : clique pour réactiver' : 'Son : coupé'); bs.classList.toggle('on', on); }
  if (bn){
    if (!('Notification' in window)){ bn.textContent = 'Notifications : non disponibles ici'; bn.disabled = true; }
    else { var p = Notification.permission; bn.textContent = 'Notifications : ' + (p === 'granted' ? 'activées' : (p === 'denied' ? 'bloquées par le navigateur' : 'activer')); bn.classList.toggle('on', p === 'granted'); }
  }
}
document.addEventListener('click', function(ev){
  if (!ev.target) return;
  if (ev.target.id === 'dx-son'){ var s = document.getElementById('btn-son'); if (s) s.click(); setTimeout(options, 150); }
  if (ev.target.id === 'dx-notif' && 'Notification' in window && Notification.permission === 'default'){
    Notification.requestPermission().then(options, options);
  }
});

/* ---------- Fourchette du jour en direct ---------- */
var ext = {date: null, haut: null, bas: null};
function jour(p){
  var J = D.jour; if (!J) return null;
  if (ext.date !== J.date){ ext = {date: J.date, haut: J.haut, bas: J.bas}; }
  ext.haut = Math.max(ext.haut, J.haut); ext.bas = Math.min(ext.bas, J.bas);
  if (p && p.live && (!T.ouvert || T.ouvert())){ ext.haut = Math.max(ext.haut, p.prix); ext.bas = Math.min(ext.bas, p.prix); }
  return ext;
}
function seance(p){
  var J = jour(p); if (!J || !p) return;
  var a = $('#dx-jb'), b = $('#dx-jh'), i = $('#dx-jpos'), pc = $('#dx-jpct'), v = $('#dx-jvar');
  if (a) a.textContent = nb(J.bas, 1); if (b) b.textContent = nb(J.haut, 1);
  if (i && J.haut > J.bas) i.style.left = Math.max(0, Math.min(100, (p.prix - J.bas) / (J.haut - J.bas) * 100)).toFixed(0) + '%';
  if (pc && D.adr) pc.textContent = nb((J.haut - J.bas) / D.adr * 100, 0);
  if (v && D.jour.pdc){ var ch = p.prix - D.jour.pdc; v.textContent = sg(ch, 2) + ' (' + sg(ch / D.jour.pdc * 100, 2) + ' %)'; v.className = ch > 0 ? 'up' : (ch < 0 ? 'down' : ''); }
}

/* ---------- Échelle des niveaux ---------- */
var derniereEchelle = '';
function echelle(force){
  var el = $('#dx-niv'), p = px(); if (!el) return;
  if (!p){ el.innerHTML = '<tbody><tr><td class="ps-mini">Prix en direct indisponible.</td></tr></tbody>'; return; }
  var J = jour(p), niv = (D.niv || []).slice();
  if (J){ niv.push({v: J.haut, lib: 'Plus haut du jour', t: 'sea'}); niv.push({v: J.bas, lib: 'Plus bas du jour', t: 'sea'}); }
  AL.forEach(function(a){ niv.push({v: a.v, lib: 'Ton alerte', t: 'perso', id: a.id}); });
  niv.sort(function(a, b){ return b.v - a.v; });
  var atr = D.atr_h1 || 0, ordre = {ent: 0, sl: 0, tp: 0, perso: 0, za: 1, zv: 1, str: 2, sea: 3, ref: 4};
  function choisir(l, n){               /* les plus proches, les niveaux du plan et les alertes toujours gardés */
    var imp = l.filter(function(x){ return ordre[x.t] === 0; }), reste = l.filter(function(x){ return ordre[x.t] !== 0; });
    reste.sort(function(a, b){ return Math.abs(a.v - p.prix) - Math.abs(b.v - p.prix); });
    return imp.concat(reste.slice(0, Math.max(0, n - imp.length))).sort(function(a, b){ return b.v - a.v; });
  }
  var haut = choisir(niv.filter(function(n){ return n.v > p.prix; }), 6), bas = choisir(niv.filter(function(n){ return n.v <= p.prix; }), 6);
  function ligne(n){
    var d = n.v - p.prix, proche = atr && Math.abs(d) <= 0.25 * atr;
    return '<tr class="dx-' + n.t + (proche ? ' dx-proche' : '') + '"><td>' + esc(n.lib) +
      (n.z ? '<small>' + nb(n.z[0], 1) + ' – ' + nb(n.z[1], 1) + '</small>' : '') + '</td><td class="num">' + nb(n.v, 1) +
      '</td><td class="num ' + (d > 0 ? 'up' : 'down') + '">' + sg(d, 1) + '</td><td>' +
      (n.t === 'perso' ? '<button type="button" class="dx-suppr" data-suppr="' + esc(n.id) + '" aria-label="Supprimer cette alerte">✕</button>' : '') + '</td></tr>';
  }
  var age = p.t ? Math.max(0, Math.round((Date.now() - p.t) / 1000)) : null;
  var etat = !p.live ? 'différé' : ((T.ouvert && !T.ouvert()) ? 'marché fermé' : 'en direct');
  var h = '<tbody>' + haut.map(ligne).join('') + '<tr class="ps-px"><td>Prix<small>' + etat + '</small></td><td class="num">' + nb(p.prix, 2) +
          '</td><td></td><td></td></tr>' + bas.map(ligne).join('') + '</tbody>';
  if (force || h !== derniereEchelle){ el.innerHTML = h; derniereEchelle = h; }
  var e = $('#dx-etat');
  if (e) e.textContent = !p.live ? 'prix différé : alertes en pause' : (age != null && age < 600 ? 'prix il y a ' + age + ' s' : 'écart au prix');
  var s = $('#dx-src'); if (s) s.textContent = p.live ? 'Prix en direct : ' + (p.src || 'XAUS') : '';
}

/* ---------- Calculateur de position ---------- */
var calc = {sens: 0};
function ouvrir(id){ var d = document.getElementById(id); if (!d) return null; if (d.showModal){ if (!d.open) d.showModal(); } else d.setAttribute('open', ''); return d; }
function fermer(d){ if (d.close) d.close(); else d.removeAttribute('open'); }
document.addEventListener('click', function(ev){
  var b = ev.target.closest && ev.target.closest('[data-fermer]'); if (b){ var d = b.closest('dialog'); if (d) fermer(d); }
  var o = ev.target.closest && ev.target.closest('[data-ouvrir]');
  if (o){ ev.preventDefault(); if (o.getAttribute('data-ouvrir') === 'calc') ouvrirCalc(); else ouvrir('dx-aide'); }
});
function champ(id, v, d){ var el = document.getElementById(id); if (el && v != null) el.value = String(Number(v).toFixed(d == null ? 2 : d)).replace('.', ','); }
function setSens(s){ calc.sens = s; document.querySelectorAll('#dx-calc .dx-sens button').forEach(function(b){ b.classList.toggle('on', +b.getAttribute('data-sens') === s); }); }
function ouvrirCalc(args){
  var d = ouvrir('dx-calc'); if (!d) return;
  var P = regles(), p = px();
  if (!$('#dx-c-risque').value) champ('dx-c-risque', P.risque_pct != null ? P.risque_pct : 1, 1);
  if (args && args.length){
    var s = /^(A|ACHAT|BUY|L|LONG)$/.test(args[0]) ? 1 : (/^(V|VENTE|SELL|S|SHORT)$/.test(args[0]) ? -1 : 0), n = args.slice(s ? 1 : 0).map(nombre);
    if (s) setSens(s);
    if (n[0] != null) champ('dx-c-entree', n[0]); if (n[1] != null) champ('dx-c-stop', n[1]); if (n[2] != null) champ('dx-c-cible', n[2]);
  } else if (!$('#dx-c-entree').value && p) champ('dx-c-entree', p.prix);
  calculer();
}
T.calc = ouvrirCalc;
function calculer(){
  var res = $('#dx-c-res'); if (!res) return;
  var e = nombre($('#dx-c-entree').value), st = nombre($('#dx-c-stop').value), ci = nombre($('#dx-c-cible').value), rq = nombre($('#dx-c-risque').value);
  var s = calc.sens, P = regles(), R = D.regles || {}, p = px();
  if (!s){ res.innerHTML = '<p class="dx-dlg-sous">Choisis Achat ou Vente.</p>'; return; }
  if (e == null || st == null){ res.innerHTML = '<p class="dx-dlg-sous">Indique l’entrée et le stop : le terminal calcule la taille et contrôle le trade.</p>'; return; }
  var dist = (e - st) * s, sp = P.spread || 0, opl = P.once_par_lot || 100, cap = P.capital || 0;
  if (!(dist > 0)){ res.innerHTML = '<div class="dx-verdict non">STOP DU MAUVAIS CÔTÉ<small>Pour ' + (s > 0 ? 'un achat le stop est sous' : 'une vente le stop est au-dessus de') + ' l’entrée.</small></div>'; return; }
  var pct = rq != null && rq > 0 ? rq : (P.risque_pct || 1), risque = cap * pct / 100, cout = (dist + sp) * opl;
  var lots = Math.floor(risque / cout * 100) / 100, reel = lots * cout;
  var gain = ci != null ? (ci - e) * s : null, rr = gain != null ? (gain - sp) / (dist + sp) : null;
  var C = [];
  function aj(n, t){ C.push([n, t]); }
  /* 1. marché et annonces */
  if (T.ouvert && !T.ouvert()) aj('non', 'Marché fermé.');
  var ev = T.prochain && T.prochain(), nm = R.news_min || 30;
  if (ev){ var m = (ev.ts - Date.now()) / 60000;
    if (Math.abs(m) <= nm) aj('non', 'Zone interdite : ' + ev.nom + (m >= 0 ? ' dans ' + Math.ceil(m) + ' min' : ' publiée il y a ' + Math.floor(-m) + ' min') + ' (pas d’entrée à ±' + nm + ' min).');
    else if (m > 0 && m <= 120) aj('att', ev.nom + ' dans ' + Math.round(m) + ' min : le trade devra être géré avant.');
    else aj('ok', 'Aucune annonce forte à ±' + nm + ' min.'); }
  else aj('ok', 'Aucune annonce forte proche.');
  /* 2. sens du plan */
  var mot = s > 0 ? 'achat' : 'vente';
  if (D.sens === s) aj('ok', 'Dans le sens du plan : Daily et H4 ' + (s > 0 ? 'haussiers' : 'baissiers') + ', le H1 ne contredit pas.');
  else if (D.sens === -s) aj('non', 'Contre le plan : il ne cherche que des ' + (s > 0 ? 'ventes' : 'achats') + ' en ce moment.');
  else aj('att', 'Le plan n’a pas de sens en ce moment (Daily, H4 et H1 pas alignés) : ' + mot + ' hors plan.');
  if (D.fond === -s) aj('non', 'Fondamental du plan contre ' + (s > 0 ? 'un achat' : 'une vente') + ' (bloc Fed, dollar, taux).');
  /* 3. zone */
  var tolz = (D.atr15 || 2) * 0.25, dz = (D.zones || []).filter(function(z){ return z.sens === s && e >= z.bas - tolz && e <= z.haut + tolz; })[0];
  if (dz) aj('ok', 'Entrée dans une zone ' + dz.type + ' ' + dz.ut + ' (' + nb(dz.bas, 1) + ' – ' + nb(dz.haut, 1) + ').');
  else aj('att', 'Entrée hors de toute zone OB / FVG intacte dans ce sens : le plan n’entre que sur un retour en zone.');
  /* 4. stop */
  if (D.atr15 && dist < 0.5 * D.atr15) aj('att', 'Stop très serré : ' + nb(dist, 1) + ' $, moins de la moitié d’une bougie 15 min moyenne (' + nb(D.atr15, 1) + ' $).');
  else if (D.atr_h1 && dist > 3 * D.atr_h1) aj('att', 'Stop très large : ' + nb(dist, 1) + ' $, plus de 3 bougies d’une heure moyennes.');
  else aj('ok', 'Stop à ' + nb(dist, 1) + ' $' + (D.atr15 ? ' (' + nb(dist / D.atr15, 1) + ' fois la bougie 15 min moyenne).' : '.'));
  /* 5. rapport gain / risque */
  if (gain != null && !(gain > 0)) aj('non', 'Objectif du mauvais côté de l’entrée.');
  else if (rr != null && rr < (R.tp1_r || 2)) aj('att', 'Gain / risque de ' + nb(rr, 2) + ' : le plan vise au moins ' + nb(R.tp1_r || 2, 0) + ' R au premier objectif.');
  else if (rr != null) aj('ok', 'Gain / risque de ' + nb(rr, 2) + ', spread compris.');
  /* 6. taille et limites du jour */
  if (lots < 0.01) aj('non', 'Stop trop large pour ton risque : même 0,01 lot dépasse ' + nb(risque, 0) + ' $.');
  if (pct > (R.risque_pct || 1) + 1e-9) aj('att', 'Risque de ' + nb(pct, 1) + ' % : ton plan dit ' + nb(R.risque_pct || 1, 1) + ' % par trade.');
  var jr = T.journal && T.journal.jour ? T.journal.jour() : null;
  if (jr && (jr.n >= (R.max_trades || 2) || jr.r <= (R.perte_max_r || -2))) aj('non', 'Limite du jour atteinte dans ton journal (' + jr.n + ' trade' + (jr.n > 1 ? 's' : '') + ', ' + sg(jr.r, 1) + ' R) : plus de trade aujourd’hui.');
  else if (jr && jr.n) aj('ok', 'Journal du jour : ' + jr.n + ' trade sur ' + (R.max_trades || 2) + ', ' + sg(jr.r, 1) + ' R.');
  if (D.retard) aj('att', 'Bougies du terminal en retard : niveaux peut-être dépassés.');
  var n = {ok: 0, att: 0, non: 0}; C.forEach(function(x){ n[x[0]]++; });
  var v = n.non ? ['non', 'HORS PLAN', n.non + ' règle' + (n.non > 1 ? 's' : '') + ' du plan interdi' + (n.non > 1 ? 'sent' : 't') + ' ce trade.']
        : (n.att ? ['att', 'À VÉRIFIER', n.att + ' point' + (n.att > 1 ? 's' : '') + ' à regarder avant d’entrer.'] : ['ok', 'CONFORME AU PLAN', 'Toutes les règles vérifiables sont respectées.']);
  var ic = {ok: '✓', att: '!', non: '✗'};
  var texte = (s > 0 ? 'ACHAT' : 'VENTE') + ' XAU/USD ' + nb(e, 2) + ' | SL ' + nb(st, 2) + (ci != null ? ' | TP ' + nb(ci, 2) : '') + ' | ' + nb(Math.max(lots, 0), 2) + ' lot';
  res.innerHTML = '<div class="dx-kpis"><div><span>Taille</span><b>' + nb(Math.max(lots, 0), 2) + ' lot</b><small>' + nb(Math.max(lots, 0) * opl, 0) + ' onces</small></div>' +
    '<div><span>Risque réel</span><b>' + nb(reel, 0) + ' $</b><small>' + nb(cap ? reel / cap * 100 : 0, 2) + ' % du compte</small></div>' +
    '<div><span>Distance du stop</span><b>' + nb(dist, 1) + ' $</b><small>spread ' + nb(sp, 2) + ' $ compté</small></div>' +
    '<div><span>Gain / risque</span><b>' + (rr != null ? nb(rr, 2) : '—') + '</b><small>' + (gain != null && lots > 0 ? 'gain visé ' + nb(lots * (gain - sp) * opl, 0) + ' $' : 'sans objectif') + '</small></div></div>' +
    '<div class="dx-verdict ' + v[0] + '">' + v[1] + '<small>' + v[2] + '</small></div>' +
    '<ul class="dx-ctl">' + ['non', 'att', 'ok'].map(function(g){ return C.filter(function(x){ return x[0] === g; }).map(function(x){
      return '<li class="' + g + '"><i>' + ic[g] + '</i><span>' + esc(x[1]) + '</span></li>'; }).join(''); }).join('') + '</ul>' +
    '<div class="dx-fin"><button type="button" class="or" id="dx-c-copier" data-texte="' + esc(texte) + '">Copier l’ordre</button>' +
    (T.journal && T.journal.preparer ? '<button type="button" id="dx-c-journal">Noter dans le journal</button>' : '') + '</div>' +
    '<p class="dx-dlg-sous" style="margin:10px 0 0">Compte de ' + nb(cap, 0) + ' $, ' + nb(opl, 0) + ' onces par lot (bouton Réglages). Contrôle du contexte, pas un conseil.</p>';
  calc.dernier = {sens: s, entree: e, stop: st, cible: ci, lots: Math.max(lots, 0), verdict: v[1]};
}
document.addEventListener('click', function(ev){
  var t = ev.target; if (!t || !t.closest) return;
  var bs = t.closest('#dx-calc .dx-sens button'); if (bs){ setSens(+bs.getAttribute('data-sens')); calculer(); }
  if (t.id === 'dx-c-prix'){ var p = px(); if (p){ champ('dx-c-entree', p.prix); calculer(); } }
  if (t.id === 'dx-c-plan'){
    var o = D.ordre;
    if (!o){ toast('Le plan n’a pas d’ordre en ce moment.', false, 'Calculateur'); return; }
    setSens(o.sens); champ('dx-c-entree', o.entree); champ('dx-c-stop', o.stop); champ('dx-c-cible', o.tp1); calculer();
  }
  if (t.id === 'dx-c-copier'){
    var tx = t.getAttribute('data-texte'), ok = function(){ t.textContent = 'Copié'; setTimeout(function(){ t.textContent = 'Copier l’ordre'; }, 1500); };
    if (navigator.clipboard && window.isSecureContext) navigator.clipboard.writeText(tx).then(ok, function(){}); else ok();
  }
  if (t.id === 'dx-c-journal' && calc.dernier && T.journal && T.journal.preparer){
    fermer(document.getElementById('dx-calc')); T.journal.preparer(calc.dernier);
  }
});
document.addEventListener('input', function(ev){ if (ev.target && ev.target.closest && ev.target.closest('#dx-calc')) calculer(); });

/* ---------- Commandes ---------- */
T.commande = function(txt){
  var c = String(txt || '').trim().toUpperCase().replace(/\s+/g, ' '), m;
  if (!c) return false;
  if ((m = c.match(/^(AL|ALERTE|ALERT)\s+(.+)$/))) return ajouterAlerte(nombre(m[2]));
  if (/^(AL|ALERTE|ALERT)$/.test(c)){ var b = document.querySelector('.onglets [data-tab="accueil"]'); if (b) b.click();
    var i = $('#dx-al-prix'); if (i){ i.scrollIntoView({block: 'center'}); setTimeout(function(){ i.focus(); }, 50); } return true; }
  if ((m = c.match(/^CALC(?:\s+(.+))?$/))){ ouvrirCalc(m[1] ? m[1].split(' ') : null); return true; }
  if (/^(AIDE|HELP|\?|H)$/.test(c)){ ouvrir('dx-aide'); return true; }
  return false;
};
document.addEventListener('keydown', function(ev){
  var tg = ev.target.tagName || ''; if (tg === 'INPUT' || tg === 'TEXTAREA' || tg === 'SELECT' || ev.metaKey || ev.ctrlKey || ev.altKey) return;
  if (document.querySelector('dialog[open]')) return;
  if (ev.key === 'c' || ev.key === 'C'){ ev.preventDefault(); ouvrirCalc(); }
  if (ev.key === '?'){ ev.preventDefault(); ouvrir('dx-aide'); }
});

/* ---------- Boucle ---------- */
var dernierPrix = null;
function tour(){
  var nouveau = charger(), p = px();
  if (p && (dernierPrix !== p.prix || nouveau)){ if (p.live) croisements(p); dernierPrix = p.prix; }
  seance(p); echelle(nouveau); options();
}
charger(); listeAlertes(); fil(); tour(); setInterval(tour, 1000);
})();
"""
