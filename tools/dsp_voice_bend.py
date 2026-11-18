#!/usr/bin/env python3
"""Per-voice pitch-bend overlay for the SHARC DSP program (Main OS 1.4.2.3).

Prototype, not tested on hardware. Design: research/sharc_dsp_patch_feasibility.md.

- New per-voice command 0x1f1: `selector | 0x01f10000 | (offset + 0x8000)`,
  offset = pitch in 1/256 semitone (signed). It is stored in VBEND[voice].
- The per-oscillator pitch sum in FUN_sw_001273b4 adds VBEND[voice] to the
  per-DSP bend term, so the stock bend (0x1fc) still applies to both voices.
- New per-voice command 0x1f6 (research/sharc_voice_pressure_y.md):
  `selector | 0x01f60000 | (sub << 8) | value7`. sub 0 = pressure, written to
  the stock per-voice target dm:b3b90 / b3b92 (smoothed by the main loop);
  sub 1 = Y, written to YT[voice]. The 2-voice cutoff loop smooths YS[voice]
  towards YT[voice] like the stock controllers and adds it to the stock
  brightness term (CC74, 0x1fb), so YT = 0 keeps the stock behaviour.
- 0x1f6 sub 2 = per-voice Breath (research/sharc_voice_timbre_source.md),
  written to BT[voice]. The cutoff loop smooths BS[voice] towards it and
  stores BOUT[voice] = stock smoothed Breath (CC#2, 0x1f9) + BS[voice]. The
  Breath case of the matrix source switch (source 0x14) loads BOUT[A] and
  BOUT[B] and continues in the store sequence of the per-voice Aftertouch
  case, so BT = 0 keeps the stock Breath source.

Instructions are encoded from templates found in the same program (no SHARC
assembler); `python3 -m tools.dsp_voice_bend OUT.ldr` writes the rebuilt
stream so that Ghidra can disassemble and check them.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from tools.build_dsp_stream import Rebuilt, extract, l16_block, l32_block, rebuild
from tools.extract_dsp_ldr import STREAM_START

VBEND = 0xBBB00  # 2 words, free data below the C stack (dm:bc000)
DETOUR = 0x12755C  # R1 = DM(-0x36,I6); DM(-0x27,I6) = R2  (2 x 32-bit)
RESUME = 0x127560  # R1 = R1 + R12, ...
TRAMPOLINE = 0x12A8A0  # free code after sw:12a89c
HANDLER = 0x12A8C0
EPILOGUE = 0x129077  # common return path of FUN_sw_00128dba (after the 0x1f5 case)
TABLE_1F1 = 0xB43FC + (0x1F1 - 0x1E1)  # jump-table entry of FUN_sw_00128dba
YT = VBEND + 2  # Y targets, voice A/B (float)
YS = VBEND + 4  # Y smoothed, voice A/B (float)
YK = VBEND + 6  # smoothing factor 0.002 (float32), as the stock controllers
YK_BITS = 0x3B03126F
RANGE_JUMP = 0x128DD8  # JUMP IF GE sw:128df9: per-voice id outside 0x1e1-0x1f5
DEFAULT = 0x128DF9
DISPATCH = 0x12A8E0
LOOP_TOP = 0x12618B  # I1 = 0xb3be6, first instruction of the 2-voice cutoff loop
LOOP_RESUME = 0x12618E
Y_CODE = 0x12A940
PRESSURE_A, PRESSURE_B = 0xB3B90, 0xB3B92
BRIGHTNESS = 0xB3BA4  # smoothed stock CC74
BT = VBEND + 7    # per-voice Breath targets, voice A/B (float)
BS = VBEND + 9    # per-voice Breath smoothed, voice A/B (float)
BOUT = VBEND + 11  # matrix source 0x14 per voice: stock Breath + BS
BREATH = 0xB3B9E  # smoothed stock CC#2 Breath, the stock source 0x14
SOURCE_BREATH = 0x1272C0  # Breath case of the matrix source switch (computed jump sw:125db9)
SOURCE_STORES = 0x1272AE  # Aftertouch case after its two loads: 4 x {store R2 (A), store R1 (B)}
SOURCE_CODE = 0x12AA00


def jump(at: int, target: int, cond: int = 0x3E) -> list[int]:
    """48-bit relative JUMP (type 8, no delay slot); cond 0x3e = always, 0x00 = EQ."""
    off = (target - at) & 0xFFFFFF
    return [0x0700 | cond, off >> 16, off & 0xFFFF]


def load_abs(reg: int, address: int) -> list[int]:
    """48-bit Rn = DM(address), from `1002000b3ba4` (R2 = DM(0xb3ba4))."""
    return [0x1000 | reg, address >> 16, address & 0xFFFF]


def trampoline() -> list[int]:
    t = [0x9D08, 0x0159,  # DM(-0x27,I6) = R2   (displaced, from sw:12755e)
         0x9C08, 0x00CA,  # R1 = DM(-0x36,I6)   (displaced, from sw:12755c)
         0x9C08, 0x0156,  # R2 = DM(-0x2a,I6)   voice counter 0/1 (from sw:1279ef)
         0xC022]          # R2 = R2 + R2        sets AZ when voice 0
    base = TRAMPOLINE
    t += jump(base + 7, base + 0x10, cond=0x00)  # IF EQ JUMP voice_a
    t += load_abs(2, VBEND + 1)                  # R2 = DM(VBEND + 1)
    t += jump(base + 0xD, base + 0x13)           # JUMP add
    t += load_abs(2, VBEND)                      # voice_a: R2 = DM(VBEND)
    t += [0xC012]                                # add: R1 = R1 + R2
    t += jump(base + 0x14, RESUME)
    assert len(t) == 0x17
    return t


def store_abs(reg: int, address: int) -> list[int]:
    """48-bit DM(address) = Rn, from `1102000b3a5b` (DM(0xb3a5b) = R2)."""
    return [0x1100 | reg, address >> 16, address & 0xFFFF]


class Code:
    """Short-word code with forward labels for the 48-bit relative jumps."""

    def __init__(self, base: int) -> None:
        self.base, self.words, self.labels, self.fixups = base, [], {}, []

    def here(self) -> int:
        return self.base + len(self.words)

    def label(self, name: str) -> None:
        self.labels[name] = self.here()

    def emit(self, *words: int) -> None:
        self.words += words

    def jump(self, target: "int | str", cond: int = 0x3E) -> None:
        self.fixups.append((len(self.words), target, cond))
        self.words += [0, 0, 0]

    def done(self) -> list[int]:
        for i, target, cond in self.fixups:
            to = self.labels[target] if isinstance(target, str) else target
            self.words[i:i + 3] = jump(self.base + i, to, cond)
        return self.words


EQ = 0x00
GE = 0x22  # cond of the stock range check at sw:128dd8


def dispatch() -> list[int]:
    """Out-of-range per-voice id: 0x1f6 (R2 == 0x15 after COMPU) or the stock default.
    Entered from FUN_sw_00128dba with R4 = voice, R12 = value."""
    c = Code(DISPATCH)
    c.jump("h", cond=EQ)                 # AZ from COMPU(R2, R1), R1 = 0x15
    c.jump(DEFAULT)
    c.label("h")
    c.emit(0x0F81, 0x007F)               # R1 = 0x7f              (from 0f810015)
    c.emit(0x0184, 0x02C1)               # R2 = R12 AND R1        (from 01840b5c)
    c.emit(0x0F81, 0xFFF9)               # R1 = -7                (from 0f82fe1f)
    c.emit(0x018D, 0xA221)               # F2 = FLOAT R2 BY R1    (value / 128, as 0x1fa)
    c.emit(0x0F81, 0x0200)               # R1 = 0x200
    c.emit(0x0184, 0x01C1)               # R1 = R12 AND R1        sets AZ unless sub 2
    c.jump("py", cond=EQ)
    c.emit(0x0180, 0x1144)               # R1 = R4 + R4           sets AZ when voice A
    c.jump("ba", cond=EQ)
    c.emit(*store_abs(2, BT + 1))
    c.jump(EPILOGUE)
    c.label("ba")
    c.emit(*store_abs(2, BT))
    c.jump(EPILOGUE)
    c.label("py")
    c.emit(0x0F81, 0x0100)               # R1 = 0x100
    c.emit(0x0184, 0x01C1)               # R1 = R12 AND R1        sets AZ when sub 0
    c.jump("p", cond=EQ)
    c.emit(0x0180, 0x1144)               # R1 = R4 + R4           sets AZ when voice A (from 01801014)
    c.jump("ya", cond=EQ)
    c.emit(*store_abs(2, YT + 1))
    c.jump(EPILOGUE)
    c.label("ya")
    c.emit(*store_abs(2, YT))
    c.jump(EPILOGUE)
    c.label("p")
    c.emit(0x0180, 0x1144)               # R1 = R4 + R4
    c.jump("pa", cond=EQ)
    c.emit(*store_abs(2, PRESSURE_B))
    c.jump(EPILOGUE)
    c.label("pa")
    c.emit(*store_abs(2, PRESSURE_A))
    c.jump(EPILOGUE)
    return c.done()


def y_code() -> list[int]:
    """Per-voice Y in the cutoff loop: R7 = FIX(brightness + YS[v]) >> 16.
    R0-R2 are free at the loop top; I5 is 0xb360f for voice A."""
    c = Code(Y_CODE)
    c.emit(0x717E, 0x803F)               # R0 = I5                (from 717e803f)
    c.emit(0x0F01, 0x000B, 0x360F)       # R1 = 0xb360f           (from 0f02000b200d)
    c.emit(0x013E, 0x0000, 0xA001)       # COMP(R0, R1)
    c.jump("a", cond=EQ)
    for v, end in ((1, True), (0, False)):
        if v == 0:
            c.label("a")
        c.emit(*load_abs(0, BT + v))     # F0 = DM(BT[v])
        c.emit(*load_abs(1, BS + v))     # F1 = DM(BS[v])
        c.emit(*load_abs(2, YK))         # F2 = 0.002
        c.emit(0x0188, 0x2001)           # F0 = F0 - F1
        c.emit(0x0193, 0x0002)           # F0 = F0 * F2
        c.emit(0x0188, 0x1110)           # F1 = F1 + F0
        c.emit(*store_abs(1, BS + v))    # DM(BS[v]) = R1
        c.emit(*load_abs(0, BREATH))     # F0 = DM(0xb3b9e)       stock Breath
        c.emit(0x0188, 0x1101)           # F1 = F0 + F1
        c.emit(*store_abs(1, BOUT + v))  # DM(BOUT[v]) = R1
        c.emit(*load_abs(0, YT + v))     # F0 = DM(YT[v])
        c.emit(*load_abs(1, YS + v))     # F1 = DM(YS[v])
        c.emit(*load_abs(2, YK))         # F2 = 0.002
        c.emit(0x0188, 0x2001)           # F0 = F0 - F1           (from 01882061)
        c.emit(0x0193, 0x0002)           # F0 = F0 * F2           (from 019300c7)
        c.emit(0x0188, 0x1110)           # F1 = F1 + F0           (from 018810cf)
        c.emit(*store_abs(1, YS + v))    # DM(YS[v]) = R1
        if end:
            c.jump("sum")
    c.label("sum")
    c.emit(*load_abs(0, BRIGHTNESS))     # F0 = DM(0xb3ba4)       (stock sw:12614a)
    c.emit(0x0188, 0x1101)               # F1 = F0 + F1           (from 01881101)
    c.emit(0x0F80, 0x001F)               # R0 = 0x1f
    c.emit(0x018D, 0x9110)               # R1 = FIX F1 BY R0      (from 018d9110)
    c.emit(0x023E, 0x7801, 0xF071)       # R7 = ASHIFT R1 BY -0x10 (from 023e7801f072)
    c.emit(0x0F11, 0x000B, 0x3BE6)       # I1 = 0xb3be6           (displaced, stock sw:12618b)
    c.jump(LOOP_RESUME)
    return c.done()


def source_code() -> list[int]:
    """Matrix source 0x14 (Breath) per voice. Replaces the first two
    instructions of the stock case; R1 and R2 are free there (the Aftertouch
    case loads both), R0 is not (read at sw:125f38)."""
    c = Code(SOURCE_CODE)
    c.emit(0x043F, 0xAA3F)               # MODIFY I4 = I6,M5      (displaced, stock sw:1272c0)
    c.emit(*load_abs(2, BOUT))           # R2 = DM(BOUT[A])       (as R2 = DM(0xb3b91))
    c.emit(*load_abs(1, BOUT + 1))       # R1 = DM(BOUT[B])       (as R1 = DM(0xb3b93))
    c.jump(SOURCE_STORES)
    return c.done()


def handler() -> list[int]:
    h = [0x0F14, VBEND >> 16, VBEND & 0xFFFF,  # I4 = VBEND        (from sw:129070)
         0x707E, 0x123F,                        # M4 = R4 (voice)   (from sw:129073)
         0x0F82, 0x8000,                        # R2 = -0x8000      (from sw:129afb)
         0xC0C2,                                # R12 = R12 + R2    (value - 0x8000)
         0x493E, 0x863F]                        # DM(M4,I4) = R12   (from sw:129075)
    h += jump(HANDLER + len(h), EPILOGUE)
    return h


def overlay() -> bytes:
    return (l16_block(DETOUR, jump(DETOUR, TRAMPOLINE) + [0x0001])  # JUMP; 16-bit NOP
            + l16_block(TRAMPOLINE, trampoline())
            + l16_block(HANDLER, handler())
            + l32_block(TABLE_1F1, [HANDLER])
            + l16_block(RANGE_JUMP, jump(RANGE_JUMP, DISPATCH, GE))
            + l16_block(DISPATCH, dispatch())
            + l16_block(LOOP_TOP, jump(LOOP_TOP, Y_CODE))
            + l16_block(Y_CODE, y_code())
            + l16_block(SOURCE_BREATH, jump(SOURCE_BREATH, SOURCE_CODE) + [0x0001, 0x0001])  # JUMP; 2 NOPs
            + l16_block(SOURCE_CODE, source_code())
            + l32_block(VBEND, [0, 0, 0, 0, 0, 0, YK_BITS, 0, 0, 0, 0, 0, 0]))


def main() -> None:
    image = Path("firmware/extracted/P12_Main_1_4_2_3.bin").read_bytes()
    out = rebuild(extract(image), overlay())
    Path(sys.argv[1]).write_bytes(out.stream)
    for seg in out.segments():
        print(*seg)



SENDER_LOAD = 0x9D005E30  # lw a0,0(s3) in the DSP boot sender loop
SENDER_LAST = (0x9D005E9C, 0x9D005EEC)  # ori s1,zero,0xbbc3 (index of the last word)
LW_A0_S3 = 0x8E640000
ORI_S1 = 0x34110000


def c_header(out: Rebuilt) -> str:
    """dsp_stream_data.h for firmware/patches/mpe/dsp_stream.c."""
    new_words: list[int] = []
    rows, index = [], 0
    for src, off, n in out.segments():
        words = n // 4
        if src == "image":
            ref = f"(const uint32_t *)0x{STREAM_START + off:08x}u"
        else:
            ref = f"dsp_new_words + {len(new_words)}"
            new_words += struct.unpack_from(f"<{words}I", out.stream, off)
        rows.append(f"    {{ {index + words}u, {index}u, {ref} }},")
        index += words
    body = ",\n".join("    " + ", ".join(f"0x{w:08x}u" for w in new_words[k:k + 6])
                      for k in range(0, len(new_words), 6))
    return ("/* Generated by tools/dsp_voice_bend.py. Do not edit. */\n"
            f"#define DSP_STREAM_WORDS {index}u\n"
            f"static const uint32_t dsp_new_words[{len(new_words)}] = {{\n{body}\n}};\n"
            "static const struct dsp_segment dsp_segments[] = {\n" + "\n".join(rows) + "\n};\n")


def stream_words(out: Rebuilt) -> int:
    return len(out.stream) // 4


if __name__ == "__main__":
    main()
