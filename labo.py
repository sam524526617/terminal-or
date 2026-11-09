# -*- coding: utf-8 -*-
"""
LABO DES STRATÉGIES : toutes les stratégies testées, leurs vrais résultats de backtest et leur statut
====================================================================================================

Une stratégie n'est « validée » que si elle passe tous les critères de labo.json (positive sur la période de choix
ET sur la période de vérification, profit factor suffisant, robuste aux coûts doublés, assez de trades).
Tant que la stratégie qui produit les signaux en direct n'est pas validée, la page l'affiche clairement.
Les résultats viennent des backtests (backtest/backtest_plan.py, backtest/strategies_mtf.py) et sont recopiés dans
labo.json après chaque test.
"""

import html
import json
from pathlib import Path

FICHIER = Path(__file__).resolve().parent / "labo.json"


def esc(x):
    return html.escape(str(x), quote=True)


def fr(x, d=2, signe=False, unite=""):
    if x is None:
        return "—"
    s = f"{x:+,.{d}f}" if signe else f"{x:,.{d}f}"
    return s.replace(",", " ").replace(".", ",").replace("-", "−") + unite


def charger():
    try:
        return json.loads(FICHIER.read_text(encoding="utf-8"))
    except Exception:
        return None


def strategie_en_direct(lab):
    return next((s for s in (lab or {}).get("strategies", []) if s.get("en_direct")), None)


def bandeau(lab):
    """Avertissement au-dessus de la décision tant que la stratégie en direct n'est pas validée."""
    s = strategie_en_direct(lab)
    if not s or s.get("statut") == "validée":
        return ""
    p = s.get("periodes", {})
    chiffres = " · ".join(f"{k} : {fr(v.get('r_moyen'), 2, True)} R par trade" for k, v in p.items())
    return (f'<div class="lb-bandeau" role="note"><b>Démo uniquement.</b> Les signaux ci-dessous viennent de '
            f'« {esc(s["nom"])} », {esc(s["statut"])} au backtest ({esc(chiffres)}). Ne les prends pas en réel tant '
            f'qu\'une stratégie n\'est pas validée. <a href="#analyse" data-aller="labo">Voir le labo</a></div>')


def bloc_labo(lab):
    if not lab:
        return '<section><h2>Labo des stratégies</h2><p class="vide">labo.json introuvable.</p></section>'
    crit = "".join(f"<li>{esc(c)}</li>" for c in lab.get("criteres", []))
    validees = [s for s in lab["strategies"] if s.get("statut") == "validée"]
    cartes = ""
    for s in lab["strategies"]:
        st = s.get("statut", "à l'étude")
        cls = {"validée": "up", "rejetée": "down"}.get(st, "warn")
        bord = {"up": "lb-ok", "down": "lb-ko", "warn": "lb-att"}[cls]
        lignes = ""
        for nom_p, v in s.get("periodes", {}).items():
            rm = v.get("r_moyen")
            lignes += (f'<tr><th scope="row">{esc(nom_p)}{" <small>(" + esc(v["source"]) + ")</small>" if v.get("source") else ""}</th>'
                       f'<td class="num">{v.get("trades", "—")}</td>'
                       f'<td class="num">{fr(v.get("reussite"), 0, unite=" %") if v.get("reussite") is not None else "—"}</td>'
                       f'<td class="num {"up" if (rm or 0) > 0 else "down" if (rm or 0) < 0 else ""}">{fr(rm, 3, True, " R")}</td>'
                       f'<td class="num">{fr(v.get("total"), 1, True, " R")}</td>'
                       f'<td class="num">{fr(v.get("pf"), 2)}</td></tr>')
        cartes += (f'<article class="lb-carte {bord}"><div class="lb-tete"><div><b>{esc(s["nom"])}</b>'
                   f'<small>{esc(s.get("actif", ""))}{" · produit les signaux en direct" if s.get("en_direct") else ""}</small></div>'
                   f'<span class="lb-statut {cls}">{esc(st)}</span></div><p class="lb-regles">{esc(s.get("regles", ""))}</p>'
                   f'<div class="tbl"><table class="lb-tab"><thead><tr><th>Période</th><th>Trades</th><th>Réussite</th>'
                   f'<th>Par trade</th><th>Total</th><th>PF</th></tr></thead><tbody>{lignes}</tbody></table></div>'
                   f'<p class="lb-note">{esc(s.get("note", ""))}</p></article>')
    resume = (f'<b class="up">{len(validees)} stratégie(s) validée(s)</b> : ' + ", ".join(esc(s["nom"]) for s in validees)
              if validees else '<b class="warn">Aucune stratégie validée pour l\'instant.</b> Le terminal reste un outil '
              'd\'analyse et d\'entraînement en démo tant qu\'aucune ne passe tous les critères.')
    return (f'<section><h2>Labo des stratégies</h2><p class="lb-resume">{resume}</p>'
            f'<p class="pourquoi">Chaque stratégie est rejouée sur les bougies 1 minute de 2019 à aujourd\'hui, avec '
            f'l\'écart achat-vente. On la choisit sur 2019-2024, puis on la vérifie sur 2025-2026, des années qu\'elle n\'a '
            f'pas vues. Une stratégie n\'est validée que si elle remplit tous ces critères :</p><ul class="lb-crit">{crit}</ul>'
            f'<div class="lb-cartes">{cartes}</div>'
            f'<p class="pourquoi">Résultats recopiés le {esc(lab.get("maj", ""))} depuis les backtests du dépôt '
            f'(onglet Actions de GitHub, « Backtest du plan » et « Backtest stratégies MTF »).</p></section>')


CSS = """
/* Labo des stratégies */
.lb-bandeau{margin:10px 14px 0;padding:10px 14px;border:1px solid rgba(240,169,75,.55);border-radius:8px;
  background:rgba(240,169,75,.08);color:var(--ink);font-size:14px;line-height:1.5}
.lb-bandeau b{color:var(--amber)} .lb-bandeau a{color:var(--brass);text-decoration:underline}
.lb-resume{font-size:15.5px;margin:0 0 8px}
.lb-crit{margin:0 0 18px;padding-left:20px;font-size:14px;line-height:1.6;color:var(--mute)}
.lb-cartes{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:12px;margin-bottom:14px}
.lb-carte{background:var(--panel);border:1px solid var(--line);border-left:3px solid var(--line);border-radius:8px;padding:14px 16px;
  display:flex;flex-direction:column;gap:8px;min-width:0}
.lb-carte.lb-ok{border-left-color:var(--up)} .lb-carte.lb-ko{border-left-color:var(--down)} .lb-carte.lb-att{border-left-color:var(--amber)}
.lb-tete{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}
.lb-tete b{font:600 17px var(--cond)} .lb-tete small{display:block;color:var(--mute);font-size:12.5px}
.lb-statut{font:700 12px var(--cond);text-transform:uppercase;letter-spacing:.06em;padding:3px 9px;border-radius:4px;border:1px solid currentColor;white-space:nowrap}
.lb-statut.up{color:var(--up)} .lb-statut.down{color:var(--down)} .lb-statut.warn{color:var(--amber)}
.lb-regles{margin:0;font-size:13.5px;color:var(--mute);line-height:1.45}
.lb-tab{width:100%;border-collapse:collapse;font-size:13.5px}
.lb-tab th{text-align:left;font:600 11px var(--cond);text-transform:uppercase;letter-spacing:.06em;color:var(--mute);padding:6px 8px;border-bottom:1px solid var(--line)}
.lb-tab tbody th{font:600 13.5px var(--cond);text-transform:none;letter-spacing:0;color:var(--ink)}
.lb-tab tbody th small{font-weight:500;color:var(--mute)}
.lb-tab td{padding:7px 8px;border-bottom:1px solid var(--band2)}
.lb-tab td.num{font-family:var(--cond);font-variant-numeric:tabular-nums;white-space:nowrap}
.lb-note{margin:0;font-size:13px;line-height:1.45}
@media (max-width:640px){.lb-cartes{grid-template-columns:minmax(0,1fr)}.lb-bandeau{margin:8px 8px 0}}
"""
