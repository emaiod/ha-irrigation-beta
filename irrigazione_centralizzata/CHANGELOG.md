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
