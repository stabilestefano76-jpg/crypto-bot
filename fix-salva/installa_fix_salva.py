#!/usr/bin/env python3
"""FIX 'Salva impostazioni' in Analisi e rischio: il messaggio (Salvato / errore) ora compare SOTTO il bottone,
i campi mostrano i limiti permessi e dopo il salvataggio si rileggono i valori davvero salvati dal bot.
Lancialo dalla cartella crypto-bot. Copia in _backups/prima_fix_salva."""
import os, shutil

P = os.path.join("frontend", "app", "desk.tsx")
if not os.path.exists(P):
    raise SystemExit("STOP: lancia il comando dalla cartella crypto-bot. Non tocco nulla.")
t = open(P, encoding="utf-8").read()
if "saveMsg" in t:
    raise SystemExit("Gia' installato. Non tocco nulla.")


def once(t, old, new, label):
    if t.count(old) != 1:
        raise SystemExit("STOP: punto da modificare non trovato o duplicato (%s). Non tocco nulla." % label)
    return t.replace(old, new)


t = once(t, '  const [msg, setMsg] = useState("");\n', '  const [msg, setMsg] = useState("");\n  const [saveMsg, setSaveMsg] = useState("");\n', "stato")
t = once(t,
    '    try { await deskApi.setConfig(patch); setMsg("Salvato."); }\n    catch (e: any) { setMsg("ERRORE: " + String(e?.message ?? e)); }\n    setBusy(false); load();\n  };\n  const saveAutoNums',
    '    setSaveMsg("");\n    try {\n      await deskApi.setConfig(patch);\n      setSaveMsg("✓ Salvato. I valori qui sopra sono quelli ora in uso dal bot.");\n      if (af && Object.keys(patch).some((k) => k in af)) setAf(null);\n    }\n    catch (e: any) { setSaveMsg("NON salvato: " + String(e?.message ?? e)); }\n    setBusy(false); load();\n  };\n  const saveAutoNums', "setAuto")
t = once(t, 'if (!isFinite(n) || n < 0) { setMsg("ERRORE: scrivi numeri validi."); return; }',
         'if (!isFinite(n) || n < 0) { setSaveMsg("NON salvato: scrivi numeri validi (con il punto o la virgola)."); return; }', "numeri")
btn = '            <Text style={styles.btnText}>Salva impostazioni</Text>\n          </Pressable>\n'
if t.count(btn) != 2:
    raise SystemExit("STOP: bottoni salva non trovati (%d). Non tocco nulla." % t.count(btn))
shown = '{!!saveMsg && <Text style={[styles.msg, { marginTop: 8, color: saveMsg.startsWith("✓") ? colors.success : colors.error }]}>{saveMsg}</Text>}\n'
t = t.replace(btn, btn + "          " + shown)
for old, new in [
    ('"Leva massima del bot (si abbassa da sola se serve)"', '"Leva massima del bot, da 1 a 10 (si abbassa da sola se serve)"'),
    ('"Rischio per operazione (% del capitale)", "auto_risk_pct"', '"Rischio per operazione (% del capitale, da 0,1 a 3)", "auto_risk_pct"'),
    ('"Rischio totale massimo di tutte le posizioni (% del capitale)"', '"Rischio totale massimo di tutte le posizioni (% del capitale, da 0,5 a 10)"'),
    ('"Perdita massima al giorno (% del capitale)"', '"Perdita massima al giorno (% del capitale, da 0,5 a 10)"'),
    ('"Aperture automatiche al giorno (massimo)"', '"Aperture automatiche al giorno (massimo, da 1 a 20)"'),
    ('"Primo lotto chiuso quando il guadagno vale (volte il rischio)"', '"Primo lotto chiuso quando il guadagno vale (volte il rischio, da 0,3 a 3)"'),
    ('"Attesa su una moneta dopo una chiusura (minuti)"', '"Attesa su una moneta dopo una chiusura (minuti, da 0 a 1440)"'),
]:
    t = once(t, old, new, old[:30])
os.makedirs("_backups/prima_fix_salva", exist_ok=True)
shutil.copy(P, os.path.join("_backups/prima_fix_salva", "desk.tsx"))
open(P, "w", encoding="utf-8").write(t)
print("Fatto. Ora git add/commit/push.")
