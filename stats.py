# -*- coding: utf-8 -*-
"""
FONCTION STAT : les statistiques de séance (fréquences de base)
==============================================================

Ce que l'or a fait, jour après jour, sur les deux dernières années de bougies horaires : à quelle heure se font le
plus haut et le plus bas, quelle part de l'amplitude est déjà faite à une heure donnée, ce qui se passe après la
cassure du range asiatique, quels jours et quelles séances bougent le plus.

Ce sont des fréquences passées, pas des prévisions : elles donnent un ordre de grandeur (« à cette heure le plus
haut du jour tient 6 fois sur 10 »), pour ne pas viser un objectif que la journée n'a presque jamais atteint ni
acheter un plus haut qui tient le plus souvent. Les heures sont en heure de Paris ; une journée va de minuit à 23 h.
"""

import html
import math

import numpy as np
import pandas as pd

SEANCES = [("Asie", 0, 9), ("Londres", 9, 14), ("New York", 14, 23)]
JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi"]
RECENT = 60          # jours pour les grandeurs en dollars (le niveau des prix change, on ne mélange pas 2 ans)
GROS = 1.5           # « grosse journée » : amplitude supérieure à 1,5 fois la moyenne des 20 jours précédents


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=0, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def pct(x, n=None):
    return "—" if x is None or not math.isfinite(x) else f"{x * 100:.0f} %"


# ---------------------------------------------------------------------------
# CALCULS
# ---------------------------------------------------------------------------

def matrices(hb):
    """Bougies horaires (heure de Paris) -> une ligne par jour, une colonne par heure (NaN si pas de cotation)."""
    df = hb[["open", "high", "low", "close"]].dropna()
    df = df[~df.index.duplicated(keep="last")]
    jours = pd.Index(sorted(set(df.index.date)))
    pos = {j: k for k, j in enumerate(jours)}
    n = len(jours)
    M = {c: np.full((n, 24), np.nan) for c in ("open", "high", "low", "close")}
    li = np.fromiter((pos[d] for d in df.index.date), dtype=int, count=len(df))
    co = df.index.hour.to_numpy()
    for c in M:
        M[c][li, co] = df[c].to_numpy(dtype=float)
    return jours, M


def premier_valide(a):
    """Première valeur non vide de chaque ligne."""
    idx = np.argmax(np.isfinite(a), axis=1)
    return a[np.arange(len(a)), idx]


def dernier_valide(a):
    idx = a.shape[1] - 1 - np.argmax(np.isfinite(a[:, ::-1]), axis=1)
    return a[np.arange(len(a)), idx]


def calculer(intra, maintenant):
    """Renvoie le dictionnaire des statistiques, ou None si les bougies horaires manquent."""
    hb = (intra or {}).get("heures")
    if hb is None or len(hb) < 24 * 60:
        return None
    jours, M = matrices(hb)
    H, L = M["high"], M["low"]
    nb_bougies = np.isfinite(H).sum(axis=1)
    sem = np.array([j.weekday() for j in jours])
    auj = maintenant.date()
    i_auj = int(np.where(jours == auj)[0][0]) if auj in set(jours) else None
    complet = (nb_bougies >= 16) & (sem < 5)
    if i_auj is not None:
        complet[i_auj] = False                      # la journée en cours n'entre pas dans les statistiques
    if complet.sum() < 60:
        return None
    Hc, Lc = H[complet], L[complet]
    Oc, Cc = premier_valide(M["open"][complet]), dernier_valide(M["close"][complet])
    haut, bas = np.nanmax(Hc, axis=1), np.nanmin(Lc, axis=1)
    amp = haut - bas
    h_haut = np.nanargmax(Hc, axis=1)
    h_bas = np.nanargmin(Lc, axis=1)
    n = len(amp)
    semc = sem[complet]
    rec = slice(max(0, n - RECENT), n)
    # amplitude relative : rapportée à la moyenne des 20 jours précédents (comparable d'une année à l'autre)
    moy20 = pd.Series(amp).rolling(20).mean().shift(1).to_numpy()
    rel = amp / moy20

    S = {"n": int(n), "debut": jours[complet][0], "fin": jours[complet][-1], "recent": int(min(RECENT, n))}

    # ---- profil horaire
    amp_h = Hc - Lc
    S["heures"] = [{"h": h, "amp": float(np.nanmedian(amp_h[rec, h])) if np.isfinite(amp_h[rec, h]).sum() >= 10 else None,
                    "haut": float((h_haut == h).mean()), "bas": float((h_bas == h).mean())} for h in range(24)]

    # ---- amplitude du jour (60 derniers jours, en dollars)
    q = lambda a, p: float(np.nanpercentile(a, p))
    S["amplitude"] = {"med": q(amp[rec], 50), "q25": q(amp[rec], 25), "q75": q(amp[rec], 75), "q90": q(amp[rec], 90)}

    # ---- séances
    S["seances"] = []
    for nom, a, b in SEANCES:
        sa = np.nanmax(Hc[:, a:b], axis=1) - np.nanmin(Lc[:, a:b], axis=1)
        S["seances"].append({"nom": nom, "debut": a, "fin": b, "amp": q(sa[rec], 50), "part": float(np.nanmedian(sa / amp)),
                             "haut": float(((h_haut >= a) & (h_haut < b)).mean()), "bas": float(((h_bas >= a) & (h_bas < b)).mean())})

    # ---- range asiatique (minuit - 9 h) : ce que font Londres et New York ensuite
    a0, a1 = SEANCES[0][1], SEANCES[0][2]
    ah, al = np.nanmax(Hc[:, a0:a1], axis=1), np.nanmin(Lc[:, a0:a1], axis=1)
    apres_h, apres_l = Hc[:, a1:], Lc[:, a1:]
    c_h = apres_h > ah[:, None]
    c_l = apres_l < al[:, None]
    casse_h, casse_l = c_h.any(axis=1), c_l.any(axis=1)
    h1 = np.where(casse_h, np.argmax(c_h, axis=1), 99)
    l1 = np.where(casse_l, np.argmax(c_l, axis=1), 99)
    prem_h, prem_l = h1 < l1, l1 < h1
    def suite(prem, dessus):
        k = int(prem.sum())
        if k < 15:
            return None
        cont = (Cc[prem] > ah[prem]) if dessus else (Cc[prem] < al[prem])
        inv = (Cc[prem] < al[prem]) if dessus else (Cc[prem] > ah[prem])
        autre = (casse_l[prem] if dessus else casse_h[prem])
        ext = (haut[prem] - ah[prem]) if dessus else (al[prem] - bas[prem])
        return {"n": k, "continue": float(cont.mean()), "inverse": float(inv.mean()), "dedans": float(1 - cont.mean() - inv.mean()),
                "autre_cote": float(autre.mean()), "ext": float(np.nanmedian(ext / (ah[prem] - al[prem])))}
    S["asie"] = {"amp": q((ah - al)[rec], 50), "haut": float(casse_h.mean()), "bas": float(casse_l.mean()),
                 "deux": float((casse_h & casse_l).mean()), "aucun": float((~casse_h & ~casse_l).mean()),
                 "premier_haut": float(prem_h.mean()), "premier_bas": float(prem_l.mean()),
                 "suite_haut": suite(prem_h, True), "suite_bas": suite(prem_l, False)}

    # ---- jours de la semaine
    S["semaine"] = []
    for k, nom in enumerate(JOURS):
        m = semc == k
        if m.sum() < 15:
            continue
        S["semaine"].append({"nom": nom, "n": int(m.sum()), "rel": float(np.nanmedian(rel[m])),
                             "amp": float(np.nanmedian(amp[rec][semc[rec] == k])) if (semc[rec] == k).sum() >= 4 else None,
                             "hausse": float((Cc[m] > Oc[m]).mean())})

    # ---- lendemain d'une grosse journée
    gros = np.where(np.isfinite(rel[:-1]) & (rel[:-1] > GROS))[0]
    if len(gros) >= 12:
        sens = np.sign(Cc[gros] - Oc[gros])
        S["gros"] = {"n": int(len(gros)), "rel": float(np.nanmedian(rel[gros + 1])),
                     "meme_sens": float((np.sign(Cc[gros + 1] - Oc[gros + 1]) == sens).mean()),
                     "dans": float(((Hc[gros + 1].max(axis=1, initial=-np.inf, where=np.isfinite(Hc[gros + 1])) <= haut[gros])
                                    & (Lc[gros + 1].min(axis=1, initial=np.inf, where=np.isfinite(Lc[gros + 1])) >= bas[gros])).mean())}

    # ---- lecture de la journée en cours
    S["jour"] = None
    if i_auj is not None and np.isfinite(H[i_auj]).sum() >= 1 and sem[i_auj] < 5:
        ht, lt = H[i_auj], L[i_auj]
        h_now = int(np.max(np.where(np.isfinite(ht))[0]))
        if h_now >= 1:
            rh, rl = float(np.nanmax(ht[:h_now + 1])), float(np.nanmin(lt[:h_now + 1]))
            hh, hl = int(np.nanargmax(ht[:h_now + 1])), int(np.nanargmin(lt[:h_now + 1]))
            # mêmes grandeurs, à la même heure, sur les jours passés
            ph = np.fmax.accumulate(np.where(np.isfinite(Hc), Hc, -np.inf), axis=1)[:, h_now]
            pl = np.fmin.accumulate(np.where(np.isfinite(Lc), Lc, np.inf), axis=1)[:, h_now]
            ok = np.isfinite(ph) & np.isfinite(pl) & (ph > pl)
            part = np.where(ok, (ph - pl) / amp, np.nan)               # part de l'amplitude finale déjà faite
            tient_h, tient_l = haut <= ph + 1e-9, bas >= pl - 1e-9
            hh_p = np.nanargmax(np.where(np.arange(24)[None, :] <= h_now, np.where(np.isfinite(Hc), Hc, -np.inf), -np.inf), axis=1)
            hl_p = np.nanargmin(np.where(np.arange(24)[None, :] <= h_now, np.where(np.isfinite(Lc), Lc, np.inf), np.inf), axis=1)

            def tenue(tient, h_passe, h_auj):
                """Fréquence à laquelle l'extrême tient jusqu'au soir, parmi les jours où il avait le même âge (± 1 h)."""
                m = ok & (np.abs((h_now - h_passe) - (h_now - h_auj)) <= 1)
                if m.sum() < 40:
                    m = ok & ((h_now - h_passe) >= (h_now - h_auj))
                if m.sum() < 40:
                    m = ok
                return float(tient[m].mean()), int(m.sum())
            amp_now = rh - rl
            pm = float(np.nanmedian(part))
            p25, p75 = float(np.nanpercentile(part[np.isfinite(part)], 25)), float(np.nanpercentile(part[np.isfinite(part)], 75))
            passe_rec = (ph - pl)[rec][ok[rec]]
            th, nh = tenue(tient_h, hh_p, hh)
            tl, nl = tenue(tient_l, hl_p, hl)
            J = {"heure": h_now, "haut": rh, "bas": rl, "h_haut": hh, "h_bas": hl, "amp": amp_now,
                 "rang": float((passe_rec < amp_now).mean()) if len(passe_rec) >= 20 else None,
                 "amp_med_heure": float(np.median(passe_rec)) if len(passe_rec) >= 20 else None,
                 "part": pm, "finale": (amp_now / p75, amp_now / pm, amp_now / p25),
                 "tient_haut": th, "n_haut": nh, "tient_bas": tl, "n_bas": nl,
                 "tient_haut_base": float(tient_h[ok].mean()), "tient_bas_base": float(tient_l[ok].mean())}
            # range asiatique du jour
            if h_now >= a1 and np.isfinite(ht[a0:a1]).sum() >= 3:
                ja_h, ja_l = float(np.nanmax(ht[a0:a1])), float(np.nanmin(lt[a0:a1]))
                ch, cl_ = ht[a1:h_now + 1] > ja_h, lt[a1:h_now + 1] < ja_l
                i_h = int(np.argmax(ch)) if ch.any() else 99
                i_l = int(np.argmax(cl_)) if cl_.any() else 99
                cote = "haut" if i_h < i_l else ("bas" if i_l < i_h else ("deux" if i_h != 99 else None))
                J["asie"] = {"haut": ja_h, "bas": ja_l, "cote": cote, "heure": (a1 + min(i_h, i_l)) if cote else None}
            S["jour"] = J
    return S


# ---------------------------------------------------------------------------
# AFFICHAGE
# ---------------------------------------------------------------------------

def carte_maintenant(S, ajust=0.0):
    """Carte de la Vue d'ensemble : ce que disent les fréquences à cette heure-ci."""
    if not S or not S.get("jour"):
        return ""
    J, A = S["jour"], S["amplitude"]
    lig = [("Amplitude du jour", f'{fr(J["amp"], 0)} $' + (f' · rang {pct(J["rang"])}' if J.get("rang") is not None else ""),
            f'médiane d\'une journée complète : {fr(A["med"], 0)} $'),
           ("Amplitude finale probable", f'{fr(J["finale"][0], 0)} – {fr(J["finale"][2], 0)} $',
            f'à {J["heure"]} h, {pct(J["part"])} de l\'amplitude est déjà faite d\'habitude'),
           (f'Plus haut du jour ({J["h_haut"]} h) tient', pct(J["tient_haut"]), f'{J["n_haut"]} jours comparables'),
           (f'Plus bas du jour ({J["h_bas"]} h) tient', pct(J["tient_bas"]), f'{J["n_bas"]} jours comparables')]
    asie = J.get("asie")
    if asie and S.get("asie"):
        if asie["cote"] in ("haut", "bas"):
            su = S["asie"]["suite_haut" if asie["cote"] == "haut" else "suite_bas"]
            if su:
                lig.append((f'Range asiatique cassé par le {asie["cote"]} à {asie["heure"]} h',
                            pct(su["continue"]), "des cas : clôture du jour au-delà du range"))
        elif asie["cote"] is None:
            lig.append(("Range asiatique intact", pct(1 - S["asie"]["aucun"]), "des jours il finit par être cassé"))
    rows = "".join(f'<tr><td>{esc(k)}<small>{esc(sous)}</small></td><td class="num">{esc(v)}</td></tr>' for k, v, sous in lig)
    return (f'<div class="ps-carte st-carte"><div class="ps-tete"><span class="k">Statistiques du moment</span>'
            f'<a href="#analyse" data-aller="stat" class="ps-lien">détail</a></div><table class="ps-mk st-mk">{rows}</table>'
            f'<p class="ps-mini">Fréquences sur {S["n"]} jours. Un ordre de grandeur, pas une prévision.</p></div>')


def svg_heures(S, w=1000, h=320):
    """Profil de la journée : amplitude médiane par heure (barres fines), sessions en bandes discrètes,
    et fréquence du plus haut (au-dessus de l'axe) et du plus bas du jour (en dessous) en miroir."""
    hs = S["heures"]
    amax = max([x["amp"] or 0 for x in hs] + [1])
    fmax = max([max(x["haut"], x["bas"]) for x in hs] + [0.01])
    g, d, top = 78, 14, 30
    pw = w - g - d
    pas = pw / 24
    h1 = 140
    y0 = top + h1
    ym = y0 + 64                 # axe du miroir plus haut / plus bas
    hf = 40
    now = (S.get("jour") or {}).get("heure")
    out = [f'<svg viewBox="0 0 {w} {h}" class="st-svg" role="img" aria-label="Profil de la journée heure par heure">']
    for nom, a, b in SEANCES:
        x0, x1 = g + a * pas, g + b * pas
        out.append(f'<line x1="{x0 + 2:.1f}" x2="{x1 - 2:.1f}" y1="{top - 14}" y2="{top - 14}" class="st-ses"/>'
                   f'<line x1="{x0 + 2:.1f}" x2="{x0 + 2:.1f}" y1="{top - 18}" y2="{top - 10}" class="st-ses"/>'
                   f'<text x="{x0 + 6:.1f}" y="{top - 19}" class="st-se">{esc(nom.upper())}</text>')
    for k in range(4):
        v = amax * k / 3
        y = y0 - v / amax * (h1 - 6)
        out.append(f'<line x1="{g}" x2="{w - d}" y1="{y:.1f}" y2="{y:.1f}" class="st-gr"/>'
                   f'<text x="{g - 8}" y="{y + 3:.1f}" class="st-ax" text-anchor="end">{fr(v, 0)} $</text>')
    for x in hs:
        cx = g + x["h"] * pas
        if now is not None and x["h"] == now:
            out.append(f'<rect x="{cx:.1f}" y="{top - 4}" width="{pas:.1f}" height="{h - top - 18}" class="st-now"/>')
        if x["amp"]:
            ha = x["amp"] / amax * (h1 - 6)
            out.append(f'<rect x="{cx + pas * .3:.1f}" y="{y0 - ha:.1f}" width="{pas * .4:.1f}" height="{ha:.1f}" class="st-amp">'
                       f'<title>{x["h"]} h : amplitude médiane {fr(x["amp"], 1)} $</title></rect>')
        hh, hb_ = x["haut"] / fmax * hf, x["bas"] / fmax * hf
        out.append(f'<rect x="{cx + pas * .3:.1f}" y="{ym - hh:.1f}" width="{pas * .4:.1f}" height="{max(hh, .6):.1f}" class="st-up">'
                   f'<title>{x["h"]} h : plus haut du jour {x["haut"] * 100:.0f} % des jours</title></rect>'
                   f'<rect x="{cx + pas * .3:.1f}" y="{ym:.1f}" width="{pas * .4:.1f}" height="{max(hb_, .6):.1f}" class="st-dn">'
                   f'<title>{x["h"]} h : plus bas du jour {x["bas"] * 100:.0f} % des jours</title></rect>')
        if x["h"] % 2 == 0:
            out.append(f'<text x="{cx + pas / 2:.1f}" y="{h - 4}" class="st-ax" text-anchor="middle">{x["h"]:02d}</text>')
    out.append(f'<line x1="{g}" x2="{w - d}" y1="{ym}" y2="{ym}" class="st-gr"/>'
               f'<text x="{g - 8}" y="{ym - hf + 8}" class="st-ax" text-anchor="end">plus haut</text>'
               f'<text x="{g - 8}" y="{ym + hf - 2}" class="st-ax" text-anchor="end">plus bas</text>')
    out.append("</svg>")
    return "".join(out)


def bloc_stat(S):
    """Écran STAT."""
    if not S:
        return ('<section><h2>Statistiques de séance</h2><p class="pourquoi">Indisponibles : il faut au moins trois mois de '
                'bougies horaires.</p></section>')
    A = S["amplitude"]
    h = []
    intro = (f'<p class="pourquoi">Ce que l\'or a fait sur {S["n"]} journées (du {S["debut"]:%d/%m/%Y} au {S["fin"]:%d/%m/%Y}), '
             f'bougies d\'une heure, heure de Paris. Les montants en dollars viennent des {S["recent"]} derniers jours. '
             f'Ce sont des fréquences passées : elles disent ce qui est habituel, pas ce qui va arriver.</p>')
    J = S.get("jour")
    if J:
        asie = J.get("asie")
        cartes = [
            ("Amplitude du jour", f'{fr(J["amp"], 0)} $', (f'plus que {pct(J["rang"])} des journées à {J["heure"]} h' if J.get("rang") is not None else "")
             + (f' · médiane à cette heure {fr(J["amp_med_heure"], 0)} $' if J.get("amp_med_heure") else "")),
            ("Amplitude finale probable", f'{fr(J["finale"][0], 0)} – {fr(J["finale"][2], 0)} $',
             f'd\'habitude {pct(J["part"])} de l\'amplitude est faite à {J["heure"]} h · valeur centrale {fr(J["finale"][1], 0)} $'),
            (f'Le plus haut ({J["h_haut"]} h) tient jusqu\'au soir', pct(J["tient_haut"]),
             f'{J["n_haut"]} jours où le plus haut avait le même âge · toutes journées : {pct(J["tient_haut_base"])}'),
            (f'Le plus bas ({J["h_bas"]} h) tient jusqu\'au soir', pct(J["tient_bas"]),
             f'{J["n_bas"]} jours où le plus bas avait le même âge · toutes journées : {pct(J["tient_bas_base"])}')]
        if asie:
            if asie["cote"] in ("haut", "bas"):
                su = S["asie"]["suite_haut" if asie["cote"] == "haut" else "suite_bas"]
                if su:
                    cartes.append((f'Range asiatique cassé par le {asie["cote"]} à {asie["heure"]} h', pct(su["continue"]),
                                   f'des cas, clôture du jour au-delà du range · {pct(su["autre_cote"])} cassent aussi l\'autre côté'))
            elif asie["cote"] is None:
                cartes.append(("Range asiatique encore intact", pct(1 - S["asie"]["aucun"]),
                               f'des jours il finit par être cassé · plus souvent par le {"haut" if S["asie"]["premier_haut"] >= S["asie"]["premier_bas"] else "bas"} en premier'))
        kp = "".join(f'<div><span class="k">{esc(k)}</span><b>{esc(v)}</b><small>{esc(sous)}</small></div>' for k, v, sous in cartes)
        h.append(f'<section><h2>La journée en cours, à {J["heure"]} h</h2>{intro}<div class="st-kpis">{kp}</div></section>')
    else:
        h.append(f'<section><h2>Statistiques de séance</h2>{intro}<p class="vide">Pas de journée en cours (week-end ou début de séance).</p></section>')

    h.append('<section><h2>Profil de la journée, heure par heure</h2><p class="pourquoi">Barres bleues : amplitude médiane de la bougie '
             'd\'une heure (combien le prix bouge). En bas, la fréquence à laquelle le plus haut du jour (vert) et le plus bas du jour '
             '(rouge) se font dans cette heure. L\'heure en cours est surlignée.</p>'
             f'<div class="st-graph">{svg_heures(S)}</div></section>')

    se = "".join(f'<tr><th scope="row">{esc(x["nom"])}</th><td>{x["debut"]} h – {x["fin"]} h</td><td class="num">{fr(x["amp"], 0)} $</td>'
                 f'<td class="num">{pct(x["part"])}</td><td class="num">{pct(x["haut"])}</td><td class="num">{pct(x["bas"])}</td></tr>'
                 for x in S["seances"])
    h.append(f'<section><h2>Séances</h2><p class="pourquoi">Amplitude médiane de chaque séance, sa part dans l\'amplitude du jour, et la '
             f'fréquence à laquelle elle contient le plus haut ou le plus bas du jour. Journée complète : médiane {fr(A["med"], 0)} $, '
             f'une sur deux entre {fr(A["q25"], 0)} et {fr(A["q75"], 0)} $, une sur dix au-delà de {fr(A["q90"], 0)} $.</p>'
             f'<div class="tbl"><table class="st-tab"><thead><tr><th>Séance</th><th>Heures</th><th>Amplitude médiane</th>'
             f'<th>Part de la journée</th><th>Fait le plus haut</th><th>Fait le plus bas</th></tr></thead><tbody>{se}</tbody></table></div></section>')

    As = S["asie"]
    def lig_suite(nom, su):
        if not su:
            return ""
        return (f'<tr><th scope="row">{esc(nom)}</th><td class="num">{su["n"]}</td><td class="num up">{pct(su["continue"])}</td>'
                f'<td class="num">{pct(su["dedans"])}</td><td class="num down">{pct(su["inverse"])}</td><td class="num">{pct(su["autre_cote"])}</td>'
                f'<td class="num">{fr(su["ext"], 1)} ×</td></tr>')
    h.append(f'<section><h2>Range asiatique (minuit – 9 h)</h2><p class="pourquoi">Amplitude médiane : {fr(As["amp"], 0)} $. '
             f'Londres ou New York cassent le haut {pct(As["haut"])} des jours, le bas {pct(As["bas"])}, les deux {pct(As["deux"])}, '
             f'aucun {pct(As["aucun"])}. Le tableau suit le premier côté cassé : où clôture la journée, et jusqu\'où va le prix '
             f'au-delà du range (en multiples de sa hauteur).</p>'
             f'<div class="tbl"><table class="st-tab"><thead><tr><th>Premier côté cassé</th><th>Jours</th><th>Clôture au-delà (continuation)</th>'
             f'<th>Clôture dans le range</th><th>Clôture de l\'autre côté</th><th>Casse aussi l\'autre côté</th><th>Extension médiane</th></tr></thead>'
             f'<tbody>{lig_suite("Le haut", As["suite_haut"])}{lig_suite("Le bas", As["suite_bas"])}</tbody></table></div></section>')

    sm = "".join(f'<tr><th scope="row">{esc(x["nom"])}</th><td class="num">{x["n"]}</td><td class="num">{fr(x["rel"], 2)} ×</td>'
                 f'<td class="num">{fr(x["amp"], 0) + " $" if x["amp"] else "—"}</td><td class="num">{pct(x["hausse"])}</td></tr>'
                 for x in S["semaine"])
    gros = ""
    if S.get("gros"):
        G = S["gros"]
        gros = (f'<p class="pourquoi">Après une grosse journée (amplitude supérieure à {fr(GROS, 1)} fois la moyenne, {G["n"]} cas) : '
                f'le lendemain fait {fr(G["rel"], 2)} fois l\'amplitude habituelle, clôture dans le même sens {pct(G["meme_sens"])} du temps, '
                f'et reste entièrement à l\'intérieur de la veille {pct(G["dans"])} du temps.</p>')
    h.append(f'<section><h2>Jours de la semaine</h2><p class="pourquoi">Amplitude du jour rapportée à la moyenne des 20 jours précédents '
             f'(1,00 = une journée normale), amplitude médiane récente et part des journées qui clôturent en hausse.</p>'
             f'<div class="tbl"><table class="st-tab"><thead><tr><th>Jour</th><th>Jours</th><th>Amplitude relative</th>'
             f'<th>Amplitude récente</th><th>Clôture en hausse</th></tr></thead><tbody>{sm}</tbody></table></div>{gros}</section>')
    h.append('<section><h2>Comment s\'en servir</h2><p class="pourquoi">Avant de viser un objectif, compare-le à l\'amplitude finale '
             'probable : un objectif qui demande une journée deux fois plus large que d\'habitude sera rarement touché. '
             'Avant d\'acheter un nouveau plus haut en fin de journée, regarde à quelle fréquence le plus haut tient à cette heure. '
             'Ces chiffres ne remplacent pas les filtres du plan : ils servent à juger si le marché a encore de la place.</p></section>')
    return "".join(h)


CSS = """
/* Fonction STAT */
.st-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.st-kpis div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;display:flex;flex-direction:column;gap:4px;min-width:0}
.st-kpis b{font:700 24px var(--mono);letter-spacing:-.02em}
.st-kpis small{font-size:12.5px;color:var(--mute);line-height:1.45}
.st-graph{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px;overflow-x:auto}
.st-svg{display:block;width:100%;height:auto;min-width:640px}
.st-svg .st-gr{stroke:#1f232b;stroke-width:1} .st-svg .st-ax{fill:var(--mute);font:500 10px var(--mono)}
.st-svg .st-se{fill:#9ba3b1;font:600 9.5px var(--mono);letter-spacing:.12em} .st-svg .st-ses{stroke:#4b5260;stroke-width:1}
.st-svg .st-amp{fill:#7ea6d6} .st-svg .st-up{fill:#2fd27d;opacity:.85} .st-svg .st-dn{fill:#ff5a5f;opacity:.85}
.st-svg .st-now{fill:rgba(126,166,214,.08)}
.st-tab{width:100%;border-collapse:collapse;font-size:13.5px}
.st-tab th{text-align:left;font:600 10.5px var(--text);letter-spacing:.07em;text-transform:uppercase;color:var(--mute);padding:8px 10px;border-bottom:1px solid var(--line)}
.st-tab tbody th{font:600 13.5px var(--text);letter-spacing:0;text-transform:none;color:var(--ink)}
.st-tab td{padding:9px 10px;border-bottom:1px solid var(--band2)}
.st-tab td.num,.st-tab th:not(:first-child){text-align:right}
.st-tab thead th:first-child{text-align:left}
.st-mk td small{display:block;font-size:11.5px;color:var(--mute);font-weight:400}
.st-mk td.num{font-weight:600;white-space:nowrap;vertical-align:top}
"""
