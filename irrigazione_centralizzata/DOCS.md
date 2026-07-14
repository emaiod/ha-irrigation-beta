# Documentazione – Irrigazione Centralizzata 0.2.5

## Requisiti

- Home Assistant OS oppure Home Assistant Supervised.
- Almeno una valvola rappresentata da `switch`, `valve` o `input_boolean`.
- Facoltativi: pompa, entità `weather`, sensori di umidità e servizio `notify`.

## Configurazione iniziale

### 1. Zone

Per ogni zona indica nome, entità della valvola e durata massima di sicurezza. Umidità e tipo di terreno possono essere disabilitati completamente.

### 2. Programmi

Un programma contiene una sequenza ordinata di zone. Può essere:

- **AUTO**: giorni e almeno una partenza valida;
- **MANUALE**: nessuna partenza automatica;
- **DISABILITATO**: non viene avviato dallo scheduler;
- **ERRORE**: giorni/orari configurati in modo incompleto.

### 3. Partenze automatiche

Sono supportati più orari fissi separati da virgola e una partenza relativa ad alba o tramonto. È possibile combinarli nello stesso programma. L'offset solare accetta valori tra -240 e +240 minuti.

### 4. Pompa

Quando abilitata, la pompa viene accesa prima della prima zona, rispettando l'anticipo configurato. Alla fine viene spenta dopo il ritardo impostato. Anche in caso di arresto o errore l'add-on tenta lo spegnimento.

### 5. Meteo

Il modulo meteo è facoltativo. Se attivo, lo stato dell'entità meteo può bloccare il programma in condizioni piovose. L'add-on salva inoltre uno storico periodico e una fotografia meteo all'inizio e alla fine di ogni programma.

## Pagina Utilizzo

Mostra programma e zona attivi, tempo rimanente e sequenza completa. È possibile arrestare tutto, saltare la zona corrente o escludere una zona ancora in attesa.

## Calendario

La pagina Calendario elenca le partenze dei successivi 30 giorni. Per alba/tramonto viene mostrato il prossimo evento calcolato da `sun.sun`.

## Registro e storico meteo

Il registro riporta durata, origine dell'avvio, esito e messaggio. Lo storico meteo comprende condizione, temperatura, umidità, pressione, vento e precipitazioni quando disponibili.

## Notifiche

Seleziona un servizio `notify.*` oppure la notifica persistente di Home Assistant. Sono notificati avvio, completamento, arresto, valvole/pompe mancanti ed errori.

## Aggiornamenti

A ogni rilascio il campo `version` in `config.yaml` deve essere incrementato e il `CHANGELOG.md` aggiornato. Poi richiedi **Controlla aggiornamenti** nell'App Store di Home Assistant.

## Backup

Il database SQLite risiede in `/data/irrigation.db` ed è incluso nei backup dell'add-on di Home Assistant.
