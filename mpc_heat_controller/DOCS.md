# MPC Heat Controller

Förhandsversion 0.2.0. Öppna webbgränssnittet via Home Assistant.

Installationsguiden låter dig välja temperaturgivare och komfortmål. I skuggläge läser appen valda givare via Home Assistants interna API. Ingen separat token behövs.

Demo använder ett syntetiskt exempelhus och exempelväder. Den separata vädertabellen visar verklig timprognos från vald väderentitet, exempelvis Met.no. Ingen väderdata blandas in i den syntetiska inomhusprognosen.

Skuggläget visar mätvärden och loggar dem var femte minut i `/data/measurements.sqlite`, tillsammans med konfigurationen. Loggningen fungerar även med stängt webbgränssnitt. Rader äldre än 90 dagar rensas. Saknade eller gamla värden märks i loggen, inte fylls i. Rum valda för uppföljning ingår inte i regleringens medeltemperatur.

Väder hämtas var 30:e minut via `weather.get_forecasts` med typen `hourly`. Vid fel försöker appen igen vid nästa insamling. Tidpunkten i UI anger när appen hämtade prognosen, inte när leverantören skapade den. Enheter utan timprognos ger ett felmeddelande. Givare som inte rapporterat på två timmar markeras som gamla; gränsen är tills vidare fast.

Egna HA-entiteter och en kalibrerad MPC återstår. Appen skickar inga kommandon till värmepumpen.

Inställningarna lagras i appens beständiga datakatalog. CSV-granskning sparar inte filen och tränar inte modellen.
