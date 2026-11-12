#!/usr/bin/env python3
"""CRUSCOTTO: una schermata con tutto (portafoglio simulato dedicato long/short, curva del capitale, esposizione,
limiti di rischio, posizioni con distanze, stato bot/Terminale, eventi) + saldi reali Bybit in SOLA LETTURA.
Serve 'analisi-rischio-scan' gia' installata. Lancialo dalla cartella crypto-bot. Copie in _backups/prima_cruscotto."""
import os, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
F = os.path.join(HERE, "files")
LINE = "# ============================================================================\n"
CRU = LINE + "# CRUSCOTTO"
TERM = LINE + "# TERMINALE"
DESK = LINE + "# ANALISI E RISCHIO"
A0, A1 = "// CRUSCOTTO-INIZIO\n", "// CRUSCOTTO-FINE\n"


def rd(*p):
    return open(os.path.join(F, *p), encoding="utf-8").read()


def _once(t, old, new, label):
    if t.count(old) != 1:
        raise SystemExit("STOP: punto da modificare non trovato o duplicato (%s). Non tocco nulla." % label)
    return t.replace(old, new)


def patch_server(s):
    if "desk_scan" not in s:
        raise SystemExit("STOP: installa prima 'analisi-rischio-scan'. Non tocco nulla.")
    if s.count(DESK) != 1:
        raise SystemExit("STOP: blocco Analisi e rischio non trovato. Non tocco nulla.")
    block = rd("backend", "cruscotto_block.py")
    if "crus_loop" in s:                                   # versione precedente: sostituisco il blocco
        if s.count(CRU) != 1:
            raise SystemExit("STOP: non riconosco la versione precedente. Non tocco nulla.")
        i = s.index(CRU)
        ends = [s.index(m, i) for m in (TERM, DESK) if m in s[i:]]
        if not ends:
            raise SystemExit("STOP: fine del blocco non trovata. Non tocco nulla.")
        return s[:i] + block + s[min(ends):]
    anchor = TERM if s.count(TERM) == 1 else DESK
    s = _once(s, anchor, block + anchor, "blocco")
    s = _once(s, "    desk_task = asyncio.create_task(desk_loop())\n",
              "    desk_task = asyncio.create_task(desk_loop())\n    crus_task = asyncio.create_task(crus_loop())\n", "avvio")
    s = _once(s, "    desk_task.cancel()\n", "    desk_task.cancel()\n    crus_task.cancel()\n", "stop")
    return s


def patch_api(s):
    new = A0 + rd("api_cruscotto.ts").split(A0, 1)[1].split(A1, 1)[0] + A1
    if A0 in s:
        if s.count(A0) != 1 or s.count(A1) != 1 or s.index(A0) > s.index(A1):
            raise SystemExit("STOP: api.ts non riconosciuto. Non tocco nulla.")
        return s[:s.index(A0)] + new + s[s.index(A1) + len(A1):]
    return s.rstrip("\n") + "\n\n" + new


def patch_strategies(s):
    if 'key: "cruscotto"' in s:
        return s
    return _once(s, '''    route: "/desk",
  },
''', '''    route: "/desk",
  },
  {
    key: "cruscotto",
    title: "Cruscotto",
    subtitle: "Tutto in una schermata: capitale, long e short, rischio, posizioni, bot (simulazione)",
    icon: "speedometer",
    route: "/cruscotto",
  },
''', "menu")


PATCHES = {"backend/server.py": patch_server, "frontend/src/api.ts": patch_api, "frontend/app/strategies.tsx": patch_strategies}
NEW = {"frontend/app/cruscotto.tsx": ("frontend", "app", "cruscotto.tsx")}


def apply_all(root):
    new = {}
    for rel, fn in PATCHES.items():
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            raise SystemExit("ERRORE: manca %s. Lancia questo comando dentro la cartella crypto-bot." % rel)
        new[rel] = fn(open(p, encoding="utf-8").read())
    for rel, parts in NEW.items():
        new[rel] = rd(*parts)
    bk = os.path.join(root, "_backups", "prima_cruscotto")
    os.makedirs(bk, exist_ok=True)
    for rel in new:
        p = os.path.join(root, rel)
        if os.path.isfile(p):
            shutil.copy2(p, os.path.join(bk, rel.replace("/", "__")))
    for rel, txt in new.items():
        open(os.path.join(root, rel), "w", encoding="utf-8").write(txt)
    return list(new)


if __name__ == "__main__":
    print("FATTO. Aggiornati/creati:", ", ".join(apply_all(".")))
