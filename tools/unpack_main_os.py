#!/usr/bin/env python3
"""Extract a Prophet 12 PIC32 image from a block-packed Main OS SysEx."""

from __future__ import annotations

import argparse
from pathlib import Path


SYSEX_HEADER = bytes.fromhex("f0 01 2a 77")
FLASH_SIZE = 0x80000
RAW_BLOCK_SIZE = 0x400
PACKED_BLOCK_SIZE = 1171
BLOCK_COUNT = FLASH_SIZE // RAW_BLOCK_SIZE
TRAILER_SIZE = 3


def firmware_checksum(image: bytes) -> int:
    """Return the firmware's 14-bit sum of little-endian 16-bit words."""
    if len(image) % 2:
        raise ValueError("firmware image length must be even")

    return sum(
        image[offset] | (image[offset + 1] << 8)
        for offset in range(0, len(image), 2)
    ) & 0x3FFF


def unpack_7bit_block(data: bytes) -> bytes:
    """Decode one independently packed block of MSB maps and 7-bit bytes."""
    output = bytearray()
    cursor = 0

    while cursor < len(data):
        msbs = data[cursor]
        cursor += 1
        group = data[cursor : cursor + 7]
        for bit, value in enumerate(group):
            if value & 0x80:
                raise ValueError(f"non-7-bit data byte at block offset {cursor + bit:#x}")
            output.append(value | (((msbs >> bit) & 1) << 7))
        cursor += len(group)

    return bytes(output)


def unpack_main_os(sysex: bytes) -> tuple[bytes, int]:
    """Decode 512 independent 1 KiB blocks and return image and stored checksum."""
    if not sysex.startswith(SYSEX_HEADER):
        raise ValueError(f"unexpected SysEx header: {sysex[:4].hex(' ')}")

    expected_size = len(SYSEX_HEADER) + BLOCK_COUNT * PACKED_BLOCK_SIZE + TRAILER_SIZE
    if len(sysex) != expected_size:
        raise ValueError(f"unexpected SysEx size: {len(sysex)} (expected {expected_size})")

    payload_start = len(SYSEX_HEADER)
    output = bytearray()
    for block_index in range(BLOCK_COUNT):
        start = payload_start + block_index * PACKED_BLOCK_SIZE
        block = unpack_7bit_block(sysex[start : start + PACKED_BLOCK_SIZE])
        if len(block) != RAW_BLOCK_SIZE:
            raise ValueError(
                f"decoded block {block_index} has {len(block)} bytes "
                f"(expected {RAW_BLOCK_SIZE})"
            )
        output.extend(block)

    checksum_lsb, checksum_msb, eox = sysex[-TRAILER_SIZE:]
    if eox != 0xF7:
        raise ValueError(f"unexpected SysEx terminator: {eox:#x}")
    checksum = checksum_lsb | (checksum_msb << 7)
    computed_checksum = firmware_checksum(output)
    if checksum != computed_checksum:
        raise ValueError(
            f"firmware checksum mismatch: stored {checksum:#06x}, "
            f"computed {computed_checksum:#06x}"
        )
    return bytes(output), checksum


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="official Prophet 12 Main OS .syx")
    parser.add_argument("output", type=Path, help="destination raw PIC32 image")
    args = parser.parse_args()

    image, stored_checksum = unpack_main_os(args.input.read_bytes())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(image)
    print(f"wrote {len(image)} bytes to {args.output}")
    print(f"verified 14-bit checksum: {stored_checksum:#06x}")


if __name__ == "__main__":
    main()
