# -*- coding: utf-8 -*-
"""
DONNÉES (fonction DATA) : toutes les données de marché utiles à un trader d'or, rassemblées sur un écran
=======================================================================================================

Positionnement (fonds spéculatifs COT, particuliers OANDA, ETF), volatilité (implicite, réalisée, amplitude du jour,
profil heure par heure), ratios de l'or, l'or dans les autres devises, corrélations et surprise macro.
Ces données sont déjà calculées par le terminal ; cet écran les présente.
"""

import html

import numpy as np
import pandas as pd


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if signe and abs(x) < 0.5 * 10 ** (-d):
        x = 0.0
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def coul(v):
    return "" if v is None or v == 0 else ("up" if v > 0 else "down")


def spark(s, n=90, w=110, h=26):
    if s is None:
        return ""
    s = pd.to_numeric(s, errors="coerce").dropna().tail(n)
    if len(s) < 5:
        return ""
    lo, hi = float(s.min()), float(s.max())
    r = (hi - lo) or 1
    pts = " ".join(f"{2 + i * (w - 4) / (len(s) - 1):.1f},{h - 2 - (float(v) - lo) / r * (h - 4):.1f}" for i, v in enumerate(s))
    return f'<svg class="dt-spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true"><polyline points="{pts}"/></svg>'


def jauge(pct, gauche, droite):
    """Barre 0-100 avec un repère (percentile, part d'acheteurs…)."""
    if pct is None:
        return ""
    return (f'<div class="dt-jauge"><div class="dt-j"><i style="left:{max(0, min(100, pct)):.0f}%"></i></div>'
            f'<div class="dt-jl"><span>{esc(gauche)}</span><span>{esc(droite)}</span></div></div>')


def carte(titre, corps, note=""):
    return (f'<div class="dt-carte"><div class="dt-tete">{esc(titre)}</div>{corps}'
            f'{f"<p class=dt-note>{esc(note)}</p>" if note else ""}</div>')


def kv(lignes):
    return '<table class="dt-kv">' + "".join(f'<tr><td>{esc(k)}</td><td class="num {c}">{v}</td></tr>' for k, v, c in lignes) + '</table>'


# ---------------------------------------------------------------------------
# POSITIONNEMENT
# ---------------------------------------------------------------------------

def positionnement(a, ajust):
    h = []
    c = a.get("cot")
    if c:
        lect = (c.get("lecture") or ("", ""))
        lignes = [("Position nette", f'{fr(c["net"], 0)} contrats', ""),
                  ("Variation sur la semaine", fr(c["chg"], 0, True), coul(c["chg"])),
                  ("Acheteurs / vendeurs", f'{fr(c["long"], 0)} / {fr(c["short"], 0)}', ""),
                  ("Part de l'intérêt ouvert", fr(c.get("pct_oi"), 1, unite=" %"), "")]
        lignes += [(g["lib"], f'{fr(g["net"], 0)} ({fr(g["chg"], 0, True)})', coul(g["chg"])) for g in c.get("groupes", [])]
        corps = (kv(lignes) + jauge(c["pct3a"], "peu exposés (rachat possible)", "très exposés (liquidation possible)")
                 + f'<p class="dt-lect"><b>{esc(lect[0])}</b> {esc(lect[1])}</p>')
        h.append(carte(f"Fonds spéculatifs (COT, {c.get('categorie', '')}) · {pd.Timestamp(c['date']):%d/%m}",
                       corps, f"Percentile sur 3 ans : {fr(c['pct3a'], 0)}. Publié le vendredi, données du mardi."))
    cn = a.get("carnets")
    if cn:
        pa = cn.get("part_acheteurs")
        stops_a = ", ".join(fr(x["prix"] + 0, 1) for x in cn.get("stops_acheteurs", [])) or "aucune"
        stops_v = ", ".join(fr(x["prix"] + 0, 1) for x in cn.get("stops_vendeurs", [])) or "aucune"
        lignes = [("Particuliers acheteurs", fr(pa, 0, unite=" %"), ""),
                  ("Acheteurs en perte", fr(cn.get("longs_pieges"), 0, unite=" %"), "down" if (cn.get("longs_pieges") or 0) > 50 else ""),
                  ("Vendeurs en perte", fr(cn.get("shorts_pieges"), 0, unite=" %"), "up" if (cn.get("shorts_pieges") or 0) > 50 else ""),
                  ("Prix moyen des acheteurs", fr(cn.get("entree_longs"), 1), ""),
                  ("Prix moyen des vendeurs", fr(cn.get("entree_shorts"), 1), ""),
                  ("Poches d'ordres stop au-dessus du prix", stops_a, ""),
                  ("Poches d'ordres stop sous le prix", stops_v, "")]
        lect = ("La foule est très acheteuse : signal contrarien plutôt baissier, et beaucoup de stops sous le prix à aller chercher."
                if (pa or 50) >= 65 else "La foule est très vendeuse : signal contrarien plutôt haussier." if (pa or 50) <= 35
                else "Positionnement de la foule équilibré : pas de signal contrarien.")
        h.append(carte("Particuliers (carnets OANDA)", kv(lignes) + jauge(pa, "vendeurs", "acheteurs") + f'<p class="dt-lect">{esc(lect)}</p>',
                       "Les particuliers ont souvent tort aux extrêmes ; leurs stops attirent le prix (liquidité)."))
    g = a.get("gld")
    if g:
        h.append(carte(f"ETF GLD · {pd.Timestamp(g['date']):%d/%m}", kv([
            ("Avoirs", f'{fr(g["t"], 1)} t', ""), ("Sur 5 jours", f'{fr(g["d5"], 1, True)} t ({fr(g.get("usd5"), 2, True)} Md$)', coul(g["d5"])),
            ("Sur 20 jours", f'{fr(g["d20"], 1, True)} t ({fr(g.get("usd20"), 2, True)} Md$)', coul(g["d20"]))]),
            "Entrées = investisseurs qui achètent de l'or physique via l'ETF ; sorties = ils allègent."))
    st = a.get("structure")
    if st:
        h.append(carte("Or physique (courbe des futures)", kv([
            ("Coût de portage", fr(st["ref"]["portage"], 2, unite=" %"), ""), ("SOFR", fr(st["sofr"], 2, unite=" %"), ""),
            ("Écart", fr(st["ref"]["vs_sofr"], 2, True, " pt"), "up" if st["tension"] else ""),
            ("Location implicite de l'or", fr(st.get("location_implicite"), 2, unite=" %"), "")]),
            "Tension physique (or rare à emprunter)." if st["tension"] else "Courbe normale : pas de pénurie physique."))
    return h


# ---------------------------------------------------------------------------
# VOLATILITÉ
# ---------------------------------------------------------------------------

def profil_svg(pv, w=520, h=120):
    moy, auj = pv.get("moy"), pv.get("auj")
    if moy is None:
        return ""
    m = np.nan_to_num(np.asarray(moy, dtype=float))
    a_ = np.nan_to_num(np.asarray(auj, dtype=float)) if auj is not None else np.zeros_like(m)
    top = max(m.max(), a_.max()) or 1
    bw = w / len(m)
    barres = "".join(f'<rect x="{i * bw:.1f}" y="{h - 16 - v / top * (h - 22):.1f}" width="{bw * .8:.1f}" height="{v / top * (h - 22):.1f}" class="m"/>'
                     for i, v in enumerate(m))
    barres += "".join(f'<rect x="{i * bw + bw * .2:.1f}" y="{h - 16 - v / top * (h - 22):.1f}" width="{bw * .45:.1f}" height="{v / top * (h - 22):.1f}" class="a"/>'
                      for i, v in enumerate(a_) if v > 0)
    lab = "".join(f'<text x="{k * 4 * bw:.1f}" y="{h - 3}" class="ax">{k} h</text>' for k in range(0, 24, 3))
    return (f'<svg viewBox="0 0 {w} {h}" class="dt-profil" role="img" aria-label="Amplitude moyenne de l\'or par tranche de 15 minutes">'
            f'{barres}{lab}</svg><div class="dt-leg"><span class="m">moyenne des séances</span><span class="a">aujourd\'hui</span></div>')


def volatilite(a):
    h = []
    v, s = a.get("vol"), a.get("seance") or {}
    lignes = []
    if v:
        lignes += [("Volatilité implicite (GVZ)", fr(v.get("implicite"), 1, unite=" %"), ""),
                   ("Volatilité réalisée 20 jours", fr(v.get("realisee"), 1, unite=" %"), ""),
                   ("Prime implicite − réalisée", fr(v.get("prime"), 1, True, " pt"), "")]
    if s.get("atr"):
        lignes += [("ATR quotidien (14 j)", fr(s["atr"], 1, unite=" $"), ""),
                   ("Amplitude du jour consommée", fr(s.get("pct_atr"), 0, unite=" %"), "down" if (s.get("pct_atr") or 0) >= 90 else "")]
    if a.get("range_jour"):
        lignes.append(("Amplitude attendue (GVZ)", f'± {fr(a["range_jour"], 0)} $', ""))
    if lignes:
        lect = ""
        if (s.get("pct_atr") or 0) >= 90:
            lect = "Plus de 90 % de l'amplitude habituelle est déjà faite : les extensions deviennent rares, prudence sur les cassures tardives."
        elif v and v.get("prime") is not None and v["prime"] > 3:
            lect = "Les options paient plus que la volatilité réelle : le marché se protège, un mouvement violent est redouté."
        h.append(carte("Volatilité", kv(lignes) + (f'<p class="dt-lect">{esc(lect)}</p>' if lect else "")))
    pv = a.get("profil_vol")
    if pv:
        h.append(carte(f"Amplitude de l'or heure par heure (Paris, {pv.get('jours', 0)} séances)", profil_svg(pv),
                       f"Aujourd'hui, l'or bouge {fr(pv.get('ratio'), 2)} fois la normale à cette heure-ci. Trader quand le marché bouge, "
                       "éviter les heures creuses où les cassures échouent souvent."))
    return h


# ---------------------------------------------------------------------------
# RATIOS, DEVISES, CORRÉLATIONS, SURPRISE MACRO
# ---------------------------------------------------------------------------

def ratios(a):
    rs = a.get("ratios") or []
    if not rs:
        return []
    lig = "".join(f'<tr><th scope="row">{esc(r["lib"])}</th><td class="num">{fr(r["val"], 2)}</td>'
                  f'<td class="num {coul(r.get("d20"))}">{fr(r.get("d20"), 1, True, " %")}</td>'
                  f'<td class="num">{fr(r.get("pct1a"), 0)}</td><td>{spark(r.get("serie"))}</td>'
                  f'<td class="dt-expl">{esc(r.get("expl", ""))}</td></tr>' for r in rs)
    return ['<div class="dt-plein">' + carte("Ratios de l'or", f'<div class="dt-defil"><table class="dt-tab"><thead><tr><th>Ratio</th><th>Valeur</th><th>20 j</th>'
                  f'<th>Percentile 1 an</th><th>3 mois</th><th>Lecture</th></tr></thead><tbody>{lig}</tbody></table></div>') + '</div>']


def devises(a):
    dv = a.get("devises") or []
    if not dv:
        return []
    lig = "".join(f'<tr><th scope="row">Or en {esc(d["nom"].lower())}</th><td class="num">{fr(d["prix"], 0)}</td>'
                  f'<td class="num {coul(d["d5"])}">{fr(d["d5"], 2, True, " %")}</td>'
                  f'<td class="num {coul(d["d20"])}">{fr(d["d20"], 2, True, " %")}</td></tr>' for d in dv)
    return [carte("L'or dans les autres devises", f'<div class="dt-defil"><table class="dt-tab"><thead><tr><th>Devise</th><th>Cours</th>'
                  f'<th>5 j</th><th>20 j</th></tr></thead><tbody>{lig}</tbody></table></div>',
                  "Si l'or monte dans toutes les devises, c'est une vraie demande d'or ; s'il ne monte qu'en dollars, c'est surtout le dollar qui baisse.")]


def correlations(a):
    cs = a.get("correlations") or []
    if not cs:
        return []
    def bar(v):
        if v is None:
            return ""
        return (f'<span class="dt-cor"><i class="{"up" if v >= 0 else "down"}" style="{"left:50%" if v >= 0 else "right:50%"};'
                f'width:{abs(v) * 50:.0f}%"></i></span>')
    lig = "".join(f'<tr><th scope="row">{esc(c["nom"])}</th><td class="num">{fr(c.get("c20"), 2, True)}</td><td>{bar(c.get("c20"))}</td>'
                  f'<td class="num">{fr(c.get("c60"), 2, True)}</td></tr>' for c in cs)
    return [carte("Corrélations de l'or", f'<table class="dt-tab"><thead><tr><th>Avec</th><th>20 j</th><th></th><th>60 j</th></tr></thead>'
                  f'<tbody>{lig}</tbody></table>', "−1 : bougent en sens inverse ; +1 : ensemble. Un changement net entre 60 j et 20 j "
                  "signale que le moteur de l'or change.")]


def surprise(a):
    s = a.get("surprise")
    if not s:
        return []
    lig = "".join(f'<tr><td>{esc(x["titre"])}</td><td class="num">{esc(x.get("prevision") or "—")}</td>'
                  f'<td class="num">{esc(x.get("reel") or "—")}</td><td class="{coul(-x["sens"]) if x.get("sens") else ""}">'
                  f'{"dure" if x.get("sens", 0) > 0 else "souple" if x.get("sens", 0) < 0 else "conforme"}</td></tr>' for x in s["lignes"])
    sc = s.get("score")
    return [carte(f"Surprise macro US (indice maison : {fr(sc, 2, True)})",
                  f'<table class="dt-tab"><thead><tr><th>Annonce</th><th>Prévu</th><th>Réel</th><th>Surprise</th></tr></thead><tbody>{lig}</tbody></table>',
                  "Positif : les chiffres sortent plus forts que prévu (Fed plus dure, pression sur l'or). Négatif : l'inverse.")]


def bloc_donnees(a, ajust=0.0):
    pos, vol = positionnement(a, ajust), volatilite(a)
    autres = ratios(a) + devises(a) + correlations(a) + surprise(a)
    if not (pos or vol or autres):
        return '<section><h2>Données</h2><p class="vide">Données indisponibles.</p></section>'
    return (f'<section class="dt"><h2>Positionnement</h2><p class="pourquoi">Qui est acheteur, qui est vendeur, qui est en perte.</p>'
            f'<div class="dt-grille">{"".join(pos)}</div></section>'
            f'<section class="dt"><h2>Volatilité</h2><div class="dt-grille">{"".join(vol)}</div></section>'
            f'<section class="dt"><h2>Relations de l\'or avec les autres marchés</h2><div class="dt-grille dt-large">{"".join(autres)}</div></section>')


CSS = """
/* Données (DATA) */
.dt-grille{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:12px;align-items:start}
.dt-grille.dt-large{grid-template-columns:repeat(auto-fit,minmax(460px,1fr))}
.dt-carte{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0;overflow:hidden}
.dt-plein{grid-column:1/-1;min-width:0}
.dt-defil{overflow-x:auto;max-width:100%}
.dt-kv td.num{white-space:normal;word-break:break-word}
.dt-tete{font:600 10.5px var(--text);letter-spacing:.08em;text-transform:uppercase;color:var(--brass);margin-bottom:8px}
.dt-kv{width:100%;border-collapse:collapse;font-size:13px}
.dt-kv td{padding:5px 0;border-bottom:1px solid var(--band2)} .dt-kv td.num{text-align:right;font-family:var(--mono)}
.dt-lect{margin:8px 0 0;font-size:13px;line-height:1.5} .dt-note{margin:8px 0 0;font-size:12px;color:var(--mute);line-height:1.45}
.dt-jauge{margin-top:10px} .dt-j{position:relative;height:6px;border-radius:3px;background:linear-gradient(90deg,var(--up),#555 50%,var(--down))}
.dt-j i{position:absolute;top:-4px;width:3px;height:14px;margin-left:-1px;background:var(--ink);border-radius:1px}
.dt-jl{display:flex;justify-content:space-between;font-size:11px;color:var(--mute);margin-top:3px}
.dt-tab{width:100%;border-collapse:collapse;font-size:13px}
.dt-tab th{text-align:left;font:600 10.5px var(--text);letter-spacing:.06em;text-transform:uppercase;color:var(--mute);padding:6px 8px;border-bottom:1px solid var(--line)}
.dt-tab tbody th{font:500 13px var(--text);text-transform:none;letter-spacing:0;color:var(--ink);white-space:nowrap}
.dt-tab td{padding:6px 8px;border-bottom:1px solid var(--band2)} .dt-tab td.num{font-family:var(--mono);text-align:right;white-space:nowrap}
.dt-expl{font-size:12px;color:var(--mute);min-width:260px;white-space:normal;line-height:1.45}
.dt-spark polyline{fill:none;stroke:var(--brass);stroke-width:1.3}
.dt-cor{position:relative;display:inline-block;width:110px;height:7px;background:var(--band2);border-radius:4px;vertical-align:middle}
.dt-cor i{position:absolute;top:0;bottom:0;border-radius:4px} .dt-cor i.up{background:var(--up)} .dt-cor i.down{background:var(--down)}
.dt-profil{display:block;width:100%;height:auto} .dt-profil .m{fill:#3a3a42} .dt-profil .a{fill:var(--brass)}
.dt-profil .ax{fill:var(--mute);font:500 10px var(--mono)}
.dt-leg{display:flex;gap:14px;font-size:11.5px;color:var(--mute);margin-top:4px}
.dt-leg span::before{content:"";display:inline-block;width:9px;height:9px;margin-right:5px;vertical-align:-1px}
.dt-leg .m::before{background:#3a3a42} .dt-leg .a::before{background:var(--brass)}
@media (max-width:640px){.dt-grille.dt-large{grid-template-columns:minmax(0,1fr)}}
"""
