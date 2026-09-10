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
