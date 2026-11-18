#!/usr/bin/env python3
# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Write MPE demo and test MIDI files for the Prophet 12 MPE firmware.

Standard MIDI files (format 0), no dependencies. MPE Lower Zone: manager
channel 1, member channels 2-7 (six notes, one per DSP in Single mode).
Per-note controls: Pitch Bend (range +-48 semitones, the firmware's Global
MPE Bend Range default), Channel Pressure (Z), CC74 (Y). Each note's
controllers are sent before its Note On, as MPE requires.

Files (research/mpe_demo.md):

- strum.mid: a chord strummed across four octaves, per-note pressure and
  CC74, slides to a second chord, vibrato, a final dive;
- swarm.mid: an original cluster that drifts, spreads, and glides into a
  wide chord (inspired by the classic converging glissando, not a copy of
  any trademarked sound);
- risset_up.mid, risset_down.mid: an endless Shepard-Risset glissando,
  six voices an octave apart, each gliding six octaves on its own bend;
- test.mid: bend calibration against reference notes, per-voice
  independence, a seventh note (voice stealing), channel reuse after a bend.

The firmware ignores the MPE Configuration Message (RPN 6). It is written
only with --mcm (for other MPE synths), because the stock firmware's
handling of RPN data entry on channel 1 has not been checked.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import random
import struct

PPQ = 480
TEMPO_US = 500_000  # 120 BPM: one tick = 500000 / 480 us
BEND_RANGE = 48.0
MANAGER = 0  # MIDI channel 1 (0-based)
MEMBERS = list(range(1, 7))  # MIDI channels 2-7
STEP = 0.02  # bend at 50 Hz, pressure and CC74 at 25 Hz per note: DIN load below about 55 %


def ticks(seconds: float) -> int:
    return max(0, round(seconds * 1_000_000 * PPQ / TEMPO_US))


def bend_value(semitones: float) -> int:
    return max(0, min(16383, round(8192 + semitones / BEND_RANGE * 8192)))


class Song:
    def __init__(self, name: str) -> None:
        self.events: list[tuple[int, int, bytes]] = []  # (tick, order, data)
        self.order = 0
        self.meta(0.0, 0x03, name.encode())
        self.add(0.0, b"\xff\x51\x03" + TEMPO_US.to_bytes(3, "big"))

    def add(self, t: float, data: bytes) -> None:
        self.events.append((ticks(t), self.order, data))
        self.order += 1

    def meta(self, t: float, kind: int, text: bytes) -> None:
        self.add(t, bytes([0xFF, kind]) + vlq(len(text)) + text)

    def marker(self, t: float, text: str) -> None:
        self.meta(t, 0x06, text.encode())

    def bend(self, t: float, ch: int, semitones: float) -> None:
        v = bend_value(semitones)
        self.add(t, bytes([0xE0 | ch, v & 0x7F, v >> 7]))

    def pressure(self, t: float, ch: int, value: float) -> None:
        self.add(t, bytes([0xD0 | ch, max(0, min(127, round(value)))]))

    def cc(self, t: float, ch: int, number: int, value: float) -> None:
        self.add(t, bytes([0xB0 | ch, number, max(0, min(127, round(value)))]))

    def note(
        self,
        ch: int,
        key: int,
        start: float,
        end: float,
        velocity: int = 90,
        bend=None,
        z=None,
        y=None,
    ) -> None:
        """One MPE note; bend/z/y are functions of the time since start."""
        f_bend = bend or (lambda _u: 0.0)
        f_z = z or (lambda _u: 0.0)
        f_y = y or (lambda _u: 64.0)
        last = [None, None, None]

        def controls(t: float, force: bool = False, slow: bool = True) -> None:
            # Bend every step; pressure and CC74 (slower gestures) every
            # second step; only values that changed are sent.
            u = t - start
            b = bend_value(f_bend(u))
            if force or b != last[0]:
                self.bend(t, ch, f_bend(u))
                last[0] = b
            if not (force or slow):
                return
            z_value, y_value = round(f_z(u)), round(f_y(u))
            if force or z_value != last[1]:
                self.pressure(t, ch, f_z(u))
                last[1] = z_value
            if force or y_value != last[2]:
                self.cc(t, ch, 74, f_y(u))
                last[2] = y_value

        controls(start, force=True)  # before the Note On
        self.add(start, bytes([0x90 | ch, key, velocity]))
        t, n = start + STEP, 1
        while t < end:
            controls(t, slow=n % 2 == 0)
            t, n = t + STEP, n + 1
        self.add(end, bytes([0x80 | ch, key, 64]))

    def mcm(self, t: float, members: int = 15) -> None:
        for number, value in ((101, 0), (100, 6), (6, members)):
            self.cc(t, MANAGER, number, value)

    def write(self, path: Path) -> None:
        """Format 0: one track, every channel (for MPE-aware players)."""
        path.write_bytes(smf(0, [sorted(self.events)]))

    def write_split(self, path: Path) -> None:
        """Format 1: a tempo/marker track, then one track per MIDI channel,
        named "Ch N", for DAWs that drop channels on import (route each
        track to its channel)."""
        ordered = sorted(self.events)
        meta = [e for e in ordered if e[2][0] == 0xFF]
        tracks = [meta]
        for ch in sorted({e[2][0] & 0x0F for e in ordered if e[2][0] < 0xF0}):
            name = f"Ch {ch + 1}".encode()
            title = (0, -1, b"\xff\x03" + vlq(len(name)) + name)
            tracks.append([title] + [e for e in ordered if e[2][0] < 0xF0 and e[2][0] & 0x0F == ch])
        path.write_bytes(smf(1, tracks))


def smf(fmt: int, tracks: list) -> bytes:
    out = b"MThd" + struct.pack(">IHHH", 6, fmt, len(tracks), PPQ)
    last = max(e[0] for t in tracks for e in t)
    for events in tracks:
        track, now = bytearray(), 0
        for tick, _order, data in events:
            track += vlq(tick - now) + data
            now = tick
        track += vlq(last - now + ticks(1.0)) + b"\xff\x2f\x00"
        out += b"MTrk" + struct.pack(">I", len(track)) + bytes(track)
    return out


def vlq(n: int) -> bytes:
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append(0x80 | (n & 0x7F))
        n >>= 7
    return bytes(reversed(out))


def ease(x: float) -> float:
    """Smooth 0..1 step (cosine)."""
    x = max(0.0, min(1.0, x))
    return 0.5 - 0.5 * math.cos(math.pi * x)


def ramp(u: float, t0: float, t1: float, a: float, b: float) -> float:
    return a + (b - a) * ease((u - t0) / (t1 - t0)) if t1 > t0 else b


# ---- strum.mid ----
def strum() -> Song:
    s = Song("MPE strum")
    chord_a = [36, 43, 52, 59, 62, 67]  # C2 G2 E3 B3 D4 G4 (Cmaj9, open)
    chord_b = [33, 45, 52, 57, 64, 72]  # A1 A2 E3 A3 E4 C5 (Am add9 colour)
    end = 17.0
    s.marker(0.5, "Strum: per-note pressure and CC74")
    s.marker(6.0, "Per-note slides to a second chord")
    s.marker(9.5, "Vibrato on the top voices")
    s.marker(12.5, "Dive: each note at its own speed")
    for i, (ch, a, b) in enumerate(zip(MEMBERS, chord_a, chord_b)):
        start = 0.5 + 0.075 * i
        rate = 0.18 + 0.07 * i  # each voice has its own filter motion
        phase = i * 1.1
        slide_t0 = 6.0 + 0.25 * (5 - i)  # top voices move first
        dive_rate = 3.0 + 1.2 * i
        dive_depth = -(12 + 4 * (i % 3))

        def bend(u, a=a, b=b, t0=slide_t0 - start, vib=i >= 3, d0=12.5 - start, rate=dive_rate, depth=dive_depth):
            semis = ramp(u, t0, t0 + 1.4, 0.0, b - a)
            if vib:
                depth_v = ramp(u, 9.5 - 0.5 - start + 0.5, 11.0 - start, 0.0, 0.35)
                semis += depth_v * math.sin(2 * math.pi * 5.3 * u)
            semis += ramp(u, d0, d0 + rate, 0.0, depth)
            return semis

        def z(u, i=i, d0=12.5 - start):
            swell = ramp(u, 0.0, 2.0, 25, 95) + 12 * math.sin(2 * math.pi * 0.3 * u + i)
            return swell * (1.0 - ease((u - d0) / 3.5))

        def y(u, rate=rate, phase=phase):
            return 64 + 40 * math.sin(2 * math.pi * rate * u + phase)

        s.note(ch, a, start, end - 0.1 * i, 70 + 8 * i, bend, z, y)
    return s


# ---- swarm.mid ----
def swarm(seed: int = 12) -> Song:
    rng = random.Random(seed)
    s = Song("MPE swarm")
    target = [39, 46, 51, 58, 65, 70]  # Eb2 Bb2 Eb3 Bb3 F4 Bb4
    start, arrive, end = 0.3, 11.0, 17.0
    s.marker(start, "Cluster: six voices drifting")
    s.marker(5.0, "Spreading")
    s.marker(arrive, "Arrival: wide chord")
    for i, (ch, goal) in enumerate(zip(MEMBERS, target)):
        key = 55 + rng.randrange(4)  # G3..Bb3 cluster
        # Smoothed random walk for the drift, precomputed every STEP.
        drift, value, velocity = [], 0.0, 0.0
        for _ in range(int((end - start) / STEP) + 2):
            velocity = 0.97 * velocity + rng.uniform(-0.02, 0.02)
            value = max(-2.5, min(2.5, value + velocity))
            drift.append(value)
        glide_t0 = 5.0 + rng.uniform(0.0, 1.5) - start
        glide_t1 = arrive - start + rng.uniform(-0.4, 0.4)
        overshoot = rng.choice((-1, 1)) * rng.uniform(0.2, 0.5)

        def bend(u, key=key, goal=goal, d=drift, g0=glide_t0, g1=glide_t1, o=overshoot):
            k = min(len(d) - 1, int(u / STEP))
            wander = d[k] * (1.0 - ease((u - g0) / (g1 - g0)))
            glide = (goal - key) * ease(((u - g0) / (g1 - g0)) ** 1.6) if u > g0 else 0.0
            settle = o * math.exp(-(u - g1) * 2.5) * math.sin(2 * math.pi * 1.2 * (u - g1)) if u > g1 else 0.0
            return wander + glide + settle

        def z(u):
            return ramp(u, 0.0, arrive - start, 15, 110) * (1.0 - ease((u - (end - 3.5 - start)) / 3.0))

        def y(u, i=i):
            return ramp(u, 0.0, arrive - start + 1.0, 20, 105) + 6 * math.sin(2 * math.pi * (0.4 + 0.1 * i) * u)

        s.note(ch, key, start + 0.05 * i, end, 60 + 5 * i, bend, z, y)
    return s


# ---- risset_up.mid, risset_down.mid ----
def risset(direction: int = 1, octave_seconds: float = 3.0, duration: float = 45.0) -> Song:
    """Shepard-Risset glissando: six voices an octave apart glide forever.

    Each voice covers six octaves (key 60, bend -36 .. +36, inside the
    +-48 range) in 6 * octave_seconds, with pressure (Z) shaped as a bell
    over its position: silent at both ends, loudest in the middle. At the
    silent end the voice restarts at the other end on the same channel
    (bend sent before the new Note On). Needs a program whose level comes
    from pressure: see research/mpe_demo.md.
    """
    s = Song("MPE Risset glissando " + ("up" if direction > 0 else "down"))
    span, cycle = 72.0, 6 * octave_seconds
    s.marker(0.0, "Risset glissando: fade in")
    fade_in, fade_out = 4.0, 5.0
    for i, ch in enumerate(MEMBERS):
        phase = i / 6.0
        t = 0.0
        while t < duration:
            p0 = (phase + t / cycle) % 1.0  # position at the segment start
            seg_end = min(duration, t + (1.0 - p0) * cycle)

            def position(u, p0=p0):
                return p0 + u / cycle

            def bend(u):
                p = position(u)
                return direction * (p - 0.5) * span

            def z(u, t0=t):
                bell = math.sin(math.pi * min(1.0, position(u))) ** 2
                master = ease((t0 + u) / fade_in) * (1.0 - ease((t0 + u - (duration - fade_out)) / fade_out))
                return 120 * bell * master

            def y(u):
                return 50 + 50 * math.sin(math.pi * min(1.0, position(u)))

            s.note(ch, 60, t, seg_end, 64, bend, z, y)
            t = seg_end
    return s


# ---- test.mid ----
def test() -> Song:
    s = Song("MPE tests")
    t = 0.5
    # 1. Bend calibration: a bent note against a reference note (no beating).
    for shift in (12, -12, 24, -24, 48, -48):
        base = 60 - shift
        s.marker(t, f"Bend {shift:+d}: note {base} bent to 60, reference 60 joins")
        s.note(MEMBERS[0], base, t, t + 3.0, 90, bend=lambda u, sh=shift: ramp(u, 0.2, 1.0, 0.0, sh))
        s.note(MEMBERS[1], 60, t + 1.5, t + 3.0, 90)
        t += 3.5
    # 2. Independence: six held notes, one voice moves at a time.
    chord = [48, 55, 60, 64, 67, 72]
    hold0, per = t, 2.5
    s.marker(t, "Independence: one voice moves at a time")
    total = per * 6 + 1.0
    for i, (ch, key) in enumerate(zip(MEMBERS, chord)):
        a = 1.0 + per * i

        def bend(u, a=a):
            return 1.0 * math.sin(2 * math.pi * 3.0 * (u - a)) * (a <= u < a + per)

        def z(u, a=a):
            return 40 + 60 * math.sin(math.pi * (u - a) / per) if a <= u < a + per else 40

        def y(u, a=a):
            return 64 + 50 * math.sin(2 * math.pi * (u - a) / per) if a <= u < a + per else 64

        s.note(ch, key, hold0, hold0 + total, 80, bend, z, y)
    t = hold0 + total + 0.5
    # 3. Seventh note: the oldest note is stolen.
    s.marker(t, "Voice stealing: seven notes, the first one should stop")
    for i in range(7):
        ch = MEMBERS[i % 6] if i < 6 else 7  # channel 8 for the seventh
        s.note(ch, 52 + 3 * i, t + 0.4 * i, t + 5.0, 85)
    t += 5.5
    # 4. Channel reuse: a note left bent, then a new note on the same channel.
    s.marker(t, "Channel reuse: second note must be in tune (bend reset before Note On)")
    s.note(MEMBERS[0], 60, t, t + 1.5, 90, bend=lambda u: ramp(u, 0.2, 0.8, 0.0, 7.0))
    s.note(MEMBERS[0], 64, t + 2.0, t + 3.5, 90)
    s.note(MEMBERS[1], 64, t + 2.75, t + 3.5, 90)  # reference
    return s


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output_dir", type=Path, nargs="?", default=Path("demos"))
    parser.add_argument("--mcm", action="store_true", help="write the Lower Zone MCM (RPN 6) for other MPE synths")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    songs = (
        ("strum", strum()),
        ("swarm", swarm()),
        ("risset_up", risset(1)),
        ("risset_down", risset(-1)),
        ("test", test()),
    )
    for name, song in songs:
        if args.mcm:
            song.mcm(0.0)
        path = args.output_dir / f"{name}.mid"
        song.write(path)
        song.write_split(args.output_dir / f"{name}_tracks.mid")
        print(f"{path}: {len(song.events)} events (+ {name}_tracks.mid, one track per channel)")


if __name__ == "__main__":
    main()
