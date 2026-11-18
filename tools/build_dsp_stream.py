#!/usr/bin/env python3
# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Rebuild the SHARC boot stream of the Prophet 12 with overlay blocks appended.

The application is one zlib block inside the stream (tools/extract_dsp_ldr.py).
Overlay blocks are appended to the inflated block stream, so they load after
the original blocks and overwrite them. zlib level 6, wbits 13, memLevel 8
reproduces the original compressed bytes exactly, so only the tail of the
compressed data changes (research/sharc_dsp_patch_feasibility.md).

The new stream is: original words [0, HEADER), new compressed-block header,
original words up to the first changed word, the new tail, then the original
final block. `Rebuilt.segments()` lists these pieces for the PIC32 sender.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

from tools.extract_dsp_ldr import COMPRESSED, IMAGE_BASE, KERNEL_BYTES, STREAM_START, STREAM_WORDS, inflate, walk

TAG_INIT_L16 = 3
TAG_INIT_L32 = 4
MARKER = 0xDEADBEEF  # the PIC32 sender loop branches on this word value


def swap_words(data: bytes) -> bytes:
    """Reverse the bytes of each 32-bit word (the stream stores zlib data this way)."""
    return b"".join(data[k : k + 4][::-1] for k in range(0, len(data), 4))


def compress(app: bytes) -> bytes:
    c = zlib.compressobj(6, zlib.DEFLATED, 13, 8)
    return c.compress(app) + c.flush()


def l16_block(sw_address: int, shorts: list[int]) -> bytes:
    """INIT_L16 block: short words, little-endian, padded to a 32-bit boundary."""
    data = struct.pack(f"<{len(shorts)}H", *shorts)
    data += b"\0" * (-len(data) % 4)
    return struct.pack("<III", TAG_INIT_L16, len(shorts), sw_address) + data


def l32_block(address: int, words: list[int]) -> bytes:
    """INIT_L32 block: 32-bit data words at normal-word addresses."""
    return struct.pack("<III", TAG_INIT_L32, len(words), address) + struct.pack(f"<{len(words)}I", *words)


@dataclass
class Rebuilt:
    original: bytes  # original stream
    stream: bytes  # new stream
    header: int  # byte offset of the compressed-block header
    first_diff: int  # byte offset of the first changed byte after the header

    def segments(self) -> list[tuple[str, int, int]]:
        """(source, offset, length) in bytes: 'image' = original stream, 'new' = new stream."""
        if self.stream == self.original:
            return [("image", 0, len(self.original))]
        start = self.first_diff - self.first_diff % 4
        final = len(self.original) - self.final_len
        return [
            ("image", 0, self.header),
            ("new", self.header, 12),
            ("image", self.header + 12, start - self.header - 12),
            ("new", start, len(self.stream) - start - self.final_len),
            ("image", final, self.final_len),
        ]

    @property
    def final_len(self) -> int:
        return 12 + KERNEL_BYTES  # final FINAL_INIT block (tag 0 header and IVT)


def extract(image: bytes) -> bytes:
    start = STREAM_START - IMAGE_BASE
    return image[start : start + STREAM_WORDS * 4]


def rebuild(stream: bytes, overlay: bytes = b"") -> Rebuilt:
    blocks = walk(stream, KERNEL_BYTES)
    (header, _, count, info, size), = [b for b in blocks if b[1] == COMPRESSED]
    if info >> 16 != 13 or blocks[-1][0] != len(stream) - 12 - KERNEL_BYTES:
        raise ValueError("unexpected stream layout")
    body = stream[header + 12 : header + 12 + size]
    app = inflate(body, count)
    if compress(app) != swap_words(body)[:count]:
        raise ValueError("recompression does not reproduce the original block")
    walk(overlay, 0)  # overlay must be well-formed blocks
    z = compress(app + overlay)
    pad = -len(z) % 4
    new_body = swap_words(z + b"0" * pad)  # the original pads with ASCII '0' (0x30)
    new = (stream[:header] + struct.pack("<III", COMPRESSED, len(z), 13 << 16 | pad) + new_body
           + stream[header + 12 + size :])
    first = next((i for i in range(header + 12, min(len(new), len(stream))) if new[i] != stream[i]),
                 min(len(new), len(stream)))
    out = Rebuilt(stream, new, header, first)
    for src, off, n in out.segments():
        if src == "new":
            words = struct.unpack_from(f"<{n // 4}I", new, off)
            if MARKER in words:
                raise ValueError("new data contains the sender marker word 0xdeadbeef")
    return out


@dataclass
class Inserted:
    """Overlay blocks inserted at the outer level, after the compressed block.

    Whether the decompression kernel processes plain blocks there is what
    P12-test-06 checks (research/sharc_dsp_patch_feasibility.md)."""

    original: bytes
    stream: bytes
    at: int  # byte offset of the final block in the original stream

    def segments(self) -> list[tuple[str, int, int]]:
        n = len(self.stream) - len(self.original)
        return [("image", 0, self.at), ("new", self.at, n), ("image", self.at, len(self.original) - self.at)]


def insert_outer(stream: bytes, overlay: bytes) -> Inserted:
    blocks = walk(stream, KERNEL_BYTES)
    final = blocks[-1][0]
    if blocks[-2][1] != COMPRESSED or blocks[-1][1] != 0 or final != len(stream) - 12 - KERNEL_BYTES:
        raise ValueError("unexpected stream layout")
    walk(overlay, 0)
    words = struct.unpack(f"<{len(overlay) // 4}I", overlay)
    if MARKER in words:
        raise ValueError("overlay contains the sender marker word 0xdeadbeef")
    return Inserted(stream, stream[:final] + overlay + stream[final:], final)
