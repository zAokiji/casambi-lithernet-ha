# Entwurf und Befunde

Warum diese Integration so gebaut ist, wie sie gebaut ist. Der Code verweist
auf die Abschnitte dieser Datei; wer hier nachschlägt, findet die Begründung
und, wo es eine gibt, den Messwert dahinter.

Alle Angaben zum Gateway stammen aus dem Lithernet-Systemhandbuch, Kapitel 5.6
(MQTT), und wurden gegen einen Mitschnitt einer laufenden Anlage geprüft,
Gateway-Firmware 4.35. Wo Handbuch und Wirklichkeit sich widersprechen, gilt
die Wirklichkeit, und die Abweichung steht hier.

## Topics

Jedes Topic beginnt mit einem Präfix und der Bridge-ID, also `casambi/0/…`.
Beides ist einstellbar; die Werte stehen in `const.py` und nirgends sonst.

**Befehle** an das Gateway:

| Topic | Nutzlast | Zweck |
|---|---|---|
| `set/target_level` | `level` 0–255, `duration`, `targetid`, `targettype` | Ein, Aus und Dimmen |
| `set/target_tc` | `tc` 0–255, `duration`, `targetid`, `targettype` | Farbtemperatur |
| `set/target_dimmers` | `dimmer_index` ab 0, `dimmer_value`, `duration`, `targetid`, `targettype` | Ein DALI-Treiber einer Unit |
| `set/scene_level` | `scene`, `level`, `duration` | Szene aufrufen |
| `set/level` | `level`, `duration` | Alle Leuchten des Netzes |

**Zustände** vom Gateway:

| Topic | Inhalt |
|---|---|
| `get/poll_device/<id>/values` | `level`, `last_level`, `cct_level`, `scene`, Farbwerte, `vertical`, `last_change` |
| `get/poll_device/<id>/propertys` | `online`, `node_type`, `priority`, `condition`, `battery_level`, weitere Diagnosewerte |
| `get/poll_group/<n>`, `get/poll_broadcast`, `get/poll_ungrouped` | `level` als Mittelwert, `last_level`, `cct_level`, `vertical` |
| `get/poll_scene/<n>` | `active`, `level` |

Die Schreibweise `propertys` stammt vom Gateway und ist kein Tippfehler im
Code. `last_change` ist ein Sekundenzähler des Gateways, kein Zeitstempel.

**Zieltypen** in der Nutzlast: 0 alle Leuchten, 1 Unit, 2 Gruppe (mit Adresse 0
die ungruppierten), 3 und 4 Szene, 5 alle Leuchten eines Herstellers.

## Der wichtigste Fallstrick: ein Befehl pro Aufruf

Beim Einschalten mit einer Helligkeit darf **genau eine** Nachricht das Gerät
erreichen. Wird zusätzlich ein separater Ein-Befehl mit Level 255 gesendet,
überschreibt er jeden Dimm- und jeden Farbtemperaturbefehl, und die Leuchte
springt auf volle Helligkeit.

Das ist in einer echten Anlage passiert, und der Grund, warum eine von Hand
gepflegte MQTT-Konfiguration dort `on_command_type: brightness` brauchte.

Der Code beugt dem baulich vor: es gibt keine Ein-Operation. Einschalten ist
ein Level größer als null, Ausschalten ist Level null. `tests/test_wire_commands.py`
vergleicht für jede Entitätsart die vollständige Liste der gesendeten
Nachrichten, nicht nur deren erste.

## Farbtemperatur

Das Handbuch erlaubt Kelvin oder eine normalisierte Skala von 0 bis 255. Die
Leuchten reagieren nur auf die normalisierte Form:

    tc = (K - min) / (max - min) * 255

Die Grenzen sind pro Leuchte einstellbar. Das Gateway meldet die
Farbtemperatur **nicht** zurück; `cct_level` bleibt auf null, auch bei
Leuchten, die Farbtemperatur können. Home Assistant führt den Wert deshalb
optimistisch und merkt sich, was zuletzt gesendet wurde.

## Polling-Methoden

Im Gateway entscheidet die Polling-Methode, ob Home Assistant überhaupt
Zustände sieht.

| Wert | Verhalten | Latenz |
|---|---|---|
| `inactive` | sendet keine Zustände | – |
| `active` | fragt alle Units zyklisch ab | rund 7 s |
| `passive` | meldet Änderungen, fragt Units nicht zyklisch ab | schnell |
| `passive_37_80` | meldet Änderungen und fragt in ruhigen Zeiten zusätzlich ab | rund 0,16 s |
| `passive_37_90` | nur Meldungen, keine aktive Abfrage | schnell |
| `passive_39_52` | wie oben, mit weiteren Meldungsarten | schnell |

Die Zahlen in den Namen bezeichnen die **Casambi-Firmware der Leuchten**, nicht
die des Gateways. Gruppen und Szenen werden in jedem Modus zyklisch abgefragt,
weil Casambi dafür keine Meldungen kennt.

## Zustand und Verfügbarkeit

**Zweistufige Verfügbarkeit.** Eine Entität ist nur erreichbar, wenn der Broker
verbunden ist und, bei Elementen mit `propertys`, das Gateway die Unit als
`online` meldet. Gruppen, Szenen und Broadcast haben kein `online` und hängen
nur am Broker.

**Retained.** Das Gateway veröffentlicht `values` und `propertys` retained, in
einem Mitschnitt 424 von 560 Nachrichten. Nach einem Neustart von Home
Assistant ist der Zustand deshalb sofort da. Gruppen-, Szenen- und
Broadcast-Topics sind nicht retained und starten als unbekannt.

**Der Rückfall nach drei Sekunden.** Eine Entität, die nicht blind bedient
wird, wartet nach einem Befehl auf eine Zustandsmeldung. Kommt innerhalb von
drei Sekunden keine, übernimmt sie den gesendeten Wert. Das verhindert
hängende Schieberegler bei Units, die nie einen Zustand melden, etwa weil sie
außerhalb des Poll-Bereichs liegen.

Dabei gibt es einen bekannten Wettlauf. Das Gateway fragt zyklisch ab, eine
Meldung kann also kurz vor der Wirkung eines Befehls veröffentlicht worden
sein und noch den alten Wert tragen. Sie gilt trotzdem als Antwort, und die
Entität zeigt den alten Zustand, bis das Gateway die Änderung meldet. Die
Gegenrichtung, eine Meldung nur bei passendem Wert zu akzeptieren, wurde
gebaut und wieder verworfen: sie tauscht dieses kurze Flackern gegen ein
schlechteres Verhalten, nämlich ein Kommando, das die Leuchte nie erreicht
hat, würde als erfolgreich angezeigt.

**Blind bedient** wird eine Entität, wenn der Nutzer es erzwingt, wenn die Art
es verlangt, oder wenn die Polling-Methode gar keine Zustände liefert.

## Elementarten

| Art | Adressraum | Zustand |
|---|---|---|
| Dimmbare Leuchte | Unit | echt |
| Leuchte mit Farbtemperatur | Unit | Helligkeit echt, Farbtemperatur blind |
| Unit mit mehreren DALI-Treibern | Unit | Einzeltreiber blind, Gesamtwert als Mischwert |
| Casambi-Gruppe | Gruppe | echt |
| Schaltausgang | Unit | echt |
| Casambi-Szene | Szene | echt |
| Alle Leuchten | Broadcast | Mittelwert, blind |

**Units mit mehreren Treibern** melden nur einen Mischwert für die ganze Unit.
Drei Treiber auf 50, 25 und 15 Prozent ergeben eine Meldung mit Level 75.
Einzelzustände gibt es nicht, deshalb werden die Treiber blind bedient.

**Der Schaltausgang** entsteht wahlweise als Schalter oder als Lüfter. Ein
Nachlauf gehört bewusst nicht in die Integration, sondern in eine Automation.

**Kollisionsgefahr bei den Kennungen.** Die vier Arten, die eine Unit
adressieren, erzeugen dieselbe interne Kennung. Zwei davon auf derselben
Adresse wären eine Kollision, bei der Home Assistant eine Entität
stillschweigend verwirft. Die Dublettenprüfung im Formular vergleicht deshalb
die Kennung, nicht das Paar aus Art und Adresse.

## Diagnose je Leuchte

Aus `propertys` entstehen sieben Entitäten je Unit, vier davon standardmäßig
abgeschaltet. Die beiden interessanten:

- **Zustandscode** aus `condition`: 0x00, 0x80 und 0xA0 bedeuten in Ordnung,
  0x01 überhitzt, 0x09 Überlast, 0x81 thermische Überlast, 0x82
  Leuchtmittelausfall, 0x83 Treiberausfall, 0x85 inkompatible Hardware, 0x86
  Hardware nicht gefunden, 0x87 Konfiguration fehlgeschlagen.
- **Steuerungsquelle** aus `priority`, maskiert mit den sechs unteren Bits:
  1 Notbeleuchtung, 2 Gebäudeleittechnik, 3 Handbedienung, 8 Präsenzmelder,
  11 Datums-Timer, 12 Zeitschaltuhr, 15 Start, 4 bis 14 sonst eine
  Casambi-Automation.

Die Steuerungsquelle beantwortet die Frage, wer eine Leuchte zuletzt gesetzt
hat. Genau daran scheiterte einmal die Suche nach der Ursache, als in einer
Anlage morgens alle Leuchten auf ein Prozent standen.

Ein unbekannter Rohwert ergibt den Zustand `unrecognized` und nicht `unknown`,
weil Home Assistant letzteres für „noch keine Daten" verwendet und beides im
Logbuch sonst nicht zu unterscheiden wäre.

## Fehlerbehandlung

Zwei Reparaturhinweise: die MQTT-Integration fehlt, und einen Tag lang kam
keine einzige Zustandsmeldung, obwohl die eingestellte Polling-Methode welche
liefern müsste. Beide hängen am Eintrag und verschwinden mit ihm.

Ungültige Nachrichten werden verworfen, gezählt und einmal je Topic gemeldet,
danach nur noch auf Debug-Ebene. Der zuletzt bekannte gute Zustand bleibt
dabei erhalten.

Der Diagnose-Export enthält die Gateway-Konfiguration, die Elementliste, die
Zähler und die letzte Nachricht je Topic. Zugangsdaten kommen nicht vor, weil
die Integration keine besitzt: sie spricht durch die MQTT-Integration.

## Was das Gateway noch könnte

Dokumentiert, aber nicht umgesetzt: RGBW und Hue/Sättigung, das Verhältnis
zwischen direktem und indirektem Anteil, Casambi-Sensoren mit Präsenz und
Helligkeit, Casambi-Taster mit kurzem und langem Druck, sowie die
Gegenrichtung, in der Home Assistant dem Casambi-Netz Präsenz, Helligkeit und
virtuelle Taster liefert.

Sensoren, Taster und die Element-Adressierung brauchen Casambi-Firmware ab
37.90 auf den Leuchten selbst. In der Anlage, an der diese Integration
entstand, kam in 37 Sekunden Mitschnitt keine einzige solche Nachricht an.

Ab Gateway-Firmware 4.41 gibt es einen eigenen Betriebsmodus „HomeAssistant"
mit automatischer Erkennung. Er beschränkt sich auf Leuchten, maximal 63
Stück, ohne Zustandsrückmeldung für Gruppen und Szenen und ohne Sensoren, und
er schließt den MQTT-Modus aus. Für einfache Anlagen ist er eine Alternative,
für alles darüber hinaus nicht.
