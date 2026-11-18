#!/usr/bin/env python3
# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Build the single-voice pitch-bend hardware test image (experimental).

The patch reroutes the Normal-mode pitch-bend broadcast to one physical voice so
that a listening test can show whether the SHARCs honour the per-voice
selector for command class 0x1fc. See research/voice_bend_test.md.

Only the canonical Main OS 1.4.2.3 image is accepted as input.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct

if __package__:
    from tools.pack_main_os import pack_main_os
else:
    from pack_main_os import pack_main_os  # type: ignore[no-redef]

BASE = 0x9D000000
CANONICAL_SHA256 = "16c20113783115798ffd2db8ec67ae62599b1aa9a5483d89f97d61d071ad2c45"

HOOK = 0x9D016BE8  # Normal-mode pitch-bend broadcast, reached only from 0x9d016a48
TRAMPOLINE = 0x9D073460  # start of the erased region after the linked image
SEND_COMMAND_TO_DSP = 0x9D0052F4
HANDLER_CONTINUE = 0x9D0136B4

# Original words, little-endian as stored in flash.
ORIGINAL_HOOK = (
    0x3C04C1FC,  # lui  a0,0xc1fc
    0x0F401493,  # jal  BroadcastDspCommand (0x9d00524c)
)
ERASED = 0xFFFFFFFF
TRAMPOLINE_WORDS = 5

PITCH_BEND_OPCODE = 0x01FC0000
VOICE_SELECTORS = (0x80000000, 0x40000000)  # even, odd physical voice


def j(target: int) -> int:
    return 0x08000000 | ((target >> 2) & 0x03FFFFFF)


def jal(target: int) -> int:
    return 0x0C000000 | ((target >> 2) & 0x03FFFFFF)


def lui_a1(value: int) -> int:
    return 0x3C050000 | (value & 0xFFFF)


def li_a0(value: int) -> int:
    return 0x24040000 | (value & 0xFFFF)  # addiu a0,zero,value


OR_A1_V0_A1 = 0x00452825
CLEAR_A0 = 0x00002021  # addu a0,zero,zero


def voice_destination(voice: int) -> tuple[int, int]:
    """Return (DSP target, command prefix) for one physical voice."""
    if not 0 <= voice < 12:
        raise ValueError("voice must be in [0, 12)")
    return voice // 2, VOICE_SELECTORS[voice % 2] | PITCH_BEND_OPCODE


def patch_words(voice: int) -> dict[int, int]:
    target, prefix = voice_destination(voice)
    return {
        HOOK: j(TRAMPOLINE),
        HOOK + 4: lui_a1(prefix >> 16),  # delay slot; v0 still holds value14
        TRAMPOLINE + 0: OR_A1_V0_A1,
        TRAMPOLINE + 4: jal(SEND_COMMAND_TO_DSP),
        TRAMPOLINE + 8: li_a0(target),  # delay slot
        TRAMPOLINE + 12: j(HANDLER_CONTINUE),
        TRAMPOLINE + 16: CLEAR_A0,  # delay slot, as in the original path
    }


def read_word(image: bytes | bytearray, address: int) -> int:
    return struct.unpack_from("<I", image, address - BASE)[0]


def apply_patch(image: bytes, voice: int) -> bytes:
    if hashlib.sha256(image).hexdigest() != CANONICAL_SHA256:
        raise ValueError("input is not the canonical Main OS 1.4.2.3 image")
    for offset, expected in enumerate(ORIGINAL_HOOK):
        if read_word(image, HOOK + 4 * offset) != expected:
            raise ValueError("hook site does not contain the expected instructions")
    for index in range(TRAMPOLINE_WORDS):
        if read_word(image, TRAMPOLINE + 4 * index) != ERASED:
            raise ValueError("trampoline area is not erased")
    patched = bytearray(image)
    for address, word in patch_words(voice).items():
        struct.pack_into("<I", patched, address - BASE, word)
    return bytes(patched)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="canonical 1.4.2.3 raw image")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--voice", type=int, default=0, help="physical voice 0-11")
    args = parser.parse_args()

    patched = apply_patch(args.input.read_bytes(), args.voice)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"P12_Main_1_4_2_3_pbtest_voice{args.voice}"
    bin_path = args.output_dir / f"{stem}.bin"
    syx_path = args.output_dir / f"{stem}.syx"
    bin_path.write_bytes(patched)
    syx_path.write_bytes(pack_main_os(patched))
    for path in (bin_path, syx_path):
        print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}")


if __name__ == "__main__":
    main()
