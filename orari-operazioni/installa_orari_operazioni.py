#!/usr/bin/env python3
"""Mostra data e ora di apertura e di chiusura (e la durata) su ogni operazione in 'Analisi e rischio'
e su ogni posizione aperta del Cruscotto. Orari nel fuso del tuo computer. Lancialo dalla cartella crypto-bot.
Copie in _backups/prima_orari_operazioni."""
import os, shutil
D = os.path.join("frontend", "app", "desk.tsx")
C = os.path.join("frontend", "app", "cruscotto.tsx")
for p in (D, C):
    if not os.path.exists(p):
        raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot (manca %s). Non tocco nulla." % p)
d = open(D, encoding="utf-8").read()
c = open(C, encoding="utf-8").read()
if "const orario = " in d or "const orario = " in c:
    raise SystemExit("Gia' installato. Non tocco nulla.")


def once(t, old, new, label):
    if t.count(old) != 1:
        raise SystemExit("STOP: punto da modificare non trovato o duplicato (%s). Non tocco nulla." % label)
    return t.replace(old, new)


HELP = '''const orario = (v?: string | number | null) => {
  if (v === undefined || v === null || v === "") return "-";
  const dt = new Date(typeof v === "number" ? v * 1000 : v);
  if (isNaN(dt.getTime())) return "-";
  return dt.toLocaleString("it-IT", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
};
const durata = (a?: string | number | null, b?: string | number | null) => {
  const t = (v?: string | number | null) => (v === undefined || v === null || v === "" ? NaN : new Date(typeof v === "number" ? v * 1000 : v).getTime());
  const m = Math.max(0, (t(b) - t(a)) / 60000);
  if (!isFinite(m)) return "-";
  return m < 60 ? `${Math.round(m)} min` : m < 1440 ? `${Math.floor(m / 60)} h ${Math.round(m % 60)} min` : `${Math.floor(m / 1440)} g ${Math.round((m % 1440) / 60)} h`;
};
'''
d = once(d, 'const reasonText = (r?: string) => (r === "chiusura_manuale" ? "chiusa da te" : r ?? "");\n',
         'const reasonText = (r?: string) => (r === "chiusura_manuale" ? "chiusa da te" : r ?? "");\n' + HELP, "aiuto desk")
d = once(d, "      <Text style={styles.line}>Entrata {px(p.fill_price)} · importo {usd(p.notional)} · margine {usd(p.margin)}</Text>\n",
         "      <Text style={styles.line}>Entrata {px(p.fill_price)} · importo {usd(p.notional)} · margine {usd(p.margin)}</Text>\n"
         "      <Text style={styles.note}>\n"
         "        Aperta {orario(p.opened_at)}{isOpen ? ` · da ${durata(p.opened_at, new Date().toISOString())}` : ` · chiusa ${orario(p.closed_at)} · durata ${durata(p.opened_at, p.closed_at)}`}\n"
         "      </Text>\n", "riga desk")
c = once(c, "function Gauge(", HELP + "\nfunction Gauge(", "aiuto cruscotto")
c = once(c, "      <MngLine m={(p as any).mng} />\n    </View>\n  );\n}\n",
         "      <Text style={st.dim}>Aperta {orario(p.opened_ts)}</Text>\n      <MngLine m={(p as any).mng} />\n    </View>\n  );\n}\n", "riga cruscotto")
os.makedirs("_backups/prima_orari_operazioni", exist_ok=True)
shutil.copy(D, "_backups/prima_orari_operazioni/desk.tsx"); shutil.copy(C, "_backups/prima_orari_operazioni/cruscotto.tsx")
open(D, "w", encoding="utf-8").write(d); open(C, "w", encoding="utf-8").write(c)
print("Fatto. Ora git add/commit/push.")
