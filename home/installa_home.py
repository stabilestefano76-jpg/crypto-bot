#!/usr/bin/env python3
"""HOME 'OGGI': nuova prima schermata (stato bot, capitale, posizioni aperte, segnali, scorciatoie con nome, tutto in italiano).
Serve il Cruscotto gia' installato. Lancialo dalla cartella crypto-bot. Copie in _backups/prima_home."""
import os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join("frontend", "app", "(tabs)", "index.tsx")
LAY = os.path.join("frontend", "app", "(tabs)", "_layout.tsx")
BK = os.path.join("_backups", "prima_home")

if not os.path.exists(IDX) or not os.path.exists(LAY):
    raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot. Non tocco nulla.")
if "crusApi" not in open(os.path.join("frontend", "src", "api.ts"), encoding="utf-8").read():
    raise SystemExit("STOP: installa prima il Cruscotto. Non tocco nulla.")
old = open(IDX, encoding="utf-8").read()
if "OGGI-HOME" not in old and "SignalsScreen" not in old:
    raise SystemExit("STOP: la prima schermata non e' quella che mi aspetto. Non tocco nulla.")

lay = open(LAY, encoding="utf-8").read()
for a, b in [('title: "Signals"', 'title: "Oggi"'), ('title: "Portfolio"', 'title: "Portafoglio"'),
             ('title: "History"', 'title: "Storico"'), ('title: "Settings"', 'title: "Impostazioni"')]:
    lay = lay.replace(a, b)

os.makedirs(BK, exist_ok=True)
if not os.path.exists(os.path.join(BK, "index.tsx")):
    shutil.copy(IDX, os.path.join(BK, "index.tsx"))
    shutil.copy(LAY, os.path.join(BK, "_layout.tsx"))
new = open(os.path.join(HERE, "files", "frontend", "app", "(tabs)", "index.tsx"), encoding="utf-8").read()
open(IDX, "w", encoding="utf-8").write(new)
open(LAY, "w", encoding="utf-8").write(lay)
print("Fatto: nuova schermata 'Oggi' installata. Ora git add/commit/push.")
