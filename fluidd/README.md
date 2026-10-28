# ACE Pro panel for Fluidd

A native dashboard card for [Fluidd](https://github.com/fluidd-core/fluidd)
that shows the state of the ACE Pro / ACE2 Pro units driven by this
repository's Klipper driver.

![Panel screenshot](docs/panel.png)

## Features

- **Slot grid** — one spool per slot, colored from the driver data (RFID /
  `ACE_SET_SLOT`). Click a spool to load it (`T<n>`, not while printing or
  paused); click the pencil to edit material, nozzle temperature and color.
  Tool numbers come from the driver, so units behind a printer-side tool
  changer show their real `T<n>`.
- **Multi-unit** — supports mixed ACE + ACE2 PRO setups; each unit gets its
  own slot group with model, firmware and connection state.
- **Filament path** — live view `ACE -> [SPLITTER] -> [Encoder] -> [Nozzle]`
  driven by your filament sensors. The splitter (RDM) and nozzle states come
  from the driver under whatever names the `[ace]` config gives them; an
  encoder appears when a sensor named `ace_encoder` / `tracker` exists. Nodes
  appear only for configured sensors; the section hides without sensors.
- **Segment flow animation** — while ACE is loading/unloading, the segment
  between the last sensor that sees filament and the next one flows
  (works in both directions).
- **Jam indicator** — the encoder ring turns red when filament is at the
  nozzle but not at the encoder (jam or break between them).
- **Feed assist toggle** — for the loaded tool's slot
  (`ACE_ENABLE_FEED_ASSIST` / `ACE_DISABLE_FEED_ASSIST INSTANCE= INDEX=`);
  disabled when the loaded tool is not fed by an ACE.
- **Dryer dialog** — per unit: chamber temperature, DRYING/IDLE state with
  remaining time, temperature slider (35 °C up to the unit's
  `max_dryer_temperature`, step 5) and duration slider (30–240 min, step 30).
- Service actions: load all slots, full unload, reconnect, request status.

The card shows up automatically on the Fluidd dashboard when the ACE
driver is present, and hides itself when the ACE is disconnected.

## Install

Run **on the Klipper host**:

```bash
git clone --branch dev https://github.com/Kobra-S1/ACEPRO.git
cd ACEPRO/fluidd
bash install.sh
```

or as a one-liner (the script fetches the panel sources by itself):

```bash
curl -fsSL https://raw.githubusercontent.com/Kobra-S1/ACEPRO/dev/fluidd/install.sh | bash
```

The installer is self-sufficient: it checks Moonraker and the ACE driver
objects in the API, installs Node.js 22 LTS + pnpm if missing, adds a
temporary swap file on low-memory hosts (removed afterwards), builds Fluidd
`FLUIDD_REF` (a tested release tag) with the card patched in and deploys it
to the detected webroot (`~/fluidd`, `/usr/share/fluidd`, `/var/www/fluidd`,
`/opt/fluidd`, `/home/pi/fluidd`). The new build replaces the webroot
directory (its `config.json` is kept); the previous one stays as
`webroot.bak-ace-<timestamp>`. The deployed bundle is verified; on failure
the webroot is rolled back from the backup. It asks for sudo only for the
steps that need root (Node.js, pnpm, swap, a root-owned webroot).

Options (environment variables):

```bash
WEBROOT=/home/pi/fluidd bash install.sh   # custom webroot
FLUIDD_REF=v1.37.7 bash install.sh        # Fluidd release to build
FLUIDD_DIR=~/fluidd-src bash install.sh   # build in an existing Fluidd checkout (kept)
KEEP_REPO=1 bash install.sh               # keep the built repo for debugging
FORCE=1 bash install.sh                   # skip the ACE driver preflight check
NO_SWAP=1 bash install.sh                 # never create a temporary swap file
MOONRAKER_URL=http://192.168.1.10:7125 bash install.sh  # non-default Moonraker
```

Updating = re-running `bash install.sh` (fresh Fluidd clone, fresh backup).

After the install, hard-reload Fluidd in the browser (Ctrl+Shift+R). If the
card does not appear, clear the site data / service worker in
DevTools -> Application -> Storage and reload once more.

## Files

- `src/mixins/ace.ts` — typed accessors over the `printer.ace` /
  `printer.ace_instance_<n>` objects and the filament sensors.
- `src/components/widgets/ace/AceCard.vue` — the dashboard card.
- `apply_fluidd_ace.py` — anchor-based patcher for Fluidd
  (`Dashboard.vue` + layout state); idempotent, fails loudly when the
  anchors do not match.
- `install.sh` — self-sufficient installer (see above).

## License

GPL-3.0 — the card is a derivative work of Fluidd (GPLv3).
