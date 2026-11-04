# -*- coding: utf-8 -*-
"""
MOTEUR DE DÉCISION EN DIRECT : ACHAT, VENTE ou RIEN, avec l'ordre prêt à passer
==============================================================================

Applique les règles de plan.py (les mêmes que le backtest) aux bougies du terminal, à la clôture de la
dernière bougie 15 min. Une seule réponse en haut de la page :
  - ACHAT / VENTE : un ordre limite est à placer (ou une position du plan est en cours), avec entrée, stop,
    objectifs, taille et invalidation ;
  - RIEN : le premier filtre qui bloque, et ce qui le débloquerait.
"""

import html
import json
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

import plan as P

FLECHE = {1: "▲", -1: "▼", 0: "–"}
MOT_SENS = {1: "ACHAT", -1: "VENTE", 0: "RIEN"}


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n.d."
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−")


def heure(t_horloge):
    return P.depuis_horloge(t_horloge).strftime("%H:%M")


# ---------------------------------------------------------------------------
# DONNÉES
# ---------------------------------------------------------------------------

def unites_de_temps(intra, maintenant_utc):
    """Bougies du terminal (15 min sur 60 jours, 1 h sur 2 ans) -> unités de temps du plan, sur l'horloge interne.
    Les bougies pas encore terminées sont retirées."""
    b, hb = intra.get("barres"), intra.get("heures")
    if b is None or len(b) < 200 or hb is None or len(hb) < 300:
        return None, None
    now = P.vers_horloge(pd.DatetimeIndex([maintenant_utc]))[0]
    m15 = P.sur_horloge(b)
    m15 = m15[m15.index + pd.Timedelta(minutes=15) <= now]
    h1 = P.sur_horloge(hb)
    h1 = h1[h1.index + pd.Timedelta(hours=1) <= now]
    if len(m15) < 200:
        return None, None
    tf = {"M15": P.agreger(m15, "15min"), "M30": P.agreger(m15, "30min"), "H1": P.agreger(h1, "1h"),
          "H4": P.agreger(h1, "4h"), "D": P.agreger(h1, "1D"), "W": P.agreger(h1, "W")}
    # une bougie H4, Daily ou Weekly encore en cours ne compte pas : etat_a() et armer() ne lisent que les bougies terminées
    return tf, now


def annonces_fortes(cal):
    return [e for e in (cal or []) if e.get("impact") == "High" and e.get("date") is not None]


def bloque_par_annonce(cal):
    evts = annonces_fortes(cal)

    def bloque(t_horloge):
        t = P.depuis_horloge(t_horloge)
        return any(abs((e["date"] - t).total_seconds()) <= P.NEWS_MIN * 60 for e in evts)
    return bloque


# ---------------------------------------------------------------------------
# DÉCISION
# ---------------------------------------------------------------------------

def decider(data, maintenant, ajust, fond_vals, compte):
    """data : données du terminal ; maintenant : datetime avec fuseau ; ajust : future -> prix affiché (spot) ;
    fond_vals : (taux réel 10 ans en pb sur 5 j, dollar en % sur 5 j, taux 2 ans en pb sur 5 j)."""
    intra = data.get("intraday")
    if not intra:
        return None
    try:
        tf, now = unites_de_temps(intra, maintenant)
    except Exception as e:  # le terminal ne doit jamais tomber à cause du bloc de décision
        return {"erreur": f"bougies inutilisables ({e})"}
    if tf is None:
        return {"erreur": "pas assez de bougies 15 min et 1 h"}
    ctx = P.contexte(tf)
    m15 = tf["M15"]
    n = len(m15)
    i_der = n - 1
    sp = lambda v: None if v is None else v + ajust
    px = float(ctx["c"][i_der])
    fond = P.bloc_fondamental(*fond_vals)
    tend = {k: int(ctx["tend"][k][i_der]) for k in ("W", "D", "H4", "H1")}
    cal = data.get("cal") or []
    bloque = bloque_par_annonce(cal)

    # ---- signaux de la journée de trading (règles de compte) et ordres encore actifs ----
    jour = pd.Timestamp(ctx["fins"][i_der]).normalize()
    i0 = max(30, min(int(np.searchsorted(ctx["fins"], np.datetime64(jour))), n - 1 - P.FENETRE_DECL - 8))
    T = m15.index.to_numpy().astype("datetime64[ns]").astype(np.int64)
    H, L, C = ctx["h"], ctx["l"], ctx["c"]
    vus = []
    for i in range(i0, n):
        for d in (1, -1):
            for s in P.signal(ctx, i, d, fond) or []:
                if s["entree_type"] != "ce":
                    continue
                s["filtres"] = P.filtres_ok(s) and not bloque(s["t_sig"])
                t_e, t_s, r, issue, etat = P.suivre_ordre(s, T, H, L, C, bloque=bloque)
                s.update(t_entree=t_e, t_sortie=t_s, R=r, issue=issue, etat=etat)
                vus.append(s)
    vus.sort(key=lambda s: s["t_sig"])
    pris, occupe, nb_jour, r_jour = [], None, 0, 0.0
    for s in vus:
        if not s["filtres"]:
            continue
        t_ref = s["t_entree"] or s["t_sig"]
        if occupe is not None and t_ref < occupe:
            continue
        if s["t_entree"] is not None and pd.Timestamp(s["t_entree"]).normalize() == jour:
            if nb_jour >= P.MAX_TRADES_JOUR or r_jour <= P.PERTE_MAX_JOUR_R:
                continue
            nb_jour += 1
            if s["etat"] == "fini" and s["R"] is not None:
                r_jour += s["R"]
        if s["etat"] in ("attente", "en cours", "tp1") or s["t_entree"] is not None:
            pris.append(s)
            occupe = s["t_sortie"] if s["t_sortie"] is not None else pd.Timestamp.max
    actif = next((s for s in reversed(pris) if s["etat"] in ("attente", "en cours", "tp1")), None)
    limite = nb_jour >= P.MAX_TRADES_JOUR or r_jour <= P.PERTE_MAX_JOUR_R

    # ---- état des cinq filtres maintenant ----
    t_now_paris = P.depuis_horloge(now)
    evts = annonces_fortes(cal)
    proche = next((e for e in evts if abs((e["date"] - t_now_paris).total_seconds()) <= P.NEWS_MIN * 60), None)
    suivante = next((e for e in evts if e["date"] > t_now_paris), None)
    d = tend["D"] if (tend["D"] != 0 and tend["D"] == tend["H4"]) else 0
    d_ref = d or tend["D"] or tend["H4"]
    mot = {1: "haussier", -1: "baissier", 0: "neutre"}
    st = ctx["st"]

    def niveau_cassure(tfk, sens):
        x = st[tfk]["der_h" if sens > 0 else "der_l"]
        if x is None:
            return None
        return None if x[1] else x[0]

    filtres, debloque = [], None
    # 1. annonces
    if proche:
        fin_blocage = proche["date"] + timedelta(minutes=P.NEWS_MIN)
        filtres.append(("Annonces", f"{proche['titre']} à {proche['date']:%H:%M} : zone interdite", "ko"))
        debloque = f"Attendre {fin_blocage:%H:%M}, 30 min après {proche['titre']}."
    else:
        txt = (f"Rien à ±30 min · prochaine forte : {suivante['titre']} à {suivante['date']:%H:%M}"
               if suivante and suivante["date"] - t_now_paris < timedelta(hours=24) else "Aucune annonce forte proche")
        filtres.append(("Annonces", txt, "ok"))
    # 2. fondamental
    if d_ref and fond == -d_ref:
        filtres.append(("Fondamental", f"Bloc Fed / dollar / taux {mot[fond]} : contre un {MOT_SENS[d_ref].lower()}", "ko"))
        debloque = debloque or "Le bloc Fed / dollar / taux réels doit redevenir neutre (taux et dollar qui se calment)."
    else:
        filtres.append(("Fondamental", f"Bloc Fed / dollar / taux {mot[fond]}", "ok"))
    # 3. tendance
    t_txt = " · ".join(f"{k} {FLECHE[tend[k]]}" for k in ("W", "D", "H4", "H1"))
    if not d:
        filtres.append(("Tendance", f"{t_txt} : Daily et H4 pas alignés", "ko"))
        if not debloque:
            if tend["D"]:
                niv = niveau_cassure("H4", tend["D"])
                debloque = (f"Le H4 doit recasser dans le sens du Daily : clôture H4 "
                            f"{'au-dessus de' if tend['D'] > 0 else 'sous'} {fr(sp(niv))}." if niv is not None else
                            "Le H4 doit former un nouveau point puis casser dans le sens du Daily.")
            else:
                debloque = "Le Daily n'a pas de direction : attendre une cassure de structure en Daily."
    elif tend["H1"] == -d:
        filtres.append(("Tendance", f"{t_txt} : le H1 contredit", "ko"))
        niv = niveau_cassure("H1", d)
        debloque = debloque or (f"Clôture H1 {'au-dessus de' if d > 0 else 'sous'} {fr(sp(niv))} pour réaligner le H1."
                                if niv is not None else "Le H1 doit recasser dans le sens du Daily et du H4.")
    else:
        filtres.append(("Tendance", f"{t_txt} : {MOT_SENS[d].lower()}s seulement", "ok"))
    # 4. zone
    zone_txt, zone_ok, prochaine_zone = None, False, None
    if d:
        rang = int(ctx["arme"][d][max(0, i_der - P.FENETRE_DECL):i_der + 1].max())
        if actif and actif["sens"] == d:
            zone_ok, zone_txt = True, f"Prix revenu dans une zone {P.RANGS[actif['rang_zone']]}"
        elif rang:
            zone_ok, zone_txt = True, f"Prix revenu dans une zone {P.RANGS[rang]}"
        cand = [z for z in ctx["vierges"] if z[0] == d and ((z[5] < px) if d > 0 else (z[5] > px))]
        cand.sort(key=lambda z: abs(z[5] - px))
        if cand:
            z = cand[0]
            prochaine_zone = (f"{z[2]} {P.RANGS[z[1]]} {fr(sp(z[3]), 1)}–{fr(sp(z[4]), 1)} "
                              f"(à {fr(abs(z[5] - px), 1)} $)")
    ko_avant = any(x[2] == "ko" for x in filtres)
    if ko_avant:
        filtres.append(("Zone", "Vérifiée après les filtres précédents", "wait"))
    elif zone_ok:
        filtres.append(("Zone", zone_txt, "ok"))
    else:
        filtres.append(("Zone", f"Attendre : {prochaine_zone}" if prochaine_zone else
                        "Aucune zone OB / FVG intacte dans ce sens", "wait"))
        debloque = debloque or (f"Attendre le retour du prix dans la zone {prochaine_zone}." if prochaine_zone else
                                "Attendre qu'une nouvelle zone OB / FVG se forme en M30, H1 ou H4.")
    # 5. déclencheur
    if any(x[2] in ("ko", "wait") for x in filtres):
        filtres.append(("Déclencheur", "Vérifié après les filtres précédents", "wait"))
    elif actif:
        filtres.append(("Déclencheur", f"Cassure M15 à {heure(actif['t_sig'])} qui laisse un FVG", "ok"))
    else:
        niv = niveau_cassure("M15", d)
        filtres.append(("Déclencheur", "Attendre une cassure M15 qui laisse un FVG", "wait"))
        debloque = debloque or (f"Clôture M15 {'au-dessus de' if d > 0 else 'sous'} {fr(sp(niv))} en laissant un FVG."
                                if niv is not None else "Attendre une cassure de structure M15 qui laisse un FVG.")

    # ---- verdict ----
    out = {"maj": P.depuis_horloge(ctx["fins"][i_der]), "px": sp(px), "fond": fond, "tend": tend, "filtres": filtres, "ajust": ajust,
           "nb_jour": nb_jour, "r_jour": r_jour, "rsi": float(ctx["r15"][i_der]), "erreur": None,
           "journal": [dict(s, entree=sp(s["entree"]), stop=sp(s["stop"])) for s in pris if s["t_entree"] is not None
                       and pd.Timestamp(s["t_entree"]).normalize() == jour],
           "suivante": suivante}
    if actif and not (limite and actif["etat"] == "attente"):
        a = actif
        risque = a["risque"]
        lots = compte["capital"] * compte.get("risque_pct", P.RISQUE_PCT) / 100 / (risque * compte.get("once_par_lot", 100))
        out.update(verdict=a["sens"], note=a["note"], etat=a["etat"],
                   ordre={"sens": a["sens"], "entree": sp(a["entree"]), "stop": sp(a["stop"]), "tp1": sp(a["tp1"]),
                          "tp2": sp(a["tp2"]), "tp2_liq": a["tp2_liquidite"], "risque": risque,
                          "lots": int(lots * 100) / 100, "t_sig": a["t_sig"],
                          "expire": a["t_sig"] + pd.Timedelta(minutes=P.ORDRE_VIE_MIN), "zone": P.RANGS[a["rang_zone"]],
                          "divergence": a["divergence"], "W": a["W"]})
    else:
        out.update(verdict=0, note=None, etat=None, ordre=None,
                   debloque=("Limite du jour atteinte (2 trades ou −2 R) : reprise demain." if limite else debloque))
    return out


# ---------------------------------------------------------------------------
# AFFICHAGE
# ---------------------------------------------------------------------------

def bloc_html(dec):
    if not dec:
        return ""
    if dec.get("erreur"):
        return (f'<section class="dz" data-zone="decision"><div class="dz-carte dz-rien"><span class="k">Décision</span>'
                f'<p>Moteur de décision indisponible : {esc(dec["erreur"])}.</p></div></section>')
    v, o = dec["verdict"], dec.get("ordre")
    cls = {1: "dz-achat", -1: "dz-vente", 0: "dz-rien"}[v]
    # carte principale
    if o:
        sens_txt = "Achat" if v > 0 else "Vente"
        if dec["etat"] == "attente":
            sous = f"Ordre {sens_txt.lower()} limite à placer"
            pourquoi = (f"Daily et H4 {'haussiers' if v > 0 else 'baissiers'}, prix revenu dans une zone {o['zone']}, "
                        f"puis cassure M15 à {heure(o['t_sig'])} qui a laissé un FVG. {sens_txt} limite au 50 % de ce FVG.")
            k1, v1 = "Ordre annulé si", f"pas exécuté avant {heure(o['expire'])}, ou si le prix atteint TP1 sans nous"
        elif dec["etat"] == "tp1":
            sous = "Position en cours · TP1 touché"
            pourquoi = "TP1 atteint : la moitié est encaissée. Stop au point d'entrée, le reste vise la liquidité H1."
            k1, v1 = "Stop", f"au point d'entrée, {fr(o['entree'])}"
        else:
            sous = "Position du plan en cours"
            pourquoi = f"L'ordre du signal de {heure(o['t_sig'])} a été exécuté. On laisse travailler : pas de stop déplacé avant TP1."
            k1, v1 = "Gestion", "à TP1 : fermer 50 %, stop au point d'entrée"
        if dec["etat"] == "tp1":
            k2, v2 = "Reste de la position", f"vise TP2 à {fr(o['tp2'])}"
        else:
            k2, v2 = "Invalidé si", f"clôture M15 {'sous' if v > 0 else 'au-dessus de'} {fr(o['stop'])}"
        note = f'<span class="dz-note">Note {esc(dec["note"])}</span>'
        bonus = []
        if o["W"] == v:
            bonus.append("Weekly aligné")
        if o["zone"] == "H4":
            bonus.append("zone H4")
        if o["divergence"]:
            bonus.append("divergence RSI")
        setup = f"Zone {o['zone']} + cassure M15 + FVG" + (f" · {', '.join(bonus)}" if bonus else "")
    else:
        sous, note, setup = "Aucun ordre à placer", '<span class="dz-note">Pas de setup</span>', ""
        bloquant = next((f for f in dec["filtres"] if f[2] == "ko"), None) or next((f for f in dec["filtres"] if f[2] == "wait"), None)
        pourquoi = (f"Filtre « {bloquant[0]} » : {bloquant[1]}." if bloquant else "Tous les filtres sont verts, en attente du signal.")
        k1, v1 = "Ce qui débloque", dec.get("debloque") or "—"
        k2, v2 = "Aujourd'hui", f"{dec['nb_jour']} trade{'s' if dec['nb_jour'] > 1 else ''} sur {P.MAX_TRADES_JOUR}, {fr(dec['r_jour'], 1, True)} R"
    carte = (f'<div class="dz-carte {cls}"><div class="dz-tete"><span class="k">Décision maintenant</span>'
             f'<span class="dz-maj">bougie 15 min de {dec["maj"]:%H:%M} · prix {fr(dec["px"])}</span></div>'
             f'<div class="dz-verdict"><b>{MOT_SENS[v]}</b><div>{note}<small>{esc(sous)}</small>'
             f'{f"<small>{esc(setup)}</small>" if setup else ""}</div></div>'
             f'<p class="dz-pourquoi">{esc(pourquoi)}</p>'
             f'<div class="dz-kv"><div><span class="k">{esc(k1)}</span><span>{esc(v1)}</span></div>'
             f'<div><span class="k">{esc(k2)}</span><span>{esc(v2)}</span></div></div></div>')
    # filtres
    ico = {"ok": "✓", "ko": "✗", "wait": "–"}
    lib = {"ok": "validé", "ko": "bloque", "wait": "en attente"}
    nb_ok = sum(1 for f in dec["filtres"] if f[2] == "ok")
    lis = "".join(f'<li class="dz-{s}"><span class="dz-ic" aria-hidden="true">{ico[s]}</span><div><b>{k + 1}. {esc(t)} '
                  f'<em>· {lib[s]}</em></b><small>{esc(txt)}</small></div></li>'
                  for k, (t, txt, s) in enumerate(dec["filtres"]))
    filtres = (f'<div class="dz-panneau"><div class="dz-tete"><span class="k">Filtres de ton plan</span>'
               f'<span class="dz-compte {cls}">{nb_ok} / {len(dec["filtres"])}</span></div><ol class="dz-filtres">{lis}</ol></div>')
    # ticket
    if o:
        lignes = [("Entrée", fr(o["entree"]), "brass"),
                  ("Stop · sous l'OB" if v > 0 else "Stop · au-dessus de l'OB", f"{fr(o['stop'])}  ({fr(o['risque'])} $)", "down"),
                  ("TP1 · 50 %", f"{fr(o['tp1'])}  (2 R)", "up"),
                  ("TP2 · " + ("liquidité H1" if o["tp2_liq"] else "3 R"), fr(o["tp2"]), "up"),
                  ("Taille", f'<span id="dz-lots" data-risque="{o["risque"]:.4f}">{fr(o["lots"])}</span> lot', ""),
                  ("Risque", f'<span id="dz-risque" data-risque="{o["risque"]:.4f}">{o["lots"] * o["risque"] * 100:,.0f} $</span>', "")]
        rows = "".join(f'<tr><td>{esc(k)}</td><td class="num {c}">{val}</td></tr>' for k, val, c in lignes)
        texte = (f"{'ACHAT' if v > 0 else 'VENTE'} LIMITE XAU/USD {fr(o['entree'])} | SL {fr(o['stop'])} | "
                 f"TP1 {fr(o['tp1'])} (50 %) | TP2 {fr(o['tp2'])}")
        ticket = (f'<div class="dz-panneau"><div class="dz-tete"><span class="k">Ticket d\'ordre</span>'
                  f'<span class="dz-type {cls}">{"ACHAT" if v > 0 else "VENTE"} {"LIMITE" if dec["etat"] == "attente" else "EN COURS"}</span></div>'
                  f'<table class="dz-ticket">{rows}</table>'
                  f'<p class="dz-dist" id="dz-dist" data-entree="{o["entree"]:.2f}" data-sens="{v}"></p>'
                  f'<button type="button" class="dz-copier {cls}" data-texte="{esc(texte)}">Copier l\'ordre</button></div>')
    else:
        ticket = (f'<div class="dz-panneau"><div class="dz-tete"><span class="k">Ticket d\'ordre</span>'
                  f'<span class="dz-type">EN ATTENTE</span></div><p class="dz-vide">Aucun ordre à placer.</p>'
                  f'<p class="dz-pied">Le terminal surveille les niveaux et passe en ACHAT ou VENTE dès que les cinq '
                  f'filtres sont verts. Règles : {P.RISQUE_PCT:g} % par trade, {P.MAX_TRADES_JOUR} trades par jour, '
                  f'arrêt à {fr(P.PERTE_MAX_JOUR_R, 0)} R.</p></div>')
    # journal du jour
    jr = dec.get("journal") or []
    if jr:
        items = "".join(f'<li><span class="num">{heure(s["t_sig"])}</span> {MOT_SENS[s["sens"]].lower()} {fr(s["entree"])} · '
                        f'{esc(s["issue"])}{" · " + fr(s["R"], 1, True) + " R" if s["R"] is not None and s["etat"] == "fini" else ""}</li>'
                        for s in jr)
        journal = f'<div class="dz-journal"><span class="k">Signaux du plan aujourd\'hui</span><ul>{items}</ul></div>'
    else:
        journal = ""
    return f'<section class="dz" data-zone="decision" aria-label="Décision">{carte}{filtres}{ticket}{journal}</section>'


CSS = """
/* Décision (moteur du plan) */
.dz{display:flex;flex-wrap:wrap;gap:10px;padding:10px 14px 0}
.dz .k{color:var(--mute)}
.dz-carte,.dz-panneau{background:var(--panel);border:1px solid var(--line);border-radius:6px;padding:14px 16px;min-width:0}
.dz-carte{flex:5 1 420px;display:flex;flex-direction:column;gap:12px}
.dz-panneau{flex:3 1 290px;display:flex;flex-direction:column;gap:10px}
.dz-tete{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.dz-maj{font:500 12px var(--cond);color:var(--mute)}
.dz-verdict{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.dz-verdict>b{font:700 58px/1 var(--cond);letter-spacing:.01em}
.dz-verdict div{display:flex;flex-direction:column;gap:4px}
.dz-verdict small{font:500 14px var(--cond);color:var(--ink)}
.dz-note{align-self:flex-start;font:700 12px var(--cond);padding:3px 9px;border-radius:4px;border:1px solid currentColor}
.dz-pourquoi{margin:0;font-size:15.5px;line-height:1.5;max-width:68ch}
.dz-kv{display:flex;flex-wrap:wrap;gap:8px 26px;padding-top:12px;border-top:1px solid var(--line)}
.dz-kv div{display:flex;flex-direction:column;gap:2px;min-width:0;max-width:52ch}
.dz-achat .dz-verdict>b,.dz-achat .dz-note,.dz-compte.dz-achat,.dz-type.dz-achat{color:var(--up)}
.dz-vente .dz-verdict>b,.dz-vente .dz-note,.dz-compte.dz-vente,.dz-type.dz-vente{color:var(--down)}
.dz-rien .dz-verdict>b,.dz-rien .dz-note{color:var(--steel)}
.dz-carte.dz-achat{border-color:rgba(79,184,132,.55);background:linear-gradient(180deg,rgba(79,184,132,.09),var(--panel) 65%)}
.dz-carte.dz-vente{border-color:rgba(224,96,90,.55);background:linear-gradient(180deg,rgba(224,96,90,.09),var(--panel) 65%)}
.dz-compte,.dz-type{font:600 13px var(--cond);color:var(--mute)}
.dz-filtres{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.dz-filtres li{display:flex;gap:10px;align-items:flex-start}
.dz-filtres li>div{display:flex;flex-direction:column;gap:1px;min-width:0}
.dz-filtres b{font:600 14px var(--cond)} .dz-filtres em{font-style:normal;font-weight:500}
.dz-filtres small{font-size:13px;color:var(--mute);line-height:1.35}
.dz-ic{flex:none;width:22px;height:22px;border-radius:5px;display:flex;align-items:center;justify-content:center;font:700 13px var(--cond)}
.dz-ok .dz-ic{color:var(--up);background:rgba(79,184,132,.15)} .dz-ok em{color:var(--up)}
.dz-ko .dz-ic{color:var(--down);background:rgba(224,96,90,.17)} .dz-ko em{color:var(--down)}
.dz-wait .dz-ic{color:var(--mute);background:rgba(133,148,162,.15)} .dz-wait em,.dz-wait b{color:var(--mute)}
.dz-ticket{width:100%;border-collapse:collapse}
.dz-ticket td{padding:7px 0;border-bottom:1px solid var(--band2);font-size:13.5px;color:var(--mute)}
.dz-ticket td.num{text-align:right;color:var(--ink);font:600 15px var(--cond);font-variant-numeric:tabular-nums}
.dz-ticket td.brass{color:var(--brass)} .dz-ticket td.up{color:var(--up)} .dz-ticket td.down{color:var(--down)}
.dz-dist{margin:0;font:500 13px var(--cond);color:var(--mute);min-height:1em}
.dz-copier{min-height:44px;font:700 14px var(--cond);border:0;color:var(--bg)}
.dz-copier.dz-achat{background:var(--up)} .dz-copier.dz-vente{background:var(--down)}
.dz-vide{margin:0;font:600 15px var(--cond)} .dz-pied{margin:0;font-size:13px;color:var(--mute);line-height:1.5}
.dz-journal{flex:1 1 100%;display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 16px;font-size:13px;color:var(--mute);padding:0 2px}
.dz-journal ul{display:flex;flex-wrap:wrap;gap:4px 18px;margin:0;padding:0;list-style:none}
@media (max-width:640px){.dz{padding:8px 8px 0}.dz-verdict>b{font-size:46px}}
"""

JS = r"""
(function(){
function nbFr(x, d){ return x.toLocaleString('fr-FR', {minimumFractionDigits: d, maximumFractionDigits: d}); }
function regles(){
  var T = window.terminalOr, P = {};
  var c = (T && T.ctx && T.ctx().compte) || {};
  Object.keys(c).forEach(function(k){ P[k] = c[k]; });
  try { var u = JSON.parse(localStorage.getItem('reglages-or') || '{}') || {};
        Object.keys(u).forEach(function(k){ if (u[k] !== '' && u[k] != null && !isNaN(+u[k])) P[k] = +u[k]; }); } catch(e){}
  return P;
}
function majDecision(){
  var T = window.terminalOr, px = T && T.px && T.px().prix;
  var dist = document.getElementById('dz-dist');
  if (dist && px){
    var e = parseFloat(dist.getAttribute('data-entree')), s = +dist.getAttribute('data-sens'), ecart = (px - e) * s;
    dist.textContent = 'Prix en direct ' + nbFr(px, 2) + ' · ' + (ecart > 0 ? 'à ' + nbFr(ecart, 2) + ' $ de l’entrée' : 'entrée atteinte');
  }
  var lots = document.getElementById('dz-lots');
  if (lots){
    var P = regles(), r = parseFloat(lots.getAttribute('data-risque'));
    var pct = P.risque_pct || 1, cap = P.capital || 0, opl = P.once_par_lot || 100;
    if (r > 0 && cap > 0){
      var l = Math.floor(cap * pct / 100 / (r * opl) * 100) / 100;
      lots.textContent = nbFr(l, 2);
      var rq = document.getElementById('dz-risque');
      if (rq) rq.textContent = nbFr(l * r * opl, 0) + ' $ (' + nbFr(pct, 1) + ' %)';
    }
  }
}
document.addEventListener('click', function(ev){
  var b = ev.target.closest && ev.target.closest('.dz-copier'); if (!b) return;
  var t = b.getAttribute('data-texte'), ok = function(){ var o = b.textContent; b.textContent = 'Copié'; setTimeout(function(){ b.textContent = o; }, 1500); };
  if (navigator.clipboard) navigator.clipboard.writeText(t).then(ok, function(){}); else ok();
});
majDecision(); setInterval(majDecision, 2000);
})();
"""
