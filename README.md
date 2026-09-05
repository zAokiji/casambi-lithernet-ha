# Casambi (Lithernet MQTT) für Home Assistant

Diese Integration bindet Casambi-Leuchten, die über ein Lithernet-MQTT-Gateway
erreichbar sind, in Home Assistant ein. Sie richtet sich an Nutzer, die eine
Casambi-Anlage mit einem Lithernet-Gateway betreiben und ihre Leuchten ohne
YAML-Pflege in Home Assistant anlegen wollen. Der Nutzer gibt in der
HA-Oberfläche nur die Casambi-Unit-ID, einen Namen und den Leuchtentyp ein.
Die Integration erzeugt daraus die passenden Entitäten und spricht MQTT selbst.

Die Integration steuert ausschließlich über MQTT. Sie nutzt weder die
Casambi-Cloud-API noch Bluetooth und schreibt nicht in die Weboberfläche des
Gateways. Einstellungen im Gateway nimmt der Nutzer selbst vor, ein
Einrichtungsassistent erklärt und prüft sie.

## Voraussetzungen

- Eine Home-Assistant-Instanz mit geladener MQTT-Integration und einem
  erreichbaren MQTT-Broker, zum Beispiel dem Mosquitto-Add-on.
- Ein Lithernet-Gateway, das im MQTT-Betriebsmodus läuft und mit dem Broker
  verbunden ist.

## Installation

Die Installation erfolgt über HACS als benutzerdefiniertes Repository:

1. In HACS unter „Benutzerdefinierte Repositories" dieses Repository
   eintragen, Kategorie **Integration**.
2. Die Integration „Casambi (Lithernet MQTT)" installieren.
3. Home Assistant neu starten.
4. Die Integration über „Integration hinzufügen" einrichten.

![HACS-Installation](docs/images/00-hacs-installation.png)

## Einrichtung des Gateways

Bevor die Integration eingerichtet wird, muss das Lithernet-Gateway selbst
konfiguriert sein. Die folgenden Tabellen zeigen die nötigen Felder in der
Gateway-Weboberfläche.

### Control System → MQTT

| Feld | Beispielwert | Erklärung |
|---|---|---|
| MQTT IP | Adresse des Brokers im eigenen Netz | Adresse des MQTT-Brokers, zum Beispiel des Mosquitto-Add-ons auf Home Assistant. |
| Port | 1883 | Standard, unverschlüsselt im LAN. |
| Benutzer / Passwort | Ein auf dem Broker angelegter Benutzer | Muss ein in Mosquitto angelegter Benutzer sein. |
| Bridge ID | 0 | Wird Teil jedes MQTT-Topics. |

> **Achtung, zwei ähnliche IP-Felder.** `IP-Settings` ist die Adresse des
> Gateways selbst. `Control System → MQTT IP` ist die Adresse des Brokers.
> Eine Verwechslung erzeugt einen Adresskonflikt mit Home Assistant.

### Casambi Settings → Polling Method

Diese Einstellung entscheidet, ob Home Assistant überhaupt Zustände sieht.
Ohne die richtige Polling-Methode funktionieren zwar Befehle, aber Home
Assistant bleibt blind für das, was in der Casambi-App oder von anderen
Quellen aus geschieht.

| Wert | Verhalten | Latenz | Empfehlung |
|---|---|---|---|
| `inactive` | Gateway sendet keinerlei Zustände. Befehle funktionieren, Home Assistant bleibt blind. | – | Nicht verwenden |
| `active` | Gateway fragt alle Units im Poll-Bereich zyklisch ab und publiziert die Antworten. | ca. 7 s bei Poll-Bereich 1–30 | Nur wenn passiv nicht verfügbar |
| `passive` | Units melden Änderungen per Notification, werden dann abgefragt. Keine zyklische Abfrage der Units. | schnell | Nur wenn `passive_37_80` nicht verfügbar |
| `passive_37_80` | Wie `passive`, zusätzlich werden die Units in ruhigen Zeiten nacheinander zyklisch abgefragt. | ca. 0,16 s | Empfohlen |
| `passive_37_90` | Nur Notifications, keine aktive Abfrage der Units mehr. Setzt Casambi-Evolution-Firmware ≥ 37.90 im Netz voraus. | schnell | Erst nach Firmware-Prüfung |
| `passive_39_52` | Wie 37_90 mit weiteren Notification-Typen. Setzt Evolution ≥ 39.52 voraus. | schnell | Erst nach Firmware-Prüfung |

Die Zahlen in den Namen `37_80`, `37_90` und `39_52` bezeichnen die
**Casambi-Firmware der Leuchten** selbst, die sogenannte Evolution-Version.
Sie haben nichts mit der Firmware des Lithernet-Gateways zu tun. Welche
Evolution-Version im Netz läuft, zeigt das Gateway unter Casambi Settings →
„Casambi Version".

Szenen und Gruppen werden in jedem passiven Modus weiterhin zyklisch
abgefragt, weil Casambi dafür keine Notifications kennt.

Zusätzlich relevant ist der **Poll-Bereich**, also die Unit-IDs von…bis, die
im aktiven Modus abgefragt werden. Der Bereich muss alle Unit-IDs abdecken,
die in Home Assistant verwendet werden sollen. Units außerhalb des Bereichs
werden im aktiven Modus nicht abgefragt und laufen dann blind.

### Weitere Felder

| Feld | Wert | Erklärung |
|---|---|---|
| Use Broadcast | `false` | Betrifft BLE-Broadcast, nicht MQTT. Muss aus bleiben. |
| IP-Settings | Feste Adresse, DHCP inactive | Eigene Adresse des Gateways. Sollte statisch sein, damit die Weboberfläche auffindbar bleibt. |

![Gateway-Einstellungen](docs/images/01-einrichtung-gateway.png)

## Der Einrichtungsassistent

Beim ersten Einrichten führt der Config Flow Schritt für Schritt durch die
Gateway-Konfiguration und prüft danach, ob Befehle und Zustände fließen.

1. **Voraussetzungen prüfen.** Der Assistent prüft, ob die MQTT-Integration
   geladen und mit dem Broker verbunden ist, und zeigt Broker-Adresse und
   Port an. Diese Adresse wird gleich ins Gateway eingetragen.
2. **Gateway mit MQTT verbinden.** Erklärtext zu den Feldern aus
   „Control System → MQTT", inklusive der Warnung zu den zwei IP-Feldern.
   Eingegeben werden die Gateway-Adresse, nur zur Dokumentation, und die
   Bridge-ID.
3. **Polling-Methode.** Erklärtext zur Polling-Tabelle mit Empfehlung
   `passive_37_80` und Hinweis auf den Poll-Bereich. Der Nutzer gibt an,
   welche Methode er im Gateway eingestellt hat.
4. **Broadcast und Sonstiges.** Kurzer Hinweis, `Use Broadcast` aus zu
   lassen und dem Gateway eine feste IP zu geben.
5. **Verbindungsprüfung** in zwei Teilen:
   - **Blinktest:** Der Nutzer gibt die Unit-ID einer sichtbaren Leuchte
     ein. Die Integration schaltet sie kurz auf volle Helligkeit und
     wieder aus und fragt, ob sie reagiert hat. Bei „Nein" folgen Hinweise
     zu MQTT-IP, Benutzer und Bridge-ID mit der Möglichkeit, es erneut zu
     versuchen.
   - **Zustandsprüfung:** Die Integration hört eine festgelegte Zeit lang
     alle Zustands-Topics mit, während der Nutzer eine Leuchte in der
     Casambi-App bedient. Das Ergebnis zeigt die Anzahl empfangener
     Nachrichten. Kommt nichts an, weist der Assistent auf die
     Polling-Methode `inactive` als wahrscheinlichste Ursache hin und
     bietet an, trotzdem fortzufahren. Dann laufen alle Leuchten blind.
6. **Abschluss.** Zusammenfassung und Anlage des Gateway-Eintrags. Danach
   werden einzelne Leuchten über „Hinzufügen" auf der Integrationsseite
   angelegt.

Die Verbindungsprüfung aus Schritt 5 ist auch später über die Optionen des
Gateway-Eintrags erreichbar, um nach einem Gateway-Update oder einem
Broker-Wechsel schnell zu testen.

![Einrichtungsassistent, Verbindungsprüfung](docs/images/02-einrichtung-verbindungspruefung.png)

## Elementtypen

Beim Anlegen eines Elements wählt der Nutzer zuerst den Typ. Danach richten
sich die weiteren Felder nach diesem Typ.

| Typ | Wofür | Adresse | Zustand |
|---|---|---|---|
| Dimmbare Leuchte | Einzelne Casambi-Unit, ein/aus und Helligkeit | Unit-ID | Wird zurückgemeldet |
| Leuchte mit Farbtemperatur | Einzelne Casambi-Unit mit Tunable White | Unit-ID | Helligkeit wird zurückgemeldet, Farbtemperatur wird blind bedient |
| Unit mit mehreren DALI-Treibern | Eine Casambi-Unit mit mehreren Dimmern, z. B. mehrere Lichtlinien an einem Gerät | Unit-ID | Einzelne Treiber werden blind bedient, eine optionale Gesamt-Entität kann den gemeldeten Mischwert anzeigen |
| Casambi-Gruppe | Eine in der Casambi-App angelegte Gruppe | Gruppen-ID | Wird zurückgemeldet |
| Schaltausgang | Reiner Ein/Aus-Ausgang, z. B. ein Lüfter | Unit-ID | Wird zurückgemeldet |
| Casambi-Szene | Ruft eine in der Casambi-App angelegte Szene auf | Szenen-ID | Wird zurückgemeldet, das Gateway meldet, ob die Szene aktiv ist |
| Alle Leuchten (Broadcast) | Setzt mit einem Befehl jede Leuchte im Netz | Keine Adresse | Mittelwert über das Netz, wird deshalb blind bedient |

### Dimmbare Leuchte

Eine einzelne Casambi-Unit, die geschaltet und gedimmt werden kann. Sie wird
als `light`-Entität mit Helligkeit angelegt. Solange die Polling-Methode
Zustände liefert, meldet die Leuchte ihren tatsächlichen Zustand zurück.

### Leuchte mit Farbtemperatur

Eine Casambi-Unit mit Tunable White wird als `light`-Entität mit Helligkeit
und Farbtemperatur angelegt, mit einstellbaren Kelvin-Grenzen. Das Gateway
meldet die Farbtemperatur nicht zurück, deshalb merkt sich die Integration
den zuletzt gesendeten Wert. Die Helligkeit wird echt zurückgemeldet.

### Unit mit mehreren DALI-Treibern

Manche Casambi-Units steuern mehrere Leuchten über mehrere DALI-Treiber
gleichzeitig. Für jeden Treiber entsteht eine eigene Entität. Da das Gateway
für die gesamte Unit nur einen Mischwert meldet, laufen die einzelnen
Treiber-Entitäten blind. Zusätzlich kann eine Gesamt-Entität angelegt
werden, die alle Treiber mit einem Befehl gemeinsam setzt und den vom
Gateway gemeldeten Mischwert anzeigt.

### Casambi-Gruppe

Eine in der Casambi-App angelegte Gruppe wird als `light`-Entität mit
Helligkeit angelegt und mit einem einzigen Funkbefehl geschaltet. Ihr
Zustand wird zurückgemeldet. Casambi-Gruppen sind etwas anderes als
Home-Assistant-Lichtgruppen. Für eine reine Zusammenfassung in Home
Assistant ist eine Home-Assistant-Lichtgruppe der bessere Weg. Die
Casambi-Gruppe hat den Vorteil, dass nur ein Funkbefehl nötig ist.

### Schaltausgang

Eine Casambi-Unit mit reinem Schaltausgang, zum Beispiel ein Lüfter, kennt
nur ein und aus, kein Dimmen. Sie kann als `fan`-Entität oder als
`switch`-Entität dargestellt werden. Ein Nachlauf, etwa „nach Licht aus noch
einige Minuten weiterlaufen", gehört bewusst nicht in die Integration,
sondern in eine Home-Assistant-Automation.

### Casambi-Szene

Ruft eine in der Casambi-App angelegte Szene auf und wird als `light`-Entität
mit Helligkeit angelegt, weil das Gateway zurückmeldet, ob die Szene aktiv
ist. Die Szene muss vorher in der Casambi-App angelegt werden, die
Integration kann selbst keine Szenen anlegen.

### Alle Leuchten (Broadcast)

Setzt mit einem einzigen Funkbefehl jede Leuchte im Netz gleichzeitig und
wird als `light`-Entität mit Helligkeit angelegt. Die gemeldete Helligkeit
ist ein Mittelwert über das gesamte Netz und wird deshalb blind bedient.
Dieser Typ eignet sich besonders als „Alles aus".

![Elementtypen anlegen](docs/images/03-elementtypen.png)

## Diagnose

Die Integration zeigt je Leuchte diagnostische Werte an, die vom Gateway
zurückgemeldet werden:

- **Erreichbar:** Ob die Leuchte gerade online ist.
- **Zustandscode:** Ob die Leuchte in Ordnung ist oder ein Problem wie
  Überhitzung, Überlast oder ein Treiberausfall vorliegt.
- **Steuerungsquelle:** Ob zuletzt ein Timer, ein Präsenzmelder, eine
  Casambi-Automation oder eine Handbedienung die Leuchte gesetzt hat.

Diese Werte helfen dabei, die Ursache unerwarteter Zustandsänderungen zu
finden, zum Beispiel wenn Leuchten unerwartet gedimmt werden und unklar ist,
ob ein Timer, eine Automation oder eine Person dafür verantwortlich war.

## Grenzen und Bekanntes

- **Farbtemperatur wird nicht zurückgemeldet.** Das Gateway meldet bei
  Tunable-White-Leuchten keinen tatsächlichen Farbtemperaturwert. Die
  Integration führt die Farbtemperatur deshalb blind, die Helligkeit bleibt
  dagegen echt.
- **Keine Einzelzustände bei Units mit mehreren Treibern.** Das Gateway
  meldet für eine Unit mit mehreren DALI-Treibern nur einen Mischwert für
  die gesamte Unit. Einzelne Treiber-Entitäten laufen deshalb blind.
- **Casambi-Gruppen und Szenen werden immer zyklisch abgefragt.** Anders
  als einzelne Leuchten werden Gruppen und Szenen in jedem Polling-Modus,
  auch den passiven, weiterhin zyklisch abgefragt, weil Casambi dafür keine
  Notifications kennt.
- **Sensoren und Taster brauchen neuere Casambi-Firmware.** Präsenzmelder,
  Helligkeitssensoren und Taster, die über Casambi eingebunden sind, setzen
  eine neuere Casambi-Evolution-Firmware auf den Leuchten voraus und sind
  nicht Teil dieser Version.
- **Gateway-Betriebsmodus „HomeAssistant" als Alternative.** Ab
  Gateway-Firmware 4.41 beta bietet das Lithernet-Gateway selbst einen
  Betriebsmodus „HomeAssistant" mit MQTT-Auto-Discovery. Er eignet sich für
  einfache Anlagen mit reinen Ein/Aus- und Dimm-Leuchten, deckt aber weder
  Tunable White noch Units mit mehreren Treibern noch Szenenstatus ab und
  schließt den MQTT-Modus dieser Integration aus, da ein Gateway nur einen
  Betriebsmodus gleichzeitig hat. Die Zuordnung von Casambi-Elementen zu
  Home-Assistant-Leuchten erfolgt in diesem Modus zudem in der
  Gateway-Weboberfläche und nicht in Home Assistant selbst. Diese
  Integration im MQTT-Modus bleibt daher der empfohlene Weg für Anlagen mit
  mehr als einfachen Ein/Aus- und Dimm-Leuchten.

## Migration von bestehender YAML-Konfiguration

Wer seine Leuchten bereits über YAML in Home Assistant eingebunden hat, findet
die Schritte zur Ablösung in [docs/MIGRATION.md](docs/MIGRATION.md).

## Lizenz

MIT, siehe [LICENSE](LICENSE).
