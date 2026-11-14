# -*- coding: utf-8 -*-
"""
THÈME PROFESSIONNEL (v8) : une seule police, une grille, une navigation en haut
==============================================================================

Appliqué par-dessus les styles existants (il vient en dernier) : palette sobre, Inter pour le texte et JetBrains Mono
pour les chiffres, barre du haut sur une ligne, onglets fixés sous la barre avec « Vue d'ensemble » en premier,
panneaux et tableaux harmonisés. Le mode Trader / Analyst disparaît : tout est rangé dans les onglets.
"""

POLICES = ('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&'
           'family=JetBrains+Mono:wght@400;500;600;700&family=Rajdhani:wght@500;600;700&display=swap" rel="stylesheet">')

CSS = """
/* ===================== Thème professionnel v8 ===================== */
:root{
  --bg:#000000; --panel:#0b0b0d; --band:#141418; --band2:#1c1c21; --line:#2a2a31; --ink:#f1f1f3; --mute:#9b9ba3;
  --brass:#7ea6d6; --steel:#b4b4bc; --up:#2fd27d; --down:#ff5a5f; --amber:#d6be78;
  --titre:"Rajdhani","Inter",system-ui,sans-serif;
  --cond:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif; --text:"Inter",system-ui,-apple-system,"Segoe UI",sans-serif;
  --mono:"JetBrains Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
}
html,body{background:var(--bg)}
body{font:14px/1.5 var(--text);letter-spacing:-.003em;font-feature-settings:"cv11","ss01"}
/* Chiffres : police à chasse fixe pour les prix et les tableaux, Inter à chiffres alignés pour le reste */
.num,td.num,.dz-ticket td.num,#px-live,.horloges b,.prochaine b,.lb-tab td.num,.on-tab td.num,.fd-tab td.num,.dz-tab td.num{
  font-family:var(--mono);font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.v,.dz-mom b,.fd-ct b,.fd-kpis b,.on-kpis b,.on-pp b,.strip .v,.dz-maj,.fd-vars b{font-family:var(--text);font-variant-numeric:tabular-nums}
.k{font:600 10.5px var(--text);letter-spacing:.08em;text-transform:uppercase;color:var(--mute)}
a{color:inherit}
button{font:500 13px var(--text);border-radius:6px;padding:6px 12px;background:var(--band);border:1px solid var(--line);color:var(--ink);transition:border-color .15s,background .15s}
button:hover{border-color:#3a4554;background:var(--band2)}
button.or{background:var(--brass);border-color:var(--brass);color:#000000;font-weight:700}

/* Barre du haut : une seule ligne */
.barre{background:rgba(6,8,12,.94);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.barre .ligne{flex-wrap:nowrap;gap:16px;padding:8px 20px;min-height:56px;overflow-x:auto;scrollbar-width:none}
.barre .ligne::-webkit-scrollbar{display:none}
.barre .ligne>*{flex:none;white-space:nowrap}
.barre .ligne .k{white-space:nowrap}
.logo{font:800 15px var(--text);letter-spacing:.06em;display:flex;align-items:center;gap:2px}
.logo i{width:10px;height:18px;border-radius:1px;background:linear-gradient(180deg,#b7cfec,var(--brass))}
.logo small{font:500 11px var(--mono);color:var(--mute)}
.prix-live{display:flex;flex-direction:column;line-height:1.1;padding-left:16px;border-left:1px solid var(--line)}
.prix-live b{font:600 22px var(--mono);color:var(--ink)}
.prix-live small{font-size:11px;color:var(--mute)}
.horloges{gap:14px;padding-left:16px;border-left:1px solid var(--line)}
.horloges div:nth-child(2),.horloges div:nth-child(4){display:none}
.horloges b{font-size:14px}
.marche,.fraicheur{padding-left:16px;border-left:1px solid var(--line)}
@media (max-width:1560px){.fraicheur:not(.prochaine){display:none}}
@media (max-width:1380px){.horloges{display:none}}
@media (max-width:1180px){.marche{display:none} .actions button{padding:6px 8px}}
@media (max-width:980px){.prochaine{display:none}}
.marche b,.fraicheur b{font:500 13.5px var(--text)}
.prochaine b{font-family:var(--mono)}
.actions{gap:6px;flex-wrap:nowrap}
.actions button{padding:6px 10px;font-size:12.5px;white-space:nowrap}
#btn-mode,#btn-notif{display:none}
/* Écrans regroupés : sommaire et titres de rubrique */
.ec-som{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:0 0 18px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
.ec-som a{display:inline-flex;align-items:center;gap:7px;font:600 13px var(--text);color:var(--ink);text-decoration:none;border:1px solid var(--line);border-radius:6px;padding:5px 10px;background:var(--band)}
.ec-som a:hover{border-color:var(--brass)}
.ec-som .fc,.ec-titre .fc{font:700 10.5px var(--mono);color:var(--brass);background:rgba(126,166,214,.1);border:1px solid rgba(126,166,214,.35);border-radius:4px;padding:1px 5px;letter-spacing:.04em}
.ec-sec{scroll-margin-top:calc(var(--haut-barre,57px) + 60px)}
.ec-sec+.ec-sec{margin-top:34px;padding-top:26px;border-top:2px solid var(--line)}
.ec-titre{display:flex;align-items:center;gap:10px;margin:0 0 14px}
.ec-titre h2{font:800 22px var(--text);letter-spacing:-.01em;margin:0;color:var(--ink)}
.tape{border-bottom:1px solid var(--line);background:#0c1016}

/* Navigation : onglets fixés sous la barre */
.onglets{position:sticky;top:var(--haut-barre,57px);z-index:25;background:rgba(6,8,12,.96);backdrop-filter:blur(8px);
  -webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:0 20px;gap:4px;scrollbar-width:none}
.onglets::-webkit-scrollbar{display:none}
.onglets button{font:600 13px var(--text);color:var(--mute);padding:14px 10px 12px;border:0;border-bottom:2px solid transparent;border-radius:0;background:none;white-space:nowrap;flex:none}
.onglets button:hover{color:var(--ink);background:none}
.onglets button.actif,.onglets button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--brass)}
.onglets kbd{display:none}
.onglets .fc{font:700 10.5px var(--mono);color:var(--brass);background:rgba(126,166,214,.1);border:1px solid rgba(126,166,214,.35);
  border-radius:4px;padding:1px 5px;margin-right:8px;letter-spacing:.04em}
.onglets button.actif .fc,.onglets button[aria-selected=true] .fc{background:var(--brass);color:#000;border-color:var(--brass)}
.cmd{margin-left:auto;display:flex;align-items:center;gap:8px;padding:8px 0}
.cmd label{font:700 10.5px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--brass)}
.cmd input{width:190px;background:#000;border:1px solid var(--line);border-radius:6px;color:var(--brass);font:600 13px var(--mono);
  padding:7px 10px;text-transform:uppercase}
.cmd input:focus{outline:none;border-color:var(--brass);box-shadow:0 0 0 2px rgba(126,166,214,.25)}
.cmd input.err{border-color:var(--down)}
.cmd-aide{font-size:11.5px;color:var(--mute);white-space:nowrap}
/* Titres de panneaux en ambre, comme sur un terminal Bloomberg */
.ps-tete .k,.ph .k,.mk-tete,.dz-tete .k,.lb-tete small,.ps-titre{color:var(--brass);font-family:var(--titre);font-size:12px;letter-spacing:.16em}
/* Style terminal : panneaux à angles vifs, coins dorés, titres techniques */
.ps-carte,.panel,.dz-carte,.dz-panneau,.fd-comp,.fd-carte,.on-carte,.lb-carte,.pq-j,.pq-ia,.an-c,.an-hero,.st-kpis div,.jr-kpis div,.rb-strat,.dx-sante,.ec-som{border-radius:2px!important}
.ps-carte{position:relative}
.ps-carte::before,.ps-carte::after,.pq-j::before,.pq-j::after{content:"";position:absolute;width:9px;height:9px;border-color:var(--brass);border-style:solid;opacity:.65;pointer-events:none}
.ps-carte::before,.pq-j::before{top:-1px;left:-1px;border-width:1px 0 0 1px} .ps-carte::after,.pq-j::after{bottom:-1px;right:-1px;border-width:0 1px 1px 0}
.pq-j{position:relative}
.contenu h2,.ec-titre h2,.pq-h h2{font-family:var(--titre)!important;letter-spacing:.06em;text-transform:uppercase}
.logo{font-family:var(--titre)!important;letter-spacing:.18em}
.onglets button{font-family:var(--titre)!important;font-size:14px!important;letter-spacing:.08em;text-transform:uppercase}
.contenu{max-width:1600px;padding:0 20px 48px}
.tabpanel{padding-top:16px}
#tab-accueil .dz{padding:0 0 12px}
#tab-accueil .lb-bandeau{margin:0 0 12px}
#tab-accueil .strip{margin:0 0 12px}
#tab-accueil .cockpit{padding:0}

/* Sections des onglets */
.contenu section{margin-bottom:32px}
.contenu h2{font:700 19px/1.3 var(--text);letter-spacing:-.01em;margin:6px 0 6px;color:var(--ink)}
.contenu h3{font:600 15px var(--text)}
.pourquoi{font-size:13.5px;color:var(--mute);max-width:88ch;line-height:1.55}

/* Panneaux */
.panel,.dz-carte,.dz-panneau,.fd-comp,.fd-carte,.fd-kpis div,.on-kpis div,.on-carte,.on-baro,.lb-carte,.fd-hero,.on-hero{
  background:var(--panel);border:1px solid var(--line);border-radius:10px}
.ph{padding:11px 14px;border-bottom:1px solid var(--line)}
.ph .k{color:var(--ink);font-size:11px}
.pb{padding:12px 14px}

/* Décision */
.dz{gap:12px}
.dz-carte,.dz-panneau{padding:16px 18px}
.dz-verdict>b{font:800 60px/1 var(--text);letter-spacing:-.03em}
.dz-note{font:700 11.5px var(--text);border-radius:5px;padding:4px 10px}
.dz-verdict small{font:500 13.5px var(--text)}
.dz-pourquoi{font-size:15px}
.dz-filtres b{font:600 13.5px var(--text)}
.dz-ic{border-radius:6px;font:700 12px var(--text)}
.dz-ticket td{font-size:13.5px}
.dz-ticket td.num{font-size:14px;font-weight:600}
.dz-copier{border-radius:8px;font:700 14px var(--text)}
.dz-struct td{font:500 13.5px var(--text)}
.dz-svg .ax,.dz-svg .zl,.dz-svg .tt{font-family:var(--mono)}

/* Bandeau de chiffres : une grille de cartes au lieu d'une ligne serrée */
.strip{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;overflow:visible;border:0;padding:0}
.strip .it{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;white-space:normal}
.strip .v{font-size:16px;font-weight:600}
.strip .s{font:500 12px var(--text)}

/* Cockpit : graphique en direct, contrôle, annonce, niveaux, actualités */
.cockpit{gap:12px;grid-template-columns:minmax(0,1.7fr) minmax(0,1fr) minmax(0,1fr)}
.minis{display:none}
#p-graph .pb{min-height:560px}

/* Tableaux : un tableau plus large que l'écran défile dans son cadre, jamais la page entière */
.tbl,.dt-defil{overflow-x:auto;max-width:100%;-webkit-overflow-scrolling:touch}
.contenu section,.contenu details{max-width:100%;overflow-x:auto}
.contenu .ps,.contenu .dz,.contenu .ps-tv{overflow:visible}
/* Tableaux */
table th{font-family:var(--text)}
.m td,.m th{font-size:13.5px}

/* Lisibilité générale */
.up{color:var(--up)} .down{color:var(--down)} .warn{color:var(--amber)} .brass{color:var(--brass)}
.vide{color:var(--mute)}
::selection{background:rgba(214,169,74,.35)}

@media (max-width:1100px){.cockpit{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
@media (max-width:1700px){.cmd-aide{display:none}}
@media (max-width:1600px){.cmd label{display:none}}
@media (max-width:1380px){.onglets .fc{display:none}.cmd label{display:none}}
@media (max-width:1100px){.cmd input{width:130px}}
@media (max-width:760px){
  .cmd{display:none}
  .barre .ligne{gap:10px 14px;padding:8px 12px;flex-wrap:wrap}
  .horloges,.fraicheur,.marche{display:none}
  .prix-live{padding-left:0;border-left:0}
  .actions{width:100%;justify-content:flex-start}
  .onglets{padding:0 8px}
  .onglets button{padding:12px 10px 10px;font-size:13px}
  .contenu{padding:0 10px 40px}
  .cockpit{grid-template-columns:minmax(0,1fr)}
  .dz-verdict>b{font-size:46px}
  #p-graph .pb{min-height:380px}
}
"""

JS = r"""
(function(){
/* v8 : plus de mode Trader / Analyst, tout est dans les onglets */
try { document.body.classList.remove('mode-trader'); localStorage.setItem('mode-or', 'analyst'); } catch(e){}
/* Première visite en v8 : ouvrir la Vue d'ensemble */
try {
  if (!localStorage.getItem('or-v8')){
    var b = document.querySelector('.onglets [data-tab="accueil"]'); if (b) b.click();
    localStorage.setItem('or-v8', '1');
  }
} catch(e){}
/* Barre de commande : un code de fonction puis Entrée ; « / » pour y aller depuis n'importe où */
var CODES = {GO:'accueil', ACCUEIL:'accueil', MKT:'marches', MARCHES:'marches', DATA:'data', DONNEES:'data', TECH:'technique', FA:'fondamental', FONDA:'fondamental',
  ECO:'annonces', CAL:'annonces', NEWS:'actus', N:'actus', STAT:'stat', STATS:'stat', WHY:'pourquoi', POURQUOI:'pourquoi', RISK:'salle', CHART:'direct', HEAT:'analyse', LAB:'labo', JRN:'suivi', JOURNAL:'suivi'};
var cmd = document.getElementById('cmd');
function aller(code){
  var T = window.terminalOr; if (T && T.commande && T.commande(code)) return true;   /* AL 4100, CALC, AIDE */
  var id = CODES[(code || '').trim().toUpperCase()];
  if (!id) return false;
  var b = document.querySelector('.onglets [data-tab="' + id + '"]');
  if (b){ b.click(); window.scrollTo({top: 0}); return true; }
  if (T && T.onglet && document.getElementById('sec-' + id)){ T.onglet(id); return true; }   /* rubrique d'un écran : MKT, DATA, STAT, NEWS, LAB */
  return false;
}
if (cmd){
  cmd.addEventListener('keydown', function(e){
    if (e.key === 'Enter'){ e.preventDefault(); var ok = aller(cmd.value); cmd.classList.toggle('err', !ok); if (ok){ cmd.value = ''; cmd.blur(); } }
    if (e.key === 'Escape'){ cmd.value = ''; cmd.blur(); }
  });
  document.addEventListener('keydown', function(e){
    var tg = (e.target.tagName || '');
    if (e.key === '/' && tg !== 'INPUT' && tg !== 'TEXTAREA'){ e.preventDefault(); cmd.focus(); cmd.select(); }
  });
}
/* Prix en direct recopié dans la carte « Prix et séance » */
setInterval(function(){ var a = document.getElementById('px-live'), b = document.getElementById('ps-px-live');
  if (a && b && a.textContent && a.textContent !== b.textContent) b.textContent = a.textContent; }, 2000);
/* Hauteur réelle de la barre, pour coller les onglets dessous */
function hb(){ var b = document.querySelector('.barre'); if (b) document.documentElement.style.setProperty('--haut-barre', b.offsetHeight + 'px'); }
hb(); window.addEventListener('resize', hb);
})();
"""
