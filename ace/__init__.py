"""
Compatibility shim package for tests.

This small top-level package ensures `import ace` and submodules
(resolve to the moved package under `extras/ace`) for compatibility
with existing code and tests.
"""
import os

# Compute the repository root (one level up from this file)
_repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
# Path to the moved package
_extras_ace = os.path.join(_repo_root, 'extras', 'ace')

if os.path.isdir(_extras_ace):
    # Prepend so these modules take precedence
    __path__.insert(0, _extras_ace)

# Expose nothing else here; actual implementation lives in extras/ace
