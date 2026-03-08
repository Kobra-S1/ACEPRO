# ACE Status Integration Assets

This folder keeps the Moonraker component and the standalone ACE dashboard so they can be symlinked into Moonraker, Mainsail, or Fluidd without duplicating files.

Contents:
- `moonraker/ace_status.py` — Moonraker component exposing `/server/ace/status`, `/server/ace/slots`, and `/server/ace/command`.
- `web/` — static dashboard assets (`ace.html`, `ace-dashboard.js`, `ace-dashboard.css`, `ace-dashboard-config.js`, `favicon.svg`) plus an nginx sample.

Usage (manual):
1) Moonraker: `ln -s /path/to/repo/ace_status_integration/moonraker/ace_status.py ~/moonraker/moonraker/components/ace_status.py`
2) Mainsail/Fluidd (served dir): symlink the four dashboard files into the directory your UI is hosted from, e.g. `~/mainsail/ace.html`.
3) Update `ace-dashboard-config.js` if you need a fixed API base; defaults to the current host.
4) Restart Moonraker after adding the component. Static UI files do not require a restart unless your web server needs a reload.
