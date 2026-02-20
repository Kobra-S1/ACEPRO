#!/usr/bin/env python3
"""
ace_console.py - Standalone ACE Pro diagnostic tool.

Usage:
    python3 ace_console.py /dev/ttyACM0
    python3 ace_console.py /dev/ttyACM1 --baud 115200 --interval 2.0
"""

import argparse
import json
import serial
import struct
import sys
import time


# ========== Framing ==========

def calc_crc(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        data = byte
        data ^= crc & 0xFF
        data ^= (data & 0x0F) << 4
        crc = ((data << 8) | (crc >> 8)) ^ (data >> 4) ^ (data << 3)
    return crc


def build_frame(request: dict, request_id: int) -> bytes:
    request["id"] = request_id
    payload = json.dumps(request).encode("utf-8")
    frame = bytearray([0xFF, 0xAA])
    frame += struct.pack("<H", len(payload))
    frame += payload
    frame += struct.pack("<H", calc_crc(payload))
    frame += b"\xFE"
    return bytes(frame)


def read_frame(ser: serial.Serial, buf: bytearray, timeout: float = 5.0) -> tuple[dict | None, bytearray]:
    """Read and parse one complete frame. Returns (parsed_dict, updated_buf)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        chunk = ser.read(4096)
        if chunk:
            buf += chunk

        while True:
            if len(buf) < 7:
                break

            # Sync to header
            if not (buf[0] == 0xFF and buf[1] == 0xAA):
                hdr = buf.find(bytes([0xFF, 0xAA]))
                if hdr == -1:
                    buf = bytearray()
                    break
                buf = buf[hdr:]
                if len(buf) < 7:
                    break

            payload_len = struct.unpack("<H", buf[2:4])[0]
            frame_len = 2 + 2 + payload_len + 2 + 1

            if len(buf) < frame_len:
                break

            terminator_idx = 4 + payload_len + 2
            if buf[terminator_idx] != 0xFE:
                next_hdr = buf.find(bytes([0xFF, 0xAA]), 1)
                buf = buf[next_hdr:] if next_hdr != -1 else bytearray()
                continue

            frame = bytes(buf[:frame_len])
            buf = bytearray(buf[frame_len:])

            payload = frame[4:4 + payload_len]
            crc_rx = frame[4 + payload_len:4 + payload_len + 2]
            crc_calc = struct.pack("<H", calc_crc(payload))

            if crc_rx != crc_calc:
                print(f"  [!] CRC mismatch - dropping frame", file=sys.stderr)
                continue

            try:
                return json.loads(payload.decode("utf-8")), buf
            except Exception as e:
                print(f"  [!] JSON decode error: {e}", file=sys.stderr)

        time.sleep(0.02)

    return None, buf


def send_and_receive(ser: serial.Serial, buf: bytearray, request: dict, request_id: int, timeout: float = 5.0) -> tuple[dict | None, bytearray]:
    frame = build_frame(request, request_id)
    ser.write(frame)
    return read_frame(ser, buf, timeout=timeout)


# ========== Main ==========

def main():
    parser = argparse.ArgumentParser(description="ACE Pro diagnostic console")
    parser.add_argument("port", help="Serial port, e.g. /dev/ttyACM0")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--interval", type=float, default=1.0, help="get_status poll interval in seconds (default: 1.0)")
    parser.add_argument("--count", type=int, default=0, help="Number of status polls before exit (0 = infinite)")
    args = parser.parse_args()

    print(f"Connecting to {args.port} @ {args.baud} baud...")
    try:
        ser = serial.Serial(
            port=args.port,
            baudrate=args.baud,
            timeout=0,
            write_timeout=1.0
        )
    except Exception as e:
        print(f"Failed to open port: {e}", file=sys.stderr)
        sys.exit(1)

    # Flush stale data
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    print(f"Port open. Sending get_info...\n")

    buf = bytearray()
    req_id = 0

    # --- get_info ---
    response, buf = send_and_receive(ser, buf, {"method": "get_info"}, req_id, timeout=5.0)
    req_id += 1
    if response:
        print(f"[get_info] {json.dumps(response, indent=2)}\n")
    else:
        print("[get_info] No response (timeout)\n", file=sys.stderr)

    # --- cyclic get_status ---
    print(f"Polling get_status every {args.interval}s  (Ctrl+C to stop)\n")
    poll = 0
    try:
        while args.count == 0 or poll < args.count:
            t0 = time.time()
            response, buf = send_and_receive(ser, buf, {"method": "get_status"}, req_id, timeout=5.0)
            req_id += 1
            elapsed = time.time() - t0
            poll += 1

            ts = time.strftime("%H:%M:%S")
            if response:
                result = response.get("result", {})
                status = result.get("status", "?")
                action = result.get("action", "?")
                temp = result.get("temp", "?")
                slots = result.get("slots", [])

                slot_summary = "  ".join(
                    f"S{s.get('index','?')}:{s.get('status','?')}"
                    for s in slots
                )
                print(f"[{ts}] #{poll:4d}  status={status}  action={action}  temp={temp}°C  |  {slot_summary}  ({elapsed*1000:.0f}ms)")

                # Print full JSON on first poll so user can see all fields
                if poll == 1:
                    print(f"\n  Full response:\n{json.dumps(response, indent=4)}\n")
            else:
                print(f"[{ts}] #{poll:4d}  TIMEOUT ({elapsed*1000:.0f}ms)", file=sys.stderr)

            # Sleep remaining interval
            spent = time.time() - t0
            remaining = args.interval - spent
            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        print(f"\nStopped after {poll} polls.")
    finally:
        ser.close()


if __name__ == "__main__":
    main()
