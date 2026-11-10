# -*- coding: utf-8 -*-
"""
FONCTION JRN : le journal des vrais trades
=========================================

Le terminal sait ce que le plan aurait fait ; il ne sait pas ce que tu as fait. Ici tu notes tes trades réels
(entrée, stop, sortie, taille, conforme au plan ou non). Le journal calcule le résultat en R et en dollars,
les statistiques (réussite, gain moyen, profit factor, pire baisse), compare tes trades conformes au plan à tes
trades hors plan, et alimente le verrou de risque de la Vue d'ensemble : deux trades pris ou −2 R dans la journée,
et le terminal affiche « limite du jour atteinte ».

Tout est enregistré dans le navigateur (rien n'est envoyé) : pense à exporter une sauvegarde de temps en temps.
Le bloc est en dehors des zones que le robot rafraîchit : une saisie en cours n'est jamais effacée.
"""


def bloc_journal():
    return (
        '<section class="jr" id="jr"><h2>Journal de trading</h2>'
        '<p class="pourquoi">Tes trades réels. Note-les juste après l\'entrée, puis complète la sortie : c\'est ce journal, pas '
        'le rejeu du plan, qui dit si tu gagnes. Enregistré dans ce navigateur seulement : exporte une sauvegarde régulièrement.</p>'
        '<div class="jr-kpis" id="jr-kpis"></div>'
        '<div class="jr-deux"><div class="jr-courbe" id="jr-courbe"></div><div class="jr-lecture" id="jr-lecture"></div></div>'
        '<form class="jr-form" id="jr-form" autocomplete="off">'
        '<label>Date et heure d\'entrée<input type="datetime-local" id="jr-t" required></label>'
        '<label>Sens<select id="jr-sens"><option value="1">Achat</option><option value="-1">Vente</option></select></label>'
        '<label>Entrée<input id="jr-entree" inputmode="decimal" required placeholder="4 061,20"></label>'
        '<label>Stop<input id="jr-stop" inputmode="decimal" required placeholder="4 052,00"></label>'
        '<label>Sortie<input id="jr-sortie" inputmode="decimal" placeholder="vide si en cours"></label>'
        '<label>Taille (lots)<input id="jr-lots" inputmode="decimal" required placeholder="0,10"></label>'
        '<label>Type<select id="jr-setup"><option>Plan</option><option>Hors plan</option></select></label>'
        '<label class="jr-large">Note<input id="jr-note" maxlength="140" placeholder="pourquoi ce trade, ce que tu as ressenti"></label>'
        '<div class="jr-actions"><button type="submit" class="or">Ajouter le trade</button>'
        '<span class="jr-msg" id="jr-msg" role="status"></span></div></form>'
        '<div class="tbl"><table class="jr-tab" id="jr-tab"><thead><tr><th>Entrée</th><th>Sens</th><th>Type</th><th>Prix</th><th>Stop</th>'
        '<th>Sortie</th><th>Lots</th><th>Résultat</th><th>En $</th><th>Note</th><th></th></tr></thead><tbody></tbody></table></div>'
        '<div class="jr-outils"><button type="button" id="jr-csv">Exporter en CSV</button>'
        '<button type="button" id="jr-json">Sauvegarde complète</button>'
        '<label class="jr-import">Restaurer une sauvegarde<input type="file" id="jr-import" accept="application/json,.json"></label></div>'
        '</section>')


CSS = """
/* Fonction JRN : journal des vrais trades */
.jr-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:12px}
.jr-kpis div{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;display:flex;flex-direction:column;gap:2px;min-width:0}
.jr-kpis b{font:700 20px var(--mono);letter-spacing:-.02em} .jr-kpis small{font-size:12px;color:var(--mute)}
.jr-deux{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:12px;margin-bottom:14px}
.jr-courbe,.jr-lecture{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;min-width:0}
.jr-courbe svg{display:block;width:100%;height:auto}
.jr-courbe .gr{stroke:#24242b} .jr-courbe .ax{fill:var(--mute);font:500 11px var(--mono)}
.jr-lecture ul{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column;gap:8px;font-size:13.5px;line-height:1.45}
.jr-lecture li b{font-family:var(--mono)}
.jr-form{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:10px;background:var(--panel);border:1px solid var(--line);border-radius:10px;
  padding:12px 14px;margin-bottom:14px;align-items:end}
.jr-form label{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--mute);min-width:0;grid-column:span 1}
.jr-form label:first-child{grid-column:span 2}
.jr-form label.jr-large{grid-column:span 5}
.jr-form input,.jr-form select{background:#000;border:1px solid var(--line);border-radius:6px;color:var(--ink);font:500 14px var(--mono);
  padding:8px 9px;min-width:0;width:100%;color-scheme:dark}
.jr-form input:focus,.jr-form select:focus{outline:none;border-color:var(--brass);box-shadow:0 0 0 2px rgba(255,159,26,.25)}
.jr-form input.err{border-color:var(--down)}
.jr-actions{grid-column:span 3;display:flex;align-items:center;gap:10px}
.jr-msg{font-size:12.5px;color:var(--mute)} .jr-msg.err{color:var(--down)}
.jr-tab{width:100%;border-collapse:collapse;font-size:13.5px}
.jr-tab th{text-align:left;font:600 10.5px var(--text);letter-spacing:.07em;text-transform:uppercase;color:var(--mute);padding:8px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
.jr-tab td{padding:8px 10px;border-bottom:1px solid var(--band2);white-space:nowrap}
.jr-tab td.num{font-family:var(--mono);text-align:right} .jr-tab td.jr-n{white-space:normal;min-width:160px;color:var(--mute)}
.jr-tab .jr-hp{color:var(--amber)}
.jr-tab button{font-size:12px;padding:4px 8px}
.jr-tab input{width:92px;background:#000;border:1px solid var(--line);border-radius:6px;color:var(--ink);font:500 13px var(--mono);padding:5px 7px}
.jr-vide{color:var(--mute);padding:14px 10px}
.jr-outils{display:flex;flex-wrap:wrap;gap:8px;margin-top:10px;align-items:center}
.jr-import{font-size:13px;color:var(--mute);border:1px solid var(--line);border-radius:6px;padding:6px 12px;cursor:pointer;background:var(--band)}
.jr-import input{display:none}
@media (max-width:1100px){.jr-form{grid-template-columns:repeat(4,minmax(0,1fr))}.jr-form label.jr-large{grid-column:span 4}.jr-actions{grid-column:span 4}
  .jr-deux{grid-template-columns:minmax(0,1fr)}}
@media (max-width:640px){.jr-form{grid-template-columns:repeat(2,minmax(0,1fr))}.jr-form label.jr-large,.jr-actions,.jr-form label:first-child{grid-column:span 2}}
"""

JS = r"""
(function(){
var T = window.terminalOr || (window.terminalOr = {});
var CLE = 'trades-or';
function $(s){ return document.querySelector(s); }
function nb(x, d){ return (x == null || isNaN(x)) ? '—' : Number(x).toLocaleString('fr-FR', {minimumFractionDigits: d, maximumFractionDigits: d}); }
function sg(x, d){ return (x > 0 ? '+' : (x < 0 ? '−' : '')) + nb(Math.abs(x), d); }
function esc(t){ return String(t == null ? '' : t).replace(/[&<>"]/g, function(c){ return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; }); }
function nombre(txt){ var v = parseFloat(String(txt == null ? '' : txt).replace(/[\s  ]/g, '').replace(',', '.')); return isNaN(v) ? null : v; }
function charger(){ try { var v = JSON.parse(localStorage.getItem(CLE) || '[]'); return Array.isArray(v) ? v : []; } catch(e){ return []; } }
function sauver(){ try { localStorage.setItem(CLE, JSON.stringify(TR)); } catch(e){} }
var TR = charger();
function opl(){ var P = T.regles ? T.regles() : {}; return P.once_par_lot || 100; }
function resR(t){ var r = Math.abs(t.entree - t.stop); return (t.sortie == null || !(r > 0)) ? null : (t.sortie - t.entree) * t.sens / r; }
function resD(t){ return t.sortie == null ? null : (t.sortie - t.entree) * t.sens * t.lots * opl(); }
function cleJour(ts){ var d = new Date(ts); return d.getFullYear() + '-' + ('0' + (d.getMonth() + 1)).slice(-2) + '-' + ('0' + d.getDate()).slice(-2); }
function dateFr(ts){ var d = new Date(ts); return ('0' + d.getDate()).slice(-2) + '/' + ('0' + (d.getMonth() + 1)).slice(-2) + ' ' + ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2); }

/* Bilan du jour, pour le verrou de risque et le calculateur */
function jour(){
  var auj = cleJour(Date.now()), n = 0, r = 0, ouverts = 0;
  TR.forEach(function(t){ if (cleJour(t.t) !== auj) return; n++; var x = resR(t); if (x == null) ouverts++; else r += x; });
  return {n: n, r: r, ouverts: ouverts};
}
function verrou(){
  var J = jour(), R = (T.direct && T.direct().regles) || {}, max = R.max_trades || 2, perte = R.perte_max_r != null ? R.perte_max_r : -2;
  var k = document.getElementById('jr-auj-k'), a = document.getElementById('jr-auj-n'), b = document.getElementById('jr-auj-r'), v = document.getElementById('jr-verrou');
  if (!a || !b) return;
  if (!J.n){ if (k) k.textContent = 'Aujourd’hui (plan)'; a.textContent = a.getAttribute('data-plan'); b.textContent = b.getAttribute('data-plan'); if (v) v.hidden = true; return; }
  if (k) k.textContent = 'Aujourd’hui (ton journal)';
  a.textContent = J.n + ' / ' + max + ' trades' + (J.ouverts ? ' · ' + J.ouverts + ' en cours' : '');
  b.textContent = sg(J.r, 1) + ' R'; b.className = J.r > 0 ? 'up' : (J.r < 0 ? 'down' : 'flat');
  var stop = J.n >= max || J.r <= perte;
  if (v){
    v.hidden = !stop;
    if (stop) v.innerHTML = 'STOP : limite du jour atteinte<small>' + (J.r <= perte ? sg(J.r, 1) + ' R aujourd’hui, arrêt à ' + sg(perte, 0) + ' R.' : J.n + ' trades pris sur ' + max + '.') +
      ' Ta règle : plus de nouveau trade aujourd’hui, reprise demain.</small>';
  }
}

/* Statistiques */
function stats(){
  var clos = TR.filter(function(t){ return resR(t) != null; }).sort(function(a, b){ return a.t - b.t; });
  var R = clos.map(resR), n = R.length, S = {n: n, clos: clos, R: R, ouverts: TR.length - n};
  if (!n) return S;
  var gains = R.filter(function(x){ return x > 0; }), pertes = R.filter(function(x){ return x < 0; });
  var som = function(a){ return a.reduce(function(x, y){ return x + y; }, 0); };
  S.total = som(R); S.moy = S.total / n; S.reussite = gains.length / n;
  S.pf = pertes.length ? som(gains) / -som(pertes) : null;
  S.gainMoy = gains.length ? som(gains) / gains.length : null; S.perteMoy = pertes.length ? som(pertes) / pertes.length : null;
  var cum = 0, pic = 0, dd = 0, serie = 0, pire = 0; S.courbe = [];
  R.forEach(function(x){ cum += x; pic = Math.max(pic, cum); dd = Math.min(dd, cum - pic); serie = x < 0 ? serie + 1 : 0; pire = Math.max(pire, serie); S.courbe.push(cum); });
  S.dd = dd; S.pire = pire; S.dollars = som(clos.map(resD));
  function groupe(f){ var g = {}; clos.forEach(function(t, i){ var k = f(t); (g[k] = g[k] || []).push(R[i]); }); return g; }
  S.setup = groupe(function(t){ return t.setup || 'Plan'; });
  S.jour = groupe(function(t){ return ['dimanche','lundi','mardi','mercredi','jeudi','vendredi','samedi'][new Date(t.t).getDay()]; });
  S.seance = groupe(function(t){ var h = new Date(t.t).getHours(); return h < 9 ? 'Asie (0 h – 9 h)' : (h < 14 ? 'Londres (9 h – 14 h)' : 'New York (14 h – 23 h)'); });
  S.sens = groupe(function(t){ return t.sens > 0 ? 'Achats' : 'Ventes'; });
  return S;
}
function courbe(c){
  var w = 640, h = 170, g = 40, d = 8, ht = 10, b = 22;
  var mn = Math.min.apply(null, c.concat([0])), mx = Math.max.apply(null, c.concat([0])), et = (mx - mn) || 1;
  var x = function(i){ return g + (c.length === 1 ? 0.5 : i / (c.length - 1)) * (w - g - d); }, y = function(v){ return ht + (mx - v) / et * (h - ht - b); };
  var pts = c.map(function(v, i){ return x(i).toFixed(1) + ',' + y(v).toFixed(1); }).join(' ');
  var der = c[c.length - 1], coul = der >= 0 ? 'var(--up)' : 'var(--down)';
  return '<svg viewBox="0 0 ' + w + ' ' + h + '" role="img" aria-label="Courbe de tes résultats en R">' +
    '<line class="gr" x1="' + g + '" x2="' + (w - d) + '" y1="' + y(0).toFixed(1) + '" y2="' + y(0).toFixed(1) + '"/>' +
    '<text class="ax" x="' + (g - 6) + '" y="' + (y(mx) + 4).toFixed(1) + '" text-anchor="end">' + sg(mx, 1) + '</text>' +
    '<text class="ax" x="' + (g - 6) + '" y="' + (y(mn) + 4).toFixed(1) + '" text-anchor="end">' + sg(mn, 1) + '</text>' +
    '<polyline points="' + pts + '" fill="none" stroke="' + coul + '" stroke-width="2" stroke-linejoin="round"/>' +
    '<circle cx="' + x(c.length - 1).toFixed(1) + '" cy="' + y(der).toFixed(1) + '" r="3.5" fill="' + coul + '"/>' +
    '<text class="ax" x="' + g + '" y="' + (h - 6) + '">trade 1</text><text class="ax" x="' + (w - d) + '" y="' + (h - 6) + '" text-anchor="end">trade ' + c.length + ' · ' + sg(der, 1) + ' R</text></svg>';
}
function lecture(S){
  var L = [], som = function(a){ return a.reduce(function(x, y){ return x + y; }, 0); };
  function res(a){ return '<b>' + sg(som(a), 1) + ' R</b> sur ' + a.length + ' trade' + (a.length > 1 ? 's' : '') + ' (' + sg(som(a) / a.length, 2) + ' R par trade)'; }
  var p = S.setup['Plan'], hp = S.setup['Hors plan'];
  if (p && hp){
    L.push('Conformes au plan : ' + res(p) + '. Hors plan : ' + res(hp) + '.');
    if (som(hp) / hp.length < som(p) / p.length) L.push('Tes trades hors plan font moins bien que ceux du plan : ce sont eux qui coûtent.');
    else L.push('Tes trades hors plan font mieux que ceux du plan sur cet échantillon : note pourquoi, c’est peut-être une règle à tester au backtest.');
  } else if (hp) L.push('Tous tes trades sont notés hors plan : ' + res(hp) + '.');
  else if (p) L.push('Tous tes trades sont conformes au plan : ' + res(p) + '.');
  var se = Object.keys(S.seance).map(function(k){ return [k, S.seance[k]]; }).filter(function(x){ return x[1].length >= 3; });
  if (se.length > 1){ se.sort(function(a, b){ return som(b[1]) / b[1].length - som(a[1]) / a[1].length; });
    L.push('Meilleure séance : ' + se[0][0] + ', ' + res(se[0][1]) + '. La moins bonne : ' + se[se.length - 1][0] + ', ' + res(se[se.length - 1][1]) + '.'); }
  var sn = Object.keys(S.sens); if (sn.length > 1) L.push(sn.map(function(k){ return k + ' : ' + res(S.sens[k]); }).join(' · ') + '.');
  if (S.gainMoy != null && S.perteMoy != null) L.push('Gain moyen <b>' + sg(S.gainMoy, 2) + ' R</b>, perte moyenne <b>' + sg(S.perteMoy, 2) + ' R</b>' +
    (S.perteMoy < -1.05 ? ' : tes pertes dépassent 1 R, le stop n’est pas toujours respecté.' : '.'));
  if (S.n < 30) L.push('Moins de 30 trades clos : ces chiffres bougent encore beaucoup, ne tire pas de conclusion définitive.');
  return L;
}
function rendre(){
  var kp = $('#jr-kpis'); if (!kp) return;
  var S = stats(), cl = function(v){ return v > 0 ? 'up' : (v < 0 ? 'down' : ''); };
  if (!S.n){
    kp.innerHTML = '<div><span class="k">Trades clos</span><b>0</b><small>' + (S.ouverts ? S.ouverts + ' en cours' : 'ajoute ton premier trade ci-dessous') + '</small></div>';
    $('#jr-courbe').innerHTML = '<span class="k">Courbe de résultats</span><p class="jr-vide">Elle apparaît dès le premier trade clos.</p>';
    $('#jr-lecture').innerHTML = '<span class="k">Lecture</span><p class="jr-vide">Le journal compare tes trades conformes au plan à tes trades hors plan, par séance et par sens.</p>';
  } else {
    kp.innerHTML = '<div><span class="k">Trades clos</span><b>' + S.n + '</b><small>' + (S.ouverts ? S.ouverts + ' en cours' : 'aucun en cours') + '</small></div>' +
      '<div><span class="k">Réussite</span><b>' + nb(S.reussite * 100, 0) + ' %</b></div>' +
      '<div><span class="k">Gain moyen</span><b class="' + cl(S.moy) + '">' + sg(S.moy, 2) + ' R</b><small>par trade</small></div>' +
      '<div><span class="k">Total</span><b class="' + cl(S.total) + '">' + sg(S.total, 1) + ' R</b><small>' + sg(S.dollars, 0) + ' $</small></div>' +
      '<div><span class="k">Profit factor</span><b>' + (S.pf == null ? '∞' : nb(S.pf, 2)) + '</b><small>gains ÷ pertes</small></div>' +
      '<div><span class="k">Pire baisse</span><b>' + sg(S.dd, 1) + ' R</b><small>pire série : ' + S.pire + ' perte' + (S.pire > 1 ? 's' : '') + '</small></div>';
    $('#jr-courbe').innerHTML = '<span class="k">Courbe de résultats (en R)</span>' + courbe(S.courbe);
    $('#jr-lecture').innerHTML = '<span class="k">Lecture</span><ul>' + lecture(S).map(function(x){ return '<li>' + x + '</li>'; }).join('') + '</ul>';
  }
  var tb = $('#jr-tab tbody');
  var lignes = TR.slice().sort(function(a, b){ return b.t - a.t; }).map(function(t){
    var r = resR(t), d = resD(t);
    return '<tr data-id="' + esc(t.id) + '"><td>' + dateFr(t.t) + '</td><td class="' + (t.sens > 0 ? 'up' : 'down') + '">' + (t.sens > 0 ? 'Achat' : 'Vente') + '</td>' +
      '<td class="' + (t.setup === 'Hors plan' ? 'jr-hp' : '') + '">' + esc(t.setup || 'Plan') + '</td><td class="num">' + nb(t.entree, 2) + '</td><td class="num">' + nb(t.stop, 2) + '</td>' +
      '<td class="num">' + (t.sortie == null ? '<input inputmode="decimal" placeholder="prix" aria-label="Prix de sortie">' : nb(t.sortie, 2)) + '</td><td class="num">' + nb(t.lots, 2) + '</td>' +
      '<td class="num ' + cl(r) + '">' + (r == null ? 'en cours' : sg(r, 2) + ' R') + '</td><td class="num ' + cl(d) + '">' + (d == null ? '—' : sg(d, 0) + ' $') + '</td>' +
      '<td class="jr-n">' + esc(t.note || '') + '</td><td>' + (t.sortie == null ? '<button type="button" data-a="clore">Clôturer</button> ' : '') +
      '<button type="button" data-a="suppr" aria-label="Supprimer ce trade">Suppr.</button></td></tr>';
  }).join('');
  tb.innerHTML = lignes || '<tr><td colspan="11" class="jr-vide">Aucun trade enregistré.</td></tr>';
  verrou();
}
function msg(t, err){ var m = $('#jr-msg'); if (m){ m.textContent = t; m.className = 'jr-msg' + (err ? ' err' : ''); } }
function maintenantLocal(){ var d = new Date(Date.now() - new Date().getTimezoneOffset() * 60000); return d.toISOString().slice(0, 16); }

document.addEventListener('submit', function(ev){
  if (!ev.target || ev.target.id !== 'jr-form') return;
  ev.preventDefault();
  var t = new Date($('#jr-t').value).getTime(), sens = +$('#jr-sens').value, e = nombre($('#jr-entree').value), st = nombre($('#jr-stop').value),
      so = $('#jr-sortie').value.trim() === '' ? null : nombre($('#jr-sortie').value), lots = nombre($('#jr-lots').value);
  var err = !t ? 'Date invalide.' : (e == null || st == null || lots == null || !(lots > 0)) ? 'Entrée, stop et taille sont obligatoires.'
          : !((e - st) * sens > 0) ? 'Le stop est du mauvais côté de l’entrée.' : ($('#jr-sortie').value.trim() !== '' && so == null) ? 'Prix de sortie invalide.' : '';
  if (err){ msg(err, true); return; }
  TR.push({id: Date.now() + '-' + Math.round(Math.random() * 1e6), t: t, sens: sens, entree: e, stop: st, sortie: so, lots: lots,
           setup: $('#jr-setup').value, note: $('#jr-note').value.trim()});
  sauver(); rendre(); msg('Trade ajouté.');
  ['jr-entree', 'jr-stop', 'jr-sortie', 'jr-note'].forEach(function(i){ document.getElementById(i).value = ''; });
});
document.addEventListener('click', function(ev){
  var b = ev.target.closest && ev.target.closest('#jr-tab button[data-a]'); if (!b) return;
  var tr = b.closest('tr'), id = tr.getAttribute('data-id'), t = TR.filter(function(x){ return String(x.id) === id; })[0]; if (!t) return;
  if (b.getAttribute('data-a') === 'suppr'){
    if (b.getAttribute('data-sur') !== '1'){ b.setAttribute('data-sur', '1'); b.textContent = 'Confirmer'; setTimeout(function(){ b.removeAttribute('data-sur'); b.textContent = 'Suppr.'; }, 4000); return; }
    TR = TR.filter(function(x){ return x !== t; }); sauver(); rendre(); return;
  }
  var i = tr.querySelector('input'), v = i ? nombre(i.value) : null;
  if (v == null){ if (i){ i.classList.add('err'); i.focus(); } return; }
  t.sortie = v; sauver(); rendre();
});
function telecharger(nom, contenu, type){
  var a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([contenu], {type: type})); a.download = nom;
  document.body.appendChild(a); a.click(); setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 500);
}
document.addEventListener('click', function(ev){
  if (!ev.target) return;
  var jourF = cleJour(Date.now());
  if (ev.target.id === 'jr-json') telecharger('journal-or-' + jourF + '.json', JSON.stringify({version: 1, trades: TR}, null, 1), 'application/json');
  if (ev.target.id === 'jr-csv'){
    var l = ['date;sens;type;entree;stop;sortie;lots;resultat_R;resultat_dollars;note'];
    TR.slice().sort(function(a, b){ return a.t - b.t; }).forEach(function(t){
      var r = resR(t), d = resD(t), f = function(x, k){ return x == null ? '' : String(x.toFixed(k)).replace('.', ','); };
      l.push([new Date(t.t).toLocaleString('fr-FR'), t.sens > 0 ? 'achat' : 'vente', t.setup || 'Plan', f(t.entree, 2), f(t.stop, 2), f(t.sortie, 2), f(t.lots, 2),
              f(r, 2), f(d, 0), '"' + String(t.note || '').replace(/"/g, '""') + '"'].join(';'));
    });
    telecharger('journal-or-' + jourF + '.csv', '﻿' + l.join('\r\n'), 'text/csv;charset=utf-8');
  }
});
document.addEventListener('change', function(ev){
  if (!ev.target || ev.target.id !== 'jr-import' || !ev.target.files || !ev.target.files[0]) return;
  var f = new FileReader();
  f.onload = function(){
    try {
      var j = JSON.parse(f.result), l = Array.isArray(j) ? j : j.trades, ids = {}, aj = 0;
      if (!Array.isArray(l)) throw 0;
      TR.forEach(function(t){ ids[t.id] = 1; });
      l.forEach(function(t){ if (t && t.id && !ids[t.id] && t.t && t.entree != null && t.stop != null){ TR.push(t); aj++; } });
      sauver(); rendre(); msg(aj + ' trade' + (aj > 1 ? 's' : '') + ' restauré' + (aj > 1 ? 's' : '') + '.');
    } catch(e){ msg('Fichier illisible : ce n’est pas une sauvegarde du journal.', true); }
    ev.target.value = '';
  };
  f.readAsText(ev.target.files[0]);
});

/* Accès pour le calculateur et le verrou */
T.journal = {
  jour: jour,
  preparer: function(o){
    if (T.onglet) T.onglet('suivi');
    var set = function(id, v, d){ var el = document.getElementById(id); if (el && v != null) el.value = String(Number(v).toFixed(d)).replace('.', ','); };
    $('#jr-t').value = maintenantLocal(); $('#jr-sens').value = String(o.sens);
    set('jr-entree', o.entree, 2); set('jr-stop', o.stop, 2); set('jr-lots', o.lots, 2);
    $('#jr-setup').value = o.verdict === 'HORS PLAN' ? 'Hors plan' : 'Plan';
    var f = $('#jr-form'); if (f) f.scrollIntoView({block: 'center'});
    msg('Vérifie les valeurs puis clique sur « Ajouter le trade ».');
  }
};
var ti = $('#jr-t'); if (ti && !ti.value) ti.value = maintenantLocal();
rendre(); setInterval(verrou, 2000);
window.addEventListener('storage', function(e){ if (e.key === CLE){ TR = charger(); rendre(); } });
})();
"""
