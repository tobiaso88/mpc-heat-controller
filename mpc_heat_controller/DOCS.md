# MPC Heat Controller

Förhandsversion 0.4.0. Öppna webbgränssnittet via Home Assistant.

Installationsguiden låter dig välja temperaturgivare och komfortmål. I skuggläge läser appen valda givare via Home Assistants interna API. Ingen separat token behövs.

Demo använder ett syntetiskt exempelhus och exempelväder. Den separata vädertabellen visar verklig timprognos från vald väderentitet, exempelvis Met.no. Ingen väderdata blandas in i den syntetiska inomhusprognosen.

Skuggläget visar mätvärden och loggar dem var femte minut i `/data/measurements.sqlite`, tillsammans med konfigurationen. Loggningen fungerar även med stängt webbgränssnitt. Rader äldre än 90 dagar rensas. Saknade eller gamla värden märks i loggen, inte fylls i. Rum valda för uppföljning ingår inte i regleringens medeltemperatur.

Väder hämtas var 30:e minut via `weather.get_forecasts` med typen `hourly`. Vid fel försöker appen igen vid nästa insamling. Tidpunkten i UI anger när appen hämtade prognosen, inte när leverantören skapade den. Enheter utan timprognos ger ett felmeddelande. Givare som inte rapporterat på två timmar markeras som gamla; gränsen är tills vidare fast.

Egna HA-entiteter och en kalibrerad MPC återstår. Appen skickar inga kommandon till värmepumpen.

Inställningarna lagras i appens beständiga datakatalog. CSV-granskning sparar inte filen och tränar inte modellen.

## Offlineutvärdering av husmodell

Granska CSV under Historik, välj därefter rumsgivare, verklig utetemperatur och historisk beräknad styrsignal. Välj en gemensam vinterperiod (datum i UTC) och klicka Anpassa och validera offline. Minst 240 kompletta timmar krävs; längre perioder behövs för meningsfull bedömning.

Modellen beskriver nästa timmes temperaturförändring som en linjär funktion av inne-, ute- och beräknad utetemperatur. Timmedel räknas från tillgängliga rader. Det är inte tidsvägda medel av tillståndsändringar; välj helst en enhetlig period med timstatistik. Inga luckor fylls. Träningsperioden utgör de första 70 procenten av kompletta timmar och valideringen de sista 30 procenten. Prognoserna rullas fram rekursivt utan framtida rumstemperaturer som indata.

Valideringen använder däremot kända framtida historiska utetemperaturer och styrsignaler. Den prövar inte prognosfel i vädertjänsten eller alternativ MPC-styrning. Testfönstren överlappar och är inte oberoende försök. Styrsignalens historiska begränsningar och pumpens interna reglering är ännu inte identifierade. Därför aktiveras ingen modell automatiskt. Senaste lyckade resultat, givarval och CSV sparas i /data/model.sqlite och återställs vid omladdning och omstart. En ny lyckad utvärdering ersätter den tidigare. Misslyckad utvärdering behåller tidigare sparat underlag.

## Modelljämförelse i 0.4.0

Den enkla modellen jämförs med en kandidat som även använder temperaturförändringar under de senaste två timmarna och styrsignalens senaste sex timmar. Alla modeller tränas på samma rader. Alla fyra horisonter utvärderas från samma starttider med kompletta 24-timmarsfönster och sex timmars förhistorik. Därför kan antalet testfönster och felvärden skilja sig från 0.3.0. Kandidaten använder standardiserade variabler och en fast ridge-regularisering på 0,01, utan anpassning mot valideringsdata. Ingen kandidat aktiveras automatiskt.

## Ohmigos inställda värde

Välj sensor eller number-entitet i installationsguiden. Appen läser och loggar den på samma sätt som temperaturgivarna. Ett inställt värde är inte en kvittens på vad pumpen läst och visar inte säkert watchdogens fallback. Värdet påverkar inte regleringens rumstemperaturmedelvärde.
