#!/usr/bin/env python3
"""La simulazione (Analisi e rischio e Terminale) NON usa piu' il campo 'Perdita massima possibile per operazione (USDC)'
di Trading reale: decide il rischio per operazione solo la percentuale impostata in Analisi e rischio.
Cosi' puoi rimettere quel campo basso (1,5) per proteggere le strategie con soldi veri. Lancialo dalla cartella crypto-bot.
Copia in _backups/prima_sim_rischio_libero."""
import os, shutil
P = os.path.join("backend", "server.py")
if not os.path.exists(P):
    raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot. Non tocco nulla.")
t = open(P, encoding="utf-8").read()
old = 'max_loss_usdc=s["max_loss_usdc"] if s["check_max_loss"] else None)'
new = 'max_loss_usdc=None)'
n = t.count(old)
if n == 0 and 'max_loss_usdc=None)' in t:
    raise SystemExit("Gia' installato. Non tocco nulla.")
if n != 2:
    raise SystemExit("STOP: trovati %d punti invece di 2. Non tocco nulla." % n)
os.makedirs("_backups/prima_sim_rischio_libero", exist_ok=True)
shutil.copy(P, "_backups/prima_sim_rischio_libero/server.py")
open(P, "w", encoding="utf-8").write(t.replace(old, new))
print("Fatto. Ora git add/commit/push.")
