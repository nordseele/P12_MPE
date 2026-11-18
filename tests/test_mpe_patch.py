# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Checks for the experimental MPE test image (research/mpe_test_image.md).

Two parts, each skipped when its tool is missing:
- the host harness compiles firmware/patches/mpe/mpe.c natively against
  models of the stock firmware primitives and runs behavioral checks;
- the image build compiles the patch with XC32 and checks every changed word.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

from tools.make_mpe_image import (
    ARG_CALL_HOOKS,
    CALL_HOOKS,
    JUMP_HOOKS,
    UI_DEBUG_HOOKS,
    table_patch_words,
    DEFAULT_XC32,
    ENTRY_HOOKS,
    NOP,
    PATCH_BASE,
    PATCH_LIMIT,
    apply_patch,
    M4K_MNEMONICS,
    build_patch,
    disassembly_mnemonics,
    gnu_available,
    gnu_toolchain,
)
from tools.make_voice_bend_test import BASE, j, jal, read_word
from tools.pack_main_os import pack_main_os
from tools.unpack_main_os import unpack_main_os

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_IMAGE = REPOSITORY_ROOT / "firmware/extracted/P12_Main_1_4_2_3.bin"
HARNESS = REPOSITORY_ROOT / "tests/host/mpe_harness.c"
XC32 = Path(os.environ.get("XC32_BIN", DEFAULT_XC32))
HOST_CC = shutil.which("cc")


@unittest.skipUnless(HOST_CC, "no host C compiler")
class MpeHostHarnessTest(unittest.TestCase):
    def run_harness(self, pair: bool, voice_bend: bool = False) -> None:
        with tempfile.TemporaryDirectory() as temp:
            exe = Path(temp) / "harness"
            subprocess.run(
                [HOST_CC, "-std=c99", "-Wall", "-Wextra", "-Wno-unused-function",
                 f"-DMPE_PAIR_MODE={1 if pair else 0}",
                 f"-DMPE_DSP_VOICE_BEND={1 if voice_bend else 0}", "-o", str(exe), str(HARNESS)],
                check=True, capture_output=True, text=True,
            )
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_single_mode(self) -> None:
        self.run_harness(pair=False)

    def test_pair_mode(self) -> None:
        self.run_harness(pair=True)

    def test_twelve_voice_mode(self) -> None:
        self.run_harness(pair=False, voice_bend=True)


@unittest.skipUnless(CANONICAL_IMAGE.exists(), "canonical image not available")
@unittest.skipUnless((XC32 / "xc32-gcc").exists(), "XC32 not installed")
class MpeImageTest(unittest.TestCase):
    @classmethod
    def toolchain(cls):
        return XC32

    @classmethod
    def setUpClass(cls) -> None:
        cls.image = CANONICAL_IMAGE.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            cls.blob, cls.symbols, cls.disassembly = build_patch(cls.toolchain(), False, Path(temp))
        cls.patched = apply_patch(cls.image, cls.blob, cls.symbols)

    def test_patch_fits_free_flash(self) -> None:
        self.assertLessEqual(PATCH_BASE + len(self.blob), PATCH_LIMIT)

    def test_only_intended_words_change(self) -> None:
        allowed = set(range(PATCH_BASE, PATCH_BASE + len(self.blob), 4))
        allowed |= {entry for entry in ENTRY_HOOKS} | {entry + 4 for entry in ENTRY_HOOKS}
        allowed |= set(CALL_HOOKS) | set(ARG_CALL_HOOKS) | set(JUMP_HOOKS)
        allowed |= set(table_patch_words(self.symbols))
        changed = {
            BASE + offset
            for offset in range(0, len(self.image), 4)
            if self.image[offset : offset + 4] != self.patched[offset : offset + 4]
        }
        self.assertTrue(changed <= allowed, sorted(hex(a) for a in changed - allowed))

    def test_entry_hooks_jump_to_wrappers(self) -> None:
        for entry, (original, wrapper, stub) in ENTRY_HOOKS.items():
            self.assertEqual(read_word(self.patched, entry), j(self.symbols[wrapper]))
            self.assertEqual(read_word(self.patched, entry + 4), NOP)
            stub_words = struct.unpack_from("<4I", self.patched, self.symbols[stub] - BASE)
            self.assertEqual(stub_words, (*original, j(entry + 8), NOP))

    def test_call_hooks(self) -> None:
        for site, (_target, wrapper) in CALL_HOOKS.items():
            self.assertEqual(read_word(self.patched, site), jal(self.symbols[wrapper]))
            self.assertEqual(read_word(self.patched, site + 4), NOP)
        for site, (_target, wrapper, delay) in ARG_CALL_HOOKS.items():
            self.assertEqual(read_word(self.patched, site), jal(self.symbols[wrapper]))
            self.assertEqual(read_word(self.patched, site + 4), delay)
            self.assertEqual(read_word(self.image, site + 4), delay)

    def test_sysex_round_trip(self) -> None:
        image, _checksum = unpack_main_os(pack_main_os(self.patched))
        self.assertEqual(image, self.patched)

    def test_jump_and_menu_table_patches(self) -> None:
        for site, (_target, replacement) in JUMP_HOOKS.items():
            self.assertEqual(read_word(self.patched, site), j(self.symbols[replacement]))
        for site, word in table_patch_words(self.symbols).items():
            self.assertEqual(read_word(self.patched, site), word)
        self.assertEqual(read_word(self.patched, 0x9D00AB20), 0x24060029)  # last row: 0x22 + 8 MPE rows - 1


@unittest.skipUnless(CANONICAL_IMAGE.exists(), "canonical image not available")
@unittest.skipUnless(gnu_available(), "GNU MIPS toolchain not available (docker build -t p12-gnu-mips tools/gnu_mips)")
class GnuMpeImageTest(MpeImageTest):
    """The same checks with the standard GNU toolchain (research/toolchain.md)."""

    __unittest_skip__ = False  # not inherited from the XC32 class

    @classmethod
    def toolchain(cls):
        return gnu_toolchain("-Os")

    def test_m4k_instructions_only(self) -> None:
        unexpected = disassembly_mnemonics(self.disassembly) - M4K_MNEMONICS
        self.assertFalse(unexpected, sorted(unexpected))


@unittest.skipUnless(CANONICAL_IMAGE.exists(), "canonical image not available")
@unittest.skipUnless((XC32 / "xc32-gcc").exists(), "XC32 not installed")
class UiDebugImageTest(unittest.TestCase):
    def test_ui_debug_hooks_and_changed_words(self) -> None:
        image = CANONICAL_IMAGE.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            blob, symbols, _ = build_patch(XC32, False, Path(temp), ui_debug=True)
        patched = apply_patch(image, blob, symbols, ui_debug=True)
        hooks = {**ENTRY_HOOKS, **UI_DEBUG_HOOKS}
        for entry, (original, wrapper, stub) in hooks.items():
            self.assertEqual(read_word(patched, entry), j(symbols[wrapper]))
            stub_words = struct.unpack_from("<4I", patched, symbols[stub] - BASE)
            self.assertEqual(stub_words, (*original, j(entry + 8), NOP))
        allowed = set(range(PATCH_BASE, PATCH_BASE + len(blob), 4))
        allowed |= set(hooks) | {entry + 4 for entry in hooks} | set(CALL_HOOKS) | set(ARG_CALL_HOOKS)
        allowed |= set(JUMP_HOOKS) | set(table_patch_words(symbols))
        changed = {
            BASE + offset
            for offset in range(0, len(image), 4)
            if image[offset : offset + 4] != patched[offset : offset + 4]
        }
        self.assertTrue(changed <= allowed)
        self.assertEqual(unpack_main_os(pack_main_os(patched))[0], patched)


if __name__ == "__main__":
    unittest.main()
