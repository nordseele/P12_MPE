"""Checks for tools/build_dsp_stream.py (needs the canonical image, Git-ignored)."""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from tools.build_dsp_stream import extract, insert_outer, l16_block, l32_block, rebuild, swap_words
from tools.dsp_voice_bend import (DEFAULT, DISPATCH, EPILOGUE, GE, RANGE_JUMP, RESUME, SOURCE_BREATH,
                                  SOURCE_CODE, TRAMPOLINE, Y_CODE, dispatch, handler, jump, overlay,
                                  source_code, trampoline, y_code)
from tools.dsp_voice_bend import c_header
from tools.extract_dsp_ldr import COMPRESSED, KERNEL_BYTES, inflate, walk
from tools.make_mpe_image import (
    BASE, DSP_SENDER_LAST, DSP_SENDER_LOAD, XYZ_SOURCE_NAMES, apply_patch, build_patch, dsp_stream_from_image,
    gnu_available, gnu_toolchain,
)

IMAGE = Path(__file__).resolve().parents[1] / "firmware/extracted/P12_Main_1_4_2_3.bin"


@unittest.skipUnless(IMAGE.exists(), "canonical image not present")
class DspStreamTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.stream = extract(IMAGE.read_bytes())

    def test_empty_overlay_is_identity(self) -> None:
        out = rebuild(self.stream)
        self.assertEqual(out.stream, self.stream)
        self.assertEqual(out.segments(), [("image", 0, len(self.stream))])

    def test_overlay_round_trip(self) -> None:
        overlay = l16_block(0x12A8A0, [0x0001] * 5) + l32_block(0xBBB00, [0, 0])
        out = rebuild(self.stream, overlay)
        blocks = walk(out.stream, KERNEL_BYTES)
        (off, _, count, info, size), = [b for b in blocks if b[1] == COMPRESSED]
        app = inflate(out.stream[off + 12 : off + 12 + size], count)
        original = inflate(self.stream[off + 12 : off + 12 + walk(self.stream, KERNEL_BYTES)[-2][4]],
                           walk(self.stream, KERNEL_BYTES)[-2][2])
        self.assertEqual(app, original + overlay)
        # the pieces rebuild the new stream from the image and the new data
        joined = b"".join((self.stream if src == "image" else out.stream)[o : o + n]
                          for src, o, n in out.segments())
        self.assertEqual(joined, out.stream)
        new_bytes = sum(n for src, _, n in out.segments() if src == "new")
        self.assertLess(new_bytes, 16 * 1024)

    def test_insert_outer(self) -> None:
        out = insert_outer(self.stream, overlay())
        blocks = walk(out.stream, KERNEL_BYTES)
        tags = [b[1] for b in walk(overlay(), 0)]
        self.assertEqual([b[1] for b in blocks[-len(tags) - 2:]], [COMPRESSED, *tags, 0])
        joined = b"".join((self.stream if src == "image" else out.stream)[o : o + n]
                          for src, o, n in out.segments())
        self.assertEqual(joined, out.stream)

    def test_swap_words(self) -> None:
        self.assertEqual(swap_words(struct.pack("<I", 0x11223344)), struct.pack(">I", 0x11223344))


class VoiceBendEncodingTests(unittest.TestCase):
    """Encodings checked once against Ghidra's disassembly (2026-11-15)."""

    def test_jump_encoding(self) -> None:
        self.assertEqual(jump(0x12755C, 0x12A8A0), [0x073E, 0x0000, 0x3344])
        self.assertEqual(jump(0x129B4D, 0x1290D5), [0x073E, 0x00FF, 0xF588])  # stock sw:129b4d
        self.assertEqual(jump(0x12517B, 0x125184, cond=0x00), [0x0700, 0x0000, 0x0009])

    def test_code(self) -> None:
        t = trampoline()
        self.assertEqual(t[-3:], jump(TRAMPOLINE + 0x14, RESUME))
        self.assertEqual(handler()[-3:], [0x073E, 0x00FF, 0xE7AD])  # JUMP sw:129077
        self.assertEqual(walk(overlay(), 0)[-1][3], 0xBBB00)
        # 0x1f6 / per-voice Y overlay, checked against Ghidra (PatchCheck2.java, 2026-11-16)
        self.assertEqual(jump(RANGE_JUMP, DEFAULT, GE), [0x0722, 0x0000, 0x0021])  # stock sw:128dd8
        self.assertEqual(jump(RANGE_JUMP, DISPATCH, GE), [0x0722, 0x0000, 0x1B08])
        # With 0x1f6 sub 2 and the per-voice Breath source, checked against
        # Ghidra (PatchCheck3.java, 2026-11-16)
        self.assertEqual(dispatch()[-3:], jump(0x12A92C, EPILOGUE))
        self.assertLessEqual(DISPATCH + len(dispatch()), Y_CODE)
        self.assertEqual(y_code()[-3:], [0x073E, 0x00FF, 0xB7D9])  # JUMP sw:12618e
        self.assertLessEqual(Y_CODE + len(y_code()), SOURCE_CODE)
        self.assertEqual(jump(SOURCE_BREATH, SOURCE_CODE), [0x073E, 0x0000, 0x3740])
        self.assertEqual(source_code(), [0x043F, 0xAA3F, 0x1002, 0x000B, 0xBB0B, 0x1001, 0x000B, 0xBB0C,
                                         0x073E, 0x00FF, 0xC8A6])  # ..., JUMP sw:1272ae
        self.assertLess(SOURCE_CODE + len(source_code()), 0x12C000)


@unittest.skipUnless(IMAGE.exists() and gnu_available(), "canonical image or GNU toolchain missing")
class DspSenderHookTests(unittest.TestCase):
    def test_linked_table_reproduces_stream(self) -> None:
        image = IMAGE.read_bytes()
        out = rebuild(extract(image), overlay())
        words = len(out.stream) // 4
        with tempfile.TemporaryDirectory() as temp:
            blob, symbols, _ = build_patch(gnu_toolchain(), False, Path(temp), dsp_header=c_header(out))
        patched = apply_patch(image, blob, symbols, dsp_words=words)
        self.assertEqual(dsp_stream_from_image(patched, symbols, words), out.stream)
        word = lambda a: struct.unpack_from("<I", patched, a - BASE)[0]  # noqa: E731
        self.assertEqual(word(DSP_SENDER_LOAD) >> 26, 3)  # jal
        self.assertEqual([word(a) for a in DSP_SENDER_LAST], [0x34110000 | (words - 1)] * 2)

    def test_xyz_source_names(self) -> None:
        image = IMAGE.read_bytes()
        with tempfile.TemporaryDirectory() as temp:
            blob, symbols, _ = build_patch(gnu_toolchain(), False, Path(temp), xyz_names=True)
        patched = apply_patch(image, blob, symbols, xyz_names=True)
        names = []
        for entry in XYZ_SOURCE_NAMES:
            pointer = struct.unpack_from("<I", patched, entry - BASE)[0]
            names.append(patched[pointer - BASE :].split(b"\0")[0])
        self.assertEqual(names, [b"X: Pitchbend", b"Z: Pressure", b"Y: Timbre"])


if __name__ == "__main__":
    unittest.main()
