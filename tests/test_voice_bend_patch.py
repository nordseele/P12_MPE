# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Checks for the experimental single-voice pitch-bend test image."""

from __future__ import annotations

from pathlib import Path
import unittest

from tools.make_voice_bend_test import (
    CLEAR_A0,
    HANDLER_CONTINUE,
    HOOK,
    OR_A1_V0_A1,
    ORIGINAL_HOOK,
    SEND_COMMAND_TO_DSP,
    TRAMPOLINE,
    apply_patch,
    j,
    jal,
    li_a0,
    lui_a1,
    patch_words,
    read_word,
    voice_destination,
)
from tools.unpack_main_os import unpack_main_os
from tools.pack_main_os import pack_main_os

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_IMAGE = REPOSITORY_ROOT / "firmware/extracted/P12_Main_1_4_2_3.bin"


def jump_target(word: int, address: int) -> int:
    return ((address + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)


@unittest.skipUnless(CANONICAL_IMAGE.exists(), "canonical image not available")
class VoiceBendPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.image = CANONICAL_IMAGE.read_bytes()

    def test_encoders_match_instructions_already_in_the_image(self) -> None:
        self.assertEqual(lui_a1(0x81FC), read_word(self.image, 0x9D016A58))
        self.assertEqual(OR_A1_V0_A1, read_word(self.image, 0x9D016A5C))
        self.assertEqual(CLEAR_A0, read_word(self.image, 0x9D016A64))
        self.assertEqual(li_a0(0x1E), read_word(self.image, 0x9D016A44))
        self.assertEqual(jal(0x9D0053A8), read_word(self.image, 0x9D016B48))
        self.assertEqual(jal(0x9D00524C), ORIGINAL_HOOK[1])

    def test_jump_targets_decode_correctly(self) -> None:
        words = patch_words(0)
        self.assertEqual(jump_target(words[HOOK], HOOK), TRAMPOLINE)
        self.assertEqual(
            jump_target(words[TRAMPOLINE + 4], TRAMPOLINE + 4), SEND_COMMAND_TO_DSP
        )
        self.assertEqual(
            jump_target(words[TRAMPOLINE + 12], TRAMPOLINE + 12), HANDLER_CONTINUE
        )

    def test_voice_destinations_follow_flash_tables(self) -> None:
        # Tables at 0x9d070074 (target) and 0x9d070044 (selector).
        for voice in range(12):
            target, prefix = voice_destination(voice)
            self.assertEqual(target, read_word(self.image, 0x9D070074 + 4 * voice))
            selector = read_word(self.image, 0x9D070044 + 4 * voice)
            self.assertEqual(prefix, selector | 0x01FC0000)

    def test_only_the_seven_intended_words_change(self) -> None:
        for voice in (0, 1, 11):
            patched = apply_patch(self.image, voice)
            changed = {
                offset & ~3
                for offset in range(len(self.image))
                if patched[offset] != self.image[offset]
            }
            expected = {address - 0x9D000000 for address in patch_words(voice)}
            self.assertLessEqual(changed, expected)
            self.assertEqual(len(patched), len(self.image))

    def test_sysex_round_trip_of_patched_image(self) -> None:
        patched = apply_patch(self.image, 0)
        decoded, _checksum = unpack_main_os(pack_main_os(patched))
        self.assertEqual(decoded, patched)

    def test_rejects_non_canonical_input(self) -> None:
        with self.assertRaises(ValueError):
            apply_patch(bytes(len(self.image)), 0)


if __name__ == "__main__":
    unittest.main()
