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
        return patch_data(patch_fees(s[:i] + block + s[min(ends):]))
    anchor = TERM if s.count(TERM) == 1 else DESK
    s = _once(s, anchor, block + anchor, "blocco")
    s = _once(s, "    desk_task = asyncio.create_task(desk_loop())\n",
              "    desk_task = asyncio.create_task(desk_loop())\n    crus_task = asyncio.create_task(crus_loop())\n", "avvio")
    s = _once(s, "    desk_task.cancel()\n", "    desk_task.cancel()\n    crus_task.cancel()\n", "stop")
    return patch_data(patch_fees(s))


FEE_PATCHES = [
    ('fee_pct=cfg.sim_fee_pct, funding_day_pct=', 'fee_pct=_desk_fee(cfg), funding_day_pct='),
    ('\n        ev = PEC.evaluate(price=price, notional=pl["notional"], fee_pct=cfg.sim_fee_pct,', '\n        ev = PEC.evaluate(price=price, notional=pl["notional"], fee_pct=_desk_fee(cfg),'),
    ('exit_price, p["quantity"], cfg.sim_fee_pct, carry, p["margin"])', 'exit_price, p["quantity"], _desk_fee(cfg), carry, p["margin"])'),
    ('ex, part_qty, cfg.sim_fee_pct, carry, part_margin)', 'ex, part_qty, _desk_fee(cfg), carry, part_margin)'),
    ('cur, p["quantity"], cfg.sim_fee_pct, carry, p["margin"])', 'cur, p["quantity"], _desk_fee(cfg), carry, p["margin"])'),
]


def patch_fees(s):
    for old, new in FEE_PATCHES:
        if old in s:
            s = _once(s, old, new, "costi: " + old[:40])
        elif new not in s:
            raise SystemExit("STOP: punto dei costi non trovato (%s). Non tocco nulla." % old[:40])
    return s


def patch_data(s):
    """Prezzi, candele e libro ordini di Kraken per Analisi e rischio (solo quando il modo e' Kraken)."""
    i = s.index(DESK)
    k1 = s.index(LINE, i + len(DESK))
    k2 = s.index(LINE, k1 + len(LINE))
    reg = s[i:k2]
    if "simulate_fill(" in reg:
        if reg.count("simulate_fill(") != 7:
            raise SystemExit("STOP: attese 7 chiamate di prezzo in Analisi e rischio, trovate %d. Non tocco nulla." % reg.count("simulate_fill("))
        s = s[:i] + reg.replace("simulate_fill(", "_desk_fill(") + s[k2:]
    if 'pl = DK.risk_plan(side, price, frames["1h"]' in s:
        s = _once(s, 'pl = DK.risk_plan(side, price, frames["1h"]', 'pl = _desk_risk_plan(side, price, frames["1h"]', "piano RR")
    if "kr_price(symbol)" not in s:
        s = _once(s, "async def _desk_price(symbol: str) -> Optional[float]:\n",
                  "async def _desk_price(symbol: str) -> Optional[float]:\n    if _crus_cost[\"mode\"] == \"kraken\":\n        _kp = await kr_price(symbol)\n        if _kp:\n            return _kp\n", "prezzo")
    if "kr_candles(symbol, tf)" not in s:
        s = _once(s, "async def _desk_candles(symbol: str, tf: str) -> list[list[float]]:\n",
                  "async def _desk_candles(symbol: str, tf: str) -> list[list[float]]:\n    if _crus_cost[\"mode\"] == \"kraken\":\n        _kc = await kr_candles(symbol, tf)\n        if _kc:\n            return _kc\n", "candele")
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
