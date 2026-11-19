#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Voix off de synthèse (Piper, voix libres) : une piste par phrase, imprimée en base64 dans le journal.
Lancement manuel par le workflow « Voix off »."""
import base64
import json
import subprocess
import sys
import wave
from pathlib import Path

LIGNES = [
    "Lui, c'est Max. Il trade l'or.",
    "Ça monte ? Il achète.",
    "Et là… annonce de la Fed.",
    "Tout s'effondre.",
    "Le pire ?",
    "Tout était là. Le dollar, les taux, l'annonce.",
    "Il ne savait juste pas le lire.",
    "Même journée.",
    "Mais cette fois, il a Terminal Or.",
    "Annonce de la Fed à vingt heures : il attend.",
    "Le dollar monte, le fond est baissier : il cherche une vente.",
    "Une zone. Un stop. Un objectif.",
    "Et cette fois… il comprend ce qu'il fait.",
    "Terminal Or. Comprendre l'or avant de le trader. Bientôt disponible.",
]
VOIX = sys.argv[1:] or ["fr_FR-tom-medium"]
for v in VOIX:
    nom, spk = (v.split(":") + [None])[:2]
    langue = nom.split("-")[0]
    base = f"https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/{langue}/{nom.split('-')[1]}/{nom.split('-')[2]}"
    for f in (f"{nom}.onnx", f"{nom}.onnx.json", "MODEL_CARD"):
        subprocess.run(["curl", "-sSfL", "-o", f"{nom}_{f}" if f == "MODEL_CARD" else f, f"{base}/{f}"], check=True)
    print(f"\n@@CARTE {v}\n" + Path(f"{nom}_MODEL_CARD").read_text()[:1500])
    for i, l in enumerate(LIGNES):
        out = f"{nom}_{spk or 0}_{i:02d}.wav"
        cmd = ["piper", "--model", f"{nom}.onnx", "--output_file", out, "--length_scale", "1.05", "--sentence_silence", "0.25"]
        if spk is not None:
            cmd += ["--speaker", spk]
        subprocess.run(cmd, input=l.encode(), check=True, capture_output=True)
        with wave.open(out) as w:
            d = w.getnframes() / w.getframerate()
        b = base64.b64encode(Path(out).read_bytes()).decode()
        print(f"\n@@AUDIO {v} {i} {d:.3f}")
        for k in range(0, len(b), 4000):
            print("@@B64 " + b[k:k + 4000])
        print("@@FIN", flush=True)
