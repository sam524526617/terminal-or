#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Captures d'écran du terminal en ligne (pour les visuels et vidéos), imprimées en base64 dans le journal.
Lancement manuel par le workflow « Captures du terminal »."""
import base64
import sys

from playwright.sync_api import sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "https://sam524526617.github.io/terminal-or/"


def sortir(nom, data):
    b = base64.b64encode(data).decode()
    print(f"\n@@IMAGE {nom} {len(data)}")
    for k in range(0, len(b), 4000):
        print("@@B64 " + b[k:k + 4000])
    print("@@FIN", flush=True)


def onglet(p, cle):
    p.evaluate(f"window.terminalOr && window.terminalOr.onglet && window.terminalOr.onglet('{cle}')")
    p.wait_for_timeout(1200)


with sync_playwright() as pw:
    b = pw.chromium.launch()
    p = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
    p.goto(URL, wait_until="networkidle", timeout=90000)
    p.wait_for_timeout(9000)                      # TradingView et polices
    p.evaluate("document.querySelectorAll('.tabpanel').length")
    sortir("accueil", p.screenshot(type="jpeg", quality=82))
    for sel, nom in ((".ws-dec", "decision"), (".ws-gr", "graphique"), (".ws-niv", "niveaux"), (".ws-pq", "pourquoi_panneau"),
                     (".ws-mk", "marches"), (".ws-sq", "squawk"), (".ws-ag", "agenda")):
        try:
            sortir(nom, p.locator(sel).first.screenshot(type="jpeg", quality=85))
        except Exception as e:
            print(f"@@ECHEC {nom} {e}")
    for cle, sel, nom in (("resume", "#sec-resume", "resume"), ("options", "#sec-options .op-deux", "options"),
                          ("pourquoi", "#sec-pourquoi", "pourquoi"), ("fondamental", "#sec-fondamental section.fd", "fondamental"),
                          ("data", "#sec-data", "positionnement"), ("annonces", "#sec-annonces", "agenda_semaine"),
                          ("entrainement", "#en-jeu .en-graph", "entrainement"), ("entrainement", "#en-guide", "guide"),
                          ("entrainement", "#en-sim", "simulateur")):
        try:
            onglet(p, cle)
            el = p.locator(sel).first
            el.scroll_into_view_if_needed()
            p.wait_for_timeout(600)
            sortir(nom, el.screenshot(type="jpeg", quality=85))
        except Exception as e:
            print(f"@@ECHEC {nom} {e}")
    b.close()
