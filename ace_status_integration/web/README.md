# ACE Dashboard (Mainsail/Fluidd compatible)

Static files for the ACE dashboard. Symlink them into the directory your UI serves:

- `ace.html`
- `ace-dashboard.css`
- `ace-dashboard.js`
- `ace-dashboard-config.js`
- `favicon.svg`
- (optional) `ace_dashboard.nginx.conf` sample

Examples:
- Mainsail: `ln -s /path/to/repo/ace_status_integration/web/ace.* ~/mainsail/`
- Fluidd: `ln -s /path/to/repo/ace_status_integration/web/ace.* ~/fluidd/`

Open `http://<host>/ace.html` after linking. Adjust `ace-dashboard-config.js` if you need a fixed API host.
