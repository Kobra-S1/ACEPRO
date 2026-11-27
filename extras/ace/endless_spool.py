"""
EndlessSpool: Handles endless spool filament matching logic.

Responsibilities:
- Find exact material/color matches across all slots
- Predict that match for status polls (AceManager.get_status)
- No pause/resume logic (manager owns that)
- No jam detection (removed)

Integration:
- Called from: RunoutMonitor (runout), AceManager.get_status (prediction)
"""

import logging
from .config import (
    ACE_INSTANCES,
    SLOTS_PER_ACE,
    get_instance_from_tool,
    get_local_slot,
    get_tool_offset,
)


# Why a slot cannot take over a runout (EndlessSpool._mismatch). Keys, not
# messages: the status poll asks too and must not build log text.
_MISMATCH_NOT_READY = "not_ready"
_MISMATCH_UNKNOWN_MATERIAL = "unknown_material"
_MISMATCH_MATERIAL = "material"
_MISMATCH_COLOR = "color"


def _material(inv):
    return inv.get("material", "").lower().strip()


def _rgb(color):
    return f"RGB({color[0]},{color[1]},{color[2]})"


def _describe_mismatch(mismatch, wanted, candidate):
    """Log text for a _MISMATCH_* key."""
    if mismatch == _MISMATCH_NOT_READY:
        return f"status={candidate.get('status')} (need 'ready')"
    if mismatch == _MISMATCH_UNKNOWN_MATERIAL:
        return (
            f"cannot match unknown materials "
            f"(current='{_material(wanted)}', candidate='{_material(candidate)}')"
        )
    if mismatch == _MISMATCH_MATERIAL:
        return (
            f"material mismatch "
            f"(want '{_material(wanted)}', got '{_material(candidate)}')"
        )
    return (
        f"color mismatch (want {_rgb(wanted.get('color', [0, 0, 0]))}, "
        f"got {_rgb(candidate.get('color', [0, 0, 0]))})"
    )


class EndlessSpool:
    """Endless spool matching logic only."""

    def __init__(self, printer, gcode, manager):
        """
        Initialize endless spool handler.

        Args:
            printer: Klipper printer object
            gcode: Klipper gcode object
            manager: AceManager instance
        """
        self.printer = printer
        self.gcode = gcode
        self.manager = manager
        self.reactor = printer.get_reactor()

    def get_match_mode(self):
        """
        Get the current endless spool match mode from persistent storage.

        Returns:
            str: "exact" (material + color), "material" (material only),
                 or "next" (first ready spool regardless of material/color)
        """
        mode = self.manager.state.get("ace_endless_spool_match_mode", "exact")

        # Normalize/guardrail unexpected values
        if mode not in {"exact", "material", "next"}:
            mode = "exact"

        return mode

    def predict_next_tool(self, current_tool):
        """
        The tool a runout of current_tool would swap to now, or -1.

        Same search as find_exact_match but silent, as it runs on every
        status poll. A prediction: inventory may change before the runout.
        """
        wanted = self._slot_inventory(current_tool)
        if wanted is None:
            return -1
        match_mode = self.get_match_mode()
        for candidate_tool, candidate in self._candidates_after(current_tool):
            if self._mismatch(wanted, candidate, match_mode) is None:
                return candidate_tool
        return -1

    def find_exact_match(self, current_tool):
        """
        Find next spool with matching material and optionally color.

        Match mode (configurable via ace_endless_spool_match_mode):
        - "exact": Match both material AND color (default)
        - "material": Match material only, ignore color
        - "next": First ready spool, regardless of material/color

        Searches all slots in all instances, starting from the next tool
        and wrapping around, and logs why each skipped slot did not match.

        Args:
            current_tool: Tool index with runout (0-based)

        Returns:
            int: Tool index of match, or -1 if none found
        """
        current_inv = self._slot_inventory(current_tool)
        if current_inv is None:
            return -1

        current_material = _material(current_inv)
        current_color = current_inv.get("color", [0, 0, 0])

        match_mode = self.get_match_mode()

        if match_mode == "material":
            logging.info(
                f"ACE: Looking for endless spool match (MATERIAL ONLY): {current_material}"
            )
        elif match_mode == "next":
            logging.info("ACE: Looking for endless spool match (NEXT READY SPOOL)")
        else:
            logging.info(
                f"ACE: Looking for endless spool match (EXACT): "
                f"{current_material} RGB({current_color[0]},{current_color[1]},{current_color[2]})"
            )

        for candidate_tool, cand_inv in self._candidates_after(current_tool):
            mismatch = self._mismatch(current_inv, cand_inv, match_mode)
            if mismatch is not None:
                logging.info(
                    f"ACE: T{candidate_tool} skipped - "
                    f"{_describe_mismatch(mismatch, current_inv, cand_inv)}"
                )
                continue

            if match_mode == "next":
                logging.info(f"ACE: Match found (next ready): T{candidate_tool}")
            else:
                logging.info(
                    f"ACE: Match found: T{current_tool} → T{candidate_tool}"
                )
            return candidate_tool

        if match_mode == "material":
            logging.info(
                f"ACE: No match for T{current_tool} (material: {current_material})"
            )
        elif match_mode == "next":
            logging.info(
                f"ACE: No ready spool available for T{current_tool}"
            )
        else:
            logging.info(
                f"ACE: No match for T{current_tool} "
                f"({current_material}, RGB({current_color[0]},{current_color[1]},{current_color[2]}))"
            )
        return -1

    @staticmethod
    def _slot_inventory(tool):
        """Inventory dict of a tool's slot, or None for a tool no ACE has."""
        inst_num = get_instance_from_tool(tool)
        local_slot = get_local_slot(tool, inst_num)
        if inst_num < 0 or local_slot < 0:
            return None
        ace_inst = ACE_INSTANCES.get(inst_num)
        if not ace_inst:
            return None
        return ace_inst.inventory[local_slot]

    def _candidates_after(self, current_tool):
        """(tool, inventory) of every other slot, from the next tool on,
        wrapping across all instances."""
        first_tool = get_tool_offset(0)
        total_tools = len(ACE_INSTANCES) * SLOTS_PER_ACE
        for offset in range(1, total_tools):
            candidate_tool = first_tool + (
                current_tool - first_tool + offset) % total_tools
            candidate = self._slot_inventory(candidate_tool)
            if candidate is not None:
                yield candidate_tool, candidate

    @staticmethod
    def _mismatch(wanted, candidate, match_mode):
        """Why candidate cannot replace wanted (a _MISMATCH_* key), or None."""
        if candidate.get("status") != "ready":
            return _MISMATCH_NOT_READY
        # In "next" mode we ignore material/color and take the first ready slot
        if match_mode == "next":
            return None
        wanted_material = _material(wanted)
        cand_material = _material(candidate)
        # SAFETY: Never match "unknown" materials - we don't know if they're compatible!
        if wanted_material == "unknown" or cand_material == "unknown":
            return _MISMATCH_UNKNOWN_MATERIAL
        if cand_material != wanted_material:
            return _MISMATCH_MATERIAL
        if match_mode == "exact" and (
                candidate.get("color", [0, 0, 0]) != wanted.get("color", [0, 0, 0])):
            return _MISMATCH_COLOR
        return None

    def execute_swap(self, from_tool, to_tool):
        """
        Execute endless spool swap with intelligent fallback.

        On feed failure:
        1. Smart unload the failed tool (to_tool)
        2. Search for next matching spool
        3. Retry with new candidate
        """
        self.gcode.respond_info(f"ACE: Endless spool swap: T{from_tool} → T{to_tool}")

        tried_tools = {from_tool}
        current_target_tool = to_tool
        max_swap_attempts = 3

        try:
            for swap_attempt in range(max_swap_attempts):
                try:
                    self.gcode.respond_info(
                        f"ACE: Tool change attempt {swap_attempt + 1}/{max_swap_attempts}: "
                        f"T{from_tool} → T{current_target_tool}"
                    )

                    from_inst_num = get_instance_from_tool(from_tool)
                    from_slot = get_local_slot(from_tool, from_inst_num)
                    if from_inst_num >= 0 and from_slot >= 0:
                        ace_inst = self.manager.instances[from_inst_num]
                        if ace_inst:
                            ace_inst.inventory[from_slot]["status"] = "empty"
                            self.manager._sync_inventory_to_persistent(from_inst_num, flush=False)
                            self.gcode.respond_info(f"ACE: Marked T{from_tool} as empty")

                    status = self.manager.perform_tool_change(from_tool, current_target_tool, is_endless_spool=True)
                    self.gcode.respond_info(f"ACE: {status}")

                    self.gcode.respond_info("ACE: Resuming print")
                    self.manager.gcode.run_script_from_command("RESUME PURGE=0")

                    return

                except Exception as load_error:
                    self.gcode.respond_info(
                        f"ACE: Tool change attempt {swap_attempt + 1} failed: {load_error}"
                    )

                    # If this was the last attempt, don't try recovery
                    if swap_attempt >= max_swap_attempts - 1:
                        raise

                    tried_tools.add(current_target_tool)

                    self.gcode.respond_info(
                        f"ACE: Attempting recovery - smart unload T{current_target_tool}..."
                    )

                    try:
                        to_inst_num = get_instance_from_tool(current_target_tool)
                        to_slot = get_local_slot(current_target_tool, to_inst_num)

                        if to_inst_num >= 0 and to_slot >= 0:
                            ace_inst = self.manager.instances[to_inst_num]
                            ace_inst._smart_unload_slot(
                                to_slot,
                                length=ace_inst.parkposition_to_toolhead_length,
                            )

                            ace_inst.inventory[to_slot]["status"] = "empty"
                            self.manager._sync_inventory_to_persistent(to_inst_num, flush=False)
                            self.gcode.respond_info(f"ACE: Marked T{current_target_tool} as empty (failed swap)")

                    except Exception as unload_error:
                        self.gcode.respond_info(
                            f"ACE: Warning - recovery unload failed: {unload_error}"
                        )

                    self.gcode.respond_info("ACE: Searching for next matching spool...")

                    # Temporarily mark tried tools as unavailable during search
                    saved_statuses = {}
                    for tried_tool in tried_tools:
                        tried_inst_num = get_instance_from_tool(tried_tool)
                        tried_slot = get_local_slot(tried_tool, tried_inst_num)
                        if tried_inst_num >= 0 and tried_slot >= 0:
                            tried_ace = self.manager.instances[tried_inst_num]
                            if tried_ace:
                                saved_statuses[tried_tool] = tried_ace.inventory[tried_slot]["status"]
                                tried_ace.inventory[tried_slot]["status"] = "searching"  # Temp status

                    try:
                        next_tool = self.find_exact_match(from_tool)
                    finally:
                        # Restore original statuses
                        for tried_tool, saved_status in saved_statuses.items():
                            tried_inst_num = get_instance_from_tool(tried_tool)
                            tried_slot = get_local_slot(tried_tool, tried_inst_num)
                            if tried_inst_num >= 0 and tried_slot >= 0:
                                tried_ace = self.manager.instances[tried_inst_num]
                                if tried_ace:
                                    tried_ace.inventory[tried_slot]["status"] = saved_status

                    if next_tool == -1 or next_tool in tried_tools:
                        raise Exception(
                            f"No more matching spools available (already tried: {sorted(tried_tools)})"
                        )

                    self.gcode.respond_info(f"ACE: Found next candidate: T{next_tool}")
                    current_target_tool = next_tool

        except Exception as e:
            self.gcode.respond_info("ACE: *** ENDLESS SPOOL SWAP FAILED ***")
            self.gcode.respond_info(f"ACE: {e}")
            self.gcode.respond_info("ACE: Print is PAUSED - fix the issue and RESUME manually")

            # Show user prompt for failed swap
            from_inst_num = get_instance_from_tool(from_tool)
            from_slot = get_local_slot(from_tool, from_inst_num)

            material = "unknown"
            color = [0, 0, 0]

            if from_inst_num >= 0 and from_slot >= 0:
                ace_inst = self.manager.instances[from_inst_num]
                if ace_inst:
                    # Restore status for retry
                    ace_inst.inventory[from_slot]["status"] = "ready"
                    self.manager._sync_inventory_to_persistent(from_inst_num, flush=False)

                    # Get material info for prompt
                    inv = ace_inst.inventory[from_slot]
                    material = inv.get("material", "unknown")
                    color = inv.get("color", [0, 0, 0])

            # Show failure prompt
            self._show_swap_failed_prompt(from_tool, from_inst_num, from_slot, material, color, str(e))

    def _show_swap_failed_prompt(self, tool_index, instance_num, local_slot, material, color, error_msg):
        """
        Show user prompt when endless spool swap fails.

        Args:
            tool_index: Original tool that ran out
            instance_num: ACE instance number
            local_slot: Local slot number
            material: Material type
            color: RGB color array
            error_msg: Error message explaining failure
        """
        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_begin Endless Spool Swap Failed"'
        )

        color_str = f"RGB({color[0]},{color[1]},{color[2]})"
        # Truncate error message to avoid overly long prompts
        short_error = error_msg.split('\n')[0][:100]

        prompt_text = (
            f"Endless spool swap failed for T{tool_index} (ACE {instance_num}, Slot {local_slot}) - "
            f"Material: {material}, Color: {color_str}. Error: {short_error}. "
            f"Please refill spool or load matching material, then RESUME."
        )

        self.gcode.run_script_from_command(
            f'RESPOND TYPE=command MSG="action:prompt_text {prompt_text}"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Resume|RESUME|primary"'
        )
        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_footer_button Cancel Print|CANCEL_PRINT|error"'
        )

        self.gcode.run_script_from_command(
            'RESPOND TYPE=command MSG="action:prompt_show"'
        )

    def get_status(self):
        """Return status dict for Klipper."""
        return {}
