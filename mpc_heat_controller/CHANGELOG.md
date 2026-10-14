# 0.15.8

- Rättar att giltiga soluppgångs-/solnedgångspunkter mellan hela klockslag kunde stoppa all nattkomplettering i Forecast.Solar. De filtreras från timmodellen utan att behandlas som ogiltiga källvärden.
- Lägger till normaliseringsdiagnostik i `pv_forecast.normalization`, inklusive antal överhoppade deltimmespunkter, ogiltiga punkter och kompletterade nolltimmar.
- Regressionstestar rått Forecast.Solar-format med solgränspunkter genom både normalisering och hela telemetriinsamlingen. Kvalitetskrav och PI är oförändrade.

# 0.15.7

- Kompletterar Forecast.Solars utelämnade nattimmar med nollproduktion mellan sammanhängande prognoser för två efterföljande dygn. Fyller inte luckor inne i dagsprognosen eller utanför källans tidsomfång.
- Visar konkreta orsaker när en godkänd modell inte kan bygga skuggförslag, inklusive saknade solcellsprognostimmar och lag6-historik. Orsakerna finns även i `mpc.plan_blockers`.
- Hoppar över för gamla inledande timmar i cachad väderprognos och använder nästa giltiga 24-timmarsfönster.
- Modellens kvalitetskrav, mätlogg och aktiv PI-styrning är oförändrade.

# 0.15.6

- Återanvänder sparade nollvärden från solcellsproduktion i sammanhängande platåer på 4–18 timmar, med färska värden på båda sidor. Saknade värden, återställda värden och gammal positiv effekt fylls inte i. Befintlig mätlogg skrivs inte om och givarurvalets matchning ändras inte.
- Godtar framtida nattliga nollvärden som är högst 18 timmar gamla när en färsk `sun.sun` visar att solen är under horisonten. Positiv effekt och gamla nollvärden under dagen behåller 30-minutersgränsen.
- Justerar absolut MAE-gräns för skuggförslag till 0,35 °C vid 6/12/24 timmar; minst 24 fönster, högst persistensens fel och rätt signalriktning krävs fortfarande. PI och aktiv styrning är oförändrade.
- Uppdaterad kvalitetsversion tvingar omträning av tidigare godkända modeller vid nästa kontroll efter uppdatering.

# 0.15.5

- Lägger till Exportera modellhistorik under Historik → Automatisk modell.
- Hämtar ZIP med råhistorik, kompletta timvärden för aktuella modellvarianter samt senaste modellrapport och valideringsstatus.
- Export fungerar även innan modellen klarat valideringen. Tidsstämplar anges i UTC och inga luckor fylls.

# 0.15.4

- Automatisk modell väljs mellan linear och lag6 efter kvalitetskontroll: MAE högst 0,3 °C och inte sämre än persistens vid 6/12/24 h, minst 24 fönster och fortsatt absolut tak 0,8 °C.
- Kontrollerar styrsignalens samlade respons vid 6 och 12 h; positiv respons spärras även för lag6. Lag6 använder sex timmars historik och rekursiva features i skuggplaneringen.
- Sparar rapporter och konkreta underkännandeorsaker även när temperatur- och solcellsmodellen faller. Visar orsaker i gränssnittet och korrigerar meddelandet om temperaturreserv.
- Befintlig mätlogg och matchning av givarval används utan ändringar.

# 0.15.3

- Visar senast uppmätt solcellsproduktion och de senaste 48 timmarnas produktion i väderöversikten.
- Visar Forecast.Solars egna timvärden i grafen även utan matchande väderprognos, och förklarar när vald källa inte lämnar timvärden.
- MPC i skuggläge och aktiv PI-styrning är oförändrade.

# 0.15.2

- Rättar sparning av Forecast.Solar-källor med Home Assistants alfanumeriska ID. Tidigare avvisades ett giltigt ID som bara innehöll andra tecken än små hexsiffror.
- Valet kan sparas även innan källan kopplats i Energipanelen; MPC använder fortfarande ingen solcellsprognos förrän timvärden finns. PI är oförändrad.

# 0.15.1

- Forecast.Solar-listan visar installerade källor även innan de kopplats till Energipanelen. Appen förklarar när timprognos saknas och kan uppdatera listan utan omladdning.
- Urvalet av prognoskälla är fortsatt frivilligt. MPC använder inte solcellsprognosen utan en kopplad Energy-källa och matchande historisk produktion. PI är oförändrad.

# 0.15.0

- Valbar växelriktarsensor i W/kW och valbar Forecast.Solar-källa från Home Assistants Energipanel. Matchad timprognos kan användas av en validerad solcellsmodell i MPC:s skuggläge.
- Prognostiserad och uppmätt solcellsproduktion sparas för efterhandsjämförelse i högst 90 dagar. Aktiv styrning förblir PI.

# 0.14.0

- Läser och loggar automatiskt aktuell molntäckning från vald Home Assistant-väderentitet när den finns. En validerad molnmodell kan lära av rumstemperaturen utan separat solsensor; temperaturmodellen används som reserv.
- Översiktens väderprognos visar separata diagram för utomhustemperatur, molntäckning och eventuell solinstrålning, med rätt enheter och tydliga besked när ett fält saknas.
- Utvärderingen av arkiverade skuggförslag kan jämföra automatisk molnprognos med senare molnuppgifter.
- MPC fortsätter enbart i skuggläge. Aktiv styrning är fortfarande PI med oförändrade säkerhetsspärrar och watchdogkrav.

# 0.13.0

- MPC i skuggläge kan använda historisk solinstrålning eller molnighet tillsammans med motsvarande timprognos. Utan solunderlag används fortsatt utetemperaturmodellen.
- En modell får status `ready` först efter kvalitetskontroll mot temperaturpersistens och rimlig, negativ skalad effekt av den simulerade utetemperatursignalen.
- MPC-planen följer utgångens gränser, ändringstakt och 0,5 °C-steg och kräver en aktuell kontrollerad startpunkt.
- Prognosen som fanns vid varje beräknat skuggförslag och hela planen arkiveras i högst 90 dagar. Historikvyn kan jämföra prognoser med senare mätvärden; resultatet är inte ett test av alternativa MPC-kommandon.
- PI förblir ensam aktiv styrning. Befintliga säkerhetsspärrar och watchdogkrav är kvar.

# 0.12.0

- Tränar och validerar automatiskt en husmodell från appens egen 90-dagars mätlogg när minst 250 kompletta timmar finns.
- Visar ett 24-timmars MPC-förslag och en inomhusprognos i skuggläge utan att skicka MPC-kommandot till värmepumpen.
- Behåller manuell CSV-import som separat verktyg för äldre historik.

# 0.11.0

- Appens börvärde kan synkas enkelriktat till valfria `climate`-entiteter som användaren själv markerar. Inga termostater väljs eller ändras automatiskt.
- Synkstatus visas i appen, via en egen HA-sensor och på dashboarden. Termostatfel varnar utan att stoppa PI:s separata Ohmigo-styrning.
- Börvärde, komfortintervall och följartermostater kan ändras utan att aktiv PI stoppas. Ändrat komfortmål nollställer I-delen; tekniska reglerinställningar kräver fortsatt ny aktivering.

# 0.10.0

- Färdig Home Assistant-dashboard utan HACS-beroenden för driftstatus, temperaturer, styrsignal, historik och PI-detaljer.
- Egna HA-sensorer för verklig utetemperatur, Ohmigos inställda temperatur, pumpens avlästa utetemperatur, framledning och retur gör dashboarden oberoende av installationens råa entity-ID:n.
- Valbar Home Assistant-blueprint skickar pushnotis till vald Companion-app om PI förblir inaktiv och kontrollerar även status efter HA-omstart.

# 0.9.7

- Ett låst PI-fel skapar automatiskt en beständig Home Assistant-notis. Manuell avstängning och normala väntelägen ger ingen felnotis.
- Notifieringen körs separat och kan inte fördröja eller hindra säkerhetsstoppet.

# 0.9.6

- HA-entiteten får en kort, begränsad kvittensfrist efter ett ändrat kommando så att föregående tillstånd inte felaktigt tolkas som en annan skrivare. Ett avvikande tredje värde stoppar fortfarande PI direkt.
- Feltexten visar förväntat och avläst utgångsvärde och skiljer utebliven HA-kvittens från en oväntad ändring.

# 0.9.5

- Avrundning till närmaste 0,5 °C. Sändning högst var femte minut, även vid oförändrat värde. PI och säkerhetskontroller fortsätter varje minut.
- Watchdog-inställningen måste vara minst 600 sekunder för marginal till sändningsintervallet. Hastighetsbegränsningen behålls.

# 0.9.4

- Giltiga, tillgängliga givarvärden accepteras upp till 24 timmar. Automatisk återstart kräver inte en ny rapport efter omstart. Två stabila kontroller med minst 60 sekunders mellanrum och övriga startvillkor behålls.

# 0.9.3

- Automatisk återstart visar exakt blockerande givare eller utgångsvillkor. Säkerhetskraven behålls.
- Givartider visar UTC-offset, ålder och om HA endast lämnar senaste ändring i stället för senaste rapport.

# 0.9.2

- Klockslag i grafer och mätvärden använder Home Assistants tidszon, med Europe/Stockholm som reserv. Sommar- och vintertid hanteras automatiskt. Lagring sker fortsatt i UTC.

# 0.9.1

- Valbar givare för värmepumpens avlästa utetemperatur efter Ohmigo. Loggas och visas i historikgrafen tillsammans med inställt värde. Påverkar inte PI eller startvillkor.

# 0.9.0

- PI-status och start/stopp flyttade till översiktens början; tekniska detaljer och experimentell prognos kan fällas ut.
- Sökbara kryssrutor för flera rumsgivare, tydligare stegindikering och större mobilkontroller.
- Mobilanpassad navigering, tabeller som kort och dialoger som ryms på liten skärm.
- Graf över kommande utetemperatur med tabell som alternativ.
- Historiska grafer för inomhus/börvärde och verklig utetemperatur/Ohmigo-inställning/PI-förslag från de senaste 48 timmarnas befintliga logg.
- Ingen ändring av PI-reglering eller aktiveringsvillkor.

# 0.8.0

- Temperaturkortet visar rumsgivarnas medelvärde oberoende av utegivarens status. Äldre numeriska värden märks som senast kända; saknade givare får en förklaring. PI:s krav på giltiga givare är oförändrade.

- Skapar elva avläsningsbara sensorer via HA:s MQTT Discovery: PI-status, grundläge, börvärde, medeltemperatur, PI-förslag, senast skickad temperatur/tid, temperaturfel, P, I och kompensation.
- Publiceringen körs i separat tråd och blockerar inte temperaturkommandon.
- Stabil enhetsidentitet mellan omstarter. Discovery återannonseras och sensorer får tillgänglighetskontroll och timeout.
- MQTT-publiceringsstatus visas i webbgränssnittet. Saknade mätvärden publiceras inte som noll eller demovärden.

# 0.7.0

- Valbar automatisk återstart av tidigare aktiverad PI efter appomstart eller tillfälligt kommunikations-/givaravbrott.
- Skickar inget under väntan. Kräver nya giltiga rapporter för reglergivare, utegivare och vald framledning/retur efter avbrottet samt två godkända kontroller med minst 60 sekunders mellanrum.
- Återställda HA-värden godtas inte innan riktig rapport finns. Ohmigo, gamla automationen och watchdogvillkoren kontrolleras fortfarande.
- Manuellt stopp och sparade inställningar raderar återstartsönskemålet beständigt. Ny aktivering krävs då.
- Uppgraderade installationer får automatisk återstart avstängd som standard.

# 0.6.0

- Aktiv PI kan startas uttryckligen från översikten och skriver till vald number-entitet via HA.
- Kräver giltiga givare och utgång, verifierad kommandowatchdog (minst 180 s), bekräftad ensam skrivare och avstängd namngiven gammal automation.
- Upprepar temperaturkommandon ungefär varje minut, anpassade till utgångens steg och signalens ändringsgräns.
- Stoppar skrivning vid fel, återaktiverad gammal automation, ändrade inställningar eller oväntad utgångsändring. Ingen automatisk återstart efter fel eller appomstart.
- Stoppa-knapp upphör med kommandon; hårdvarans verifierade watchdog ansvarar för fallback. Ingen direkt givarbypass implementerad.
- Befintlig skugglägeskonfiguration uppgraderas med aktiv styrning avstängd.

# 0.5.0

- Ny PI-regulator i skuggläge med inställningar för Kp, Ki, kompensationsgräns och ändringshastighet.
- Visar temperaturfel, P-del, I-del, kompensation, föreslagen utetemperatur och begränsningsstatus.
- Beräknar var femte minut i bakgrunden och loggar PI-resultat tillsammans med mätloggen.
- Stoppar förslag och nollställer integratorn vid databortfall, demo och uppehåll över 15 minuter. Relevanta konfigurationsändringar och omstarter återställer regulatorn.
- Ingen aktiv styrning.

# 0.4.0

- Sparar senaste lyckade modellutvärdering, original-CSV och givarval lokalt och återställer dem när gränssnittet öppnas.
- Jämför enkel modell, modell med temperatur- och styrsignalfördröjningar samt oförändrad temperatur på samma testfönster vid alla horisonter.
- Sex timmars förhistorik krävs; luckor fylls inte. Fördröjningsmodellens regularisering är fast och väljs inte på testdata.
- Valbar sensor/number-entitet för avläsning och loggning av Ohmigos inställda temperatur. Inga kommandon skickas.
- Modellresultat är fortsatt endast offlinekandidater.

# 0.3.0

- Historikimport med val av rumsgivare, verklig utetemperatur, historisk styrsignal och datumintervall.
- Anpassning av en enkel linjär modell till de första 70 procenten av kompletta timmar och validering på senare data.
- Visar medelabsolutfel vid 1, 6, 12 och 24 timmar, jämfört med oförändrad temperatur. Inga luckor fylls.
- Tydligare rubrik och tomstatus för inomhusprognosen.
- Modellen är endast en offlinekandidat och aktiveras inte för prognos eller styrning.

# 0.2.0

- Hämtar timprognos från vald HA-väderentitet var 30:e minut, med tidskontroll och Celsius-konvertering.
- Visar prognostider och temperaturer separat från husets ännu ej kalibrerade prognos.
- Visar reglergivare, uppföljningsrum, utomhus-, framlednings- och returtemperatur med datastatus.
- Loggar valda mätvärden och aktuell konfiguration var femte minut i skuggläge, även när UI är stängt. Lokal SQLite-logg med 90 dagars retention.
- Visar fel vid utebliven prognos eller mätdata. Använder last_reported när tillgängligt för givarens rapporteringstid.
- Ingen aktiv styrning eller modellträning.

# 0.1.0

- Första förhandsversionen med svenskt webbgränssnitt och installationsguide.
- Konfigurerbara givare och komfortmål.
- Demo med syntetisk temperaturplanering och läsning av givare i skuggläge.
- Granskning av CSV-historik.
- Ingen aktiv värmepumpsstyrning.
