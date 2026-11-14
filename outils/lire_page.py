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


def images(url, page_html):
    """Images de captures d'écran de la page, en base64 (pour les regarder hors ligne)."""
    import base64
    vues = set()
    for src in re.findall(r'(?:src|srcSet|srcset|href)="([^"]+\.(?:webp|png|jpg)[^"]*)"', page_html):
        u = urljoin(url, src.split(" ")[0])
        if "screen" not in u.lower() or u in vues or len(vues) >= 12:
            continue
        vues.add(u)
        try:
            r = requests.get(u, headers=UA, timeout=30)
            print(f"\n@@IMAGE {u} {len(r.content)}")
            b = base64.b64encode(r.content).decode()
            for k in range(0, len(b), 3000):
                print("@@B64 " + b[k:k + 3000])
            print("@@FIN")
        except Exception as e:
            print(f"@@ECHEC {u} {e}")


if __name__ == "__main__":
    for u in sys.argv[1:]:
        try:
            lire(u)
            images(u, requests.get(u, headers=UA, timeout=30).text)
        except Exception as e:
            print(f"\n######## {u} : échec {e}")
    # couleurs et polices du site (variables CSS)
    t = requests.get(sys.argv[1], headers=UA, timeout=30).text
    for css in dict.fromkeys(re.findall(r'href="([^"]+\.css[^"]*)"', t)):
        c = requests.get(urljoin(sys.argv[1], css), headers=UA, timeout=30).text
        print("\n-- CSS", css[:60], ":", " ".join(dict.fromkeys(re.findall(r"--[\w-]+:\s*[^;}{]{1,40}", c)))[:4000])
        print("-- familles :", " | ".join(dict.fromkeys(re.findall(r"font-family:\s*([^;}]+)", c)))[:600])
