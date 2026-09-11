# MPC Heat Controller

Home Assistant-app för komfortvärme med eget webbgränssnitt, PI och experimentell modellutvärdering. Mål: HA OS 2026.9.1, Raspberry Pi 4 aarch64, 8 GB.

## Version 0.9.4

- Mobilanpassat gränssnitt med vädergraf och historiska temperatur-/reglergrafer.
- Elva egna avläsningsbara HA-sensorer via MQTT Discovery, med driftstatus och PI-värden.
- Installationsguide med valbara givare, komfortmål, väderkälla och PI-parametrar.
- PI i skuggläge eller uttryckligen aktiverad skrivning till Ohmigos number-entitet.
- Valbar automatisk återstart kräver tillgängliga, giltiga givarvärden som är högst 24 timmar gamla och två godkända kontroller. Manuellt stopp och sparade inställningar blockerar återstart tills du aktiverar igen.
- Aktiv PI kräver verifierad kommandowatchdog och avstängd gammal automation. Läs [instruktionerna](mpc_heat_controller/DOCS.md) före överlämning.
- Timprognos, mätloggning och sparad offlinejämförelse av husmodeller. Ingen aktiv MPC ännu.

## Installera

Lägg till https://github.com/tobiaso88/mpc-heat-controller under Inställningar → Appar → Installera app → Repositories. Installera eller uppdatera MPC Heat Controller och öppna webbgränssnittet.

## Lokal utveckling

Python 3.11 eller senare, inga tredjepartsberoenden:

```sh
cd mpc_heat_controller
python3 -m app.server
```

Öppna http://127.0.0.1:8099. Inställningar lagras i data/ eller MPC_DATA. Lokal bindning är endast loopback. Docker-versionen använder HA Ingress utan exponerad extern port.

Tester från repositoryts rot:

```sh
PYTHONPATH=mpc_heat_controller python3 -m unittest discover -s tests -v
```

Aktiv PI är testad med simulerade HA-svar. Ingen verklig utrustning har styrts av utvecklingstesterna. Hårdvarans watchdog och den aktiva driften behöver verifieras på installationen. PID-projektet är separat och orört. Privata historikfiler publiceras inte.
