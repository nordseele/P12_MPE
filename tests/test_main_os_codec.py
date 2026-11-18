"""Round-trip and canonical-image tests for the Main OS SysEx codec."""

from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from tools.pack_main_os import pack_7bit_block, pack_main_os
from tools.unpack_main_os import (
    FLASH_SIZE,
    PACKED_BLOCK_SIZE,
    RAW_BLOCK_SIZE,
    unpack_7bit_block,
    unpack_main_os,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_IMAGE = REPOSITORY_ROOT / "firmware/extracted/P12_Main_1_4_2_3.bin"
OFFICIAL_SYSEX = (
    REPOSITORY_ROOT
    / "firmware/official/releases/1.4.2.3/P12_Keyboard_Main_1.4.2.3.syx"
)


class MainOsCodecTests(unittest.TestCase):
    def test_one_block_round_trip_and_7bit_clean_output(self) -> None:
        block = bytes((index * 73 + 0x80) & 0xFF for index in range(RAW_BLOCK_SIZE))
        packed = pack_7bit_block(block)
        self.assertEqual(len(packed), PACKED_BLOCK_SIZE)
        self.assertTrue(all(value < 0x80 for value in packed))
        self.assertEqual(unpack_7bit_block(packed), block)

    def test_full_synthetic_image_round_trip(self) -> None:
        pattern = bytes(range(256))
        image = pattern * (FLASH_SIZE // len(pattern))
        sysex = pack_main_os(image)
        decoded, stored_checksum = unpack_main_os(sysex)
        self.assertEqual(decoded, image)
        self.assertEqual(stored_checksum, 0)

    def test_rejects_wrong_image_size(self) -> None:
        for size in (0, FLASH_SIZE - 1, FLASH_SIZE + 1):
            with self.subTest(size=size):
                with self.assertRaises(ValueError):
                    pack_main_os(bytes(size))

    @unittest.skipUnless(
        CANONICAL_IMAGE.is_file() and OFFICIAL_SYSEX.is_file(),
        "local canonical firmware evidence is not present",
    )
    def test_reproduces_official_1_4_2_3_sysex_byte_for_byte(self) -> None:
        image = CANONICAL_IMAGE.read_bytes()
        official = OFFICIAL_SYSEX.read_bytes()
        repacked = pack_main_os(image)

        self.assertEqual(
            hashlib.sha256(image).hexdigest(),
            "16c20113783115798ffd2db8ec67ae62599b1aa9a5483d89f97d61d071ad2c45",
        )
        self.assertEqual(
            hashlib.sha256(official).hexdigest(),
            "502715feb43236e880a97567a30b35809fea277b9eecc7de89c4a664c64e4583",
        )
        self.assertEqual(repacked, official)


if __name__ == "__main__":
    unittest.main()
