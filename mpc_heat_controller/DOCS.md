# MPC Heat Controller 0.8.0

## Grundläge och aktiv PI

Appen börjar alltid utan skrivning. Om automatisk återstart är vald och PI tidigare aktiverats inväntar appen färska giltiga givarrapporter och återupptar sedan styrningen. Demo visar syntetiska data. Skuggläge beräknar PI med verkliga givare och loggar utan skrivning. Aktiv PI startas från översikten. Återstartsönskemålet sparas beständigt bara om automatisk återstart är vald.

## Förbered överlämning

1. Välj skuggläge, rumsgivare och verklig utegivare. Välj Ohmigos number-entitet som inställt värde. Giltiga min/max/step och °C krävs på entiteten.
2. Ställ in PI och absoluta signalgränser. Startvärden Kp=2 och Ki=0,1 är exempel, inte injusterade parametrar.
3. Verifiera på hårdvaran att uteblivna temperaturkommandon ger fallback till riktig utegivare och att upprepade oförändrade kommandon håller watchdog vid liv. Ange den verifierade timeouten i sekunder, minst 180. Programmet kan inte verifiera hårdvarans beteende åt dig.
4. Stäng av gamla MQTT-automationen och alla andra skrivare. Välj automationen i guiden. Appen kontrollerar att den är avstängd inför varje skrivning; andra skrivare kan inte säkert upptäckas.
5. Spara. På översikten väljer du Aktivera PI-styrning och bekräftar verklig skrivning. Att installera eller spara aktiverar aldrig PI.

Aktiv PI skriver via number.set_value ungefär varje minut plus nätverks- och beräkningstid, även när temperaturen är oförändrad. Utgången följer entitetens steg samt appens absoluta gränser och ändringshastighet. Små ändringar ackumuleras tills ett helt steg ryms inom ändringsgränsen. HA:s lyckade servicesvar är inte kvittens från pumpen.

## Stopp, fel och omstart

Stoppa PI upphör med nya kommandon. Ett redan pågående HTTP-anrop kan behöva avslutas först (timeout 15 sekunder). Ingen direkt bypass eller återgångssignal skickas: Ohmigos verifierade watchdog måste ge fallback när kommandona upphör. Stoppa appen i HA om webbgränssnittets stopp inte kan bekräftas. Återaktivera inte gamla automationen förrän nya appens skrivning stoppats.

Vid ogiltiga eller äldre än två timmar rapporterade reglergivare, HA-fel, loggningsfel, återaktiverad gammal automation, ändrade inställningar eller oväntat utgångsvärde stoppas fortsatt skrivning och ny aktivering krävs. Automatisk återstart kan väljas enligt avsnittet nedan. Väderfel påverkar inte PI, som använder verklig utegivare. Utgångens inställda värde kan vara oförändrat länge; dess färska avläsning från HA används vid överlämning men visar inte om pumpen är i fallback.

## PI-beräkning

Fel = börvärde minus rumsgivarnas medeltemperatur. P = Kp × fel. I ökar med Ki × fel × timmar. Utgång = verklig utetemperatur minus P och I. Positivt värmebehov ger lägre simulerad utetemperatur. Kompensationsgräns, absoluta signalgränser och ändringshastighet tillämpas. Absoluta gränser prioriteras om verklig utetemperatur ligger utanför dem. Integrering fryses när den skulle förstärka en begränsning; I begränsas även separat.

I nollställs vid datafel, omstart, relevanta inställningsändringar, demo eller beräkningsuppehåll över 15 minuter. Vid aktivering startar PI från giltigt avläst Ohmigo-värde inom gränserna och ändrar därefter gradvis. Regulatorn är inte en hårdvarusäkerhetsfunktion och behöver verifieras på installationen innan obevakad drift.

## Loggning och modeller

Mätvärden och konfiguration sparas i /data/measurements.sqlite, PI-resultat i tabellen pi_samples, med 90 dagars retention. Insamling sker cirka var femte minut i skuggläge och varje minut vid aktiv PI, även när UI är stängt. Kommandostatus visas separat. Uppföljningsrum ingår inte i temperaturmedelvärdet.

Väderprognos hämtas var 30:e minut från vald HA-entitet med hourly via weather.get_forecasts. Hämtningstid är inte leverantörens publiceringstid.

Historikvyn kan granska CSV och jämföra enkel modell med fördröjningsmodell. Senaste lyckade CSV, givarval och resultat sparas i /data/model.sqlite. Alla modeller och horisonter använder gemensamma 24-timmarsfönster med sex timmars förhistorik. Träning använder första 70 procenten av kompletta timmar; senare data används för validering. Timmedel är aritmetiska, luckor fylls inte. Fördröjningsmodellen har fast ridge=0,01.

Historiska framtida väder- och styrvärden används i offlineutvärderingen, inte historiska väderprognoser eller alternativa MPC-kommandon. Ingen MPC-modell aktiveras automatiskt. Egna HA-entiteter finns via MQTT Discovery enligt nedan.

## Automatisk återstart i 0.7.0

Kryssa i automatisk återstart i Förbered aktiv PI, spara och aktivera PI en gång. Att kryssa i eller spara startar aldrig styrningen på egen hand. Inställningen är av som standard vid uppgradering.

Efter omstart eller tillfälligt avbrott krävs nya rapporter från alla reglergivare, utegivaren och vald framledning/retur, rapporterade efter omstarten eller avbrottet. Givarna måste vara giltiga i två kontroller med minst 60 sekunders mellanrum. Under hela väntan skickas inga kommandon. Ohmigo måste ha ett tillgängligt numeriskt tillstånd med rätt metadata, den gamla automationen måste vara avstängd och watchdogvillkoren uppfyllda. Uppföljningsrum och väderprognos behövs inte för PI och blockerar inte start. HA:s entitetstillstånd är inte ett oberoende bevis på pumpens eller MQTT-brokerns fysiska tillgänglighet. Watchdog behövs fortfarande.

Tillfälliga givar-, kommunikations- och loggningsfel pausar och kan återupptas. Oväntad utgångsändring och konfigurationskonflikt vid skrivning kräver manuell aktivering. Manuellt stopp och sparade inställningar raderar återstartsönskemålet, även över omstart. Stoppa därför appen via PI-stoppknappen om du vill att den ska förbli avstängd efter en senare appstart.

Regulatorn återställs och startar mjukt från tillgängligt Ohmigo-värde vid återstart. Inga gamla beräknade kommandon spelas upp.

## Egna HA-entiteter i 0.8.0

Kräver HA:s MQTT-integration ansluten till broker och MQTT Discovery med standardprefixet homeassistant. Appen använder HA-tjänsten mqtt.publish via Supervisor; inga ytterligare MQTT-lösenord behövs i appen. Efter uppdatering hittar du enheten MPC Heat Controller under Inställningar → Enheter och tjänster → MQTT. HA bestämmer slutliga entity_id utifrån namn och eventuella namnkonflikter.

Elva sensorer skapas: PI-status, Grundläge, Börvärde, Medeltemperatur, PI föreslagen utetemperatur, Senast skickad utetemperatur, Senaste temperaturkommando, Temperaturfel, PI P-del, PI I-del och PI utetemperaturkompensation. Samtliga är endast avläsningsbara; ändra börvärdet i appen. Statusvärden är active, waiting, stopped och error. En förklarande message och updated_at finns som attribut.

Publiceringen kör i egen tråd ungefär varje minut. Discovery-konfiguration behålls på brokern och återannonseras var femte minut. Tillstånd behålls inte på brokern. Utan nya MQTT-publiceringar blir sensorerna otillgängliga efter 180 sekunder. Beräkningsunderlag äldre än 420 sekunder, eller från en annan konfiguration, publiceras som otillgängligt. I skuggläge uppdateras mätdata fortfarande var femte minut.

Senast skickat värde är ett historiskt kommando till HA, inte kvittens från Ohmigo eller pumpen. Det behålls vid stopp inom samma appkörning men är otillgängligt efter omstart tills nästa kommando skickats. PI-demovärden publiceras inte. Enhetsidentiteten sparas i /data/entities.sqlite; radera inte filen om du vill behålla samma entiteter.

Ett fel i publiceringen visas i webbgränssnittet och påverkar inte PI-loopen. Rapport om lyckad publicering betyder att HA accepterade MQTT-anropet, inte att appen kontrollerat entitetsregistret. Verifiera att enheten syns i HA efter första installationen.
