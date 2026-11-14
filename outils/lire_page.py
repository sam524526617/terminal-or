#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Affiche le texte lisible d'une page web publique (titres, paragraphes, textes alternatifs des images), et la liste
des liens internes, pour pouvoir étudier un site de référence depuis un poste qui n'y a pas accès."""
import html
import re
import sys
from urllib.parse import urljoin, urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "fr-FR,fr;q=0.9"}


def lire(url):
    r = requests.get(url, headers=UA, timeout=30)
    print(f"\n######## {url} · HTTP {r.status_code} · {len(r.text)} caractères")
    t = r.text
    couleurs = sorted(set(re.findall(r"#[0-9a-fA-F]{6}\b", t)))
    alts = re.findall(r'alt="([^"]+)"', t)
    corps = re.sub(r"<script.*?</script>|<style.*?</style>|<svg.*?</svg>", " ", t, flags=re.S | re.I)
    corps = re.sub(r"<(h[1-6])[^>]*>", lambda m: "\n## " if m.group(1) in ("h1", "h2") else "\n### ", corps, flags=re.I)
    texte = html.unescape(re.sub(r"<[^>]+>", "\n", corps))
    lignes, vus = [], set()
    for l in texte.split("\n"):
        l = re.sub(r"\s+", " ", l).strip()
        if l and l not in vus:
            vus.add(l)
            lignes.append(l)
    print("\n".join(lignes[:500]))
    if alts:
        print("\n-- textes alternatifs des images :", " | ".join(dict.fromkeys(alts))[:3000])
    print("-- couleurs trouvées dans la page :", " ".join(couleurs[:80]))
    hote = urlparse(url).netloc
    liens = sorted({urljoin(url, h).split("#")[0] for h in re.findall(r'href="([^"]+)"', t)
                    if urlparse(urljoin(url, h)).netloc == hote})
    return liens


if __name__ == "__main__":
    depart = sys.argv[1]
    liens = lire(depart)
    print("\n-- liens internes :", "\n".join(liens[:80]))
    pris = 0
    for l in liens:
        if pris >= 6 or l.rstrip("/") == depart.rstrip("/") or re.search(r"\.(png|jpg|svg|css|js|ico|xml|pdf)$|login|signin|register|legal|privacy|cgu|cookie", l, re.I):
            continue
        try:
            lire(l)
            pris += 1
        except Exception as e:
            print(f"\n######## {l} : échec {e}")
