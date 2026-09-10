# MPC Heat Controller

Förhandsversion 0.1.0. Börja i Demo efter installation och öppna webbgränssnittet via Home Assistant.

Installationsguiden låter dig välja temperaturgivare och komfortmål. I skuggläge läser appen valda givare via Home Assistants interna API. Ingen separat token behövs.

Demo använder ett syntetiskt exempelhus och exempelväder. Skuggläget visar mätvärden, men verklig prognoshämtning, egna HA-entiteter och en kalibrerad MPC återstår. Appen skickar inga kommandon till värmepumpen.

Inställningarna lagras i appens beständiga datakatalog. CSV-granskning sparar inte filen och tränar inte modellen.
