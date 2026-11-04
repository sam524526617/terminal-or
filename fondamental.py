# -*- coding: utf-8 -*-
"""
ONGLET FONDAMENTAL : la lecture macro d'un desk or, reliée à la décision du plan
================================================================================

Ordre de lecture, du plus décisif au plus contextuel :
  1. Verdict du plan  : le bloc Fed / dollar / taux réels, le SEUL fondamental qui entre dans la décision,
                        avec ses trois composantes, leurs seuils et ce qui le ferait basculer.
  2. Ce qui fait bouger l'or en ce moment : régression glissante de l'or sur les taux réels et le dollar
                        (sensibilités, part expliquée, mouvement attendu contre mouvement réel). Quand la macro
                        n'explique plus l'or, ce sont les flux qui mènent, et le filtre a moins de poids.
  3. Fed            : ce que les futures fed funds anticipent, réunion par réunion.
  4. Moteurs        : taux, dollar, inflation anticipée, pétrole, actions, argent, avec leur effet sur l'or.
  5. Flux et positionnement : ETF, fonds (COT), tension sur l'or physique, liquidité.
  6. Stress et risque : VIX, volatilité obligataire, crédit, conditions financières.
  7. Macro et agenda : derniers chiffres, annonces à venir.
Tout ce qui n'est pas dans la partie 1 est du contexte : ça aide à comprendre, ça ne décide pas.
"""

import html
from datetime import timedelta

import numpy as np
import pandas as pd

import plan as P

SEUIL_REEL_PB, SEUIL_DOLLAR_PCT, SEUIL_US2_PB = 8, 0.5, 8     # mêmes seuils que plan.bloc_fondamental


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n.d."
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def propre(s):
    if s is None:
        return None
    s = pd.to_numeric(s, errors="coerce").dropna()
    return s if len(s) else None


def var(s, n, mode):
    s = propre(s)
    if s is None or len(s) <= n:
        return None
    a, b = float(s.iloc[-1 - n]), float(s.iloc[-1])
    if mode == "pct":
        return (b / a - 1) * 100 if a else None
    if mode == "pb":
        return (b - a) * 100
    return b - a


def niveau(s):
    s = propre(s)
    return float(s.iloc[-1]) if s is not None else None


def zscore_var(s, n, mode, histo=500):
    """Variation sur n jours rapportée à sa dispersion habituelle (écart-type des variations sur n jours)."""
    s = propre(s)
    if s is None or len(s) < n + 60:
        return None
    s = s.tail(histo + n)
    ch = (s / s.shift(n) - 1) * 100 if mode == "pct" else (s - s.shift(n)) * (100 if mode == "pb" else 1)
    ch = ch.dropna()
    sd = float(ch.std())
    if not sd or not np.isfinite(sd) or sd < 1e-9:
        return None
    z = float(ch.iloc[-1]) / sd
    return z if abs(z) < 15 else None


def spark(s, n=90, w=110, h=28):
    s = propre(s)
    if s is None or len(s) < 5:
        return ""
    s = s.tail(n)
    lo, hi = float(s.min()), float(s.max())
    r = (hi - lo) or 1
    pts = " ".join(f"{2 + i * (w - 6) / (len(s) - 1):.1f},{h - 3 - (float(v) - lo) / r * (h - 6):.1f}" for i, v in enumerate(s))
    lx, ly = pts.split(" ")[-1].split(",")
    return (f'<svg class="fd-spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="1.3"/>'
            f'<circle cx="{lx}" cy="{ly}" r="2.2" fill="currentColor"/></svg>')


# ---------------------------------------------------------------------------
# 1. VERDICT DU PLAN
# ---------------------------------------------------------------------------

def filtre_plan(y, f):
    comps = []
    for nom, s, n, mode, seuil, unite, lect_h, lect_b in (
            ("Taux réel 10 ans", f.get("reel10"), 5, "pb", SEUIL_REEL_PB, " pb",
             "en hausse : détenir de l'or (qui ne rapporte rien) coûte plus cher", "en baisse : l'or redevient attractif"),
            ("Dollar (DXY)", y.get("dxy"), 5, "pct", SEUIL_DOLLAR_PCT, " %",
             "en hausse : l'or coûte plus cher hors des États-Unis", "en baisse : soutien direct à l'or"),
            ("Taux 2 ans (Fed)", f.get("us2"), 5, "pb", SEUIL_US2_PB, " pb",
             "en hausse : le marché anticipe une Fed plus dure", "en baisse : le marché anticipe une Fed plus souple")):
        v = var(s, n, mode)
        vote = 0 if v is None else (-1 if v >= seuil else (1 if v <= -seuil else 0))
        lect = (lect_h if vote < 0 else lect_b if vote > 0 else "dans le bruit, pas de signal")
        comps.append({"nom": nom, "v": v, "seuil": seuil, "unite": unite, "vote": vote, "lecture": lect,
                      "jauge": None if v is None else max(-1.0, min(1.0, v / (2 * seuil))), "serie": s})
    total = sum(c["vote"] for c in comps)
    bloc = P.bloc_fondamental(*(c["v"] for c in comps))
    # ce qui ferait basculer le bloc
    if bloc == 0:
        bascule = ("Le bloc devient haussier si deux des trois composantes passent du côté favorable à l'or "
                   "(taux réels ou 2 ans en baisse de 8 pb, dollar en baisse de 0,5 % sur 5 jours), baissier dans le cas inverse.")
    else:
        sens = "baisse" if bloc > 0 else "hausse"
        bascule = (f"Le bloc redevient neutre dès qu'il ne reste plus qu'une composante {'favorable' if bloc > 0 else 'défavorable'} : "
                   f"il suffit que les taux ou le dollar arrêtent leur {sens} sur 5 jours.")
    effet = {1: "bloque les VENTES, laisse passer les achats", -1: "bloque les ACHATS, laisse passer les ventes",
             0: "ne bloque rien : achats et ventes autorisés si la technique est d'accord"}[bloc]
    return {"comps": comps, "total": total, "bloc": bloc, "effet": effet, "bascule": bascule}


# ---------------------------------------------------------------------------
# 2. RÉGIME : CE QUI FAIT BOUGER L'OR
# ---------------------------------------------------------------------------

def regression(df, cols):
    X = np.column_stack([np.ones(len(df))] + [df[c].to_numpy() for c in cols])
    yv = df["or"].to_numpy()
    coef, *_ = np.linalg.lstsq(X, yv, rcond=None)
    res = yv - X @ coef
    ss_tot = float(((yv - yv.mean()) ** 2).sum())
    r2 = 1 - float((res ** 2).sum()) / ss_tot if ss_tot else 0.0
    dl = max(1, len(df) - X.shape[1])
    try:
        cov = np.linalg.inv(X.T @ X) * float((res ** 2).sum()) / dl
        t = coef / np.sqrt(np.clip(np.diag(cov), 1e-18, None))
    except np.linalg.LinAlgError:
        t = np.zeros_like(coef)
    return coef, t, r2


def regime(y, f):
    or_, reel, dxy = propre(y.get("or")), propre(f.get("reel10")), propre(y.get("dxy"))
    if or_ is None or reel is None or dxy is None:
        return None
    norm = lambda s: s.set_axis(pd.DatetimeIndex(s.index).tz_localize(None).normalize())
    df = pd.concat({"or": norm(or_).pct_change() * 100, "reel": norm(reel).diff() * 100,
                    "dxy": norm(dxy).pct_change() * 100}, axis=1, join="inner").dropna()
    df = df[~df.index.duplicated()]
    if len(df) < 80:
        return None
    cols = ["reel", "dxy"]
    c60, t60, r2_60 = regression(df.tail(60), cols)
    c2a, t2a, r2_2a = regression(df.tail(500), cols)
    sd = df.tail(60)[cols].std()
    poids = {k: abs(c60[i + 1]) * float(sd[k]) for i, k in enumerate(cols)}
    dominant = max(poids, key=poids.get)
    noms = {"reel": "les taux réels", "dxy": "le dollar"}

    def bilan(n):
        bloc = df.tail(n)
        contrib = {k: float(c60[i + 1] * bloc[k].sum()) for i, k in enumerate(cols)}
        attendu, reel_ = sum(contrib.values()), float(bloc["or"].sum())
        return {"contrib": contrib, "attendu": attendu, "reel": reel_, "residu": reel_ - attendu}
    b5, b20 = bilan(5), bilan(20)
    corr20 = float(df.tail(20)["or"].corr(df.tail(20)["reel"]))
    corr_2a = float(df.tail(500)["or"].corr(df.tail(500)["reel"]))
    # lecture
    if r2_60 < 0.15:
        lect = ("L'or est décroché des taux réels et du dollar : la macro explique moins de 15 % de ses mouvements sur 60 jours. "
                "Ce sont les flux qui mènent (banques centrales, ETF, géopolitique) : le filtre taux / dollar a moins de poids, "
                "la technique compte davantage.")
    elif r2_60 < 0.35:
        lect = (f"L'or suit partiellement la macro ({fr(r2_60 * 100, 0)} % de ses mouvements expliqués sur 60 jours), "
                f"surtout via {noms[dominant]}. Le filtre fondamental reste utile mais n'est pas tout.")
    else:
        lect = (f"L'or est piloté par la macro ({fr(r2_60 * 100, 0)} % de ses mouvements expliqués sur 60 jours), "
                f"d'abord par {noms[dominant]}. Le filtre fondamental est pleinement pertinent : ne pas trader contre lui.")
    if b20["residu"] >= 2:
        lect_flux = (f"Sur 20 jours, l'or a fait {fr(b20['residu'], 1, True)} % de plus que ce que la macro explique : "
                     f"des acheteurs structurels sont présents (banques centrales, ETF, demande de refuge).")
    elif b20["residu"] <= -2:
        lect_flux = (f"Sur 20 jours, l'or a fait {fr(b20['residu'], 1, True)} % de moins que ce que la macro explique : "
                     f"des vendeurs pèsent au-delà des taux et du dollar (liquidations, prises de bénéfices).")
    else:
        lect_flux = "Sur 20 jours, l'or fait à peu près ce que la macro explique : pas de flux anormal visible."
    rupture = (np.sign(corr20) != np.sign(corr_2a) and abs(corr20) > 0.2)
    return {"r2_60": r2_60, "r2_2a": r2_2a, "beta_reel": float(c60[1]), "beta_dxy": float(c60[2]),
            "t_reel": float(t60[1]), "t_dxy": float(t60[2]), "beta_reel_2a": float(c2a[1]), "beta_dxy_2a": float(c2a[2]),
            "dominant": noms[dominant], "b5": b5, "b20": b20, "corr20": corr20, "corr_2a": corr_2a,
            "rupture": rupture, "lecture": lect, "lecture_flux": lect_flux, "n": len(df)}


# ---------------------------------------------------------------------------
# 3 À 7. FED, MOTEURS, FLUX, STRESS, MACRO
# ---------------------------------------------------------------------------

def moteurs(y, f):
    """(nom, série, mode, effet sur l'or quand ça monte, unité, décimales, explication)."""
    defs = [
        ("Taux réel 10 ans", f.get("reel10"), "pb", -1, " %", 2, "rendement après inflation : le coût d'opportunité de l'or"),
        ("Taux 2 ans", f.get("us2"), "pb", -1, " %", 2, "reflète les anticipations sur la Fed"),
        ("Taux 10 ans", y.get("us10"), "pb", -1, " %", 2, "taux nominal long"),
        ("Inflation anticipée 10 ans", f.get("be10"), "pb", 1, " %", 2, "demande de protection contre l'inflation"),
        ("Dollar (DXY)", y.get("dxy"), "pct", -1, "", 2, "l'or est coté en dollars"),
        ("Prime de terme 10 ans", f.get("prime_terme"), "pb", 1, " %", 2, "défiance envers la dette US"),
        ("Pétrole (Brent)", y.get("brent"), "pct", 0, " $", 1, "inflation ou géopolitique : effet ambigu"),
        ("S&P 500", y.get("spx"), "pct", 0, "", 0, "appétit pour le risque"),
        ("Argent", y.get("argent"), "pct", 1, " $", 2, "métal cousin : confirme ou non le mouvement de l'or"),
    ]
    out = []
    for nom, s, mode, effet, unite, d, expl in defs:
        s = propre(s)
        if s is None:
            continue
        v5, v20, z = var(s, 5, mode), var(s, 20, mode), zscore_var(s, 5, mode)
        impact = 0
        if effet and v5 is not None and z is not None and abs(z) >= 0.5:
            impact = effet * (1 if v5 > 0 else -1)
        out.append({"nom": nom, "niv": float(s.iloc[-1]), "v5": v5, "v20": v20, "z": z, "mode": mode, "impact": impact,
                    "unite": unite, "d": d, "expl": expl, "serie": s})
    return out


def stress(y, f):
    vix, move, hy, nfci = (propre(y.get("vix")), propre(y.get("move")), propre(f.get("hy")), propre(f.get("nfci")))
    lignes = []
    if vix is not None:
        lignes.append(("VIX (actions)", fr(niveau(vix), 1), fr(var(vix, 5, "abs"), 1, True), niveau(vix) >= 25, vix))
    if move is not None:
        lignes.append(("MOVE (obligations)", fr(niveau(move), 0), fr(var(move, 5, "abs"), 0, True), niveau(move) >= 120, move))
    if hy is not None:
        lignes.append(("Spread crédit high yield", fr(niveau(hy), 2, unite=" %"), fr(var(hy, 5, "pb"), 0, True, " pb"),
                       (var(hy, 5, "pb") or 0) >= 30, hy))
    if nfci is not None:
        lignes.append(("Conditions financières (Chicago Fed)", fr(niveau(nfci), 2), fr(var(nfci, 4, "abs"), 2, True),
                       niveau(nfci) > 0, nfci))
    tendu = sum(1 for x in lignes if x[3])
    etat = ("Stress de marché : l'or peut d'abord baisser (ventes pour couvrir des appels de marge) avant de jouer son rôle de refuge."
            if tendu >= 2 else "Tension sur un indicateur : à surveiller." if tendu == 1 else
            "Pas de stress de marché : l'or évolue sur ses moteurs habituels.")
    return {"lignes": lignes, "tendu": tendu, "etat": etat}


JOURS = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def date_courte(d):
    return f"{JOURS[d.weekday()]} {d:%d/%m %H:%M}"


def agenda(cal, now, jours=7):
    fin = now + timedelta(days=jours)
    return [e for e in (cal or []) if e.get("date") is not None and now - timedelta(hours=1) <= e["date"] <= fin][:14]


def analyser(y, f, a, cal, now):
    out = {"filtre": filtre_plan(y, f)}
    try:
        out["regime"] = regime(y, f)
    except Exception:
        out["regime"] = None
    out["moteurs"] = moteurs(y, f)
    out["stress"] = stress(y, f)
    out["fed"] = a.get("fedwatch")
    out["us2"] = (niveau(f.get("us2")), var(f.get("us2"), 5, "pb"), var(f.get("us2"), 20, "pb"))
    out["cot"], out["gld"], out["structure"], out["liq"] = a.get("cot"), a.get("gld"), a.get("structure"), a.get("liquidite")
    out["macro"] = a.get("macro") or []
    out["agenda"] = agenda(cal, now)
    out["synthese"] = synthese(out)
    return out


def synthese(F):
    fl, rg = F["filtre"], F.get("regime")
    mot = {1: "HAUSSIER", -1: "BAISSIER", 0: "NEUTRE"}[fl["bloc"]]
    phrases = [f"Fondamental du plan {mot.lower()} pour l'or : il {fl['effet'].split(',')[0]}."]
    if rg:
        if rg["r2_60"] < 0.15:
            phrases.append("L'or est décroché des taux et du dollar : les flux mènent, la technique prime.")
        else:
            phrases.append(f"Moteur dominant : {rg['dominant']} ({fr(rg['r2_60'] * 100, 0)} % des mouvements expliqués).")
        if abs(rg["b20"]["residu"]) >= 2:
            phrases.append(("Acheteurs" if rg["b20"]["residu"] > 0 else "Vendeurs") + " au-delà de la macro sur 20 jours "
                           f"({fr(rg['b20']['residu'], 1, True)} %).")
    fw = F.get("fed")
    if fw and fw.get("reunions"):
        r = fw["reunions"][0]
        p = max(("baisse", r["p_baisse"]), ("hausse", r["p_hausse"]), ("statu quo", r["p_statu"]), key=lambda x: x[1])
        phrases.append(f"Fed du {r['date']:%d/%m} : {p[0]} à {fr(p[1] * 100, 0)} %.")
    if F["stress"]["tendu"] >= 2:
        phrases.append("Stress de marché en cours.")
    return " ".join(phrases)


# ---------------------------------------------------------------------------
# AFFICHAGE
# ---------------------------------------------------------------------------

COUL = {1: "up", -1: "down", 0: "flat"}


def jauge(j, seuil_txt):
    if j is None:
        return '<div class="fd-jauge"></div>'
    pos = 50 + j * 50
    return (f'<div class="fd-jauge" title="{esc(seuil_txt)}"><i class="s1"></i><i class="s2"></i>'
            f'<b style="left:{pos:.1f}%"></b></div>')


def bloc_html(F, ia_html=""):
    if not F:
        return ""
    fl = F["filtre"]
    mot = {1: "HAUSSIER", -1: "BAISSIER", 0: "NEUTRE"}[fl["bloc"]]
    comps = "".join(
        f'<div class="fd-comp {COUL[c["vote"]]}"><div class="fd-ct"><span>{esc(c["nom"])}</span>'
        f'<b>{fr(c["v"], 2 if c["unite"] == " %" else 0, True, c["unite"])}</b></div>'
        f'{jauge(c["jauge"], "seuils ±" + str(c["seuil"]) + c["unite"])}'
        f'<small>sur 5 jours · vote si ±{fr(c["seuil"], 1 if c["unite"] == " %" else 0)}{c["unite"]} · '
        f'<em>{"+1 pour l" + chr(39) + "or" if c["vote"] > 0 else "−1 pour l" + chr(39) + "or" if c["vote"] < 0 else "0"}</em></small>'
        f'<p>{esc(c["nom"])} {esc(c["lecture"])}.</p></div>' for c in fl["comps"])
    h = [f'<section class="fd"><div class="fd-hero {COUL[fl["bloc"]]}"><div class="fd-hero-g"><span class="k">Fondamental du plan</span>'
         f'<b>{mot}</b><span class="fd-effet">pour l\'or · {esc(fl["effet"])}</span></div>'
         f'<p class="fd-synth">{esc(F["synthese"])}</p></div>'
         f'<p class="pourquoi">Le seul fondamental qui entre dans la décision : taux réels, dollar et taux 2 ans comptés comme <b>un</b> '
         f'signal, parce qu\'ils bougent ensemble. Il faut deux composantes sur trois du même côté pour qu\'il penche. '
         f'{esc(fl["bascule"])}</p><div class="fd-comps">{comps}</div></section>']

    rg = F.get("regime")
    if rg:
        def barre(v, m=3.0):
            v = max(-m, min(m, v))
            return (f'<span class="fd-barre"><i class="{"up" if v >= 0 else "down"}" '
                    f'style="{"left:50%" if v >= 0 else "right:50%"};width:{abs(v) / m * 50:.1f}%"></i></span>')
        lig = "".join(f'<tr><th scope="row">{lib}</th><td class="num">{fr(b["contrib"]["reel"], 2, True, " %")}</td>'
                      f'<td class="num">{fr(b["contrib"]["dxy"], 2, True, " %")}</td><td class="num">{fr(b["attendu"], 2, True, " %")}</td>'
                      f'<td class="num"><b>{fr(b["reel"], 2, True, " %")}</b></td><td class="num {COUL[int(np.sign(round(b["residu"], 1)))]}">'
                      f'{fr(b["residu"], 2, True, " %")}</td><td>{barre(b["residu"])}</td></tr>'
                      for lib, b in (("5 jours", rg["b5"]), ("20 jours", rg["b20"])))
        rupt = ('<p class="fd-alerte">Rupture : la corrélation de l\'or avec les taux réels sur 20 jours '
                f'({fr(rg["corr20"], 2, True)}) a changé de signe par rapport à son habitude sur 2 ans ({fr(rg["corr_2a"], 2, True)}). '
                'Le lien habituel ne joue plus en ce moment.</p>') if rg["rupture"] else ""
        h.append(
            f'<section class="fd"><h2>Ce qui fait bouger l\'or en ce moment</h2>'
            f'<p class="pourquoi">Régression des variations quotidiennes de l\'or sur celles des taux réels et du dollar. '
            f'Elle mesure si la macro pilote l\'or, et ce qui reste inexpliqué (les flux).</p>'
            f'<div class="fd-kpis">'
            f'<div><span class="k">Part expliquée, 60 jours</span><b>{fr(rg["r2_60"] * 100, 0)} %</b><small>sur 2 ans : {fr(rg["r2_2a"] * 100, 0)} %</small></div>'
            f'<div><span class="k">Sensibilité aux taux réels</span><b>{fr(rg["beta_reel"] * 10, 2, True, " %")}</b>'
            f'<small>de l\'or pour +10 pb · 2 ans : {fr(rg["beta_reel_2a"] * 10, 2, True, " %")}</small></div>'
            f'<div><span class="k">Sensibilité au dollar</span><b>{fr(rg["beta_dxy"], 2, True)}</b>'
            f'<small>% d\'or pour +1 % de DXY · 2 ans : {fr(rg["beta_dxy_2a"], 2, True)}</small></div>'
            f'<div><span class="k">Moteur dominant</span><b>{esc(rg["dominant"])}</b><small>sur 60 jours</small></div></div>'
            f'<p class="fd-lect">{esc(rg["lecture"])}</p>{rupt}'
            f'<div class="tbl"><table class="fd-tab"><thead><tr><th></th><th>Effet taux réels</th><th>Effet dollar</th>'
            f'<th>Attendu</th><th>Réel</th><th>Inexpliqué (flux)</th><th></th></tr></thead><tbody>{lig}</tbody></table></div>'
            f'<p class="fd-lect">{esc(rg["lecture_flux"])}</p></section>')

    fw = F.get("fed")
    if fw and fw.get("reunions"):
        lig = "".join(
            f'<tr><th scope="row">{r["date"]:%d/%m/%Y}</th><td class="num">{fr(r["taux"], 2, unite=" %")}</td>'
            f'<td class="num">{fr(r["delta_pb"], 0, True, " pb")}</td><td class="num">{fr(r["cumul_pb"], 0, True, " pb")}</td>'
            f'<td><span class="fd-proba"><i class="down" style="width:{r["p_baisse"] * 100:.0f}%"></i>'
            f'<i class="flat" style="width:{r["p_statu"] * 100:.0f}%"></i><i class="up" style="width:{r["p_hausse"] * 100:.0f}%"></i></span></td>'
            f'<td class="num">{fr(r["p_baisse"] * 100, 0)} % / {fr(r["p_statu"] * 100, 0)} % / {fr(r["p_hausse"] * 100, 0)} %</td></tr>'
            for r in fw["reunions"][:6])
        c12 = fw.get("cumul_12m")
        us2, d5, d20 = F["us2"]
        h.append(
            f'<section class="fd"><h2>Fed : ce que le marché anticipe</h2>'
            f'<p class="pourquoi">Probabilités tirées des futures fed funds, réunion par réunion. Pour l\'or, chaque baisse de taux '
            f'anticipée en plus est un soutien, chaque baisse retirée une pression.</p>'
            f'<div class="fd-kpis"><div><span class="k">Fed funds effectif</span><b>{fr(fw.get("effr"), 2, unite=" %")}</b>'
            f'<small>cible {fr((fw.get("cible") or (None, None))[0], 2)}–{fr((fw.get("cible") or (None, None))[1], 2)} %</small></div>'
            f'<div><span class="k">Anticipé sur 12 mois</span><b>{fr(c12, 0, True, " pb")}</b>'
            f'<small>{"soit " + fr(abs(c12) / 25, 1) + " baisse(s) de 25 pb" if c12 is not None and c12 < 0 else "pas de baisse anticipée" if c12 is not None else ""}</small></div>'
            f'<div><span class="k">Taux 2 ans</span><b>{fr(us2, 2, unite=" %")}</b><small>{fr(d5, 0, True, " pb")} sur 5 j · {fr(d20, 0, True, " pb")} sur 20 j</small></div></div>'
            f'<div class="tbl"><table class="fd-tab"><thead><tr><th>Réunion</th><th>Taux attendu</th><th>Variation</th><th>Cumul</th>'
            f'<th>Baisse / statu quo / hausse</th><th></th></tr></thead><tbody>{lig}</tbody></table></div></section>')

    mt = F.get("moteurs") or []
    if mt:
        cartes = "".join(
            f'<div class="fd-carte {COUL[m["impact"]]}"><div class="fd-ct"><span>{esc(m["nom"])}</span>'
            f'<b>{fr(m["niv"], m["d"])}{esc(m["unite"])}</b></div>{spark(m["serie"])}'
            f'<div class="fd-vars"><span>5 j <b>{fr(m["v5"], 0 if m["mode"] == "pb" else 2, True, " pb" if m["mode"] == "pb" else " %")}</b></span>'
            f'<span>20 j <b>{fr(m["v20"], 0 if m["mode"] == "pb" else 2, True, " pb" if m["mode"] == "pb" else " %")}</b></span>'
            f'<span>{fr(m["z"], 1, True)} σ</span></div>'
            f'<small>{esc(m["expl"])} · <em>{"soutient l" + chr(39) + "or" if m["impact"] > 0 else "pèse sur l" + chr(39) + "or" if m["impact"] < 0 else "neutre pour l" + chr(39) + "or"}</em></small></div>'
            for m in mt)
        h.append(f'<section class="fd"><h2>Moteurs</h2><p class="pourquoi">Niveau, variation sur 5 et 20 jours, et taille du '
                 f'mouvement sur 5 jours par rapport à l\'habitude (σ). Un moteur n\'est coloré que si son mouvement dépasse '
                 f'un demi-écart-type : en dessous, c\'est du bruit.</p><div class="fd-cartes">{cartes}</div></section>')

    flux = []
    g = F.get("gld")
    if g:
        flux.append(("ETF GLD", f'{fr(g["t"], 1)} t', f'{fr(g["d5"], 1, True, " t")} sur 5 j · {fr(g["d20"], 1, True, " t")} sur 20 j',
                     int(np.sign(g["d5"] or 0)) if abs(g.get("d5") or 0) >= 5 else 0,
                     f'soit {fr(g.get("usd5"), 2, True)} Md$ sur 5 jours. Entrées = investisseurs qui achètent.'))
    c = F.get("cot")
    if c:
        pos = -1 if c["pct3a"] >= 85 else (1 if c["pct3a"] <= 15 else 0)
        flux.append(("Fonds spéculatifs (COT)", f'{fr(c["net"], 0)} contrats', f'percentile 3 ans : {fr(c["pct3a"], 0)} · '
                     f'{fr(c["chg"], 0, True)} sur la semaine', pos,
                     (c.get("lecture") or ("", ""))[1] or "Positionnement net des gérants de fonds sur le future or."))
    st = F.get("structure")
    if st:
        flux.append(("Tension sur l'or physique", f'portage {fr(st["ref"]["portage"], 2, unite=" %")}', f'contre SOFR {fr(st["sofr"], 2, unite=" %")}',
                     1 if st["tension"] else 0, "Un portage très inférieur au SOFR signale un or physique rare et cher à emprunter."
                     if st["tension"] else "Courbe des futures normale : pas de pénurie physique."))
    lq = F.get("liq")
    if lq:
        flux.append(("Liquidité nette (Fed et Trésor)", f'{fr(lq["niveau"], 2)} T$', f'{fr(lq.get("d4"), 1, True, " %")} sur 4 sem.',
                     int(np.sign(lq.get("d4") or 0)) if abs(lq.get("d4") or 0) >= 1.5 else 0,
                     "Plus de liquidité dans le système soutient les actifs, or compris."))
    if flux:
        cartes = "".join(f'<div class="fd-carte {COUL[s_]}"><div class="fd-ct"><span>{esc(n)}</span><b>{v}</b></div>'
                         f'<div class="fd-vars"><span>{sub}</span></div><small>{esc(txt)}</small></div>' for n, v, sub, s_, txt in flux)
        h.append(f'<section class="fd"><h2>Flux et positionnement</h2><p class="pourquoi">Qui achète, qui vend. Contexte : '
                 f'n\'entre pas dans la décision, mais explique les écarts entre l\'or et la macro.</p><div class="fd-cartes">{cartes}</div></section>')

    s_ = F["stress"]
    if s_["lignes"]:
        lig = "".join(f'<tr><th scope="row">{esc(n)}</th><td class="num">{v}</td><td class="num">{d}</td>'
                      f'<td class="{"warn" if t else "flat"}">{"tendu" if t else "normal"}</td><td>{spark(se, 120, 90, 22)}</td></tr>'
                      for n, v, d, t, se in s_["lignes"])
        h.append(f'<section class="fd"><h2>Stress et risque</h2><p class="fd-lect">{esc(s_["etat"])}</p>'
                 f'<div class="tbl"><table class="fd-tab"><thead><tr><th></th><th>Niveau</th><th>Variation</th><th>État</th><th>3 mois</th></tr>'
                 f'</thead><tbody>{lig}</tbody></table></div></section>')

    mc = F.get("macro") or []
    ag = F.get("agenda") or []
    if mc or ag:
        lm = "".join(f'<tr><th scope="row">{esc(m["nom"])}</th><td class="num"><b>{esc(m["val"])}</b></td>'
                     f'<td class="num">{esc(m["prec"])}</td><td>{pd.Timestamp(m["date"]):%m/%Y}</td></tr>' for m in mc)
        la = "".join(f'<tr class="{"fd-fort" if e.get("impact") == "High" else ""}"><th scope="row">{date_courte(e["date"])}</th>'
                     f'<td>{esc(e.get("titre", ""))}</td><td>{"FORT" if e.get("impact") == "High" else "moyen"}</td>'
                     f'<td class="num">{esc(e.get("prevision") or "—")}</td><td class="num">{esc(e.get("precedent") or "—")}</td>'
                     f'<td class="num"><b>{esc(e.get("reel") or "")}</b></td></tr>' for e in ag)
        h.append(f'<section class="fd fd-2col"><div><h2>Derniers chiffres</h2><div class="tbl"><table class="fd-tab"><thead><tr><th></th>'
                 f'<th>Dernier</th><th>Précédent</th><th>Mois</th></tr></thead><tbody>{lm}</tbody></table></div></div>'
                 f'<div><h2>Agenda des 7 jours</h2><p class="pourquoi">Annonces fortes : pas d\'entrée 30 min avant et après.</p>'
                 f'<div class="tbl"><table class="fd-tab"><thead><tr><th>Heure (Paris)</th><th>Annonce</th><th>Impact</th><th>Prévu</th>'
                 f'<th>Précédent</th><th>Réel</th></tr></thead><tbody>{la or "<tr><td colspan=6>Rien à l’agenda.</td></tr>"}</tbody></table></div></div></section>')
    if ia_html:
        h.append(ia_html)
    return "".join(h)


CSS = """
/* Fondamental pro */
.fd{margin-bottom:30px}
.fd-hero{display:flex;flex-wrap:wrap;gap:14px 30px;align-items:center;padding:18px 20px;border:1px solid var(--line);border-radius:8px;background:var(--panel);margin-bottom:12px}
.fd-hero.up{border-color:rgba(79,184,132,.5);background:linear-gradient(90deg,rgba(79,184,132,.09),var(--panel) 60%)}
.fd-hero.down{border-color:rgba(224,96,90,.5);background:linear-gradient(90deg,rgba(224,96,90,.09),var(--panel) 60%)}
.fd-hero-g{display:flex;flex-direction:column;gap:2px}
.fd-hero-g b{font:700 44px/1.05 var(--cond)}
.fd-hero.up .fd-hero-g b{color:var(--up)} .fd-hero.down .fd-hero-g b{color:var(--down)} .fd-hero.flat .fd-hero-g b{color:var(--steel)}
.fd-effet{font:500 14px var(--cond);color:var(--ink)}
.fd-synth{margin:0;flex:1 1 360px;font-size:15px;line-height:1.55}
.fd-comps{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:10px}
.fd-comp,.fd-carte{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:7px;min-width:0}
.fd-comp.up,.fd-carte.up{border-left:3px solid var(--up)} .fd-comp.down,.fd-carte.down{border-left:3px solid var(--down)}
.fd-ct{display:flex;justify-content:space-between;align-items:baseline;gap:10px}
.fd-ct span{font:600 13px var(--cond);color:var(--mute);text-transform:uppercase;letter-spacing:.05em}
.fd-ct b{font:600 20px var(--cond);font-variant-numeric:tabular-nums}
.fd-comp small,.fd-carte small{font-size:12.5px;color:var(--mute);line-height:1.4}
.fd-comp em,.fd-carte em{font-style:normal;font-weight:600}
.fd-comp.up em,.fd-carte.up em{color:var(--up)} .fd-comp.down em,.fd-carte.down em{color:var(--down)}
.fd-comp p{margin:0;font-size:13.5px;line-height:1.45}
.fd-jauge{position:relative;height:8px;border-radius:4px;background:var(--band2)}
.fd-jauge i{position:absolute;top:-3px;bottom:-3px;width:1px;background:var(--mute)} .fd-jauge .s1{left:25%} .fd-jauge .s2{left:75%}
.fd-jauge b{position:absolute;top:-3px;width:4px;height:14px;margin-left:-2px;border-radius:2px;background:var(--ink)}
.fd-comp.up .fd-jauge b{background:var(--up)} .fd-comp.down .fd-jauge b{background:var(--down)}
.fd-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px;margin:10px 0}
.fd-kpis div{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:12px 14px;display:flex;flex-direction:column;gap:3px}
.fd-kpis b{font:600 22px var(--cond);font-variant-numeric:tabular-nums} .fd-kpis small{font-size:12.5px;color:var(--mute)}
.fd-lect{margin:10px 0;font-size:14.5px;line-height:1.55;max-width:90ch}
.fd-alerte{margin:10px 0;padding:10px 12px;border:1px solid rgba(240,169,75,.5);border-radius:6px;color:var(--amber);font-size:14px}
.fd-tab{width:100%;border-collapse:collapse;font-size:14px}
.fd-tab th{text-align:left;font:600 11px var(--cond);letter-spacing:.07em;text-transform:uppercase;color:var(--mute);padding:8px 10px;border-bottom:1px solid var(--line)}
.fd-tab tbody th{font:600 14px var(--cond);letter-spacing:0;text-transform:none;color:var(--ink);white-space:nowrap}
.fd-tab td{padding:9px 10px;border-bottom:1px solid var(--band2)}
.fd-tab td.num{font-family:var(--cond);font-variant-numeric:tabular-nums;white-space:nowrap}
.fd-tab tr.fd-fort th,.fd-tab tr.fd-fort td:nth-child(3){color:var(--amber)}
.fd-barre{position:relative;display:inline-block;width:120px;height:8px;background:var(--band2);border-radius:4px;vertical-align:middle}
.fd-barre i{position:absolute;top:0;bottom:0;border-radius:4px} .fd-barre i.up{background:var(--up)} .fd-barre i.down{background:var(--down)}
.fd-proba{display:flex;width:160px;height:10px;border-radius:5px;overflow:hidden;background:var(--band2)}
.fd-proba i.down{background:var(--up)} .fd-proba i.flat{background:#56636f} .fd-proba i.up{background:var(--down)}
.fd-cartes{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px}
.fd-spark{color:var(--mute)} .fd-carte.up .fd-spark{color:var(--up)} .fd-carte.down .fd-spark{color:var(--down)}
.fd-vars{display:flex;flex-wrap:wrap;gap:4px 14px;font:500 12.5px var(--cond);color:var(--mute)} .fd-vars b{color:var(--ink);font-weight:600}
.fd-2col{display:grid;grid-template-columns:repeat(auto-fit,minmax(380px,1fr));gap:20px}
@media (max-width:640px){.fd-2col{grid-template-columns:minmax(0,1fr)}.fd-hero-g b{font-size:36px}}
"""
