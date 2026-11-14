#!/usr/bin/env python3
"""Aggiunge in 'Analisi e rischio' il campo 'Posizioni aperte insieme (massimo)'. Il bot lo accetta da 1 a 6.
Lancialo dalla cartella crypto-bot. Copia in _backups/prima_campo_posizioni."""
import os, shutil
P = os.path.join("frontend", "app", "desk.tsx")
if not os.path.exists(P):
    raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot. Non tocco nulla.")
t = open(P, encoding="utf-8").read()
if '"max_open"' in t:
    raise SystemExit("Gia' installato. Non tocco nulla.")
A = "max_trades_day: String(s.config.max_trades_day), "
B = 'autoField("Aperture automatiche al giorno (massimo, da 1 a 20)", "max_trades_day")}\n'
if t.count(A) != 1 or t.count(B) != 1:
    raise SystemExit("STOP: punto da modificare non trovato. Non tocco nulla.")
t = t.replace(A, A + "max_open: String(s.config.max_open), ")
t = t.replace(B, B + '          {autoField("Posizioni aperte insieme (massimo, da 1 a 6)", "max_open")}\n')
os.makedirs("_backups/prima_campo_posizioni", exist_ok=True)
shutil.copy(P, "_backups/prima_campo_posizioni/desk.tsx")
open(P, "w", encoding="utf-8").write(t)
print("Fatto. Ora git add/commit/push.")
