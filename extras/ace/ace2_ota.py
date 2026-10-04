"""ACE2 box firmware update over the IAP commands.

Owns the firmware-file checks, the chunk plan and the update state machine.
Transport-free: the caller injects send / call_later / report callables, so
the whole sequence runs in tests without a reactor or a serial port.

The sequence is stock's (Kobra X avata_main 2.0.2.2, OTA_FILAMENT_HUB_START,
0x01330048): IAP_UPGRADE {size, crc, "V"+x.y.z}, then IAP_FIRMWARE for every
64-byte chunk at 0x08024000 + offset with the last chunk zero-padded, then
IAP_UPGRADE_FINISH; three attempts per command; status polling of the box
suspended meanwhile. Divergences, each deliberate:
- a reply with a non-zero result code is a failed attempt (stock ignores IAP
  reply payloads);
- the vector table is sanity-checked before anything is sent;
- after FINISH the box is asked for its version (stock leaves that to its
  background poller).
"""

from __future__ import annotations

import enum
import logging
import os
import re
import struct
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .protocol import crc16_mcrf4xx

# Stock's file-name contract: the version sent to the box comes from here.
FIRMWARE_NAME_PATTERN = re.compile(
    r"ACE2_V(\d{1,3}\.\d{1,3}\.\d{1,3})\w*_(\d{8})\.bin"
)
# Where the box's bootloader stages a new image; the application itself is
# linked at 0x08008000 (reset vector of V1.1.31 and V1.1.34).
STAGING_BASE_ADDRESS = 0x08024000
CHUNK_SIZE_BYTES = 64
MAX_ATTEMPTS = 3
# Backstop for a request whose callback never comes (the serial manager
# drops queued callbacks on disconnect); its own timeout is 5 s.
STEP_WATCHDOG_S = 15.0
REBOOT_WAIT_S = 5.0
VERIFY_ATTEMPTS = 5
VERIFY_INTERVAL_S = 2.0
PROGRESS_STEP_PERCENT = 10

_RAM_REGION = 0x20000000
_FLASH_REGION = 0x08000000


class FirmwareImageError(ValueError):
    """The file cannot be an ACE2 application image."""


@dataclass(frozen=True)
class Ace2FirmwareImage:
    """A validated image and the values announced for it in IAP_UPGRADE."""

    data: bytes
    version: str  # as announced and as the image names itself: "V1.1.34"
    build_date: str  # YYYYMMDD from the file name
    crc: int  # CRC-16/MCRF4XX over data, unpadded

    @property
    def size(self) -> int:
        return len(self.data)


def parse_firmware_filename(filename: str) -> tuple[str, str]:
    """Return ("V1.1.34", "20260430") for "ACE2_V1.1.34_20260430.bin".

    Raises FirmwareImageError for any other name: the version sent to the
    box is taken from the name, as stock does.
    """
    match = FIRMWARE_NAME_PATTERN.fullmatch(filename)
    if match is None:
        raise FirmwareImageError(
            f"firmware file name '{filename}' does not match "
            "ACE2_V<x.y.z>_<YYYYMMDD>.bin"
        )
    return f"V{match.group(1)}".upper(), match.group(2)


def firmware_image_from_bytes(filename: str, data: bytes) -> Ace2FirmwareImage:
    """Validate one application image and compute what IAP_UPGRADE announces.

    The first two words of a Cortex-M image are the initial stack pointer
    (in RAM) and the reset handler (in flash, Thumb bit set); anything else
    is not an application image and is refused before the box is touched.
    """
    version, build_date = parse_firmware_filename(filename)
    if len(data) < 8:
        raise FirmwareImageError(
            f"'{filename}' is {len(data)} bytes; too short for a vector table"
        )
    initial_sp, reset_handler = struct.unpack_from("<II", data, 0)
    if initial_sp & 0xFFF00000 != _RAM_REGION:
        raise FirmwareImageError(
            f"'{filename}' initial stack pointer 0x{initial_sp:08X} is not in RAM "
            "(0x200xxxxx); not an ACE2 application image"
        )
    if reset_handler & 0xFF000000 != _FLASH_REGION or not reset_handler & 1:
        raise FirmwareImageError(
            f"'{filename}' reset vector 0x{reset_handler:08X} is not a Thumb "
            "address in flash (0x08xxxxxx); not an ACE2 application image"
        )
    return Ace2FirmwareImage(
        data=bytes(data),
        version=version,
        build_date=build_date,
        crc=crc16_mcrf4xx(data),
    )


def load_firmware_image(path: str) -> Ace2FirmwareImage:
    """Read and validate an image file. Raises OSError or FirmwareImageError."""
    with open(path, "rb") as firmware_file:
        data = firmware_file.read()
    return firmware_image_from_bytes(os.path.basename(path), data)


def plan_chunks(image: Ace2FirmwareImage) -> list[tuple[int, bytes]]:
    """(flash address, 64 bytes) per IAP_FIRMWARE frame; the last is zero-padded."""
    chunks = []
    for offset in range(0, image.size, CHUNK_SIZE_BYTES):
        chunk = image.data[offset:offset + CHUNK_SIZE_BYTES]
        chunks.append(
            (STAGING_BASE_ADDRESS + offset, chunk.ljust(CHUNK_SIZE_BYTES, b"\x00"))
        )
    return chunks


def normalize_version(version: str) -> str:
    """'V1.1.34', 'v1.1.34' and '1.1.34' compare equal."""
    return version.strip().upper().lstrip("V")


def update_refusal(instance) -> Optional[str]:
    """Why ``instance`` must not be flashed now, or None when it may.

    Stock stops active slots before flashing; here the operator does, so an
    update never interrupts filament motion it did not start.
    """
    if not instance.protocol.supports_firmware_update():
        return "this instance does not speak the ACE2 protocol; only ACE2 boxes take firmware updates"
    if not instance.serial_mgr.is_connected():
        return "the box is not connected"
    if instance._is_printing_or_paused():
        return "a print is running or paused"
    if instance._feed_assist_index >= 0:
        return (
            f"feed assist is active on slot {instance._feed_assist_index}; "
            "run ACE_DISABLE_FEED_ASSIST first"
        )
    status = instance._info.get("status")
    if status != "ready":
        return f"the box reports status '{status}', not 'ready'"
    return None


class UpdateOutcome(enum.Enum):
    VERIFIED = "verified"  # the box reported the new version after FINISH
    UNVERIFIED = "unverified"  # FINISH sent; the new version was not confirmed
    FAILED = "failed"  # aborted before FINISH; the staged image was not committed


@dataclass(frozen=True)
class UpdateResult:
    outcome: UpdateOutcome
    message: str


class _Phase(enum.Enum):
    IDLE = "idle"
    ANNOUNCE = "announce"
    TRANSFER = "transfer"
    COMMIT = "commit"
    REBOOT_WAIT = "reboot_wait"
    VERIFY = "verify"
    DONE = "done"


class Ace2FirmwareUpdater:
    """One IAP run for one box.

    Collaborators, all called from the reactor thread only (not thread-safe):
    - ``protocol``: an adapter with build_iap_*_request and build_get_info_request;
    - ``send(request, callback)``: queues a request; the callback gets
      ``response=`` a response dict, or None on timeout;
    - ``call_later(delay_s, fn)``: runs ``fn()`` once, later;
    - ``report(text)``: operator-visible progress line;
    - ``set_polling_suspended(bool)``: stops / resumes the box's status polls;
    - ``on_finished(UpdateResult)``: called exactly once, at the end.

    At most one update runs per process: the IAP state lives in the box and
    a second concurrent run on the shared bus could interleave chunks.
    """

    _active: Optional["Ace2FirmwareUpdater"] = None

    def __init__(
        self,
        image: Ace2FirmwareImage,
        *,
        protocol,
        send: Callable[[Mapping[str, Any], Callable[..., None]], None],
        call_later: Callable[[float, Callable[[], None]], None],
        report: Callable[[str], None],
        set_polling_suspended: Callable[[bool], None],
        on_finished: Callable[[UpdateResult], None],
        label: str = "ACE2",
    ):
        self.image = image
        self._protocol = protocol
        self._send = send
        self._call_later = call_later
        self._report = report
        self._set_polling_suspended = set_polling_suspended
        self._on_finished = on_finished
        self._label = label
        self._chunks = plan_chunks(image)
        self._phase = _Phase.IDLE
        self._chunk_index = 0
        self._attempt = 0
        self._verify_attempt = 0
        self._request_token = 0
        self._pending_token: Optional[int] = None
        self._last_reported_percent = 0
        self._commit_warning = ""

    @classmethod
    def active(cls) -> Optional["Ace2FirmwareUpdater"]:
        return cls._active

    @property
    def chunk_count(self) -> int:
        return len(self._chunks)

    def start(self) -> None:
        """Begin the run. Raises RuntimeError if any update is already running."""
        if Ace2FirmwareUpdater._active is not None:
            raise RuntimeError("an ACE2 firmware update is already running")
        if self._phase is not _Phase.IDLE:
            raise RuntimeError("this updater has already run")
        Ace2FirmwareUpdater._active = self
        self._set_polling_suspended(True)
        self._say(
            f"flashing {self.image.version} ({self.image.size} bytes, "
            f"{self.chunk_count} chunks, crc16 0x{self.image.crc:04X})"
        )
        self._phase = _Phase.ANNOUNCE
        self._attempt = 1
        self._guarded(self._send_current)

    # -- request plumbing -------------------------------------------------

    def _build_current_request(self):
        if self._phase is _Phase.ANNOUNCE:
            return self._protocol.build_iap_upgrade_request(
                self.image.size, self.image.crc, self.image.version
            )
        if self._phase is _Phase.TRANSFER:
            address, data = self._chunks[self._chunk_index]
            return self._protocol.build_iap_firmware_request(address, data)
        if self._phase is _Phase.COMMIT:
            return self._protocol.build_iap_finish_request()
        if self._phase is _Phase.VERIFY:
            return self._protocol.build_get_info_request()
        raise AssertionError(f"no request in phase {self._phase}")

    def _send_current(self) -> None:
        self._request_token += 1
        token = self._request_token
        self._pending_token = token
        request = self._build_current_request()

        def on_response(response=None):
            self._guarded(lambda: self._on_reply(token, response))

        def on_watchdog():
            self._guarded(lambda: self._on_reply(token, None))

        self._send(request, on_response)
        self._call_later(STEP_WATCHDOG_S, on_watchdog)

    def _on_reply(self, token: int, response) -> None:
        # Late replies, a second delivery of one reply and watchdogs of
        # answered requests all carry a spent token.
        if token != self._pending_token:
            return
        self._pending_token = None
        if self._phase is _Phase.VERIFY:
            self._on_verify_reply(response)
        else:
            self._on_iap_reply(response)

    def _guarded(self, step: Callable[[], None]) -> None:
        """Run one externally triggered step; an error ends the run as FAILED.

        Every entry point goes through here, so no exception can leave the
        box's polling suspended or the process-wide update slot taken.
        """
        try:
            step()
        except Exception as exc:
            logging.exception("%s: firmware update step failed", self._label)
            self._finish(UpdateOutcome.FAILED, f"internal error: {exc}")

    @staticmethod
    def _reply_problem(response) -> Optional[str]:
        if response is None:
            return "no answer"
        code = response.get("code", 0)
        if code:
            return f"rejected with code {code} ({response.get('msg', '?')})"
        return None

    # -- IAP phases -------------------------------------------------------

    def _step_name(self) -> str:
        if self._phase is _Phase.TRANSFER:
            address, _ = self._chunks[self._chunk_index]
            return f"chunk {self._chunk_index + 1}/{self.chunk_count} (0x{address:08X})"
        return {
            _Phase.ANNOUNCE: "IAP_UPGRADE",
            _Phase.COMMIT: "IAP_UPGRADE_FINISH",
        }[self._phase]

    def _on_iap_reply(self, response) -> None:
        problem = self._reply_problem(response)
        if problem is None:
            self._advance()
            return
        step = self._step_name()
        if self._attempt < MAX_ATTEMPTS:
            self._attempt += 1
            logging.info("%s: %s %s, retry %d/%d", self._label, step, problem,
                         self._attempt, MAX_ATTEMPTS)
            self._send_current()
            return
        if self._phase is _Phase.COMMIT:
            # Stock logs a failed FINISH and carries on: the box may have
            # rebooted before answering.
            self._commit_warning = f"{step} {problem} after {MAX_ATTEMPTS} attempts"
            self._begin_reboot_wait()
            return
        self._finish(
            UpdateOutcome.FAILED,
            f"{step} {problem} after {MAX_ATTEMPTS} attempts; update aborted "
            "before commit, the box keeps its current firmware",
        )

    def _advance(self) -> None:
        self._attempt = 1
        if self._phase is _Phase.ANNOUNCE:
            self._phase = _Phase.TRANSFER
            self._send_current()
        elif self._phase is _Phase.TRANSFER:
            self._chunk_index += 1
            self._report_progress()
            if self._chunk_index < self.chunk_count:
                self._send_current()
            else:
                self._phase = _Phase.COMMIT
                self._send_current()
        elif self._phase is _Phase.COMMIT:
            self._begin_reboot_wait()

    def _report_progress(self) -> None:
        percent = self._chunk_index * 100 // self.chunk_count
        step = percent - percent % PROGRESS_STEP_PERCENT
        if step > self._last_reported_percent:
            self._last_reported_percent = step
            self._say(f"{step}% ({self._chunk_index}/{self.chunk_count} chunks)")

    # -- reboot and verification ------------------------------------------

    def _begin_reboot_wait(self) -> None:
        self._phase = _Phase.REBOOT_WAIT
        self._say("image committed, waiting for the box to reboot")
        self._call_later(REBOOT_WAIT_S, lambda: self._guarded(self._begin_verify))

    def _begin_verify(self) -> None:
        if self._phase is not _Phase.REBOOT_WAIT:
            return
        self._phase = _Phase.VERIFY
        self._verify_attempt = 1
        self._send_current()

    def _on_verify_reply(self, response) -> None:
        reported = None
        if response is not None:
            result = response.get("result") or {}
            reported = result.get("version") or None
        if reported and normalize_version(reported) == normalize_version(self.image.version):
            self._finish(UpdateOutcome.VERIFIED, f"box reports {reported}")
            return
        if self._verify_attempt < VERIFY_ATTEMPTS:
            self._verify_attempt += 1
            self._call_later(VERIFY_INTERVAL_S, lambda: self._guarded(self._send_current))
            return
        if reported:
            detail = f"box still reports {reported}, expected {self.image.version}"
        else:
            detail = (
                "box did not answer GET_INFO after the reboot; "
                "run ACE_RECONNECT for this instance and check its version"
            )
        self._finish(UpdateOutcome.UNVERIFIED, detail)

    # -- end --------------------------------------------------------------

    def _finish(self, outcome: UpdateOutcome, message: str) -> None:
        if self._phase is _Phase.DONE:
            return
        self._phase = _Phase.DONE
        if self._commit_warning:
            message = f"{message} (warning: {self._commit_warning})"
        if Ace2FirmwareUpdater._active is self:
            Ace2FirmwareUpdater._active = None
        self._set_polling_suspended(False)
        self._say(f"{outcome.value}: {message}")
        self._on_finished(UpdateResult(outcome, message))

    def _say(self, text: str) -> None:
        self._report(f"{self._label}: firmware update: {text}")
