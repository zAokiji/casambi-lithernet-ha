# Recorded gateway payloads

Every file here is a message the Lithernet gateway (firmware 4.35, bridge 0)
actually published. They come from **two** captures of the reference
installation, which is why the `last_change` counter falls into two bands:

- around 54800, from the 37 second capture on 2026-09-05 that section 12.3 of
  the project document lists field by field
- around 86300, from the capture on 2026-09-04 that was taken while
  investigating why every luminaire had been dimmed to one percent

Both are real. Do not "harmonise" the counter: it is the gateway's own uptime
in seconds and carries no meaning for the parser.

Two files are edited rather than recorded, because the installation was
healthy at the time:

- `unit_properties_lamp_failure.json` — `condition` 130 (0x82), a general
  failure and an ambient temperature, to exercise the diagnostic path.
- `scene_values_active.json` / `group_values_on.json` — an active scene and a
  group that is on.

Everything else is verbatim. If the gateway ever changes its format, the
parser tests fail here first, which is the point.
