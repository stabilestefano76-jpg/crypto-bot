#!/usr/bin/env python3
"""Aggiunge il limite 'Massimo operazioni nella stessa direzione' (long o short) in Analisi e rischio.
Parte a 3. Vale per le aperture automatiche e manuali (le manuali si possono forzare). Dopo l'installazione le posizioni gia'
aperte non vengono toccate. Lancialo dalla cartella crypto-bot. Copie in _backups/prima_stessa_direzione."""
import os, shutil
L = os.path.join("backend", "desk_logic.py")
S = os.path.join("backend", "server.py")
D = os.path.join("frontend", "app", "desk.tsx")
A = os.path.join("frontend", "src", "api.ts")
for p in (L, S, D, A):
    if not os.path.exists(p):
        raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot (manca %s). Non tocco nulla." % p)
l, s, d, a = (open(p, encoding="utf-8").read() for p in (L, S, D, A))
if "max_same_dir" in l:
    raise SystemExit("Gia' installato. Non tocco nulla.")


def once(t, old, new, label):
    if t.count(old) != 1:
        raise SystemExit("STOP: punto da modificare non trovato o duplicato (%s). Non tocco nulla." % label)
    return t.replace(old, new)


l = once(l, '    "max_open": 3,\n', '    "max_open": 3,\n    "max_same_dir": 3,           # massimo di posizioni aperte nella STESSA direzione (long o short)\n', "default")
l = once(l, '"max_open": (1, 6),', '"max_open": (1, 6), "max_same_dir": (1, 6),', "limiti")
l = once(l, 'INT_KEYS = ("max_open", ', 'INT_KEYS = ("max_open", "max_same_dir", ', "interi")
FN = '''async def same_dir_why(side: str, dc: dict[str, Any], force: bool) -> list[str]:
    """Troppe posizioni nella stessa direzione: se il mercato gira perdono tutte insieme."""
    lim = int(dc.get("max_same_dir") or 0)
    if lim <= 0 or force:
        return []
    n = await db.desk_positions.count_documents({"status": "open", "side": side})
    if n >= lim:
        return ["gia' %d posizioni %s aperte (massimo %d nella stessa direzione)" % (n, side, lim)]
    return []


'''
s = once(s, "async def _desk_open_core(", FN + "async def _desk_open_core(", "funzione")
s = once(s, "        # rischio TOTALE (vale anche per le aperture manuali)\n",
         "        why += await same_dir_why(side, dc, force)\n        # rischio TOTALE (vale anche per le aperture manuali)\n", "controllo")
d = once(d, "max_open: String(s.config.max_open), ", "max_open: String(s.config.max_open), max_same_dir: String(s.config.max_same_dir ?? 3), ", "iniziale")
d = once(d, '          {autoField("Posizioni aperte insieme (massimo, da 1 a 6)", "max_open")}\n',
         '          {autoField("Posizioni aperte insieme (massimo, da 1 a 6)", "max_open")}\n'
         '          {autoField("Massimo nella stessa direzione (long o short, da 1 a 6)", "max_same_dir")}\n', "campo")
a = once(a, "funding_day_pct: number; max_open: number;\n  auto_enabled", "funding_day_pct: number; max_open: number; max_same_dir?: number;\n  auto_enabled", "tipo")
os.makedirs("_backups/prima_stessa_direzione", exist_ok=True)
for p in (L, S, D, A):
    shutil.copy(p, os.path.join("_backups/prima_stessa_direzione", os.path.basename(p)))
for p, t in ((L, l), (S, s), (D, d), (A, a)):
    open(p, "w", encoding="utf-8").write(t)
print("Fatto. Ora git add/commit/push.")
