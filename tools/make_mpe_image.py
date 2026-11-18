#!/usr/bin/env python3
"""Build the experimental MPE test image (test 5) for Main OS 1.4.2.3.

Compiles firmware/patches/mpe/ with a standard GNU MIPS cross toolchain
(default, -Os; research/toolchain.md) or Microchip XC32 (--toolchain xc32,
-O1), links it at 0x9d073460,
installs the hooks and table relocations in the canonical image, and packs the
result as a Main OS SysEx update. See research/mpe_test_image.md and
research/mpe_menu.md.

Every hook site is checked against its expected original words before
patching. Only the hook words and the linked patch blob may change.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

if __package__:
    from tools.make_voice_bend_test import BASE, CANONICAL_SHA256, j, jal, read_word
    from tools.pack_main_os import pack_main_os
    from tools.unpack_main_os import unpack_main_os
else:
    from make_voice_bend_test import (  # type: ignore[no-redef]
        BASE,
        CANONICAL_SHA256,
        j,
        jal,
        read_word,
    )
    from pack_main_os import pack_main_os  # type: ignore[no-redef]
    from unpack_main_os import unpack_main_os  # type: ignore[no-redef]

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = REPOSITORY_ROOT / "firmware/patches/mpe"
DEFAULT_XC32 = Path("/Applications/microchip/xc32/v5.10/bin")
GNU_PREFIX = "mipsel-linux-gnu-"
GNU_DOCKER_TOOL = REPOSITORY_ROOT / "tools/gnu_mips/docker-tool"

PATCH_BASE = 0x9D073460
PATCH_LIMIT = 0x9D07D000  # startup and self-programming code start here
NOP = 0x00000000
ERASED = 0xFFFFFFFF

# Entry hooks: address -> (two original words, wrapper symbol, stub symbol).
# The entry becomes "j wrapper; nop"; the stub holds the two original words
# followed by "j entry+8; nop".
ENTRY_HOOKS = {
    0x9D01A3F0: ((0x3C09A001, 0x25229A80), "mpe_midi_submit_event", "orig_midi_submit_event"),
    0x9D01C074: ((0x27BDFFD0, 0xAFB10014), "mpe_allocate_voice", "orig_allocate_voice"),
    0x9D01C7FC: ((0x27BDFFD0, 0xAFB3001C), "mpe_release_voices", "orig_release_voices"),
    0x9D02C3F4: ((0x27BDFFE0, 0xAFB00010), "mpe_channel_accepted", "orig_channel_accepted"),
    0x9D02B06C: (
        (0x27BDFFE8, 0x00002021),
        "mpe_send_program_parameters",
        "orig_send_program_parameters",
    ),
    0x9D00A4E8: ((0x27BDFFB8, 0xAFB3002C), "mpe_global_menu_handler", "orig_global_menu_handler"),
    0x9D028A5C: ((0x27BDFF70, 0xAFB30074), "mpe_global_menu_display", "orig_global_menu_display"),
    # Arpeggiator list (research/arpeggiator.md)
    0x9D033CC4: ((0x27BDFFE0, 0x308400FF), "mpe_arp_list_add", "orig_arp_list_add"),
    0x9D033E60: ((0x27BDFFD8, 0x308400FF), "mpe_arp_list_remove", "orig_arp_list_remove"),
}

# Jump hooks: address -> (original j target, replacement symbol).
JUMP_HOOKS = {
    0x9D003D6C: (0x9D003A4C, "mpe_save_globals_page"),  # SaveGlobals tail: j SpiFlashProgram256
}

# Global menu (research/mpe_menu.md).
STOCK_GLOBAL_ROWS = 34
MPE_MENU_ROWS = 8
STOCK_NAME_TABLE = 0x9D07022C
STOCK_ROW_MAP = 0x9D0701E8
CURSOR_LIMIT_SITE = 0x9D00AB20  # li a2,0x21 (last row) in GlobalMenuStateHandler
# (lui site, lui word, addiu site, addiu word, table symbol)
TABLE_POINTER_SITES = [
    (0x9D028BEC, 0x3C0C9D07, 0x9D028BF0, 0x259E022C, "mpe_global_names"),  # display: row names
    (0x9D00A664, 0x3C099D07, 0x9D00A668, 0x253101E8, "mpe_global_map"),  # handler: value edit
    (0x9D028C74, 0x3C189D07, 0x9D028E30, 0x270F01E8, "mpe_global_map"),  # display: row values
    (0x9D028E94, 0x3C079D07, 0x9D028E98, 0x24E601E8, "mpe_global_map"),  # display: header label
]


def hi_lo(address: int) -> tuple[int, int]:
    low = address & 0xFFFF
    high = ((address >> 16) + (1 if low & 0x8000 else 0)) & 0xFFFF
    return high, low


def menu_rows() -> int:
    return STOCK_GLOBAL_ROWS + MPE_MENU_ROWS


def table_patch_words(symbols: dict[str, int]) -> dict[int, int]:
    words = {CURSOR_LIMIT_SITE: 0x24060000 | (menu_rows() - 1)}
    for lui_site, lui_word, addiu_site, addiu_word, symbol in TABLE_POINTER_SITES:
        high, low = hi_lo(symbols[symbol])
        words[lui_site] = (lui_word & 0xFFFF0000) | high
        words[addiu_site] = (addiu_word & 0xFFFF0000) | low
    return words


def check_menu_tables(image: bytes, blob: bytes, symbols: dict[str, int]) -> None:
    if read_word(image, CURSOR_LIMIT_SITE) != 0x24060000 | (STOCK_GLOBAL_ROWS - 1):
        raise ValueError("cursor limit site does not hold the expected code")
    for lui_site, lui_word, addiu_site, addiu_word, _symbol in TABLE_POINTER_SITES:
        if read_word(image, lui_site) != lui_word or read_word(image, addiu_site) != addiu_word:
            raise ValueError(f"table pointer site 0x{lui_site:08x} does not hold the expected code")
    rows = menu_rows()
    names = struct.unpack_from(f"<{rows}I", blob, symbols["mpe_global_names"] - PATCH_BASE)
    stock_names = struct.unpack_from(f"<{STOCK_GLOBAL_ROWS}I", image, STOCK_NAME_TABLE - BASE)
    if names[:STOCK_GLOBAL_ROWS] != stock_names:
        raise ValueError("relocated name table does not start with the stock names")
    for pointer in names[STOCK_GLOBAL_ROWS:]:
        if not PATCH_BASE <= pointer < PATCH_BASE + len(blob):
            raise ValueError("MPE row name does not point into the patch")
    offset = symbols["mpe_global_map"] - PATCH_BASE
    row_map = blob[offset : offset + rows]
    if row_map[:STOCK_GLOBAL_ROWS] != image[STOCK_ROW_MAP - BASE : STOCK_ROW_MAP - BASE + STOCK_GLOBAL_ROWS]:
        raise ValueError("relocated row map does not start with the stock map")
    if any(value != 0x26 for value in row_map[STOCK_GLOBAL_ROWS:]):
        raise ValueError("MPE rows must use the action-row marker 0x26")


# UI debug option: the three front-panel event producers
# (research/ui_and_parameter_access.md).
UI_DEBUG_HOOKS = {
    0x9D01A24C: ((0x27BDFFE0, 0xAFB10014), "ui_debug_button", "orig_ui_button"),
    0x9D01A298: ((0x27BDFFE0, 0x3C060006), "ui_debug_encoder", "orig_ui_encoder"),
    0x9D01A2EC: ((0x27BDFFE0, 0xAFB20018), "ui_debug_pot", "orig_ui_pot"),
    0x9D0067C8: (
        (0x27BDFFC0, 0xAFB1001C),
        "ui_debug_set_layer_parameter",
        "orig_set_layer_parameter",
    ),
    0x9D007588: (
        (0x27BDFFE0, 0xAFB10014),
        "ui_debug_set_global_parameter",
        "orig_set_global_parameter",
    ),
}


def entry_hooks(ui_debug: bool = False) -> dict:
    hooks = dict(ENTRY_HOOKS)
    if ui_debug:
        hooks.update(UI_DEBUG_HOOKS)
    return hooks


# Call-site hooks: address -> (original jal target, wrapper symbol).
# The delay slot at address+4 must be a nop and stays unchanged.
CALL_HOOKS = {
    0x9D01603C: (0x9D00524C, "mpe_local_wheel"),  # wheel: jal BroadcastDspCommand
    0x9D011D94: (0x9D00435C, "mpe_boot"),  # boot: jal LoadGlobalsFromSpiFlash
    0x9D0016C4: (0x9D01A074, "mpe_main_poll"),  # main loop helper, flag-9 clear path
    0x9D00194C: (0x9D01A074, "mpe_main_poll"),  # main loop helper, after ArpTick(1)
}

# Call-site hooks whose delay slot holds an argument move: address ->
# (original jal target, wrapper symbol, delay-slot word kept unchanged).
TrackAndAllocateNote = 0x9D01C2EC
TrackAndReleaseNote = 0x9D01C9DC
ArpTick = 0x9D0348D0
ARG_CALL_HOOKS = {
    0x9D03468C: (TrackAndAllocateNote, "mpe_arp_allocate", 0x02203021),  # ArpStep
    0x9D034AE8: (TrackAndReleaseNote, "mpe_arp_release", 0x02203021),  # ArpTick gate end
    0x9D034B8C: (TrackAndReleaseNote, "mpe_arp_release", 0x02003021),  # ArpTick step
    0x9D033C4C: (TrackAndReleaseNote, "mpe_arp_release", 0x00000000),  # ArpNoteOff
    0x9D033FBC: (TrackAndReleaseNote, "mpe_arp_release", 0x02002821),  # ArpListRemoveNote
    0x9D02CA18: (ArpTick, "mpe_ext_arp_tick", 0x00002021),  # MidiClockTick, layer A
    0x9D02CA20: (ArpTick, "mpe_ext_arp_tick", 0x24040001),  # MidiClockTick, layer B
    # Control Change as seen by the two stock parsers (research/mpe_manager_and_mcm.md)
    0x9D02BA04: (0x9D02B3D8, "mpe_din_cc_read", 0xA5B09A86),  # DIN: jal MidiReadDataByte (CC value)
    0x9D03589C: (0x9D02C3F4, "mpe_usb_cc_accepted", 0x02002021),  # USB: jal MidiIsChannelAccepted
}

CFLAGS = [
    "-O1",
    "-march=m4k",
    "-mno-mips16",
    "-mno-micromips",
    "-mno-abicalls",
    "-fno-pic",
    "-G0",
    "-mno-gpopt",
    "-fno-common",
    "-ffreestanding",
    "-fno-builtin",
    "-fno-tree-loop-distribute-patterns",
    "-fno-jump-tables",
    "-nostdlib",
    "-Wall",
    "-Wextra",
    "-Werror",
]
ASFLAGS = ["-march=m4k", "-mno-mips16", "-mno-micromips", "-mno-abicalls", "-fno-pic", "-G0"]


@dataclass(frozen=True)
class Toolchain:
    """How to call gcc, ld, nm, ... of one cross toolchain."""

    name: str
    launcher: tuple[str, ...]  # command prefix, e.g. the Docker wrapper
    prefix: str  # tool name prefix, may include a directory
    cflags: tuple[str, ...] = ()
    asflags: tuple[str, ...] = ()

    def tool(self, name: str) -> list[str]:
        return [*self.launcher, self.prefix + name]


def xc32_toolchain(bin_dir: Path = DEFAULT_XC32) -> Toolchain:
    return Toolchain("xc32", (), str(bin_dir / "xc32-"))


# A Linux-targeted GCC is used freestanding: no PIC/PIE, no stack protector,
# no unwind tables, soft float (the M4K core has no FPU), no libraries.
GNU_CFLAGS = (
    "-mabi=32", "-EL", "-msoft-float", "-fno-pie", "-fno-stack-protector",
    "-fno-asynchronous-unwind-tables", "-fno-unwind-tables", "-fno-ident",
)
GNU_ASFLAGS = ("-mabi=32", "-EL", "-msoft-float", "-fno-pie")


def gnu_toolchain(optimize: str = "-Os") -> Toolchain:
    """Debian GCC/binutils, native when on PATH, else in Docker."""
    native = shutil.which(GNU_PREFIX + "gcc") is not None
    launcher = () if native else (str(GNU_DOCKER_TOOL),)
    return Toolchain("gnu", launcher, GNU_PREFIX, (*GNU_CFLAGS, optimize), GNU_ASFLAGS)


def gnu_available() -> bool:
    """True when the GNU toolchain is on PATH or its Docker image is built."""
    if shutil.which(GNU_PREFIX + "gcc"):
        return True
    if not shutil.which("docker"):
        return False
    probe = subprocess.run(
        ["docker", "image", "inspect", os.environ.get("P12_GNU_MIPS_IMAGE", "p12-gnu-mips")],
        capture_output=True,
    )
    return probe.returncode == 0


# Integer MIPS32r2 instructions the M4K core of the PIC32MX executes; the
# GNU build is checked against this list (no FPU, MIPS16, or microMIPS).
M4K_MNEMONICS = frozenset("""
    addiu addu and andi b bal beq beql beqz beqzl bgez bgezl bgtz bgtzl blez blezl bltz bltzl
    bne bnel bnez bnezl clo clz di div divu ehb ei ext ins j jal jalr jr lb lbu lh lhu li ll lui lw
    lwl lwr madd maddu mfc0 mfhi mflo move movn movz msub msubu mtc0 mthi mtlo mul mult multu negu
    nop nor not or ori rotr rotrv sb sc seb seh sh sll sllv slt slti sltiu sltu sra srav srl srlv
    subu sw swl swr sync teq tne wsbh xor xori
""".split())


def disassembly_mnemonics(disassembly: str) -> set[str]:
    found = set()
    for line in disassembly.splitlines():
        fields = line.split("\t")
        if len(fields) >= 3 and fields[0].strip().endswith(":") and fields[2].strip():
            found.add(fields[2].split()[0])
    return found


def run(command: list[str]) -> str:
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return result.stdout


DEFAULT_OPTIONS = {
    "MPE_PAIR_MODE": 0,
    "MPE_BEND_RANGE": 48,
    "MPE_Y_TO_BREATH": 1,
    "MPE_Y_BRIGHTNESS": 1,
    "MPE_DSP_VOICE_BEND": 0,
}


def build_patch(
    toolchain: Toolchain | Path,
    pair_mode: bool,
    work: Path,
    options: dict[str, int] | None = None,
    ui_debug: bool = False,
    dsp_header: str | None = None,
    xyz_names: bool = False,
) -> tuple[bytes, dict[str, int], str]:
    """Compile and link the patch. Return (blob, symbols, disassembly).

    `toolchain` is a Toolchain, or an XC32 bin directory."""
    tc = xc32_toolchain(toolchain) if isinstance(toolchain, Path) else toolchain
    gcc, ld = tc.tool("gcc"), tc.tool("ld")
    cflags = [*CFLAGS, *tc.cflags]
    asflags = [*ASFLAGS, *tc.asflags]
    values = dict(DEFAULT_OPTIONS)
    values.update(options or {})
    values["MPE_PAIR_MODE"] = 1 if pair_mode else 0
    defines = [f"-D{name}={value}" for name, value in sorted(values.items())]
    c_obj, s_obj, elf = work / "mpe.o", work / "mpe_entry.o", work / "mpe.elf"
    m_obj = work / "mpe_menu.o"
    run([*gcc, *cflags, *defines, "-c", str(SOURCE_DIR / "mpe.c"), "-o", str(c_obj)])
    run([*gcc, *cflags, "-c", str(SOURCE_DIR / "mpe_menu.c"), "-o", str(m_obj)])
    run([*gcc, *asflags, "-c", str(SOURCE_DIR / "mpe_entry.S"), "-o", str(s_obj)])
    objects = [str(s_obj), str(c_obj), str(m_obj)]
    if ui_debug:
        u_obj, us_obj = work / "ui_debug.o", work / "ui_debug_entry.o"
        run([*gcc, *cflags, "-c", str(SOURCE_DIR / "ui_debug.c"), "-o", str(u_obj)])
        run([*gcc, *asflags, "-c", str(SOURCE_DIR / "ui_debug_entry.S"), "-o", str(us_obj)])
        objects += [str(us_obj), str(u_obj)]
    if xyz_names:
        n_obj = work / "source_names.o"
        run([*gcc, *cflags, "-c", str(SOURCE_DIR / "source_names.c"), "-o", str(n_obj)])
        objects.append(str(n_obj))
    if dsp_header is not None:  # --dsp-voice-bend: DSP boot stream redirection
        (work / "dsp_stream_data.h").write_text(dsp_header)
        d_obj, ds_obj = work / "dsp_stream.o", work / "dsp_stream_entry.o"
        run([*gcc, *cflags, "-I", str(work), "-c", str(SOURCE_DIR / "dsp_stream.c"), "-o", str(d_obj)])
        run([*gcc, *asflags, "-c", str(SOURCE_DIR / "dsp_stream_entry.S"), "-o", str(ds_obj)])
        objects += [str(ds_obj), str(d_obj)]
    run([*ld, "-T", str(SOURCE_DIR / "mpe.ld"), "-o", str(elf), *objects])

    sections = run([*tc.tool("size"), "-A", str(elf)])
    for line in sections.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0] in (".data", ".bss") and int(fields[1]) != 0:
            raise ValueError(f"patch has a writable section {fields[0]}")

    symbols: dict[str, int] = {}
    for line in run([*tc.tool("nm"), str(elf)]).splitlines():
        fields = line.split()
        if len(fields) == 3:
            symbols[fields[2]] = int(fields[0], 16) & 0xFFFFFFFF  # GNU nm sign-extends

    # Code ends before the read-only data (mpe.ld places *(.text*) before
    # *(.rodata*) in one output section); the $gp scan covers code only.
    code_end, data_start = PATCH_BASE, PATCH_LIMIT
    for line in run([*tc.tool("readelf"), "-s", "-W", str(elf)]).splitlines():
        fields = line.split()
        if len(fields) < 8 or fields[3] not in ("FUNC", "OBJECT"):
            continue
        address, size = int(fields[1], 16) & 0xFFFFFFFF, int(fields[2], 0)
        if not PATCH_BASE <= address < PATCH_LIMIT:
            continue
        if fields[3] == "FUNC":
            code_end = max(code_end, address + size)
        else:
            data_start = min(data_start, address)
    if code_end > data_start:
        raise ValueError("patch code and read-only data overlap")
    symbols[CODE_END_SYMBOL] = code_end

    blob_path = work / "mpe.bin"
    run([*tc.tool("objcopy"), "-O", "binary", "-j", ".text", str(elf), str(blob_path)])
    disassembly = run([*tc.tool("objdump"), "-d", str(elf)])
    return blob_path.read_bytes(), symbols, disassembly


CODE_END_SYMBOL = "__patch_code_end"  # computed by build_patch, not a linker symbol


def check_blob(blob: bytes, code_end: int | None = None) -> None:
    if len(blob) % 4:
        raise ValueError("patch size is not word aligned")
    if PATCH_BASE + len(blob) > PATCH_LIMIT:
        raise ValueError("patch does not fit in the free flash region")
    words = len(blob) // 4 if code_end is None else (code_end - PATCH_BASE + 3) // 4
    for index in range(words):
        word = struct.unpack_from("<I", blob, 4 * index)[0]
        opcode = word >> 26
        rs, rt = (word >> 21) & 31, (word >> 16) & 31
        # Reject any $gp (register 28) use by I-type and R-type instructions.
        if opcode != 2 and opcode != 3 and (rs == 28 or rt == 28 or (opcode == 0 and (word >> 11) & 31 == 28)):
            raise ValueError(f"patch uses $gp at 0x{PATCH_BASE + 4 * index:08x}")


# DSP boot sender loop (research/sharc_dsp_patch_feasibility.md)
DSP_SENDER_LOAD = 0x9D005E30  # lw a0,0(s3) -> jal dsp_stream_fetch (delay slot: clear v1)
DSP_SENDER_LAST = (0x9D005E9C, 0x9D005EEC)  # ori s1,zero,0xbbc3: index of the last word sent
LW_A0_S3 = 0x8E640000
ORI_S1_ZERO = 0x34110000
STOCK_DSP_WORDS = 0xBBC4

# Mod-matrix source names (read by 0x9d01afac): table entry -> (stock string, new symbol)
SOURCE_NAME_TABLE = 0x9D06D4B4  # one pointer per DSP source ID, ID 0 = "Off"
XYZ_SOURCE_NAMES = {
    SOURCE_NAME_TABLE + 4 * 0x0D: (0x9D06CF10, "mpe_source_name_x"),  # Pitchbend
    SOURCE_NAME_TABLE + 4 * 0x13: (0x9D06CF78, "mpe_source_name_z"),  # Aftertouch
    SOURCE_NAME_TABLE + 4 * 0x14: (0x9D06CF84, "mpe_source_name_y"),  # CC#2: Breath
}


def patch_words(
    blob: bytes,
    symbols: dict[str, int],
    ui_debug: bool = False,
    dsp_words: int | None = None,
    xyz_names: bool = False,
) -> dict[int, int]:
    words: dict[int, int] = {}
    for index in range(len(blob) // 4):
        words[PATCH_BASE + 4 * index] = struct.unpack_from("<I", blob, 4 * index)[0]
    for entry, (_original, wrapper, _stub) in entry_hooks(ui_debug).items():
        words[entry] = j(symbols[wrapper])
        words[entry + 4] = NOP
    for site, (_target, wrapper) in CALL_HOOKS.items():
        words[site] = jal(symbols[wrapper])
    for site, (_target, wrapper, _delay) in ARG_CALL_HOOKS.items():
        words[site] = jal(symbols[wrapper])
    for site, (_target, replacement) in JUMP_HOOKS.items():
        words[site] = j(symbols[replacement])
    words.update(table_patch_words(symbols))
    if dsp_words is not None:
        words[DSP_SENDER_LOAD] = jal(symbols["dsp_stream_fetch"])
        for site in DSP_SENDER_LAST:
            words[site] = ORI_S1_ZERO | (dsp_words - 1)
    if xyz_names:
        for entry, (_stock, symbol) in XYZ_SOURCE_NAMES.items():
            words[entry] = symbols[symbol]
    return words


def apply_patch(
    image: bytes,
    blob: bytes,
    symbols: dict[str, int],
    ui_debug: bool = False,
    dsp_words: int | None = None,
    xyz_names: bool = False,
) -> bytes:
    if hashlib.sha256(image).hexdigest() != CANONICAL_SHA256:
        raise ValueError("input is not the canonical Main OS 1.4.2.3 image")
    check_blob(blob, symbols.get(CODE_END_SYMBOL))
    for index in range(len(blob) // 4):
        if read_word(image, PATCH_BASE + 4 * index) != ERASED:
            raise ValueError("patch region is not erased")
    for entry, (original, _wrapper, stub) in entry_hooks(ui_debug).items():
        for offset, expected in enumerate(original):
            if read_word(image, entry + 4 * offset) != expected:
                raise ValueError(f"entry hook 0x{entry:08x} does not hold the expected code")
        stub_offset = symbols[stub] - PATCH_BASE
        relocated = struct.unpack_from("<4I", blob, stub_offset)
        if relocated != (original[0], original[1], j(entry + 8), NOP):
            raise ValueError(f"stub {stub} does not relocate 0x{entry:08x} exactly")
    for site, (target, _wrapper) in CALL_HOOKS.items():
        if read_word(image, site) != jal(target) or read_word(image, site + 4) != NOP:
            raise ValueError(f"call hook 0x{site:08x} does not hold the expected code")
    for site, (target, _wrapper, delay) in ARG_CALL_HOOKS.items():
        if read_word(image, site) != jal(target) or read_word(image, site + 4) != delay:
            raise ValueError(f"call hook 0x{site:08x} does not hold the expected code")
    for site, (target, _replacement) in JUMP_HOOKS.items():
        if read_word(image, site) != j(target):
            raise ValueError(f"jump hook 0x{site:08x} does not hold the expected code")
    if dsp_words is not None:
        if not 0 < dsp_words <= 0x10000:
            raise ValueError("DSP stream length out of range")
        if read_word(image, DSP_SENDER_LOAD) != LW_A0_S3:
            raise ValueError("DSP sender load does not hold the expected code")
        for site in DSP_SENDER_LAST:
            if read_word(image, site) != ORI_S1_ZERO | (STOCK_DSP_WORDS - 1):
                raise ValueError(f"DSP sender count 0x{site:08x} does not hold the expected code")
    if xyz_names:
        for entry, (stock, _symbol) in XYZ_SOURCE_NAMES.items():
            if read_word(image, entry) != stock:
                raise ValueError(f"source name entry 0x{entry:08x} does not hold the expected pointer")
    check_menu_tables(image, blob, symbols)

    patched = bytearray(image)
    for address, word in patch_words(blob, symbols, ui_debug, dsp_words, xyz_names).items():
        struct.pack_into("<I", patched, address - BASE, word)
    return bytes(patched)


def dsp_stream_from_image(patched: bytes, symbols: dict[str, int], words: int) -> bytes:
    """Evaluate the linked segment table as dsp_stream_word() does, word by word."""
    table, segments, index = symbols["dsp_segments"], [], 0
    while index < words:
        end, start, base = struct.unpack_from("<3I", patched, table - BASE + 12 * len(segments))
        segments.append((end, start, base))
        index = end
    return b"".join(
        patched[base - BASE + 4 * (i - start) : base - BASE + 4 * (i - start) + 4]
        for end, start, base in segments
        for i in range(start, end)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="canonical 1.4.2.3 raw image")
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--pair", action="store_true", help="Pair mode: both voices of a DSP per note")
    parser.add_argument(
        "--bend-range", type=int, default=48,
        help="pitch-bend range (semitones) forced on the DSPs while MPE is active; 0 = program value",
    )
    parser.add_argument("--no-y-breath", action="store_true", help="MPE Y > Timbre default Off (else Matrix+Cut)")
    parser.add_argument("--no-y-brightness", action="store_true", help="MPE Y > Timbre default Matrix (no cutoff term)")
    parser.add_argument("--ui-debug", action="store_true", help="add the UI event monitor overlay")
    parser.add_argument(
        "--dsp-outer-blocks", action="store_true",
        help="with --dsp-voice-bend: send the DSP overlay as plain blocks after the compressed block "
             "(experiment, saves about 12 KB of patch area)",
    )
    parser.add_argument(
        "--xyz-source-names", action="store_true",
        help="show Pitchbend, Aftertouch and CC#2: Breath as X, Z and Y in the mod matrix",
    )
    parser.add_argument(
        "--dsp-voice-bend", action="store_true",
        help="prototype: rebuilt SHARC stream with per-voice bend command 0x1f1 (not hardware tested)",
    )
    parser.add_argument(
        "--xc32", type=Path, default=Path(os.environ.get("XC32_BIN", DEFAULT_XC32)),
        help="XC32 bin directory",
    )
    parser.add_argument(
        "--toolchain", choices=("gnu", "xc32"), default="gnu",
        help="gnu (Debian GCC, default; native or tools/gnu_mips Docker image) or xc32 (Microchip, -O1)",
    )
    parser.add_argument("--name", help="output file stem instead of the generated one (e.g. P12-test-05)")
    parser.add_argument("--optimize", default="-Os", help="GNU toolchain only: -Os (default) or -O1")
    args = parser.parse_args()
    if args.toolchain == "gnu" and not gnu_available():
        parser.error(
            "GNU toolchain not found: run `docker build -t p12-gnu-mips tools/gnu_mips`, "
            "install mipsel-linux-gnu-gcc, or use --toolchain xc32"
        )
    if not 0 <= args.bend_range <= 127:
        parser.error("--bend-range must be in [0, 127]")
    options = {
        "MPE_DSP_VOICE_BEND": 1 if args.dsp_voice_bend else 0,
        "MPE_BEND_RANGE": args.bend_range,
        "MPE_Y_TO_BREATH": 0 if args.no_y_breath else 1,
        "MPE_Y_BRIGHTNESS": 0 if args.no_y_brightness else 1,
    }

    image = args.input.read_bytes()
    dsp_header, dsp_words, dsp_stream = None, None, None
    if args.dsp_voice_bend:
        from tools.build_dsp_stream import extract, rebuild
        from tools.dsp_voice_bend import c_header, overlay

        if args.dsp_outer_blocks:
            from tools.build_dsp_stream import insert_outer

            rebuilt = insert_outer(extract(image), overlay())
        else:
            rebuilt = rebuild(extract(image), overlay())
        dsp_header, dsp_words, dsp_stream = c_header(rebuilt), len(rebuilt.stream) // 4, rebuilt.stream

    with tempfile.TemporaryDirectory() as temp:
        toolchain = xc32_toolchain(args.xc32) if args.toolchain == "xc32" else gnu_toolchain(args.optimize)
        blob, symbols, disassembly = build_patch(
            toolchain, args.pair, Path(temp), options, args.ui_debug, dsp_header,
            args.xyz_source_names,
        )
    patched = apply_patch(
        image, blob, symbols, args.ui_debug, dsp_words, args.xyz_source_names
    )
    if dsp_words is not None and dsp_stream_from_image(patched, symbols, dsp_words) != dsp_stream:
        raise ValueError("the linked DSP segment table does not reproduce the rebuilt stream")
    sysex = pack_main_os(patched)
    round_trip, _checksum = unpack_main_os(sysex)
    if round_trip != patched:
        raise ValueError("SysEx round trip does not reproduce the patched image")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"P12_Main_1_4_2_3_mpe_test5_{'pair' if args.pair else 'single'}"
    stem += f"_pb{args.bend_range}" if args.bend_range else "_pbprog"
    if args.no_y_breath or args.no_y_brightness:
        stem += "_custom"
    if args.xyz_source_names:
        stem += "_xyz"
    if args.dsp_voice_bend:
        stem += "_dspbend"
    if args.ui_debug:
        stem += "_uidebug"
    if args.toolchain == "xc32":
        stem += "_xc32"
    elif args.optimize != "-Os":
        stem += "_gnu" + args.optimize.replace("-", "_").lower()
    if args.name:
        stem = args.name
    bin_path = args.output_dir / f"{stem}.bin"
    syx_path = args.output_dir / f"{stem}.syx"
    lst_path = args.output_dir / f"{stem}.lst"
    bin_path.write_bytes(patched)
    syx_path.write_bytes(sysex)
    lst_path.write_text(disassembly)
    print(f"patch: {len(blob)} bytes at 0x{PATCH_BASE:08x}-0x{PATCH_BASE + len(blob):08x}")
    for path in (bin_path, syx_path):
        print(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path}")


if __name__ == "__main__":
    main()
