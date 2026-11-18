/* Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md. */
/*
 * Prophet 12 Main OS 1.4.2.3 - experimental MPE patch, test image 3.
 *
 * Test image 2 added, over image 1: per-member CC1/CC2/CC4/CC11 to the
 * matching DSP mod sources, Y (CC74) doubled to Breath, Z (pressure)
 * doubled to Expression, and an optional pitch-bend range override.
 * Test image 3 turns those options into runtime settings edited in the
 * Global menu (mpe_menu.c) and saved in the Global SPI flash page, and adds
 * the fallback for programs that cannot use MPE.
 * Test image 4 adds layered MPE for Stack and Split programs: each layer's
 * notes use the layer's own DSP group (A: DSPs 0-2, B: DSPs 3-5), so a
 * Stack note owns one DSP in each group (research/mpe_layered.md).
 * Test image 5 adds per-note expression for arpeggiated layers in Normal
 * and Split programs: each arp step sounds on a DSP owned by the member
 * (finger) that holds the note, or by a frozen snapshot of it once the
 * finger has lifted under Hold (research/arpeggiator.md).
 * Test image 8 adds MPE Voices = 12 Voice (needs --dsp-voice-bend and a
 * fixed MPE bend range): each note owns one voice instead of one DSP, with
 * its bend through 0x1f1 (research/mpe_12_voice.md). Test image 9 sends
 * its pressure and Y through the per-voice DSP command 0x1f6
 * (research/sharc_voice_pressure_y.md).
 *
 * Design: research/mpe_design.md (revision 2), with the test-image
 * simplifications listed in research/mpe_test_image.md:
 *   - MPE Lower Zone is enabled at boot: manager channel 1, member channels
 *     2-16. Since P12-pt-03 the MPE Configuration Message (RPN 6 on the
 *     manager channel) sets the member count and turns the zone on or off
 *     (research/mpe_manager_and_mcm.md).
 *   - Six notes, one per DSP. Single mode by default, Pair mode at build time.
 *   - It is active only in Normal mode (not Split, Stack, or Multi), with the
 *     sounding layer neither in Unison nor in Arpeggiator mode. Otherwise every
 *     event takes the stock path unchanged.
 *
 * Linked at 0x9d073460 (free flash after the linked application image).
 * The state lives at a fixed RAM address above the 1 KiB heap
 * (research/mpe_test_image.md). Built with -G0, so $gp is never used.
 */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

/* Default settings, used until the SPI flash holds a valid MPE record. */
#ifndef MPE_DEFAULT_ENABLED
#define MPE_DEFAULT_ENABLED 1
#endif
#ifndef MPE_PAIR_MODE
#define MPE_PAIR_MODE 0
#endif
#ifndef MPE_WHEEL_MODE
#define MPE_WHEEL_MODE 0 /* 0 = bends every MPE note, 1 = ignored */
#endif
#ifndef MPE_FALLBACK
#define MPE_FALLBACK 0 /* other programs: 0 = play member notes, 1 = silent */
#endif
#ifndef MPE_LAYERED
#define MPE_LAYERED 1 /* Stack/Split: 1 = layered MPE, 0 = MPE Fallback rule */
#endif
#ifndef MPE_BEND_RANGE
#define MPE_BEND_RANGE 48 /* semitones sent to the DSPs after each program
                             transfer while MPE is active; 0 = no override */
#endif
#ifndef MPE_DSP_VOICE_BEND
#define MPE_DSP_VOICE_BEND 0 /* 1 = the DSP stream has the per-voice bend command
                                0x1f1 (--dsp-voice-bend); member bend uses it */
#endif
#ifndef MPE_Y_TO_BREATH
#define MPE_Y_TO_BREATH 1 /* member CC74 also drives the CC#2 Breath source */
#endif
#ifndef MPE_ARP_MODE
#define MPE_ARP_MODE 0 /* arpeggiated layers: 0 = per-note MPE, 1 = fallback */
#endif
#ifndef MPE_Y_BRIGHTNESS
#define MPE_Y_BRIGHTNESS 1 /* member CC74 keeps the stock brightness word */
#endif
/* MPE Y > Timbre: 0 Off, 1 Matrix (the Y: Timbre source), 2 Matrix+Cut
 * (also the stock cutoff term). Y_TO_BREATH Off gives Off. */
#define Y_MATRIX 1u
#define Y_MATRIX_CUT 2u
#define MPE_Y_MODE (MPE_Y_TO_BREATH ? (MPE_Y_BRIGHTNESS ? Y_MATRIX_CUT : Y_MATRIX) : 0u)

#define NONE 0xffu
#define NUM_DSP 6u
#define NUM_CH 16u
#define MEMBER_COUNT 15u /* full Lower Zone: member channels 2-16 */
#define MPE_MAGIC 0x3445504du /* "MPE4" */

#define PITCH_CENTRE 0x2000u
#define CC74_DEFAULT 64u

/* Stock broadcast words, sent to one DSP (hardware Experiment C). */
#define CMD_PITCH 0xc1fc0000u
/* Per-voice bend (DSP overlay, research/sharc_dsp_patch_feasibility.md):
 * selector | 0x01f10000 | (offset + 0x8000), offset in 1/256 semitone. */
#define CMD_VOICE_BEND 0x01f10000u
#define VOICE_BEND_NEUTRAL 0xc1f18000u /* both voices, offset 0 */
#define SEL_VOICE_A 0x80000000u
#define SEL_VOICE_B 0x40000000u
#define SEL_BOTH 0xc0000000u
#define CMD_PRESSURE 0xc1fa0000u
#define CMD_CC74 0xc1fb0000u
/* Per-voice pressure and Y (DSP overlay, research/sharc_voice_pressure_y.md):
 * selector | 0x01f60000 | (sub << 8) | value7, sub 0 = pressure, 1 = Y. Y adds
 * to the stock brightness term (0x1fb), which 12 Voice keeps at 0.
 * sub 2 = Breath per voice (research/sharc_voice_timbre_source.md): it adds
 * to the stock Breath (0x1f9) in mod source 0x14, so in 12 Voice a member's
 * Breath (CC2, and Y with MPE Y > Matrix) is a per-voice source. */
#define CMD_VOICE_CTL 0x01f60000u
#define VCTL_Y 0x100u
#define VCTL_BREATH 0x200u
#define Y_NEUTRAL (SEL_BOTH | CMD_VOICE_CTL | VCTL_Y)
#define BREATH_NEUTRAL (SEL_BOTH | CMD_VOICE_CTL | VCTL_BREATH)
/* Controller mod sources (research/expressive_controls.md). */
#define CMD_MOD_WHEEL 0xc1fd0000u /* CC1 */
#define CMD_BREATH 0xc1f90000u    /* CC2 */
#define CMD_FOOT 0xc1f80000u      /* CC4 */
#define CMD_EXPRESSION 0xc1f70000u /* CC11 */

/* Per-member controller slots beyond pitch, pressure, and CC74. */
#define CTL_MOD_WHEEL 0u
#define CTL_BREATH 1u
#define CTL_FOOT 2u
#define CTL_EXPRESSION 3u
#define NUM_CTL 4u

/* DSP-side program parameters (research/ui_and_parameter_access.md). */
#define P_BEND_UP 0x04au
#define P_BEND_DOWN 0x04bu

/* Internal event status values passed to MidiSubmitEvent. */
#define EV_NOTE_OFF 0x80u
#define EV_NOTE_ON 0x90u
#define EV_POLY_PRESSURE 0xa0u
#define EV_CONTROL 0xb0u
#define EV_CHANNEL_PRESSURE 0xd0u
#define EV_PITCH_BEND 0xe0u
#define EV_NRPN 0xfdu /* FUN_9d02b5c8 (DIN and USB): d1 = NRPN number, d2 = 14-bit value */

/* MPE settings over NRPN: 433 + setting index (menu order), 433-447
 * reserved for them; 448-479 reserved for later tools. The stock table
 * 0x9d071840 maps no layer parameter to 433-479 (nor to 945-991 for Layer B),
 * and the OS 1.4.1 NRPN map lists none there (research/nrpn_map_firmware.md). */
#define NRPN_SETTINGS 433u

/* Parameter indices (research/layer_dsp_parameters.md, voice_allocation.md). */
#define G_MULTI_MODE 0x1eu
#define L_AB_MODE 0x1a9u
#define L_UNISON 0x101u
#define L_ARP 0x128u
#define L_ARP_LOCK 0x12cu /* Lock Notes */
#define AB_STACK 1u

/* Settings, in the order of the menu rows and of the saved record. */
#define SET_ENABLED 0u
#define SET_PAIR 1u
#define SET_BEND 2u
#define SET_Y 3u
#define SET_WHEEL 4u
#define SET_FALLBACK 5u
#define SET_LAYERED 6u
#define SET_ARP 7u
#define NUM_SETTINGS 8u
#define BEND_MAX 96u

#define VOICES_TWELVE 2u /* MPE Voices: 0 Single, 1 Pair, 2 12 Voice */
#define VOICES_DEFAULT (MPE_PAIR_MODE ? 1u : MPE_DSP_VOICE_BEND ? VOICES_TWELVE : 0u)
static const u8 setting_max[NUM_SETTINGS] = {1, MPE_DSP_VOICE_BEND ? VOICES_TWELVE : 1, BEND_MAX, Y_MATRIX_CUT,
                                             1, 1, 1, 1};
static const u8 setting_default[NUM_SETTINGS] = {
    MPE_DEFAULT_ENABLED, VOICES_DEFAULT, MPE_BEND_RANGE, MPE_Y_MODE,
    MPE_WHEEL_MODE, MPE_FALLBACK, MPE_LAYERED, MPE_ARP_MODE,
};

/* Saved record: offset 0x80 of the Global page (SPI flash 0xc9000), whose
 * stock content ends at offset 78 (research/pitch_wheel_and_global_menu.md). */
#define REC_OFFSET 0x80u
#define REC_VERSION 2u /* 1: ten settings, with Y > Bright, Y > Matrix, Z > Express */
#define REC_LENGTH (5u + NUM_SETTINGS + 1u)
#define GLOBALS_PAGE 0xc9000u

struct MpeState {
    u32 magic;
    u8 set[NUM_SETTINGS];
    u8 ctx;          /* member channel during a synchronous legacy call */
    u8 fallback_last; /* member whose expression the fallback forwards */
    u8 cursor[2];    /* next DSP to try, per DSP group */
    /* A unit is a DSP (Single, Pair) or a voice (12 Voice). */
    u8 dsp_owner[12]; /* owner of each unit: member channel, NONE = free */
    u8 member_dsp[NUM_CH][2]; /* owned unit per group (group 1: Stack/Split B) */
    u8 member_note[NUM_CH];
    u8 member_pressure[NUM_CH];
    u8 member_cc74[NUM_CH];
    u16 member_pitch[NUM_CH];
    u16 manager_pitch;
    /* Zone-level pressure and Y from the manager channel; each adds to the
     * member's own value, clamped to 127 (research/mpe_manager_and_mcm.md). */
    u8 manager_pressure;
    u8 manager_cc74;
    u8 member_ctl[NUM_CH][NUM_CTL];
    u16 dirty; /* bit u: unit u got member values since its last stock note */
    /* Zone size: member channels 1..members (0-based), set by the MPE
     * Configuration Message. Volatile: the full zone after boot and whenever
     * MPE Mode is switched by the menu or NRPN 433. */
    u8 members;
    /* RPN selection per transport (0 DIN, 1 USB), as seen by the parser
     * hooks: selected flag, channel, and the two halves [0] LSB, [1] MSB. */
    u8 rpn_sel[2];
    u8 rpn_ch[2];
    u8 rpn[2][2];
};
/* mpe.ld: 0xa0014000-0xa00140ff */
typedef char mpe_state_fits[sizeof(struct MpeState) <= 0x100 ? 1 : -1];

/* Arpeggiator state (research/arpeggiator.md). The stock arp keeps a list of
 * up to 32 notes per layer; the patch mirrors it with the source of each
 * entry. A source is a member channel (1-15), or a virtual owner
 * NUM_CH + s for frozen snapshot s (s = 0 is the neutral expression). */
#define ARP_LEN 32u
#define NUM_FROZEN 40u
#define VIRT_NEUTRAL NUM_CH

struct Frozen {
    u16 pitch;
    u8 pressure;
    u8 cc74;
    u8 ctl[NUM_CTL];
};

struct ArpMpeState {
    u8 tag[2][ARP_LEN];  /* source of each list entry */
    u8 note[2][ARP_LEN]; /* note of each entry, to detect a stale mirror */
    u8 lock_src[2];      /* Lock Notes: source of the transposing key */
    u8 playing_src[2];   /* source of the sounding arp note */
    volatile u8 ext_in[2]; /* external clock ticks seen by the ISR */
    u8 ext_done[2];      /* ticks run by the main loop */
    struct Frozen frozen[NUM_FROZEN];
};

static const u32 ctl_command[NUM_CTL] = {
    CMD_MOD_WHEEL, CMD_BREATH, CMD_FOOT, CMD_EXPRESSION,
};

/* Fixed addresses, defined in mpe.ld. */
extern struct MpeState mpe_state;
extern struct ArpMpeState arp_mpe_state;   /* 0xa0014400 */
extern volatile u16 arp_note_list[2 * ARP_LEN]; /* 0xa0000e38 */
extern volatile u16 arp_count[2];  /* 0xa00007e4 */
extern volatile u16 arp_index[2];  /* 0xa00007e8 */
extern volatile u32 voice_age[12];  /* 0xa0009a90: 0 = free, else age */
extern volatile u32 voice_note[12]; /* 0xa0009ac0 */

/* Stock firmware functions (o32 ABI). */
extern void SendCommandToDsp(u32 target, u32 word);
extern void SendCommandToLayerDsps(u32 layer, u32 word);
extern void BroadcastDspCommand(u32 word);
extern void ActivateVoiceForNote(u32 voice, u32 layer, u32 arg, u32 note);
extern void ReleaseVoiceAndCompactAges(u32 voice, u32 layer, u32 arg, u32 note);
extern u32 GetGlobalParameter(u32 index);
extern u32 GetLayerParameter(u32 layer, u32 index);
extern u32 GetSelectedLayerState(void);
extern void LoadGlobalsFromSpiFlash(void);
extern void SpiFlashProgram256(u32 address);
extern u32 SpiReadByte(u32 address);
extern volatile u8 spi_page_buffer[256];  /* 0xa0007e00 */
extern void TrackAndAllocateNote(u32 note, u32 arg, u32 layer);
extern void TrackAndReleaseNote(u32 arg, u32 layer, u32 note);
extern void ArpTick(u32 layer);
extern void MainLoopHelper(void); /* 0x9d01a074, unidentified leaf helper */
extern u32 MidiReadDataByte(void); /* 0x9d02b3d8 */
/* Stock MIDI parser state (research/mpe_manager_and_mcm.md). */
extern volatile u16 din_cc_number;   /* 0xa0009a86: event record d1, written before the value read */
extern volatile u8 din_channel;      /* 0xa00008e1, gp-0x7e0f */
extern volatile u16 din_nrpn_number; /* 0xa00007aa, gp-0x7f46: 0x3fff = none */
extern volatile u16 usb_nrpn_number; /* 0xa000080e, gp-0x7ee2: used when < 0x430 */
extern volatile u16 usb_rx_index;    /* 0xa00009b2, gp-0x7d3e: packet being parsed */
extern volatile u8 usb_rx_packets[]; /* 0xa00104a0: 4 bytes per USB-MIDI event packet */

/* Relocated entries of the hooked functions (mpe_entry.S). */
extern void orig_midi_submit_event(u32 status, u32 channel, u32 d1, u32 d2, u32 extra);
extern void orig_allocate_voice(u32 layer, u32 arg, u32 note);
extern void orig_release_voices(u32 layer, u32 arg, u32 note);
extern u32 orig_channel_accepted(u32 channel);
extern void orig_send_program_parameters(void);
extern void orig_arp_list_add(u32 layer, u32 note);
extern void orig_arp_list_remove(u32 layer, u32 note);

#define S (mpe_state)
#define A (arp_mpe_state)

static int is_virtual(u32 c) { return c >= NUM_CH && c < NUM_CH + NUM_FROZEN; }

/* Expression of a member channel or of a frozen snapshot. */
static u32 ex_pitch(u32 c) { return c < NUM_CH ? S.member_pitch[c] : A.frozen[c - NUM_CH].pitch; }
/* Owner NONE (a unit with no member: stock-path notes) has no value of its own. */
static u32 ex_pressure(u32 c) { return c < NUM_CH ? S.member_pressure[c] : is_virtual(c) ? A.frozen[c - NUM_CH].pressure : 0; }
static u32 ex_cc74(u32 c) { return c < NUM_CH ? S.member_cc74[c] : is_virtual(c) ? A.frozen[c - NUM_CH].cc74 : 0; }
static u32 ex_ctl(u32 c, u32 k) { return c < NUM_CH ? S.member_ctl[c][k] : is_virtual(c) ? A.frozen[c - NUM_CH].ctl[k] : 0; }

static void arp_reset(void)
{
    u32 i, k, L;
    for (L = 0; L < 2; L++) {
        for (i = 0; i < ARP_LEN; i++) {
            A.tag[L][i] = NONE;
            A.note[L][i] = NONE;
        }
        A.lock_src[L] = NONE;
        A.playing_src[L] = NONE;
        A.ext_in[L] = 0;
        A.ext_done[L] = 0;
    }
    for (i = 0; i < NUM_FROZEN; i++) {
        A.frozen[i].pitch = PITCH_CENTRE;
        A.frozen[i].pressure = 0;
        A.frozen[i].cc74 = CC74_DEFAULT;
        for (k = 0; k < NUM_CTL; k++)
            A.frozen[i].ctl[k] = 0;
    }
}

static void member_clear_expression(u32 c)
{
    u32 k;
    S.member_pitch[c] = PITCH_CENTRE;
    S.member_pressure[c] = 0;
    S.member_cc74[c] = CC74_DEFAULT;
    for (k = 0; k < NUM_CTL; k++)
        S.member_ctl[c][k] = 0;
}

static void mpe_reset(void)
{
    u32 i;
    for (i = 0; i < NUM_SETTINGS; i++)
        S.set[i] = setting_default[i];
    S.ctx = NONE;
    S.cursor[0] = 0;
    S.cursor[1] = 0;
    S.fallback_last = NONE;
    for (i = 0; i < 12; i++)
        S.dsp_owner[i] = NONE;
    for (i = 0; i < NUM_CH; i++) {
        S.member_dsp[i][0] = NONE;
        S.member_dsp[i][1] = NONE;
        S.member_note[i] = NONE;
        member_clear_expression(i);
    }
    S.manager_pitch = PITCH_CENTRE;
    S.manager_pressure = 0;
    S.manager_cc74 = 0;
    S.dirty = 0;
    S.members = MEMBER_COUNT;
    for (i = 0; i < 2; i++) {
        S.rpn_sel[i] = 0;
        S.rpn_ch[i] = NONE;
        S.rpn[i][0] = S.rpn[i][1] = 0x7f;
    }
    arp_reset();
    S.magic = MPE_MAGIC;
}

static void ensure_init(void)
{
    if (S.magic != MPE_MAGIC)
        mpe_reset();
}

static u32 sounding_layer(void)
{
    return (GetSelectedLayerState() & 0xff) == 1 ? 1u : 0u;
}

/* A/B mode (layer A parameter 0x1a9): 0 Normal, 1 Stack, 2 Split
 * (internal order, confirmed by RouteNoteOnByLayer/RouteNoteOffByLayer). */
static u32 ab_mode(void)
{
    return GetLayerParameter(0, L_AB_MODE) & 0xffff;
}

static int arp_on(u32 layer)
{
    return (GetLayerParameter(layer, L_ARP) & 0xffff) != 0;
}

/* An arpeggiated layer uses per-note MPE when the MPE Arp setting allows
 * it, except in Stack, whose arp path is not mapped (layer argument 2). */
static int layer_blocks_mpe(u32 layer)
{
    if ((GetLayerParameter(layer, L_UNISON) & 0xffff) != 0)
        return 1;
    return arp_on(layer) && (S.set[SET_ARP] != 0 || ab_mode() == AB_STACK);
}

static int mpe_active(void)
{
    ensure_init();
    if (!S.set[SET_ENABLED])
        return 0;
    if ((GetGlobalParameter(G_MULTI_MODE) & 0xffff) != 0)
        return 0;
    if (ab_mode() == 0)
        return !layer_blocks_mpe(sounding_layer());
    /* Stack or Split: both layers sound, each on its own DSP group. */
    if (!S.set[SET_LAYERED])
        return 0;
    return !layer_blocks_mpe(0) && !layer_blocks_mpe(1);
}

/* 12 Voice needs the per-voice bend with a fixed MPE range; with Bend Range
 * = Program it falls back to Single. */
static int twelve(void)
{
    return MPE_DSP_VOICE_BEND && S.set[SET_PAIR] == VOICES_TWELVE && S.set[SET_BEND] != 0;
}

static int pair(void) { return S.set[SET_PAIR] == 1; }
static u32 num_units(void) { return twelve() ? 12u : NUM_DSP; }
static u32 unit_dsp(u32 u) { return twelve() ? u / 2 : u; }
static u32 unit_of_voice(u32 v) { return twelve() ? v : v / 2; }
/* Voices MPE manages in an owned unit: voice A only in Single mode. */
static int managed_voice(u32 v) { return twelve() || pair() || !(v & 1); }
static u32 voice_sel(u32 v) { return (v & 1) ? SEL_VOICE_B : SEL_VOICE_A; }

/* DSP group of an allocation: Normal mode uses all six DSPs (group 0);
 * Stack and Split use DSPs 0-2 for layer A and 3-5 for layer B, the same
 * voice ranges [0,6) and [6,12) as the stock allocator. */
static u32 group_of_layer(u32 layer)
{
    return ab_mode() != 0 ? (layer & 1) : 0;
}

static u32 group_first_dsp(u32 g)
{
    return ab_mode() != 0 ? num_units() / 2 * g : 0;
}

static u32 group_size(void)
{
    return ab_mode() != 0 ? num_units() / 2 : num_units();
}

static int dsp_owned_by(u32 d, u32 c)
{
    return d < num_units() && S.dsp_owner[d] == c;
}

/* DSP owned by member c in group g, or NONE. */
static u32 owned_dsp(u32 c, u32 g)
{
    u32 d = S.member_dsp[c][g];
    return dsp_owned_by(d, c) ? d : NONE;
}

static int voice_bend_active(void)
{
    return MPE_DSP_VOICE_BEND && S.set[SET_BEND] != 0 && mpe_active();
}

static void send_pitch(u32 d, u32 c)
{
    int p;
    if (voice_bend_active()) {
        /* Manager bend: per DSP (0x1fc, DSP range = MPE range). Member bend:
         * per voice (0x1f1), scaled here with the same range. Single mode
         * plays voice A of the DSP (even physical voice), Pair mode both. */
        int off = ((int)ex_pitch(c) - (int)PITCH_CENTRE) * (int)S.set[SET_BEND] / 32;
        u32 sel = twelve() ? voice_sel(d) : pair() ? SEL_BOTH : SEL_VOICE_A;
        SendCommandToDsp(unit_dsp(d), CMD_PITCH | S.manager_pitch);
        SendCommandToDsp(unit_dsp(d), sel | CMD_VOICE_BEND | ((u32)(off + 0x8000) & 0xffff));
        return;
    }
    p = (int)ex_pitch(c) + (int)S.manager_pitch - (int)PITCH_CENTRE;
    if (p < 0)
        p = 0;
    if (p > 0x3fff)
        p = 0x3fff;
    SendCommandToDsp(d, CMD_PITCH | (u32)p);
}

static u32 clamp7(u32 v) { return v > 127 ? 127 : v; }

static int y_matrix(void) { return S.set[SET_Y] != 0; }
static int y_bright(void) { return S.set[SET_Y] == Y_MATRIX_CUT; }

/* Manager (zone) pressure and Y add to the owner's own value. The manager Y
 * follows the member Y into the Breath source (MPE Y > Timbre). */
static u32 ctl_value(u32 c, u32 k)
{
    u32 v = ex_ctl(c, k);
    if (k == CTL_BREATH && y_matrix())
        v += S.manager_cc74;
    return clamp7(v);
}

static void send_ctl(u32 d, u32 c, u32 k)
{
    if (k == CTL_BREATH && twelve())
        SendCommandToDsp(unit_dsp(d), voice_sel(d) | CMD_VOICE_CTL | VCTL_BREATH | ctl_value(c, k));
    else
        SendCommandToDsp(unit_dsp(d), ctl_command[k] | ctl_value(c, k));
}

static void send_pressure(u32 d, u32 c)
{
    u32 v = clamp7(ex_pressure(c) + S.manager_pressure);
    if (twelve())
        SendCommandToDsp(unit_dsp(d), voice_sel(d) | CMD_VOICE_CTL | v);
    else
        SendCommandToDsp(d, CMD_PRESSURE | v);
}

/* Callers send Y only with MPE Y > Bright On. */
static void send_cc74(u32 d, u32 c)
{
    u32 v = clamp7(ex_cc74(c) + S.manager_cc74);
    if (twelve())
        SendCommandToDsp(unit_dsp(d), voice_sel(d) | CMD_VOICE_CTL | VCTL_Y | v);
    else
        SendCommandToDsp(d, CMD_CC74 | v);
}

/* The controllers CC1, CC4 and CC11 stay per DSP: in 12 Voice the two
 * voices of a DSP share them, and the last value sent wins. Breath (CC2) is
 * per voice in 12 Voice (send_ctl). */
static void send_expression(u32 d, u32 c)
{
    u32 k;
    send_pitch(d, c);
    send_pressure(d, c);
    if (y_bright())
        send_cc74(d, c);
    for (k = 0; k < NUM_CTL; k++)
        send_ctl(d, c, k);
}

/* Set one controller slot of member c and send it to its DSP(s). */
static void set_ctl(u32 c, u32 k, u32 value)
{
    u32 g, d;
    S.member_ctl[c][k] = (u8)(value & 0x7f);
    for (g = 0; g < 2; g++)
        if ((d = owned_dsp(c, g)) != NONE)
            send_ctl(d, c, k);
}

static void send_member_pressure(u32 c)
{
    u32 g, d;
    for (g = 0; g < 2; g++)
        if ((d = owned_dsp(c, g)) != NONE)
            send_pressure(d, c);
}

static void send_member_cc74(u32 c)
{
    u32 g, d;
    for (g = 0; g < 2; g++)
        if ((d = owned_dsp(c, g)) != NONE)
            send_cc74(d, c);
}

static void send_member_pitch(u32 c)
{
    u32 g, d;
    for (g = 0; g < 2; g++)
        if ((d = owned_dsp(c, g)) != NONE)
            send_pitch(d, c);
}

/* Unowned DSPs get the manager bend alone: they may hold stock-path notes
 * (manager channel, local keyboard). */
static void resend_all_pitch(void)
{
    u32 d;
    for (d = 0; d < num_units(); d++) {
        if (S.dsp_owner[d] != NONE)
            send_pitch(d, S.dsp_owner[d]);
        else
            SendCommandToDsp(unit_dsp(d), CMD_PITCH | S.manager_pitch);
    }
}

/* Send the manager pressure (what & 1) and/or manager Y (what & 2) to every
 * unit: combined with the owner's value, alone on unowned units (stock-path
 * notes: manager channel, local keyboard). Unowned units get the Breath word
 * only while MPE Y > Timbre is not Off, or when forced (bit 2; the setting
 * was just changed), so that a manager CC2 value is not overwritten without
 * need. */
static void resend_manager(u32 what, u32 force)
{
    u32 d, c;
    for (d = 0; d < num_units(); d++) {
        c = S.dsp_owner[d];
        if (what & 1)
            send_pressure(d, c);
        if (what & 2) {
            if (y_bright())
                send_cc74(d, c);
            if (y_matrix() || (force & 2))
                send_ctl(d, c, CTL_BREATH);
        }
    }
}

static u32 first_voice(u32 d) { return twelve() ? d : 2 * d; }
static u32 last_voice(u32 d) { return twelve() ? d : pair() ? 2 * d + 1 : 2 * d; }

/* Release the MPE voice(s) of unit d; return how many were released. */
static u32 release_dsp_voices(u32 d, u32 layer, u32 arg, int match, u32 note)
{
    u32 v, n = 0;
    for (v = first_voice(d); v <= last_voice(d); v++) {
        if (voice_age[v] == 0)
            continue;
        if (match && voice_note[v] != note)
            continue;
        ReleaseVoiceAndCompactAges(v, layer, arg, note);
        n++;
    }
    return n;
}

static void drop_ownership(u32 d)
{
    u32 owner = S.dsp_owner[d], g;
    if (owner < NUM_CH)
        for (g = 0; g < 2; g++)
            if (S.member_dsp[owner][g] == d)
                S.member_dsp[owner][g] = NONE;
    S.dsp_owner[d] = NONE;
}

/* A DSP is free when its MPE voice(s) are idle, whatever the stale owner. */
static int dsp_free(u32 d)
{
    u32 v;
    for (v = first_voice(d); v <= last_voice(d); v++)
        if (voice_age[v] != 0)
            return 0;
    return 1;
}

/* Choose a unit in group g: the next free one in rotation, otherwise the one
 * whose primary voice holds the oldest note (ages are compacted per stock
 * voice range, which matches the group in Stack and Split). 12 Voice
 * prefers a free voice whose DSP is idle (fewer shared controllers). */
static u32 choose_dsp(u32 g)
{
    u32 first = group_first_dsp(g), size = group_size();
    u32 i, d, score, best = NONE, best_score = 0, best_age = 0xffffffffu;
    for (i = 0; i < size; i++) {
        d = first + (S.cursor[g] + i) % size;
        if (!dsp_free(d))
            continue;
        if (!twelve())
            return d;
        score = voice_age[d ^ 1] == 0 ? 2 : 1;
        if (score > best_score) {
            best = d;
            best_score = score;
        }
    }
    if (best != NONE)
        return best;
    for (i = 0; i < size; i++) {
        u32 age = voice_age[first_voice(first + i)];
        if (age != 0 && age < best_age) {
            best_age = age;
            best = first + i;
        }
    }
    return best == NONE ? first + S.cursor[g] % size : best;
}

/* DSP of group g owned by virtual owner c, or NONE. */
static u32 virtual_dsp(u32 c, u32 g)
{
    u32 i, first = group_first_dsp(g), size = group_size();
    for (i = 0; i < size; i++)
        if (S.dsp_owner[first + i] == c)
            return first + i;
    return NONE;
}

/* Stock allocation while MPE is active (manager channel, local keyboard).
 * Every voice that this call gave the note to (newly active, or holding
 * another note before) is handled after the stock Note On:
 *  - with the per-voice bend, it gets offset 0;
 *  - a member that still owned that voice's unit loses it (voice A in Single
 *    mode, either voice in Pair and 12 Voice), so its later expression
 *    cannot move the new note;
 *  - an unowned unit gets the manager pressure and Y alone, with no member
 *    part: always for the 12 Voice per-voice Y and Breath, and for the
 *    values the stock firmware also writes (pressure, per-DSP controllers)
 *    only when a member note left its own values there (dirty), so that a
 *    held local aftertouch is kept on a new key. In Single mode a note on
 *    voice B of an owned DSP shares the owner's per-DSP values. */
static void stock_allocate_voice(u32 layer, u32 arg, u32 note)
{
    u32 v, u, prev_age[12], prev_note[12];
    int vb = MPE_DSP_VOICE_BEND && voice_bend_active();
    if (!mpe_active()) {
        orig_allocate_voice(layer, arg, note);
        return;
    }
    for (v = 0; v < 12; v++) {
        prev_age[v] = voice_age[v];
        prev_note[v] = voice_note[v];
    }
    orig_allocate_voice(layer, arg, note);
    for (v = 0; v < 12; v++) {
        if (voice_age[v] == 0 || voice_note[v] != note ||
            (prev_age[v] != 0 && prev_note[v] == note))
            continue;
        if (vb)
            SendCommandToDsp(v / 2, voice_sel(v) | CMD_VOICE_BEND | 0x8000u);
        u = unit_of_voice(v);
        if (managed_voice(v) && S.dsp_owner[u] != NONE)
            drop_ownership(u);
        if (S.dsp_owner[u] != NONE)
            continue;
        if (twelve()) {
            SendCommandToDsp(v / 2, voice_sel(v) | CMD_VOICE_CTL | VCTL_Y |
                             (y_bright() ? S.manager_cc74 : 0u));
            send_ctl(u, NONE, CTL_BREATH);
        }
        if (!(S.dirty & (1u << u)))
            continue;
        S.dirty &= (u16)~(1u << u);
        send_pressure(u, NONE);
        if (!twelve()) {
            if (y_bright())
                send_cc74(u, NONE);
            if (y_matrix())
                send_ctl(u, NONE, CTL_BREATH);
        }
    }
}

/* ---- Hook: AllocateVoiceForNote (0x9d01c074) ---- */
void mpe_allocate_voice(u32 layer, u32 arg, u32 note)
{
    u32 c, d, v, g;
    layer &= 0xff;
    arg &= 0xffff;
    note &= 0xffff;
    ensure_init();
    c = S.ctx;
    if ((c >= NUM_CH && !is_virtual(c)) || !mpe_active() ||
        (GetLayerParameter(layer, L_UNISON) & 0xffff) != 0) {
        stock_allocate_voice(layer, arg, note);
        return;
    }
    g = group_of_layer(layer);
    /* A member never holds two DSPs in the same group. */
    if (c < NUM_CH && (d = owned_dsp(c, g)) != NONE) {
        release_dsp_voices(d, layer, arg, 0, 0);
        drop_ownership(d);
    }
    d = choose_dsp(g);
    release_dsp_voices(d, layer, arg, 0, 0);
    drop_ownership(d);

    send_expression(d, c); /* before Note On, so the attack starts in tune */
    S.dirty |= (u16)(1u << d);
    for (v = first_voice(d); v <= last_voice(d); v++)
        ActivateVoiceForNote(v, layer, arg, note);

    S.dsp_owner[d] = (u8)c;
    if (c < NUM_CH)
        S.member_dsp[c][g] = (u8)d;
    S.cursor[g] = (u8)((d - group_first_dsp(g) + 1) % group_size());
}

/* A voice that MPE manages: voice A of an owned DSP in Single mode, either
 * voice in Pair mode, an owned voice in 12 Voice (owner = member or frozen
 * arp source). */
static int mpe_owned_voice(u32 v)
{
    return S.dsp_owner[unit_of_voice(v)] != NONE && managed_voice(v);
}

/* Stock release of a non-member note while MPE is active. The stock
 * polyphonic scan releases every voice whose note matches (full 32-bit
 * compare of voice_note at 0x9d01c94c), which would also end a member note
 * of the same number. MPE-managed voices are hidden from it for the call. */
static void stock_release_voices(u32 layer, u32 arg, u32 note)
{
    u32 v, saved[12];
    for (v = 0; v < 12; v++) {
        saved[v] = voice_note[v];
        if (mpe_owned_voice(v))
            voice_note[v] = 0xffffffffu;
    }
    orig_release_voices(layer, arg, note);
    for (v = 0; v < 12; v++)
        if (mpe_owned_voice(v))
            voice_note[v] = saved[v];
}

/* ---- Hook: ReleaseVoicesMatchingNote (0x9d01c7fc) ---- */
void mpe_release_voices(u32 layer, u32 arg, u32 note)
{
    u32 c, d;
    layer &= 0xff;
    arg &= 0xffff;
    note &= 0xffff;
    ensure_init();
    c = S.ctx;
    if (!mpe_active()) {
        orig_release_voices(layer, arg, note);
        return;
    }
    if (c >= NUM_CH && !is_virtual(c)) {
        stock_release_voices(layer, arg, note);
        return;
    }
    /* Owner-scoped: never fall back to the note-only scan. Stack calls
     * this once per layer, so only the DSP of this layer's group goes. */
    d = c < NUM_CH ? owned_dsp(c, group_of_layer(layer))
                   : virtual_dsp(c, group_of_layer(layer));
    /* A stale Note Off (the channel already moved to another note) matches
     * nothing and must not drop the live note's ownership. */
    if (d != NONE && release_dsp_voices(d, layer, arg, 1, note) != 0)
        drop_ownership(d);
}

/* ---- Arpeggiator (research/arpeggiator.md) ---- */

static int valid_source(u32 c) { return (c >= 1 && c < NUM_CH) || is_virtual(c); }

static int entry_valid(u32 L, u32 i)
{
    return i < arp_count[L] && i < ARP_LEN &&
           A.note[L][i] == (u8)arp_note_list[L * ARP_LEN + i];
}

/* Source of the list entry the arp is about to play. */
static u32 arp_source(u32 L)
{
    u32 i, src = NONE;
    if ((GetLayerParameter(L, L_ARP_LOCK) & 0xffff) != 0) {
        src = A.lock_src[L];
    } else {
        i = arp_index[L];
        if (entry_valid(L, i))
            src = A.tag[L][i];
    }
    return valid_source(src) ? src : VIRT_NEUTRAL;
}

static int slot_in_use(u32 v)
{
    u32 L, i;
    for (L = 0; L < 2; L++) {
        if (A.lock_src[L] == v || A.playing_src[L] == v)
            return 1;
        for (i = 0; i < ARP_LEN; i++)
            if (A.tag[L][i] == v && entry_valid(L, i))
                return 1;
    }
    for (i = 0; i < 12; i++)
        if (S.dsp_owner[i] == v)
            return 1;
    return 0;
}

/* Snapshot member c's expression into a free slot; return its owner id,
 * or the neutral owner when every slot is in use. */
static u32 freeze_member(u32 c)
{
    u32 s, k;
    struct Frozen *f;
    for (s = 1; s < NUM_FROZEN; s++) {
        if (slot_in_use(NUM_CH + s))
            continue;
        f = &A.frozen[s];
        f->pitch = S.member_pitch[c];
        f->pressure = S.member_pressure[c];
        f->cc74 = S.member_cc74[c];
        for (k = 0; k < NUM_CTL; k++)
            f->ctl[k] = S.member_ctl[c][k];
        return NUM_CH + s;
    }
    return VIRT_NEUTRAL;
}

/* After member c's Note Off: entries it added that stay in an arpeggiated
 * layer's list (Hold, Auto Latch) are detached from the channel, so a new
 * finger on the same channel does not bend them. */
static void arp_detach_member(u32 c)
{
    u32 L, i, g, d, v = NONE;
    for (L = 0; L < 2; L++) {
        if (!arp_on(L))
            continue;
        for (i = 0; i < ARP_LEN; i++) {
            if (A.tag[L][i] != c || !entry_valid(L, i))
                continue;
            if (v == NONE)
                v = freeze_member(c);
            A.tag[L][i] = (u8)v;
        }
        if (A.lock_src[L] == c) {
            if (v == NONE)
                v = freeze_member(c);
            A.lock_src[L] = (u8)v;
        }
        if (A.playing_src[L] == c) {
            if (v == NONE)
                v = freeze_member(c);
            g = group_of_layer(L);
            if ((d = owned_dsp(c, g)) != NONE) {
                S.dsp_owner[d] = (u8)v;
                S.member_dsp[c][g] = NONE;
            }
            A.playing_src[L] = (u8)v;
        }
    }
}

/* ---- Hook: ArpListAddNote (0x9d033cc4) ----
 * Called for every routed Note On, arp on or off. Record the source of the
 * entry the stock code appends. */
void mpe_arp_list_add(u32 layer, u32 note)
{
    u32 L = layer & 0xff, before, after, src;
    before = L < 2 ? arp_count[L] : 0;
    orig_arp_list_add(layer, note);
    if (L >= 2)
        return;
    ensure_init();
    src = S.ctx < NUM_CH ? S.ctx : NONE;
    if ((GetLayerParameter(L, L_ARP_LOCK) & 0xffff) != 0) {
        A.lock_src[L] = (u8)src; /* the key transposes the locked pattern */
        return;
    }
    after = arp_count[L];
    if (after == before + 1 && before < ARP_LEN) {
        A.tag[L][before] = (u8)src;
        A.note[L][before] = (u8)arp_note_list[L * ARP_LEN + before];
    }
}

/* ---- Hook: ArpListRemoveNote (0x9d033e60) ----
 * The stock code removes the last entry with the note number and shifts
 * the list down; mirror the shift. When two members hold the same note,
 * first move the releasing member's tag to the entry that goes. */
void mpe_arp_list_remove(u32 layer, u32 note)
{
    u32 L = layer & 0xff, before, after, i, k = NONE, c, t;
    if (L >= 2) {
        orig_arp_list_remove(layer, note);
        return;
    }
    ensure_init();
    note &= 0xffff;
    before = arp_count[L];
    if (before > ARP_LEN)
        before = ARP_LEN;
    for (i = 0; i < before; i++)
        if (arp_note_list[L * ARP_LEN + i] == note)
            k = i;
    c = S.ctx;
    if (k != NONE && c < NUM_CH && A.tag[L][k] != c) {
        for (i = 0; i < k; i++) {
            if (arp_note_list[L * ARP_LEN + i] == note && A.tag[L][i] == c) {
                t = A.tag[L][i];
                A.tag[L][i] = A.tag[L][k];
                A.tag[L][k] = (u8)t;
                break;
            }
        }
    }
    orig_arp_list_remove(layer, note);
    after = arp_count[L];
    if (after + 1 == before && k != NONE) {
        for (i = k; i < after; i++) {
            A.tag[L][i] = A.tag[L][i + 1];
            A.note[L][i] = A.note[L][i + 1];
        }
    }
}

/* ---- Hook: ArpStep's TrackAndAllocateNote call (0x9d03468c) ----
 * Allocate the step for the entry's source. */
void mpe_arp_allocate(u32 note, u32 arg, u32 layer)
{
    u32 L = layer & 0xff, saved = S.ctx;
    if (L < 2 && S.magic == MPE_MAGIC && mpe_active()) {
        S.ctx = (u8)arp_source(L);
        A.playing_src[L] = S.ctx;
    }
    TrackAndAllocateNote(note, arg, layer);
    S.ctx = (u8)saved;
}

/* ---- Hook: the arp's TrackAndReleaseNote calls (0x9d034ae8, 0x9d034b8c,
 * 0x9d033c4c, 0x9d033fbc) ---- release for the source that played it. */
void mpe_arp_release(u32 arg, u32 layer, u32 note)
{
    u32 L = layer & 0xff, saved = S.ctx;
    if (L < 2 && S.magic == MPE_MAGIC && mpe_active())
        S.ctx = A.playing_src[L];
    TrackAndReleaseNote(arg, layer, note);
    S.ctx = (u8)saved;
}

/* ---- Hook: MidiClockTick's ArpTick calls (0x9d02ca18, 0x9d02ca20) ----
 * External MIDI clock runs in the UART and Timer 3 ISRs. While MPE is
 * active, count the tick and let the main loop run it, so all MPE state is
 * only touched from the main loop (as with the internal clock). */
void mpe_ext_arp_tick(u32 layer)
{
    if (S.magic == MPE_MAGIC && mpe_active()) {
        A.ext_in[layer & 1]++;
        return;
    }
    ArpTick(layer);
}

/* ---- Hook: the main loop's two calls of 0x9d01a074 (0x9d0016c4,
 * 0x9d00194c); one of them runs on every iteration. ---- */
void mpe_main_poll(void)
{
    u32 L;
    if (S.magic == MPE_MAGIC) {
        for (L = 0; L < 2; L++) {
            while (A.ext_done[L] != A.ext_in[L]) {
                A.ext_done[L]++;
                ArpTick(L);
            }
        }
    }
    MainLoopHelper();
}

/* Fallback for programs that cannot use MPE (Split, Stack, Multi, Unison,
 * Arpeggiator): member notes are played as manager-channel notes. */
static int fallback_play(void)
{
    ensure_init();
    return S.set[SET_ENABLED] && S.set[SET_FALLBACK] == 0;
}

/* ---- Hook: MidiIsChannelAccepted (0x9d02c3f4) ---- */
u32 mpe_channel_accepted(u32 channel)
{
    ensure_init();
    if ((channel & 0xff) <= S.members && (mpe_active() || fallback_play()))
        return 1; /* manager channel 1 and the member channels of the zone */
    return orig_channel_accepted(channel); /* channels above the zone: stock filter */
}

/* ---- Hook: local pitch wheel, call at 0x9d01603c ---- */
void mpe_local_wheel(u32 word)
{
    if (mpe_active()) {
        if (S.set[SET_WHEEL] == 0) {
            S.manager_pitch = (u16)(word & 0x3fff);
            resend_all_pitch();
        }
        return;
    }
    BroadcastDspCommand(word);
}

/* Send the bend range now: the MPE setting while MPE is active, otherwise
 * the program's own values, per layer group in Stack and Split. */
/* MPE Y > Bright Off: the DSP CC74 term is a fixed cutoff offset that the
 * stock firmware never resets (research/sharc_dsp_code.md); clear it. 12
 * Voice keeps it at 0 too (Y goes per voice); outside 12 Voice the per-voice
 * Y is cleared. */
static void clear_brightness(void)
{
    if (mpe_active() && (!y_bright() || twelve()))
        BroadcastDspCommand(CMD_CC74 | 0u);
    if (MPE_DSP_VOICE_BEND && !(twelve() && mpe_active() && y_bright()))
        BroadcastDspCommand(Y_NEUTRAL);
    /* Breath: per voice in 12 Voice, per DSP otherwise. Clear the side that
     * is not in use, so that a value left by the other mode does not add. */
    if (MPE_DSP_VOICE_BEND) {
        if (twelve() && mpe_active())
            BroadcastDspCommand(CMD_BREATH | 0u);
        else
            BroadcastDspCommand(BREATH_NEUTRAL);
    }
}

static void apply_bend_range(void)
{
    u32 layer;
    clear_brightness();
    if (MPE_DSP_VOICE_BEND && !voice_bend_active())
        BroadcastDspCommand(VOICE_BEND_NEUTRAL); /* no stale per-voice bend */
    if (mpe_active() && S.set[SET_BEND] != 0) {
        BroadcastDspCommand((P_BEND_UP << 16) | S.set[SET_BEND]);
        BroadcastDspCommand((P_BEND_DOWN << 16) | S.set[SET_BEND]);
        return;
    }
    if (ab_mode() != 0 || (GetGlobalParameter(G_MULTI_MODE) & 0xffff) != 0) {
        for (layer = 0; layer < 2; layer++) {
            SendCommandToLayerDsps(layer, (P_BEND_UP << 16) |
                                   (GetLayerParameter(layer, P_BEND_UP) & 0xffff));
            SendCommandToLayerDsps(layer, (P_BEND_DOWN << 16) |
                                   (GetLayerParameter(layer, P_BEND_DOWN) & 0xffff));
        }
        return;
    }
    layer = sounding_layer();
    BroadcastDspCommand((P_BEND_UP << 16) | (GetLayerParameter(layer, P_BEND_UP) & 0xffff));
    BroadcastDspCommand((P_BEND_DOWN << 16) | (GetLayerParameter(layer, P_BEND_DOWN) & 0xffff));
}

/* ---- Hook: SendProgramParametersToDsps (0x9d02b06c) ----
 * After every full program transfer, override the pitch-bend range on all
 * DSPs while MPE is active. The program in RAM and on screen keeps its own
 * values; editing the range on the panel sends the program value again until
 * the next transfer. Values above the stock limits (12 up, 24 down) are an
 * experiment: their DSP-side behavior is unknown. */
void mpe_send_program_parameters(void)
{
    orig_send_program_parameters();
    clear_brightness();
    if (MPE_DSP_VOICE_BEND && !voice_bend_active())
        BroadcastDspCommand(VOICE_BEND_NEUTRAL); /* e.g. a program without MPE */
    if (S.set[SET_BEND] != 0 && mpe_active()) {
        BroadcastDspCommand((P_BEND_UP << 16) | S.set[SET_BEND]);
        BroadcastDspCommand((P_BEND_DOWN << 16) | S.set[SET_BEND]);
    }
    if ((S.manager_pressure | S.manager_cc74) != 0 && mpe_active())
        resend_manager(3, 0); /* e.g. the program before could not use MPE */
}

/* ---- Settings persistence ---- */
static u32 record_checksum(const u8 *values, u32 count)
{
    u32 i, sum = 0xa5;
    for (i = 0; i < count; i++)
        sum = (sum + values[i]) & 0xff;
    return sum;
}

/* Hook: tail call of SaveGlobalsToSpiFlash to SpiFlashProgram256 at
 * 0x9d003d6c. The stock code has just erased the sector and filled bytes
 * 0-77 of the page buffer; the MPE record is added at offset 0x80. */
void mpe_save_globals_page(u32 address)
{
    u32 i;
    ensure_init();
    if ((address & 0xffffff) == GLOBALS_PAGE) {
        volatile u8 *r = spi_page_buffer + REC_OFFSET;
        r[0] = 'M';
        r[1] = 'P';
        r[2] = 'E';
        r[3] = REC_VERSION;
        r[4] = NUM_SETTINGS;
        for (i = 0; i < NUM_SETTINGS; i++)
            r[5 + i] = S.set[i];
        r[5 + NUM_SETTINGS] = (u8)record_checksum(S.set, NUM_SETTINGS);
    }
    SpiFlashProgram256(address);
}

/* Records written by an image with fewer settings (count < NUM_SETTINGS)
 * are accepted; the missing settings keep their defaults. A version 1
 * record (up to ten settings: ..., Y > Bright, Y > Matrix, Z > Express,
 * Wheel, ...) is converted: Y > Matrix gives Y > Timbre (Matrix+Cut with
 * Y > Bright), Z > Express is dropped. */
#define REC_V1_MAX 10u
static void load_settings(void)
{
    u8 r[5 + REC_V1_MAX + 1];
    u32 i, count, max;
    for (i = 0; i < sizeof r; i++)
        r[i] = (u8)SpiReadByte(GLOBALS_PAGE + REC_OFFSET + i);
    count = r[4];
    max = r[3] == 1 ? REC_V1_MAX : NUM_SETTINGS;
    if (r[0] != 'M' || r[1] != 'P' || r[2] != 'E' || (r[3] != 1 && r[3] != REC_VERSION) ||
        count == 0 || count > max || r[5 + count] != record_checksum(r + 5, count))
        return; /* keep defaults */
    if (r[3] == 1) {
        if (count > 4)
            r[5 + SET_Y] = r[5 + 4] ? (r[5 + 3] ? Y_MATRIX_CUT : Y_MATRIX) : 0;
        else if (count > 3)
            count = 3; /* Y > Bright alone: keep the Y default */
        for (i = 4; i + 2 < count; i++)
            r[5 + i] = r[5 + i + 2];
        if (count > 4)
            count = count < 6 ? 4 : count - 2;
    }
    for (i = 0; i < count; i++)
        S.set[i] = r[5 + i] <= setting_max[i] ? r[5 + i] : setting_default[i];
}

/* ---- Hook: boot call to LoadGlobalsFromSpiFlash at 0x9d011d94 ---- */
void mpe_boot(void)
{
    mpe_reset();
    LoadGlobalsFromSpiFlash();
    load_settings();
}

static void member_legacy(u32 c, u32 status, u32 note, u32 value, u32 extra)
{
    S.ctx = (u8)c;
    orig_midi_submit_event(status, 0, note, value, extra);
    S.ctx = NONE;
}

static void member_note_off(u32 c, u32 extra)
{
    u32 note = S.member_note[c];
    if (note == NONE)
        return;
    S.member_note[c] = NONE;
    member_legacy(c, EV_NOTE_OFF, note, 0, extra);
    arp_detach_member(c);
}

static void member_reset_expression(u32 c)
{
    u32 g, d;
    member_clear_expression(c);
    for (g = 0; g < 2; g++)
        if ((d = owned_dsp(c, g)) != NONE)
            send_expression(d, c);
}

static void release_all_mpe(void)
{
    u32 d, c;
    for (d = 0; d < 12; d++) {
        if (S.dsp_owner[d] != NONE) {
            /* Layer argument selects the age-compaction range: voices 6-11
             * (DSPs 3-5) hold layer B in Stack and Split. */
            if (d < num_units())
                release_dsp_voices(d, first_voice(d) >= 6 ? 1 : 0, 0, 0, 0);
            drop_ownership(d);
        }
    }
    for (c = 0; c < NUM_CH; c++) {
        S.member_note[c] = NONE;
        S.member_dsp[c][0] = NONE;
        S.member_dsp[c][1] = NONE;
    }
    S.dirty = 0xfff; /* any unit may hold member values (and units may change) */
}

static void member_set_pressure(u32 c, u32 value)
{
    S.member_pressure[c] = (u8)(value & 0x7f);
    send_member_pressure(c);
}

static void handle_member(u32 c, u32 status, u32 d1, u32 d2, u32 extra)
{
    switch (status) {
    case EV_NOTE_ON:
        member_note_off(c, extra); /* one note per member channel */
        S.member_note[c] = (u8)d1;
        member_legacy(c, EV_NOTE_ON, d1, d2, extra);
        break;
    case EV_NOTE_OFF:
        if (S.member_note[c] == d1)
            member_note_off(c, extra);
        break;
    case EV_PITCH_BEND:
        S.member_pitch[c] = (u16)(d2 & 0x3fff);
        send_member_pitch(c);
        break;
    case EV_CHANNEL_PRESSURE:
        member_set_pressure(c, d2);
        break;
    case EV_POLY_PRESSURE:
        /* One note per member channel: poly pressure for that note is the
         * member's pressure; for any other note it is consumed. */
        if (d1 == S.member_note[c])
            member_set_pressure(c, d2);
        break;
    case EV_CONTROL:
        if (d1 == 74) {
            S.member_cc74[c] = (u8)(d2 & 0x7f);
            if (y_bright())
                send_member_cc74(c);
            if (y_matrix())
                set_ctl(c, CTL_BREATH, d2);
        } else if (d1 == 1) {
            set_ctl(c, CTL_MOD_WHEEL, d2);
        } else if (d1 == 2) {
            set_ctl(c, CTL_BREATH, d2);
        } else if (d1 == 4) {
            set_ctl(c, CTL_FOOT, d2);
        } else if (d1 == 11) {
            set_ctl(c, CTL_EXPRESSION, d2);
        } else if (d1 == 120 || d1 == 123) {
            member_note_off(c, extra);
        } else if (d1 == 121) {
            member_reset_expression(c);
        }
        break; /* other member CCs are consumed */
    default:
        break; /* anything else on a member: consumed */
    }
}

static void handle_manager(u32 status, u32 d1, u32 d2, u32 extra)
{
    u32 c;
    switch (status) {
    /* Note On/Off take the default stock path with no member context, like
     * the local keyboard: stock_allocate_voice / stock_release_voices. */
    case EV_CHANNEL_PRESSURE:
        S.manager_pressure = (u8)(d2 & 0x7f);
        resend_manager(1, 0); /* never the legacy broadcast */
        return;
    case EV_PITCH_BEND:
        S.manager_pitch = (u16)(d2 & 0x3fff);
        resend_all_pitch(); /* never the legacy broadcast */
        return;
    case EV_CONTROL:
        if (d1 == 74) {
            S.manager_cc74 = (u8)(d2 & 0x7f);
            resend_manager(2, 0);
            return;
        }
        orig_midi_submit_event(status, 0, d1, d2, extra);
        if (d1 == 120 || d1 == 123) {
            release_all_mpe();
        } else if (d1 == 121) {
            S.manager_pitch = PITCH_CENTRE;
            S.manager_pressure = 0;
            S.manager_cc74 = 0;
            for (c = 1; c <= MEMBER_COUNT; c++)
                member_reset_expression(c);
            /* The stock reset leaves the CC74 term and the per-voice words. */
            resend_manager(2, 0);
        }
        return;
    default:
        orig_midi_submit_event(status, 0, d1, d2, extra);
        return;
    }
}

/* Fallback for programs that cannot use MPE: member notes play as
 * manager-channel notes through the stock path. Expression stays per layer
 * (stock broadcast), so it follows the most recent note: the member that
 * played the last Note On. Expression of other members is only cached, and
 * a new note first sends its member's cached values. */
static void fallback_member(u32 c, u32 status, u32 d1, u32 d2, u32 extra)
{
    switch (status) {
    case EV_NOTE_ON:
        S.fallback_last = (u8)c;
        orig_midi_submit_event(EV_PITCH_BEND, 0, 0, S.member_pitch[c], extra);
        orig_midi_submit_event(EV_CHANNEL_PRESSURE, 0, 0, S.member_pressure[c], extra);
        orig_midi_submit_event(EV_CONTROL, 0, 74, S.member_cc74[c], extra);
        orig_midi_submit_event(status, 0, d1, d2, extra);
        return;
    case EV_NOTE_OFF:
        orig_midi_submit_event(status, 0, d1, d2, extra);
        return;
    case EV_PITCH_BEND:
        S.member_pitch[c] = (u16)(d2 & 0x3fff);
        break;
    case EV_CHANNEL_PRESSURE:
        S.member_pressure[c] = (u8)(d2 & 0x7f);
        break;
    case EV_CONTROL:
        if (d1 == 74)
            S.member_cc74[c] = (u8)(d2 & 0x7f);
        break;
    default:
        return; /* poly pressure and other member messages: consumed */
    }
    if (c == S.fallback_last)
        orig_midi_submit_event(status, 0, d1, d2, extra);
}

/* ---- Hook: MidiSubmitEvent (0x9d01a3f0) ---- */
void mpe_setting_set(u32 i, u32 value);

void mpe_midi_submit_event(u32 status, u32 channel, u32 d1, u32 d2, u32 extra)
{
    status &= 0xff;
    channel &= 0xff;
    d1 &= 0xffff;
    d2 &= 0xffff;
    extra &= 0xffff;
    ensure_init();
    if (status == EV_NRPN && d1 - NRPN_SETTINGS < NUM_SETTINGS) {
        mpe_setting_set(d1 - NRPN_SETTINGS, d2); /* out of range, increment/decrement: ignored */
        return;
    }
    if (S.ctx != NONE || channel >= NUM_CH) {
        orig_midi_submit_event(status, channel, d1, d2, extra);
        return;
    }
    if (!mpe_active()) {
        if (channel >= 1 && channel <= S.members && fallback_play()) {
            fallback_member(channel, status, d1, d2, extra);
            return;
        }
        /* A program that cannot use MPE: the stock path broadcasts the
         * manager pressure and CC74; remember them for the next MPE program. */
        if (channel == 0 && S.set[SET_ENABLED]) {
            if (status == EV_CHANNEL_PRESSURE)
                S.manager_pressure = (u8)(d2 & 0x7f);
            else if (status == EV_CONTROL && d1 == 74)
                S.manager_cc74 = (u8)(d2 & 0x7f);
            else if (status == EV_CONTROL && d1 == 121)
                S.manager_pressure = S.manager_cc74 = 0;
        }
        orig_midi_submit_event(status, channel, d1, d2, extra);
        return;
    }
    if (channel == 0)
        handle_manager(status, d1, d2, extra);
    else if (channel <= S.members)
        handle_member(channel, status, d1, d2, extra);
    else
        orig_midi_submit_event(status, channel, d1, d2, extra); /* above the zone */
}

/* ---- MPE Configuration Message (RPN 6 on the manager channel) ----
 * The stock parsers know no RPN: CC100/CC101 never deselect the NRPN, so a
 * Data Entry that follows an RPN selection is applied to the last NRPN
 * (DIN 0x9d02ba44, USB 0x9d035c24). The parser hooks below see every
 * accepted Control Change before the stock code. An RPN selection writes
 * "no NRPN" to that transport's stock NRPN number, so the stock code drops
 * the Data Entry (CC6, CC38, CC96, CC97) until the next CC98/CC99. */
#define NRPN_NONE 0x3fffu
#define RPN_MCM 6u

/* n member channels (2..n+1); 0 turns the zone off. The result is the MPE
 * Mode setting plus the volatile member count; MPE notes are released on a
 * change as for a setting change. */
static void mcm(u32 n)
{
    if (n == 0) {
        mpe_setting_set(SET_ENABLED, 0);
        return;
    }
    if (n > MEMBER_COUNT)
        n = MEMBER_COUNT;
    mpe_setting_set(SET_ENABLED, 1); /* off -> on: full zone first */
    if (S.members != n) {
        if (mpe_active())
            release_all_mpe();
        S.members = (u8)n;
    }
}

static void cc_seen(u32 t, u32 channel, u32 cc, u32 value)
{
    ensure_init();
    if (cc == 100 || cc == 101) {
        if (S.rpn_ch[t] != channel) { /* RPN selection is per channel */
            S.rpn_ch[t] = (u8)channel;
            S.rpn[t][0] = S.rpn[t][1] = 0x7f;
        }
        S.rpn[t][cc - 100] = (u8)value;
        S.rpn_sel[t] = 1;
        if (t)
            usb_nrpn_number = NRPN_NONE;
        else
            din_nrpn_number = NRPN_NONE;
    } else if (cc == 98 || cc == 99) {
        S.rpn_sel[t] = 0; /* the stock code selects the NRPN again */
    } else if (cc == 6 && channel == 0 && S.rpn_sel[t] && S.rpn_ch[t] == 0 &&
               S.rpn[t][1] == 0 && S.rpn[t][0] == RPN_MCM) {
        mcm(value);
    }
}

/* ---- Hook: MidiDispatchChannelVoice's read of the Control Change value,
 * jal MidiReadDataByte at 0x9d02ba04 (DIN). The delay slot has stored the
 * controller number in the event record. ---- */
u32 mpe_din_cc_read(void)
{
    u32 value = MidiReadDataByte();
    cc_seen(0, din_channel, din_cc_number & 0xff, value & 0x7f);
    return value;
}

/* ---- Hook: UsbMidiProcessEventPackets' channel check of a Control Change
 * packet, jal MidiIsChannelAccepted at 0x9d03589c (USB). ---- */
u32 mpe_usb_cc_accepted(u32 channel)
{
    u32 ok = mpe_channel_accepted(channel);
    volatile u8 *p = usb_rx_packets + 4u * usb_rx_index;
    if (ok)
        cc_seen(1, channel & 0xff, p[2], p[3] & 0x7f);
    return ok;
}

/* ---- Settings API for the menu (mpe_menu.c) ---- */
u32 mpe_setting_count(void) { return NUM_SETTINGS; }

u32 mpe_setting_max(u32 i) { return i < NUM_SETTINGS ? setting_max[i] : 0; }

u32 mpe_setting_get(u32 i)
{
    ensure_init();
    return i < NUM_SETTINGS ? S.set[i] : 0;
}

void mpe_setting_set(u32 i, u32 value)
{
    int was_active;
    ensure_init();
    if (i >= NUM_SETTINGS || value > setting_max[i] || S.set[i] == value)
        return;
    was_active = mpe_active();
    /* Voice ownership depends on these; Bend Range = Program turns 12 Voice off. */
    if ((i == SET_ENABLED || i == SET_PAIR || i == SET_LAYERED || i == SET_ARP ||
         (i == SET_BEND && (value == 0) != (S.set[i] == 0))) && was_active)
        release_all_mpe();
    S.set[i] = (u8)value;
    if (i == SET_ENABLED) {
        S.manager_pressure = S.manager_cc74 = 0; /* a new zone starts neutral */
        S.members = MEMBER_COUNT; /* and full; an MCM sets the size after this */
    }
    if (i == SET_ENABLED || i == SET_BEND || i == SET_LAYERED || i == SET_ARP || i == SET_Y ||
        i == SET_PAIR)
        apply_bend_range(); /* also clears CC74 and per-voice Y (clear_brightness) */
    /* The manager values go where the new settings put them. Changing
     * Y > Timbre also rewrites the Breath source on unowned units. */
    if ((S.manager_pressure | S.manager_cc74) != 0 && mpe_active())
        resend_manager(3, i == SET_Y ? 2u : 0u);
}

/* Re-send the bend range that applies now (MPE override or program values).
 * For code that changes Unison or Arp On, which decide whether MPE is
 * active. */
void mpe_refresh_bend_range(void)
{
    ensure_init();
    apply_bend_range();
}
