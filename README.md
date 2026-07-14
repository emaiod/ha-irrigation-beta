# 💧 Irrigazione Centralizzata per Home Assistant

Add-on open source per trasformare le entità già presenti in Home Assistant in un sistema di irrigazione centralizzato, programmabile e sicuro.

## Funzioni principali

- Zone basate su `switch`, `valve` o `input_boolean`.
- Programmi con sequenze, durate e giorni indipendenti.
- Avvio manuale e automatico a orario fisso, alba o tramonto.
- Offset configurabile prima/dopo alba e tramonto.
- Pompa di sollevamento opzionale con anticipo e ritardo.
- Controlli meteo e umidità completamente escludibili.
- Notifiche di avvio, completamento, arresto ed errore.
- Salto della zona attiva o di una zona futura.
- Badge immediati: AUTO, MANUALE, DISABILITATO, ERRORE.
- Calendario delle prossime partenze.
- Registro irrigazioni e storico meteo persistenti in SQLite.
- Interfaccia responsive integrata tramite Home Assistant Ingress.

## Installazione

1. In Home Assistant apri **Impostazioni → App → App Store**.
2. Apri il menu **⋮ → Repository**.
3. Aggiungi l'URL di questo repository.
4. Installa **Irrigazione Centralizzata**, avviala e abilita **Mostra nella barra laterale**.

Richiede Home Assistant OS o Home Assistant Supervised e l'accesso API del Supervisor.

## Avvio ad alba o tramonto

Nel programma seleziona `Alba` o `Tramonto` e imposta un offset in minuti:

- `-30`: trenta minuti prima;
- `0`: all'evento;
- `+20`: venti minuti dopo.

Il calcolo utilizza l'entità `sun.sun` di Home Assistant.

## Sicurezza

Prima dell'attivazione l'add-on verifica la disponibilità della pompa e della valvola. In caso di errore tenta la chiusura della valvola, lo spegnimento della pompa, salva il problema nel registro e invia una notifica.

## Dati e privacy

Configurazione, log e storico meteo sono conservati localmente nel volume persistente `/data` dell'add-on. Nessun dato viene inviato a servizi esterni dall'add-on.

## Documentazione

Consulta [`irrigazione_centralizzata/DOCS.md`](irrigazione_centralizzata/DOCS.md) per la guida completa.

## Licenza

Distribuito con licenza MIT. Contributi, segnalazioni e pull request sono benvenuti.
