# -*- coding: utf-8 -*-
"""
ENTRAÎNEMENT ET SIMULATEUR DE RISQUE (fonction TRAIN)
=====================================================

Deux outils qui servent à progresser, pas à prédire :

1. Entraînement à l'aveugle : le terminal tire au hasard un moment des dernières semaines, montre les vraies bougies
   15 min jusque-là (date cachée), et demande une décision : achat, vente ou rien. Stop à 1,5 fois la bougie moyenne
   (ATR), objectif à 2 fois le risque, 4 heures maximum, coûts comptés. La suite est ensuite rejouée et le résultat
   s'ajoute au score. Le but : mesurer honnêtement sa lecture du graphique, et apprendre à passer son tour.

2. Simulateur de risque : à partir d'un taux de réussite, d'un gain moyen et d'un risque par trade, 3 000 suites de
   trades tirées au hasard montrent ce qui attend vraiment le compte : résultat médian, mauvais scénario, chances de
   perdre 20 % ou 50 %. Les chiffres peuvent venir du journal ou de l'entraînement.

Tout est calculé dans le navigateur ; le bloc est placé hors des zones rafraîchies par le robot.
"""

import json

import pandas as pd

JOURS = 45          # profondeur des bougies fournies à l'entraînement
COUT = 0.40         # écart achat-vente + glissement, en $ par aller-retour (comme le backtest)


def donnees(intra, ajust=0.0):
    """Bougies 15 min des dernières semaines : [minute epoch, ouverture, haut, bas, clôture], au prix affiché."""
    b = (intra or {}).get("barres")
    if b is None or len(b) < 400:
        return []
    b = b.rename(columns=str.lower)[["open", "high", "low", "close"]].dropna()
    b = b[b.index >= b.index[-1] - pd.Timedelta(days=JOURS)]
    idx = b.index.tz_convert("UTC").tz_localize(None) if b.index.tz is not None else b.index
    t = (idx - pd.Timestamp(0)) // pd.Timedelta(minutes=1)
    v = (b.to_numpy() + float(ajust or 0.0)).round(1)
    return [[int(m), *map(float, r)] for m, r in zip(t, v)]


ETAPES = [
    ("Y a-t-il une annonce ?", "annonces", "ECO",
     "L'en-tête affiche la prochaine annonce forte et son compte à rebours. Un bandeau rouge apparaît quand elle approche.",
     "Pas d'entrée 30 minutes avant ni 30 minutes après une annonce forte : le prix part dans tous les sens et les stops sautent. "
     "Après la publication, le bandeau défilant résume le chiffre et la réaction de l'or."),
    ("Que dit le fondamental ?", "fondamental", "FA",
     "Le gros mot en haut de l'écran Fondamental : HAUSSIER, BAISSIER ou NEUTRE. Il résume les taux réels, le dollar et les taux 2 ans.",
     "S'il est baissier, n'achète pas ; s'il est haussier, ne vends pas. Neutre : il ne bloque rien, c'est la technique qui décide. "
     "La rubrique « Pourquoi l'or bouge » explique les derniers jours en clair."),
    ("Que dit la technique ?", "resume", "RES",
     "Le résumé technique : un verdict (HAUSSIER, BAISSIER, EN CORRECTION, PARTAGÉ) et quatre flèches, du Daily au 15 minutes.",
     "Le plan ne trade que si le Daily et le 4 heures vont dans le même sens et que le 1 heure ne les contredit pas. "
     "« En correction » ou « partagé » veut dire : attendre. La phrase de synthèse donne le prix exact qui changerait la situation."),
    ("Où sont les niveaux ?", "accueil", "GO",
     "Le panneau « Niveaux en direct » de la Vue d'ensemble : zones d'achat (vert) et de vente (rouge), sommets et creux à casser, "
     "niveaux de la veille. La carte des options (OPT) ajoute les murs.",
     "On n'achète pas juste sous une résistance et on ne vend pas juste au-dessus d'un support. Une bonne entrée se fait près d'un "
     "niveau, avec de la place jusqu'au suivant pour l'objectif."),
    ("La journée a-t-elle encore de la place ?", "stat", "STAT",
     "Dans le résumé technique, la carte « Amplitude » : la part de l'amplitude habituelle déjà faite aujourd'hui, et le mouvement "
     "attendu par les options.",
     "Au-delà de 100 %, la journée a déjà beaucoup bougé : viser loin devient peu réaliste. L'écran STAT montre à quelles heures "
     "l'or fait d'habitude son plus haut et son plus bas."),
    ("Que font les gros et la foule ?", "data", "DATA",
     "Les cartes Fonds, Banques et Particuliers, et la jauge de risque (RISK).",
     "C'est du contexte, pas un signal d'entrée. Un extrême compte : des fonds très exposés ou une foule très acheteuse rendent "
     "un retournement plus probable. Le rapport COT ne sort qu'une fois par semaine."),
    ("Que dit le plan ?", "accueil", "GO",
     "Le panneau « Décision du plan » : RIEN, ACHAT ou VENTE, la liste des filtres, et « ce qui débloque ».",
     "RIEN est la réponse normale la plupart du temps. Les signaux sont marqués DÉMO parce que le plan perd au backtest : "
     "sers-t'en pour apprendre à lire, pas pour trader en réel."),
    ("Avant d'entrer", "suivi", "JRN",
     "Le calculateur (touche C, ou CALC dans la barre de commande) : entrée, stop, objectif, taille de position.",
     "Risque 1 % du compte au maximum, stop placé avant d'entrer, objectif d'au moins 2 fois le risque. Note le trade dans le "
     "journal tout de suite : c'est lui qui dira si tu progresses."),
]

PIEGES = [
    ("Un seul indicateur ne suffit jamais.", "Cherche trois éléments d'accord : le fond, la technique, et un niveau proche."),
    ("Quand les éléments se contredisent, la bonne décision est de ne rien faire.", "Passer son tour ne coûte rien."),
    ("Un RSI « suracheté » n'est pas un signal de vente.", "Dans une tendance forte, il peut le rester longtemps."),
    ("Les murs d'options et les zones sont des endroits où le prix réagit souvent, pas des prévisions.", "Ils cassent parfois, et violemment."),
    ("Le terminal explique le passé et décrit le présent.", "Il ne connaît pas l'avenir, et aucune de ses stratégies n'a passé la validation."),
]

LEXIQUE = [
    ("R", "le risque d'un trade : la distance entre l'entrée et le stop. Un gain de 2 R = deux fois ce que tu risquais."),
    ("OB (bloc d'ordres)", "la dernière bougie avant un mouvement fort : une zone où le prix revient souvent réagir."),
    ("FVG (déséquilibre)", "un vide laissé par trois bougies rapides : le prix revient souvent le combler."),
    ("Cassure de structure", "une clôture au-delà du dernier sommet ou creux : c'est ce qui fixe la tendance."),
    ("ATR", "la taille moyenne d'une bougie : sert à placer un stop ni trop serré ni trop large."),
    ("RSI", "la force du mouvement récent, de 0 à 100 : au-dessus de 70 il est étiré vers le haut, sous 30 vers le bas."),
    ("VWAP", "le prix moyen payé dans la journée, pondéré par les volumes."),
    ("COT", "le rapport hebdomadaire qui dit qui est acheteur ou vendeur sur le marché des futures."),
    ("Put / call", "un put protège contre une baisse, un call parie sur une hausse."),
    ("Taux réel", "ce que rapporte une obligation américaine une fois l'inflation retirée : quand il monte, l'or baisse souvent."),
]


def guide():
    """Mode d'emploi : la lecture du terminal dans l'ordre, les pièges, le vocabulaire."""
    etapes = "".join(
        f'<li class="en-etape"><div class="en-et-t"><span class="en-num">{i}</span><h3>{t}</h3>'
        f'<a href="#analyse" data-aller="{cible}" class="en-lien"><span class="fc">{code}</span>ouvrir</a></div>'
        f'<p><b>Où regarder.</b> {ou}</p><p><b>Comment le lire.</b> {lire}</p></li>'
        for i, (t, cible, code, ou, lire) in enumerate(ETAPES, 1))
    pieges = "".join(f'<li><b>{a}</b> {b}</li>' for a, b in PIEGES)
    lex = "".join(f'<div><dt>{a}</dt><dd>{b}</dd></div>' for a, b in LEXIQUE)
    return ('<section class="jr" id="en-guide"><h2>Comment lire le terminal</h2>'
            '<p class="pourquoi">La routine à suivre avant chaque trade, toujours dans le même ordre. Elle prend cinq minutes. '
            'Si une étape dit non, on s\'arrête : pas besoin de regarder la suite.</p>'
            f'<ol class="en-etapes">{etapes}</ol>'
            f'<div class="en-deux"><div class="en-bloc"><h3>Les pièges de lecture</h3><ul class="en-pieges">{pieges}</ul></div>'
            f'<details class="en-bloc"><summary>Le vocabulaire en une ligne</summary><dl class="en-lex">{lex}</dl></details></div></section>')


def bloc(intra, ajust=0.0):
    d = donnees(intra, ajust)
    if len(d) < 400:
        jeu = ('<section class="jr" id="en-jeu"><h2>Entraînement à l\'aveugle</h2>'
               '<p class="vide">Bougies 15 min indisponibles : l\'entraînement reviendra à la prochaine mise à jour.</p></section>')
    else:
        jeu = (
            '<section class="jr" id="en-jeu"><h2>Entraînement à l\'aveugle</h2>'
            '<p class="pourquoi">Un moment réel des dernières semaines, tiré au hasard, date cachée. Lis le graphique et décide : '
            'achat, vente, ou rien. Stop à 1,5 fois la bougie moyenne, objectif à 2 fois le risque, 4 heures maximum, coûts '
            'comptés. La suite est rejouée et ton score dit si ta lecture vaut mieux que le hasard. Passer son tour est une vraie '
            'décision : les bons traders passent souvent.</p>'
            '<div class="en-info" id="en-info"></div>'
            '<div class="en-graph"><canvas id="en-cv" aria-label="Graphique d\'entraînement"></canvas></div>'
            '<div class="en-actions" id="en-actions">'
            '<button type="button" class="en-a" data-s="1">Achat</button>'
            '<button type="button" class="en-v" data-s="-1">Vente</button>'
            '<button type="button" data-s="0">Je passe</button>'
            '<button type="button" class="or" id="en-suiv" hidden>Situation suivante</button></div>'
            '<p class="en-res" id="en-res" role="status" aria-live="polite"></p>'
            '<div class="jr-kpis" id="en-kpis"></div>'
            '<div class="en-bas"><div class="en-courbe" id="en-courbe"></div><div class="en-lecture" id="en-lecture"></div></div>'
            '<div class="jr-outils"><button type="button" id="en-raz">Remettre le score à zéro</button></div>'
            f'<script type="application/json" id="en-data">{json.dumps({"b": d, "cout": COUT}, separators=(",", ":"))}</script>'
            '</section>')
    sim = (
        '<section class="jr" id="en-sim"><h2>Simulateur de risque</h2>'
        '<p class="pourquoi">Ce que tes chiffres donnent vraiment sur la durée. 3 000 suites de trades tirées au hasard avec ton taux '
        'de réussite et ton gain moyen : le résultat du milieu, le mauvais scénario (1 chance sur 20), et les chances de voir le '
        'compte perdre 20 % ou 50 % en route. Change le risque par trade et regarde ce qui bouge.</p>'
        '<form class="en-form" id="en-form" autocomplete="off" onsubmit="return false">'
        '<label>Taux de réussite (%)<input id="en-wr" inputmode="decimal" value="40"></label>'
        '<label>Gain moyen (en R)<input id="en-g" inputmode="decimal" value="2"></label>'
        '<label>Perte moyenne (en R)<input id="en-l" inputmode="decimal" value="1"></label>'
        '<label>Risque par trade (%)<input id="en-rk" inputmode="decimal" value="1"></label>'
        '<label>Nombre de trades<input id="en-n" inputmode="numeric" value="100"></label>'
        '<div class="en-src"><button type="button" id="en-src-j">Chiffres de mon journal</button>'
        '<button type="button" id="en-src-e">Chiffres de l\'entraînement</button></div></form>'
        '<p class="en-res" id="en-sim-msg" role="status" aria-live="polite"></p>'
        '<div class="jr-kpis" id="en-sim-kpis"></div>'
        '<div class="en-graph en-eventail"><canvas id="en-sim-cv" aria-label="Éventail des résultats possibles"></canvas></div>'
        '<p class="en-leg">Éventail des comptes possibles, trade après trade : ligne claire = résultat du milieu, bande foncée = la '
        'moitié des cas, bande claire = 9 cas sur 10.</p>'
        '<div class="en-tab" id="en-sim-tab"></div>'
        '<p class="en-lecture" id="en-sim-lecture"></p></section>')
    return guide() + jeu + sim


CSS = """
.en-etapes{list-style:none;margin:0 0 12px;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px}
.en-etape{background:#0b0c10;border:1px solid var(--band2);border-radius:2px;padding:12px 14px;min-width:0}
.en-et-t{display:flex;align-items:center;gap:10px;margin-bottom:8px}
.en-et-t h3{flex:1;margin:0;font:700 15px var(--titre);letter-spacing:.04em;color:var(--ink);min-width:0}
.en-num{flex:none;width:24px;height:24px;display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--brass);color:var(--brass);font:700 12px var(--mono)}
.en-lien{flex:none;display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--brass);text-decoration:none}
.en-lien:hover{text-decoration:underline}
.en-etape p{margin:0 0 6px;font-size:13.5px;line-height:1.5;color:var(--steel)} .en-etape p:last-child{margin:0} .en-etape p b{color:var(--ink)}
.en-deux{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:10px;align-items:start}
.en-bloc{background:#0b0c10;border:1px solid var(--band2);border-radius:2px;padding:12px 14px;min-width:0}
.en-bloc h3,.en-bloc summary{margin:0 0 8px;font:700 12px var(--titre);letter-spacing:.14em;text-transform:uppercase;color:var(--brass)}
.en-bloc summary{cursor:pointer;margin:0} .en-bloc[open] summary{margin-bottom:8px}
.en-pieges{margin:0;padding-left:18px;font-size:13.5px;line-height:1.55;color:var(--steel)} .en-pieges li{margin-bottom:6px} .en-pieges b{color:var(--ink)}
.en-lex{margin:0;font-size:13.5px;line-height:1.5} .en-lex div{padding:6px 0;border-top:1px solid var(--band2)} .en-lex div:first-child{border:0}
.en-lex dt{display:inline;font-weight:700;color:var(--ink)} .en-lex dt::after{content:" : "} .en-lex dd{display:inline;margin:0;color:var(--steel)}
@media (max-width:800px){.en-deux{grid-template-columns:1fr}}
.en-info{display:flex;flex-wrap:wrap;gap:6px 18px;font:500 13px var(--mono);color:var(--mute);margin:0 0 8px}
.en-info b{color:var(--ink);font-weight:600}
.en-graph{position:relative;height:380px;background:#07080b;border:1px solid var(--band2);border-radius:2px;overflow:hidden;min-width:0}
.en-graph canvas{position:absolute;inset:0;width:100%;height:100%;display:block}
.en-eventail{height:260px;margin-top:10px}
.en-actions{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 6px}
.en-actions button{min-width:120px;padding:10px 16px;font-weight:600}
.en-actions .en-a{border-color:var(--up);color:var(--up)} .en-actions .en-v{border-color:var(--down);color:var(--down)}
.en-actions .en-a:hover{background:rgba(47,210,125,.12)} .en-actions .en-v:hover{background:rgba(255,90,95,.12)}
.en-actions button:disabled{opacity:.35;cursor:default}
.en-res{min-height:22px;font-size:14px;line-height:1.5;margin:4px 0 12px;color:var(--ink)}
.en-res b.up{color:var(--up)} .en-res b.down{color:var(--down)}
.en-bas{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:10px;margin-bottom:10px}
.en-courbe,.en-bas .en-lecture{background:#0b0c10;border:1px solid var(--band2);border-radius:2px;padding:10px 12px;min-height:110px;min-width:0}
.en-courbe svg{display:block;width:100%;height:110px}
.en-lecture{font-size:13.5px;line-height:1.55;color:var(--steel)} .en-lecture b{color:var(--ink)}
.en-form{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;background:#0b0c10;border:1px solid var(--band2);border-radius:2px;padding:12px;margin-bottom:8px}
.en-form label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--mute);min-width:0}
.en-form input{background:#000;border:1px solid var(--line);border-radius:4px;color:var(--ink);font:500 14px var(--mono);padding:8px 10px;min-width:0;width:100%}
.en-form input:focus{outline:none;border-color:var(--brass);box-shadow:0 0 0 2px rgba(126,166,214,.25)}
.en-form input.err{border-color:var(--down)}
.en-src{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:8px}
.en-leg{font-size:12px;color:var(--mute);margin:6px 0 12px}
.en-tab{overflow-x:auto;margin-bottom:10px}
.en-tab table{width:100%;border-collapse:collapse;font:500 13px var(--mono);min-width:520px}
.en-tab th{font:600 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--mute);text-align:right;padding:6px 8px;border-bottom:1px solid var(--line)}
.en-tab td{text-align:right;padding:7px 8px;border-bottom:1px solid var(--band2)}
.en-tab th:first-child,.en-tab td:first-child{text-align:left}
.en-tab tr.ici td{background:rgba(126,166,214,.08);color:var(--ink)}
.en-tab .up{color:var(--up)} .en-tab .down{color:var(--down)} .en-tab .att{color:var(--amber)}
@media (max-width:800px){.en-bas{grid-template-columns:1fr}.en-form{grid-template-columns:repeat(2,minmax(0,1fr))}.en-graph{height:300px}.en-actions button{flex:1 1 40%;min-width:0}}
"""

JS = r"""
(function(){
var T = window.terminalOr = window.terminalOr || {};
var $ = function(s){ return document.querySelector(s); };
function nb(x, d){ if (x == null || !isFinite(x)) return '—'; return x.toFixed(d == null ? 1 : d).replace('.', ',').replace('-', '−').replace(/\B(?=(\d{3})+(?!\d))/g, ' '); }
function sg(x, d){ return (x > 0 ? '+' : '') + nb(x, d); }
function pc(x){ return isFinite(x) ? Math.round(x * 100) + ' %' : '—'; }
function num(el){ var v = parseFloat(String(el.value).replace(/\s/g, '').replace(',', '.')); return isFinite(v) ? v : NaN; }
function toile(cv){
  var r = cv.parentNode.getBoundingClientRect(), k = window.devicePixelRatio || 1;
  if (r.width < 40) return null;
  cv.width = Math.round(r.width * k); cv.height = Math.round(r.height * k);
  var c = cv.getContext('2d'); c.setTransform(k, 0, 0, k, 0, 0); c.clearRect(0, 0, r.width, r.height);
  return {c: c, w: r.width, h: r.height};
}
var VERT = '#2fd27d', ROUGE = '#ff5a5f', BLEU = '#7ea6d6', GRIS = '#9b9ba3', LIGNE = '#1c1c21';

/* ---------- Entraînement à l'aveugle ---------- */
var CLE = 'or-entrainement', HIST = 96, SUITE = 16, K_STOP = 1.5, OBJ = 2;
var brut = document.getElementById('en-data'), B = [], COUT = 0.4;
try { if (brut){ var o = JSON.parse(brut.textContent); B = o.b || []; COUT = o.cout || 0.4; } } catch(e){}
var S = []; try { var v = JSON.parse(localStorage.getItem(CLE) || '[]'); if (Array.isArray(v)) S = v; } catch(e){}
function sauver(){ try { localStorage.setItem(CLE, JSON.stringify(S.slice(-500))); } catch(e){} }
var cand = [];
(function(){
  for (var i = HIST; i + SUITE < B.length; i++){
    var h = new Date(B[i][0] * 60000).getHours();
    if (h < 8 || h > 20) continue;
    if (B[i + SUITE][0] - B[i][0] > SUITE * 15 + 45) continue;          /* pas de fermeture du marché dans la suite */
    if (B[i][0] - B[i - 20][0] > 20 * 15 + 90) continue;                /* ni juste avant */
    cand.push(i);
  }
})();
var M = null;   /* manche en cours : {i, atr, stop, vu (nb de bougies de la suite montrées), sens, fini} */
function atr(i){ var s = 0; for (var k = i - 13; k <= i; k++){ s += Math.max(B[k][2] - B[k][3], Math.abs(B[k][2] - B[k - 1][4]), Math.abs(B[k][3] - B[k - 1][4])); } return s / 14; }
function issue(i, sens, stop){
  /* renvoie {r, n (bougies), motif} ; si stop et objectif sont dans la même bougie, on compte le stop */
  var e = B[i][4], sl = e - sens * stop, tp = e + sens * OBJ * stop, c = COUT / stop;
  for (var k = 1; k <= SUITE; k++){
    var b = B[i + k], touSL = sens > 0 ? b[3] <= sl : b[2] >= sl, touTP = sens > 0 ? b[2] >= tp : b[3] <= tp;
    if (touSL) return {r: -1 - c, n: k, motif: 'stop touché'};
    if (touTP) return {r: OBJ - c, n: k, motif: 'objectif atteint'};
  }
  return {r: sens * (B[i + SUITE][4] - e) / stop - c, n: SUITE, motif: 'sortie au bout de 4 heures'};
}
function nouvelle(){
  if (!cand.length) return;
  var i = cand[Math.floor(Math.random() * cand.length)], a = atr(i);
  M = {i: i, atr: a, stop: Math.max(K_STOP * a, 1), vu: 0, sens: null, fini: false};
  var d = new Date(B[i][0] * 60000), hh = d.getHours() + ' h ' + ('0' + d.getMinutes()).slice(-2);
  $('#en-info').innerHTML = '<span>Heure <b>' + hh + '</b> (date cachée)</span><span>Prix <b>' + nb(B[i][4], 1) + '</b></span>' +
    '<span>Stop <b>' + nb(M.stop, 1) + ' $</b></span><span>Objectif <b>' + nb(OBJ * M.stop, 1) + ' $</b> (' + OBJ + ' R)</span><span>Durée max <b>4 h</b></span>';
  $('#en-res').textContent = '';
  boutons(true); dessiner();
}
function boutons(actifs){
  [].forEach.call(document.querySelectorAll('#en-actions [data-s]'), function(b){ b.disabled = !actifs; });
  $('#en-suiv').hidden = actifs;
}
function dessiner(){
  var cv = $('#en-cv'); if (!cv || !M) return;
  var t = toile(cv); if (!t) return;
  var c = t.c, w = t.w, h = t.h, mg = 56, i0 = M.i - HIST + 1, n = HIST + SUITE, pas = (w - mg - 8) / n;
  var dernier = M.i + M.vu, lo = Infinity, hi = -Infinity;
  for (var k = i0; k <= dernier; k++){ lo = Math.min(lo, B[k][3]); hi = Math.max(hi, B[k][2]); }
  var e = B[M.i][4];
  lo = Math.min(lo, e - M.stop * 1.15); hi = Math.max(hi, e + M.stop * 1.15);
  if (M.sens){ lo = Math.min(lo, e - M.sens * M.stop, e + M.sens * OBJ * M.stop); hi = Math.max(hi, e - M.sens * M.stop, e + M.sens * OBJ * M.stop); }
  var m = (hi - lo) * 0.06; lo -= m; hi += m;
  var X = function(k){ return 8 + (k - i0 + 0.5) * pas; }, Y = function(p){ return 10 + (hi - p) / (hi - lo) * (h - 38); };
  c.font = '11px JetBrains Mono, monospace'; c.textBaseline = 'middle';
  var ech = [1, 2, 5, 10, 20, 50, 100].filter(function(x){ return (hi - lo) / x <= 7; })[0] || 100;
  for (var p = Math.ceil(lo / ech) * ech; p <= hi; p += ech){
    c.strokeStyle = LIGNE; c.beginPath(); c.moveTo(0, Y(p)); c.lineTo(w - mg, Y(p)); c.stroke();
    c.fillStyle = GRIS; c.textAlign = 'left'; c.fillText(nb(p, 0), w - mg + 6, Y(p));
  }
  c.textAlign = 'center';
  for (k = i0; k <= M.i + SUITE; k++){
    var dt = new Date(B[k][0] * 60000);
    if (dt.getMinutes() === 0 && dt.getHours() % 4 === 0){
      c.strokeStyle = LIGNE; c.beginPath(); c.moveTo(X(k), 0); c.lineTo(X(k), h - 24); c.stroke();
      c.fillStyle = GRIS; c.fillText(dt.getHours() + ' h', X(k), h - 12);
    }
  }
  c.fillStyle = 'rgba(126,166,214,.05)'; c.fillRect(X(M.i) + pas / 2, 0, w - mg - X(M.i) - pas / 2, h - 24);   /* la suite, encore cachée */
  for (k = i0; k <= dernier; k++){
    var b = B[k], col = b[4] >= b[1] ? VERT : ROUGE, x = X(k), lg = Math.max(1.5, pas * 0.62);
    c.strokeStyle = col; c.fillStyle = col; c.lineWidth = 1;
    c.beginPath(); c.moveTo(x, Y(b[2])); c.lineTo(x, Y(b[3])); c.stroke();
    c.fillRect(x - lg / 2, Math.min(Y(b[1]), Y(b[4])), lg, Math.max(1, Math.abs(Y(b[1]) - Y(b[4]))));
  }
  function niveau(p, col, txt, pointille){
    c.strokeStyle = col; c.setLineDash(pointille ? [5, 4] : []); c.beginPath(); c.moveTo(X(M.i) - pas * 6, Y(p)); c.lineTo(w - mg, Y(p)); c.stroke(); c.setLineDash([]);
    c.fillStyle = col; c.textAlign = 'right'; c.fillText(txt, w - mg - 4, Y(p) - 8);
  }
  if (M.sens){
    niveau(e, BLEU, 'entrée ' + nb(e, 1), false);
    niveau(e - M.sens * M.stop, ROUGE, 'stop', true);
    niveau(e + M.sens * OBJ * M.stop, VERT, 'objectif', true);
  } else niveau(e, BLEU, 'prix ' + nb(e, 1), true);
}
function jouer(sens){
  if (!M || M.fini) return;
  boutons(false); $('#en-suiv').hidden = true;
  M.sens = sens || null;
  var r = sens ? issue(M.i, sens, M.stop) : null, fin = sens ? r.n : SUITE;
  var lent = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function pas(){
    M.vu = lent ? fin : M.vu + 1; dessiner();
    if (M.vu < fin){ setTimeout(pas, 70); return; }
    M.fini = true; conclure(sens, r);
  }
  pas();
}
var JSEM = ['dimanche', 'lundi', 'mardi', 'mercredi', 'jeudi', 'vendredi', 'samedi'];
function conclure(sens, r){
  var d = new Date(B[M.i][0] * 60000), quand = JSEM[d.getDay()] + ' ' + ('0' + d.getDate()).slice(-2) + '/' + ('0' + (d.getMonth() + 1)).slice(-2);
  var txt;
  if (sens){
    S.push({t: Date.now(), s: sens, r: Math.round(r.r * 100) / 100});
    var mn = r.n * 15, duree = mn >= 60 ? Math.floor(mn / 60) + ' h' + (mn % 60 ? ' ' + mn % 60 : '') : mn + ' min';
    txt = (sens > 0 ? 'Achat' : 'Vente') + ' : ' + r.motif + (r.n < SUITE || r.motif !== 'sortie au bout de 4 heures' ? ' après ' + duree : '') +
      ', <b class="' + (r.r >= 0 ? 'up' : 'down') + '">' + sg(r.r, 2) + ' R</b>.';
  } else {
    var a = issue(M.i, 1, M.stop), v = issue(M.i, -1, M.stop);
    S.push({t: Date.now(), s: 0, r: 0, a: Math.round(a.r * 100) / 100, v: Math.round(v.r * 100) / 100});
    var bien = a.r <= 0 && v.r <= 0;
    txt = 'Tu as passé. Un achat aurait fait <b class="' + (a.r >= 0 ? 'up' : 'down') + '">' + sg(a.r, 2) + ' R</b>, une vente <b class="' +
      (v.r >= 0 ? 'up' : 'down') + '">' + sg(v.r, 2) + ' R</b>.' + (bien ? ' Les deux perdaient : bien vu.' : '');
  }
  $('#en-res').innerHTML = txt + ' C\'était le ' + quand + '.';
  sauver(); score(); $('#en-suiv').hidden = false; $('#en-suiv').focus();
}
function bilan(){
  var pris = S.filter(function(x){ return x.s; }), R = pris.map(function(x){ return x.r; }), n = R.length;
  var o = {dec: S.length, n: n, passes: S.length - n, R: R};
  if (!n) return o;
  var g = R.filter(function(x){ return x > 0; }), p = R.filter(function(x){ return x <= 0; });
  var som = function(a){ return a.reduce(function(x, y){ return x + y; }, 0); };
  o.total = som(R); o.moy = o.total / n; o.reussite = g.length / n;
  o.gain = g.length ? som(g) / g.length : null; o.perte = p.length ? -som(p) / p.length : null;
  var pass = S.filter(function(x){ return !x.s && x.a != null; });
  o.passBien = pass.length ? pass.filter(function(x){ return x.a <= 0 && x.v <= 0; }).length / pass.length : null;
  return o;
}
function score(){
  var o = bilan(), k = $('#en-kpis'); if (!k) return;
  var cl = function(x){ return x > 0 ? ' class="up"' : (x < 0 ? ' class="down"' : ''); };
  k.innerHTML = '<div><span class="k">Décisions</span><b>' + o.dec + '</b><small>' + o.n + ' trades · ' + o.passes + ' passes</small></div>' +
    '<div><span class="k">Réussite</span><b>' + (o.n ? pc(o.reussite) : '—') + '</b><small>équilibre vers 35 % avec un objectif à 2 R</small></div>' +
    '<div><span class="k">Total</span><b' + cl(o.total) + '>' + (o.n ? sg(o.total, 1) + ' R' : '—') + '</b><small>coûts compris</small></div>' +
    '<div><span class="k">Par trade</span><b' + cl(o.moy) + '>' + (o.n ? sg(o.moy, 2) + ' R' : '—') + '</b><small>ce qui compte vraiment</small></div>';
  var cb = $('#en-courbe');
  if (o.n < 2) cb.innerHTML = '<p class="vide">La courbe de ton score apparaît après 2 trades.</p>';
  else {
    var cum = 0, pts = [0].concat(o.R.map(function(x){ cum += x; return cum; })), lo = Math.min.apply(null, pts), hi = Math.max.apply(null, pts);
    if (hi - lo < 1){ hi += 0.5; lo -= 0.5; }
    var X = function(i){ return 4 + i / (pts.length - 1) * 592; }, Y = function(v){ return 6 + (hi - v) / (hi - lo) * 98; };
    cb.innerHTML = '<svg viewBox="0 0 600 110" preserveAspectRatio="none" role="img" aria-label="Score cumulé en R"><line x1="0" x2="600" y1="' + Y(0) + '" y2="' + Y(0) +
      '" stroke="#2a2a31" stroke-dasharray="4 4"/><polyline fill="none" stroke="' + (cum >= 0 ? VERT : ROUGE) + '" stroke-width="2" vector-effect="non-scaling-stroke" points="' +
      pts.map(function(v, i){ return X(i).toFixed(1) + ',' + Y(v).toFixed(1); }).join(' ') + '"/></svg>';
  }
  var L = $('#en-lecture'), t;
  if (o.n < 20) t = 'Encore <b>' + (20 - o.n) + ' trade' + (20 - o.n > 1 ? 's' : '') + '</b> avant une première lecture : en dessous de 20, le hasard domine le score.';
  else {
    var es = Math.sqrt(o.R.reduce(function(s, x){ return s + (x - o.moy) * (x - o.moy); }, 0) / (o.n - 1)) / Math.sqrt(o.n);
    if (o.moy - 2 * es > 0) t = 'Sur ' + o.n + ' trades, <b>' + sg(o.moy, 2) + ' R par trade</b> : l\'écart avec zéro dépasse ce que le hasard explique d\'habitude. Continue jusqu\'à 100 pour confirmer.';
    else if (o.moy + 2 * es < 0) t = 'Sur ' + o.n + ' trades, <b>' + sg(o.moy, 2) + ' R par trade</b> : ta lecture perd plus que le hasard seul. Passe plus souvent, et ne prends que les situations les plus nettes.';
    else t = 'Sur ' + o.n + ' trades, <b>' + sg(o.moy, 2) + ' R par trade</b> : on ne peut pas encore distinguer ce score du hasard (marge de ±' + nb(2 * es, 2) + ' R). Il faut plus de trades.';
  }
  if (o.passBien != null && o.passes >= 5) t += ' Quand tu passes, les deux sens perdaient <b>' + pc(o.passBien) + '</b> du temps.';
  t += ' Un bon score ici ne garantit rien en réel : même période de marché, pas d\'émotion, pas d\'argent en jeu.';
  L.innerHTML = t;
}
if ($('#en-cv') && cand.length){
  $('#en-actions').addEventListener('click', function(e){
    var b = e.target.closest('button'); if (!b) return;
    if (b.id === 'en-suiv'){ nouvelle(); return; }
    if (b.dataset.s != null && !b.disabled) jouer(parseInt(b.dataset.s, 10));
  });
  $('#en-raz').addEventListener('click', function(){ if (S.length && !window.confirm('Effacer tout le score de l\'entraînement ?')) return; S = []; sauver(); score(); });
  nouvelle(); score();
  if (window.ResizeObserver) new ResizeObserver(function(){ dessiner(); }).observe($('#en-cv').parentNode);
  else window.addEventListener('resize', dessiner);
}
T.entrainement = {bilan: bilan};

/* ---------- Simulateur de risque ---------- */
var N_SIM = 3000;
function hasard(g){ return function(){ g |= 0; g = g + 0x6D2B79F5 | 0; var t = Math.imul(g ^ g >>> 15, 1 | g); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function simuler(wr, G, Lp, rk, n, bandes){
  var rnd = hasard(12345), fin = new Float64Array(N_SIM), dd = new Float64Array(N_SIM), serie = new Float64Array(N_SIM);
  var pasB = Math.max(1, Math.floor(n / 60)), nb_ = Math.floor(n / pasB) + 1, grille = bandes ? [] : null;
  if (bandes) for (var j = 0; j < nb_; j++) grille.push(new Float64Array(N_SIM));
  for (var s = 0; s < N_SIM; s++){
    var c = 1, pic = 1, pire = 0, sr = 0, ps = 0;
    if (bandes) grille[0][s] = 1;
    for (var k = 1; k <= n; k++){
      if (rnd() < wr){ c *= 1 + rk * G; sr = 0; } else { c *= Math.max(0, 1 - rk * Lp); sr++; if (sr > ps) ps = sr; }
      if (c > pic) pic = c; var d = 1 - c / pic; if (d > pire) pire = d;
      if (bandes && k % pasB === 0) grille[k / pasB][s] = c;
    }
    fin[s] = c; dd[s] = pire; serie[s] = ps;
  }
  var q = function(a, p){ var t = Array.prototype.slice.call(a).sort(function(x, y){ return x - y; }); return t[Math.min(t.length - 1, Math.floor(p * t.length))]; };
  var part = function(a, f){ var z = 0; for (var i = 0; i < a.length; i++) if (f(a[i])) z++; return z / a.length; };
  var o = {med: q(fin, 0.5) - 1, p5: q(fin, 0.05) - 1, p95: q(fin, 0.95) - 1, perte: part(fin, function(x){ return x < 1; }),
           dd20: part(dd, function(x){ return x >= 0.2; }), dd50: part(dd, function(x){ return x >= 0.5; }), ddMed: q(dd, 0.5), serie: q(serie, 0.5), serie95: q(serie, 0.95)};
  if (bandes) o.bandes = grille.map(function(a){ return [q(a, 0.05), q(a, 0.25), q(a, 0.5), q(a, 0.75), q(a, 0.95)]; });
  return o;
}
function pcs(x){ return (x > 0 ? '+' : '') + nb(x * 100, Math.abs(x) < 0.1 ? 1 : 0) + ' %'; }
function eventail(o){
  var cv = $('#en-sim-cv'); if (!cv || !o || !o.bandes) return;
  var t = toile(cv); if (!t) return;
  var c = t.c, w = t.w, h = t.h, mg = 56, Bd = o.bandes, lo = Infinity, hi = -Infinity;
  Bd.forEach(function(b){ lo = Math.min(lo, b[0]); hi = Math.max(hi, b[4]); });
  lo = Math.min(lo, 0.98); hi = Math.max(hi, 1.02);
  var L = Math.log(lo), H = Math.log(hi), X = function(i){ return 6 + i / (Bd.length - 1) * (w - mg - 12); }, Y = function(v){ return 10 + (H - Math.log(Math.max(v, 1e-6))) / (H - L) * (h - 22); };
  c.font = '11px JetBrains Mono, monospace'; c.textBaseline = 'middle'; c.textAlign = 'left';
  [0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10, 20, 50].forEach(function(v){
    if (v < lo || v > hi) return;
    c.strokeStyle = v === 1 ? '#3a3a44' : LIGNE; c.setLineDash(v === 1 ? [4, 4] : []); c.beginPath(); c.moveTo(0, Y(v)); c.lineTo(w - mg, Y(v)); c.stroke(); c.setLineDash([]);
    c.fillStyle = GRIS; c.fillText(pcs(v - 1), w - mg + 6, Y(v));
  });
  function bande(a, b, col){ c.fillStyle = col; c.beginPath(); Bd.forEach(function(x, i){ c[i ? 'lineTo' : 'moveTo'](X(i), Y(x[b])); }); for (var i = Bd.length - 1; i >= 0; i--) c.lineTo(X(i), Y(Bd[i][a])); c.closePath(); c.fill(); }
  bande(0, 4, 'rgba(126,166,214,.13)'); bande(1, 3, 'rgba(126,166,214,.28)');
  c.strokeStyle = '#dfe9f5'; c.lineWidth = 2; c.beginPath(); Bd.forEach(function(x, i){ c[i ? 'lineTo' : 'moveTo'](X(i), Y(x[2])); }); c.stroke(); c.lineWidth = 1;
}
var dernierSim = null;
function calculer(){
  var E = {wr: $('#en-wr'), g: $('#en-g'), l: $('#en-l'), rk: $('#en-rk'), n: $('#en-n')}; if (!E.wr) return;
  var wr = num(E.wr) / 100, G = num(E.g), Lp = num(E.l), rk = num(E.rk) / 100, n = Math.round(num(E.n));
  var ok = {wr: wr > 0 && wr < 1, g: G > 0 && G <= 20, l: Lp > 0 && Lp <= 5, rk: rk > 0 && rk <= 0.25, n: n >= 10 && n <= 2000}, bon = true;
  Object.keys(E).forEach(function(k){ E[k].classList.toggle('err', !ok[k]); if (!ok[k]) bon = false; });
  if (!bon){ $('#en-sim-msg').textContent = 'Vérifie les cases en rouge : réussite entre 1 et 99 %, risque jusqu\'à 25 %, 10 à 2 000 trades.'; return; }
  $('#en-sim-msg').textContent = '';
  var esp = wr * G - (1 - wr) * Lp, o = simuler(wr, G, Lp, rk, n, true); dernierSim = o;
  var cl = function(x){ return x > 0 ? ' class="up"' : (x < 0 ? ' class="down"' : ''); };
  $('#en-sim-kpis').innerHTML =
    '<div><span class="k">Espérance</span><b' + cl(esp) + '>' + sg(esp, 2) + ' R</b><small>gain moyen par trade</small></div>' +
    '<div><span class="k">Résultat du milieu</span><b' + cl(o.med) + '>' + pcs(o.med) + '</b><small>après ' + n + ' trades</small></div>' +
    '<div><span class="k">Mauvais scénario</span><b' + cl(o.p5) + '>' + pcs(o.p5) + '</b><small>1 chance sur 20 de faire pire</small></div>' +
    '<div><span class="k">Finir en perte</span><b>' + pc(o.perte) + '</b><small>des cas</small></div>' +
    '<div><span class="k">Perdre 20 % en route</span><b' + (o.dd20 >= 0.25 ? ' class="down"' : '') + '>' + pc(o.dd20) + '</b><small>baisse du milieu : ' + pc(o.ddMed) + '</small></div>' +
    '<div><span class="k">Perdre 50 % en route</span><b' + (o.dd50 >= 0.05 ? ' class="down"' : '') + '>' + pc(o.dd50) + '</b><small>série de pertes : ' + o.serie + ' d\'habitude, ' + o.serie95 + ' possible</small></div>';
  eventail(o);
  var lignes = [0.005, 0.01, 0.02, 0.05].concat([0.005, 0.01, 0.02, 0.05].indexOf(rk) < 0 ? [rk] : []).sort(function(a, b){ return a - b; }).map(function(r){
    var x = r === rk ? o : simuler(wr, G, Lp, r, n, false);
    return '<tr' + (r === rk ? ' class="ici"' : '') + '><td>' + nb(r * 100, r * 100 % 1 ? 1 : 0) + ' % par trade</td><td' + cl(x.med) + '>' + pcs(x.med) + '</td><td' + cl(x.p5) + '>' + pcs(x.p5) +
      '</td><td' + (x.dd20 >= 0.25 ? ' class="down"' : (x.dd20 >= 0.05 ? ' class="att"' : '')) + '>' + pc(x.dd20) + '</td><td' + (x.dd50 >= 0.05 ? ' class="down"' : (x.dd50 >= 0.01 ? ' class="att"' : '')) + '>' + pc(x.dd50) + '</td></tr>';
  }).join('');
  $('#en-sim-tab').innerHTML = '<table><thead><tr><th>Risque</th><th>Résultat du milieu</th><th>Mauvais scénario</th><th>Perdre 20 %</th><th>Perdre 50 %</th></tr></thead><tbody>' + lignes + '</tbody></table>';
  var t;
  if (esp <= 0) t = '<b>Espérance négative : ' + sg(esp, 2) + ' R par trade.</b> Avec ces chiffres, aucun réglage du risque ne sauve le compte : il perd seulement plus ou moins vite. ' +
    'La seule chose à faire est d\'améliorer la sélection des trades ; il faudrait au moins ' + pc(Lp / (G + Lp)) + ' de réussite pour être à l\'équilibre.';
  else {
    t = '<b>Espérance positive : ' + sg(esp, 2) + ' R par trade.</b> ';
    if (o.dd50 >= 0.05) t += 'Mais à ' + nb(rk * 100, 1) + ' % par trade, le compte est divisé par deux en route dans ' + pc(o.dd50) + ' des cas : presque personne ne continue à trader après ça. Baisse le risque.';
    else if (o.dd20 >= 0.25) t += 'À ' + nb(rk * 100, 1) + ' % par trade, une baisse de 20 % arrive dans ' + pc(o.dd20) + ' des cas : il faut pouvoir la supporter sans changer de méthode.';
    else t += 'À ' + nb(rk * 100, 1) + ' % par trade, les baisses restent supportables : c\'est le genre de risque qui laisse le temps à l\'avantage de s\'exprimer.';
    t += ' Attends-toi à une série de ' + o.serie + ' pertes de suite, et jusqu\'à ' + o.serie95 + ' : c\'est normal avec ' + pc(wr) + ' de réussite, ce n\'est pas le signe que la méthode est cassée.';
  }
  $('#en-sim-lecture').innerHTML = t;
}
function remplir(wr, G, Lp, source){
  var set = function(id, v, d){ $(id).value = nb(v, d).replace(/\s/g, ''); };
  set('#en-wr', wr * 100, 0); set('#en-g', G, 2); set('#en-l', Lp, 2); calculer();
  $('#en-sim-msg').textContent = 'Chiffres repris ' + source + '.';
}
if ($('#en-form')){
  $('#en-form').addEventListener('input', calculer);
  $('#en-src-j').addEventListener('click', function(){
    var J = T.journal && T.journal.stats ? T.journal.stats() : null;
    if (!J || !J.n || J.n < 10 || J.gainMoy == null || J.perteMoy == null){ $('#en-sim-msg').textContent = 'Il faut au moins 10 trades clos dans le journal, avec des gains et des pertes.'; return; }
    remplir(J.reussite, J.gainMoy, -J.perteMoy, 'du journal (' + J.n + ' trades)');
  });
  $('#en-src-e').addEventListener('click', function(){
    var o = bilan();
    if (!o.n || o.n < 10 || o.gain == null || o.perte == null || !(o.perte > 0)){ $('#en-sim-msg').textContent = 'Il faut au moins 10 trades à l\'entraînement, avec des gains et des pertes.'; return; }
    remplir(o.reussite, o.gain, o.perte, 'de l\'entraînement (' + o.n + ' trades)');
  });
  calculer();
  if (window.ResizeObserver) new ResizeObserver(function(){ eventail(dernierSim); }).observe($('#en-sim-cv').parentNode);
  else window.addEventListener('resize', function(){ eventail(dernierSim); });
}
})();
"""
