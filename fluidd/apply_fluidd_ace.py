#!/usr/bin/env python3
"""
Fluidd patcher for the ACE Pro panel.
Run from the fluidd repository root:
    python3 apply_fluidd_ace.py

Uses anchor-based replacements instead of a unified diff, so it survives
minor line shifts between Fluidd versions. Each anchor must match exactly
once; otherwise the file is left untouched and an error is reported.
"""

import sys
from pathlib import Path

def patch_file(path, edits):
    p = Path(path)
    if not p.exists():
        print(f"[!] File not found: {path}")
        return False
    src = p.read_text()
    if "AceCard" in src or "ace-card" in src:
        print(f"[=] {path} already patched, skipping")
        return True
    for anchor, replacement, name in edits:
        count = src.count(anchor)
        if count != 1:
            print(f"[!] {path}: anchor '{name}' matched {count} time(s), expected 1.")
            print("    The file differs from the expected Fluidd version - patch manually.")
            return False
    for anchor, replacement, name in edits:
        src = src.replace(anchor, replacement, 1)
    p.write_text(src)
    print(f"[+] {path} patched")
    return True

ok = True

# --- src/views/Dashboard.vue ---
ok &= patch_file("src/views/Dashboard.vue", [
    (
        "import AfcCard from '@/components/widgets/afc/AfcCard.vue'\n",
        "import AfcCard from '@/components/widgets/afc/AfcCard.vue'\n"
        "import AceCard from '@/components/widgets/ace/AceCard.vue'\n",
        "import AceCard"
    ),
    (
        "    AfcCard\n  }",
        "    AfcCard,\n    AceCard\n  }",
        "components: AceCard"
    ),
    (
        "  get supportsAfc (): boolean {\n"
        "    return this.$typedGetters['printer/getSupportsAfc']\n"
        "  }\n",
        "  get supportsAfc (): boolean {\n"
        "    return this.$typedGetters['printer/getSupportsAfc']\n"
        "  }\n"
        "\n"
        "  get supportsAce (): boolean {\n"
        "    return (this.$typedState.printer.printer as any).ace != null\n"
        "  }\n",
        "supportsAce getter"
    ),
    (
        "    if (item.id === 'afc-card' && !this.supportsAfc) return true\n",
        "    if (item.id === 'afc-card' && !this.supportsAfc) return true\n"
        "    if (item.id === 'ace-card' && !this.supportsAce) return true\n",
        "filtered(): ace-card"
    ),
])

# --- src/store/layout/state.ts ---
ok &= patch_file("src/store/layout/state.ts", [
    (
        "          { id: 'mmu-card', enabled: true, collapsed: false },\n",
        "          { id: 'mmu-card', enabled: true, collapsed: false },\n"
        "          { id: 'ace-card', enabled: true, collapsed: false },\n",
        "layout: ace-card"
    ),
])

if not ok:
    print("\nPatch was NOT applied completely.")
    sys.exit(1)

print("\nDone. Next:")
print("  pnpm install")
print("  pnpm run build")
