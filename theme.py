# -*- coding: utf-8 -*-
"""
THÈME PROFESSIONNEL (v8) : une seule police, une grille, une navigation en haut
==============================================================================

Appliqué par-dessus les styles existants (il vient en dernier) : palette sobre, Inter pour le texte et JetBrains Mono
pour les chiffres, barre du haut sur une ligne, onglets fixés sous la barre avec « Vue d'ensemble » en premier,
panneaux et tableaux harmonisés. Le mode Trader / Analyst disparaît : tout est rangé dans les onglets.
"""

POLICES = ('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&'
           'family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">')

CSS = """
/* ===================== Thème professionnel v8 ===================== */
:root{
  --bg:#0a0d12; --panel:#10141b; --band:#151a22; --band2:#1b212b; --line:#232b36; --ink:#e7eaf0; --mute:#8d97a8;
  --brass:#d6a94a; --steel:#a3adbb; --up:#34c38f; --down:#f0616d; --amber:#f5a623;
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
button.or{background:var(--brass);border-color:var(--brass);color:#14100a;font-weight:600}

/* Barre du haut : une seule ligne */
.barre{background:rgba(10,13,18,.92);backdrop-filter:blur(8px);-webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--line)}
.barre .ligne{flex-wrap:nowrap;gap:16px;padding:8px 20px;min-height:56px;overflow-x:auto;scrollbar-width:none}
.barre .ligne::-webkit-scrollbar{display:none}
.barre .ligne>*{flex:none;white-space:nowrap}
.barre .ligne .k{white-space:nowrap}
.logo{font:800 15px var(--text);letter-spacing:.06em;display:flex;align-items:center;gap:2px}
.logo i{width:10px;height:18px;border-radius:2px;background:linear-gradient(180deg,#f0c766,var(--brass))}
.logo small{font:500 11px var(--mono);color:var(--mute)}
.prix-live{display:flex;flex-direction:column;line-height:1.1;padding-left:16px;border-left:1px solid var(--line)}
.prix-live b{font:600 22px var(--mono);color:var(--ink)}
.prix-live small{font-size:11px;color:var(--mute)}
.horloges{gap:14px;padding-left:16px;border-left:1px solid var(--line)}
.horloges div:nth-child(2),.horloges div:nth-child(4){display:none}
.horloges b{font-size:14px}
.marche,.fraicheur{padding-left:16px;border-left:1px solid var(--line)}
@media (max-width:1560px){.fraicheur:not(.prochaine){display:none}}
.marche b,.fraicheur b{font:500 13.5px var(--text)}
.prochaine b{font-family:var(--mono)}
.actions{gap:6px;flex-wrap:nowrap}
.actions button{padding:6px 10px;font-size:12.5px;white-space:nowrap}
#btn-mode,#btn-notif{display:none}
.tape{border-bottom:1px solid var(--line);background:#0c1016}

/* Navigation : onglets fixés sous la barre */
.onglets{position:sticky;top:var(--haut-barre,57px);z-index:25;background:rgba(10,13,18,.95);backdrop-filter:blur(8px);
  -webkit-backdrop-filter:blur(8px);border-bottom:1px solid var(--line);padding:0 20px;gap:4px;scrollbar-width:none}
.onglets::-webkit-scrollbar{display:none}
.onglets button{font:600 13.5px var(--text);color:var(--mute);padding:14px 14px 12px;border:0;border-bottom:2px solid transparent;border-radius:0;background:none}
.onglets button:hover{color:var(--ink);background:none}
.onglets button.actif,.onglets button[aria-selected=true]{color:var(--ink);border-bottom-color:var(--brass)}
.onglets kbd{display:none}
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

/* Tableaux */
table th{font-family:var(--text)}
.m td,.m th{font-size:13.5px}

/* Lisibilité générale */
.up{color:var(--up)} .down{color:var(--down)} .warn{color:var(--amber)} .brass{color:var(--brass)}
.vide{color:var(--mute)}
::selection{background:rgba(214,169,74,.35)}

@media (max-width:1100px){.cockpit{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}}
@media (max-width:760px){
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
/* Hauteur réelle de la barre, pour coller les onglets dessous */
function hb(){ var b = document.querySelector('.barre'); if (b) document.documentElement.style.setProperty('--haut-barre', b.offsetHeight + 'px'); }
hb(); window.addEventListener('resize', hb);
})();
"""
