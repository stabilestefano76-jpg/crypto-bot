#!/usr/bin/env python3
"""Toglie la regola 'dopo N perdite di fila il bot si ferma per qualche ora' (Analisi e rischio e Terminale).
Restano il rischio totale massimo e il limite di perdita giornaliero. Lancialo dalla cartella crypto-bot.
Copie in _backups/prima_senza_pausa."""
import os, shutil
L = os.path.join("backend", "desk_logic.py")
D = os.path.join("frontend", "app", "desk.tsx")
if not (os.path.exists(L) and os.path.exists(D)):
    raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot. Non tocco nulla.")
l = open(L, encoding="utf-8").read()
d = open(D, encoding="utf-8").read()
if "regola tolta su richiesta" in l:
    raise SystemExit("Gia' installato. Non tocco nulla.")
old = "    if n <= 0 or len(recent_pnls_newest_first) < n or since_last_close_h is None:\n        return None\n"
if l.count(old) != 1:
    raise SystemExit("STOP: punto da modificare non trovato. Non tocco nulla.")
l = l.replace(old, "    return None  # regola tolta su richiesta: nessuna pausa dopo le perdite di fila\n" + old)
txt_old = "Dopo {st.config.streak_pause} perdite di fila il bot si ferma per {st.config.streak_pause_h} ore. Il rischio totale"
if d.count(txt_old) == 1:
    d = d.replace(txt_old, "Il bot non si ferma più dopo le perdite di fila. Il rischio totale")
os.makedirs("_backups/prima_senza_pausa", exist_ok=True)
shutil.copy(L, "_backups/prima_senza_pausa/desk_logic.py"); shutil.copy(D, "_backups/prima_senza_pausa/desk.tsx")
open(L, "w", encoding="utf-8").write(l); open(D, "w", encoding="utf-8").write(d)
print("Fatto. Ora git add/commit/push.")
