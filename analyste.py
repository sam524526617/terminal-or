# -*- coding: utf-8 -*-
"""
ANALYSTE IA : une note de desk écrite à partir du plan, pas à côté
=================================================================

Avant, l'analyste commentait une « vue de marché » que le terminal n'affiche plus. Ici il reçoit exactement ce que
le trader a sous les yeux : la décision du plan et ses cinq filtres, la structure par unité de temps avec les niveaux
à casser, les zones encore intactes, le fondamental du plan, les statistiques de la séance en cours, l'agenda et ses
fenêtres interdites, le positionnement. Il doit dire où il est d'accord avec le plan, où il ne l'est pas, et ce qui
ferait changer la lecture, en ne citant que des niveaux présents dans le relevé.

Le relevé (brief), la consigne, le schéma de réponse, l'appel à Claude et l'affichage sont dans ce module.
"""

import html
import json
import math
from datetime import timedelta

import plan as P

MODELE_CLAUDE = "claude-opus-5-5"
FORMAT = 3            # version du format de la note (une note d'un ancien format est régénérée)


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=1, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n.d."
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


# ---------------------------------------------------------------------------
# RELEVÉ ENVOYÉ À L'ANALYSTE
# ---------------------------------------------------------------------------

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def brief(a, data, ajust, now, niveaux=None, labo_statut=None, mouvements=None):
    """Texte du relevé : tout ce que la page affiche d'utile à la décision, en prix spot."""
    dz, s = a["decision"], a.get("seance") or {}
    t = dz.get("tech") or {}
    L = [f"RELEVÉ DU TERMINAL OR · {JOURS[now.weekday()]} {now:%d/%m/%Y %H:%M} (heure de Paris) · tous les prix en XAU/USD spot", ""]
    L.append("== PRIX ET SÉANCE ==")
    if s:
        sp = lambda x: None if x is None else x + ajust
        L.append(f"Prix {fr(dz['px'], 2)} · jour {fr(sp(s.get('bas')))} – {fr(sp(s.get('haut')))} · veille {fr(sp(s.get('pdl')))} – "
                 f"{fr(sp(s.get('pdh')))} (clôture {fr(sp(s.get('pdc')))}) · amplitude moyenne d'un jour {fr(s.get('atr'))} $ "
                 f"({fr(s.get('pct_atr'), 0)} % faite) · VWAP {fr(sp(s.get('vwap')))}")
        for x in s.get("seances") or []:
            if x.get("haut") is not None:
                L.append(f"  séance {x['nom']} : {fr(sp(x['bas']))} – {fr(sp(x['haut']))}{' (en cours)' if x.get('en_cours') else ''}")
    m = dz.get("marche") or {}
    if not m.get("ouvert", True):
        L.append(f"MARCHÉ FERMÉ ({m.get('raison')}), réouverture {JOURS[m['reouverture'].weekday()]} {m['reouverture']:%H:%M}." if m.get("reouverture") else "MARCHÉ FERMÉ.")
    if dz.get("en_retard"):
        L.append(f"ATTENTION : bougies en retard de {dz.get('retard_min', 0):.0f} min.")

    if mouvements:
        L += ["", "== DERNIÈRES SÉANCES (variation, ce qui a bougé le même jour, annonces, technique, titres de presse) =="] + list(mouvements)
    L += ["", "== DÉCISION DU PLAN (règles fixes du trader) =="]
    o = dz.get("ordre")
    if o:
        L.append(f"Verdict : {'ACHAT' if o['sens'] > 0 else 'VENTE'}, note {dz.get('note')}, état « {dz.get('etat')} ». Entrée {fr(o['entree'], 2)}, "
                 f"stop {fr(o['stop'], 2)}, TP1 {fr(o['tp1'], 2)} (2 R), TP2 {fr(o['tp2'], 2)}, zone {o['zone']}.")
    else:
        L.append("Verdict : RIEN (aucun ordre).")
    for nom, txt, etat in dz.get("filtres") or []:
        L.append(f"  filtre {nom} [{ {'ok': 'validé', 'ko': 'BLOQUE', 'wait': 'en attente'}[etat] }] : {txt}")
    deb = dz.get("debloque") or []
    for x in ([deb] if isinstance(deb, str) else deb):
        L.append(f"  ce qui débloque : {x}")
    L.append(f"Trades du plan aujourd'hui : {dz.get('nb_jour', 0)} sur {P.MAX_TRADES_JOUR}, {fr(dz.get('r_jour', 0.0), 1, True)} R. "
             f"Règles : {P.RISQUE_PCT:g} % par trade, arrêt à {P.PERTE_MAX_JOUR_R:g} R, pas d'entrée à ±{P.NEWS_MIN} min d'une annonce forte.")
    st = (dz.get("histo") or {}).get("stats")
    if st:
        L.append(f"Le plan rejoué sur 60 jours : {st['n']} trades, réussite {st['reussite']:.0f} %, {fr(st['total'], 1, True)} R.")
    if labo_statut:
        L.append(f"Statut de cette stratégie au backtest sur plusieurs années : {labo_statut}.")

    L += ["", "== STRUCTURE PAR UNITÉ DE TEMPS (tendance = sens de la dernière cassure de structure) =="]
    mot = {1: "haussière", -1: "baissière", 0: "neutre"}
    for l in t.get("lignes", []):
        niv = lambda x, lib: (f"{lib} {fr(x['prix'])} ({'cassé' if x['casse'] else 'à ' + fr(x['dist'], 1, True) + ' $'})" if x else f"{lib} n.d.")
        L.append(f"  {l['nom']} : {mot[l['tend']]} · {niv(l['sommet'], 'dernier sommet')} · {niv(l['creux'], 'dernier creux')}")
    L.append(f"Momentum : RSI 15 min {fr(t.get('rsi15'), 0)}, RSI 1 h {fr(t.get('rsi_h1'), 0)}, ATR 15 min {fr(t.get('atr15'))} $, "
             f"ATR 1 h {fr(t.get('atr_h1'))} $" + (", divergence RSI haussière" if t.get("div_h") else ", divergence RSI baissière" if t.get("div_b") else ""))
    zs = t.get("zones") or []
    if zs:
        L.append("Zones OB / FVG encore intactes (les plus proches) :")
        for z in zs[:8]:
            L.append(f"  {'achat' if z['sens'] > 0 else 'vente'} {z['type']} {z['ut']} : {fr(z['bas'])} – {fr(z['haut'])}, contact {fr(z['niveau'])} "
                     f"({fr(z['dist'], 1, True)} $){' · dans le sens du plan' if z.get('plan') else ''}")
    if niveaux:
        haut = [n for n in niveaux if n["v"] > dz["px"]][-6:]
        bas = [n for n in niveaux if n["v"] <= dz["px"]][:6]
        L.append("Niveaux autour du prix : au-dessus " + " ; ".join(f"{n['lib']} {fr(n['v'])}" for n in haut)
                 + " | en dessous " + " ; ".join(f"{n['lib']} {fr(n['v'])}" for n in bas))

    F = a.get("fonda")
    if F:
        L += ["", "== FONDAMENTAL =="]
        L.append(F["synthese"])
        for c in F["filtre"]["comps"]:
            L.append(f"  {c['nom']} : {fr(c['v'], 2 if c['unite'] == ' %' else 0, True, c['unite'])} sur 5 jours → {c['lecture']}")
        rg = F.get("regime")
        if rg:
            L.append(f"  {rg['lecture']} {rg.get('lecture_flux', '')}")
        for cle in ("cot", "gld"):
            x = a.get(cle)
            if cle == "cot" and x:
                L.append(f"  Fonds spéculatifs (COT) : net {fr(x['net'], 0)} contrats, {fr(x['chg'], 0, True)} sur la semaine, percentile 3 ans {fr(x['pct3a'], 0)}.")
            if cle == "gld" and x:
                L.append(f"  ETF GLD : {fr(x['d5'], 1, True)} t sur 5 jours, {fr(x['d20'], 1, True)} t sur 20 jours.")

    S = a.get("stat")
    if S and S.get("jour"):
        J, A = S["jour"], S["amplitude"]
        L += ["", f"== STATISTIQUES DE SÉANCE (fréquences sur {S['n']} jours, pas des prévisions) =="]
        L.append(f"À {J['heure']} h : amplitude du jour {fr(J['amp'], 0)} $ ; d'habitude {J['part'] * 100:.0f} % de l'amplitude finale est déjà faite ; "
                 f"amplitude finale probable {fr(J['finale'][0], 0)} – {fr(J['finale'][2], 0)} $ (médiane d'une journée : {fr(A['med'], 0)} $).")
        L.append(f"Le plus haut du jour ({J['h_haut']} h) tient jusqu'au soir {J['tient_haut'] * 100:.0f} % du temps ; "
                 f"le plus bas ({J['h_bas']} h) {J['tient_bas'] * 100:.0f} %.")
        asie = J.get("asie")
        if asie and asie.get("cote") in ("haut", "bas"):
            su = S["asie"]["suite_haut" if asie["cote"] == "haut" else "suite_bas"]
            if su:
                L.append(f"Range asiatique {fr(asie['bas'] + ajust)} – {fr(asie['haut'] + ajust)} cassé par le {asie['cote']} à {asie['heure']} h : "
                         f"la journée clôture au-delà {su['continue'] * 100:.0f} % du temps.")

    cal = [e for e in (data.get("cal") or []) if e.get("date") is not None and e["date"] >= now - timedelta(hours=3)]
    forts = [e for e in cal if e["impact"] == "High"][:6]
    if forts:
        L += ["", "== ANNONCES FORTES (USD) =="]
        for e in forts:
            passe = e["date"] < now
            L.append(f"  {JOURS[e['date'].weekday()]} {e['date']:%d/%m %H:%M} {e['titre']} · prévu {e.get('prevision') or 'n.d.'} · précédent {e.get('precedent') or 'n.d.'}"
                     + (f" · publié {e.get('reel') or 'n.d.'}" if passe else f" · fenêtre interdite {e['date'] - timedelta(minutes=P.NEWS_MIN):%H:%M}–{e['date'] + timedelta(minutes=P.NEWS_MIN):%H:%M}"))
    titres = []
    for items in (data.get("news") or {}).values():
        titres += [it["titre"] for it in items[:3]]
    if titres:
        L += ["", "== TITRES RÉCENTS (contexte seulement) =="] + [f"  - {x}" for x in titres[:10]]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# CONSIGNE ET FORMAT DE RÉPONSE
# ---------------------------------------------------------------------------

CONSIGNE = """Tu écris la note de séance d'un desk or (XAU/USD) pour un trader particulier qui débute, trade en intraday et suit un plan écrit : tendance Daily et H4 alignées, retour dans une zone OB ou FVG, cassure M15 qui laisse un FVG, ordre limite au 50 % du FVG, 1 % de risque, deux trades par jour au plus.

Tu reçois le relevé complet de son terminal. Il a ce relevé sous les yeux : ne le lui récite pas. Ta valeur est de relier les éléments entre eux et de dire ce qu'il ne voit pas seul : quel moteur domine vraiment, si la technique et le fondamental racontent la même histoire, où le plan a raison d'attendre et où il risque de passer à côté, ce qui ferait basculer la lecture.

Ce qui compte pour lui :
- Ce plan a perdu de l'argent au backtest sur plusieurs années ; ses signaux sont affichés comme une démonstration. Ne pousse donc jamais à prendre un trade. Décris des conditions et des niveaux, et dis franchement quand le mieux est de ne rien faire.
- Il a tendance à vouloir trader souvent. Quand le contexte est mauvais (filtres bloqués, annonce proche, amplitude du jour déjà faite, marché fermé), dis-le sans détour.
- N'utilise que des prix et des chiffres présents dans le relevé, recopiés tels quels. Si une information manque, dis qu'elle manque.
- Quand les signaux se contredisent, expose la contradiction au lieu de trancher artificiellement, et indique ton niveau de conviction.
- Écris en français simple, avec des phrases complètes. Explique chaque terme technique la première fois en quelques mots. Pas de jargon inutile, pas de formules creuses.
- Les titres d'actualité ne sont qu'un contexte : ne leur fais pas dire plus qu'ils ne disent.
- Il veut comprendre pourquoi l'or monte ou baisse. Quand tu expliques un mouvement, donne la cause la plus probable d'après le relevé et dis si elle est fondamentale (dollar, taux, annonce, peur) ou technique (cassure d'un niveau, prises de bénéfices, stops qui sautent). Deux choses qui bougent ensemble ne prouvent rien : parle de cause probable.

Longueur : une note qui se lit en une minute. Chaque champ va droit au fait."""

SCHEMA = {
    "type": "object",
    "properties": {
        "titre": {"type": "string", "description": "Une phrase : la situation de l'or maintenant et ce que ça implique."},
        "synthese": {"type": "string", "description": "3 à 4 phrases : ce qui se passe, pourquoi, et quel moteur domine."},
        "pourquoi_recent": {"type": "string", "description": "3 à 4 phrases : pourquoi l'or a monté ou baissé ces derniers jours. Explique d'abord le mouvement le plus fort des dernières séances (cause la plus probable, fondamentale ou technique), puis la séance en cours. Si le relevé ne permet pas de le savoir, dis-le."},
        "biais": {"type": "string", "enum": ["haussier", "baissier", "neutre"], "description": "Ta lecture à quelques heures."},
        "conviction": {"type": "string", "enum": ["faible", "moyenne", "forte"]},
        "accord_plan": {"type": "string", "enum": ["d'accord", "nuancé", "en désaccord"], "description": "Ton avis sur la décision du plan."},
        "accord_explication": {"type": "string", "description": "1 à 2 phrases : pourquoi."},
        "scenario_principal": {"type": "string", "description": "Le scénario le plus probable, avec les niveaux du relevé qui le confirment."},
        "scenario_alternatif": {"type": "string", "description": "Ce qui se passe si le scénario principal échoue, et à quel niveau on le sait."},
        "achat": {"type": "string", "description": "Dans quelles conditions un achat serait cohérent aujourd'hui, ou pourquoi l'éviter."},
        "vente": {"type": "string", "description": "Même chose pour une vente."},
        "niveaux": {"type": "array", "items": {"type": "string"}, "description": "3 à 5 niveaux du relevé, chacun avec son rôle (« 4 110,0 : clôture H1 au-dessus = H1 réaligné »)."},
        "annonces": {"type": "array", "items": {"type": "string"}, "description": "Pour chaque annonce forte à venir : l'heure, la fenêtre sans trade et la réaction probable de l'or selon le chiffre."},
        "risques": {"type": "array", "items": {"type": "string"}, "description": "2 à 3 choses qui invalideraient ta lecture."},
        "discipline": {"type": "string", "description": "Une phrase : l'erreur à ne pas commettre aujourd'hui dans ce contexte précis."},
    },
    "required": ["titre", "synthese", "pourquoi_recent", "biais", "conviction", "accord_plan", "accord_explication", "scenario_principal",
                 "scenario_alternatif", "achat", "vente", "niveaux", "annonces", "risques", "discipline"],
    "additionalProperties": False,
}
LISTES = ("niveaux", "annonces", "risques")
TEXTES = tuple(k for k in SCHEMA["properties"] if k not in LISTES)

CONSIGNE_JSON = (CONSIGNE + "\n\nRéponds uniquement avec un objet JSON valide, sans texte autour, avec exactement ces clés : "
                 + " ; ".join(f'"{k}" ({"liste de phrases" if k in LISTES else "texte"} : {v["description"] if "description" in v else " / ".join(v["enum"])})'
                              for k, v in SCHEMA["properties"].items()) + ".")


def lire(texte):
    """Objet JSON de la note (tolère du texte ou des balises autour), ou None."""
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
    if not isinstance(d, dict) or not d.get("synthese"):
        return None
    for k in LISTES:
        v = d.get(k)
        d[k] = [str(x) for x in v] if isinstance(v, list) else ([str(v)] if v else [])
    for k in TEXTES:
        d[k] = str(d.get(k) or "")
    d["format"] = FORMAT
    return d


def appeler_claude(cle, releve, consigne=CONSIGNE, schema=SCHEMA):
    """Note rédigée par Claude (SDK officiel). Réponse contrainte par le schéma : toujours un JSON valide.
    Si les filtres de sécurité du modèle refusent la demande, l'API bascule seule sur un modèle de secours."""
    import anthropic

    client = anthropic.Anthropic(api_key=cle, timeout=300.0, max_retries=2)
    args = dict(model=MODELE_CLAUDE, max_tokens=16000, system=consigne,
                thinking={"type": "adaptive"}, output_config={"effort": "high"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                messages=[{"role": "user", "content": "Relevé du terminal :\n\n" + releve}])
    if schema:
        args["output_config"]["format"] = {"type": "json_schema", "schema": schema}
    try:
        rep = client.beta.messages.create(**args)
    except anthropic.AuthenticationError:
        raise RuntimeError("clé Claude refusée")
    except anthropic.RateLimitError:
        raise RuntimeError("limite de débit Claude atteinte")
    except anthropic.APIStatusError as e:
        raise RuntimeError(f"erreur Claude {e.status_code} : {str(e.message)[:120]}")
    except anthropic.APIConnectionError:
        raise RuntimeError("Claude injoignable")
    if rep.stop_reason == "refusal":
        raise RuntimeError("demande refusée par le modèle")
    if rep.stop_reason == "max_tokens":
        raise RuntimeError("réponse coupée")
    texte = "".join(b.text for b in rep.content if b.type == "text").strip()
    return texte, rep.model


# ---------------------------------------------------------------------------
# AFFICHAGE
# ---------------------------------------------------------------------------

COUL = {"haussier": "up", "baissier": "down", "neutre": "flat", "d'accord": "up", "en désaccord": "down", "nuancé": "warn"}


def carte_accueil(ia, quand=""):
    """Carte de la Vue d'ensemble : l'essentiel de la note, en trois lignes."""
    j = (ia or {}).get("json")
    if not j or j.get("format") != FORMAT:
        return ""
    return (f'<div class="ps-carte an-carte"><div class="ps-tete"><span class="k">Note de l\'analyste IA</span>'
            f'<a href="#analyse" data-aller="fondamental" class="ps-lien">note complète</a></div>'
            f'<p class="an-t">{esc(j["titre"])}</p>'
            f'<div class="an-puces"><span class="an-p {COUL.get(j["biais"], "flat")}">Biais {esc(j["biais"])} · conviction {esc(j["conviction"])}</span>'
            f'<span class="an-p {COUL.get(j["accord_plan"], "flat")}">Plan : {esc(j["accord_plan"])}</span></div>'
            f'<p class="an-d"><span class="k">À ne pas faire aujourd\'hui</span>{esc(j["discipline"])}</p>'
            f'<p class="ps-mini">{esc(quand)} · un avis à relire avec ton propre jugement.</p></div>')


def bloc(ia, quand=""):
    """Note complète (onglet Fondamental)."""
    if not ia or not ia.get("texte"):
        return ('<section id="analyste"><h2>Note de l\'analyste IA</h2><p class="vide">Inactive : ajoute une clé Claude '
                '(secret GitHub ANTHROPIC_API_KEY) ou une clé Gemini gratuite (GEMINI_API_KEY) pour recevoir une note de séance '
                'toutes les 2 heures.</p></section>')
    j = ia.get("json")
    tete = (f'<p class="pourquoi">Rédigée par {esc(ia.get("fournisseur", "Claude"))} ({esc(ia.get("modele", ""))}) le {esc(quand)}'
            f'{", " + esc(ia["raison"]) if ia.get("raison") else ""}, à partir du relevé de cette page : décision du plan, structure, '
            f'zones, fondamental, statistiques, agenda. C\'est un avis : il peut se tromper.</p>')
    if not j or j.get("format") != FORMAT:
        return (f'<section id="analyste"><h2>Note de l\'analyste IA</h2>{tete}'
                f'<p class="vide">La note sera réécrite au nouveau format au prochain passage.</p></section>')
    li = lambda xs: "".join(f"<li>{esc(x)}</li>" for x in xs) or "<li>—</li>"
    return (f'<section id="analyste" class="an"><h2>Note de l\'analyste IA</h2>{tete}'
            f'<div class="an-hero"><p class="an-titre">{esc(j["titre"])}</p><p>{esc(j["synthese"])}</p>'
            f'<div class="an-puces"><span class="an-p {COUL.get(j["biais"], "flat")}">Biais {esc(j["biais"])} · conviction {esc(j["conviction"])}</span>'
            f'<span class="an-p {COUL.get(j["accord_plan"], "flat")}">Avis sur la décision du plan : {esc(j["accord_plan"])}</span></div>'
            f'<p class="an-acc">{esc(j["accord_explication"])}</p></div>'
            f'<div class="an-grille">'
            f'<div class="an-c"><span class="k">Scénario principal</span><p>{esc(j["scenario_principal"])}</p></div>'
            f'<div class="an-c"><span class="k">Scénario alternatif</span><p>{esc(j["scenario_alternatif"])}</p></div>'
            f'<div class="an-c an-a"><span class="k">Pour un achat</span><p>{esc(j["achat"])}</p></div>'
            f'<div class="an-c an-v"><span class="k">Pour une vente</span><p>{esc(j["vente"])}</p></div>'
            f'<div class="an-c"><span class="k">Niveaux qui comptent</span><ul>{li(j["niveaux"])}</ul></div>'
            f'<div class="an-c"><span class="k">Annonces</span><ul>{li(j["annonces"])}</ul></div>'
            f'<div class="an-c"><span class="k">Ce qui invaliderait la lecture</span><ul>{li(j["risques"])}</ul></div>'
            f'<div class="an-c an-d2"><span class="k">À ne pas faire aujourd\'hui</span><p>{esc(j["discipline"])}</p></div>'
            f'</div></section>')


DEMO = json.dumps({
    "titre": "L'or reste sous le dernier sommet 1 h : le plan a raison d'attendre.",
    "synthese": "Le Daily et le H4 pointent vers le haut, mais le H1 a cassé vers le bas et n'a pas encore repris son dernier sommet. "
                "Le fondamental du plan est neutre : ni les taux réels ni le dollar ne poussent franchement. Ce sont donc les flux et la "
                "technique qui mènent, et la technique est partagée.",
    "pourquoi_recent": "La forte baisse de la semaine vient surtout de prises de bénéfices après une longue hausse : le dollar et les taux "
                       "ont peu bougé ce jour-là, et le plus bas de 20 jours a cédé, ce qui a déclenché des ventes en chaîne. "
                       "Aujourd'hui l'or se stabilise sans nouvelle cause visible.",
    "biais": "neutre", "conviction": "faible", "accord_plan": "d'accord",
    "accord_explication": "Tant que le H1 contredit le Daily et le H4, acheter revient à parier contre le mouvement en cours.",
    "scenario_principal": "Consolidation sous le dernier sommet 1 h, puis reprise si une bougie d'une heure clôture au-dessus.",
    "scenario_alternatif": "Si le prix casse le dernier creux 1 h, le H4 est menacé et le plan n'a plus de sens d'achat.",
    "achat": "Seulement après une clôture 1 h au-dessus du dernier sommet, puis un retour dans une zone d'achat intacte.",
    "vente": "Le plan n'autorise pas de vente : le Daily et le H4 sont haussiers.",
    "niveaux": ["Dernier sommet 1 h : une clôture au-dessus réaligne le H1", "Plus bas du jour : sous ce niveau, la séance tourne à la baisse"],
    "annonces": ["Inflation PCE : pas d'entrée 30 minutes avant ni après"],
    "risques": ["Clôture 1 h sous le dernier creux", "Dollar qui repart nettement à la hausse"],
    "discipline": "Ne pas acheter « parce que le Daily monte » alors que le H1 n'a rien confirmé.",
}, ensure_ascii=False)

CSS = """
/* Note de l'analyste IA */
.an-hero{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--brass);border-radius:10px;padding:14px 16px;margin-bottom:10px}
.an-hero p{margin:0 0 8px;font-size:14.5px;line-height:1.55;max-width:95ch}
.an-titre{font:700 18px/1.35 var(--text)!important;color:var(--ink)}
.an-puces{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0 8px}
.an-p{font:700 11.5px var(--text);border:1px solid currentColor;border-radius:6px;padding:3px 9px;color:var(--mute)}
.an-p.up{color:var(--up)} .an-p.down{color:var(--down)} .an-p.warn{color:var(--amber)}
.an-acc{color:var(--mute)}
.an-grille{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.an-c{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.an-c .k{color:var(--brass);display:block;margin-bottom:6px}
.an-c p{margin:0;font-size:14px;line-height:1.55} .an-c ul{margin:0;padding-left:18px;font-size:14px;line-height:1.55;display:flex;flex-direction:column;gap:4px}
.an-c.an-a{border-left:3px solid var(--up)} .an-c.an-v{border-left:3px solid var(--down)} .an-c.an-d2{border-left:3px solid var(--amber)}
.an-carte .an-t{margin:0 0 8px;font:600 14.5px/1.45 var(--text)}
.an-carte .an-d{margin:8px 0 0;font-size:13px;line-height:1.5} .an-carte .an-d .k{display:block;margin-bottom:2px;color:var(--amber)}
@media (max-width:760px){.an-grille{grid-template-columns:minmax(0,1fr)}}
"""
