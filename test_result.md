#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================

user_problem_statement: |
  Parte 1: riscrivere la logica di entry della strategia counter_trend (chip "Rev Pre-FVG")
  con reversal PRE-FVG nel consolidamento. Sequenza: impulso -> FVG lasciata indietro ->
  consolidamento -> pattern reversal DENTRO il box -> breakout in chiusura. Direzione trade =
  direzione breakout. Target = bordo opposto (lontano) della FVG nella direzione del breakout;
  TP1 = midpoint FVG (65%), TP2 = bordo lontano (35%). SL oltre il box.
  Parte 2: gestione SL post-TP1 -> a TP1 chiude 65% e mantiene lo SL ATR; solo quando il prezzo
  avanza +0.5% oltre TP1 sposta lo SL a TP1+commissioni; altrimenti mantiene lo SL ATR. TP2 chiude il resto.

backend:
  - task: "Strategia Reversal Pre-FVG (riscrittura analyze_pair_counter)"
    implemented: true
    working: "NA"
    file: "backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Riscritta analyze_pair_counter: box consolidamento -> direzione da breakout in chiusura -> pattern reversal dentro il box -> FVG target nella direzione del breakout (long=bearish FVG sopra, short=bullish FVG sotto) -> TP2=bordo lontano, TP1=midpoint -> SL oltre il box -> conferme volume+RSI. Validato con test sintetico (/tmp/test_counter_entry.py): LONG entry=box_high, SL<box_low, TP1=midpoint, TP2=far edge. Scan reale counter_trend 200 OK, 0 segnali (filtri rari per design). Serve retest con mocking di kucoin.get_klines per LONG e SHORT + gate R:R e invarianti direzione."
  - task: "Gestione SL post-TP1 (manage_counter_position + post_tp1_advance_pct)"
    implemented: true
    working: "NA"
    file: "backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Nuovo manage_counter_position instradato in manage_open_position per strategy=counter_trend; monitor tp_check esteso a counter_trend. A TP1 chiude tp1_pct 65% e MANTIENE lo SL ATR (no breakeven immediato). Dopo +post_tp1_advance_pct (0.5%) oltre TP1 sposta SL a TP1+fee; altrimenti resta ATR. TP2 chiude il resto. Validato macchina a stati con /tmp/test_counter.py (tutti i casi passati)."

frontend:
  - task: "Settings: chip 'Rev Pre-FVG' + sezione Reversal Pre-FVG Strategy"
    implemented: true
    working: "NA"
    file: "frontend/app/(tabs)/settings.tsx"
    stuck_count: 0
    priority: "medium"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Chip strategy-counter_trend rinominato 'Rev Pre-FVG'. Nuova sezione con input consolidamento/TP1/TP2/post_tp1_advance_pct (testID input-ct-*). Verificato via screenshot: sezione visibile con avanzamento post-TP1=0.5. Aggiunto campo post_tp1_advance_pct al tipo Config in src/api.ts."
  - task: "Detail: TP1/TP2 e spiegazioni per counter_trend"
    implemented: true
    working: "NA"
    file: "frontend/app/detail/[id].tsx"
    stuck_count: 0
    priority: "low"
    needs_retesting: true
    status_history:
        - working: "NA"
          agent: "main"
          comment: "Riga TP1(65%)/TP2(35%) ora mostrata anche per strategy=counter_trend; aggiunte spiegazioni reasonFor per pattern (Engulfing/Star), RSI HTF Extreme, RSI Momentum Turn."

metadata:
  created_by: "main_agent"
  version: "1.1"
  test_sequence: 9
  run_ui: false

test_plan:
  current_focus:
    - "Strategia Reversal Pre-FVG (riscrittura analyze_pair_counter)"
    - "Gestione SL post-TP1 (manage_counter_position + post_tp1_advance_pct)"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
    - agent: "main"
      message: |
        Implementate Parte 1 (entry reversal pre-FVG, riscrittura analyze_pair_counter) e Parte 2
        (gestione SL post-TP1, manage_counter_position). Ho gia' validato con test sintetici locali
        (/tmp/test_counter.py e /tmp/test_counter_entry.py) sia la macchina a stati post-TP1 sia
        l'entry LONG. Serve testing BACKEND: mockare kucoin.get_klines per costruire scenari LONG e
        SHORT deterministici e verificare: (1) direzione = breakout, (2) target = FVG opposta nella
        direzione del breakout (long=bearish FVG sopra -> TP2=top, short=bullish FVG sotto -> TP2=bottom),
        (3) TP1=midpoint, entry=box breakout level, SL oltre il box, (4) gate R:R (scarta se est_rr<min_rr_ratio),
        (5) manage_counter_position: TP1 65% mantiene SL ATR, +0.5% -> SL a TP1+fee, altrimenti SL resta, TP2 chiude.
        Endpoint: PUT /api/config {"strategy_mode":"counter_trend"}, POST /api/scan, GET /api/signals.
        FRONTEND (solo smoke): chip 'Rev Pre-FVG' (testID strategy-counter_trend) mostra la sezione
        "Reversal Pre-FVG Strategy" con input-ct-* e persiste post_tp1_advance_pct su Save.
        NON regredire: strategie scoring/impulse_fvg, Reset History, portfolio.

## Bybit migration + FVG Reversal (Fase 1 & 2)
backend:
  - task: "Migrazione KuCoin -> Bybit EU v5 (dati pubblici)"
    implemented: true
    working: true
    file: "backend/server.py"
    status_history:
      - working: true
        agent: "main"
        comment: "BybitClient (get_symbols/get_tickers/get_klines normalizzati), PriceFeed su WS Bybit v5, fees/funding/spread adattati, /exchange firma HMAC Bybit v5. Verificato: candele live 200 ascending, /pairs USDC+EUR, scan 20-25 coppie con segnali, WS connesso (ws_connected true). Vincoli EU: spot USDC/EUR (no USDT), nessun linear."
  - task: "Strategia FVG Reversal + selezione parallela (enabled_strategies)"
    implemented: true
    working: true
    file: "backend/server.py"
    status_history:
      - working: true
        agent: "main"
        comment: "analyze_pair_fvg_reversal (contro-trend su ritracciamento, param fvgr_*), manage_fvg_reversal_position (TP1 65%/post-TP1 SL->TP1+fee/trailing/TP2), dispatch parallelo via active_strategies. Test sintetico /tmp/test_fvgr.py: entry+gestione TUTTI PASS. Scan parallelo 4 strategie senza errori."
frontend:
  - task: "UI multi-select strategie + sezione FVG Reversal"
    implemented: true
    working: true
    file: "frontend/app/(tabs)/settings.tsx"
    status_history:
      - working: true
        agent: "main"
        comment: "Chip multi-select (enabled_strategies) + sezione FVG Reversal Strategy con parametri indipendenti. Screenshot conferma toggle multipli e sezioni condizionali. api.ts Config aggiornato."
agent_communication:
  - agent: "main"
    message: "Fase 1 (Bybit dati pubblici) e Fase 2 (FVG Reversal parallela) complete e verificate. Prossimo: Fase 3 (esecuzione reale Bybit + toggle paper/reale + ON/OFF + risk sizing 10/20/50/100 con esposizione combinata), Fase 4 (chiudi-tutte/parziale + rifinitura grafico live)."
