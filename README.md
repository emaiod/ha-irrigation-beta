# Irrigation Controller

Integrazione custom nativa per Home Assistant che trasforma il precedente add-on di irrigazione in un componente installabile tramite HACS.

## Funzioni

- Configurazione dall'interfaccia di Home Assistant tramite config flow.
- Pannello grafico nella barra laterale.
- Zone associate a entità `valve`, `switch` o `input_boolean`.
- Programmi con giorni della settimana e uno o più orari.
- Avvio relativo ad alba o tramonto con offset.
- Sequenze di zone con durata indipendente.
- Pompa opzionale con anticipo e ritardo configurabili.
- Pausa configurabile fra una zona e la successiva.
- Skip zona in base a un sensore di umidità.
- Skip programma se l'entità meteo segnala pioggia.
- Avvio manuale, stop e salto zona dalla GUI.
- Servizi Home Assistant per automazioni e script.
- Registro persistente degli ultimi eventi.

## Installazione di sviluppo

Copia `custom_components/irrigation_controller` nella cartella `custom_components` della configurazione Home Assistant, riavvia Home Assistant e aggiungi **Irrigation Controller** da **Impostazioni → Dispositivi e servizi → Aggiungi integrazione**.

Dopo la configurazione comparirà la voce **Irrigation Controller** nella barra laterale.

## Servizi

- `irrigation_controller.run_program`
- `irrigation_controller.stop`
- `irrigation_controller.skip_zone`

## Stato del progetto

Questa è la prima conversione nativa (`0.1.0`). Il codice dell'add-on precedente rimane nel branch `main` come riferimento durante la migrazione.
