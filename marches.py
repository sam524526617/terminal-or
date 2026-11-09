# -*- coding: utf-8 -*-
"""
MARCHÉS (fonction MKT) : tout le marché qui fait bouger l'or, d'un coup d'œil
============================================================================

Un tableau par famille (métaux, dollar et devises, taux américains, énergie, risque) avec le dernier cours, les
variations sur 1 jour, 5 jours, 1 mois et depuis le début de l'année, la position dans la fourchette sur 52 semaines,
une courbe de 3 mois et l'effet habituel sur l'or. En dessous, la courbe des taux américains (aujourd'hui, il y a un
mois, il y a un an) et la courbe des taux réels. Données quotidiennes (Yahoo Finance, FRED).
"""

import html

import numpy as np
import pandas as pd

GROUPES = [
    ("Métaux", [("Or (future COMEX)", "y", "or", "pct", 0, 0), ("Argent", "y", "argent", "pct", 0, 1),
                ("Cuivre", "y", "cuivre", "pct", 0, 0), ("Mines d'or (GDX)", "y", "gdx", "pct", 0, 1)]),
    ("Dollar et devises", [("Dollar index (DXY)", "y", "dxy", "pct", 0, -1), ("EUR/USD", "y", "eurusd", "pct", 0, 1),
                           ("USD/JPY", "y", "usdjpy", "pct", 0, -1), ("USD/CNY", "y", "usdcny", "pct", 0, -1)]),
    ("Taux américains", [("3 mois", "y", "us3m", "pb", 1, -1), ("2 ans", "f", "us2", "pb", 1, -1),
                         ("5 ans", "y", "us5", "pb", 1, -1), ("10 ans", "y", "us10", "pb", 1, -1),
                         ("30 ans", "y", "us30", "pb", 1, -1), ("Taux réel 10 ans", "f", "reel10", "pb", 1, -1),
                         ("Inflation anticipée 10 ans", "f", "be10", "pb", 1, 1)]),
    ("Énergie", [("Brent", "y", "brent", "pct", 0, 0), ("WTI", "y", "wti", "pct", 0, 0)]),
    ("Risque", [("S&P 500", "y", "spx", "pct", 0, 0), ("VIX (actions)", "y", "vix", "abs", 0, 1),
                ("MOVE (obligations)", "y", "move", "abs", 0, 0), ("GVZ (volatilité de l'or)", "y", "gvz", "abs", 0, 0)]),
]


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "—"
    if signe and abs(x) < 0.5 * 10 ** (-d):
        x = 0.0
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def propre(s):
    if s is None:
        return None
    s = pd.to_numeric(s, errors="coerce").dropna()
    if len(s) < 3:
        return None
    s.index = pd.DatetimeIndex(s.index).tz_localize(None) if getattr(s.index, "tz", None) is not None else pd.DatetimeIndex(s.index)
    return s


def variation(s, n, mode):
    if s is None or len(s) <= n:
        return None
    a, b = float(s.iloc[-1 - n]), float(s.iloc[-1])
    if mode == "pct":
        return (b / a - 1) * 100 if a else None
    if mode == "pb":
        return (b - a) * 100
    return b - a


def depuis_janvier(s, mode):
    if s is None:
        return None
    d = s[s.index < pd.Timestamp(s.index[-1].year, 1, 1)]
    if not len(d):
        return None
    a, b = float(d.iloc[-1]), float(s.iloc[-1])
    return ((b / a - 1) * 100 if a else None) if mode == "pct" else ((b - a) * 100 if mode == "pb" else b - a)


def spark(s, n=63, w=96, h=24):
    s = s.tail(n)
    lo, hi = float(s.min()), float(s.max())
    r = (hi - lo) or 1
    pts = " ".join(f"{2 + i * (w - 4) / (len(s) - 1):.1f},{h - 2 - (float(v) - lo) / r * (h - 4):.1f}" for i, v in enumerate(s))
    cl = "up" if s.iloc[-1] >= s.iloc[0] else "down"
    return f'<svg class="mk-spark {cl}" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true"><polyline points="{pts}"/></svg>'


def cellule_var(v, mode, effet):
    if v is None:
        return '<td class="num">—</td>'
    u = " pb" if mode == "pb" else (" %" if mode == "pct" else "")
    d = 0 if mode == "pb" else 2
    cl = "up" if v > 0 else ("down" if v < 0 else "")
    return f'<td class="num {cl}">{fr(v, d, True, u)}</td>'


def tableau(y, f):
    lignes = []
    for groupe, items in GROUPES:
        corps = ""
        for nom, src, cle, mode, est_taux, effet in items:
            s = propre((y if src == "y" else f).get(cle))
            if s is None:
                continue
            last = float(s.iloc[-1])
            an = s.tail(252)
            lo, hi = float(an.min()), float(an.max())
            pos = (last - lo) / (hi - lo) * 100 if hi > lo else 50
            v1 = variation(s, 1, mode)
            imp = 0
            if effet and v1:
                imp = effet * (1 if v1 > 0 else -1)
            d = 2 if (est_taux or last < 100) else (0 if last > 1000 else 2)
            corps += (f'<tr><th scope="row">{esc(nom)}</th><td class="num mk-last">{fr(last, d)}{" %" if est_taux else ""}</td>'
                      + cellule_var(v1, mode, effet) + cellule_var(variation(s, 5, mode), mode, effet)
                      + cellule_var(variation(s, 21, mode), mode, effet) + cellule_var(depuis_janvier(s, mode), mode, effet)
                      + f'<td><div class="mk-52" title="plus bas 52 sem. {fr(lo, d)} · plus haut {fr(hi, d)}"><i style="left:{pos:.0f}%"></i></div></td>'
                      + f'<td>{spark(s)}</td><td class="mk-eff {"up" if imp > 0 else "down" if imp < 0 else ""}">'
                      + ("soutient l'or" if imp > 0 else "pèse sur l'or" if imp < 0 else ("—" if not effet else "stable")) + '</td></tr>')
        if corps:
            lignes.append(f'<tbody><tr class="mk-groupe"><th colspan="9">{esc(groupe)}</th></tr>{corps}</tbody>')
    return "".join(lignes)


def courbe(y, f):
    """Courbe des taux nominaux (3 mois → 30 ans) aujourd'hui, il y a 1 mois et il y a 1 an, plus les taux réels."""
    points = [("3 m", 0.25, propre(y.get("us3m"))), ("2 a", 2, propre(f.get("us2"))), ("5 a", 5, propre(y.get("us5"))),
              ("10 a", 10, propre(y.get("us10"))), ("30 a", 30, propre(y.get("us30")))]
    points = [p for p in points if p[2] is not None]
    reels = [(lib, m, propre(f.get(k))) for lib, m, k in (("5 a", 5, "reel5"), ("10 a", 10, "reel10"), ("30 a", 30, "reel30"))]
    reels = [p for p in reels if p[2] is not None]
    if len(points) < 3:
        return ""
    def val(s, n):
        return float(s.iloc[-1 - n]) if len(s) > n else None
    series = [("Aujourd'hui", 0, "c0"), ("Il y a 1 mois", 21, "c1"), ("Il y a 1 an", 252, "c2")]
    tout = [val(s, n) for _, _, s in points for _, n, _ in series] + [val(s, 0) for _, _, s in reels]
    tout = [v for v in tout if v is not None]
    lo, hi = min(tout) - 0.2, max(tout) + 0.2
    W, H, g, d, b = 720, 260, 44, 16, 30
    xs = {m: g + i * (W - g - d) / (len(points) - 1) for i, (_, m, _) in enumerate(points)}
    yv = lambda v: 10 + (hi - v) / (hi - lo) * (H - b - 10)
    svg = [f'<svg viewBox="0 0 {W} {H}" class="mk-courbe" role="img" aria-label="Courbe des taux américains">']
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        svg.append(f'<line x1="{g}" x2="{W - d}" y1="{yv(v):.1f}" y2="{yv(v):.1f}" class="gr"/>'
                   f'<text x="{g - 6}" y="{yv(v) + 4:.1f}" text-anchor="end" class="ax">{fr(v, 1)} %</text>')
    for lib, m, _ in points:
        svg.append(f'<text x="{xs[m]:.1f}" y="{H - 8}" text-anchor="middle" class="ax">{lib}</text>')
    for nom, n, cl in reversed(series):
        pts = [(xs[m], yv(val(s, n))) for _, m, s in points if val(s, n) is not None]
        if len(pts) >= 2:
            svg.append(f'<polyline class="{cl}" points="{" ".join(f"{x:.1f},{y_:.1f}" for x, y_ in pts)}"/>')
            if n == 0:
                svg += [f'<circle cx="{x:.1f}" cy="{y_:.1f}" r="3.5" class="{cl}"/>' for x, y_ in pts]
    rpts = [(xs.get(m), yv(val(s, 0))) for _, m, s in reels if m in xs]
    if len(rpts) >= 2:
        svg.append(f'<polyline class="cr" points="{" ".join(f"{x:.1f},{y_:.1f}" for x, y_ in rpts)}"/>')
    svg.append("</svg>")
    pente = None
    s2, s10 = propre(f.get("us2")), propre(y.get("us10"))
    if s2 is not None and s10 is not None:
        pente = (float(s10.iloc[-1]) - float(s2.iloc[-1])) * 100
    leg = ('<div class="mk-leg"><span class="c0">Aujourd\'hui</span><span class="c1">Il y a 1 mois</span>'
           '<span class="c2">Il y a 1 an</span><span class="cr">Taux réels (5, 10, 30 ans)</span></div>')
    lecture = ""
    if pente is not None:
        lecture = (f'Pente 10 ans − 2 ans : <b>{fr(pente, 0, True, " pb")}</b>. '
                   + ("Courbe inversée : le marché anticipe des baisses de taux, souvent un soutien pour l'or à moyen terme."
                      if pente < 0 else "Courbe positive : situation normale ; une pentification rapide par le haut "
                      "(taux longs qui montent) pèse sur l'or si elle vient des taux réels."))
    return (f'<div class="mk-carte"><div class="mk-tete">Courbe des taux américains</div>{leg}{"".join(svg)}'
            f'<p class="mk-lect">{lecture}</p></div>')


def bloc_marches(y, f):
    t = tableau(y, f)
    if not t:
        return '<section><h2>Marchés</h2><p class="vide">Données indisponibles.</p></section>'
    return (f'<section class="mk"><h2>Marchés · tout ce qui fait bouger l\'or</h2>'
            f'<p class="pourquoi">Cours de clôture quotidiens (Yahoo Finance, FRED). Couleurs des variations : hausse en vert, '
            f'baisse en rouge. Dernière colonne : effet habituel du mouvement du jour sur l\'or. Barre 52 semaines : où se '
            f'situe le cours entre son plus bas et son plus haut de l\'année.</p>'
            f'<div class="tbl"><table class="mk-tab"><thead><tr><th>Actif</th><th>Dernier</th><th>1 j</th><th>5 j</th><th>1 mois</th>'
            f'<th>Depuis janv.</th><th>52 semaines</th><th>3 mois</th><th>Pour l\'or</th></tr></thead>{t}</table></div>'
            f'{courbe(y, f)}</section>')


CSS = """
/* Marchés (MKT) */
.mk-tab{width:100%;border-collapse:collapse;font-size:13px}
.mk-tab th{text-align:left;font:600 10.5px var(--text);letter-spacing:.07em;text-transform:uppercase;color:var(--mute);padding:7px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.mk-tab tbody th{font:500 13px var(--text);letter-spacing:0;text-transform:none;color:var(--ink);white-space:nowrap}
.mk-tab td{padding:6px 10px;border-bottom:1px solid var(--band2);white-space:nowrap}
.mk-tab td.num{font-family:var(--mono);text-align:right}
.mk-tab td.mk-last{color:var(--ink);font-weight:600}
.mk-tab tr.mk-groupe th{font:700 11px var(--text);letter-spacing:.1em;text-transform:uppercase;color:var(--brass);padding:14px 10px 6px;border-bottom:1px solid var(--brass)}
.mk-52{position:relative;width:110px;height:6px;border-radius:3px;background:linear-gradient(90deg,rgba(240,97,109,.35),rgba(141,151,168,.25),rgba(52,195,143,.35))}
.mk-52 i{position:absolute;top:-4px;width:3px;height:14px;margin-left:-1px;background:var(--ink);border-radius:1px}
.mk-spark polyline{fill:none;stroke-width:1.4} .mk-spark.up polyline{stroke:var(--up)} .mk-spark.down polyline{stroke:var(--down)}
.mk-eff{font-size:12px;color:var(--mute)} .mk-eff.up{color:var(--up)} .mk-eff.down{color:var(--down)}
.mk-carte{margin-top:18px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;max-width:900px}
.mk-tete{font:600 10.5px var(--text);letter-spacing:.08em;text-transform:uppercase;color:var(--mute);margin-bottom:6px}
.mk-courbe{display:block;width:100%;height:auto}
.mk-courbe .gr{stroke:var(--band2)} .mk-courbe .ax{fill:var(--mute);font:500 11px var(--mono)}
.mk-courbe polyline{fill:none;stroke-width:2} .mk-courbe .c0{stroke:var(--brass);fill:var(--brass)} .mk-courbe polyline.c0{fill:none}
.mk-courbe .c1{stroke:#6f7f96;stroke-dasharray:5 4} .mk-courbe .c2{stroke:#3d4757;stroke-dasharray:2 4}
.mk-courbe .cr{stroke:#5fb0e8;stroke-width:1.6}
.mk-leg{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:12px;color:var(--mute);margin-bottom:6px}
.mk-leg span::before{content:"";display:inline-block;width:14px;height:0;border-top:2px solid;margin-right:6px;vertical-align:middle}
.mk-leg .c0::before{border-color:var(--brass)} .mk-leg .c1::before{border-color:#6f7f96;border-top-style:dashed}
.mk-leg .c2::before{border-color:#3d4757;border-top-style:dotted} .mk-leg .cr::before{border-color:#5fb0e8}
.mk-lect{margin:8px 0 0;font-size:13.5px;line-height:1.5}
"""
