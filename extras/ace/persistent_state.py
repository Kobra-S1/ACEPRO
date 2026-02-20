"""
Centralised persistent variable access for the ACE Pro module.

Wraps Klipper's ``save_variables`` object so that every read, in-memory
write and persist-to-disk operation goes through a single gateway.

**Deferred-flush strategy (Option A)**

``set()`` updates RAM and marks the variable *dirty* for later flushing.
``set_and_save()`` updates RAM and — depending on *persistence_mode* —
either flushes to disk immediately or defers the write like ``set()``.

Use ``set()`` in time-critical paths (toolchanges, mid-print callbacks)
where blocking Klipper's single-threaded reactor with synchronous
``configparser.write()`` must be avoided.  Call ``flush()`` at a safe
moment (print end, disconnect) to persist all dirty variables.

Persistence modes (set via ``persistence_mode`` in printer.cfg):

- ``deferred`` *(default)*: ``set_and_save()`` behaves like ``set()`` —
  RAM + dirty mark only.  Disk writes happen only in ``flush()``.
  Never blocks the reactor mid-print.
- ``immediate``: ``set_and_save()`` writes to disk right away (legacy).
  Use when you want key state persisted even without a clean shutdown.

Typical usage::

    state = PersistentState(printer, gcode)

    # Read (always fresh from Klipper)
    tool = state.get("ace_current_index", -1)

    # In-memory + marked dirty (disk write deferred until flush)
    state.set("ace_filament_pos", "bowden")

    # In-memory + immediate disk write (for user-facing commands)
    state.set_and_save("ace_current_index", 2)

    # Persist all dirty variables from prior set() calls
    state.flush()
"""

import configparser
import json
import logging


class PersistentState:
    """
    Single access point for Klipper ``save_variables``.

    Every component (AceManager, AceInstance, commands, RunoutMonitor,
    EndlessSpool) should use this instead of touching
    ``printer.lookup_object("save_variables")`` directly.
    """

    def __init__(self, printer, gcode, persistence_mode="deferred"):
        """
        Args:
            printer:          Klipper printer object
            gcode:            Klipper gcode object (needed for SAVE_VARIABLE commands)
            persistence_mode: ``"deferred"`` (default) or ``"immediate"``.
                              Controls whether ``set_and_save()`` writes to disk
                              right away or defers to the next ``flush()`` call.
        """
        self.printer = printer
        self.gcode = gcode
        self._immediate = (persistence_mode == "immediate")
        self._dirty = set()  # variable names awaiting disk flush

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _variables(self):
        """Return the live ``allVariables`` dict reference.

        This is intentionally a method (not a cached property) so that
        callers always see the latest dict even after Klipper reloads
        the save-variables file.
        """
        save_vars = self.printer.lookup_object("save_variables")
        return save_vars.allVariables

    def _write_to_disk(self, varname, value):
        """Issue one ``SAVE_VARIABLE`` gcode command for *varname*.

        Type conversion rules (Klipper uses ``ast.literal_eval``):
        - ``bool``  → ``True`` / ``False``
        - ``str``   → single-quote + double-quote wrapped
        - ``dict`` / ``list`` → JSON with Python literals, single-quote
          wrapped
        - everything else (int, float, None …) → ``str(value)``
        """
        if isinstance(value, bool):
            formatted = "True" if value else "False"
            self.gcode.run_script_from_command(
                f"SAVE_VARIABLE VARIABLE={varname} VALUE={formatted}"
            )
        elif isinstance(value, str):
            self.gcode.run_script_from_command(
                f"SAVE_VARIABLE VARIABLE={varname} VALUE='\"{value}\"'"
            )
        elif isinstance(value, (dict, list)):
            payload = (json.dumps(value)
                       .replace("true", "True")
                       .replace("false", "False")
                       .replace("null", "None"))
            self.gcode.run_script_from_command(
                f"SAVE_VARIABLE VARIABLE={varname} VALUE='{payload}'"
            )
        else:
            self.gcode.run_script_from_command(
                f"SAVE_VARIABLE VARIABLE={varname} VALUE={value}"
            )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get(self, varname, default=None):
        """Read a single variable (always fresh).

        Args:
            varname: Variable name.
            default: Returned when *varname* does not exist.

        Returns:
            The stored value, or *default*.
        """
        return self._variables().get(varname, default)

    def get_all(self):
        """Return the full variables dict (live reference)."""
        return self._variables()

    # ------------------------------------------------------------------
    # Write — in-memory only
    # ------------------------------------------------------------------

    def set(self, varname, value):
        """Update a variable **in memory** and mark it dirty.

        The variable is added to the dirty set and will be flushed
        to disk on the next ``flush()`` call.  No disk I/O happens
        here, keeping the Klipper reactor unblocked during
        time-critical paths such as toolchanges.

        Args:
            varname: Variable name.
            value:   Any value.
        """
        self._variables()[varname] = value
        self._dirty.add(varname)

    # ------------------------------------------------------------------
    # Write — in-memory + deferred disk persistence
    # ------------------------------------------------------------------

    def set_and_save(self, varname, value):
        """Update a variable in memory and persist according to the mode.

        - ``immediate`` mode: writes to disk right away via ``SAVE_VARIABLE``.
        - ``deferred`` mode: behaves like ``set()`` — RAM update + dirty mark
          only; the actual disk write is deferred to the next ``flush()``.

        For unconditionally time-critical paths, use ``set()`` directly.

        Args:
            varname: Variable name.
            value:   Any JSON-serialisable value.
        """
        self._variables()[varname] = value
        if self._immediate:
            self._dirty.discard(varname)  # no longer dirty — writing now
            self._write_to_disk(varname, value)
        else:
            self._dirty.add(varname)  # deferred — will be flushed later

    # ------------------------------------------------------------------
    # Flush — persist all dirty variables to disk
    # ------------------------------------------------------------------

    @property
    def has_pending(self):
        """``True`` when there are dirty variables awaiting disk flush."""
        return bool(self._dirty)

    def flush(self):
        """Write every dirty variable to ``saved_variables.cfg``.

        Each variable is written via a ``SAVE_VARIABLE`` gcode command.
        The dirty set is cleared after all writes complete.

        Safe to call when nothing is dirty — it simply returns.
        """
        if not self._dirty:
            return

        variables = self._variables()
        flushed = list(self._dirty)
        for varname in flushed:
            value = variables.get(varname)
            try:
                self._write_to_disk(varname, value)
            except Exception:
                logging.exception(
                    "ACE: Failed to flush variable %s", varname
                )
        self._dirty.clear()

    def flush_direct(self):
        """Write ALL in-memory variables directly to ``saved_variables.cfg``.

        Bypasses the GCode queue entirely — safe to call during shutdown or
        emergency stop when ``run_script_from_command`` is unavailable or
        would be rejected with "Printer is shutdown".

        Writes the complete ``allVariables`` dict (not just dirty entries) so
        that even ``set_and_save()`` calls whose queued ``SAVE_VARIABLE``
        command was interrupted mid-shutdown are still persisted.
        """
        try:
            save_vars = self.printer.lookup_object("save_variables")
            filename = save_vars.filename
            variables = save_vars.allVariables

            cfg = configparser.ConfigParser()
            cfg.add_section("Variables")
            for name, val in sorted(variables.items()):
                cfg.set("Variables", name, repr(val))

            with open(filename, "w") as fh:
                cfg.write(fh)

            self._dirty.clear()
            logging.info(
                "ACE: flush_direct wrote %d variable(s) to %s",
                len(variables), filename
            )
        except Exception:
            logging.exception("ACE: flush_direct failed")
