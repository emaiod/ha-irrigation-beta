## 0.3.2

- Registro irrigazioni ridisegnato con card responsive leggibili anche da smartphone.
- Gli esiti `error` mostrano il motivo completo in un riquadro dedicato **Motivo dell’errore**.
- Visualizzati chiaramente data, programma, zona, durata, origine dell’avvio ed esito.
- Se un vecchio errore non contiene un messaggio, viene mostrato un avviso esplicito anziché una nota vuota.
- Aggiunto cache busting degli asset frontend alla versione 0.3.2.

## 0.3.1

- Portale operatori esteso con modifica della programmazione.
- Aggiunto avvio manuale delle singole zone con durata selezionabile.
- Aggiunte sezioni Stato, Programmi e Zone nel portale esterno.
- Registrazione delle modifiche e degli avvii manuali nel registro attività operatori.

## 0.3.0

- Portale operatori riscritto con template HTML, CSS e JavaScript separati.
- Eliminato l'uso di stringhe HTML formattate dentro Python che causava `KeyError: font-family`.
- Accesso separato con username e password gestiti dall'add-on.
- Dashboard operatore con stato, programma, zona, tempo residuo e sequenza completa.
- Comandi operatore per avvio programma, arresto totale e salto delle zone attive o future.
- Redirect automatico dalla porta 8100 a `/operator`.
- Cookie di sessione HTTP-only e registro delle azioni degli operatori.
- Nessuna modifica al motore di irrigazione o all'interfaccia amministrativa esistente.

## 0.2.8

- Corretto definitivamente l’avvio del portale operatori sulla porta 8100.
- Rinominato il modulo ASGI in `operator_portal` per evitare conflitti con il modulo Python `operator`.
- Forzata una nuova compilazione dell’add-on per evitare il riutilizzo del build 0.2.7.
- Rimossi i file `__pycache__` dal pacchetto.

## 0.2.7

- Portale operatori separato sulla porta 8100.
- Gestione utenti operatori dal pannello amministrativo.
- Credenziali separate da Home Assistant e registro attività.

## 0.2.6

- Corretto il caricamento degli asset frontend tramite cache busting Ingress.
- Ripristinati e verificati i pulsanti **Salta zona** sulla zona attiva e sulle zone in attesa.
- Verificata la visualizzazione dei badge AUTO, MANUALE, DISABILITATO ed ERRORE.
- Mantenute le funzioni calendario, alba/tramonto e storico meteo della 0.2.5.

## 0.2.5

- Badge AUTO, MANUALE, DISABILITATO ed ERRORE su ogni programma.
- Riepilogo di giorni, orari e prossima partenza nelle card.
- Validazione delle pianificazioni incomplete.
- Partenza automatica relativa ad alba o tramonto, con anticipo/ritardo configurabile.
- Nuova pagina Calendario con le prossime partenze automatiche.
- Storico meteo persistente, acquisito periodicamente e all'inizio/fine dei programmi.
- Documentazione GitHub ampliata per installazione, configurazione e pubblicazione.

## 0.2.4

- Aggiunto il pulsante **Salta zona** direttamente sulla zona in corso.
- Aggiunta la possibilità di escludere una zona ancora in attesa prima che venga eseguita.
- Le zone saltate preventivamente vengono ignorate dal motore e registrate nel log.
- Migliorato il riepilogo del programma in corso con stato e azioni per ogni zona.

## 0.2.3

- Vista completa del programma in corso con stato di ogni zona.
- Comando per saltare la zona attiva e passare alla successiva.
- Notifiche per avvio, completamento, arresto ed errori.
- Segnalazione immediata delle entità mancanti o non disponibili.
- Configurazione della destinazione notifiche Home Assistant.

## 0.2.2

- Aggiunta card Programmazione nella pagina principale.
- Mostrati numero di programmi, numero di zone e prossima partenza pianificata.
- Aggiunto accesso rapido alla pagina Programmazione.

# Changelog

## 0.2.1
- Aggiunta modifica dei programmi esistenti.
- Il modulo viene compilato con giorni, orari, pompa, meteo e sequenza zone già salvati.
- Aggiunto pulsante per annullare la modifica senza alterare il programma.

## 0.2.0
- Prima versione installabile come repository Home Assistant.
- Interfaccia Ingress per utilizzo, programmazione e log.
- Zone basate su entità `switch`, `valve` o `input_boolean`.
- Programmi con giorni, orari, sequenza e durata per zona.
- Pompa opzionale con anticipo di accensione e ritardo di spegnimento.
- Moduli opzionali per meteo, umidità del terreno e tipo terreno.
- Arresto generale e limiti massimi di sicurezza.
- Registro persistente SQLite in `/data`.