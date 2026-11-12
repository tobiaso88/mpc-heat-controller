# MPC Heat Controller 0.19.1

## Grundläge och aktiv PI

Appen börjar alltid utan skrivning. Om automatisk återstart är vald och PI tidigare aktiverats inväntar appen färska giltiga givarrapporter och återupptar sedan styrningen. Demo visar syntetiska data. Skuggläge beräknar PI med verkliga givare och loggar utan skrivning. Aktiv PI startas från översikten. Återstartsönskemålet sparas beständigt bara om automatisk återstart är vald.

## Förbered överlämning

1. Välj skuggläge, rumsgivare och verklig utegivare. Välj Ohmigos number-entitet som inställt värde. Giltiga min/max/step och °C krävs på entiteten.
2. Ställ in PI och absoluta signalgränser. Startvärden Kp=2 och Ki=0,1 är exempel, inte injusterade parametrar.
3. Verifiera på hårdvaran att uteblivna temperaturkommandon ger fallback till riktig utegivare och att upprepade oförändrade kommandon håller watchdog vid liv. Ange den verifierade timeouten i sekunder, minst 600. Programmet kan inte verifiera hårdvarans beteende åt dig.
4. Stäng av gamla MQTT-automationen och alla andra skrivare. Välj automationen i guiden. Appen kontrollerar att den är avstängd inför varje skrivning; andra skrivare kan inte säkert upptäckas.
5. Spara. På Hem väljer du Starta aktiv styrning och bekräftar verklig skrivning; bekräftelsen är spärrad tills förberedelserna i Inställningar → Aktiv styrning är uppfyllda. Att installera eller spara aktiverar aldrig PI.

Aktiv PI kontrollerar varje minut och skriver via number.set_value tidigast 300 sekunder efter föregående sändning, även när temperaturen är oförändrad. Nätverks- och beräkningstid kan förlänga intervallet. Efter ett ändrat kommando får HA-entitetens tillstånd upp till 120 sekunder på sig att återspegla det nya värdet; ett annat värde stoppar fortfarande PI direkt. Utgången avrundas till närmaste 0,5 °C och följer appens absoluta gränser och ändringshastighet. Entiteten måste stödja halva grader. Vid ett startvärde utanför dessa steg kan första sändningen vänta tills hastighetsgränsen tillåter avrundningen. Små ändringar ackumuleras tills ett helt steg ryms inom ändringsgränsen. Större ändringar tas i halvgradssteg i takt med *Maximal signaländring* (förval 2 °C/h, alltså 0,5 °C per kvart) i stället för att hållas tillbaka och sedan skickas i ett hopp. En lång period med oförändrat värde ger inte utrymme för mer än ett steg åt gången; vid högre ändringstakt motsvarar utrymmet ett sändningsintervall på fem minuter. HA:s lyckade servicesvar och entitetstillstånd är inte kvittens från pumpen.

## Stopp, fel och omstart

Stoppa PI upphör med nya kommandon. Ett redan pågående HTTP-anrop kan behöva avslutas först (timeout 15 sekunder). Ingen direkt bypass eller återgångssignal skickas: Ohmigos verifierade watchdog måste ge fallback när kommandona upphör. Stoppa appen i HA om webbgränssnittets stopp inte kan bekräftas. Återaktivera inte gamla automationen förrän nya appens skrivning stoppats.

Vid ogiltiga eller äldre än 24 timmar rapporterade reglergivare, HA-fel, loggningsfel, återaktiverad gammal automation, tekniska reglerinställningar eller oväntat utgångsvärde stoppas fortsatt skrivning och ny aktivering krävs. Komfortmål och följartermostater kan däremot uppdateras live enligt avsnittet nedan. Låsta PI-fel skapar också en beständig notis i Home Assistant; ett notisfel påverkar inte watchdog eller stoppet. Automatisk återstart kan väljas enligt avsnittet nedan. Väderfel påverkar inte PI, som använder verklig utegivare. Utgångens inställda värde kan vara oförändrat länge; dess färska avläsning från HA används vid överlämning men visar inte om pumpen är i fallback.

## PI-beräkning

Fel = börvärde minus rumsgivarnas medeltemperatur. P = Kp × fel. I ökar med Ki × fel × timmar. Utgång = verklig utetemperatur minus P och I. Positivt värmebehov ger lägre simulerad utetemperatur. Kompensationsgräns, absoluta signalgränser och ändringshastighet tillämpas. Absoluta gränser prioriteras om verklig utetemperatur ligger utanför dem. Integrering fryses när den skulle förstärka en begränsning; I begränsas även separat.

I-delen beskriver husets bestående avvikelse från värmekurvan och behålls därför när börvärdet eller komfortområdet ändras. Under aktiv styrning sparas den varje minut i `/data/control.sqlite`. Vid start, automatisk återstart och efter en paus återanvänds det sparade värdet om det är högst 24 timmar gammalt och PI-inställningar, givarval, utgång och signalgränser är oförändrade; annars börjar I från noll. I nollställs fortfarande vid ändrade PI-inställningar, demo och beräkningsuppehåll över 15 minuter. I skuggläge utan aktiv styrning sparas inget värde. Vid aktivering startar PI från giltigt avläst Ohmigo-värde inom gränserna och ändrar därefter gradvis. Regulatorn är inte en hårdvarusäkerhetsfunktion och behöver verifieras på installationen innan obevakad drift.

## Loggning och modeller

Mätvärden och konfiguration sparas i /data/measurements.sqlite, PI-resultat i tabellen pi_samples och MPC-skuggförslag i mpc_samples, med 90 dagars retention. Insamling sker cirka var femte minut i skuggläge och varje minut vid aktiv PI, även när UI är stängt. Kommandostatus visas separat. Uppföljningsrum ingår inte i temperaturmedelvärdet.

Väderprognos hämtas var 30:e minut från vald HA-entitet med hourly via weather.get_forecasts. Hämtningstid är inte leverantörens publiceringstid. Översikten visar separata diagram för prognostiserad utomhustemperatur, molntäckning och eventuell solinstrålning under kommande dygn, med varsin enhet och tydligt besked om fält saknas.

Appens mätlogg aggregeras automatiskt till timmedel för valda reglergivare, utegivaren och Ohmigos faktiska inställda värde. Efter minst 250 kompletta timmar tränas och valideras husmodellen automatiskt. En strikt godkänd modell prövas mot en nytränad högst en gång per dygn och behålls tills den nya är tydligt bättre (se *Testsignal och behållen modell*); underkända modeller kontrolleras högst en gång per timme. Minst 168 kompletta timpar måste finnas under de senaste 14 dygnen. Givarval måste vara oförändrade inom perioden. Automatisk status och modell sparas i /data/model.sqlite.

En godkänd automatisk modell används med aktuell timprognos för ett 24-timmars MPC-förslag i skuggläge. Förslaget och inomhusprognosen visas i gränssnittet men skickas aldrig till värmepumpen. Aktiv styrning fortsätter att använda PI.

Vyn Lärande kan dessutom granska importerad CSV och jämföra enkel modell med fördröjningsmodell. Senaste lyckade CSV, givarval och resultat sparas separat i /data/model.sqlite. Alla modeller och horisonter använder gemensamma 24-timmarsfönster med sex timmars förhistorik. Valideringen använder kompletta 24-timmarsfönster under senaste veckan. Varje UTC-testdygn tränas separat på högst 14 tidigare dygn, med minst 168 kompletta timpar; bara utfall strikt före testdygnet används. Därefter tränas driftmodellerna separat på de senaste 14 dygnen. Felmåtten gäller den dagliga träningsmetoden, inte en efterhandsutvärdering av slutmodellens egna träningsdata. Fönstren överlappar och är inte oberoende försök. Timmedel är aritmetiska, luckor fylls inte. Fördröjningsmodellen har fast ridge=0,01.

Historiska framtida väder- och styrvärden används i offlineutvärderingen, inte historiska väderprognoser. Detta är en kandidatkontroll och inte ett oberoende fälttest av MPC-styrning. Egna HA-entiteter finns via MQTT Discovery enligt nedan.

## Automatisk återstart i 0.7.0

Kryssa i automatisk återstart under Inställningar → Aktiv styrning, spara och aktivera PI en gång. Att kryssa i eller spara startar aldrig styrningen på egen hand. Inställningen är av som standard vid uppgradering.

Efter omstart eller tillfälligt avbrott krävs tillgängliga och giltiga värden från alla reglergivare, utegivaren och vald framledning/retur. Rapporttiden får vara högst 24 timmar gammal och värdet får inte vara markerat som återställt. En ny rapport efter appens omstart krävs inte; oförändrade temperaturer accepteras. HA:s last_reported används om den finns, annars last_updated. Givarna måste vara giltiga i två kontroller med minst 60 sekunders mellanrum. Under hela väntan skickas inga kommandon. Ohmigo måste ha ett tillgängligt numeriskt tillstånd med rätt metadata, den gamla automationen måste vara avstängd och watchdogvillkoren uppfyllda. Uppföljningsrum och väderprognos behövs inte för PI och blockerar inte start. HA:s entitetstillstånd är inte ett oberoende bevis på pumpens eller MQTT-brokerns fysiska tillgänglighet. Watchdog behövs fortfarande.

Tillfälliga givar-, kommunikations- och loggningsfel pausar och kan återupptas. Oväntad utgångsändring och konfigurationskonflikt vid skrivning kräver manuell aktivering. Manuellt stopp och tekniska inställningsändringar raderar återstartsönskemålet, även över omstart; rena komfort- och termostatval gör det inte. Stoppa därför appen via PI-stoppknappen om du vill att den ska förbli avstängd efter en senare appstart.

Regulatorn startar mjukt från tillgängligt Ohmigo-värde vid återstart och återanvänder en sparad I-del enligt avsnittet PI-beräkning. Inga gamla beräknade kommandon spelas upp.

## Egna HA-entiteter i 0.8.0

Kräver HA:s MQTT-integration ansluten till broker och MQTT Discovery med standardprefixet homeassistant. Appen använder HA-tjänsten mqtt.publish via Supervisor; inga ytterligare MQTT-lösenord behövs i appen. Efter uppdatering hittar du enheten MPC Heat Controller under Inställningar → Enheter och tjänster → MQTT. HA bestämmer slutliga entity_id utifrån namn och eventuella namnkonflikter.

Sjutton sensorer skapas: PI-status, termostatsynkning, Grundläge, Börvärde, Medeltemperatur, verklig utetemperatur, Ohmigo inställd temperatur, värmepumpens avlästa utetemperatur, framledning, retur, PI föreslagen utetemperatur, Senast skickad utetemperatur, Senaste temperaturkommando, Temperaturfel, PI P-del, PI I-del och PI utetemperaturkompensation. Samtliga är endast avläsningsbara; ändra börvärdet i appen. Statusvärden för PI är active, waiting, stopped och error. Termostatsynkning visar off, ok eller warning. En förklarande message och updated_at finns som attribut.

## Gemensamt börvärde för rumstermostater i 0.11.0

Under Inställningar → Komfort kan du välja noll eller flera `climate`-entiteter som ska följa appens börvärde. Inget väljs automatiskt. I skuggläge kontrollerar appen deras `temperature`-börvärde varje minut och använder `climate.set_temperature` när ett valt värde avviker. Synkningen fortsätter även om PI-styrningen är stoppad, eftersom valet är en separat uttrycklig behörighet. Demoläge skriver aldrig till termostater.

Synkningen är enkelriktad: appens börvärde är master. En manuell ändring på en vald termostat skrivs därför tillbaka till appens värde vid nästa kontroll. Ändringar av börvärde, komfortintervall och valda följartermostater kan sparas utan att aktiv PI stoppas; I-delen behålls när komfortmålet ändras. Andra tekniska inställningsändringar kräver fortfarande ny aktivering. Värmepumpens egen climate-entitet bör lämnas omarkerad om den ska fortsätta reglera självständigt. En otillgänglig termostat eller misslyckad skrivning visas som warning men stoppar inte PI:s separata Ohmigo-styrning.

Publiceringen kör i egen tråd ungefär varje minut. Discovery-konfiguration behålls på brokern och återannonseras var femte minut. Tillstånd behålls inte på brokern. Utan nya MQTT-publiceringar blir sensorerna otillgängliga efter 180 sekunder. Beräkningsunderlag äldre än 420 sekunder, eller från en annan konfiguration, publiceras som otillgängligt. I skuggläge uppdateras mätdata fortfarande var femte minut.

Senast skickat värde är ett historiskt kommando till HA, inte kvittens från Ohmigo eller pumpen. Det behålls vid stopp inom samma appkörning men är otillgängligt efter omstart tills nästa kommando skickats. PI-demovärden publiceras inte. Enhetsidentiteten sparas i /data/entities.sqlite; radera inte filen om du vill behålla samma entiteter.

Ett fel i publiceringen visas i webbgränssnittet och påverkar inte PI-loopen. Rapport om lyckad publicering betyder att HA accepterade MQTT-anropet, inte att appen kontrollerat entitetsregistret. Verifiera att enheten syns i HA efter första installationen.

## Gränssnittet i 0.16.0

Gränssnittet har fyra vyer: **Hem**, **Väder**, **Lärande** och **Inställningar**. På mobil finns de i en flikrad längst ned. Ljust och mörkt tema följer enhetens inställning.

- **Hem** visar innetemperaturen mot komfortområdet, ett klartextbesked och önskad temperatur med −/+. Snabbjusteringen flyttar hela komfortområdet 0,5 °C och sparas efter en kort paus; den räknas som en komfortändring och stoppar inte aktiv PI. Styrkortet visar läge, senast skickat kommando samt PI- och MPC-förslag. Start öppnar en dialog med förberedelserna som checklista; Stoppa visas när styrningen är aktiv, väntar på återstart eller när status inte kan läsas.
- Statusraden sammanfattar givare, väder, solprognos, husmodell, termostatsynk, HA-sensorer och loggning. Tryck på en ruta för detaljer.
- Tidslinjen **Igår · Nu · Imorgon** visar uppmätt historik (12/24/48 h) och prognos för kommande dygn på samma tidsaxel. Streckade kurvor är prognoser eller förslag som inte skickas. Kurvor kan döljas via förklaringen.
- **Värmekedjan** visar verklig ute, Ohmigos värde, framledning/retur och inne, samt om skillnaden ger mer eller mindre värme. Under den visas PI-beräkningen steg för steg.
- **Lärande** visar modellens steg (samla data, kvalitetskontroll, skuggförslag), träffsäkerhet per horisont mot kravet 0,35 °C, jämförelse av skuggförslag, export och CSV-utvärdering.
- **Inställningar** är uppdelad i avsnitt med sökbara givarväljare som visar aktuellt värde. En spara-list visar vilka fält som ändrats och varnar om sparandet stoppar aktiv styrning.

API:et är oförändrat. Tider visas i Home Assistants tidszon och tal med decimalkomma.

## Byta utegivare och behålla historiken

När du väljer en ny verklig utetemperatur under Inställningar → Värmesystem frågar appen vad som ska hända med historiken:

- **Börja om insamlingen** (förval): husmodellen samlar en ny period på minst 250 kompletta timmar med den nya givaren. Den tidigare perioden finns kvar i loggen och kommer tillbaka om du byter tillbaka inom 90 dagar.
- **Flytta historiken till den nya givaren**: tidigare mätningar från den gamla givaren märks om som den nya i appens mätlogg. Även arkiverade skuggförslag och den sparade modellens givarval uppdateras, så att modellen och skuggförslagen fortsätter utan ny insamling.

Flytten ändrar inga värden. Mätte den gamla och den nya givaren olika, till exempel på grund av placering i sol, tränas modellen därefter på blandade mätningar. Kvalitetskontrollen gäller oförändrat och kan underkänna modellen. Varje omflyttad mätning får fältet `migrated_from`, och flytten loggas i tabellen `migrations`.

Före flytten sparas säkerhetskopior av `measurements.sqlite` och `model.sqlite` i `/data/backups/`. De tre senaste behålls. Misslyckas något steg, även att spara inställningen, återställs båda databaserna. Flytten nekas om den nya givaren redan finns i loggen under den gamla givarens period, eller om den gamla givaren även hade en annan roll. Byte av utegivare är en teknisk ändring och stoppar aktiv PI som tidigare.

API: `POST /api/outdoor/migrate` med `{"config": {...}, "confirm": true}`.

## MPC-inlärning i 0.19.0

Under Inställningar → Avancerat → *MPC-inlärning* kan MPC få styra värmen när aktiv styrning är på. Förvalet är avstängt. Att slå på eller ändra inställningen stoppar aktiv styrning som andra tekniska ändringar; starta den sedan igen på Hem.

Varje minut räknar både PI och MPC. MPC:s signal för nästa timme skickas, men aldrig mer än *Största avvikelse från PI* (förval 2 °C) från PI:s signal. PI styr ensam när:

- innetemperaturen är under komfortområdet, och därefter tills den är 0,2 °C över nedre gränsen igen (dock aldrig över börvärdet),
- ingen modell är godkänd för inlärning eller ingen plan ryms inom signalgränser och ändringstakt,
- planen är äldre än 15 minuter.

När det är varmare än komfortområdet får MPC bara ge mindre värme (högre signal) än PI. Begränsningen gäller tills temperaturen är 0,2 °C under övre gränsen igen. Marginalen hindrar att PI och MPC byter plats varje minut när temperaturen ligger vid en komfortgräns; bytet i sig följer *Maximal signaländring* i halvgradssteg. Watchdog, signalgränser, *Maximal signaländring*, kvittens från Home Assistant och stopp vid fel gäller oförändrat.

Godkännandet för inlärning är lösare än för skuggförslag men behåller säkerhetskontrollerna: minst 48 timmar testsignal åt vardera hållet i träningsperioden, rätt signalriktning och rimlig storlek, minst 24 valideringsfönster och MAE högst 0,8 °C vid 1, 6, 12 och 24 h. För nästa timme ska modellen vara bättre än persistens eller ha ett fel på högst 0,1 °C. Den godkända modell med lägst fel används. Solcellsmodellen används när prognosen täcker hela dygnet, annars temperaturmodellen.

Planeringen lägger till en dygnsprofil: modellens genomsnittliga entimmesfel per klockslag de senaste sju dygnen (minst tre värden per klockslag, högst ±0,3 °C per timme). Den beskriver värme modellen saknar indata för, till exempel matlagning, närvaro och kvällssol, och gör att MPC kan dra ner värmen före kvällen.

En ny modell tränas och prövas varje dygn, men modellen i bruk behålls tills den nya är tydligt bättre. Det som gör det möjligt att skilja signalens effekt från utetemperaturens är testsignalen, se *Testsignal och behållen modell*.

Historikgrafen på Hem har raden *Styrdes av* (grön = MPC, lila = PI) och visar Ohmigos inställda värde i grönt när MPC styrde. Valet loggas varje minut i tabellen `control_samples` och sparas i 90 dagar. Styrkortet visar vem som styr nu och orsaken.

## Testsignal och behållen modell (0.22.0)

PI sätter signalen till utetemperaturen minus en korrigering för temperaturfelet. Signalen blir då nästan en funktion av utetemperatur och rumstemperatur, som modellen redan har som indata, och det går inte att avgöra från sådana data vad signalen själv gör. En modell tränad på enbart PI-styrning får därför en osäker eller felvänd signaleffekt, oavsett hur länge den tränas.

**Testsignal.** När MPC-inlärning är påslagen, aktiv styrning är igång och ingen modell är godkänd lägger PI på en testsignal: +1 °C i åtta timmar, sedan −1 °C i åtta timmar, och så vidare (högst *Största avvikelse från PI*). Blocken följer UTC-klockan, så de fortsätter i samma takt efter en omstart. Åtta timmar är valt för att mönstret ska glida mot dygnet; tolvtimmarsblock skulle sammanfalla med dygnsrytmen och förväxlas med den. Testsignalen pausar när innetemperaturen är utanför komfortområdet och tills den är 0,2 °C innanför igen, och den körs inte när en godkänd modell bara saknar plan, till exempel för att väderprognosen saknas. Styrkortet på Hem visar *PI med testsignal* och varje minuts val loggas i `control_samples`.

**Krav på testsignal.** En modell godkänns, för både skuggförslag och MPC-inlärning, först när dess träningsperiod innehåller minst 48 timmar med höjd signal och 48 timmar med sänkt. En timme räknas när minst 30 loggade minuter finns och minst tre fjärdedelar av dem hade en av Home Assistant bekräftad testsignal på minst 0,5 °C åt samma håll. Avvikelsen mäts mot PI på samma avrundade och signalbegränsade skala. En begärd testsignal som klipps bort eller ännu inte syns i HA räknas inte; inte heller föregående blocks kvarvarande avvikelse när signalen byter håll. Bekräftelsen gäller HA:s utgångsvärde, inte värmepumpens fysiska respons. Äldre loggposter som bara visar begärd testsignal räknas inte. Variation som MPC själv skapar räknas inte, eftersom den beror på samma temperaturer som modellen ska förklara. Utan MPC-inlärning och aktiv styrning körs ingen testsignal och ingen modell godkänns; Lärande visar hur många timmar som samlats de senaste 14 dygnen.

**Behållen modell.** Varje dygn tränas en ny modell på de senaste 14 dygnen. Modellen i bruk prövas samtidigt med oförändrade koefficienter på samma testfönster som den nya. Alla gemensamma testfönster måste börja tidigast vid de behållna modellernas senaste träningsslut, så att prognosutfallen inte ingick i deras träning. Om färre än 24 sådana fönster finns skjuts jämförelsen upp. Föregående validering behålls utan ny tidsstämpel och spärrar fortsatt planering efter 48 timmar om inga färska testdata kan utvärderas. Den behålls om den fortfarande klarar kraven för inlärning, och byts bara ut när den nya också klarar kraven och har minst 10 % lägre sammanlagt fel vid 6, 12 och 24 timmar. En ny modell tränad utan testsignal kan alltså inte tränga undan en som tränats med. Klarar den behållna modellen inte längre kraven används den nya, om den är godkänd; annars styr PI med testsignal tills en modell godkänns. Om ett dygns data inte går att utvärdera, till exempel på grund av luckor, sparas modellen till nästa försök. Valet mellan enkel modell och fördröjningsmodell är trögt på samma sätt: den typ som används behålls tills den andra har minst 10 % lägre fel. Lärande visar när modellen i bruk tränades och att den behålls. Byte av givarval eller en ny kvalitetsversion börjar om.

Valideringen måste fortfarande vara högst 48 timmar gammal för att modellen ska få planera, men träningen får vara äldre. Träningsfönstret är fortsatt 14 dygn: i simulering gav ett längre fönster en sämre skattad signaleffekt när bara en del av perioden hade testsignal.

**Övriga krav.** Persistenskravet gäller inte när modellens fel redan är litet: högst 0,15 °C vid 6, 12 och 24 timmar, och högst 0,1 °C vid en timme för inlärning. Ju jämnare regleringen håller temperaturen, desto svårare är det att slå "temperaturen står still", utan att modellen blivit sämre. Signalens samlade effekt vid 6–12 timmar måste ligga mellan −1 och −0,02 °C per °C; en effekt nära noll får planeraren att driva signalen mot gränserna.

Gränsvärdena (48 timmar, åttatimmarsblock, ±1 °C, 10 %) är valda från simulering med ett antaget hus och appens egen regler- och träningskod, inte från mätningar i en verklig installation.

## Ta bort en rumsgivare och behålla historiken

När du tar bort en eller flera givare under *Rum som styr värmen*, och minst en finns kvar, frågar appen vad som ska hända med historiken:

- **Behåll historiken utan borttagna givare** (förval): tidigare mätningar räknas som medelvärdet av de kvarvarande rummen. Husmodellen tränas om direkt på den behållna perioden.
- **Börja om insamlingen**: husmodellen samlar en ny period på minst 250 kompletta timmar med det nya rumsurvalet.

Inga mätvärden ändras. Bara mätloggens givarval uppdateras, och tidigare urval sparas i fältet `indoor_previous`. De borttagna givarnas värden finns kvar i loggen och i exporten. Den sparade modellen märks inte om, eftersom den tränats på ett annat medelvärde. Arkiverade skuggförslag följs upp mot det medelvärde som gällde när de gjordes. Ändringen loggas i tabellen `migrations`. Säkerhetskopior tas som vid byte av utegivare. Byte av utegivare och borttagning av rumsgivare måste sparas var för sig. Ändringen stoppar aktiv PI som andra tekniska ändringar.

API: `POST /api/indoor/migrate` med `{"config": {...}, "confirm": true}`.

## Grafer och mobilgränssnitt i 0.9.5

PI-status och start/stopp finns högst på översikten efter temperaturkorten. Beräkningsdetaljer, anslutningsstatus och den experimentella inomhusprognosen kan fällas ut. Inställningarnas rumsgivare väljs med sökbara kryssrutor.

Vädergrafen visar prognostemperaturer för kommande 24 timmar. Timtabellen finns kvar under Visa timprognos. Historiska grafer hämtar de senaste 48 timmarna ur appens mätlogg: inomhusmedel och börvärde i en graf, utegivare, Ohmigos inställda värde och PI-förslag i en annan. Sista loggade värdet i varje tiominutersintervall visas. Luckor över 20 minuter, ogiltiga värden och ändrade givarval bryter linjerna. Ett ensamt värde visas som en punkt. Loggning måste vara igång för historiska grafer; importerad modell-CSV används inte där.

På mobil kan grafer rullas i sidled för läsbara tidsaxlar. Tabeller visas som märkta kort. Ohmigos inställda värde är fortfarande inte en kvittens från värmepumpen.

Välj vid behov **Värmepumpens avlästa utetemperatur (valfri)** i Inställningar. Det är pumpens uppfattade temperatur efter Ohmigo, inte verklig utetemperatur. Givaren används endast för uppföljning och loggning; bortfall stoppar inte PI. Historik för givaren samlas från att den valts.

## Sol i skuggförslaget och modellgränser

Appen läser faktisk `weather.get_forecasts`-respons för vald timväderentitet. `temperature` krävs. `cloud_coverage` (%) och det leverantörsspecifika `solar_irradiance` (W/m²) tas med endast när de finns och är giltiga. Gränssnittet visar tillgängliga fält och markerar avsaknad av solinstrålningsprognos. Home Assistants generella prognosformat dokumenterar `cloud_coverage`, men garanterar inte solinstrålning. Vald väderentitets verkliga stöd måste därför kontrolleras i appen; källan kan inte identifieras från denna kodbas.

Om du redan har solpaneler kan du under Inställningar → Väder & sol välja växelriktarens **momentana solcellsproduktion** (W eller kW) och motsvarande **Forecast.Solar-källa**. Installerade Forecast.Solar-källor visas i appen även innan Energipanelen är klar; välj källa eller tryck Uppdatera prognoskällor. För att läsa timprognosen måste den valda källan kopplas till solproduktionen i Home Assistants Energipanel. Appen läser då Energipanelens timvärden (`energy/solar_forecast`, Wh per timme) och omvandlar dem till genomsnittlig effekt i W. En totalsensor i kWh eller nätets exporteffekt är inte rätt historiskt underlag. Forecast.Solar kan utelämna nattens nollproduktion. Appen kompletterar då nattimmar med 0 W mellan sista timmen i en dagsprognos och första timmen i nästa dagsprognos. Båda dagarna måste ha minst tre sammanhängande timvärden, datumen måste följa på varandra i källans tidszon, den sista timmen måste vara efter eller vid kl. 12 och nästa dags första timme före eller vid kl. 12. Avståndet mellan gränstimmarna måste vara 6–18 timmar. Detta är en begränsad tolkning av källans dygnsprognoser, inte en astronomisk beräkning. Luckor inne i dagsprognosen, ogiltiga källvärden eller saknade prognosdygn fylls inte på detta sätt, och prognosens tidsomfång utökas inte. En utelämnad nollpunkt vid lokal midnatt återställs även mellan två kända närliggande timmar. Utan 24 matchande timvärden används den validerade temperaturmodellen i skuggläge om den klarat kvalitetskontrollen; annars visas inget MPC-förslag.

Växelriktare kan sluta rapportera när produktionen når noll på kvällen. Ett sparat nollvärde får därför vara högst 18 timmar gammalt när `sun.sun` har en högst 30 minuter gammal rapport som visar `below_horizon`. Återställda värden godtas inte. Under dagen eller utan färsk solstatus gäller fortfarande 30 minuter; samma gräns gäller alltid positiv produktion.

För äldre mätloggar utan sparad solstatus används en begränsad nollplatåregel: en följd av uttryckligen sparade nollvärden som enbart underkänts för ålder får återanvändas om den börjar med ett färskt nollvärde, avslutas med en färsk giltig rapport, varar 4–18 timmar och har högst 15 minuter mellan mätningarna. Detta är en heuristik för nattliga platåer, inte ett astronomiskt bevis på mörker. Saknade, återställda eller andra ogiltiga värden samt positiva gamla värden bryter följden. En oavslutad natt återanvänds först efter nästa färska rapport. Råloggen skrivs inte om, givarvalet matchas som tidigare, och antalet återanvända nollvärden anges som `recovered_pv_zero_samples` i solcellsrapporten och historikexporten.

Solcellsproduktionen är en indirekt solsignal: panelernas lutning, orientering, skuggning och växelriktarens begränsning kan skilja sig från husets solvärme. Solcellsmodellen kräver minst 250 kompletta historiska timmar, minst 24 testfönster vid 6/12/24 timmar, MAE högst 0,35 °C och inte sämre än temperaturpersistens, om inte felet är högst 0,15 °C (fortsatt absolut tak 0,8 °C). Den skalade effekten av produktionen måste vara positiv men högst 0,001 °C/h per W; felvänd eller orimlig solcellsmodell stoppas. Arkivet sparar den prognos som fanns vid beslutet och jämför senare med uppmätt produktion inom samma 90-dagars retention. Detta är utvärdering av skuggförslag, inte bevis för effekten av alternativa MPC-kommandon.

Ingen separat solsensor krävs. Appen kan automatiskt logga aktuell molnighet från vald väderentitet och lära dess samband med uppmätt innetemperatur. Om både denna historik och 24 timmars molnprognos finns och modellen valideras används molnmodellen. Annars används den temperaturbaserade modellen. Den som redan har en W/m²-sensor eller en separat %-sensor kan fortfarande välja den manuellt; då måste motsvarande prognosfält finnas. Minst 250 kompletta timvärden krävs för varje modell.

`ready` kräver minst 24 valideringsfönster samt MAE högst 0,35 °C vid 6, 12 och 24 timmar och inte sämre än att hålla aktuell rumstemperatur konstant, om inte felet är högst 0,15 °C (fortsatt absolut tak 0,8 °C). Träningsperioden måste dessutom innehålla minst 48 timmar testsignal åt vardera hållet. Gränserna är en konservativ spärr mot svaga modeller, inte en garanti för driftprestanda. Valideringen använder **verkligt framtida väder** och historisk styrsignal, vilket ger modellen bättre väderinformation än den hade haft vid beslut. Några arkiverade `weather.get_forecasts`-svar från beslutstillfällena finns ännu inte; utvärdering mot **då tillgängliga prognoser** kan därför inte rapporteras eller användas som bevis för prognoskvalitet. Överlappande fönster är dessutom beroende.

Planen kräver aktuell bekräftad `number`-utgång med gränser och steg som stöder 0,5 °C. Den begränsas till både entitetens och appens signalgränser, appens timsteg och PI:s ändringstakt per timme. Sökningen behåller de billigaste banorna för varje signalnivå, så att en bana som börjar värma tidigt inför en kall natt inte sorteras bort för att den kostar mer de första timmarna. Planen är fortfarande enbart visning; endast PI har skrivväg till Home Assistant och dess watchdogkrav gäller oförändrat.

## Arkiverad utvärdering av skuggförslag

Varje beräknat MPC-skuggförslag sparas i `mpc_forecasts` med beslutstid, vald väderentitet, tid då prognosen hämtades, fältnamn, de 24 prognostimmarna som användes, hela föreslagna signal- och temperaturbanan samt givarval. Tabellen rensas efter 90 dagar, liksom övrig mätlogg. `GET /api/mpc/evaluation` och knappen i historikvyn jämför de senaste mogna förslagen med giltiga mätvärden inom 30 minuter från respektive prognostimme. Saknade mätningar fylls inte i. Prognos för uteväder (och valt solfält) jämförs vid sin prognostid; MPC:s inomhusprognos jämförs vid den tid som planen anger. Gränssnittet visar senaste jämförelsen; API:et returnerar upp till 24 mogna förslag med timrader och medelfel.

Detta är **utvärdering av skuggförslag**. Inomhustemperaturen som senare mäts upp påverkades av verklig PI-styrning och faktiskt väder. Jämförelsen kan visa prognosfel under den körningen, men inte hur huset skulle ha reagerat på andra MPC-kommandon. Den bevisar därför inte att MPC-styrning skulle förbättra komfort eller energianvändning.

En modell får inte `ready` om ett bestående steg på +1 °C i styrsignalen ger positiv inomhusrespons efter 6 eller 12 timmar. Medelresponsen vid dessa horisonter måste ligga mellan −1 och −0,02 °C per °C. Högre signal betyder mindre värme i denna installation. Detta är en plausibilitetsspärr, inte ett kausalt bevis på signalens effekt.

## Sol utan extra givare

Välj inne- och utegivare, Ohmigos avlästa inställda temperatur och en timväderentitet. Appen loggar automatiskt `cloud_coverage` från väderentitetens **aktuella tillstånd** när attributet finns. Efter minst 250 kompletta timmar kan en molnmodell tränas mot uppmätt rumstemperatur och historisk signal; samma väderentitets molnprognos används då i skuggplanen. En temperaturbaserad modell tränas också och används när molnprognosen saknas eller molnmodellen inte valideras. Val av separat sol- eller molnsensor är frivilligt och avsett för redan installerade sensorer.

Temperaturmätningarna visar hur huset faktiskt reagerar på värme, utetemperatur och tidigare solpåverkan. Utan en framtida sol- eller molnprognos kan modellen inte veta om ett ännu inte märkbart molntäcke kommer att ändras. Den kan då förutse utifrån uteprognosen och kända husförhållanden, men inte hävda att den förutser en plötslig solig period innan temperaturen påverkas. Ingen modell görs redo enbart för att aktuellt väderattribut finns; samma historiska storhet måste ha loggats och modellen måste klara kvalitetskontrollen.

Automatisk träning kvalitetskontrollerar både `linear` och `lag6` och väljer den godkända kandidaten med lägst genomsnittlig MAE vid 6/12/24 h. Styrsignalens respons mäts genom ett bestående steg på +1 °C och rekursiv simulering: responsen får inte vara positiv vid vare sig 6 eller 12 h, och medelresponsen måste ligga mellan −1 och −0,02 °C per °C. Lag6 använder sex kompletta historiska timmar av rumstemperatur och signal, sedan simulerade värden genom planeringshorisonten. Saknas historiken visas inget lag6-förslag. Rapporter och orsaker med uppmätta värden sparas i status även vid underkännande och visas i gränssnittet. Mätlagring och matchning av givarval är oförändrade.

I Lärande → Exportera hämtar **Exportera modellhistorik** en ZIP som förbereds i bakgrunden via `POST /api/model/export`. Gränssnittet följer jobbet via `GET /api/model/export/status?id=…` och hämtar filen via `GET /api/model/export?id=…` när den är klar. Bara ett exportjobb körs åt gången och en omstart av appen avbryter jobbet. `measurements.csv` innehåller hela kvarvarande råloggen med kvalitet och dåvarande givarinställningar. `hourly_*.csv` innehåller kompletta UTC-timvärden för aktuella modellvarianter med samma urval som träningen. `model.json` beskriver givarval, uteslutna värden och senaste sparade modellresultat och status. Export fungerar även innan modellen klarat valideringen. Timvärden räknas från nuvarande logg; tidigare träningsunderlag kan ha hunnit rensas av 90-dagarsgränsen.

Om en godkänd modell inte kan bygga en skuggplan visar status den konkreta orsaken, exempelvis antal saknade `pv_power`-timmar med första luckans tidsstämpel, för kort väderprognos eller saknad historiktimme för lag6. Samma orsaker returneras i `mpc.plan_blockers` i `/api/telemetry`. För en cachad väderprognos väljs 24 aktuella timmar efter att för gamla inledande timmar har hoppats över; fönstret måste fortfarande börja inom två timmar, ha giltiga timintervall och nå minst 22 timmar framåt.

Forecast.Solar kan även lämna giltiga periodpunkter vid soluppgång och solnedgång som inte ligger på hela klockslag. Dessa räknas inte som fullständiga timvärden och flyttas inte till ett annat klockslag, men blockerar inte nattkompletteringen. Ogiltiga värden (exempelvis `unavailable`, negativ produktion eller saknad tidszon) blockerar fortfarande nattregeln. `pv_forecast.normalization` i `/api/telemetry` visar antal källpunkter, fulla timvärden, överhoppade deltimmespunkter, ogiltiga punkter och kompletterade nolltimmar, samt exempel på filtrerade tidsstämplar.


## Färsk modell och solcellsunderlag (0.20.0)

Lärande visar separat vilken period driftmodellen tränats på och när senaste valideringsutfallet mättes. Mer än 48 timmar gamla valideringsutfall blockerar liveplanering, även om äldre felmått klarade gränserna. Sedan 0.22.0 får själva träningen vara äldre, eftersom en behållen modell prövas på färska testfönster varje dygn. När ingen modell är godkänd fortsätter PI enligt befintliga regler.

Luckor med gamla solcellsnollor visas med tidsstämplar. Vid dagtid behövs en färsk produktionsrapport från integrationen; ett upprepat gammalt värde bevisar inte aktuell nollproduktion. Nya loggrader sparar solens tillstånd och dess rapporttid för att kunna styrka nattnollor. Vid bekräftad natt krävs färskt mörkerunderlag (högst 30 minuter gammalt) och ett verkligt sparat nollvärde med högst 18 timmars ålder. Saknade värden, positiva gamla värden och återställda värden blir inte nollor. Äldre loggar utan solunderlag behåller den begränsade platåregeln. Känd solstatus som inte styrker mörker kan inte kringgås av den regeln.
