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
