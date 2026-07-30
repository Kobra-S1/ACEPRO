# =============================================================================
# ACE Pro Installer - path discovery and component status detection
# =============================================================================
# Sourced by installer.sh - not executable on its own.
# =============================================================================

# Fill in default paths for everything the installer touches. Values that were
# already set (e.g. via command line flags) are kept.
detect_paths() {
    KLIPPER_DIR="${KLIPPER_DIR:-$INSTALL_HOME/klipper}"
    CONFIG_DIR="${CONFIG_DIR:-$INSTALL_HOME/printer_data/config}"
    MOONRAKER_DIR="${MOONRAKER_DIR:-$INSTALL_HOME/moonraker}"
    MOONRAKER_CONF="${MOONRAKER_CONF:-$CONFIG_DIR/moonraker.conf}"
    MAINSAIL_DIR="${MAINSAIL_DIR:-$INSTALL_HOME/mainsail}"
    FLUIDD_DIR="${FLUIDD_DIR:-$INSTALL_HOME/fluidd}"
    KLIPPERSCREEN_ROOT_DIR="${KLIPPERSCREEN_ROOT_DIR:-$INSTALL_HOME/KlipperScreen}"

    BACKUP_ROOT="$CONFIG_DIR/acepro_backups"
}

# Discover available printer models from config/printer_*.cfg
discover_printers() {
    PRINTER_MODELS=()
    local f base model
    for f in "$SCRIPT_DIR"/config/printer_*.cfg; do
        [ -f "$f" ] || continue
        base=$(basename "$f" .cfg)
        model="${base#printer_}"
        [ "$model" = "generic_macros" ] && continue
        PRINTER_MODELS+=("$model")
    done
}

printer_display_name() {
    case "$1" in
        K3)  echo "Kobra 3" ;;
        KS1) echo "Kobra S1" ;;
        K3M) echo "Kobra K3M (BETA)" ;;
        KS1M) echo "Kobra S1 Max (ALPHA - UNTESTED)" ;;
        *)   echo "$1" ;;
    esac
}

# Maturity warning for a printer model. Echoes nothing for models that have
# been validated on real hardware; otherwise echoes one warning line per call.
printer_maturity_warning() {
    case "$1" in
        K3M)
            echo "The K3M config is BETA - verified only partially on real hardware."
            ;;
        KS1M)
            echo "The Kobra S1 Max (KS1M) config is ALPHA and has NEVER been run on a printer."
            echo "It was derived on paper from the stock go-klipper printer.cfg. Pin"
            echo "assignments, bed mesh exclusion zones, the toolhead encoder polarity,"
            echo "the cutter/purge coordinates and the ACE tube lengths are all UNVERIFIED."
            echo "Expect crashes into the frame, false filament runouts and failed loads."
            echo "Review every section against your machine, and keep a hand on the power"
            echo "switch during the first homing, first cut and first purge."
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Component status detection
# ---------------------------------------------------------------------------
# Sets the ST_* variables used by the menus. Each is a short colored string.

_st_ok()      { printf '%s%s%s' "$GREEN" "$1" "$NC"; }
_st_warn()    { printf '%s%s%s' "$YELLOW" "$1" "$NC"; }
_st_missing() { printf '%s%s%s' "$RED" "$1" "$NC"; }

detect_status() {
    # Driver symlinks in klipper extras
    local ace_target="$KLIPPER_DIR/klippy/extras/ace"
    if [ -L "$ace_target" ]; then
        ST_DRIVER=$(_st_ok "Installed")
    elif [ -e "$ace_target" ]; then
        ST_DRIVER=$(_st_warn "Present (copy)")
    else
        ST_DRIVER=$(_st_missing "Not installed")
    fi

    # Generic config files
    local generic_count=0
    [ -f "$CONFIG_DIR/printer_generic_macros.cfg" ] && (( generic_count++ ))
    [ -f "$CONFIG_DIR/ace_macros_generic.cfg" ] && (( generic_count++ ))
    case "$generic_count" in
        2) ST_GENERIC=$(_st_ok "Installed") ;;
        1) ST_GENERIC=$(_st_warn "Partial") ;;
        *) ST_GENERIC=$(_st_missing "Not installed") ;;
    esac

    # printer.cfg (we cannot tell whose it is, only whether one exists)
    if [ -f "$CONFIG_DIR/printer.cfg" ]; then
        ST_PRINTERCFG=$(_st_ok "Present")
    else
        ST_PRINTERCFG=$(_st_missing "Missing")
    fi

    # Printer specific ACE config
    if [ -z "$PRINTER_MODEL" ]; then
        ST_ACECFG="-"
    elif [ -f "$CONFIG_DIR/ace_${PRINTER_MODEL}.cfg" ]; then
        ST_ACECFG=$(_st_ok "Installed")
    else
        ST_ACECFG=$(_st_missing "Not installed")
    fi

    # Spoolman integration config
    if [ ! -f "$CONFIG_DIR/spoolman_logic.cfg" ]; then
        ST_SPOOLMAN=$(_st_missing "Not installed")
    elif grep -qE '^[[:space:]]*\[include spoolman_logic\.cfg\]' "$CONFIG_DIR/printer.cfg" 2>/dev/null; then
        ST_SPOOLMAN=$(_st_ok "Installed")
    else
        ST_SPOOLMAN=$(_st_warn "Not included")
    fi

    # Moonraker ACE status component
    local mr_components="$MOONRAKER_DIR/moonraker/components"
    if [ ! -d "$mr_components" ]; then
        ST_MOONRAKER=$(_st_missing "Not found")
    else
        local mr_count=0
        [ -e "$mr_components/ace_status.py" ] && (( mr_count++ ))
        [ -f "$MOONRAKER_CONF" ] && grep -qi '^[[:space:]]*\[ace_status\]' "$MOONRAKER_CONF" && (( mr_count++ ))
        case "$mr_count" in
            2) ST_MOONRAKER=$(_st_ok "Installed") ;;
            1) ST_MOONRAKER=$(_st_warn "Partial") ;;
            *) ST_MOONRAKER=$(_st_missing "Not installed") ;;
        esac
    fi

    # Dashboards
    if [ ! -d "$MAINSAIL_DIR" ]; then
        ST_MAINSAIL=$(_st_missing "Not found")
    elif [ -e "$MAINSAIL_DIR/ace.html" ]; then
        ST_MAINSAIL=$(_st_ok "Installed")
    else
        ST_MAINSAIL=$(_st_missing "Not installed")
    fi

    if [ ! -d "$FLUIDD_DIR" ]; then
        ST_FLUIDD=$(_st_missing "Not found")
    elif [ -e "$FLUIDD_DIR/ace.html" ]; then
        ST_FLUIDD=$(_st_ok "Installed")
    else
        ST_FLUIDD=$(_st_missing "Not installed")
    fi

    # KlipperScreen: panel symlink + core patch
    if [ ! -d "$KLIPPERSCREEN_ROOT_DIR" ]; then
        ST_KSCREEN=$(_st_missing "Not found")
    else
        local ks_panel="$KLIPPERSCREEN_ROOT_DIR/panels/acepro.py"
        local ks_screen_py="$KLIPPERSCREEN_ROOT_DIR/screen.py"
        local ks_patched=0
        if [ -f "$ks_screen_py" ] && grep -q "_ace_subscription_objects" "$ks_screen_py"; then
            ks_patched=1
        fi
        if [ -e "$ks_panel" ] && [ "$ks_patched" -eq 1 ]; then
            ST_KSCREEN=$(_st_ok "Patched")
        elif [ -e "$ks_panel" ] || [ "$ks_patched" -eq 1 ]; then
            ST_KSCREEN=$(_st_warn "Partial")
        else
            ST_KSCREEN=$(_st_missing "Not installed")
        fi
    fi
}
