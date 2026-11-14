# -*- coding: utf-8 -*-
"""
POURQUOI L'OR BOUGE : l'explication de chaque séance
===================================================

Pour chacune des dernières séances (et la séance en cours), le terminal cherche ce qui a bougé en même temps que l'or :
  - le fondamental : dollar, taux réels, taux 2 ans, peur (VIX), actions, pétrole, et ce que la relation habituelle
    « taux + dollar » explique du mouvement (le reste vient des flux : prises de bénéfices, liquidations, achats refuge) ;
  - les annonces économiques du jour et l'heure de la bougie la plus violente ;
  - la technique : plus haut ou plus bas de la veille et de 20 jours cassé, amplitude par rapport à l'habitude, clôture
    en haut ou en bas de la séance, marché qui venait de beaucoup monter ou baisser, séance qui a fait le mouvement ;
  - les titres de presse du jour.

Ce sont des explications probables, pas des certitudes : deux choses qui bougent ensemble ne prouvent pas que l'une
cause l'autre. Quand rien n'explique un mouvement, la rubrique le dit.
"""

import html
import math
import re

import numpy as np
import pandas as pd

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
SEANCES = [("l'Asie", 0, 9), ("Londres", 9, 14), ("New York", 14, 24)]
FORT_PCT = 1.0            # variation d'un jour à partir de laquelle on parle de forte hausse / forte baisse
NB_JOURS = 7


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=1, signe=False, unite=""):
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "n.d."
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def _jour(serie, jour, mode):
    """Variation d'une série quotidienne ce jour-là : en %, en points de base ou en points. None si pas de relevé."""
    if serie is None:
        return None
    s = pd.to_numeric(serie, errors="coerce").dropna()
    if not len(s):
        return None
    idx = pd.DatetimeIndex(s.index)
    idx = idx.tz_localize(None) if idx.tz is not None else idx
    s = pd.Series(s.to_numpy(), index=idx.normalize())
    s = s[~s.index.duplicated(keep="last")]
    j = pd.Timestamp(jour)
    if j not in s.index:
        return None
    p = s.index.get_loc(j)
    if p == 0:
        return None
    a, b = float(s.iloc[p - 1]), float(s.iloc[p])
    return (b / a - 1) * 100 if mode == "pct" else ((b - a) * 100 if mode == "pb" else b - a)


def rsi(c, n=14):
    d = c.diff()
    h = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    b = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + h / b.replace(0, np.nan))


def journees(hb):
    """Bougies horaires (heure de Paris) -> une ligne par jour de marché."""
    df = hb[["open", "high", "low", "close"]].dropna()
    g = df.groupby(df.index.date)
    D = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(), "close": g["close"].last(),
                      "n": g["close"].size()})
    D.index = pd.DatetimeIndex(D.index)
    return D[(D.index.weekday < 5) & (D["n"] >= 6)]


def analyser(y, f, intra, cal, annonces_hist, titres, regime, now, ajust=0.0):
    """Liste des dernières séances, la plus récente d'abord, chacune avec ses explications."""
    hb = (intra or {}).get("heures")
    if hb is None or len(hb) < 24 * 40:
        return None
    D = journees(hb)
    if len(D) < 30:
        return None
    D["var"] = D["close"].diff()
    D["pct"] = D["close"].pct_change() * 100
    D["amp"] = D["high"] - D["low"]
    D["amp_moy"] = D["amp"].rolling(20).mean().shift(1)
    D["h20"] = D["high"].rolling(20).max().shift(1)
    D["l20"] = D["low"].rolling(20).min().shift(1)
    D["rsi"] = rsi(D["close"]).shift(1)
    D["avant10"] = (D["close"].shift(1) / D["close"].shift(11) - 1) * 100
    amp_h = float((hb["high"] - hb["low"]).tail(24 * 20).median())
    br, bd = (regime or {}).get("beta_reel"), (regime or {}).get("beta_dxy")
    evts = [e for e in (cal or []) if e.get("date") is not None and e.get("impact") == "High"]
    out = []
    for jour in D.index[-NB_JOURS:][::-1]:
        r, p = D.loc[jour], D.index.get_loc(jour)
        if p == 0 or not np.isfinite(r["pct"]):
            continue
        prec = D.iloc[p - 1]
        sens = 1 if r["var"] > 0 else -1
        en_cours = jour.date() == now.date()
        X = {"date": jour, "en_cours": en_cours, "pct": float(r["pct"]), "var": float(r["var"]), "cloture": float(r["close"]) + ajust,
             "haut": float(r["high"]) + ajust, "bas": float(r["low"]) + ajust, "sens": sens,
             "fort": abs(r["pct"]) >= FORT_PCT, "calme": abs(r["pct"]) < 0.25, "fonda": [], "tech": [], "evts": [], "titres": []}

        # ---- fondamental : ce qui a bougé le même jour
        d_dxy, d_reel, d_us2 = _jour(y.get("dxy"), jour, "pct"), _jour(f.get("reel10"), jour, "pb"), _jour(f.get("us2"), jour, "pb")
        d_vix, d_spx, d_brent = _jour(y.get("vix"), jour, "pts"), _jour(y.get("spx"), jour, "pct"), _jour(y.get("brent"), jour, "pct")
        def fact(lib, v, seuil, effet, d, unite, pourquoi_h, pourquoi_b):
            if v is None or abs(v) < seuil:
                return
            e = effet * (1 if v > 0 else -1)                 # +1 : soutient l'or, −1 : pèse sur l'or
            X["fonda"].append({"lib": lib, "txt": f"{lib} {fr(v, d, True, unite)}", "effet": e,
                               "pourquoi": pourquoi_h if v > 0 else pourquoi_b, "explique": e == sens})
        fact("Dollar", d_dxy, 0.25, -1, 2, " %", "un dollar plus cher rend l'or plus coûteux pour le reste du monde",
             "un dollar plus faible rend l'or moins cher pour le reste du monde")
        fact("Taux réels 10 ans", d_reel, 4, -1, 0, " pb", "des taux réels plus hauts rendent l'or, qui ne rapporte rien, moins attirant",
             "des taux réels plus bas rendent l'or plus attirant face aux obligations")
        fact("Taux 2 ans", d_us2, 5, -1, 0, " pb", "le marché attend une Fed plus dure", "le marché attend une Fed plus souple")
        fact("Pétrole", d_brent, 2.0, 1, 1, " %", "un pétrole en hausse ravive la peur de l'inflation",
             "un pétrole en baisse calme la peur de l'inflation")
        if d_vix is not None and abs(d_vix) >= 1.5:
            if d_vix > 0 and sens < 0 and (d_spx or 0) <= -1.0:
                X["fonda"].append({"lib": "Vente générale", "txt": f"Actions {fr(d_spx, 1, True, ' %')}, VIX {fr(d_vix, 1, True)}", "effet": -1,
                                   "pourquoi": "quand tout chute, les investisseurs vendent aussi leur or pour trouver du cash", "explique": True})
            else:
                e = 1 if d_vix > 0 else -1
                X["fonda"].append({"lib": "Peur (VIX)", "txt": f"VIX {fr(d_vix, 1, True)}", "effet": e,
                                   "pourquoi": "la peur pousse vers l'or, valeur refuge" if d_vix > 0 else "le calme revient, moins besoin de refuge",
                                   "explique": e == sens})
        elif d_spx is not None and abs(d_spx) >= 1.0:
            e = -1 if d_spx > 0 else 1
            X["fonda"].append({"lib": "Actions", "txt": f"S&P 500 {fr(d_spx, 1, True, ' %')}", "effet": e,
                               "pourquoi": "l'appétit pour le risque détourne de l'or" if d_spx > 0 else "les actions baissent, l'or sert de refuge",
                               "explique": e == sens})
        X["macro"] = None
        if br is not None and bd is not None and d_dxy is not None and d_reel is not None:
            attendu = br * d_reel + bd * d_dxy                 # en % d'or, d'après la relation des 60 derniers jours
            part = attendu / r["pct"] if abs(r["pct"]) > 0.05 else 0.0
            X["macro"] = {"attendu": float(attendu), "part": float(max(-1.0, min(1.5, part)))}

        # ---- annonces du jour et bougie la plus violente
        hj = hb[hb.index.date == jour.date()]
        for e in evts:
            if e["date"].date() == jour.date() and e["date"] <= now:
                X["evts"].append({"t": e["date"], "titre": e["titre"], "reel": e.get("reel"), "prev": e.get("prevision"),
                                  "prec": e.get("precedent")})
        for a_ in annonces_hist or []:
            try:
                t = pd.Timestamp(a_["t"]).to_pydatetime()
            except Exception:
                continue
            if t.date() == jour.date() and not any(abs((x["t"] - t).total_seconds()) < 600 for x in X["evts"]):
                X["evts"].append({"t": t, "titre": a_.get("type") or "Annonce", "reel": a_.get("reel"), "prev": a_.get("prev"), "prec": None})
        X["choc"] = None
        if len(hj):
            corps = (hj["close"] - hj["open"])
            k = corps.abs().idxmax()
            if abs(corps.loc[k]) >= 2 * amp_h:
                ev = next((x for x in X["evts"] if abs((x["t"] - k.to_pydatetime()).total_seconds()) <= 3600
                           or 0 <= (x["t"] - k.to_pydatetime()).total_seconds() < 3600), None)
                X["choc"] = {"heure": k.hour, "var": float(corps.loc[k]), "evt": ev["titre"] if ev else None}
            par = []
            for nom, a0, a1 in SEANCES:
                s_ = hj[(hj.index.hour >= a0) & (hj.index.hour < a1)]
                if len(s_):
                    par.append((nom, float(s_["close"].iloc[-1] - s_["open"].iloc[0])))
            if par:
                nom, v = max(par, key=lambda x: x[1] * sens)
                if v * sens > 0 and abs(v) >= 0.5 * abs(r["var"]):
                    X["seance"] = (nom, v)

        # ---- technique
        T = X["tech"]
        if np.isfinite(r["l20"]) and r["low"] < r["l20"]:
            T.append(("Plus bas de 20 jours cassé", f"sous {fr(r['l20'] + ajust)} : les stops des acheteurs sautent et des vendeurs de cassure entrent, "
                                                   "ce qui accélère la baisse", -1))
        elif r["close"] < prec["low"]:
            T.append(("Clôture sous le plus bas de la veille", f"sous {fr(prec['low'] + ajust)} : les acheteurs de la veille sont tous en perte", -1))
        if np.isfinite(r["h20"]) and r["high"] > r["h20"]:
            T.append(("Plus haut de 20 jours cassé", f"au-dessus de {fr(r['h20'] + ajust)} : les stops des vendeurs sautent et des acheteurs de cassure "
                                                    "entrent, ce qui accélère la hausse", 1))
        elif r["close"] > prec["high"]:
            T.append(("Clôture au-dessus du plus haut de la veille", f"au-dessus de {fr(prec['high'] + ajust)} : les vendeurs de la veille sont en perte", 1))
        if np.isfinite(r["amp_moy"]) and r["amp_moy"] > 0 and r["amp"] >= 1.5 * r["amp_moy"]:
            T.append(("Amplitude inhabituelle", f"{fr(r['amp'], 0)} $ dans la journée, {fr(r['amp'] / r['amp_moy'], 1)} fois l'habitude", 0))
        if sens < 0 and np.isfinite(r["avant10"]) and r["avant10"] >= 3 and abs(r["pct"]) >= 0.6:
            T.append(("Prises de bénéfices", f"l'or venait de prendre {fr(r['avant10'], 1, True, ' %')} en 10 jours"
                                             + (f" (RSI {r['rsi']:.0f}, marché suracheté)" if np.isfinite(r["rsi"]) and r["rsi"] >= 70 else "")
                                             + " : beaucoup de gains à encaisser", -1))
        if sens > 0 and np.isfinite(r["avant10"]) and r["avant10"] <= -3 and abs(r["pct"]) >= 0.6:
            T.append(("Rebond technique", f"l'or venait de perdre {fr(r['avant10'], 1, True, ' %')} en 10 jours"
                                          + (f" (RSI {r['rsi']:.0f}, marché survendu)" if np.isfinite(r["rsi"]) and r["rsi"] <= 30 else "")
                                          + " : les vendeurs rachètent", 1))
        if r["amp"] > 0 and not en_cours:
            pos = (r["close"] - r["low"]) / r["amp"]
            if sens < 0 and pos <= 0.25:
                T.append(("Clôture près du plus bas", "les vendeurs ont gardé la main jusqu'au bout, aucun rebond", -1))
            elif sens < 0 and pos >= 0.6:
                T.append(("Longue mèche basse", f"le prix est tombé à {fr(r['low'] + ajust)} puis a été racheté : des acheteurs défendent ce niveau", 1))
            elif sens > 0 and pos >= 0.75:
                T.append(("Clôture près du plus haut", "les acheteurs ont gardé la main jusqu'au bout", 1))
            elif sens > 0 and pos <= 0.4:
                T.append(("Longue mèche haute", f"le prix est monté à {fr(r['high'] + ajust)} puis a été vendu : des vendeurs défendent ce niveau", -1))

        # ---- titres du jour
        for it in titres or []:
            d_ = it.get("date")
            if d_ is not None and d_.date() == jour.date() and len(X["titres"]) < 3:
                t_ = re.sub(r"\s+", " ", it["titre"]).strip()
                if t_ and t_.lower() not in [x["titre"].lower() for x in X["titres"]]:
                    X["titres"].append({"titre": t_, "source": it.get("source") or "", "lien": it.get("lien") or "#"})
        X["clair"] = en_clair(X)
        out.append(X)
    return out


def en_clair(X):
    """Deux ou trois phrases : le pourquoi de la séance."""
    quoi = ("Séance calme" if X["calme"] else ("Forte hausse" if X["sens"] > 0 else "Forte baisse") if X["fort"] else ("Hausse" if X["sens"] > 0 else "Baisse"))
    ph = [f"{quoi} de {fr(abs(X['pct']), 2)} % ({fr(X['var'], 0, True)} $)" + (", séance en cours." if X["en_cours"] else ".")]
    if X["calme"]:
        ph.append("Rien de notable : ni le dollar ni les taux n'ont franchement bougé." if not X["fonda"] else
                  "L'or n'a pas suivi : " + ", ".join(x["txt"] for x in X["fonda"][:2]) + ".")
        return " ".join(ph)
    oui = [x for x in X["fonda"] if x["explique"]]
    non = [x for x in X["fonda"] if not x["explique"]]
    if oui:
        ph.append("Côté fondamental : " + " ; ".join(f"{x['txt']} ({x['pourquoi']})" for x in oui[:2]) + ".")
    m = X.get("macro")
    if m and oui and m["part"] >= 0.25:
        ph.append(f"Taux et dollar expliquent environ {min(100, round(m['part'] * 100))} % du mouvement" + ("." if m["part"] >= 0.8 else " ; le reste vient des flux."))
    elif not oui:
        ph.append("Ni le dollar ni les taux ne l'expliquent" + (f" (ils allaient même dans l'autre sens : {', '.join(x['txt'] for x in non[:2])})" if non else "")
                  + (" : c'est un mouvement de flux (achats refuge, achats de fond ou vendeurs qui rachètent) ou technique." if X["sens"] > 0 else
                     " : c'est un mouvement de flux (prises de bénéfices ou liquidations) ou technique."))
    c = X.get("choc")
    if c and (c["var"] > 0) == (X["sens"] > 0):
        ph.append(f"Le gros du mouvement : la bougie de {c['heure']} h ({fr(c['var'], 0, True)} $)"
                  + (f", au moment de l'annonce {c['evt']}." if c["evt"] else ", sans annonce à cette heure-là."))
    elif c:
        ph.append(f"À noter : un coup brutal dans l'autre sens à {c['heure']} h ({fr(c['var'], 0, True)} $)"
                  + (f", sur l'annonce {c['evt']}" if c["evt"] else "") + ", effacé ensuite.")
    if not (c and (c["var"] > 0) == (X["sens"] > 0)) and X.get("seance"):
        ph.append(f"Le mouvement s'est fait surtout pendant {X['seance'][0]} ({fr(X['seance'][1], 0, True)} $).")
    t = [x for x in X["tech"] if x[2] == X["sens"]]
    if t:
        ph.append("Côté technique : " + t[0][0].lower() + ", " + t[0][1] + ".")
    return " ".join(ph)


def releve(P, n=4):
    """Lignes pour le relevé envoyé à l'analyste IA."""
    L = []
    for X in (P or [])[:n]:
        L.append(f"  {JOURS[X['date'].weekday()]} {X['date']:%d/%m}{' (en cours)' if X['en_cours'] else ''} : {fr(X['pct'], 2, True)} % ({fr(X['var'], 0, True)} $), "
                 f"de {fr(X['bas'])} à {fr(X['haut'])}. " + " ; ".join(x["txt"] for x in X["fonda"])
                 + (" ; annonces : " + ", ".join(f"{e['titre']} (réel {e['reel'] or 'n.d.'}, prévu {e['prev'] or 'n.d.'})" for e in X["evts"]) if X["evts"] else "")
                 + (" ; technique : " + ", ".join(t[0] for t in X["tech"]) if X["tech"] else "")
                 + (" ; titres : " + " | ".join(t["titre"] for t in X["titres"]) if X["titres"] else ""))
    return L


def bloc(P, note_ia=None):
    """Rubrique de la Vue d'ensemble."""
    if not P:
        return ""
    cartes = ""
    for X in P:
        cl = "flat" if X["calme"] else ("up" if X["sens"] > 0 else "down")
        etiq = (f'<span class="pq-fort {cl}">{"FORTE HAUSSE" if X["sens"] > 0 else "FORTE BAISSE"}</span>' if X["fort"] else
                ('<span class="pq-fort flat">CALME</span>' if X["calme"] else ""))
        puces = "".join(f'<span class="pq-p {"up" if x["effet"] > 0 else "down"}{"" if x["explique"] else " pq-contre"}" '
                        f'title="{esc(x["pourquoi"])}{"" if x["explique"] else " — mais l’or a fait l’inverse"}">{esc(x["txt"])}</span>' for x in X["fonda"])
        puces += "".join(f'<span class="pq-p pq-t {"up" if s > 0 else "down" if s < 0 else ""}" title="{esc(d)}">{esc(lib)}</span>' for lib, d, s in X["tech"])
        ev = "".join(f'<li><b>{e["t"]:%H:%M}</b> {esc(e["titre"])} · réel {esc(e["reel"] or "n.d.")} · prévu {esc(e["prev"] or "n.d.")}</li>' for e in X["evts"][:3])
        ti = "".join(f'<li><a href="{esc(t["lien"])}" target="_blank" rel="noopener">{esc(t["titre"])}</a>'
                     f'{" · " + esc(t["source"]) if t["source"] else ""}</li>' for t in X["titres"])
        cartes += (f'<article class="pq-j pq-{cl}{" pq-gros" if X["fort"] else ""}"><div class="pq-tete"><span class="pq-date">{JOURS[X["date"].weekday()]} {X["date"]:%d/%m}'
                   f'{" · en cours" if X["en_cours"] else ""}</span>{etiq}</div>'
                   f'<div class="pq-var {cl}"><b>{fr(X["pct"], 2, True)} %</b><span>{fr(X["var"], 0, True)} $ · {fr(X["bas"])} – {fr(X["haut"])}</span></div>'
                   f'<p class="pq-clair">{esc(X["clair"])}</p>'
                   + (f'<div class="pq-puces">{puces}</div>' if puces else "")
                   + (f'<ul class="pq-ev"><li class="k">Annonces du jour</li>{ev}</ul>' if ev else "")
                   + (f'<ul class="pq-ti"><li class="k">Dans la presse</li>{ti}</ul>' if ti else "") + '</article>')
    ia = ""
    if note_ia:
        ia = (f'<div class="pq-ia"><span class="k">L\'explication de l\'analyste IA</span><p>{esc(note_ia)}</p></div>')
    return (f'<section class="pq" data-zone="pq" aria-label="Pourquoi l\'or bouge"><div class="pq-h"><h2>Pourquoi l\'or bouge</h2>'
            f'<p>Chaque séance expliquée : ce qui a bougé en même temps (dollar, taux, peur), les annonces, la technique. '
            f'Pastille verte : ça soutient l\'or ; rouge : ça pèse ; barrée : l\'or a fait l\'inverse. Ce sont des explications '
            f'probables, pas des certitudes.</p></div>{ia}<div class="pq-grille">{cartes}</div></section>')


CSS = """
/* Pourquoi l'or bouge */
.pq{margin:0 0 14px}
.pq-h{display:flex;flex-wrap:wrap;align-items:baseline;gap:4px 16px;margin-bottom:8px}
.pq-h h2{font:700 17px var(--text);margin:0;color:var(--brass)} .pq-h p{margin:0;font-size:12.5px;color:var(--mute);line-height:1.5;max-width:110ch}
.pq-ia{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--brass);border-radius:10px;padding:11px 14px;margin-bottom:10px}
.pq-ia p{margin:4px 0 0;font-size:14.5px;line-height:1.55;max-width:120ch}
.pq-grille{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px;align-items:start}
.pq-j{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0;display:flex;flex-direction:column;gap:8px}
.pq-j.pq-gros.pq-down{border-color:rgba(255,90,95,.6);background:linear-gradient(180deg,rgba(255,90,95,.08),var(--panel) 60%)}
.pq-j.pq-gros.pq-up{border-color:rgba(47,210,125,.6);background:linear-gradient(180deg,rgba(47,210,125,.08),var(--panel) 60%)}
.pq-tete{display:flex;justify-content:space-between;align-items:center;gap:8px}
.pq-date{font:600 12px var(--text);letter-spacing:.04em;text-transform:uppercase;color:var(--mute)}
.pq-fort{font:800 10.5px var(--text);letter-spacing:.06em;border-radius:5px;padding:2px 7px;border:1px solid currentColor}
.pq-fort.up{color:var(--up)} .pq-fort.down{color:var(--down)} .pq-fort.flat{color:var(--mute)}
.pq-var{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap} .pq-var b{font:700 24px var(--mono);letter-spacing:-.02em}
.pq-var.up b{color:var(--up)} .pq-var.down b{color:var(--down)} .pq-var.flat b{color:var(--steel)}
.pq-var span{font:500 12px var(--mono);color:var(--mute)}
.pq-clair{margin:0;font-size:13.5px;line-height:1.55}
.pq-puces{display:flex;flex-wrap:wrap;gap:5px}
.pq-p{font:600 11.5px var(--text);border-radius:5px;padding:2px 7px;border:1px solid var(--line);color:var(--mute);cursor:help}
.pq-p.up{color:var(--up);border-color:rgba(47,210,125,.45)} .pq-p.down{color:var(--down);border-color:rgba(255,90,95,.45)}
.pq-p.pq-contre{text-decoration:line-through;opacity:.6} .pq-p.pq-t{border-style:dashed}
.pq-ev,.pq-ti{list-style:none;margin:0;padding:6px 0 0;border-top:1px solid var(--band2);display:flex;flex-direction:column;gap:3px;font-size:12.5px;line-height:1.45}
.pq-ev .k,.pq-ti .k{color:var(--mute)} .pq-ev b{font-family:var(--mono);font-weight:600}
.pq-ti a{color:var(--ink)}
"""
