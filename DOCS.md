# Installazione

## Da repository GitHub

1. Pubblica l'intero contenuto del pacchetto nella radice di un repository GitHub.
2. Modifica `repository.yaml` e `config.yaml`, sostituendo `USERNAME` con il tuo account GitHub.
3. In Home Assistant apri **Impostazioni → App → App Store**.
4. Dal menu in alto scegli **Repository** e aggiungi l'URL del repository.
5. Aggiorna lo store, apri **Irrigazione Centralizzata**, installa e avvia.
6. Attiva **Mostra nella barra laterale** e premi **Apri interfaccia web**.

## Installazione locale

La cartella `irrigazione_centralizzata` deve essere copiata nella condivisione Samba `addons`, non in `/config/addons`.
La destinazione deve risultare `/addons/irrigazione_centralizzata/config.yaml`.

Dopo la copia: **App Store → menu ⋮ → Controlla aggiornamenti**.

# Sicurezza operativa

Prima delle prove scollega la pompa o chiudi l'acqua. Verifica una zona alla volta e configura sempre un tempo massimo realistico. L'app prova a chiudere la valvola attiva e spegnere la pompa anche in caso di arresto o errore, ma non sostituisce protezioni hardware contro marcia a secco, sovrapressione o allagamento.
