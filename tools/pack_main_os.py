#!/usr/bin/env python3
"""Pack a raw Prophet 12 PIC32 image into a Main OS SysEx container."""

from __future__ import annotations

import argparse
from pathlib import Path

if __package__:
    from tools.unpack_main_os import (
        BLOCK_COUNT,
        FLASH_SIZE,
        RAW_BLOCK_SIZE,
        SYSEX_HEADER,
        firmware_checksum,
    )
else:
    from unpack_main_os import (  # type: ignore[no-redef]
        BLOCK_COUNT,
        FLASH_SIZE,
        RAW_BLOCK_SIZE,
        SYSEX_HEADER,
        firmware_checksum,
    )


def pack_7bit_block(data: bytes) -> bytes:
    """Encode one block with a fresh MSB bitmap for every seven raw bytes."""
    output = bytearray()
    for offset in range(0, len(data), 7):
        group = data[offset : offset + 7]
        msbs = 0
        for bit, value in enumerate(group):
            msbs |= ((value >> 7) & 1) << bit
        output.append(msbs)
        output.extend(value & 0x7F for value in group)
    return bytes(output)


def pack_main_os(image: bytes) -> bytes:
    """Pack a complete 512 KiB image and append its 14-bit checksum."""
    if len(image) != FLASH_SIZE:
        raise ValueError(
            f"unexpected firmware image size: {len(image)} (expected {FLASH_SIZE})"
        )

    payload = bytearray(SYSEX_HEADER)
    for block_index in range(BLOCK_COUNT):
        start = block_index * RAW_BLOCK_SIZE
        payload.extend(pack_7bit_block(image[start : start + RAW_BLOCK_SIZE]))

    checksum = firmware_checksum(image)
    payload.extend((checksum & 0x7F, (checksum >> 7) & 0x7F, 0xF7))
    return bytes(payload)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="raw 512 KiB PIC32 image")
    parser.add_argument("output", type=Path, help="destination Main OS .syx")
    args = parser.parse_args()

    image = args.input.read_bytes()
    sysex = pack_main_os(image)
    args.output.write_bytes(sysex)
    print(f"wrote {len(sysex)} bytes to {args.output}")
    print(f"stored 14-bit checksum: {firmware_checksum(image):#06x}")


if __name__ == "__main__":
    main()
