#!/usr/bin/env python3
# Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md.
"""Play MPE test scenarios to the Prophet 12 over MIDI (listen to the result).

  python3 tools/mpe_scenario.py stale-note-off [--port mio] [--channel 2]
  python3 tools/mpe_scenario.py repeat-note [--channel 1] [--count 12] [--note 60]
  python3 tools/mpe_scenario.py manager-notes [--channel 2]
  python3 tools/mpe_scenario.py voice-y | voice-pressure | keyboard-y | stack

voice-y, voice-pressure, keyboard-y, stack: the P12-test-09 checklist
(research/sharc_voice_pressure_y.md), for `MPE Voices` = `12 Voice`. Test
program: tools/p12_test_program.py (--stack for the stack scenario).
- voice-y (items 2 and 5): 12 held notes, so every DSP holds two; Y on one
  note at a time. MPE Y > Timbre = Matrix+Cut, a program with a low cutoff. Expected:
  only the swept note opens. In Single, its DSP partner opens with it.
- voice-pressure (item 3): 6 pitches, each on two channels (12 notes);
  pressure on one channel at a time. Route Aftertouch to oscillator pitch
  (small amount) in the program: one voice detunes, heard as beating.
- keyboard-y (item 4): member notes hold Y = 127, then you play a key on the
  P12: it must stay dark. Press Enter to finish.
- stack (item 6): a Stack program; 6 notes, bend and Y on each in turn (both
  layers move), then a 7th note steals the oldest (60) in both layers.

stale-note-off (TODO.md, "MPE Bugs Found 2026-11-15"): a second Note On on a
member channel that still holds a note, then the stale Note Off of the first
note. Expected with the fix (P12-test-05): note 64 keeps sounding after the
stale Note Off, bends up 2 semitones and back, then stops at its own Note Off.
Without the fix it would stick and ignore the bend.

repeat-note: the same note, one at a time, numbered, so that a voice that
sounds different can be counted in the allocator's rotation (A/B between
images). Channel 1 is the MPE manager (stock allocation path).

manager-notes (P12-test-07): notes on the manager channel 1 next to a member
note on --channel (default 2). Each printed line says what should be heard.
"""

from __future__ import annotations

import argparse
import time

import mido

BEND_RANGE = 48  # MPE bend range of the test images


def bend(semitones: float) -> int:
    return round(8192 * semitones / BEND_RANGE)  # mido pitchwheel: -8192..8191


def stale_note_off(out: mido.ports.BaseOutput, ch: int) -> None:
    steps = [
        (f"ch{ch + 1} Note On 60", mido.Message("note_on", channel=ch, note=60, velocity=90), 1.5),
        (f"ch{ch + 1} Note On 64 (same channel): 60 stops, 64 sounds",
         mido.Message("note_on", channel=ch, note=64, velocity=90), 1.5),
        ("stale Note Off 60: 64 must keep sounding", mido.Message("note_off", channel=ch, note=60), 1.5),
        ("bend +2 semitones: 64 must rise", mido.Message("pitchwheel", channel=ch, pitch=bend(2)), 1.5),
        ("bend back to centre", mido.Message("pitchwheel", channel=ch, pitch=0), 1.0),
        ("Note Off 64: silence (no stuck note)", mido.Message("note_off", channel=ch, note=64), 2.0),
    ]
    play(out, steps)


def play(out: mido.ports.BaseOutput, steps: list) -> None:
    for text, msg, wait in steps:
        print(f"{time.strftime('%H:%M:%S')}  {text}")
        out.send(msg)
        time.sleep(wait)


def manager_notes(out: mido.ports.BaseOutput, ch: int) -> None:
    m, c = 0, ch
    on = lambda chan, n: mido.Message("note_on", channel=chan, note=n, velocity=90)
    off = lambda chan, n: mido.Message("note_off", channel=chan, note=n)
    pb = lambda chan, st: mido.Message("pitchwheel", channel=chan, pitch=bend(st))
    play(out, [
        (f"ch{c + 1} (member) Note On 60", on(c, 60), 1.5),
        ("ch1 (manager) Note On 64: both sound", on(m, 64), 1.5),
        (f"ch{c + 1} bend +2: only 60 rises", pb(c, 2), 1.5),
        (f"ch{c + 1} bend centre", pb(c, 0), 1.0),
        ("ch1 bend +2: both rise", pb(m, 2), 1.5),
        ("ch1 bend centre", pb(m, 0), 1.0),
        ("ch1 Note Off 64: 60 keeps sounding", off(m, 64), 1.5),
        ("ch1 Note On 60 (same number as the member note): two voice LEDs", on(m, 60), 1.5),
        (f"ch1 Note Off 60: ch{c + 1} 60 keeps sounding", off(m, 60), 1.5),
        (f"ch{c + 1} Note Off 60: silence", off(c, 60), 1.5),
        ("reverse: ch1 Note On 60", on(m, 60), 1.0),
        (f"ch{c + 1} Note On 60: two voice LEDs", on(c, 60), 1.5),
        (f"ch{c + 1} Note Off 60: ch1 60 keeps sounding", off(c, 60), 1.5),
        ("ch1 Note Off 60: silence (no stuck note)", off(m, 60), 2.0),
    ])


def say(text: str) -> None:
    print(f"{time.strftime('%H:%M:%S')}  {text}")


def ramp(out: mido.ports.BaseOutput, make, a: int, b: int, secs: float, steps: int = 32) -> None:
    for i in range(steps + 1):
        out.send(make(round(a + (b - a) * i / steps)))
        time.sleep(secs / steps)


def hold(out: mido.ports.BaseOutput, notes: list) -> None:
    """Note On for each (channel, note), with Y and pressure at 0 first."""
    for ch, note in notes:
        out.send(mido.Message("control_change", channel=ch, control=74, value=0))
        out.send(mido.Message("aftertouch", channel=ch, value=0))
        out.send(mido.Message("note_on", channel=ch, note=note, velocity=90))
        time.sleep(0.25)


def release(out: mido.ports.BaseOutput, notes: list) -> None:
    for ch, note in notes:
        out.send(mido.Message("note_off", channel=ch, note=note))


CHORD = [36, 43, 48, 52, 55, 60, 64, 67, 72, 76, 79, 84]


def voice_y(out: mido.ports.BaseOutput) -> None:
    notes = [(i + 1, n) for i, n in enumerate(CHORD)]  # channels 2-13
    say("12 notes, dark (Y = 0)")
    hold(out, notes)
    time.sleep(1.0)
    for ch, n in notes:
        say(f"ch{ch + 1} note {n}: Y up and down, only this note opens")
        y = lambda v, ch=ch: mido.Message("control_change", channel=ch, control=74, value=v)
        ramp(out, y, 0, 127, 0.8)
        ramp(out, y, 127, 0, 0.8)
        time.sleep(0.3)
    say("release: silence")
    release(out, notes)
    time.sleep(1.5)


def voice_pressure(out: mido.ports.BaseOutput) -> None:
    pitches = [48, 55, 60, 64, 67, 72]
    notes = [(i + 1, n) for i, n in enumerate(pitches)] + [(i + 7, n) for i, n in enumerate(pitches)]
    say("6 pitches on two channels each (12 notes), no pressure: no beating")
    hold(out, notes)
    time.sleep(1.5)
    for ch, n in notes:
        say(f"ch{ch + 1} note {n}: pressure up and down, one voice detunes (beating)")
        z = lambda v, ch=ch: mido.Message("aftertouch", channel=ch, value=v)
        ramp(out, z, 0, 127, 0.8)
        ramp(out, z, 127, 0, 0.8)
        time.sleep(0.3)
    say("release: silence")
    release(out, notes)
    time.sleep(1.5)


def keyboard_y(out: mido.ports.BaseOutput) -> None:
    notes = [(1, 48), (2, 55), (3, 60), (4, 64), (5, 67)]
    say("5 member notes, Y = 127 (bright)")
    hold(out, notes)
    for ch, _ in notes:
        out.send(mido.Message("control_change", channel=ch, control=74, value=127))
    input("Play and hold keys on the P12 keyboard: they must stay dark. Enter to finish. ")
    release(out, notes)
    time.sleep(1.0)


def stack(out: mido.ports.BaseOutput) -> None:
    notes = [(i + 1, n) for i, n in enumerate([60, 64, 67, 71, 74, 77])]  # channels 2-7
    say("6 Stack notes (12 voices)")
    hold(out, notes)
    time.sleep(1.0)
    for ch, n in notes:
        say(f"ch{ch + 1} note {n}: bend +2 and back, then Y; both layers of this note only")
        pb = lambda v, ch=ch: mido.Message("pitchwheel", channel=ch, pitch=v)
        ramp(out, pb, 0, bend(2), 0.5)
        ramp(out, pb, bend(2), 0, 0.5)
        y = lambda v, ch=ch: mido.Message("control_change", channel=ch, control=74, value=v)
        ramp(out, y, 0, 127, 0.6)
        ramp(out, y, 127, 0, 0.6)
        time.sleep(0.3)
    say("ch8 note 84: 7th note, 60 stops in both layers")
    hold(out, [(7, 84)])
    time.sleep(2.0)
    say("release: silence")
    release(out, notes[1:] + [(7, 84), notes[0]])
    time.sleep(1.5)


def repeat_note(out: mido.ports.BaseOutput, ch: int, note: int, count: int) -> None:
    for i in range(1, count + 1):
        print(f"{time.strftime('%H:%M:%S')}  note {i}")
        out.send(mido.Message("note_on", channel=ch, note=note, velocity=90))
        time.sleep(0.8)
        out.send(mido.Message("note_off", channel=ch, note=note))
        time.sleep(0.7)


TEST09 = {"voice-y": voice_y, "voice-pressure": voice_pressure, "keyboard-y": keyboard_y, "stack": stack}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("scenario", choices=["stale-note-off", "repeat-note", "manager-notes", *TEST09])
    ap.add_argument("--note", type=int, default=60)
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--port", default="mio")
    ap.add_argument("--channel", type=int, default=None, help="1-16 (default: 2, or 1 for repeat-note)")
    ap.add_argument("--lead-in", type=float, default=3.0, help="seconds before the first note")
    args = ap.parse_args()
    with mido.open_output(args.port) as out:
        time.sleep(args.lead_in)
        try:
            if args.scenario == "stale-note-off":
                stale_note_off(out, (args.channel or 2) - 1)
            elif args.scenario in TEST09:
                TEST09[args.scenario](out)
            elif args.scenario == "manager-notes":
                manager_notes(out, (args.channel or 2) - 1)
            else:
                repeat_note(out, (args.channel or 1) - 1, args.note, args.count)
        finally:
            for ch in range(16):  # safety: nothing left sounding
                out.send(mido.Message("pitchwheel", channel=ch, pitch=0))
                out.send(mido.Message("aftertouch", channel=ch, value=0))
                out.send(mido.Message("control_change", channel=ch, control=74, value=0))
                out.send(mido.Message("control_change", channel=ch, control=123, value=0))


if __name__ == "__main__":
    main()
