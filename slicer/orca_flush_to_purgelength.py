#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OrcaSlicer post-processing:
- Parse flush_multiplier / flush_volumes_matrix / flush_volumes_vector comments
- Compute purge length (mm) from flush volume (mm^3) using filament diameter
- Rewrite tool-change lines: "T<n>"  ->  "T<n> PURGELENGTH=<len_mm>"
Assumptions:
- Matrix is row-major, index = old_tool * N + new_tool
- First tool selection uses vector[new_tool]
- Units: volumes in mm^3, length in mm
"""

import math
import os
import re
import sys
from typing import List, Optional, Tuple

MUL_RE   = re.compile(r"^\s*;\s*flush_multiplier\s*=\s*([0-9.+-Ee]+)")
MAT_RE   = re.compile(r"^\s*;\s*flush_volumes_matrix\s*=\s*([0-9.,\s+-Ee]+)")
VEC_RE   = re.compile(r"^\s*;\s*flush_volumes_vector\s*=\s*([0-9.,\s+-Ee]+)")
TOOL_RE  = re.compile(r"^\s*T(\d+)\s*(?:;.*)?$")  # lines like "T0" or " T1   ;comment"

def parse_number_list(s: str) -> List[float]:
    return [float(x) for x in re.split(r"[,\s]+", s.strip()) if x.strip()]

def infer_tool_count(matrix: List[float], vector: List[float]) -> Optional[int]:
    if matrix:
        n = int(round(math.sqrt(len(matrix))))
        if n * n == len(matrix):
            return n
    if vector:
        return len(vector)
    return None

def area_mm2(diameter: float) -> float:
    return math.pi * (diameter * 0.5) ** 2

def volume_to_length_mm(volume_mm3: float, filament_diam_mm: float) -> float:
    a = area_mm2(filament_diam_mm)
    if a <= 0:
        return 0.0
    return volume_mm3 / a

def compute_flush_volume(
    old_tool: Optional[int],
    new_tool: int,
    n_tools: int,
    matrix: List[float],
    vector: List[float],
    multiplier: float
) -> float:
    if old_tool is None:
        # first load: use vector for the new tool if available
        base = vector[new_tool] if (vector and new_tool < len(vector)) else 0.0
        return base * multiplier
    if old_tool == new_tool:
        return 0.0
    if not matrix or n_tools <= 0:
        return 0.0
    idx = old_tool * n_tools + new_tool
    if 0 <= idx < len(matrix):
        return matrix[idx] * multiplier
    return 0.0

def process_gcode(
    lines: List[str],
    filament_diameter: float,
    round_digits: int
) -> Tuple[List[str], dict]:
    multiplier = 1.0
    matrix: List[float] = []
    vector: List[float] = []

    # 1) Parse config comments (anywhere, but typically near the top)
    for ln in lines:
        m = MUL_RE.match(ln)
        if m:
            try:
                multiplier = float(m.group(1))
            except ValueError:
                pass
            continue
        m = MAT_RE.match(ln)
        if m:
            try:
                matrix = parse_number_list(m.group(1))
            except Exception:
                matrix = []
            continue
        m = VEC_RE.match(ln)
        if m:
            try:
                vector = parse_number_list(m.group(1))
            except Exception:
                vector = []
            continue

    n_tools = infer_tool_count(matrix, vector) or 0

    # 2) Walk file, rewrite T<n> lines
    current_tool: Optional[int] = None
    out_lines: List[str] = []
    toolchange_count = 0

    for ln in lines:
        m = TOOL_RE.match(ln)
        if m:
            new_tool = int(m.group(1))
            vol = compute_flush_volume(current_tool, new_tool, n_tools, matrix, vector, multiplier)
            length_mm = volume_to_length_mm(vol, filament_diameter)
            length_str = f"{round(length_mm, round_digits):.{round_digits}f}"

            # Prepend an ACE purge command and keep the original tool-change line intact
            out_lines.append(f"ACE_SET_PURGE_AMOUNT PURGELENGTH={length_str}\n")
            out_lines.append(ln)

            toolchange_count += 1
            current_tool = new_tool
        else:
            out_lines.append(ln)

    info = dict(
        flush_multiplier=multiplier,
        tools=n_tools,
        matrix_len=len(matrix),
        vector_len=len(vector),
        toolchanges=toolchange_count,
        filament_diameter=filament_diameter,
    )
    return out_lines, info

def main():
    if len(sys.argv) < 2:
        print("Usage: orca_flush_to_purgelength.py <gcode_file> [--diameter 1.75] [--round 2]")
        sys.exit(2)

    # Args
    gcode_path = sys.argv[1]
    # Optional flags
    filament_diameter = 1.75
    round_digits = 2

    if "--diameter" in sys.argv:
        try:
            i = sys.argv.index("--diameter")
            filament_diameter = float(sys.argv[i + 1])
        except Exception:
            pass

    if "--round" in sys.argv:
        try:
            i = sys.argv.index("--round")
            round_digits = max(0, int(sys.argv[i + 1]))
        except Exception:
            pass

    # Allow env override too (handy in Orca)
    filament_diameter = float(os.getenv("FLUSH_FILAMENT_DIAMETER", filament_diameter))

    try:
        with open(gcode_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"[flush2len] ERROR reading file: {e}", file=sys.stderr)
        sys.exit(1)

    new_lines, info = process_gcode(lines, filament_diameter, round_digits)

    # Write back in-place
    try:
        with open(gcode_path, "w", encoding="utf-8") as f:
            # add a small audit header (comment-only)
            f.write("; === flush2len postprocess begin ===\n")
            f.write(f"; multiplier={info['flush_multiplier']} tools={info['tools']} "
                    f"matrix={info['matrix_len']} vector={info['vector_len']} "
                    f"diameter={info['filament_diameter']} toolchanges={info['toolchanges']}\n")
            f.write("; This file was modified to add PURGELENGTH to T<n>.\n")
            f.write("; === flush2len postprocess end ===\n")
            f.writelines(new_lines)
    except Exception as e:
        print(f"[flush2len] ERROR writing file: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
