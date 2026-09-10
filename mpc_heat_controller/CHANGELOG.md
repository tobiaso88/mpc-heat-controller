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
