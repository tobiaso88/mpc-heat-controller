# Home Assistant-dashboard

Dashboarden i `dashboard.yaml` använder bara Home Assistants inbyggda kort. Den visar appens driftstatus, komfort, hela temperaturkedjan, PI-delar och 24 timmars historik. Start och stopp ligger kvar i appen eftersom de kräver säkerhetsbekräftelser.

## Lägg in dashboarden

1. Uppdatera och starta MPC Heat Controller 0.10.0. Vänta någon minut så att MQTT-entiteterna har skapats.
2. Kontrollera under **Inställningar → Enheter och tjänster → MQTT → MPC Heat Controller** att de sexton entiteterna finns.
3. Skapa en ny dashboard under **Inställningar → Dashboards → Lägg till dashboard**.
4. Öppna dashboarden, välj redigering och därefter **Råkonfigurationsredigerare**.
5. Ersätt innehållet med hela innehållet i `dashboard.yaml` och spara.

Vill du behålla en befintlig dashboard lägger du i stället till objektet under `views:`
som en ny vy i den befintliga råkonfigurationens egen `views:`-lista. Ersätt inte hela
konfigurationen, eftersom befintliga vyer då försvinner.

Home Assistant bestämmer entity-ID första gången en MQTT-entitet skapas. Filen använder de normala automatiskt skapade ID:na. Om någon entitet tidigare har döpts om ersätter du motsvarande ID i dashboardfilen med det som visas på enhetssidan.

Den beständiga felnotisen kräver ingen automation. För pushnotis till en telefon behövs däremot en separat HA-automation riktad till just den telefonens notify-tjänst.

## Pushnotis när PI är inaktiv

Blueprinten `blueprints/automation/mpc_heat_controller/pi_inactive_notification.yaml`
skickar en pushnotis om PI-status inte är `active` under tre minuter. Fördröjningen kan
ändras när automationen skapas. Den kontrollerar också status efter varje omstart av
Home Assistant.

1. Importera blueprinten från dess GitHub-URL eller kopiera filen till
   `/config/blueprints/automation/mpc_heat_controller/`.
2. Öppna **Inställningar → Automationer och scener → Blueprints** och skapa en automation
   från **MPC Heat Controller – meddela när PI är inaktiv**.
3. Välj sensorn **MPC Heat Controller PI-status**, telefonen och önskad fördröjning.
4. Spara och slå på automationen.

Telefonen måste vara registrerad genom Home Assistant Companion-appen och ha tillåtelse
att visa notiser.
