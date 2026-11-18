#!/usr/bin/env python3
"""Extract the SHARC boot stream (ADI .ldr) embedded in the Prophet 12 PIC32 image.

The PIC32 broadcasts 0xbbc4 32-bit words starting at 0x9d0399e8 to the six
ADSP-21479 DSPs over SPI (loop at 0x9d005e1c, research/sharc_dsp_code.md).
The words are a standard ADI boot stream: a 256-instruction boot kernel,
a decompression kernel ending in FINAL_INIT, then one zlib-compressed
application block and a second FINAL_INIT.

Usage: extract_dsp_ldr.py IMAGE.bin OUT.ldr [--inflate OUT_APP.bin]
"""

from __future__ import annotations

import argparse
import hashlib
import struct
import zlib
from pathlib import Path

IMAGE_BASE = 0x9D000000
STREAM_START = 0x9D0399E8
STREAM_WORDS = 0xBBC4
KERNEL_BYTES = 256 * 6
COMPRESSED = 0x2000
CANONICAL_1_4_2_3 = "16c20113783115798ffd2db8ec67ae62599b1aa9a5483d89f97d61d071ad2c45"
STREAM_1_4_2_3 = "641a3e984911cfecc22ee36895d7f23c42b1dfeb31302cfef9219946d38ff5a0"


def block_size(tag: int, count: int, address: int) -> int:
    """Data bytes after a block header (ADI 214xx 32-bit boot stream)."""
    if tag == 0:
        return KERNEL_BYTES
    if tag in (1, 2, 7, 8):
        return 0
    if tag == 3:
        return ((count + 1) // 2) * 4
    if tag in (4, 9, 10):
        return count * 4
    if tag == 5:
        return ((count + 1) // 2) * 12
    if tag == 6:
        return count * 8
    if tag == COMPRESSED:
        for size, info in ((count, address), (address, count)):
            wbits, pad = info >> 16, info & 0xFFFF
            if 8 <= wbits <= 15 and pad < 4 and (size + pad) % 4 == 0:
                return size + pad
    raise ValueError(f"invalid block header tag={tag:#x} count={count:#x} address={address:#x}")


def walk(stream: bytes, offset: int) -> list[tuple[int, int, int, int, int]]:
    """Return (offset, tag, count, address, size) for every block header."""
    blocks = []
    while offset + 12 <= len(stream):
        tag, count, address = struct.unpack_from("<III", stream, offset)
        size = block_size(tag, count, address)
        blocks.append((offset, tag, count, address, size))
        offset += 12 + size
    if offset != len(stream):
        raise ValueError(f"stream does not end on a block boundary ({offset:#x})")
    return blocks


def inflate(data: bytes, length: int) -> bytes:
    """Inflate a compressed block: each 32-bit word is stored byte-reversed."""
    swapped = bytearray(data)
    for k in range(0, len(data) - 3, 4):
        swapped[k : k + 4] = data[k : k + 4][::-1]
    return zlib.decompress(bytes(swapped[:length]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--inflate", type=Path, help="also write the inflated application block stream")
    args = parser.parse_args()

    image = args.image.read_bytes()
    if hashlib.sha256(image).hexdigest() != CANONICAL_1_4_2_3:
        print("warning: not the canonical 1.4.2.3 image; offsets may not apply")
    start = STREAM_START - IMAGE_BASE
    stream = image[start : start + STREAM_WORDS * 4]
    blocks = walk(stream, KERNEL_BYTES)
    args.output.write_bytes(stream)
    digest = hashlib.sha256(stream).hexdigest()
    print(f"{args.output}: {len(stream)} bytes, SHA-256 {digest}"
          + ("" if digest == STREAM_1_4_2_3 else " (differs from 1.4.2.3)"))
    for offset, tag, count, address, size in blocks:
        name = "COMPRESSED" if tag == COMPRESSED else f"tag {tag}"
        print(f"  +{offset:#07x} {name:<10} count={count:#x} address={address:#x} data={size:#x}")
        if tag == COMPRESSED and args.inflate:
            body = stream[offset + 12 : offset + 12 + size]
            app = inflate(body, count if (count + (address & 0xFFFF)) % 4 == 0 else address)
            args.inflate.write_bytes(app)
            print(f"    inflated {len(app)} bytes -> {args.inflate}")


if __name__ == "__main__":
    main()
