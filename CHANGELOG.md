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
