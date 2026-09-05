# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden in dieser Datei
festgehalten. Das Format folgt [Keep a Changelog](https://keepachangelog.com/de/1.1.0/).

## [Unreleased]

### Hinzugefügt

- Grundgerüst der Integration: Domain `casambi_lithernet`, Manifest,
  Konstanten, Datenmodell und die abstrakte Schnittstelle des
  Gateway-Objekts.
- Übersetzungsschlüssel für Config Flow, Subentry-Flows, Optionen und
  Reparaturhinweise, Deutsch und Englisch.
- Test-Infrastruktur auf Basis von
  `pytest-homeassistant-custom-component` mit MQTT-Mock.
- CI-Workflow mit Tests, hassfest und HACS-Validierung.
- README mit Installationsanleitung, Gateway-Einrichtung, Beschreibung des
  Einrichtungsassistenten, Elementtypen, Diagnose und bekannten Grenzen.
- Migrationsanleitung für die Ablösung der bestehenden YAML-Konfiguration.
