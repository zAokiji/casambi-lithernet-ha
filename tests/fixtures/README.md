# Recorded gateway payloads

Every file here is a message the Lithernet gateway (firmware 4.35, bridge 0)
actually published, captured on 2026-09-05 from the reference installation.
Two files are edited rather than recorded, because the installation was
healthy at the time:

- `unit_properties_lamp_failure.json` — `condition` 130 (0x82), a general
  failure and an ambient temperature, to exercise the diagnostic path.
- `scene_values_active.json` / `group_values_on.json` — an active scene and a
  group that is on.

Everything else is verbatim. If the gateway ever changes its format, the
parser tests fail here first, which is the point.
