# MPC Heat Controller

Separat Home Assistant-app för komfortvärme. Målmiljö: Home Assistant OS/Core 2026.9.1, Raspberry Pi 4 (aarch64), 8 GB RAM.

## Version 0.2.0

Timprognos hämtas från vald HA-väderentitet. Alla valda temperaturgivare visas med datastatus. I skuggläge loggas mätvärden och konfiguration var femte minut till lokal SQLite med 90 dagars retention, även när webbläsaren är stängd.

## Första körbara grund

- Svenskt webbgränssnitt med installationsguide, valbara givare och beständiga inställningar.
- Home Assistant-entiteter läses via Supervisor-proxyn när appen körs i HA. Inga installationsspecifika entitets-ID:n är hårdkodade.
- Demo med en enkel termisk exempelmodell och begränsad beam-search-planering över 24 timmar. Modellparametrarna är **inte identifierade från huset**. Exempelvädret är syntetiskt.
- Skuggläge läser valda givare och visar medeltemperatur. Det skapar ännu inga verkliga styrförslag: modellidentifiering återstår. Verklig väderprognos visas separat.
- CSV-granskning visar tidsperioder, värdegränser, ogiltiga rader och största intervall. Filen tränar ingen modell och sparas inte.
- Ingen kod skickar kommandon till Ohmigo, Roth eller PID. Aktiv drift stöds inte.

## Kör lokalt

Python 3.11 eller senare, utan tredjepartsberoenden:

```sh
cd mpc_heat_controller
python3 -m app.server
```

Öppna http://127.0.0.1:8099. Inställningar sparas i `data/settings.json`. `MPC_DATA` kan ange annan lagringsplats. Lokal server binds till loopback; publicera den inte direkt på nätet. Docker-konfigurationen är avsedd för HA:s interna Ingress-nät utan exponerad port.

```sh
PYTHONPATH=mpc_heat_controller python3 -m unittest discover -s tests -v
```

## Apppaketering

Appen ligger i `mpc_heat_controller/`, med `config.yaml` och `Dockerfile`. Repositoryts rot innehåller `repository.yaml` för Home Assistants appbutik. Användaren har bekräftat fungerande installation och webbgränssnitt för 0.1.0 i HA 2026.9.1. Ny prognoshämtning i 0.2.0 är testad med simulerade API-svar, men ännu inte verifierad mot användarens HA.

## Installera via Home Assistant

1. Öppna Inställningar → Appar → Installera app.
2. Öppna menyn med tre punkter och välj Repositories.
3. Lägg till `https://github.com/tobiaso88/mpc-heat-controller`.
4. Välj MPC Heat Controller och Installera. Containern byggs på din HA-enhet.
5. Starta appen och välj Öppna webbgränssnitt. Börja i Demo.

Repositoryt måste vara åtkomligt för Home Assistant. Denna version är för utvärdering och kan inte styra pumpen.

## Nästa implementation

1. Egna HA-entiteter via MQTT Discovery, med gemensam konfiguration för UI och börvärde samt livscykel/availability. Ännu inte implementerat.
2. Vidare utvärdering av prognoskvalitet mot verklig utetemperatur.
3. Historikmappning, modellidentifiering på träningsperiod och validering på separat period. Begränsade historiska styrsignaler måste skiljas från vad pumpen faktiskt mottog.
4. Verklig MPC i skuggläge med loggning. Produktionsregulator, automatisk modellträning och prestandaverifiering på Pi återstår.
5. Aktiv styrning först efter separat beslut och verifiering av Ohmigos watchdog, signalålder och återgång.

## Referenser

- https://developers.home-assistant.io/docs/apps/configuration/
- https://developers.home-assistant.io/docs/apps/communication/
- https://www.home-assistant.io/integrations/mqtt/

PID-projektet är separat och har inte ändrats. Privata planeringsanteckningar och historikfiler ingår inte i repositoryt.
