# =============================================================================
# ACE Pro Installer - UI helpers
# =============================================================================
# Colors, message printing, prompts and the box-drawing menu framework.
# Sourced by installer.sh - not executable on its own.
# =============================================================================

# Colors (real escape characters so plain printf works)
RED=$'\033[0;31m'
GREEN=$'\033[0;32m'
YELLOW=$'\033[1;33m'
BLUE=$'\033[0;34m'
CYAN=$'\033[0;36m'
MAGENTA=$'\033[0;35m'
NC=$'\033[0m'

# Inner width of menu boxes (characters between the border columns)
BOX_W=58

print_header() {
    printf '\n%s========================================%s\n' "$BLUE" "$NC"
    printf '%s%s%s\n' "$BLUE" "$1" "$NC"
    printf '%s========================================%s\n\n' "$BLUE" "$NC"
}

print_info() {
    printf '%sℹ %s%s\n' "$BLUE" "$1" "$NC"
}

print_success() {
    printf '%s✓ %s%s\n' "$GREEN" "$1" "$NC"
}

print_warning() {
    printf '%s⚠ %s%s\n' "$YELLOW" "$1" "$NC"
}

print_error() {
    printf '%s✗ %s%s\n' "$RED" "$1" "$NC"
}

# Yes/No prompt
prompt_yes_no() {
    local prompt="$1"
    local response
    while true; do
        read -r -p "$(printf '%s%s%s [y/N]: ' "$BLUE" "$prompt" "$NC")" response
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
    read -r -p "$(printf '%s%s%s [%s]: ' "$BLUE" "$prompt" "$NC" "$default")" response
    echo "${response:-$default}"
}

# Wait for a single keypress (used by menus)
read_key() {
    local key
    IFS= read -rsn1 key
    echo "$key"
}

pause_for_key() {
    printf '\n%sPress any key to continue...%s' "$CYAN" "$NC"
    read -rsn1
    printf '\n'
}

# Remove ANSI color codes (for width calculations)
strip_colors() {
    printf '%s' "$1" | sed -e $'s/\033\[[0-9;]*m//g'
}

ui_clear() {
    if [ -t 1 ]; then
        clear
    fi
}

# ---------------------------------------------------------------------------
# Box drawing
# ---------------------------------------------------------------------------

_box_rule() {
    # $1 = left corner, $2 = fill, $3 = right corner
    local i line=""
    for (( i = 0; i < BOX_W + 2; i++ )); do
        line+="$2"
    done
    printf '%s%s%s\n' "$1" "$line" "$3"
}

box_top()    { _box_rule '╔' '═' '╗'; }
box_sep()    { _box_rule '╟' '─' '╢'; }
box_bottom() { _box_rule '╚' '═' '╝'; }

# One content line inside the box; color codes allowed in the text.
# Over-long lines are truncated (dropping colors) so the box stays intact.
box_line() {
    local text="${1:-}"
    local plain pad
    plain=$(strip_colors "$text")
    if [ "${#plain}" -gt "$BOX_W" ]; then
        text="${plain:0:$(( BOX_W - 1 ))}…"
        plain=$text
    fi
    pad=$(( BOX_W - ${#plain} ))
    (( pad < 0 )) && pad=0
    printf '║ %s%*s ║\n' "$text" "$pad" ''
}

# Centered content line (e.g. titles)
box_center() {
    local text="${1:-}"
    local plain lpad
    plain=$(strip_colors "$text")
    lpad=$(( (BOX_W - ${#plain}) / 2 ))
    (( lpad < 0 )) && lpad=0
    box_line "$(printf '%*s%s' "$lpad" '' "$text")"
}

# Two-column line: fixed-width left column, rest goes to the right column.
# Color codes allowed in both parts.
box_cols() {
    local left="$1" right="${2:-}" lwidth="${3:-26}"
    local lplain lpad
    lplain=$(strip_colors "$left")
    lpad=$(( lwidth - ${#lplain} ))
    (( lpad < 0 )) && lpad=0
    box_line "$(printf '%s%*s%s' "$left" "$lpad" '' "$right")"
}
