# -*- coding: utf-8 -*-
"""
CARTE DES OPTIONS (fonction OPT)
================================

Où sont placés les contrats d'options sur l'or, prix par prix. Les prix où beaucoup de contrats sont ouverts agissent
souvent comme des murs ou des aimants, parce que ceux qui ont vendu ces options se couvrent en achetant ou en vendant
de l'or quand le prix s'en approche.

Source : les options du fonds GLD (le plus gros fonds adossé à l'or), lues sur Yahoo Finance, gratuites et mises à
jour une fois par jour (contrats ouverts à la clôture de la veille). Les prix d'exercice du GLD sont convertis en prix
de l'or avec le rapport or / GLD du moment. Ce n'est pas le marché des options sur futures du COMEX (données
payantes) : les niveaux sont proches, pas identiques.

Ce que le module calcule, sur les échéances des 45 prochains jours :
  - les murs : les prix où il y a le plus de calls au-dessus du prix, et le plus de puts en dessous ;
  - le prix aimant (« max pain ») de la prochaine grosse échéance : celui où les acheteurs d'options perdent le plus ;
  - le rapport puts / calls (contrats ouverts) ;
  - le mouvement attendu : prix du call et du put au prix du marché (straddle) sur l'échéance la plus proche d'une
    semaine, c'est-à-dire l'amplitude que le marché paie pour couvrir.
"""

import html
import json
import time
from datetime import date, datetime, timedelta

HORIZON_J = 45       # échéances retenues : celles des 45 prochains jours
LARGEUR = 0.08       # prix d'exercice gardés : à moins de 8 % du prix
TTL_H = 4            # une lecture toutes les 4 heures suffit : les contrats ouverts ne changent qu'une fois par jour
FICHIER = "options_gld.json"


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=0, signe=False, unite=""):
    if x is None:
        return "—"
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def _n(x):
    try:
        x = float(x)
        return x if x == x else 0.0
    except Exception:
        return 0.0


def charger(dossier_cache, maintenant=None):
    """Chaînes d'options du GLD pour les échéances proches. Garde une copie : si Yahoo ne répond pas, on ressert la
    dernière lecture (son âge est indiqué). Lève une erreur seulement s'il n'y a ni réponse ni copie."""
    f = dossier_cache / FICHIER
    if f.exists() and time.time() - f.stat().st_mtime < TTL_H * 3600:
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        import yfinance as yf
        t = yf.Ticker("GLD")
        auj = (maintenant.date() if maintenant else date.today())
        ech = [e for e in (t.options or []) if auj <= date.fromisoformat(e) <= auj + timedelta(days=HORIZON_J)]
        if not ech:
            raise RuntimeError("aucune échéance proche")
        h = t.history(period="5d", interval="1d", auto_adjust=False)["Close"].dropna()
        spot, jour = float(h.iloc[-1]), str(h.index[-1].date())
        out = {"spot": spot, "jour": jour, "lu": time.time(), "ech": []}
        try:      # prix du future or le même jour : donne un rapport or / GLD propre, même quand le GLD est fermé
            g = yf.Ticker("GC=F").history(period="5d", interval="1d", auto_adjust=False)["Close"].dropna()
            g = g[[str(d.date()) == jour for d in g.index]]
            out["or_ref"] = float(g.iloc[-1]) if len(g) else None
        except Exception:
            out["or_ref"] = None
        for e in ech[:16]:
            ch = None
            for _ in range(2):      # une échéance peut revenir vide : on réessaie une fois, puis on la saute
                try:
                    ch = t.option_chain(e)
                    if ch is not None and ch.calls is not None and ch.puts is not None and len(ch.calls) and len(ch.puts):
                        break
                except Exception:
                    pass
                ch = None
                time.sleep(1)
            if ch is None:
                continue
            lig = lambda df: [[_n(r.strike), int(_n(r.openInterest)), int(_n(r.volume)), _n(r.impliedVolatility), _n(r.bid), _n(r.ask),
                               _n(r.lastPrice)] for r in df.itertuples() if abs(_n(r.strike) / spot - 1) <= LARGEUR * 1.5]
            out["ech"].append({"date": e, "calls": lig(ch.calls), "puts": lig(ch.puts)})
        if not out["ech"] or sum(sum(x[1] for x in e["calls"] + e["puts"]) for e in out["ech"]) < 1000:
            raise RuntimeError("contrats ouverts absents de la réponse")
        dossier_cache.mkdir(exist_ok=True)
        f.write_text(json.dumps(out), encoding="utf-8")
        return out
    except Exception:
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        raise


def _prix(l):
    """Prix d'une option : milieu de la fourchette si elle existe, sinon dernier échange."""
    return (l[4] + l[5]) / 2 if l[4] > 0 and l[5] > 0 else l[6]


def analyser(D, prix_or, ajust=0.0, maintenant=None):
    """D : sortie de charger(). prix_or : prix de l'or affiché. ajust : écart entre le future et le prix affiché.
    Le rapport or / GLD vient des deux prix du même jour ; à défaut, du prix actuel."""
    if not D or not D.get("ech") or not prix_or:
        return None
    spot = D["spot"]
    k = ((D["or_ref"] + (ajust or 0.0)) if D.get("or_ref") else prix_or) / spot                      # 1 $ de GLD = k $ d'or
    spot_or = prix_or
    auj = (maintenant.date() if maintenant else date.today())
    C, P = {}, {}
    tot_c = tot_p = 0
    for e in D["ech"]:
        for l in e["calls"]:
            C[l[0]] = C.get(l[0], 0) + l[1]
            tot_c += l[1]
        for l in e["puts"]:
            P[l[0]] = P.get(l[0], 0) + l[1]
            tot_p += l[1]
    if tot_c + tot_p < 1000:
        return None
    bornes = (spot_or * (1 - LARGEUR), spot_or * (1 + LARGEUR))
    strikes = sorted(s for s in set(C) | set(P) if bornes[0] <= s * k <= bornes[1])
    profil = [{"k": s * k, "c": C.get(s, 0), "p": P.get(s, 0)} for s in strikes]
    haut = sorted((x for x in profil if x["k"] > spot_or and x["c"] > 0), key=lambda x: -x["c"])[:3]
    bas = sorted((x for x in profil if x["k"] < spot_or and x["p"] > 0), key=lambda x: -x["p"])[:3]

    # prix aimant : sur l'échéance proche qui porte le plus de contrats
    def poids(e):
        return sum(l[1] for l in e["calls"]) + sum(l[1] for l in e["puts"])
    grosse = max(D["ech"], key=poids)
    ks = sorted(set(l[0] for l in grosse["calls"] + grosse["puts"]))
    def perte(K):
        return (sum(l[1] * max(0.0, K - l[0]) for l in grosse["calls"]) + sum(l[1] * max(0.0, l[0] - K) for l in grosse["puts"]))
    aimant = min(ks, key=perte) * k if ks and poids(grosse) > 500 else None

    # mouvement attendu : straddle au prix du marché, échéance la plus proche de 7 jours (au moins 2 jours)
    attendu = None
    cand = [e for e in D["ech"] if (date.fromisoformat(e["date"]) - auj).days >= 2]
    if cand:
        e = min(cand, key=lambda e: abs((date.fromisoformat(e["date"]) - auj).days - 7))
        communs = set(l[0] for l in e["calls"]) & set(l[0] for l in e["puts"])
        if communs:
            ka = min(communs, key=lambda s: abs(s - spot))
            c = next(l for l in e["calls"] if l[0] == ka)
            p = next(l for l in e["puts"] if l[0] == ka)
            v = _prix(c) + _prix(p)
            if v > 0:
                attendu = {"amp": v * k, "date": e["date"], "jours": (date.fromisoformat(e["date"]) - auj).days,
                           "bas": spot_or - v * k, "haut": spot_or + v * k, "pct": v / spot}
    return {"prix": spot_or, "rapport": k, "profil": profil, "murs_haut": sorted(haut, key=lambda x: x["k"]),
            "murs_bas": sorted(bas, key=lambda x: -x["k"]), "aimant": aimant, "aimant_date": grosse["date"],
            "pc": tot_p / tot_c if tot_c else None, "tot_c": tot_c, "tot_p": tot_p, "attendu": attendu,
            "jour": D.get("jour"), "age_h": (time.time() - D["lu"]) / 3600 if D.get("lu") else None,
            "echeances": [e["date"] for e in D["ech"]]}


def niveaux(O):
    """Niveaux à tracer sur le graphique : (libellé, prix)."""
    if not O:
        return []
    out = [(f"Mur de calls", x["k"]) for x in O["murs_haut"][:2]] + [(f"Mur de puts", x["k"]) for x in O["murs_bas"][:2]]
    if O.get("aimant"):
        out.append(("Aimant options", O["aimant"]))
    return out


def _date(s):
    d = date.fromisoformat(s)
    return f"{d.day:02d}/{d.month:02d}"


def svg_profil(O, w=1000, h=None):
    """Barres horizontales par prix : calls à droite (bleu), puts à gauche (ambre), prix actuel en trait blanc."""
    pr = [x for x in O["profil"] if x["c"] + x["p"] > 0]
    if len(pr) > 44:      # on garde les prix les plus chargés, pour un graphique lisible
        seuil = sorted((x["c"] + x["p"] for x in pr), reverse=True)[43]
        pr = [x for x in pr if x["c"] + x["p"] >= seuil]
    pr = sorted(pr, key=lambda x: -x["k"])
    if not pr:
        return ""
    lg = 15
    h = h or (30 + lg * len(pr) + 8)
    mx = max(max(x["c"], x["p"]) for x in pr) or 1
    cx, demi = w / 2, w / 2 - 70
    murs = {round(x["k"], 2) for x in O["murs_haut"] + O["murs_bas"]}
    s = [f'<svg viewBox="0 0 {w} {h}" class="op-svg" role="img" aria-label="Contrats d\'options ouverts par prix">',
         f'<text x="{cx - 46}" y="14" text-anchor="end" class="op-t op-tp">PUTS (protections contre la baisse)</text>',
         f'<text x="{cx + 46}" y="14" class="op-t op-tc">CALLS (paris sur la hausse)</text>']
    y_prix, fait = None, False
    for i, x in enumerate(pr):
        y = 26 + i * lg
        if not fait and x["k"] < O["prix"]:
            y_prix, fait = y - 1, True
        fort = round(x["k"], 2) in murs
        s.append(f'<text x="{cx}" y="{y + lg / 2 + 1}" text-anchor="middle" dominant-baseline="middle" class="op-k{" op-fort" if fort else ""}">{fr(x["k"], 0)}</text>')
        lc, lp = demi * x["c"] / mx, demi * x["p"] / mx
        if lc >= 0.5:
            s.append(f'<rect x="{cx + 40}" y="{y + 2}" width="{lc:.1f}" height="{lg - 4}" class="op-c{" op-mur" if fort and x["k"] > O["prix"] else ""}">'
                     f'<title>{fr(x["k"], 0)} $ : {fr(x["c"])} calls</title></rect>')
        if lp >= 0.5:
            s.append(f'<rect x="{cx - 40 - lp:.1f}" y="{y + 2}" width="{lp:.1f}" height="{lg - 4}" class="op-p{" op-mur" if fort and x["k"] < O["prix"] else ""}">'
                     f'<title>{fr(x["k"], 0)} $ : {fr(x["p"])} puts</title></rect>')
    if y_prix is None:
        y_prix = 26 + len(pr) * lg
    s.append(f'<line x1="0" x2="{w}" y1="{y_prix}" y2="{y_prix}" class="op-prix"/>'
             f'<text x="{w - 4}" y="{y_prix - 5}" text-anchor="end" class="op-t op-tx">prix {fr(O["prix"], 0)}</text>')
    return "".join(s) + "</svg>"


def lecture(O):
    """Deux ou trois phrases simples sur ce que la carte dit aujourd'hui."""
    p, ph = O["prix"], []
    mh, mb = (O["murs_haut"] or [None])[0], (O["murs_bas"] or [None])[0]
    gros_h = max(O["murs_haut"], key=lambda x: x["c"]) if O["murs_haut"] else None
    gros_b = max(O["murs_bas"], key=lambda x: x["p"]) if O["murs_bas"] else None
    if gros_h and gros_b:
        ph.append(f'Le plus gros mur au-dessus est à <b>{fr(gros_h["k"], 0)} $</b> ({fr(gros_h["k"] - p, 0, True)} $), le plus gros en dessous à '
                  f'<b>{fr(gros_b["k"], 0)} $</b> ({fr(gros_b["k"] - p, 0, True)} $) : tant que ces contrats existent, le prix a plus de mal à sortir de cette zone.')
    if mh and (mh["k"] - p) / p < 0.004:
        ph.append(f'Le prix est collé sous un mur de calls ({fr(mh["k"], 0)} $) : acheter juste dessous laisse peu de place.')
    if mb and (p - mb["k"]) / p < 0.004:
        ph.append(f'Le prix est posé sur un mur de puts ({fr(mb["k"], 0)} $) : s\'il casse, les couvertures peuvent accélérer la baisse.')
    if O.get("aimant"):
        e = O["aimant"] - p
        ph.append(f'Prix aimant de l\'échéance du {_date(O["aimant_date"])} : <b>{fr(O["aimant"], 0)} $</b> ({fr(e, 0, True)} $ par rapport au prix). '
                  'L\'effet joue surtout dans les derniers jours avant l\'échéance.')
    if O.get("pc") is not None:
        pc = O["pc"]
        ph.append(f'Il y a {fr(pc, 2)} put pour 1 call : ' + ("le marché se protège surtout contre une baisse." if pc >= 1.1 else
                  "le marché parie surtout sur la hausse." if pc <= 0.7 else "protections et paris haussiers sont à peu près équilibrés."))
    return " ".join(ph)


def bloc(O, erreur=None):
    if not O:
        return ('<section><h2>Carte des options</h2><p class="vide">Options du GLD indisponibles'
                + (f' ({esc(erreur)})' if erreur else '') + ' : la carte reviendra à la prochaine lecture.</p></section>')
    a = O.get("attendu")
    def mur(x, cle, nom):
        d = x["k"] - O["prix"]
        return (f'<tr><td>{nom}</td><td><b>{fr(x["k"], 0)} $</b></td><td>{fr(d, 0, True)} $</td><td>{fr(x[cle])} contrats</td></tr>')
    lignes = ("".join(mur(x, "c", "Mur de calls") for x in reversed(O["murs_haut"]))
              + f'<tr class="op-ici"><td>Prix actuel</td><td><b>{fr(O["prix"], 0)} $</b></td><td></td><td></td></tr>'
              + "".join(mur(x, "p", "Mur de puts") for x in O["murs_bas"]))
    kpis = (
        f'<div><span class="k">Mouvement attendu</span><b>{"± " + fr(a["amp"], 0) + " $" if a else "—"}</b>'
        f'<small>{"d\'ici le " + _date(a["date"]) + " (" + str(a["jours"]) + " j) : " + fr(a["bas"], 0) + " à " + fr(a["haut"], 0) if a else "indisponible"}</small></div>'
        f'<div><span class="k">Prix aimant</span><b>{fr(O["aimant"], 0) + " $" if O.get("aimant") else "—"}</b>'
        f'<small>échéance du {_date(O["aimant_date"])}</small></div>'
        f'<div><span class="k">Puts pour 1 call</span><b>{fr(O["pc"], 2) if O.get("pc") is not None else "—"}</b>'
        f'<small>{fr(O["tot_p"])} puts · {fr(O["tot_c"])} calls</small></div>'
        f'<div><span class="k">Échéances lues</span><b>{len(O["echeances"])}</b>'
        f'<small>du {_date(O["echeances"][0])} au {_date(O["echeances"][-1])}</small></div>')
    age = O.get("age_h")
    fraich = "Contrats ouverts à la dernière clôture (mis à jour une fois par jour)" + \
             (f', lus il y a {fr(age, 0)} h' if age is not None and age >= 1 else "")
    return (
        '<section><h2>Carte des options</h2>'
        '<p class="pourquoi">Où sont placés les contrats d\'options sur l\'or, prix par prix. Un prix très chargé agit souvent comme un mur : '
        'ceux qui ont vendu ces options vendent de l\'or quand le prix monte vers un mur de calls, et en achètent quand il descend vers un mur de '
        'puts. Ce sont des zones où le prix réagit souvent, pas une prévision.</p>'
        f'<div class="jr-kpis op-kpis">{kpis}</div>'
        f'<div class="op-deux"><div class="op-graph">{svg_profil(O)}</div>'
        f'<div class="op-cote"><table class="op-tab"><thead><tr><th>Niveau</th><th>Prix</th><th>Distance</th><th>Taille</th></tr></thead><tbody>{lignes}</tbody></table>'
        f'<p class="op-lecture">{lecture(O)}</p></div></div>'
        f'<p class="op-src">{esc(fraich)}. Source : options du fonds GLD (Yahoo Finance), prix convertis en or (1 $ de GLD = {fr(O["rapport"], 2)} $ d\'or). '
        'Les options sur futures du COMEX, que suivent les banques, peuvent placer les murs à des prix un peu différents.</p></section>')


CSS = """
.op-kpis{grid-template-columns:repeat(4,minmax(0,1fr))}
.op-deux{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:12px;align-items:start}
.op-graph{background:#07080b;border:1px solid var(--band2);border-radius:2px;padding:8px;min-width:0;overflow:hidden}
.op-svg{display:block;width:100%;height:auto}
.op-t{font:600 11px var(--mono);letter-spacing:.06em} .op-tc{fill:var(--brass)} .op-tp{fill:var(--amber)} .op-tx{fill:var(--ink)}
.op-k{font:500 11px var(--mono);fill:var(--mute)} .op-k.op-fort{fill:var(--ink);font-weight:700}
.op-c{fill:var(--brass);opacity:.45} .op-p{fill:var(--amber);opacity:.45} .op-c.op-mur,.op-p.op-mur{opacity:1}
.op-prix{stroke:var(--ink);stroke-width:1;stroke-dasharray:5 4}
.op-cote{min-width:0}
.op-tab{width:100%;border-collapse:collapse;font:500 13px var(--mono)}
.op-tab th{font:600 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--mute);text-align:right;padding:6px 8px;border-bottom:1px solid var(--line)}
.op-tab td{text-align:right;padding:7px 8px;border-bottom:1px solid var(--band2);color:var(--steel)}
.op-tab th:first-child,.op-tab td:first-child{text-align:left;font-family:var(--sans,inherit)}
.op-tab td b{color:var(--ink)} .op-tab tr.op-ici td{background:rgba(126,166,214,.08);color:var(--ink)}
.op-lecture{font-size:13.5px;line-height:1.55;color:var(--steel);margin-top:12px} .op-lecture b{color:var(--ink)}
.op-src{font-size:12px;color:var(--mute);margin-top:10px}
@media (max-width:900px){.op-deux{grid-template-columns:1fr}.op-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}}
"""


def demo(prix_or):
    """Chaîne fictive mais vraisemblable, pour le mode démonstration."""
    import math
    spot = prix_or / 10.9
    auj = date.today()
    ech = []
    for j in (4, 11, 18, 32):
        d = auj + timedelta(days=j)
        cs, ps = [], []
        for i in range(-30, 31):
            s = round(spot) + i
            rond = 3.0 if s % 10 == 0 else (1.8 if s % 5 == 0 else 1.0)
            oi_c = int(900 * rond * math.exp(-((s - spot * 1.03) / (spot * 0.035)) ** 2) * (1 + j / 20))
            oi_p = int(800 * rond * math.exp(-((s - spot * 0.965) / (spot * 0.035)) ** 2) * (1 + j / 20))
            iv, T = 0.22, j / 365
            val = 0.4 * spot * iv * math.sqrt(T)
            c = max(spot - s, 0) + val * math.exp(-abs(s - spot) / (spot * iv * math.sqrt(T) * 1.5))
            p = max(s - spot, 0) + val * math.exp(-abs(s - spot) / (spot * iv * math.sqrt(T) * 1.5))
            cs.append([float(s), oi_c, oi_c // 6, iv, round(c * 0.98, 2), round(c * 1.02, 2), round(c, 2)])
            ps.append([float(s), oi_p, oi_p // 6, iv, round(p * 0.98, 2), round(p * 1.02, 2), round(p, 2)])
        ech.append({"date": d.isoformat(), "calls": cs, "puts": ps})
    return {"spot": spot, "jour": (auj - timedelta(days=1)).isoformat(), "lu": time.time(), "ech": ech}
