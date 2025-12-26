#!/bin/bash

# =============================================================================
# ACE Pro Klipper Driver - Interactive Installer
# =============================================================================
# This script performs the necessary installation steps to set up the ACE Pro
# driver for Klipper, including configuration file setup and symlinks.
#
# Compatible with: Raspberry Pi OS, Debian, Ubuntu
# Usage: ./installer.sh
#
# =============================================================================

set -u  # Exit on undefined variables

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory (where this script is located)
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# ============================================================================
# Helper Functions
# ============================================================================

print_header() {
    echo -e "\n${BLUE}========================================${NC}"
    echo -e "${BLUE}$1${NC}"
    echo -e "${BLUE}========================================${NC}\n"
}

print_info() {
    echo -e "${BLUE}ℹ ${1}${NC}"
}

print_success() {
    echo -e "${GREEN}✓ ${1}${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ ${1}${NC}"
}

print_error() {
    echo -e "${RED}✗ ${1}${NC}"
}

# Yes/No prompt
prompt_yes_no() {
    local prompt="$1"
    local response
    while true; do
        read -p "$(echo -e ${BLUE}${prompt}${NC} [y/N]: )" response
        case "$response" in
            [yY][eE][sS]|[yY]) return 0 ;;
            [nN][oO]|[nN]|"") return 1 ;;
            *) echo "Please answer y or n" ;;
        esac
    done
}

# Prompt for input with default
prompt_input() {
    local prompt="$1"
    local default="$2"
    local response
    read -p "$(echo -e ${BLUE}${prompt}${NC} [${default}]: )" response
    echo "${response:-$default}"
}

# Create backup with timestamp
backup_file() {
    local file="$1"
    if [ -f "$file" ]; then
        local timestamp=$(date +"%Y%m%d_%H%M%S")
        local backup="${file}.backup_${timestamp}"
        cp "$file" "$backup"
        print_success "Backed up: $file → $backup"
        return 0
    fi
    return 1
}

# Remove a symlink if it exists and show what it pointed to
remove_symlink_if_exists() {
    local path="$1"
    if is_symlink "$path"; then
        local target=$(readlink "$path")
        rm -f "$path"
        print_info "Removed symlink: $path (was pointing to: $target)"
        return 0
    fi
    return 1
}

# Check if path is a symlink
is_symlink() {
    [ -L "$1" ]
}

# Create or replace symlink
create_or_replace_symlink() {
    local source="$1"
    local target="$2"
    local description="$3"
    
    if [ ! -e "$source" ]; then
        print_error "$source does not exist, skipping symlink"
        return 1
    fi
    
    if [ -e "$target" ] || is_symlink "$target"; then
        if is_symlink "$target"; then
            print_warning "Symlink already exists: $target"
            local current_target=$(readlink "$target")
            print_info "  → Currently points to: $current_target"
        else
            print_warning "File/directory already exists: $target"
        fi
        
        if prompt_yes_no "Replace it?"; then
            rm -f "$target"
            ln -sf "$source" "$target"
            print_success "Symlink created: $target → $source"
            return 0
        else
            print_info "Skipped symlink for $description"
            return 1
        fi
    else
        # Target doesn't exist, create symlink
        mkdir -p "$(dirname "$target")"
        ln -sf "$source" "$target"
        print_success "Symlink created: $target → $source"
        return 0
    fi
}

# ============================================================================
# Main Installation
# ============================================================================

main() {
    print_header "ACE Pro Klipper Driver - Interactive Installer"
    
    # Track backup files for final instructions
    PRINTER_CONFIG_BACKUP=""
    PRINTER_GENERIC_MACROS_BACKUP=""
    ACE_CONFIG_BACKUP=""
    ACE_MACROS_BACKUP=""
    
    # ========================================================================
    # Step 1: Gather user input
    # ========================================================================
    
    print_info "Gathering installation parameters...\n"
    
    # 1.1 - Klipper installation directory
    DEFAULT_KLIPPER_DIR="$HOME/klipper"
    KLIPPER_DIR=$(prompt_input "Klipper installation directory" "$DEFAULT_KLIPPER_DIR")
    
    if [ ! -d "$KLIPPER_DIR" ]; then
        print_error "Klipper directory not found: $KLIPPER_DIR"
        exit 1
    fi
    print_success "Using Klipper directory: $KLIPPER_DIR"
    
    # 1.2 - Printer model
    echo ""
    echo "Which printer model?"
    echo "  1) Kobra 3"
    echo "  2) Kobra KS1"
    read -p "$(echo -e ${BLUE}Select [1 or 2]${NC}: )" printer_choice
    
    case "$printer_choice" in
        1)
            PRINTER_MODEL="K3"
            PRINTER_NAME="Kobra 3"
            ;;
        2)
            PRINTER_MODEL="KS1"
            PRINTER_NAME="Kobra S1"
            ;;
        *)
            print_error "Invalid choice"
            exit 1
            ;;
    esac
    print_success "Selected printer: $PRINTER_NAME"
    
    # 1.3 - Config directory
    DEFAULT_CONFIG_DIR="$HOME/printer_data/config"
    CONFIG_DIR=$(prompt_input "\nKlipper config directory" "$DEFAULT_CONFIG_DIR")
    
    if [ ! -d "$CONFIG_DIR" ]; then
        print_error "Config directory not found: $CONFIG_DIR"
        exit 1
    fi
    print_success "Using config directory: $CONFIG_DIR"
    
    # ========================================================================
    # Step 2: Ask for confirmation
    # ========================================================================
    
    echo ""
    print_header "Installation Summary"
    
    cat << EOF
Script directory:        $SCRIPT_DIR
Klipper directory:       $KLIPPER_DIR
Printer model:           $PRINTER_NAME
Config directory:        $CONFIG_DIR

Installation steps:
1. Link ace module to Klipper extras
2. Backup and install printer.cfg for $PRINTER_NAME
3. Copy printer_generic_macros.cfg (backup if exists)
4. Copy ACE configuration file
5. Copy ACE macro file
6. Link KlipperScreen panel (if available)
7. Optionally restart services
EOF
    
    if ! prompt_yes_no "Continue with installation?"; then
        print_info "Installation cancelled"
        exit 0
    fi
    
    # ========================================================================
    # Step 1: Link ace module to Klipper extras
    # ========================================================================
    
    print_header "Step 1: Linking ACE module to Klipper extras"
    
    ACE_SOURCE="$SCRIPT_DIR/extras/ace"
    ACE_TARGET="$KLIPPER_DIR/klippy/extras/ace"
    
    if [ ! -d "$ACE_SOURCE" ]; then
        print_error "ACE source directory not found: $ACE_SOURCE"
        exit 1
    fi
    
    create_or_replace_symlink "$ACE_SOURCE" "$ACE_TARGET" "ACE module"

    VP_SOURCE="$SCRIPT_DIR/extras/virtual_pins.py"
    VP_TARGET="$KLIPPER_DIR/klippy/extras/virtual_pins.py"
    
    if [ ! -f "$VP_SOURCE" ]; then
        print_error "virtual_pins.py not found: $ACE_SOURCE"
        exit 1
    fi
    
    create_or_replace_symlink "$VP_SOURCE" "$VP_TARGET" "virtual_pins module"
    
    # ========================================================================
    # Step 2: Backup and copy printer configuration
    # ========================================================================
    
    print_header "Step 2: Printer Configuration"
    
    PRINTER_CONFIG_SOURCE="$SCRIPT_DIR/config/printer_${PRINTER_MODEL}.cfg"
    PRINTER_CONFIG_TARGET="$CONFIG_DIR/printer.cfg"
    
    if [ ! -f "$PRINTER_CONFIG_SOURCE" ]; then
        print_error "Printer config file not found: $PRINTER_CONFIG_SOURCE"
        exit 1
    fi
    
    # Backup existing printer.cfg
    if [ -f "$PRINTER_CONFIG_TARGET" ]; then
        print_warning "printer.cfg already exists"
        local was_symlink=0
        if is_symlink "$PRINTER_CONFIG_TARGET"; then
            was_symlink=1
            print_info "Current printer.cfg is a symlink (→ $(readlink "$PRINTER_CONFIG_TARGET"))"
        fi

        if ! prompt_yes_no "Back up and replace printer.cfg?"; then
            print_info "Skipped printer configuration installation"
        else
            local timestamp=$(date +"%Y%m%d_%H%M%S")
            PRINTER_CONFIG_BACKUP="${PRINTER_CONFIG_TARGET}.backup_${timestamp}"
            cp "$PRINTER_CONFIG_TARGET" "$PRINTER_CONFIG_BACKUP"
            print_success "Backed up: $PRINTER_CONFIG_TARGET → $PRINTER_CONFIG_BACKUP"

            if [ "$was_symlink" -eq 1 ]; then
                remove_symlink_if_exists "$PRINTER_CONFIG_TARGET"
            fi

            cp "$PRINTER_CONFIG_SOURCE" "$PRINTER_CONFIG_TARGET"
            print_success "Copied: $PRINTER_CONFIG_SOURCE → $PRINTER_CONFIG_TARGET"
        fi
    else
        cp "$PRINTER_CONFIG_SOURCE" "$PRINTER_CONFIG_TARGET"
        print_success "Copied: $PRINTER_CONFIG_SOURCE → $PRINTER_CONFIG_TARGET"
    fi
    
    # ========================================================================
    # Step 3: Copy printer_generic_macros.cfg
    # ========================================================================

    print_header "Step 3: Printer Generic Macros"

    PRINTER_GENERIC_MACROS_SOURCE="$SCRIPT_DIR/config/printer_generic_macros.cfg"
    PRINTER_GENERIC_MACROS_TARGET="$CONFIG_DIR/printer_generic_macros.cfg"

    if [ ! -f "$PRINTER_GENERIC_MACROS_SOURCE" ]; then
        print_error "printer_generic_macros.cfg not found: $PRINTER_GENERIC_MACROS_SOURCE"
        exit 1
    fi

    if [ -f "$PRINTER_GENERIC_MACROS_TARGET" ]; then
        print_warning "printer_generic_macros.cfg already exists"
        local was_symlink=0
        if is_symlink "$PRINTER_GENERIC_MACROS_TARGET"; then
            was_symlink=1
            print_info "Current printer_generic_macros.cfg is a symlink (→ $(readlink "$PRINTER_GENERIC_MACROS_TARGET"))"
        fi

        if ! prompt_yes_no "Back up and replace printer_generic_macros.cfg?"; then
            print_info "Skipped printer_generic_macros.cfg installation"
        else
            local timestamp=$(date +"%Y%m%d_%H%M%S")
            PRINTER_GENERIC_MACROS_BACKUP="${PRINTER_GENERIC_MACROS_TARGET}.backup_${timestamp}"
            cp "$PRINTER_GENERIC_MACROS_TARGET" "$PRINTER_GENERIC_MACROS_BACKUP"
            print_success "Backed up: $PRINTER_GENERIC_MACROS_TARGET → $PRINTER_GENERIC_MACROS_BACKUP"

            if [ "$was_symlink" -eq 1 ]; then
                remove_symlink_if_exists "$PRINTER_GENERIC_MACROS_TARGET"
            fi

            cp "$PRINTER_GENERIC_MACROS_SOURCE" "$PRINTER_GENERIC_MACROS_TARGET"
            print_success "Copied: $PRINTER_GENERIC_MACROS_SOURCE → $PRINTER_GENERIC_MACROS_TARGET"
        fi
    else
        cp "$PRINTER_GENERIC_MACROS_SOURCE" "$PRINTER_GENERIC_MACROS_TARGET"
        print_success "Copied: $PRINTER_GENERIC_MACROS_SOURCE → $PRINTER_GENERIC_MACROS_TARGET"
    fi

    # Legacy filename support (cleanup old printer_macros_generic.cfg symlinks)
    LEGACY_PRINTER_GENERIC_MACROS_TARGET="$CONFIG_DIR/printer_macros_generic.cfg"
    if is_symlink "$LEGACY_PRINTER_GENERIC_MACROS_TARGET"; then
        print_warning "Legacy printer_macros_generic.cfg symlink detected"
        print_info "Current printer_macros_generic.cfg is a symlink (→ $(readlink "$LEGACY_PRINTER_GENERIC_MACROS_TARGET"))"

        if prompt_yes_no "Replace printer_macros_generic.cfg with a local copy?"; then
            local timestamp=$(date +"%Y%m%d_%H%M%S")
            local legacy_backup="${LEGACY_PRINTER_GENERIC_MACROS_TARGET}.backup_${timestamp}"
            cp "$LEGACY_PRINTER_GENERIC_MACROS_TARGET" "$legacy_backup"
            print_success "Backed up: $LEGACY_PRINTER_GENERIC_MACROS_TARGET → $legacy_backup"

            remove_symlink_if_exists "$LEGACY_PRINTER_GENERIC_MACROS_TARGET"
            cp "$PRINTER_GENERIC_MACROS_SOURCE" "$LEGACY_PRINTER_GENERIC_MACROS_TARGET"
            print_success "Copied: $PRINTER_GENERIC_MACROS_SOURCE → $LEGACY_PRINTER_GENERIC_MACROS_TARGET"
        else
            print_info "Skipped printer_macros_generic.cfg replacement"
        fi
    fi

    # ========================================================================
    # Step 4: Copy ACE configuration file
    # ========================================================================

    print_header "Step 4: ACE Configuration Files"

    ACE_CONFIG_SOURCE="$SCRIPT_DIR/config/ace_${PRINTER_MODEL}.cfg"
    ACE_CONFIG_TARGET="$CONFIG_DIR/ace_${PRINTER_MODEL}.cfg"

    if [ ! -f "$ACE_CONFIG_SOURCE" ]; then
        print_error "ACE config file not found: $ACE_CONFIG_SOURCE"
        exit 1
    fi

    if [ -f "$ACE_CONFIG_TARGET" ]; then
        print_warning "ace_${PRINTER_MODEL}.cfg already exists"
        local was_symlink=0
        if is_symlink "$ACE_CONFIG_TARGET"; then
            was_symlink=1
            print_info "Current ace_${PRINTER_MODEL}.cfg is a symlink (→ $(readlink "$ACE_CONFIG_TARGET"))"
        fi

        if ! prompt_yes_no "Back up and replace ace_${PRINTER_MODEL}.cfg?"; then
            print_info "Skipped ace_${PRINTER_MODEL}.cfg installation"
        else
            local timestamp=$(date +"%Y%m%d_%H%M%S")
            ACE_CONFIG_BACKUP="${ACE_CONFIG_TARGET}.backup_${timestamp}"
            cp "$ACE_CONFIG_TARGET" "$ACE_CONFIG_BACKUP"
            print_success "Backed up: $ACE_CONFIG_TARGET → $ACE_CONFIG_BACKUP"

            if [ "$was_symlink" -eq 1 ]; then
                remove_symlink_if_exists "$ACE_CONFIG_TARGET"
            fi

            cp "$ACE_CONFIG_SOURCE" "$ACE_CONFIG_TARGET"
            print_success "Copied: $ACE_CONFIG_SOURCE → $ACE_CONFIG_TARGET"
        fi
    else
        cp "$ACE_CONFIG_SOURCE" "$ACE_CONFIG_TARGET"
        print_success "Copied: $ACE_CONFIG_SOURCE → $ACE_CONFIG_TARGET"
    fi

    # ========================================================================
    # Step 5: Copy ACE macro file
    # ========================================================================

    print_header "Step 5: ACE Macro Files"

    MACROS_SOURCE="$SCRIPT_DIR/config/ace_macros_generic.cfg"
    MACROS_TARGET="$CONFIG_DIR/ace_macros_generic.cfg"

    if [ ! -f "$MACROS_SOURCE" ]; then
        print_error "ACE macros file not found: $MACROS_SOURCE"
        exit 1
    fi

    if [ -f "$MACROS_TARGET" ]; then
        print_warning "ace_macros_generic.cfg already exists"
        local was_symlink=0
        if is_symlink "$MACROS_TARGET"; then
            was_symlink=1
            print_info "Current ace_macros_generic.cfg is a symlink (→ $(readlink "$MACROS_TARGET"))"
        fi

        if ! prompt_yes_no "Back up and replace ace_macros_generic.cfg?"; then
            print_info "Skipped ace_macros_generic.cfg installation"
        else
            local timestamp=$(date +"%Y%m%d_%H%M%S")
            ACE_MACROS_BACKUP="${MACROS_TARGET}.backup_${timestamp}"
            cp "$MACROS_TARGET" "$ACE_MACROS_BACKUP"
            print_success "Backed up: $MACROS_TARGET → $ACE_MACROS_BACKUP"

            if [ "$was_symlink" -eq 1 ]; then
                remove_symlink_if_exists "$MACROS_TARGET"
            fi

            cp "$MACROS_SOURCE" "$MACROS_TARGET"
            print_success "Copied: $MACROS_SOURCE → $MACROS_TARGET"
        fi
    else
        cp "$MACROS_SOURCE" "$MACROS_TARGET"
        print_success "Copied: $MACROS_SOURCE → $MACROS_TARGET"
    fi
    
    # ========================================================================
    # Step 6: Link KlipperScreen panel (if available)
    # ========================================================================

    print_header "Step 6: KlipperScreen Integration (Optional)"
    
    KLIPPERSCREEN_PANELS_DIR="$HOME/KlipperScreen/panels"
    KLIPPERSCREEN_PANEL_SOURCE="$SCRIPT_DIR/KlipperScreen/acepro.py"
    KLIPPERSCREEN_PANEL_TARGET="$KLIPPERSCREEN_PANELS_DIR/acepro.py"
    
    if [ ! -d "$KLIPPERSCREEN_PANELS_DIR" ]; then
        print_warning "KlipperScreen panels directory not found: $KLIPPERSCREEN_PANELS_DIR"
        print_info "KlipperScreen integration skipped (not installed)"
    else
        if [ ! -f "$KLIPPERSCREEN_PANEL_SOURCE" ]; then
            print_error "KlipperScreen panel file not found: $KLIPPERSCREEN_PANEL_SOURCE"
        else
            print_info "KlipperScreen panels directory found"
            create_or_replace_symlink "$KLIPPERSCREEN_PANEL_SOURCE" "$KLIPPERSCREEN_PANEL_TARGET" "acepro.py panel"
        fi
    fi
    
    # ========================================================================
    # Step 7: Service restart
    # ========================================================================

    print_header "Step 7: Service Restart"
    
    echo "Klipper and KlipperScreen need to be restarted for changes to take effect."
    echo ""
    
    if prompt_yes_no "Restart Klipper service now?"; then
        print_info "Restarting Klipper..."
        sudo systemctl restart klipper
        if [ $? -eq 0 ]; then
            print_success "Klipper restarted"
        else
            print_error "Failed to restart Klipper"
        fi
    else
        print_warning "Klipper not restarted. You can restart manually:"
        echo "  sudo systemctl restart klipper"
    fi
    
    if [ -d "$KLIPPERSCREEN_PANELS_DIR" ]; then
        echo ""
        if prompt_yes_no "Restart KlipperScreen service now?"; then
            print_info "Restarting KlipperScreen..."
            sudo systemctl restart KlipperScreen 2>/dev/null || \
            sudo supervisorctl restart klipperscreen 2>/dev/null || \
            print_warning "Could not restart KlipperScreen. You can restart manually or via supervisor"
            if [ $? -eq 0 ]; then
                print_success "KlipperScreen restarted"
            fi
        else
            print_warning "KlipperScreen not restarted. You can restart manually:"
            echo "  sudo systemctl restart KlipperScreen"
            echo "  or: sudo supervisorctl restart klipperscreen"
        fi
    fi
    
    # ========================================================================
    # Installation complete
    # ========================================================================
    
    print_header "Installation Complete!"
    
    cat << EOF
ACE Pro driver installation finished!

Configuration Files:
  New printer.cfg:       $PRINTER_CONFIG_TARGET
EOF
    
    # Show backup file if one was created
    if [ -n "$PRINTER_CONFIG_BACKUP" ] && [ -f "$PRINTER_CONFIG_BACKUP" ]; then
        cat << EOF
  Backed up printer.cfg: $PRINTER_CONFIG_BACKUP
EOF
    fi

    if [ -f "$PRINTER_GENERIC_MACROS_TARGET" ]; then
        cat << EOF
  New printer_generic_macros.cfg: $PRINTER_GENERIC_MACROS_TARGET
EOF
    fi

    if [ -n "$PRINTER_GENERIC_MACROS_BACKUP" ] && [ -f "$PRINTER_GENERIC_MACROS_BACKUP" ]; then
        cat << EOF
  Backed up printer_generic_macros.cfg: $PRINTER_GENERIC_MACROS_BACKUP
EOF
    fi

    if [ -f "$ACE_CONFIG_TARGET" ]; then
        cat << EOF
  New ace_${PRINTER_MODEL}.cfg: $ACE_CONFIG_TARGET
EOF
    fi

    if [ -n "$ACE_CONFIG_BACKUP" ] && [ -f "$ACE_CONFIG_BACKUP" ]; then
        cat << EOF
  Backed up ace_${PRINTER_MODEL}.cfg: $ACE_CONFIG_BACKUP
EOF
    fi

    if [ -f "$MACROS_TARGET" ]; then
        cat << EOF
  New ace_macros_generic.cfg: $MACROS_TARGET
EOF
    fi

    if [ -n "$ACE_MACROS_BACKUP" ] && [ -f "$ACE_MACROS_BACKUP" ]; then
        cat << EOF
  Backed up ace_macros_generic.cfg: $ACE_MACROS_BACKUP
EOF
    fi
    
    cat << EOF

Next steps:
  1. Review and customize ACE configuration:
      $CONFIG_DIR/ace_${PRINTER_MODEL}.cfg
      - Set ace_count to number of ACE units
      - Adjust feed/retract speeds
      - Configure sensor pins if needed
      - Re-run installer after updates to pick up template changes

  2. Review and merge printer configuration:
     New config:          $PRINTER_CONFIG_TARGET
EOF
    
    # Show backup for reference if it exists
    if [ -n "$PRINTER_CONFIG_BACKUP" ] && [ -f "$PRINTER_CONFIG_BACKUP" ]; then
        cat << EOF
     Original backup:     $PRINTER_CONFIG_BACKUP
     
     Manually review and merge any custom changes from the backup file
     into the new printer.cfg file, especially:
     - Custom macros
     - Non-standard hardware settings
     - Personal customizations
EOF
    fi
    
    cat << EOF

  3. Review printer_generic_macros.cfg if you plan to customize pause/resume, velocity stack, or purge helpers:
      $CONFIG_DIR/printer_generic_macros.cfg

  4. Review ace_macros_generic.cfg if you plan to tweak ACE hooks or safety wrappers:
      $CONFIG_DIR/ace_macros_generic.cfg

  5. Restart Klipper if not already restarted:
     sudo systemctl restart klipper

  6. Test basic commands in Klipper console:
     e.g. ACE_GET_STATUS

  7. Optional but recommened: Set inventory for each tool:
     ACE_SET_SLOT INSTANCE=0 INDEX=0 COLOR=255,0,0 MATERIAL=PLA TEMP=210

  8. If using Orca Slicer:
     Install the orca_flush_to_purgelength.py post-processing script on your host PC
     See README.md section on Orca Slicer integration for detailed instructions

EOF
}

# ============================================================================
# Entry Point
# ============================================================================

if [ "${BASH_SOURCE[0]}" == "${0}" ]; then
    main "$@"
fi
