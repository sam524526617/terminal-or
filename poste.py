# -*- coding: utf-8 -*-
"""
POSTE DE PILOTAGE : tout ce qu'il faut pour décider, sur le premier écran, du plus important au moins important
================================================================================================================

Colonne 1 · Décision    : verdict, pourquoi, ordre complet (ou ce qui débloque), bilan du jour, statut de la stratégie.
Colonne 2 · Contexte    : les 5 filtres, la tendance par unité de temps, le fondamental du plan, momentum et volatilité.
Colonne 3 · À surveiller: prochaine annonce forte et sa fenêtre interdite, niveaux clés autour du prix,
                          marchés qui font bouger l'or, dernières actualités.
"""

import html
import re
from datetime import timedelta

import numpy as np
import pandas as pd

import plan as P

FLECHE = {1: "▲", -1: "▼", 0: "–"}
JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]
MOT = {1: "ACHAT", -1: "VENTE", 0: "RIEN"}
COUL = {1: "up", -1: "down", 0: "flat"}
ETIQ = {"hawk": ("Fed dure", "down"), "dove": ("Fed souple", "up"), "esc": ("escalade", "up"), "desc": ("détente", "down")}


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def serie(s):
    if s is None:
        return None
    s = pd.to_numeric(s, errors="coerce").dropna()
    return s if len(s) > 1 else None


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
# COLONNE 1 : DÉCISION
# ---------------------------------------------------------------------------

def col_decision(dz, statut, compte):
    v, o = dz["verdict"], dz.get("ordre")
    cls = {1: "ps-achat", -1: "ps-vente", 0: "ps-rien"}[v]
    demo = ""
    if statut and statut.get("statut") != "validée":
        demo = (f'<span class="ps-demo" title="Stratégie « {esc(statut["nom"])} » {esc(statut["statut"])} au backtest">'
                f'DÉMO · stratégie {esc(statut["statut"])}</span>')
    tete = (f'<div class="ps-tete"><span class="k">Décision</span>{demo}'
            f'<span class="ps-maj">bougie 15 min de {dz["maj"]:%H:%M}</span></div>')
    avert = ""
    if dz.get("en_retard"):
        avert += (f'<p class="ps-avert">Bougies en retard de {dz.get("retard_min", 0):.0f} min : cette décision date. '
                  f'Vérifie ton graphique avant d\'agir.</p>')
    if (dz.get("contrat") or {}).get("suspect") is not None:
        avert += ('<p class="ps-avert">Changement de contrat du future en cours : un saut artificiel peut fausser les '
                  'niveaux pendant quelques minutes. Vérifie sur ton graphique.</p>')
    tete += avert
    m = dz.get("marche") or {}
    ferme = not m.get("ouvert", True) and not (o and dz.get("etat") in ("en cours", "tp1"))
    if ferme:
        r = m.get("reouverture")
        quand = f"{JOURS[r.weekday()]} {r:%d/%m} à {r:%H:%M}" if r is not None else "bientôt"
        t = dz.get("tend") or {}
        sens = t.get("D", 0) if (t.get("D") and t.get("D") == t.get("H4") and t.get("H1") != -t.get("D")) else 0
        suite = (f"À la réouverture, le plan ne cherchera que des {'achats' if sens > 0 else 'ventes'} "
                 f"(Daily et H4 {'haussiers' if sens > 0 else 'baissiers'})." if sens else
                 "À la réouverture, le plan n'a pas de sens tant que Daily, H4 et H1 ne sont pas alignés.")
        deb = dz.get("debloque") or []
        deb = [deb] if isinstance(deb, str) else deb
        cls = "ps-rien"
        corps = (f'<div class="ps-verdict"><b>FERMÉ</b><div><span class="ps-note">Marché fermé</span>'
                 f'<small>{esc(m.get("raison") or "")}</small></div></div>'
                 f'<p class="ps-sous">Réouverture <b>{esc(quand)}</b> (heure de Paris). Aucun ordre d\'ici là : '
                 f'un ordre laissé pendant la fermeture peut être exécuté loin de son prix.</p>'
                 f'<p class="ps-sous">{esc(suite)}</p>'
                 + (f'<div class="ps-deb"><span class="k">À surveiller à la réouverture</span><ol>{"".join(f"<li>{esc(x)}</li>" for x in deb)}</ol></div>'
                    if deb and not o else ""))
    elif o:
        etat = {"attente": f"Ordre {('achat' if v > 0 else 'vente')} limite à placer",
                "en cours": "Position du plan en cours", "tp1": "TP1 touché : stop au point d'entrée"}.get(dz["etat"], "")
        corps = (f'<div class="ps-verdict"><b>{MOT[v]}</b><div><span class="ps-note">Note {esc(dz["note"])}</span>'
                 f'<small>{esc(etat)}</small><small>Zone {esc(o["zone"])} + cassure M15 + FVG</small></div></div>')
        lignes = [("Entrée", fr(o["entree"]), "brass"), ("Stop", f'{fr(o["stop"])} <em>({fr(o["risque"])} $)</em>', "down"),
                  ("TP1 · 50 %", f'{fr(o["tp1"])} <em>2 R</em>', "up"),
                  ("TP2 · " + ("liquidité H1" if o["tp2_liq"] else "3 R"), fr(o["tp2"]), "up"),
                  ("Taille", f'<span id="dz-lots" data-risque="{o["risque"]:.4f}">{fr(o["lots"])}</span> lot', ""),
                  ("Risque", f'<span id="dz-risque">{fr(o["lots"] * o["risque"] * 100, 0)} $</span>', "")]
        texte = (f"{'ACHAT' if v > 0 else 'VENTE'} LIMITE XAU/USD {fr(o['entree'])} | SL {fr(o['stop'])} | "
                 f"TP1 {fr(o['tp1'])} (50 %) | TP2 {fr(o['tp2'])}")
        rangs = "".join('<tr><td>' + esc(k) + '</td><td class="num ' + c + '">' + val + '</td></tr>' for k, val, c in lignes)
        corps += (f'<table class="ps-ticket">{rangs}</table>'
                  f'<p class="ps-dist" id="dz-dist" data-entree="{o["entree"]:.2f}" data-sens="{v}"></p>'
                  f'<p class="ps-inval">Annulé si pas exécuté avant {P.depuis_horloge(o["expire"]):%H:%M} · invalidé par une clôture M15 '
                  f'{"sous" if v > 0 else "au-dessus de"} {fr(o["stop"])}</p>'
                  f'<button type="button" class="dz-copier ps-copier {cls}" data-texte="{esc(texte)}">Copier l\'ordre</button>')
    else:
        ko = [f for f in dz["filtres"] if f[2] == "ko"]
        deb = dz.get("debloque") or []
        if isinstance(deb, str):
            deb = [deb]
        t = dz.get("tend") or {}
        sens = t.get("D", 0) if (t.get("D") and t.get("D") == t.get("H4") and t.get("H1") != -t.get("D")) else 0
        nb_ok = sum(1 for f in dz["filtres"] if f[2] == "ok")
        jauge = "".join(f'<i class="{"on" if k < nb_ok else ""}"></i>' for k in range(5))
        if not ko and sens:
            # Les filtres de fond sont validés : on attend la zone et / ou le déclencheur dans un sens connu
            mot = "ACHAT" if sens > 0 else "VENTE"
            cls = "ps-prep-a" if sens > 0 else "ps-prep-v"
            corps = (f'<div class="ps-verdict"><b>{"ACHAT" if sens > 0 else "VENTE"}<span class="ps-prep">en préparation</span></b></div>'
                     f'<p class="ps-sous">Tendance et fondamental validés : on ne cherche <b>que des {mot.lower()}s</b>. '
                     f'Pas d\'ordre tant que les étapes ci-dessous ne sont pas faites.</p>'
                     f'<div class="ps-etapes"><div class="ps-jauge" title="{nb_ok} filtres sur 5">{jauge}<span>{nb_ok} / 5</span></div>'
                     f'<span class="k">À surveiller, dans l\'ordre</span><ol>{"".join(f"<li>{esc(x)}</li>" for x in deb) or "<li>Attendre la cassure M15.</li>"}</ol></div>')
        else:
            bloq = ko or [f for f in dz["filtres"] if f[2] == "wait"][:1]
            pourquoi = (" · ".join(f"{f[0]} : {f[1]}" for f in bloq) if bloq else "Tous les filtres sont verts : en attente du signal.")
            corps = (f'<div class="ps-verdict"><b>RIEN</b><div><span class="ps-note">Pas de trade</span>'
                     f'<small>Le contexte interdit d\'entrer</small></div></div>'
                     f'<div class="ps-jauge" title="{nb_ok} filtres sur 5">{jauge}<span>{nb_ok} / 5</span></div>'
                     f'<p class="ps-pourquoi"><span class="k">Bloqué par</span>{esc(pourquoi)}</p>'
                     f'<div class="ps-deb"><span class="k">Ce qui débloque{" (tout est nécessaire)" if len(deb) > 1 else ""}</span>'
                     f'<ol>{"".join(f"<li>{esc(x)}</li>" for x in deb) or "<li>—</li>"}</ol></div>')
        trades = ((dz.get("histo") or {}).get("trades") or [])
        if trades:
            der = P.depuis_horloge(trades[0]["t_sig"])
            age = (dz["maj"] - der).days if dz.get("maj") is not None else None
            corps += (f'<p class="ps-freq">Ce plan donne en moyenne un signal tous les {max(1, round(60 / max(1, len(trades))))} jours '
                      f'({len(trades)} sur les 60 derniers jours) · dernier signal : {der:%d/%m}'
                      + (f', il y a {age} jour{"s" if age > 1 else ""}' if age else "") + '.</p>')
    jour = (f'<div class="ps-verrou" id="jr-verrou" hidden></div>'
            f'<div class="ps-jour"><div><span class="k" id="jr-auj-k">Aujourd\'hui (plan)</span><b id="jr-auj-n" data-plan="{dz["nb_jour"]} / {P.MAX_TRADES_JOUR} trades">{dz["nb_jour"]} / {P.MAX_TRADES_JOUR} trades</b></div>'
            f'<div><span class="k">Résultat</span><b id="jr-auj-r" class="{COUL[int(np.sign(round(dz["r_jour"], 2)))]}" data-plan="{fr(dz["r_jour"], 1, True)} R">{fr(dz["r_jour"], 1, True)} R</b></div>'
            f'<div><span class="k">Risque / trade</span><b>{fr(compte.get("risque_pct", 1), 1)} %</b></div>'
            f'<div><span class="k">Arrêt du jour</span><b>{fr(P.PERTE_MAX_JOUR_R, 0)} R</b></div></div>')
    return f'<div class="ps-carte ps-dec {cls}">{tete}{corps}{jour}</div>'


# ---------------------------------------------------------------------------
# COLONNE 2 : CONTEXTE
# ---------------------------------------------------------------------------

def col_contexte(dz, fonda):
    ico = {"ok": "✓", "ko": "✗", "wait": "–"}
    filtres = "".join(f'<li class="ps-{s}"><i>{ico[s]}</i><b>{esc(t)}</b><span title="{esc(x)}">{esc(x)}</span></li>'
                      for t, x, s in dz["filtres"])
    nb_ok = sum(1 for f in dz["filtres"] if f[2] == "ok")
    t = dz.get("tech") or {}
    tend = {l["ut"]: l["tend"] for l in t.get("lignes", [])}
    puces = "".join(f'<div class="ps-tf {COUL[tend.get(k, 0)]}"><span>{k}</span><b>{FLECHE[tend.get(k, 0)]}</b></div>'
                    for k in ("W", "D", "H4", "H1", "M15"))
    h = [f'<div class="ps-carte"><div class="ps-tete"><span class="k">Filtres du plan</span><span class="ps-cpt">{nb_ok} / 5</span></div>'
         f'<ul class="ps-filtres">{filtres}</ul></div>',
         f'<div class="ps-carte"><div class="ps-tete"><span class="k">Tendance</span><span class="ps-mini">dernière cassure de structure</span></div>'
         f'<div class="ps-tfs">{puces}</div></div>']
    if fonda:
        fl = fonda["filtre"]
        comps = "".join(f'<div class="{COUL[c["vote"]]}"><span>{esc(c["nom"].replace(" (Fed)", "").replace("Dollar (DXY)", "DXY"))}</span>'
                        f'<b>{fr(c["v"], 2 if c["unite"] == " %" else 0, True, c["unite"])}</b></div>' for c in fl["comps"])
        rg = fonda.get("regime")
        lect = ""
        if rg:
            lect = (f'Macro : {fr(rg["r2_60"] * 100, 0)} % des mouvements expliqués'
                    + (" · les flux mènent" if rg["r2_60"] < 0.15 else f" · moteur : {rg['dominant']}"))
        h.append(f'<div class="ps-carte"><div class="ps-tete"><span class="k">Fondamental du plan</span>'
                 f'<b class="ps-fv {COUL[fl["bloc"]]}">{ {1: "HAUSSIER", -1: "BAISSIER", 0: "NEUTRE"}[fl["bloc"]] }</b></div>'
                 f'<p class="ps-effet">{esc(fl["effet"])}</p><div class="ps-comps">{comps}</div>'
                 f'{f"<p class=ps-mini>{esc(lect)}</p>" if lect else ""}</div>')
    if t and not t.get("erreur"):
        h.append(f'<div class="ps-carte ps-mom"><div><span class="k">RSI 15 min</span><b>{fr(t.get("rsi15"), 0)}</b></div>'
                 f'<div><span class="k">RSI 1 h</span><b>{fr(t.get("rsi_h1"), 0)}</b></div>'
                 f'<div><span class="k">ATR 15 min</span><b>{fr(t.get("atr15"), 1)} $</b></div>'
                 f'<div><span class="k">ATR 1 h</span><b>{fr(t.get("atr_h1"), 1)} $</b></div></div>')
    return "".join(h)


# ---------------------------------------------------------------------------
# COLONNE 3 : À SURVEILLER
# ---------------------------------------------------------------------------

def niveaux_cles(dz, seance, ajust):
    """Niveaux autour du prix, triés du plus haut au plus bas, avec l'écart au prix."""
    t = dz.get("tech") or {}
    px = dz["px"]
    nv = []
    if seance:
        for cle, lib in (("haut_sem", "Plus haut semaine"), ("pdh", "Plus haut veille"), ("haut", "Plus haut du jour"),
                         ("bas", "Plus bas du jour"), ("pdl", "Plus bas veille"), ("bas_sem", "Plus bas semaine")):
            if seance.get(cle) is not None:
                nv.append((seance[cle] + ajust, lib, "ps-sea"))
    for l in t.get("lignes", []):
        if l["ut"] in ("H4", "H1"):
            if l["sommet"] and not l["sommet"]["casse"]:
                nv.append((l["sommet"]["prix"], f"Sommet {l['ut']} à casser", "ps-str"))
            if l["creux"] and not l["creux"]["casse"]:
                nv.append((l["creux"]["prix"], f"Creux {l['ut']} à casser", "ps-str"))
    zs = t.get("zones") or []
    za = next((z for z in zs if z["sens"] > 0), None)
    zv = next((z for z in zs if z["sens"] < 0), None)
    if zv:
        nv.append((zv["niveau"], f"Zone vente {zv['type']} {zv['ut']}", "ps-zv"))
    if za:
        nv.append((za["niveau"], f"Zone achat {za['type']} {za['ut']}", "ps-za"))
    o = dz.get("ordre")
    if o:
        nv += [(o["entree"], "Entrée du plan", "ps-ent"), (o["stop"], "Stop du plan", "ps-sl")]
    vus, propres = set(), []
    for v, lib, c in sorted(nv, key=lambda x: -x[0]):
        cle = round(v, 1)
        if cle in vus or abs(v - px) > 8 * (t.get("atr_h1") or 50):
            continue
        vus.add(cle)
        propres.append((v, lib, c))
    au_dessus = [x for x in propres if x[0] >= px][-5:]
    en_dessous = [x for x in propres if x[0] < px][:5]
    ligne = lambda v, lib, c: (f'<tr class="{c}"><td>{esc(lib)}</td><td class="num">{fr(v, 1)}</td>'
                               f'<td class="num {"up" if v > px else "down"}">{fr(v - px, 1, True)}</td></tr>')
    return ("".join(ligne(*x) for x in au_dessus)
            + f'<tr class="ps-px"><td>Prix</td><td class="num">{fr(px, 1)}</td><td></td></tr>'
            + "".join(ligne(*x) for x in en_dessous))


def marches(y, f):
    defs = [("Dollar DXY", y.get("dxy"), "pct", -1, 2, ""), ("Taux réel 10 ans", f.get("reel10"), "pb", -1, 2, " %"),
            ("Taux 2 ans", f.get("us2"), "pb", -1, 2, " %"), ("VIX", y.get("vix"), "abs", 0, 1, ""),
            ("Argent", y.get("argent"), "pct", 1, 2, " $"), ("S&P 500", y.get("spx"), "pct", 0, 0, "")]
    out = ""
    for nom, s, mode, effet, d, u in defs:
        s = serie(s)
        if s is None:
            continue
        a, b = float(s.iloc[-2]), float(s.iloc[-1])
        ch = (b / a - 1) * 100 if mode == "pct" else ((b - a) * 100 if mode == "pb" else b - a)
        if abs(ch) < (0.05 if mode == "pb" else 0.005):
            ch = 0.0
        imp = effet * int(np.sign(ch)) if effet else 0
        unite = " %" if mode == "pct" else (" pb" if mode == "pb" else "")
        out += (f'<tr><td>{esc(nom)}</td><td class="num">{fr(b, d)}{esc(u)}</td>'
                f'<td class="num {COUL[imp]}">{fr(ch, 1 if mode == "pb" else 2, True, unite)}</td></tr>')
    return out


def carte_marches(data):
    mk = marches(data["yahoo"], data["fred"])
    if not mk:
        return ""
    return (f'<div class="ps-carte"><div class="ps-tete"><span class="k">Marchés qui font bouger l\'or</span>'
            f'<span class="ps-mini">sur 1 jour · vert = soutient l\'or</span></div><table class="ps-mk">{mk}</table></div>')


def actus(news, mots, now, n=3):
    vus, fil = set(), []
    for items in (news or {}).values():
        for it in items:
            cle = re.sub(r"\W+", " ", it["titre"].lower()).strip()
            if cle not in vus:
                vus.add(cle)
                fil.append(it)
    fil.sort(key=lambda x: x.get("date") or pd.Timestamp.min.tz_localize("UTC"), reverse=True)
    out = ""
    for it in fil[:n]:
        t = it["titre"].lower()
        etq = [ETIQ[c] for c in ETIQ if any(re.search(r"\b" + re.escape(m), t) for m in (mots or {}).get(c, []))]
        age = ""
        if it.get("date") is not None:
            m = int((now - it["date"]).total_seconds() // 60)
            age = f"{m} min" if m < 60 else (f"{m // 60} h" if m < 1440 else f"{m // 1440} j")
        out += (f'<li><span class="ps-age">{esc(age)}</span><a href="{esc(it.get("lien") or "#")}" target="_blank" rel="noopener">'
                f'{esc(it["titre"])}</a>{"".join(f"<em class={c}>{esc(l)}</em>" for l, c in etq)}</li>')
    return out


def col_surveiller(dz, a, data, ajust, fiche, mots, now, avec_niveaux=True):
    h = []
    if fiche:
        d = fiche["date"]
        d0, d1 = d - timedelta(minutes=P.NEWS_MIN), d + timedelta(minutes=P.NEWS_MIN)
        dedans = d0 <= now <= d1
        h.append(f'<div class="ps-carte ps-evt {"ps-alerte" if dedans else ""}"><div class="ps-tete"><span class="k">Prochaine annonce forte</span>'
                 f'<span class="ps-cd">{dans(d - now)}</span></div><b class="ps-evt-nom">{esc(fiche["nom"])}</b>'
                 f'<p class="ps-mini">{JOURS[d.weekday()]} {d:%d/%m %H:%M} · prévu {esc(fiche.get("prevision") or "—")} · précédent {esc(fiche.get("precedent") or "—")}</p>'
                 f'<p class="ps-interdit">{"ZONE INTERDITE EN COURS · " if dedans else ""}Pas d\'entrée de {d0:%H:%M} à {d1:%H:%M}</p></div>')
    else:
        h.append('<div class="ps-carte ps-evt"><div class="ps-tete"><span class="k">Prochaine annonce forte</span></div>'
                 '<b class="ps-evt-nom">Aucune cette semaine</b></div>')
    ag = agenda_jour(data.get("cal"), now)
    if ag:
        h[-1] = h[-1][:-len("</div>")] + ag + "</div>"
    if not avec_niveaux:
        return "".join(h)
    nv = niveaux_cles(dz, a.get("seance"), ajust)
    h.append(f'<div class="ps-carte"><div class="ps-tete"><span class="k">Niveaux clés</span><span class="ps-mini">écart au prix</span></div>'
             f'<table class="ps-niv">{nv}</table></div>')
    return "".join(h)


def agenda_jour(cal, now):
    """Toutes les annonces USD d'aujourd'hui et de demain (fortes et moyennes), en lignes compactes."""
    lst = [e for e in cal or [] if e.get("date") is not None and now.date() <= e["date"].date() <= (now + timedelta(days=1)).date()]
    if not lst:
        return '<p class="ps-mini ps-ag-vide">Rien d\'autre aujourd\'hui ni demain.</p>'
    lig = ""
    for e in lst[:8]:
        passe = e["date"] <= now
        jour = "auj." if e["date"].date() == now.date() else "dem."
        lig += (f'<tr class="{"ps-ag-passe" if passe else ""}"><td class="num">{jour} {e["date"]:%H:%M}</td>'
                f'<td><i class="ps-imp {"fort" if e["impact"] == "High" else ""}"></i>{esc(e["titre"])}</td>'
                f'<td class="num">{esc(e.get("reel") or e.get("prevision") or "—")}</td></tr>')
    return (f'<div class="ps-ag"><span class="k">Agenda aujourd\'hui et demain</span>'
            f'<table class="ps-agt">{lig}</table><a href="#analyse" data-aller="annonces" class="ps-lien">agenda complet</a></div>')


def col_marche(a, data, ajust, mots, now):
    """Colonne 4 : prix et séance, positionnement, marchés, actualités."""
    h = [carte_seance(a, ajust), carte_position(a), carte_marches(data)]
    return "".join(x for x in h if x)


def carte_actus(data, mots, now):
    ac = actus(data.get("news"), mots, now)
    if not ac:
        return ""
    return (f'<div class="ps-carte"><div class="ps-tete"><span class="k">Dernières actualités</span>'
            f'<a href="#analyse" data-aller="actus" class="ps-lien">tout voir</a></div><ul class="ps-actus">{ac}</ul></div>')


def carte_seance(a, ajust):
    s = a.get("seance")
    if not s:
        return ""
    px = s["prix"] + ajust
    pdc = s.get("pdc")
    ch = (s["prix"] - pdc) if pdc else None
    chp = (ch / pdc * 100) if pdc else None
    haut, bas = s["haut"] + ajust, s["bas"] + ajust
    pos = (px - bas) / (haut - bas) * 100 if haut > bas else 50
    ses = ""
    for x in s.get("seances") or []:
        if x.get("haut") is None:
            ses += f'<tr><td>{esc(x["nom"])}</td><td class="num" colspan="2">à venir</td></tr>'
        else:
            ses += (f'<tr class="{"ps-cours" if x.get("en_cours") else ""}"><td>{esc(x["nom"])}{" ●" if x.get("en_cours") else ""}</td>'
                    f'<td class="num">{fr(x["bas"] + ajust, 1)} – {fr(x["haut"] + ajust, 1)}</td>'
                    f'<td class="num">{fr(x["haut"] - x["bas"], 1)} $</td></tr>')
    return (f'<div class="ps-carte"><div class="ps-tete"><span class="k">Prix et séance</span><span class="ps-mini">spot, en direct</span></div>'
            f'<div class="ps-prix"><b id="ps-px-live">{fr(px, 2)}</b><span id="dx-jvar" class="{"up" if (ch or 0) > 0 else "down" if (ch or 0) < 0 else ""}">'
            f'{fr(ch, 2, True)} ({fr(chp, 2, True, " %")})</span></div>'
            f'<div class="ps-range"><span id="dx-jb">{fr(bas, 1)}</span><div class="ps-rb"><i id="dx-jpos" style="left:{pos:.0f}%"></i></div><span id="dx-jh">{fr(haut, 1)}</span></div>'
            f'<p class="ps-mini">Fourchette du jour · amplitude moyenne {fr(s.get("atr"), 1)} $ · <span id="dx-jpct">{fr(s.get("pct_atr"), 0)}</span> % faite</p>'
            f'{f"<table class=ps-ses>{ses}</table>" if ses else ""}</div>')


def carte_position(a):
    lig = []
    c = a.get("cot")
    if c:
        lig.append(("Fonds (COT)", f'percentile {fr(c["pct3a"], 0)} · {fr(c["chg"], 0, True)}/sem.', "down" if c["pct3a"] >= 85 else "up" if c["pct3a"] <= 15 else ""))
    cn = a.get("carnets")
    if cn and cn.get("part_acheteurs") is not None:
        pa = cn["part_acheteurs"]
        lig.append(("Particuliers (OANDA)", f'{fr(pa, 0)} % acheteurs', "down" if pa >= 65 else "up" if pa <= 35 else ""))
    g = a.get("gld")
    if g:
        lig.append(("ETF GLD, 5 jours", f'{fr(g["d5"], 1, True)} t', "up" if (g["d5"] or 0) >= 5 else "down" if (g["d5"] or 0) <= -5 else ""))
    if not lig:
        return ""
    rows = "".join(f'<tr><td>{esc(k)}</td><td class="num {c_}">{v}</td></tr>' for k, v, c_ in lig)
    return (f'<div class="ps-carte"><div class="ps-tete"><span class="k">Positionnement</span>'
            f'<a href="#analyse" data-aller="data" class="ps-lien">détail</a></div><table class="ps-mk">{rows}</table>'
            f'<p class="ps-mini">vert : signal contrarien plutôt haussier · rouge : plutôt baissier</p></div>')


def bloc_poste(a, data, ajust, compte, statut, fiche, mots, now, echelle=None, stat_html="", robot_html=""):
    """echelle : carte « Niveaux en direct » (remplie par le navigateur) ; stat_html : carte des statistiques du moment.
    Chaque colonne est une zone que le robot rafraîchit ; la carte des niveaux en direct n'en fait pas partie,
    pour ne pas perdre ce que tu y as saisi."""
    dz = a.get("decision")
    if not dz or dz.get("erreur"):
        return ""
    if echelle:
        c3 = (f'<div class="ps-col"><div class="ps-zone" data-zone="ps-c3">'
              f'{col_surveiller(dz, a, data, ajust, fiche, mots, now, avec_niveaux=False)}</div>{echelle}</div>')
    else:
        c3 = f'<div class="ps-col" data-zone="ps-c3">{col_surveiller(dz, a, data, ajust, fiche, mots, now)}</div>'
    return (f'<section class="ps" aria-label="Poste de pilotage">'
            f'<div class="ps-col" data-zone="ps-c1">{col_decision(dz, statut, compte)}{robot_html}{carte_actus(data, mots, now)}</div>'
            f'<div class="ps-col" data-zone="ps-c2">{col_contexte(dz, a.get("fonda"))}{stat_html}</div>'
            f'{c3}'
            f'<div class="ps-col" data-zone="ps-c4">{col_marche(a, data, ajust, mots, now)}</div></section>')


CSS = """
/* Poste de pilotage : le premier écran */
.ps{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr) minmax(0,1fr) minmax(0,1fr);gap:12px;margin:0 0 12px;align-items:start}
.ps-prix{display:flex;align-items:baseline;gap:10px} .ps-prix b{font:700 26px var(--mono);letter-spacing:-.02em} .ps-prix span{font:600 13px var(--mono)}
.ps-range{display:flex;align-items:center;gap:8px;margin:8px 0 2px;font:500 11.5px var(--mono);color:var(--mute)}
.ps-rb{flex:1;position:relative;height:6px;border-radius:3px;background:linear-gradient(90deg,rgba(255,90,95,.45),#3a3a42,rgba(47,210,125,.45))}
.ps-rb i{position:absolute;top:-4px;width:3px;height:14px;margin-left:-1px;background:var(--ink);border-radius:1px}
.ps-ses,.ps-agt{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:6px}
.ps-ses td,.ps-agt td{padding:4px 0;border-bottom:1px solid var(--band2)} .ps-ses td.num,.ps-agt td.num{text-align:right;font-family:var(--mono)}
.ps-ses tr.ps-cours td:first-child{color:var(--brass)}
.ps-ag{margin-top:10px;padding-top:8px;border-top:1px solid var(--line)} .ps-ag .k{display:block}
.ps-agt td:nth-child(2){padding-left:8px} .ps-agt tr.ps-ag-passe td{color:var(--mute)}
.ps-imp{display:inline-block;width:7px;height:7px;border-radius:50%;background:#55555e;margin-right:6px;vertical-align:1px} .ps-imp.fort{background:var(--amber)}
#tab-accueil{padding-top:12px} #tab-accueil>div>.ps:first-child,#tab-accueil .ps{margin-top:0}
.ps-col{display:flex;flex-direction:column;gap:12px;min-width:0}
.ps-carte{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.ps-tete{display:flex;align-items:center;gap:8px;justify-content:space-between;margin-bottom:8px}
.ps-tete .k{margin-right:auto}
.ps-maj,.ps-mini{font-size:12px;color:var(--mute)}
p.ps-mini{margin:6px 0 0}
.ps-demo{font:700 10.5px var(--text);letter-spacing:.05em;color:var(--amber);border:1px solid rgba(245,166,35,.5);border-radius:4px;padding:2px 7px;cursor:help}
.ps-dec{display:flex;flex-direction:column;gap:10px}
.ps-dec.ps-achat{border-color:rgba(52,195,143,.55);background:linear-gradient(180deg,rgba(52,195,143,.10),var(--panel) 55%)}
.ps-dec.ps-vente{border-color:rgba(240,97,109,.55);background:linear-gradient(180deg,rgba(240,97,109,.10),var(--panel) 55%)}
.ps-verdict{display:flex;align-items:center;gap:14px}
.ps-verdict>b{font:800 54px/1 var(--text);letter-spacing:-.03em}
.ps-achat .ps-verdict>b,.ps-achat .ps-note{color:var(--up)} .ps-vente .ps-verdict>b,.ps-vente .ps-note{color:var(--down)}
.ps-rien .ps-verdict>b{color:var(--steel)}
.ps-prep-a .ps-verdict>b{color:var(--up)} .ps-prep-v .ps-verdict>b{color:var(--down)}
.ps-prep-a .ps-verdict>b,.ps-prep-v .ps-verdict>b{font-size:40px;opacity:.85}
.ps-dec.ps-prep-a{border-color:rgba(47,210,125,.35)} .ps-dec.ps-prep-v{border-color:rgba(255,90,95,.35)}
.ps-prep{display:block;font:700 13px var(--text);letter-spacing:.12em;text-transform:uppercase;color:var(--ink);margin-top:6px}
.ps-sous{margin:0;font-size:14px;line-height:1.5}
.ps-etapes ol{margin:4px 0 0;padding-left:18px;font-size:14px;line-height:1.5;display:flex;flex-direction:column;gap:3px}
.ps-jauge{display:flex;align-items:center;gap:4px;margin-bottom:6px}
.ps-jauge i{width:26px;height:6px;border-radius:3px;background:var(--band2)} .ps-jauge i.on{background:var(--brass)}
.ps-jauge span{font:600 12px var(--mono);color:var(--mute);margin-left:6px}
.ps-freq{margin:0;font-size:12.5px;color:var(--mute);line-height:1.45}
.ps-avert{margin:0;padding:8px 10px;border-radius:8px;border:1px solid rgba(255,90,95,.55);background:rgba(255,90,95,.09);color:var(--down);
  font-size:13px;font-weight:600;line-height:1.45}
.ps-verrou{padding:9px 11px;border-radius:8px;border:1px solid var(--down);background:rgba(255,90,95,.12);color:var(--down);
  font:700 13px/1.45 var(--text)}
.ps-verrou[hidden]{display:none}
.ps-verrou small{display:block;font-weight:500;color:var(--ink);margin-top:2px}
.ps-verdict div{display:flex;flex-direction:column;gap:3px}
.ps-verdict small{font-size:13px;color:var(--ink)}
.ps-note{align-self:flex-start;font:700 11px var(--text);border:1px solid currentColor;border-radius:5px;padding:2px 8px;color:var(--mute)}
.ps-pourquoi{margin:0;font-size:14px;line-height:1.5} .ps-pourquoi .k{display:block;margin-bottom:2px}
.ps-deb ol{margin:4px 0 0;padding-left:18px;font-size:14px;line-height:1.5;display:flex;flex-direction:column;gap:3px}
.ps-ticket{width:100%;border-collapse:collapse}
.ps-ticket td{padding:6px 0;border-bottom:1px solid var(--band2);font-size:13.5px;color:var(--mute)}
.ps-ticket td.num{text-align:right;color:var(--ink);font-weight:600;font-size:14.5px}
.ps-ticket td.num em{font-style:normal;color:var(--mute);font-weight:500;font-size:12px}
.ps-ticket td.brass{color:var(--brass)} .ps-ticket td.up{color:var(--up)} .ps-ticket td.down{color:var(--down)}
.ps-dist,.ps-inval{margin:0;font-size:12.5px;color:var(--mute)}
.ps-copier{min-height:42px;border:0;border-radius:8px;font:700 14px var(--text);color:#0b0e13}
.ps-copier.ps-achat{background:var(--up)} .ps-copier.ps-vente{background:var(--down)}
.ps-jour{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;padding-top:10px;border-top:1px solid var(--line)}
.ps-jour div{display:flex;flex-direction:column;gap:2px} .ps-jour b{font:600 14px var(--text);font-variant-numeric:tabular-nums}
.ps-cpt{font:600 12.5px var(--text);color:var(--mute)}
.ps-filtres{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:6px}
.ps-filtres li{display:grid;grid-template-columns:20px 92px minmax(0,1fr);gap:8px;align-items:center;font-size:13px}
.ps-filtres i{font-style:normal;width:20px;height:20px;border-radius:5px;display:flex;align-items:center;justify-content:center;font:700 11px var(--text)}
.ps-filtres b{font-weight:600}
.ps-filtres span{color:var(--mute);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.ps-ok i{color:var(--up);background:rgba(52,195,143,.15)} .ps-ko i{color:var(--down);background:rgba(240,97,109,.17)} .ps-ko b{color:var(--down)}
.ps-wait i{color:var(--mute);background:rgba(141,151,168,.15)} .ps-wait b{color:var(--mute)}
.ps-tfs{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px}
.ps-tf{display:flex;flex-direction:column;align-items:center;gap:2px;padding:7px 0;border-radius:8px;background:var(--band)}
.ps-tf span{font:600 11px var(--text);color:var(--mute)} .ps-tf b{font-size:17px}
.ps-tf.up b{color:var(--up)} .ps-tf.down b{color:var(--down)} .ps-tf.flat b{color:var(--mute)}
.ps-fv{font:800 15px var(--text);letter-spacing:.02em} .ps-fv.flat{color:var(--steel)}
.ps-effet{margin:0 0 8px;font-size:13px}
.ps-comps{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}
.ps-comps div{background:var(--band);border-radius:8px;padding:6px 8px;display:flex;flex-direction:column}
.ps-comps span{font-size:11.5px;color:var(--mute)} .ps-comps b{font:600 14px var(--text);font-variant-numeric:tabular-nums}
.ps-comps .up b{color:var(--up)} .ps-comps .down b{color:var(--down)}
.ps-mom{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}
.ps-mom div{display:flex;flex-direction:column;gap:2px} .ps-mom b{font:600 16px var(--text);font-variant-numeric:tabular-nums}
.ps-evt-nom{font:700 17px var(--text);display:block}
.ps-cd{font:600 13px var(--mono);color:var(--brass)}
.ps-interdit{margin:8px 0 0;font:600 12.5px var(--text);color:var(--amber)}
.ps-evt.ps-alerte{border-color:rgba(240,97,109,.6);background:rgba(240,97,109,.08)} .ps-evt.ps-alerte .ps-interdit{color:var(--down)}
.ps-niv,.ps-mk{width:100%;border-collapse:collapse;font-size:13px}
.ps-niv td,.ps-mk td{padding:5px 0;border-bottom:1px solid var(--band2)}
.ps-niv td.num,.ps-mk td.num{text-align:right;padding-left:10px}
.ps-niv tr.ps-px td{color:var(--brass);font-weight:700;border-top:1px solid var(--brass);border-bottom:1px solid var(--brass)}
.ps-niv tr.ps-za td:first-child{color:var(--up)} .ps-niv tr.ps-zv td:first-child{color:var(--down)}
.ps-niv tr.ps-ent td:first-child{color:var(--brass)} .ps-niv tr.ps-sl td:first-child{color:var(--down)}
.ps-niv tr.ps-str td:first-child{color:#8fa8d4}
.ps-actus{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:8px}
.ps-actus li{font-size:13px;line-height:1.4}
.ps-actus a{color:var(--ink)} .ps-age{font:500 11.5px var(--mono);color:var(--mute);margin-right:6px}
.ps-actus em{font:600 10.5px var(--text);font-style:normal;margin-left:6px;padding:1px 6px;border-radius:4px;border:1px solid currentColor}
.ps-actus em.up{color:var(--up)} .ps-actus em.down{color:var(--down)}
.ps-lien{font-size:12px;color:var(--brass)}
.ps-tv{margin:0 0 12px;padding-bottom:14px}
.ps-tvbox{position:relative;height:620px;overflow:hidden;border-radius:8px;border:1px solid var(--band2);background:#10141b}
.ps-tvbox .tv{position:absolute;inset:0}
.ps-titre{font:600 10.5px var(--text);letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin:6px 0 8px}
@media (max-width:760px){.ps-tvbox{height:420px}}
@media (max-width:1500px){.ps{grid-template-columns:minmax(0,1.1fr) minmax(0,1fr) minmax(0,1fr)}
  .ps-col:nth-child(4){grid-column:1/-1;display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));align-items:start}}
@media (max-width:1100px){.ps{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
@media (max-width:760px){.ps{grid-template-columns:minmax(0,1fr)}.ps-verdict>b{font-size:44px}.ps-jour{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""
