# =============================================================================
# ACE Pro Installer - install steps
# =============================================================================
# One step_* function per selectable component. Selecting a component on the
# component screen means "back up and replace" - the steps themselves do not
# ask per-file questions anymore.
#
# Sourced by installer.sh - not executable on its own.
# =============================================================================

# Set by the steps so the restart step only offers services that were touched
TOUCHED_KLIPPER=0
TOUCHED_MOONRAKER=0
TOUCHED_KLIPPERSCREEN=0

is_symlink() {
    [ -L "$1" ]
}

remove_symlink_if_exists() {
    local path="$1"
    if is_symlink "$path"; then
        local target
        target=$(readlink "$path")
        rm -f "$path"
        print_info "Removed symlink: $path (was pointing to: $target)"
        return 0
    fi
    return 1
}

# Create a symlink, replacing whatever is at the target
create_or_replace_symlink() {
    local source="$1"
    local target="$2"
    local description="$3"

    if [ ! -e "$source" ]; then
        print_error "$source does not exist, skipping symlink for $description"
        return 1
    fi

    mkdir -p "$(dirname "$target")"
    rm -rf "$target"
    ln -sf "$source" "$target"
    print_success "Symlink created: $target → $source"
    return 0
}

# Back up (into the run folder) and install a config file. Symlink targets
# are replaced with a real local copy.
install_config_file() {
    local source="$1"
    local target="$2"

    if [ ! -f "$source" ]; then
        print_error "Source file not found: $source"
        return 1
    fi

    if [ -f "$target" ] || is_symlink "$target"; then
        backup_file "$target"
        remove_symlink_if_exists "$target"
    fi

    mkdir -p "$(dirname "$target")"
    cp "$source" "$target"
    print_success "Installed: $source → $target"
    return 0
}

# ---------------------------------------------------------------------------
# Config file editing helpers (moonraker.conf / KlipperScreen.conf)
# ---------------------------------------------------------------------------

# Add a KlipperScreen menu entry (handles user-editable section vs auto-generated marker)
ensure_menu_entry() {
    local conf="$1"
    local header="$2"
    local block="$3"
    local label="$4"

    # Extract user-editable section (lines before the #~# auto-generated marker)
    local user_section
    user_section=$(sed '/^#~# --- Do not edit/,$d' "$conf" 2>/dev/null || cat "$conf" 2>/dev/null || true)

    if echo "$user_section" | grep -qF "$header"; then
        print_success "KlipperScreen.conf: $label entry already present"
        return 0
    fi

    print_info "Adding $label entry to $conf"
    local tmpfile
    tmpfile=$(mktemp)

    if grep -q '^#~# --- Do not edit' "$conf" 2>/dev/null; then
        # Insert the block just before the auto-generated marker
        awk -v block="$block" '
            /^#~# --- Do not edit/ && !done {
                print block; print ""; done=1
            }
            { print }
        ' "$conf" > "$tmpfile" && mv "$tmpfile" "$conf"
    else
        # No marker — append at end of file (%b expands the \n in the block)
        printf '\n%b\n' "$block" >> "$conf"
    fi
    print_success "KlipperScreen.conf: added $label entry"
}

# Ensure the ACE Pro menu entry exists in main and print menus
ensure_klipperscreen_acepro_menu() {
    local conf="$1"
    local entry_block_main='[menu __main acepro]\nname: ACE Pro\nicon: settings\npanel: acepro'
    local entry_block_print='[menu __print acepro]\nname: ACE Pro\nicon: settings\npanel: acepro'

    ensure_menu_entry "$conf" "[menu __main acepro]" "$entry_block_main" "ACE Pro (main menu)"
    ensure_menu_entry "$conf" "[menu __print acepro]" "$entry_block_print" "ACE Pro (print menu)"
}

# Ensure [ace_status] section exists in moonraker.conf (create file if missing)
ensure_moonraker_ace_status() {
    local conf="$1"

    if [ -f "$conf" ] && grep -qi '^[[:space:]]*\[ace_status\]' "$conf"; then
        print_success "moonraker.conf: [ace_status] already present"
        return 0
    fi

    mkdir -p "$(dirname "$conf")"
    if [ ! -f "$conf" ]; then
        printf '# Moonraker configuration\n\n' > "$conf"
        print_warning "Created new moonraker.conf at $conf"
    else
        backup_file "$conf"
    fi

    printf '\n# ACE status extension\n[ace_status]\n' >> "$conf"
    print_success "Added [ace_status] to $conf"
}

# Ensure font_size = small is set in the user-editable section of KlipperScreen.conf
# Handles missing file, missing [main] section, wrong value, and #~# auto-generated block.
ensure_klipperscreen_font_size() {
    local conf="$1"

    if [ ! -f "$conf" ]; then
        printf '[main]\nfont_size = small\n' > "$conf"
        print_success "Created $conf with font_size = small"
        return 0
    fi

    # Extract user-editable section (lines before the #~# auto-generated marker)
    local user_section
    user_section=$(sed '/^#~# --- Do not edit/,$d' "$conf")

    # Already correct?
    if echo "$user_section" | grep -qiE '^[[:space:]]*font_size[[:space:]]*=[[:space:]]*small[[:space:]]*$'; then
        print_success "KlipperScreen.conf: font_size = small is already configured"
        return 0
    fi

    print_info "Configuring font_size = small in $conf"
    backup_file "$conf"
    local tmpfile
    tmpfile=$(mktemp)

    if echo "$user_section" | grep -qiE '^[[:space:]]*font_size[[:space:]]*='; then
        # font_size exists with a different value — update first occurrence before #~# block
        awk '
            /^#~# --- Do not edit/ { in_auto=1 }
            !in_auto && /^[[:space:]]*font_size[[:space:]]*=/ && !replaced {
                print "font_size = small"; replaced=1; next
            }
            { print }
        ' "$conf" > "$tmpfile" && mv "$tmpfile" "$conf"
        print_success "KlipperScreen.conf: updated font_size to small"

    elif echo "$user_section" | grep -q '^\[main\]'; then
        # [main] exists in user section but no font_size — insert after first [main]
        awk '
            /^#~# --- Do not edit/ { in_auto=1 }
            !in_auto && /^\[main\]$/ && !done {
                print; print "font_size = small"; done=1; next
            }
            { print }
        ' "$conf" > "$tmpfile" && mv "$tmpfile" "$conf"
        print_success "KlipperScreen.conf: added font_size = small under [main]"

    else
        # No [main] in user section — insert block before #~# marker or prepend
        if grep -q '^#~# --- Do not edit' "$conf"; then
            awk '
                /^#~# --- Do not edit/ && !done {
                    print "[main]"; print "font_size = small"; print ""; done=1
                }
                { print }
            ' "$conf" > "$tmpfile" && mv "$tmpfile" "$conf"
        else
            { printf '[main]\nfont_size = small\n\n'; cat "$conf"; } > "$tmpfile" && mv "$tmpfile" "$conf"
        fi
        print_success "KlipperScreen.conf: added [main] section with font_size = small"
    fi
}

# ---------------------------------------------------------------------------
# Component steps
# ---------------------------------------------------------------------------

# ACE driver: symlink extras into the Klipper checkout
step_driver() {
    print_header "ACE Driver (Klipper extras)"

    local ace_source="$SCRIPT_DIR/extras/ace"
    local ace_target="$KLIPPER_DIR/klippy/extras/ace"
    local vp_source="$SCRIPT_DIR/extras/virtual_pins.py"
    local vp_target="$KLIPPER_DIR/klippy/extras/virtual_pins.py"
    local temp_source="$SCRIPT_DIR/extras/temperature_ace.py"
    local temp_target="$KLIPPER_DIR/klippy/extras/temperature_ace.py"

    if [ ! -d "$KLIPPER_DIR/klippy/extras" ]; then
        print_error "Klipper extras directory not found: $KLIPPER_DIR/klippy/extras"
        return 1
    fi

    create_or_replace_symlink "$ace_source" "$ace_target" "ACE module" || return 1
    create_or_replace_symlink "$vp_source" "$vp_target" "virtual_pins module" || return 1

    if [ -f "$temp_source" ]; then
        create_or_replace_symlink "$temp_source" "$temp_target" "temperature_ace sensor"
    else
        print_warning "temperature_ace.py not found; skipping sensor symlink"
    fi

    TOUCHED_KLIPPER=1
    return 0
}

# Generic config: printer_generic_macros.cfg + ace_macros_generic.cfg
step_generic_config() {
    print_header "Generic Configuration Files"

    install_config_file "$SCRIPT_DIR/config/printer_generic_macros.cfg" \
                        "$CONFIG_DIR/printer_generic_macros.cfg" || return 1
    install_config_file "$SCRIPT_DIR/config/ace_macros_generic.cfg" \
                        "$CONFIG_DIR/ace_macros_generic.cfg" || return 1

    # Legacy filename support (replace old printer_macros_generic.cfg symlinks
    # with a local copy so existing [include] lines keep working)
    local legacy_target="$CONFIG_DIR/printer_macros_generic.cfg"
    if is_symlink "$legacy_target"; then
        print_warning "Legacy printer_macros_generic.cfg symlink detected - replacing with local copy"
        install_config_file "$SCRIPT_DIR/config/printer_generic_macros.cfg" "$legacy_target"
    fi

    TOUCHED_KLIPPER=1
    return 0
}

# Printer specific printer.cfg
step_printer_config() {
    print_header "Printer Configuration (printer.cfg)"

    install_config_file "$SCRIPT_DIR/config/printer_${PRINTER_MODEL}.cfg" \
                        "$CONFIG_DIR/printer.cfg" || return 1

    TOUCHED_KLIPPER=1
    return 0
}

# Printer specific ACE config
step_ace_config() {
    print_header "ACE Configuration (ace_${PRINTER_MODEL}.cfg)"

    install_config_file "$SCRIPT_DIR/config/ace_${PRINTER_MODEL}.cfg" \
                        "$CONFIG_DIR/ace_${PRINTER_MODEL}.cfg" || return 1

    TOUCHED_KLIPPER=1
    return 0
}

# Spoolman integration config (optional - needs Spoolman set up in moonraker.conf)
step_spoolman() {
    print_header "Spoolman Integration (spoolman_logic.cfg)"

    install_config_file "$SCRIPT_DIR/config/spoolman_logic.cfg" \
                        "$CONFIG_DIR/spoolman_logic.cfg" || return 1

    if ! grep -qE '^[[:space:]]*\[include spoolman_logic\.cfg\]' "$CONFIG_DIR/printer.cfg" 2>/dev/null; then
        print_warning "spoolman_logic.cfg is not included from printer.cfg yet."
        print_info "Add '[include spoolman_logic.cfg]' to printer.cfg BEFORE the ACE include."
    fi

    TOUCHED_KLIPPER=1
    return 0
}

# Moonraker ACE status component + [ace_status] section
step_moonraker() {
    print_header "Moonraker ACE Status Component"

    local components_dir="$MOONRAKER_DIR/moonraker/components"
    if [ ! -d "$components_dir" ]; then
        if [ "$INTERACTIVE" -eq 1 ]; then
            print_warning "Moonraker components directory not found: $components_dir"
            MOONRAKER_DIR=$(prompt_input "Moonraker directory" "$MOONRAKER_DIR")
            components_dir="$MOONRAKER_DIR/moonraker/components"
        fi
        if [ ! -d "$components_dir" ]; then
            print_error "Moonraker components directory not found: $components_dir - skipped"
            return 1
        fi
    fi

    create_or_replace_symlink "$SCRIPT_DIR/ace_status_integration/moonraker/ace_status.py" \
                              "$components_dir/ace_status.py" \
                              "ACE status Moonraker component" || return 1

    ensure_moonraker_ace_status "$MOONRAKER_CONF"

    TOUCHED_MOONRAKER=1
    return 0
}

# Dashboard symlinks for Mainsail/Fluidd
_step_dashboard() {
    local name="$1" target_dir="$2"

    print_header "$name Dashboard"

    if [ ! -d "$target_dir" ]; then
        if [ "$INTERACTIVE" -eq 1 ]; then
            print_warning "$name directory not found: $target_dir"
            target_dir=$(prompt_input "$name install directory" "$target_dir")
        fi
        if [ ! -d "$target_dir" ]; then
            print_error "$name directory not found: $target_dir - skipped"
            return 1
        fi
    fi

    local ace_file
    for ace_file in ace.html ace-dashboard.js ace-dashboard.css ace-dashboard-config.js vue.global.prod.js favicon.svg; do
        create_or_replace_symlink "$SCRIPT_DIR/ace_status_integration/web/$ace_file" \
                                  "$target_dir/$ace_file" "$name $ace_file"
    done
    return 0
}

step_dashboard_mainsail() {
    _step_dashboard "Mainsail" "$MAINSAIL_DIR"
}

step_dashboard_fluidd() {
    _step_dashboard "Fluidd" "$FLUIDD_DIR"
}

# KlipperScreen: panel symlink, core patch, KlipperScreen.conf entries
step_klipperscreen() {
    print_header "KlipperScreen Integration"

    local panels_dir="$KLIPPERSCREEN_ROOT_DIR/panels"
    if [ ! -d "$panels_dir" ]; then
        print_error "KlipperScreen panels directory not found: $panels_dir - skipped"
        return 1
    fi

    create_or_replace_symlink "$SCRIPT_DIR/KlipperScreen/acepro.py" \
                              "$panels_dir/acepro.py" "acepro.py panel"

    # Patch KlipperScreen core to subscribe to ACE objects.
    #
    # KlipperScreen's screen.py has changed shape across upstream commits
    # (quote style via ruff reformatting, self._ws.klippy -> self._ws.api
    # rename), so a single fixed patch doesn't apply across all versions.
    # Try each variant, newest first, and apply the first one that matches
    # the user's checkout via a dry-run.
    local patch_candidates=(
        "$SCRIPT_DIR/patches/ace_global_subscription.patch"
        "$SCRIPT_DIR/patches/ace_global_subscription_transitional.patch"
        "$SCRIPT_DIR/patches/ace_global_subscription_legacy.patch"
    )
    local screen_py="$KLIPPERSCREEN_ROOT_DIR/screen.py"

    if [ -f "$screen_py" ] && grep -q "_ace_subscription_objects" "$screen_py"; then
        print_success "ACE KlipperScreen patch already applied (found _ace_subscription_objects in screen.py)"
    else
        local matched_patch="" candidate
        for candidate in "${patch_candidates[@]}"; do
            [ -f "$candidate" ] || continue
            print_info "Checking applicability of $(basename "$candidate")..."
            if patch -d "$KLIPPERSCREEN_ROOT_DIR" -p1 --forward --fuzz=0 --dry-run < "$candidate" >/dev/null 2>&1; then
                matched_patch="$candidate"
                break
            fi
        done

        if [ -n "$matched_patch" ]; then
            [ -f "$screen_py" ] && backup_file "$screen_py"
            print_info "Applying $(basename "$matched_patch") to $KLIPPERSCREEN_ROOT_DIR"
            if patch -d "$KLIPPERSCREEN_ROOT_DIR" -p1 --forward --fuzz=0 < "$matched_patch"; then
                print_success "KlipperScreen patch applied ($(basename "$matched_patch"))"
            else
                print_warning "Patch failed during apply (unexpected). Please review output."
            fi
        else
            print_warning "No patch variant applied cleanly against this KlipperScreen checkout. Skipping."
            print_info "Your KlipperScreen version may be newer/older than any known variant; see patches/ to apply manually."
        fi
    fi

    # Ensure KlipperScreen.conf has font_size = small and ACE Pro menu entries
    local ks_conf="$CONFIG_DIR/KlipperScreen.conf"
    echo ""
    print_info "Checking KlipperScreen.conf for font_size setting..."
    ensure_klipperscreen_font_size "$ks_conf"
    print_info "Checking KlipperScreen.conf for ACE Pro menu entries (main + print menus)..."
    if [ -f "$ks_conf" ]; then
        ensure_klipperscreen_acepro_menu "$ks_conf"
    fi

    TOUCHED_KLIPPERSCREEN=1
    return 0
}

# ---------------------------------------------------------------------------
# Service restarts (only for services touched during this run)
# ---------------------------------------------------------------------------

step_restart() {
    if [ "$TOUCHED_KLIPPER" -eq 0 ] && [ "$TOUCHED_MOONRAKER" -eq 0 ] && [ "$TOUCHED_KLIPPERSCREEN" -eq 0 ]; then
        return 0
    fi

    print_header "Service Restart"
    echo "The following services were touched and need a restart for changes to take effect."
    echo ""

    if [ "$TOUCHED_MOONRAKER" -eq 1 ]; then
        if [ "$INTERACTIVE" -eq 0 ] && [ "$RESTART_SERVICES" -eq 1 ] || \
           { [ "$INTERACTIVE" -eq 1 ] && prompt_yes_no "Restart Moonraker service now?"; }; then
            print_info "Restarting Moonraker..."
            if sudo systemctl restart moonraker; then
                print_success "Moonraker restarted"
            else
                print_error "Failed to restart Moonraker"
            fi
        else
            print_warning "Moonraker not restarted. You can restart manually:"
            echo "  sudo systemctl restart moonraker"
        fi
        echo ""
    fi

    if [ "$TOUCHED_KLIPPER" -eq 1 ]; then
        if [ "$INTERACTIVE" -eq 0 ] && [ "$RESTART_SERVICES" -eq 1 ] || \
           { [ "$INTERACTIVE" -eq 1 ] && prompt_yes_no "Restart Klipper service now?"; }; then
            print_info "Restarting Klipper..."
            if sudo systemctl restart klipper; then
                print_success "Klipper restarted"
            else
                print_error "Failed to restart Klipper"
            fi
        else
            print_warning "Klipper not restarted. You can restart manually:"
            echo "  sudo systemctl restart klipper"
        fi
        echo ""
    fi

    if [ "$TOUCHED_KLIPPERSCREEN" -eq 1 ]; then
        if [ "$INTERACTIVE" -eq 0 ] && [ "$RESTART_SERVICES" -eq 1 ] || \
           { [ "$INTERACTIVE" -eq 1 ] && prompt_yes_no "Restart KlipperScreen service now?"; }; then
            print_info "Restarting KlipperScreen..."
            if sudo systemctl restart KlipperScreen 2>/dev/null || \
               sudo supervisorctl restart klipperscreen 2>/dev/null; then
                print_success "KlipperScreen restarted"
            else
                print_warning "Could not restart KlipperScreen. You can restart manually or via supervisor"
            fi
        else
            print_warning "KlipperScreen not restarted. You can restart manually:"
            echo "  sudo systemctl restart KlipperScreen"
            echo "  or: sudo supervisorctl restart klipperscreen"
        fi
    fi
}
