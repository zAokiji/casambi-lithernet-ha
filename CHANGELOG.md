# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in dieser Datei
festgehalten. Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

Alles ist gebaut und getestet, die Abnahme an der echten Anlage steht noch
aus. Erst danach wird 0.1.0 vergeben.

### Hinzugefügt

- **Geführte Einrichtung des Gateways.** Der Assistent prüft die
  MQTT-Voraussetzungen, erklärt die Felder der Gateway-Oberfläche samt der
  Warnung vor den zwei ähnlichen IP-Feldern, erklärt alle sechs
  Polling-Methoden und prüft danach mit einem Blinktest und einem Mitschnitt,
  ob Befehle ankommen und Zustände zurückkommen. Bei Polling `inactive`
  entfällt die Zustandsprüfung.
- **Sieben Elementtypen**, jeweils über ein eigenes Formular anlegbar,
  bearbeitbar und entfernbar: dimmbare Leuchte, Leuchte mit Farbtemperatur,
  Unit mit bis zu acht DALI-Treibern, Casambi-Gruppe, Schaltausgang wahlweise
  als Schalter oder Lüfter, Casambi-Szene und alle Leuchten auf einmal.
- **Diagnose je Leuchte** aus den Gateway-Eigenschaften: erreichbar,
  Zustandscode im Klartext, und die Steuerungsquelle, also ob zuletzt eine
  Handbedienung, ein Präsenzmelder, eine Zeitschaltuhr oder eine
  Casambi-Automation gesetzt hat. Batteriestand, Umgebungstemperatur,
  Überhitzung und allgemeiner Fehler sind vorhanden, aber ausgeschaltet.
- **Zweistufige Verfügbarkeit.** Eine Entität ist nur erreichbar, wenn der
  Broker verbunden ist und das Gateway die Unit als online meldet.
- **Reparaturhinweise** für eine fehlende MQTT-Integration und für einen Tag
  ohne jede Zustandsnachricht, letzterer nur wenn die Polling-Methode
  überhaupt Zustände liefern müsste.
- **Diagnose-Export** über den Standardknopf von Home Assistant, mit
  Gateway-Konfiguration, Elementliste und Zählern. Er enthält keine
  Zugangsdaten, weil die Integration keine besitzt.
- Deutsche und englische Übersetzungen, Deutsch als Primärsprache.
- 324 Tests bei 98 Prozent Abdeckung, darunter ein Nachbau der
  Referenzanlage und Regressionstests für jeden bekannten Fallstrick.

### Bekannte Grenzen

- Die Farbtemperatur wird vom Gateway nicht zurückgemeldet und deshalb blind
  geführt.
- Units mit mehreren DALI-Treibern melden nur einen Mischwert, die einzelnen
  Treiber werden daher blind bedient.
- Casambi-Sensoren und -Taster brauchen neuere Firmware auf den Leuchten und
  sind noch nicht umgesetzt.

### Aus der Code-Durchsicht behoben

Fünf unabhängige Prüfungen des fertigen Codes, eine davon mit 45 gezielt
eingebauten Fehlern zur Messung der Testreihe. Behoben wurden:

- Vier Elementarten teilen sich einen Adressraum. Dieselbe Unit zweimal
  angelegt, etwa als einfache Leuchte und als Leuchte mit Farbtemperatur,
  erzeugte zwei Entitäten mit derselben Kennung; Home Assistant verwarf die
  zweite stillschweigend.
- Beide Reparaturhinweise blieben nach dem Entfernen der Integration für
  immer stehen.
- Das Verkleinern der Treiberzahl einer Unit ließ die verschwundenen
  Entitäten dauerhaft als nicht verfügbar zurück.
- Einschalten ohne Helligkeitsangabe sprang auf volle Helligkeit, statt den
  vom Gateway gemeldeten Wert zu verwenden.
- Der Blinktest ließ die Leuchte auf voller Helligkeit stehen, wenn der
  Einrichtungsdialog währenddessen geschlossen wurde.
- Ein fehlgeschlagenes MQTT-Abonnement verschwand spurlos, und der
  Diagnose-Export meldete das Thema trotzdem als abonniert.
- Das Bearbeiten eines unlesbar gewordenen Elements brach mit einem Fehler
  ab, obwohl das der einzige Weg aus diesem Zustand ist.
- Der Parser stürzte bei den JSON-Werten für Unendlich ab, statt die
  Nachricht wie jede andere unbrauchbare zu verwerfen.

Die Testreihe wurde um Prüfungen auf Leitungsebene erweitert, die für jede
Entitätsart die vollständige Liste der gesendeten Nachrichten vergleichen,
sowie um eine Vertragsreihe, die dieselben Zusagen gegen das echte Gateway und
gegen die Test-Attrappe prüft.
