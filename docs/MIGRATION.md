# Von einer bestehenden MQTT-Konfiguration umsteigen

Wer seine Casambi-Leuchten bisher von Hand als MQTT-Entitäten in
`configuration.yaml` gepflegt hat, kann sie durch diese Integration ablösen.
Diese Anleitung beschreibt den Weg, ohne dass Automationen und Dashboards
danach ins Leere zeigen.

## Warum die Reihenfolge zählt

Die neuen Entitäten gehören zur Plattform `casambi_lithernet`, die alten zur
Plattform `mqtt`. Ihre internen Kennungen kollidieren deshalb nicht. Die
**Entity-IDs** kollidieren aber sehr wohl.

Legst du eine Leuchte in der Integration an, während die alte YAML-Entität mit
demselben Namen noch in der Entitäten-Registry steht, hängt Home Assistant der
neuen eine Zwei an, etwa `light.badspot_1_2`. Die alte bleibt daneben stehen.
Deshalb wird pro Leuchte immer zuerst die alte entfernt.

## Vorbereitung

Notiere dir für jede Leuchte drei Dinge aus deiner bisherigen Konfiguration:

1. Die **Entity-ID**, unter der sie heute läuft.
2. Den **Anzeigenamen**.
3. Die **Casambi-Adresse** aus dem Befehls-Topic, also `targetid` und
   `targettype` in der Nutzlast, bei Units mit mehreren Treibern zusätzlich
   den `dimmer_index`.

Eine Tabelle mit diesen drei Spalten und einer vierten für den künftigen Typ
ist die ganze Vorbereitung. Welcher Typ zu welcher bisherigen Konfiguration
passt, steht in der Typübersicht im [README](../README.md).

## Schritte pro Leuchte

1. Integration installieren und den Gateway-Eintrag einrichten, siehe
   [README](../README.md).
2. Den YAML-Eintrag der Leuchte entfernen und Home Assistant neu starten
   oder die MQTT-Konfiguration neu laden.
3. Die dadurch verwaiste Entität unter Einstellungen, Entitäten löschen.
4. Die Leuchte in der Integration anlegen. Verwende denselben Namen wie
   bisher, damit die Entity-ID gleich bleibt.
5. Prüfen, ob die Entity-ID tatsächlich die alte ist. Weicht sie ab, setze sie
   in den Entitätseinstellungen von Hand auf den alten Wert.

Spiele den Ablauf zuerst mit einer einzigen Leuchte durch, bevor du die
restlichen migrierst.

## Drei Stolpersteine

**Umlaute ergeben eine andere Entity-ID.** Aus „WC Lüfter" bildete Home
Assistant früher `switch.wc_lufter`, heute entsteht `switch.wc_luefter`. Das
betrifft jeden Namen mit einem Umlaut. Entweder die Entity-ID nach dem Anlegen
von Hand zurücksetzen, oder die Verweise in Automationen und Dashboards
nachziehen.

**Ein Schaltausgang bleibt am besten ein Schalter.** Steht dein Lüfter heute
als `switch`-Entität in der YAML, wähle beim Anlegen ebenfalls „Schalter". Die
Darstellung als „Lüfter" ist möglich, ändert aber die Domäne und damit die
Entity-ID.

**Bei Units mit mehreren DALI-Treibern entscheidest du über eine zusätzliche
Entität.** Die Integration kann neben den einzelnen Treibern eine Entität für
die ganze Unit anlegen, die alle Treiber mit einem Funkbefehl setzt. Eine
bestehende YAML-Konfiguration kennt so etwas meist nicht. Wer eins zu eins
abbilden will, schaltet sie ab. Der Punkt ist später jederzeit änderbar, ohne
dass die Treiber-Entitäten davon berührt werden.

## Der gute Moment für eine Bereinigung

In gewachsenen Konfigurationen tragen einzelne Entitäten eine ID, die nicht
mehr zu ihrem Namen passt, weil sie einmal umbenannt wurden oder schon früher
eine Zwei angehängt bekommen haben. Die Migration ist der Moment, das
geradezuziehen: Vergibst du beim Anlegen einen Namen, der zur gewünschten ID
passt, entsteht eine saubere neue Entity-ID. Der Preis ist, dass du die
Verweise in Automationen und Dashboards einmal nachziehst.
