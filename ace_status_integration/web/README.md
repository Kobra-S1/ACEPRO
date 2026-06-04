# ACE Dashboard (Mainsail/Fluidd compatible)

Static files for the ACE dashboard. Symlink them into the directory your UI serves:

- `ace.html`
- `ace-dashboard.css`
- `ace-dashboard.js`
- `ace-dashboard-config.js`
- `vue.global.prod.js`
- `favicon.svg`
- (optional) `ace_dashboard.nginx.conf` sample

Examples:
- Mainsail: `ln -s /path/to/repo/ace_status_integration/web/ace.* ~/mainsail/ && ln -s /path/to/repo/ace_status_integration/web/vue.global.prod.js ~/mainsail/`
- Fluidd: `ln -s /path/to/repo/ace_status_integration/web/ace.* ~/fluidd/ && ln -s /path/to/repo/ace_status_integration/web/vue.global.prod.js ~/fluidd/`

Open `http://<host>/ace.html` after linking. Adjust `ace-dashboard-config.js` if you need a fixed API host.
