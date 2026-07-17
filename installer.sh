#!/bin/bash

# =============================================================================
# ACE Pro Klipper Driver - Interactive Installer
# =============================================================================
# KIAUH-style menu installer for the ACE Pro driver: select the printer model
# first, then pick the components to install or update. Every replaced file is
# backed up into <config>/acepro_backups/<timestamp>/ instead of scattering
# *.backup_* files through the config directory.
#
# Compatible with: Raspberry Pi OS, Debian, Ubuntu
#
# Interactive usage:      ./installer.sh
# Non-interactive usage:  ./installer.sh --printer K3 --all [--restart]
#                         ./installer.sh --printer K3 --components driver,ace-config
#
# =============================================================================

set -u  # Exit on undefined variables

# Script directory (where this script is located)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Resolve installation user/home for defaults (works when run via sudo).
INSTALL_USER="${SUDO_USER:-$(id -un)}"
INSTALL_HOME="$(getent passwd "$INSTALL_USER" 2>/dev/null | cut -d: -f6 || true)"
if [ -z "$INSTALL_HOME" ]; then
    INSTALL_HOME="$HOME"
fi

source "$SCRIPT_DIR/installer/ui.sh"
source "$SCRIPT_DIR/installer/detect.sh"
source "$SCRIPT_DIR/installer/backup.sh"
source "$SCRIPT_DIR/installer/steps.sh"

# ============================================================================
# Global state
# ============================================================================

PRINTER_MODEL=""
PRINTER_NAME=""
INTERACTIVE=1
RESTART_SERVICES=0

# Component selection flags (1 = install/update, 0 = skip)
SEL_DRIVER=0
SEL_GENERIC=0
SEL_PRINTERCFG=0
SEL_ACECFG=0
SEL_SPOOLMAN=0
SEL_MOONRAKER=0
SEL_MAINSAIL=0
SEL_FLUIDD=0
SEL_KSCREEN=0

# Paths may be overridden via command line before detect_paths() fills defaults
KLIPPER_DIR="${KLIPPER_DIR:-}"
CONFIG_DIR="${CONFIG_DIR:-}"
MOONRAKER_DIR="${MOONRAKER_DIR:-}"
MOONRAKER_CONF="${MOONRAKER_CONF:-}"
MAINSAIL_DIR="${MAINSAIL_DIR:-}"
FLUIDD_DIR="${FLUIDD_DIR:-}"
KLIPPERSCREEN_ROOT_DIR="${KLIPPERSCREEN_ROOT_DIR:-}"

# Per-run result tracking for the summary screen
RUN_OK=()
RUN_FAILED=()

# ============================================================================
# Command line parsing
# ============================================================================

usage() {
    cat << EOF
ACE Pro Klipper Driver Installer

Usage: $0 [options]

Without options an interactive menu is shown.

Options:
  --printer MODEL          Printer model (e.g. K3, KS1, K3M)
  --all                    Select all components (non-interactive install)
  --components LIST        Comma separated component list (non-interactive):
                             driver, generic, printer-config, ace-config,
                             spoolman, moonraker, mainsail, fluidd,
                             klipperscreen
  --restart                Restart touched services after install (non-interactive)
  --klipper-dir DIR        Klipper directory        (default: ~/klipper)
  --config-dir DIR         Klipper config directory (default: ~/printer_data/config)
  --moonraker-dir DIR      Moonraker directory      (default: ~/moonraker)
  --moonraker-conf FILE    moonraker.conf path      (default: <config-dir>/moonraker.conf)
  --mainsail-dir DIR       Mainsail directory       (default: ~/mainsail)
  --fluidd-dir DIR         Fluidd directory         (default: ~/fluidd)
  --klipperscreen-dir DIR  KlipperScreen directory  (default: ~/KlipperScreen)
  -h, --help               Show this help
EOF
}

select_component() {
    case "$1" in
        driver)         SEL_DRIVER=1 ;;
        generic)        SEL_GENERIC=1 ;;
        printer-config) SEL_PRINTERCFG=1 ;;
        ace-config)     SEL_ACECFG=1 ;;
        spoolman)       SEL_SPOOLMAN=1 ;;
        moonraker)      SEL_MOONRAKER=1 ;;
        mainsail)       SEL_MAINSAIL=1 ;;
        fluidd)         SEL_FLUIDD=1 ;;
        klipperscreen)  SEL_KSCREEN=1 ;;
        *)
            print_error "Unknown component: $1"
            exit 1
            ;;
    esac
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --printer)
                PRINTER_MODEL="$2"; shift 2 ;;
            --all)
                INTERACTIVE=0
                SEL_DRIVER=1 SEL_GENERIC=1 SEL_PRINTERCFG=1 SEL_ACECFG=1
                SEL_SPOOLMAN=1 SEL_MOONRAKER=1 SEL_MAINSAIL=1 SEL_FLUIDD=1
                SEL_KSCREEN=1
                shift ;;
            --components)
                INTERACTIVE=0
                local comp
                IFS=',' read -ra comp <<< "$2"
                local c
                for c in "${comp[@]}"; do
                    select_component "$c"
                done
                shift 2 ;;
            --restart)
                RESTART_SERVICES=1; shift ;;
            --klipper-dir)       KLIPPER_DIR="$2"; shift 2 ;;
            --config-dir)        CONFIG_DIR="$2"; shift 2 ;;
            --moonraker-dir)     MOONRAKER_DIR="$2"; shift 2 ;;
            --moonraker-conf)    MOONRAKER_CONF="$2"; shift 2 ;;
            --mainsail-dir)      MAINSAIL_DIR="$2"; shift 2 ;;
            --fluidd-dir)        FLUIDD_DIR="$2"; shift 2 ;;
            --klipperscreen-dir) KLIPPERSCREEN_ROOT_DIR="$2"; shift 2 ;;
            -h|--help)
                usage; exit 0 ;;
            *)
                print_error "Unknown option: $1"
                usage
                exit 1 ;;
        esac
    done
}

# ============================================================================
# Menus
# ============================================================================

set_printer() {
    local model="$1"
    PRINTER_MODEL="$model"
    PRINTER_NAME=$(printer_display_name "$model")
}

printer_menu() {
    discover_printers

    if [ "${#PRINTER_MODELS[@]}" -eq 0 ]; then
        print_error "No printer configs found in $SCRIPT_DIR/config/"
        pause_for_key
        return 1
    fi

    while true; do
        ui_clear
        box_top
        box_center "${CYAN}Select Printer${NC}"
        box_sep
        local i
        for i in "${!PRINTER_MODELS[@]}"; do
            box_line "$(printf '%2d) %s' "$((i + 1))" "$(printer_display_name "${PRINTER_MODELS[$i]}")")"
        done
        box_sep
        box_line "B) Back"
        box_bottom

        local choice
        read -r -p "Select: " choice
        case "$choice" in
            [bB]) return 1 ;;
            *)
                if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le "${#PRINTER_MODELS[@]}" ]; then
                    set_printer "${PRINTER_MODELS[$((choice - 1))]}"
                    return 0
                fi
                ;;
        esac
    done
}

# Preselect components based on what is present on this system
default_selection() {
    SEL_DRIVER=1
    SEL_GENERIC=1
    SEL_ACECFG=1

    # Replacing printer.cfg is the destructive one - default OFF when one
    # already exists (update scenario), ON on a fresh install.
    if [ -f "$CONFIG_DIR/printer.cfg" ]; then
        SEL_PRINTERCFG=0
    else
        SEL_PRINTERCFG=1
    fi

    # Spoolman is opt-in infrastructure - preselect only when already installed
    [ -f "$CONFIG_DIR/spoolman_logic.cfg" ] && SEL_SPOOLMAN=1 || SEL_SPOOLMAN=0

    [ -d "$MOONRAKER_DIR/moonraker/components" ] && SEL_MOONRAKER=1 || SEL_MOONRAKER=0
    [ -d "$MAINSAIL_DIR" ] && SEL_MAINSAIL=1 || SEL_MAINSAIL=0
    [ -d "$FLUIDD_DIR" ] && SEL_FLUIDD=1 || SEL_FLUIDD=0
    [ -d "$KLIPPERSCREEN_ROOT_DIR" ] && SEL_KSCREEN=1 || SEL_KSCREEN=0
}

_checkbox() {
    if [ "$1" -eq 1 ]; then
        printf '%s[x]%s' "$GREEN" "$NC"
    else
        printf '[ ]'
    fi
}

_toggle() {
    local var="$1"
    if [ "${!var}" -eq 1 ]; then
        printf -v "$var" 0
    else
        printf -v "$var" 1
    fi
}

# Component selection screen. Returns 0 = run install, 1 = back.
component_menu() {
    while true; do
        detect_status

        ui_clear
        box_top
        box_center "${CYAN}Install / Update — $PRINTER_NAME${NC}"
        box_sep
        box_cols "1) $(_checkbox $SEL_DRIVER) ACE driver" "$ST_DRIVER" 34
        box_cols "2) $(_checkbox $SEL_GENERIC) Generic config files" "$ST_GENERIC" 34
        box_cols "3) $(_checkbox $SEL_PRINTERCFG) printer.cfg (${PRINTER_MODEL})" "$ST_PRINTERCFG" 34
        box_cols "4) $(_checkbox $SEL_ACECFG) ACE config (ace_${PRINTER_MODEL}.cfg)" "$ST_ACECFG" 34
        box_cols "5) $(_checkbox $SEL_SPOOLMAN) Spoolman integration" "$ST_SPOOLMAN" 34
        box_cols "6) $(_checkbox $SEL_MOONRAKER) Moonraker ACE status" "$ST_MOONRAKER" 34
        box_cols "7) $(_checkbox $SEL_MAINSAIL) Dashboard (Mainsail)" "$ST_MAINSAIL" 34
        box_cols "8) $(_checkbox $SEL_FLUIDD) Dashboard (Fluidd)" "$ST_FLUIDD" 34
        box_cols "9) $(_checkbox $SEL_KSCREEN) KlipperScreen" "$ST_KSCREEN" 34
        box_sep
        box_line "Selected components are backed up and replaced."
        box_line "printer.cfg replaces your whole printer config -"
        box_line "only select it if you want the stock ${PRINTER_MODEL} config."
        box_sep
        box_line "A) Toggle all    I) Install selected    B) Back"
        box_bottom

        local key
        key=$(read_key)
        case "$key" in
            1) _toggle SEL_DRIVER ;;
            2) _toggle SEL_GENERIC ;;
            3) _toggle SEL_PRINTERCFG ;;
            4) _toggle SEL_ACECFG ;;
            5) _toggle SEL_SPOOLMAN ;;
            6) _toggle SEL_MOONRAKER ;;
            7) _toggle SEL_MAINSAIL ;;
            8) _toggle SEL_FLUIDD ;;
            9) _toggle SEL_KSCREEN ;;
            [aA])
                local all=1
                if [ "$SEL_DRIVER" -eq 1 ] && [ "$SEL_GENERIC" -eq 1 ] && \
                   [ "$SEL_PRINTERCFG" -eq 1 ] && [ "$SEL_ACECFG" -eq 1 ] && \
                   [ "$SEL_SPOOLMAN" -eq 1 ] && \
                   [ "$SEL_MOONRAKER" -eq 1 ] && [ "$SEL_MAINSAIL" -eq 1 ] && \
                   [ "$SEL_FLUIDD" -eq 1 ] && [ "$SEL_KSCREEN" -eq 1 ]; then
                    all=0
                fi
                SEL_DRIVER=$all SEL_GENERIC=$all SEL_PRINTERCFG=$all SEL_ACECFG=$all
                SEL_SPOOLMAN=$all SEL_MOONRAKER=$all SEL_MAINSAIL=$all SEL_FLUIDD=$all
                SEL_KSCREEN=$all
                ;;
            [iI])
                if [ "$SEL_DRIVER" -eq 0 ] && [ "$SEL_GENERIC" -eq 0 ] && \
                   [ "$SEL_PRINTERCFG" -eq 0 ] && [ "$SEL_ACECFG" -eq 0 ] && \
                   [ "$SEL_SPOOLMAN" -eq 0 ] && \
                   [ "$SEL_MOONRAKER" -eq 0 ] && [ "$SEL_MAINSAIL" -eq 0 ] && \
                   [ "$SEL_FLUIDD" -eq 0 ] && [ "$SEL_KSCREEN" -eq 0 ]; then
                    print_warning "Nothing selected"
                    pause_for_key
                else
                    return 0
                fi
                ;;
            [bB]) return 1 ;;
        esac
    done
}

paths_menu() {
    while true; do
        ui_clear
        box_top
        box_center "${CYAN}Paths${NC}"
        box_sep
        box_cols "1) Klipper dir" "${KLIPPER_DIR/#$INSTALL_HOME/\~}" 22
        box_cols "2) Config dir" "${CONFIG_DIR/#$INSTALL_HOME/\~}" 22
        box_cols "3) Moonraker dir" "${MOONRAKER_DIR/#$INSTALL_HOME/\~}" 22
        box_cols "4) moonraker.conf" "${MOONRAKER_CONF/#$INSTALL_HOME/\~}" 22
        box_cols "5) Mainsail dir" "${MAINSAIL_DIR/#$INSTALL_HOME/\~}" 22
        box_cols "6) Fluidd dir" "${FLUIDD_DIR/#$INSTALL_HOME/\~}" 22
        box_cols "7) KlipperScreen dir" "${KLIPPERSCREEN_ROOT_DIR/#$INSTALL_HOME/\~}" 22
        box_sep
        box_line "B) Back"
        box_bottom

        local choice
        read -r -p "Select: " choice
        case "$choice" in
            1) KLIPPER_DIR=$(prompt_input "Klipper directory" "$KLIPPER_DIR") ;;
            2)
                CONFIG_DIR=$(prompt_input "Config directory" "$CONFIG_DIR")
                MOONRAKER_CONF="$CONFIG_DIR/moonraker.conf"
                BACKUP_ROOT="$CONFIG_DIR/acepro_backups"
                ;;
            3) MOONRAKER_DIR=$(prompt_input "Moonraker directory" "$MOONRAKER_DIR") ;;
            4) MOONRAKER_CONF=$(prompt_input "moonraker.conf path" "$MOONRAKER_CONF") ;;
            5) MAINSAIL_DIR=$(prompt_input "Mainsail directory" "$MAINSAIL_DIR") ;;
            6) FLUIDD_DIR=$(prompt_input "Fluidd directory" "$FLUIDD_DIR") ;;
            7) KLIPPERSCREEN_ROOT_DIR=$(prompt_input "KlipperScreen directory" "$KLIPPERSCREEN_ROOT_DIR") ;;
            [bB]|"") return 0 ;;
        esac
    done
}

main_menu() {
    while true; do
        detect_status
        local printer_label legacy_count
        if [ -n "$PRINTER_MODEL" ]; then
            printer_label="${GREEN}${PRINTER_NAME}${NC}"
        else
            printer_label="${YELLOW}(none selected)${NC}"
        fi
        legacy_count=$(count_legacy_backups)

        ui_clear
        box_top
        box_center "${CYAN}ACE Pro Driver — Installer${NC}"
        box_sep
        box_cols "Printer:" "$printer_label" 12
        box_cols "Config dir:" "${CONFIG_DIR/#$INSTALL_HOME/\~}" 12
        box_sep
        box_cols "1) Install / Update" "Driver:         $ST_DRIVER" 24
        box_cols "2) Select printer" "Generic config: $ST_GENERIC" 24
        box_cols "3) Backups" "printer.cfg:    $ST_PRINTERCFG" 24
        box_cols "4) Paths" "ACE config:     $ST_ACECFG" 24
        box_cols "" "Spoolman:       $ST_SPOOLMAN" 24
        box_cols "" "Moonraker:      $ST_MOONRAKER" 24
        box_cols "" "Mainsail dash:  $ST_MAINSAIL" 24
        box_cols "" "Fluidd dash:    $ST_FLUIDD" 24
        box_cols "" "KlipperScreen:  $ST_KSCREEN" 24
        if [ "$legacy_count" -gt 0 ]; then
            box_sep
            box_line "${YELLOW}$legacy_count old *.backup_* file(s) in config root${NC}"
            box_line "${YELLOW}→ see Backups menu to migrate them${NC}"
        fi
        box_sep
        box_line "Q) Quit"
        box_bottom

        local choice
        read -r -p "Select: " choice
        case "$choice" in
            1)
                if [ -z "$PRINTER_MODEL" ]; then
                    printer_menu || continue
                fi
                default_selection
                if component_menu; then
                    run_install
                    pause_for_key
                fi
                ;;
            2) printer_menu || true ;;
            3) backups_menu ;;
            4) paths_menu ;;
            [qQ])
                print_success "Happy printing!"
                exit 0
                ;;
        esac
    done
}

# ============================================================================
# Install runner
# ============================================================================

_run_step() {
    local label="$1" fn="$2"
    if "$fn"; then
        RUN_OK+=("$label")
    else
        RUN_FAILED+=("$label")
    fi
}

run_install() {
    RUN_OK=()
    RUN_FAILED=()
    BACKUP_RUN_DIR=""
    TOUCHED_KLIPPER=0
    TOUCHED_MOONRAKER=0
    TOUCHED_KLIPPERSCREEN=0

    # Basic validation
    if [ ! -d "$CONFIG_DIR" ]; then
        print_error "Config directory not found: $CONFIG_DIR"
        return 1
    fi
    if [ "$SEL_DRIVER" -eq 1 ] && [ ! -d "$KLIPPER_DIR" ]; then
        print_error "Klipper directory not found: $KLIPPER_DIR"
        return 1
    fi
    if { [ "$SEL_PRINTERCFG" -eq 1 ] || [ "$SEL_ACECFG" -eq 1 ]; } && [ -z "$PRINTER_MODEL" ]; then
        print_error "No printer model selected (needed for printer.cfg / ACE config)"
        return 1
    fi

    [ "$SEL_DRIVER" -eq 1 ]     && _run_step "ACE driver" step_driver
    [ "$SEL_GENERIC" -eq 1 ]    && _run_step "Generic config files" step_generic_config
    [ "$SEL_PRINTERCFG" -eq 1 ] && _run_step "printer.cfg ($PRINTER_MODEL)" step_printer_config
    [ "$SEL_ACECFG" -eq 1 ]     && _run_step "ACE config (ace_${PRINTER_MODEL}.cfg)" step_ace_config
    [ "$SEL_SPOOLMAN" -eq 1 ]   && _run_step "Spoolman integration" step_spoolman
    [ "$SEL_MOONRAKER" -eq 1 ]  && _run_step "Moonraker ACE status" step_moonraker
    [ "$SEL_MAINSAIL" -eq 1 ]   && _run_step "Dashboard (Mainsail)" step_dashboard_mainsail
    [ "$SEL_FLUIDD" -eq 1 ]     && _run_step "Dashboard (Fluidd)" step_dashboard_fluidd
    [ "$SEL_KSCREEN" -eq 1 ]    && _run_step "KlipperScreen" step_klipperscreen

    step_restart
    show_summary
    return 0
}

show_summary() {
    print_header "Installation Summary"

    local label
    for label in "${RUN_OK[@]}"; do
        print_success "$label"
    done
    for label in "${RUN_FAILED[@]}"; do
        print_error "$label (failed or skipped - see output above)"
    done

    if [ -n "$BACKUP_RUN_DIR" ]; then
        echo ""
        print_info "Replaced files were backed up to:"
        echo "  $BACKUP_RUN_DIR"
        if [ -f "$BACKUP_RUN_DIR/printer.cfg" ]; then
            echo ""
            print_warning "Your previous printer.cfg is in that backup folder."
            echo "  Manually review and merge any custom changes (macros, hardware"
            echo "  settings, personal customizations) into the new printer.cfg."
        fi
    fi

    cat << EOF

Next steps:
EOF
    if [ "$SEL_ACECFG" -eq 1 ] && [ -n "$PRINTER_MODEL" ]; then
        cat << EOF
  - Review and customize the ACE configuration:
      $CONFIG_DIR/ace_${PRINTER_MODEL}.cfg
      (ace_count, feed/retract speeds, sensor pins)
EOF
    fi
    if [ "$SEL_GENERIC" -eq 1 ]; then
        cat << EOF
  - Review printer_generic_macros.cfg / ace_macros_generic.cfg if you plan
    to customize pause/resume, purge helpers or ACE hooks.
EOF
    fi
    if [ "$SEL_SPOOLMAN" -eq 1 ]; then
        cat << EOF
  - Spoolman: make sure Spoolman is configured in moonraker.conf and add
    '[include spoolman_logic.cfg]' to printer.cfg BEFORE the ACE include.
    See README.md, Spoolman Integration.
EOF
    fi
    cat << EOF
  - Test basic commands in the Klipper console, e.g. ACE_GET_STATUS
  - Optional but recommended - set inventory for each tool:
      ACE_SET_SLOT INSTANCE=0 INDEX=0 COLOR=255,0,0 MATERIAL=PLA TEMP=210
  - If using Orca Slicer: install orca_flush_to_purgelength.py on your host
    PC (see README.md, Orca Slicer integration).

EOF
}

# ============================================================================
# Entry Point
# ============================================================================

main() {
    parse_args "$@"
    detect_paths
    discover_printers

    if [ -n "$PRINTER_MODEL" ]; then
        # Validate a model given via --printer
        local found=0 m
        for m in "${PRINTER_MODELS[@]}"; do
            [ "$m" = "$PRINTER_MODEL" ] && found=1
        done
        if [ "$found" -eq 0 ]; then
            print_error "Unknown printer model: $PRINTER_MODEL (available: ${PRINTER_MODELS[*]})"
            exit 1
        fi
        set_printer "$PRINTER_MODEL"
    fi

    if [ "$INTERACTIVE" -eq 0 ]; then
        run_install
        [ "${#RUN_FAILED[@]}" -gt 0 ] && exit 1
        exit 0
    fi

    if [ ! -t 0 ]; then
        print_error "No terminal available. Use --printer/--components/--all for non-interactive mode."
        usage
        exit 1
    fi

    main_menu
}

if [ "${BASH_SOURCE[0]}" == "${0}" ]; then
    main "$@"
fi
