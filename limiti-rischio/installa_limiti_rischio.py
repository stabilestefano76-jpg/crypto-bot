#!/usr/bin/env python3
"""Alza i limiti di 'Analisi e rischio': rischio totale massimo e perdita massima al giorno da 10% a 15% del capitale
(servono per tenere 4 o piu' posizioni da 3%). Lancialo dalla cartella crypto-bot. Copie in _backups/prima_limiti_rischio."""
import os, shutil
L = os.path.join("backend", "desk_logic.py")
D = os.path.join("frontend", "app", "desk.tsx")
if not (os.path.exists(L) and os.path.exists(D)):
    raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot. Non tocco nulla.")
l = open(L, encoding="utf-8").read()
d = open(D, encoding="utf-8").read()
if '"max_total_risk_pct": (0.5, 15.0)' in l:
    raise SystemExit("Gia' installato. Non tocco nulla.")
for t, old, label in [(l, '"max_total_risk_pct": (0.5, 10.0)', "rischio totale"), (l, '"daily_loss_pct": (0.5, 10.0)', "perdita giorno")]:
    if t.count(old) != 1:
        raise SystemExit("STOP: punto da modificare non trovato (%s). Non tocco nulla." % label)
l = l.replace('"max_total_risk_pct": (0.5, 10.0)', '"max_total_risk_pct": (0.5, 15.0)').replace('"daily_loss_pct": (0.5, 10.0)', '"daily_loss_pct": (0.5, 15.0)')
d = d.replace("da 0,5 a 10)", "da 0,5 a 15)")        # le scritte sotto i campi (se la correzione 'salva' e' installata)
os.makedirs("_backups/prima_limiti_rischio", exist_ok=True)
shutil.copy(L, "_backups/prima_limiti_rischio/desk_logic.py"); shutil.copy(D, "_backups/prima_limiti_rischio/desk.tsx")
open(L, "w", encoding="utf-8").write(l); open(D, "w", encoding="utf-8").write(d)
print("Fatto. Ora git add/commit/push.")
