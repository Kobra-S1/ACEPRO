# =============================================================================
# ACE Pro Installer - backup handling
# =============================================================================
# All backups of one installer run go into a single timestamped folder below
# <CONFIG_DIR>/acepro_backups/ instead of *.backup_<timestamp> files scattered
# through the config directory. A manifest records the original path of every
# file so a run can be restored as a set.
#
# Sourced by installer.sh - not executable on its own.
# =============================================================================

BACKUP_RUN_DIR=""   # created lazily on first backup of the current run

# Back up a single file into the current run folder.
backup_file() {
    local file="$1"
    [ -f "$file" ] || return 1

    if [ -z "$BACKUP_RUN_DIR" ]; then
        BACKUP_RUN_DIR="$BACKUP_ROOT/$(date +%Y%m%d_%H%M%S)"
        mkdir -p "$BACKUP_RUN_DIR"
    fi

    local base
    base=$(basename "$file")
    cp "$file" "$BACKUP_RUN_DIR/$base"
    printf '%s\t%s\n' "$base" "$file" >> "$BACKUP_RUN_DIR/manifest.txt"
    print_success "Backed up: $file → $BACKUP_RUN_DIR/"
    return 0
}

# List existing backup run folders, newest first (prints one path per line)
list_backup_runs() {
    [ -d "$BACKUP_ROOT" ] || return 0
    local d
    for d in "$BACKUP_ROOT"/*/; do
        [ -d "$d" ] || continue
        [ "$(basename "$d")" = "migrated" ] && continue
        printf '%s\n' "${d%/}"
    done | sort -r
}

# Restore all files of one backup run to their original locations
restore_backup_run() {
    local run_dir="$1"
    local manifest="$run_dir/manifest.txt"

    if [ ! -f "$manifest" ]; then
        print_error "No manifest found in $run_dir - cannot restore automatically"
        print_info "You can copy files back manually from that folder."
        return 1
    fi

    echo ""
    print_info "This will restore the following files:"
    local base orig
    while IFS=$'\t' read -r base orig; do
        echo "  $run_dir/$base → $orig"
    done < "$manifest"
    echo ""

    if ! prompt_yes_no "Overwrite the current files with this backup?"; then
        print_info "Restore cancelled"
        return 1
    fi

    while IFS=$'\t' read -r base orig; do
        if [ -f "$run_dir/$base" ]; then
            # Replace symlinks with a real file, like the install steps do
            [ -L "$orig" ] && rm -f "$orig"
            mkdir -p "$(dirname "$orig")"
            cp "$run_dir/$base" "$orig"
            print_success "Restored: $orig"
        else
            print_warning "Missing in backup, skipped: $base"
        fi
    done < "$manifest"

    print_warning "Restart Klipper (and Moonraker/KlipperScreen if affected) to load the restored files."
    return 0
}

# Delete all but the newest N backup runs
prune_backup_runs() {
    local keep="$1"
    local runs
    mapfile -t runs < <(list_backup_runs)

    if [ "${#runs[@]}" -le "$keep" ]; then
        print_info "Nothing to prune (${#runs[@]} backup run(s), keeping $keep)"
        return 0
    fi

    local i
    for (( i = keep; i < ${#runs[@]}; i++ )); do
        rm -rf "${runs[$i]}"
        print_success "Removed old backup: ${runs[$i]}"
    done
}

# Move legacy *.backup_* files out of the config root into acepro_backups/migrated/
count_legacy_backups() {
    find "$CONFIG_DIR" -maxdepth 1 -name "*.backup_*" 2>/dev/null | wc -l
}

migrate_legacy_backups() {
    local migrated_dir="$BACKUP_ROOT/migrated"
    local moved=0 f
    while IFS= read -r f; do
        [ -n "$f" ] || continue
        mkdir -p "$migrated_dir"
        mv "$f" "$migrated_dir/"
        print_success "Moved: $(basename "$f") → $migrated_dir/"
        (( moved++ ))
    done < <(find "$CONFIG_DIR" -maxdepth 1 -name "*.backup_*" 2>/dev/null)

    if [ "$moved" -eq 0 ]; then
        print_info "No legacy *.backup_* files found in $CONFIG_DIR"
    else
        print_success "Migrated $moved legacy backup file(s) to $migrated_dir"
    fi
}

# ---------------------------------------------------------------------------
# Backups menu
# ---------------------------------------------------------------------------

backups_menu() {
    while true; do
        local runs legacy_count
        mapfile -t runs < <(list_backup_runs)
        legacy_count=$(count_legacy_backups)

        ui_clear
        box_top
        box_center "${CYAN}Backups${NC}"
        box_sep
        box_line "Backup folder: ${BACKUP_ROOT/#$INSTALL_HOME/\~}"
        box_sep

        if [ "${#runs[@]}" -eq 0 ]; then
            box_line "No backup runs found."
        else
            local i shown=10
            for (( i = 0; i < ${#runs[@]} && i < shown; i++ )); do
                local name files
                name=$(basename "${runs[$i]}")
                files=$(find "${runs[$i]}" -maxdepth 1 -type f ! -name manifest.txt | wc -l)
                box_line "$(printf '%2d) %s  (%s file(s))' "$((i + 1))" "$name" "$files")"
            done
            if [ "${#runs[@]}" -gt "$shown" ]; then
                box_line "    ... and $(( ${#runs[@]} - shown )) more"
            fi
        fi

        if [ "$legacy_count" -gt 0 ]; then
            box_sep
            box_line "${YELLOW}$legacy_count legacy *.backup_* file(s) in config root${NC}"
        fi

        box_sep
        box_line "R) Restore a backup run    P) Prune old backups"
        [ "$legacy_count" -gt 0 ] && box_line "M) Migrate legacy backups into backup folder"
        box_line "B) Back"
        box_bottom

        local choice
        read -r -p "Select: " choice
        case "$choice" in
            [rR])
                if [ "${#runs[@]}" -eq 0 ]; then
                    print_info "No backups to restore"
                else
                    local num
                    num=$(prompt_input "Number of the backup run to restore" "1")
                    if [[ "$num" =~ ^[0-9]+$ ]] && [ "$num" -ge 1 ] && [ "$num" -le "${#runs[@]}" ]; then
                        restore_backup_run "${runs[$((num - 1))]}"
                    else
                        print_error "Invalid selection: $num"
                    fi
                fi
                pause_for_key
                ;;
            [pP])
                local keep
                keep=$(prompt_input "How many backup runs to keep" "10")
                if [[ "$keep" =~ ^[0-9]+$ ]]; then
                    prune_backup_runs "$keep"
                else
                    print_error "Invalid number: $keep"
                fi
                pause_for_key
                ;;
            [mM])
                migrate_legacy_backups
                pause_for_key
                ;;
            [bB]|"")
                return 0
                ;;
        esac
    done
}
