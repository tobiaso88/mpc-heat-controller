# MPC Heat Controller 0.15.3

## Grundläge och aktiv PI

Appen börjar alltid utan skrivning. Om automatisk återstart är vald och PI tidigare aktiverats inväntar appen färska giltiga givarrapporter och återupptar sedan styrningen. Demo visar syntetiska data. Skuggläge beräknar PI med verkliga givare och loggar utan skrivning. Aktiv PI startas från översikten. Återstartsönskemålet sparas beständigt bara om automatisk återstart är vald.

## Förbered överlämning

1. Välj skuggläge, rumsgivare och verklig utegivare. Välj Ohmigos number-entitet som inställt värde. Giltiga min/max/step och °C krävs på entiteten.
2. Ställ in PI och absoluta signalgränser. Startvärden Kp=2 och Ki=0,1 är exempel, inte injusterade parametrar.
3. Verifiera på hårdvaran att uteblivna temperaturkommandon ger fallback till riktig utegivare och att upprepade oförändrade kommandon håller watchdog vid liv. Ange den verifierade timeouten i sekunder, minst 600. Programmet kan inte verifiera hårdvarans beteende åt dig.
4. Stäng av gamla MQTT-automationen och alla andra skrivare. Välj automationen i guiden. Appen kontrollerar att den är avstängd inför varje skrivning; andra skrivare kan inte säkert upptäckas.
5. Spara. På översikten väljer du Aktivera PI-styrning och bekräftar verklig skrivning. Att installera eller spara aktiverar aldrig PI.

Aktiv PI kontrollerar varje minut och skriver via number.set_value tidigast 300 sekunder efter föregående sändning, även när temperaturen är oförändrad. Nätverks- och beräkningstid kan förlänga intervallet. Efter ett ändrat kommando får HA-entitetens tillstånd upp till 120 sekunder på sig att återspegla det nya värdet; ett annat värde stoppar fortfarande PI direkt. Utgången avrundas till närmaste 0,5 °C och följer appens absoluta gränser och ändringshastighet. Entiteten måste stödja halva grader. Vid ett startvärde utanför dessa steg kan första sändningen vänta tills hastighetsgränsen tillåter avrundningen. Små ändringar ackumuleras tills ett helt steg ryms inom ändringsgränsen. HA:s lyckade servicesvar och entitetstillstånd är inte kvittens från pumpen.

## Stopp, fel och omstart

Stoppa PI upphör med nya kommandon. Ett redan pågående HTTP-anrop kan behöva avslutas först (timeout 15 sekunder). Ingen direkt bypass eller återgångssignal skickas: Ohmigos verifierade watchdog måste ge fallback när kommandona upphör. Stoppa appen i HA om webbgränssnittets stopp inte kan bekräftas. Återaktivera inte gamla automationen förrän nya appens skrivning stoppats.

Vid ogiltiga eller äldre än 24 timmar rapporterade reglergivare, HA-fel, loggningsfel, återaktiverad gammal automation, tekniska reglerinställningar eller oväntat utgångsvärde stoppas fortsatt skrivning och ny aktivering krävs. Komfortmål och följartermostater kan däremot uppdateras live enligt avsnittet nedan. Låsta PI-fel skapar också en beständig notis i Home Assistant; ett notisfel påverkar inte watchdog eller stoppet. Automatisk återstart kan väljas enligt avsnittet nedan. Väderfel påverkar inte PI, som använder verklig utegivare. Utgångens inställda värde kan vara oförändrat länge; dess färska avläsning från HA används vid överlämning men visar inte om pumpen är i fallback.

## PI-beräkning

Fel = börvärde minus rumsgivarnas medeltemperatur. P = Kp × fel. I ökar med Ki × fel × timmar. Utgång = verklig utetemperatur minus P och I. Positivt värmebehov ger lägre simulerad utetemperatur. Kompensationsgräns, absoluta signalgränser och ändringshastighet tillämpas. Absoluta gränser prioriteras om verklig utetemperatur ligger utanför dem. Integrering fryses när den skulle förstärka en begränsning; I begränsas även separat.

I nollställs vid datafel, omstart, relevanta inställningsändringar, demo eller beräkningsuppehåll över 15 minuter. Vid aktivering startar PI från giltigt avläst Ohmigo-värde inom gränserna och ändrar därefter gradvis. Regulatorn är inte en hårdvarusäkerhetsfunktion och behöver verifieras på installationen innan obevakad drift.

## Loggning och modeller

Mätvärden och konfiguration sparas i /data/measurements.sqlite, PI-resultat i tabellen pi_samples och MPC-skuggförslag i mpc_samples, med 90 dagars retention. Insamling sker cirka var femte minut i skuggläge och varje minut vid aktiv PI, även när UI är stängt. Kommandostatus visas separat. Uppföljningsrum ingår inte i temperaturmedelvärdet.

Väderprognos hämtas var 30:e minut från vald HA-entitet med hourly via weather.get_forecasts. Hämtningstid är inte leverantörens publiceringstid. Översikten visar separata diagram för prognostiserad utomhustemperatur, molntäckning och eventuell solinstrålning under kommande dygn, med varsin enhet och tydligt besked om fält saknas.

Appens mätlogg aggregeras automatiskt till timmedel för valda reglergivare, utegivaren och Ohmigos faktiska inställda värde. Efter minst 250 kompletta timmar tränas och valideras husmodellen automatiskt, därefter högst en gång per dygn. Givarval måste vara oförändrade inom perioden. Automatisk status och modell sparas i /data/model.sqlite.

En godkänd automatisk modell används med aktuell timprognos för ett 24-timmars MPC-förslag i skuggläge. Förslaget och inomhusprognosen visas i gränssnittet men skickas aldrig till värmepumpen. Aktiv styrning fortsätter att använda PI.

Historikvyn kan dessutom granska importerad CSV och jämföra enkel modell med fördröjningsmodell. Senaste lyckade CSV, givarval och resultat sparas separat i /data/model.sqlite. Alla modeller och horisonter använder gemensamma 24-timmarsfönster med sex timmars förhistorik. Träning använder första 70 procenten av kompletta timmar; senare data används för validering. Timmedel är aritmetiska, luckor fylls inte. Fördröjningsmodellen har fast ridge=0,01.

Historiska framtida väder- och styrvärden används i offlineutvärderingen, inte historiska väderprognoser. Detta är en kandidatkontroll och inte ett oberoende fälttest av MPC-styrning. Egna HA-entiteter finns via MQTT Discovery enligt nedan.

## Automatisk återstart i 0.7.0

Kryssa i automatisk återstart i Förbered aktiv PI, spara och aktivera PI en gång. Att kryssa i eller spara startar aldrig styrningen på egen hand. Inställningen är av som standard vid uppgradering.

Efter omstart eller tillfälligt avbrott krävs tillgängliga och giltiga värden från alla reglergivare, utegivaren och vald framledning/retur. Rapporttiden får vara högst 24 timmar gammal och värdet får inte vara markerat som återställt. En ny rapport efter appens omstart krävs inte; oförändrade temperaturer accepteras. HA:s last_reported används om den finns, annars last_updated. Givarna måste vara giltiga i två kontroller med minst 60 sekunders mellanrum. Under hela väntan skickas inga kommandon. Ohmigo måste ha ett tillgängligt numeriskt tillstånd med rätt metadata, den gamla automationen måste vara avstängd och watchdogvillkoren uppfyllda. Uppföljningsrum och väderprognos behövs inte för PI och blockerar inte start. HA:s entitetstillstånd är inte ett oberoende bevis på pumpens eller MQTT-brokerns fysiska tillgänglighet. Watchdog behövs fortfarande.

Tillfälliga givar-, kommunikations- och loggningsfel pausar och kan återupptas. Oväntad utgångsändring och konfigurationskonflikt vid skrivning kräver manuell aktivering. Manuellt stopp och tekniska inställningsändringar raderar återstartsönskemålet, även över omstart; rena komfort- och termostatval gör det inte. Stoppa därför appen via PI-stoppknappen om du vill att den ska förbli avstängd efter en senare appstart.

Regulatorn återställs och startar mjukt från tillgängligt Ohmigo-värde vid återstart. Inga gamla beräknade kommandon spelas upp.

## Egna HA-entiteter i 0.8.0

Kräver HA:s MQTT-integration ansluten till broker och MQTT Discovery med standardprefixet homeassistant. Appen använder HA-tjänsten mqtt.publish via Supervisor; inga ytterligare MQTT-lösenord behövs i appen. Efter uppdatering hittar du enheten MPC Heat Controller under Inställningar → Enheter och tjänster → MQTT. HA bestämmer slutliga entity_id utifrån namn och eventuella namnkonflikter.

Sjutton sensorer skapas: PI-status, termostatsynkning, Grundläge, Börvärde, Medeltemperatur, verklig utetemperatur, Ohmigo inställd temperatur, värmepumpens avlästa utetemperatur, framledning, retur, PI föreslagen utetemperatur, Senast skickad utetemperatur, Senaste temperaturkommando, Temperaturfel, PI P-del, PI I-del och PI utetemperaturkompensation. Samtliga är endast avläsningsbara; ändra börvärdet i appen. Statusvärden för PI är active, waiting, stopped och error. Termostatsynkning visar off, ok eller warning. En förklarande message och updated_at finns som attribut.

## Gemensamt börvärde för rumstermostater i 0.11.0

Under Din komfort kan du välja noll eller flera `climate`-entiteter som ska följa appens börvärde. Inget väljs automatiskt. I skuggläge kontrollerar appen deras `temperature`-börvärde varje minut och använder `climate.set_temperature` när ett valt värde avviker. Synkningen fortsätter även om PI-styrningen är stoppad, eftersom valet är en separat uttrycklig behörighet. Demoläge skriver aldrig till termostater.

Synkningen är enkelriktad: appens börvärde är master. En manuell ändring på en vald termostat skrivs därför tillbaka till appens värde vid nästa kontroll. Ändringar av börvärde, komfortintervall och valda följartermostater kan sparas utan att aktiv PI stoppas; I-delen nollställs när komfortmålet ändras. Andra tekniska inställningsändringar kräver fortfarande ny aktivering. Värmepumpens egen climate-entitet bör lämnas omarkerad om den ska fortsätta reglera självständigt. En otillgänglig termostat eller misslyckad skrivning visas som warning men stoppar inte PI:s separata Ohmigo-styrning.

Publiceringen kör i egen tråd ungefär varje minut. Discovery-konfiguration behålls på brokern och återannonseras var femte minut. Tillstånd behålls inte på brokern. Utan nya MQTT-publiceringar blir sensorerna otillgängliga efter 180 sekunder. Beräkningsunderlag äldre än 420 sekunder, eller från en annan konfiguration, publiceras som otillgängligt. I skuggläge uppdateras mätdata fortfarande var femte minut.

Senast skickat värde är ett historiskt kommando till HA, inte kvittens från Ohmigo eller pumpen. Det behålls vid stopp inom samma appkörning men är otillgängligt efter omstart tills nästa kommando skickats. PI-demovärden publiceras inte. Enhetsidentiteten sparas i /data/entities.sqlite; radera inte filen om du vill behålla samma entiteter.

Ett fel i publiceringen visas i webbgränssnittet och påverkar inte PI-loopen. Rapport om lyckad publicering betyder att HA accepterade MQTT-anropet, inte att appen kontrollerat entitetsregistret. Verifiera att enheten syns i HA efter första installationen.

## Grafer och mobilgränssnitt i 0.9.5

PI-status och start/stopp finns högst på översikten efter temperaturkorten. Beräkningsdetaljer, anslutningsstatus och den experimentella inomhusprognosen kan fällas ut. Inställningarnas rumsgivare väljs med sökbara kryssrutor.

Vädergrafen visar prognostemperaturer för kommande 24 timmar. Timtabellen finns kvar under Visa timprognos. Historiska grafer hämtar de senaste 48 timmarna ur appens mätlogg: inomhusmedel och börvärde i en graf, utegivare, Ohmigos inställda värde och PI-förslag i en annan. Sista loggade värdet i varje tiominutersintervall visas. Luckor över 20 minuter, ogiltiga värden och ändrade givarval bryter linjerna. Ett ensamt värde visas som en punkt. Loggning måste vara igång för historiska grafer; importerad modell-CSV används inte där.

På mobil kan grafer rullas i sidled för läsbara tidsaxlar. Tabeller visas som märkta kort. Ohmigos inställda värde är fortfarande inte en kvittens från värmepumpen.

Välj vid behov **Värmepumpens avlästa utetemperatur (valfri)** i Inställningar. Det är pumpens uppfattade temperatur efter Ohmigo, inte verklig utetemperatur. Givaren används endast för uppföljning och loggning; bortfall stoppar inte PI. Historik för givaren samlas från att den valts.

## Sol i skuggförslaget och modellgränser

Appen läser faktisk `weather.get_forecasts`-respons för vald timväderentitet. `temperature` krävs. `cloud_coverage` (%) och det leverantörsspecifika `solar_irradiance` (W/m²) tas med endast när de finns och är giltiga. Gränssnittet visar tillgängliga fält och markerar avsaknad av solinstrålningsprognos. Home Assistants generella prognosformat dokumenterar `cloud_coverage`, men garanterar inte solinstrålning. Vald väderentitets verkliga stöd måste därför kontrolleras i appen; källan kan inte identifieras från denna kodbas.

Om du redan har solpaneler kan du i installationsguiden välja växelriktarens **momentana solcellsproduktion** (W eller kW) och motsvarande **Forecast.Solar-källa**. Installerade Forecast.Solar-källor visas i appen även innan Energipanelen är klar; välj källa eller tryck Uppdatera prognoskällor. För att läsa timprognosen måste den valda källan kopplas till solproduktionen i Home Assistants Energipanel. Appen läser då Energipanelens timvärden (`energy/solar_forecast`, Wh per timme) och omvandlar dem till genomsnittlig effekt i W. En totalsensor i kWh eller nätets exporteffekt är inte rätt historiskt underlag. Saknade timmar fylls inte med uppskattningar; en nollpunkt vid lokal midnatt som Forecast.Solar utelämnar återställs bara om timmarna på båda sidor finns. Utan 24 matchande timvärden används den validerade temperaturmodellen i skuggläge om den klarat kvalitetskontrollen; annars visas inget MPC-förslag.

Solcellsproduktionen är en indirekt solsignal: panelernas lutning, orientering, skuggning och växelriktarens begränsning kan skilja sig från husets solvärme. Solcellsmodellen kräver minst 250 kompletta historiska timmar, minst 24 testfönster vid 6/12/24 timmar, MAE högst 0,8 °C och minst 15 % bättre än temperaturpersistens. Den skalade effekten av produktionen måste vara positiv men högst 0,001 °C/h per W; felvänd eller orimlig solcellsmodell stoppas. Arkivet sparar den prognos som fanns vid beslutet och jämför senare med uppmätt produktion inom samma 90-dagars retention. Detta är utvärdering av skuggförslag, inte bevis för effekten av alternativa MPC-kommandon.

Ingen separat solsensor krävs. Appen kan automatiskt logga aktuell molnighet från vald väderentitet och lära dess samband med uppmätt innetemperatur. Om både denna historik och 24 timmars molnprognos finns och modellen valideras används molnmodellen. Annars används den temperaturbaserade modellen. Den som redan har en W/m²-sensor eller en separat %-sensor kan fortfarande välja den manuellt; då måste motsvarande prognosfält finnas. Minst 250 kompletta timvärden krävs för varje modell.

`ready` kräver minst 24 valideringsfönster samt MAE högst 0,8 °C vid 6, 12 och 24 timmar och minst 15 % förbättring mot att hålla aktuell rumstemperatur konstant. Gränserna är en konservativ spärr mot svaga modeller, inte en garanti för driftprestanda. Valideringen använder **verkligt framtida väder** och historisk styrsignal, vilket ger modellen bättre väderinformation än den hade haft vid beslut. Några arkiverade `weather.get_forecasts`-svar från beslutstillfällena finns ännu inte; utvärdering mot **då tillgängliga prognoser** kan därför inte rapporteras eller användas som bevis för prognoskvalitet. Överlappande fönster är dessutom beroende.

Planen kräver aktuell bekräftad `number`-utgång med gränser och steg som stöder 0,5 °C. Den begränsas till både entitetens och appens signalgränser, appens timsteg och PI:s ändringstakt per timme. Planen är fortfarande enbart visning; endast PI har skrivväg till Home Assistant och dess watchdogkrav gäller oförändrat.

## Arkiverad utvärdering av skuggförslag

Varje beräknat MPC-skuggförslag sparas i `mpc_forecasts` med beslutstid, vald väderentitet, tid då prognosen hämtades, fältnamn, de 24 prognostimmarna som användes, hela föreslagna signal- och temperaturbanan samt givarval. Tabellen rensas efter 90 dagar, liksom övrig mätlogg. `GET /api/mpc/evaluation` och knappen i historikvyn jämför de senaste mogna förslagen med giltiga mätvärden inom 30 minuter från respektive prognostimme. Saknade mätningar fylls inte i. Prognos för uteväder (och valt solfält) jämförs vid sin prognostid; MPC:s inomhusprognos jämförs vid den tid som planen anger. Gränssnittet visar senaste jämförelsen; API:et returnerar upp till 24 mogna förslag med timrader och medelfel.

Detta är **utvärdering av skuggförslag**. Inomhustemperaturen som senare mäts upp påverkades av verklig PI-styrning och faktiskt väder. Jämförelsen kan visa prognosfel under den körningen, men inte hur huset skulle ha reagerat på andra MPC-kommandon. Den bevisar därför inte att MPC-styrning skulle förbättra komfort eller energianvändning.

En modell får inte `ready` om den skalade signalkoefficienten (`koefficient / skala`) inte ligger mellan −0,5 och −0,001 °C inomhus per timme för 1 °C högre simulerad utetemperatur. Negativt tecken är nödvändigt eftersom högre signal betyder mindre värme i denna installation. Gränserna stoppar både felvänd och orimligt stark eller försumbar effekt. Detta är en plausibilitetsspärr, inte ett kausalt bevis på signalens effekt.

## Sol utan extra givare

Välj inne- och utegivare, Ohmigos avlästa inställda temperatur och en timväderentitet. Appen loggar automatiskt `cloud_coverage` från väderentitetens **aktuella tillstånd** när attributet finns. Efter minst 250 kompletta timmar kan en molnmodell tränas mot uppmätt rumstemperatur och historisk signal; samma väderentitets molnprognos används då i skuggplanen. En temperaturbaserad modell tränas också och används när molnprognosen saknas eller molnmodellen inte valideras. Val av separat sol- eller molnsensor är frivilligt och avsett för redan installerade sensorer.

Temperaturmätningarna visar hur huset faktiskt reagerar på värme, utetemperatur och tidigare solpåverkan. Utan en framtida sol- eller molnprognos kan modellen inte veta om ett ännu inte märkbart molntäcke kommer att ändras. Den kan då förutse utifrån uteprognosen och kända husförhållanden, men inte hävda att den förutser en plötslig solig period innan temperaturen påverkas. Ingen modell görs redo enbart för att aktuellt väderattribut finns; samma historiska storhet måste ha loggats och modellen måste klara kvalitetskontrollen.
