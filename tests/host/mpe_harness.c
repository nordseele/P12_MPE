/*
 * Host harness for firmware/patches/mpe/mpe.c.
 *
 * Compiles the patch source natively and replaces the stock firmware
 * functions with models of their confirmed behavior
 * (research/voice_allocation.md): voice age/note tables, age compaction on
 * release, and the legacy MidiSubmitEvent path that reaches the hooked
 * AllocateVoiceForNote / ReleaseVoicesMatchingNote entries.
 * Exits non-zero on the first failed check.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../../firmware/patches/mpe/mpe.c"

struct MpeState mpe_state;
struct ArpMpeState arp_mpe_state;
volatile u16 arp_note_list[2 * ARP_LEN];
volatile u16 arp_count[2];
volatile u16 arp_index[2];
volatile u8 spi_page_buffer[256];
static u8 spi_flash[256];
static int programmed;
void SpiFlashProgram256(u32 address)
{
    u32 i;
    (void)address;
    for (i = 0; i < 256; i++)
        spi_flash[i] = spi_page_buffer[i];
    programmed++;
}
u32 SpiReadByte(u32 address) { return spi_flash[(address - GLOBALS_PAGE) & 0xff]; }
volatile u32 voice_age[12];
volatile u32 voice_note[12];

static u32 globals[0x40];
static u32 layer_params[2][0x200];
static u32 selected_layer;

#define LOG_MAX 512
static u32 log_target[LOG_MAX], log_word[LOG_MAX];
static int log_count;
static int legacy_count, native_alloc_count, native_release_count, broadcast_count;
static u32 last_broadcast;

static void log_reset(void) { log_count = 0; }

static int layer_send_count;
void SendCommandToLayerDsps(u32 layer, u32 word) { (void)layer; (void)word; layer_send_count++; }
void SendCommandToDsp(u32 target, u32 word)
{
    if (log_count < LOG_MAX) {
        log_target[log_count] = target;
        log_word[log_count] = word;
        log_count++;
    }
}
static int y_neutral_count;
void BroadcastDspCommand(u32 word)
{
    broadcast_count++;
    last_broadcast = word;
#if MPE_DSP_VOICE_BEND
    y_neutral_count += word == Y_NEUTRAL;
#endif
}
u32 GetGlobalParameter(u32 i) { return globals[i]; }
u32 GetLayerParameter(u32 l, u32 i) { return layer_params[l & 1][i]; }
u32 GetSelectedLayerState(void) { return selected_layer; }
void LoadGlobalsFromSpiFlash(void) {}

static u32 max_age(void)
{
    u32 v, m = 0;
    for (v = 0; v < 12; v++)
        if (voice_age[v] > m)
            m = voice_age[v];
    return m;
}

static const u32 sel[2] = {0x80000000u, 0x40000000u};

void ActivateVoiceForNote(u32 voice, u32 layer, u32 arg, u32 note)
{
    (void)layer; (void)arg;
    voice_note[voice] = note;
    voice_age[voice] = max_age() + 1;
    SendCommandToDsp(voice / 2, sel[voice % 2] | note | 0x01f50000u);
    SendCommandToDsp(voice / 2, sel[voice % 2] | 100u | 0x01f40000u);
}

void ReleaseVoiceAndCompactAges(u32 voice, u32 layer, u32 arg, u32 note)
{
    u32 v, a = voice_age[voice];
    (void)layer; (void)arg; (void)note;
    for (v = 0; v < 12; v++)
        if (voice_age[v] > a)
            voice_age[v]--;
    voice_age[voice] = 0;
    SendCommandToDsp(voice / 2, sel[voice % 2] | 0x01f30000u);
}

static int stock_voice = -1; /* >= 0: the native allocator model plays this voice */
void ActivateVoiceForNote(u32 voice, u32 layer, u32 arg, u32 note);
void orig_allocate_voice(u32 layer, u32 arg, u32 note)
{
    native_alloc_count++;
    if (stock_voice >= 0)
        ActivateVoiceForNote((u32)stock_voice, layer, arg, note);
}
void orig_release_voices(u32 layer, u32 arg, u32 note) { (void)layer; (void)arg; (void)note; native_release_count++; }
u32 orig_channel_accepted(u32 channel) { return channel == 0; }
static int program_sent;
void orig_send_program_parameters(void) { program_sent++; }

/* Arpeggiator model (research/arpeggiator.md): the stock list add/remove
 * (behind the hooked entries), Assign-order steps, Hold, and Lock Notes. */
static int arp_hold[2], arp_sounding[2], arp_restart[2];
static u16 arp_last[2];
static int arp_tick_calls, helper_calls;

static int arp_layer_on(u32 L) { return layer_params[L & 1][L_ARP] != 0; }
static int arp_locked(u32 L) { return layer_params[L & 1][L_ARP_LOCK] != 0; }

void orig_arp_list_add(u32 layer, u32 note)
{
    u32 L = layer & 0xff;
    if (L >= 2 || arp_locked(L) || arp_count[L] >= ARP_LEN)
        return;
    arp_note_list[L * ARP_LEN + arp_count[L]] = (u16)note;
    arp_count[L]++;
}

void orig_arp_list_remove(u32 layer, u32 note)
{
    u32 L = layer & 0xff, i, k = NONE;
    if (L >= 2 || arp_hold[L] || arp_locked(L) || arp_count[L] == 0)
        return;
    for (i = 0; i < arp_count[L]; i++)
        if (arp_note_list[L * ARP_LEN + i] == note)
            k = i;
    if (k != NONE)
        for (i = k; i + 1 < arp_count[L]; i++)
            arp_note_list[L * ARP_LEN + i] = arp_note_list[L * ARP_LEN + i + 1];
    arp_count[L]--;
    arp_note_list[L * ARP_LEN + arp_count[L]] = 0;
    if (arp_count[L] == 0 && arp_layer_on(L) && arp_sounding[L]) {
        mpe_arp_release(1, L, arp_last[L]);
        arp_sounding[L] = 0;
    }
}

void TrackAndAllocateNote(u32 note, u32 arg, u32 layer) { mpe_allocate_voice(layer, arg, note); }
void TrackAndReleaseNote(u32 arg, u32 layer, u32 note) { mpe_release_voices(layer, arg, note); }

/* ArpStep in Assign order: release the sounding note, play the next entry. */
static void model_arp_step(u32 L)
{
    u32 note;
    if (arp_sounding[L]) {
        mpe_arp_release(1, L, arp_last[L]);
        arp_sounding[L] = 0;
    }
    if (arp_count[L] == 0)
        return;
    arp_index[L] = arp_restart[L] ? 0 : (u16)((arp_index[L] + 1) % arp_count[L]);
    arp_restart[L] = 0;
    note = arp_note_list[L * ARP_LEN + arp_index[L]];
    arp_last[L] = (u16)note;
    arp_sounding[L] = 1;
    mpe_arp_allocate(note, 1, L);
}

void ArpTick(u32 layer) { arp_tick_calls++; model_arp_step(layer & 1); }
void MainLoopHelper(void) { helper_calls++; }

static void arp_reset_model(void)
{
    memset((void *)arp_note_list, 0, sizeof arp_note_list);
    memset((void *)arp_count, 0, sizeof arp_count);
    memset((void *)arp_index, 0, sizeof arp_index);
    memset(arp_hold, 0, sizeof arp_hold);
    memset(arp_sounding, 0, sizeof arp_sounding);
    memset(arp_restart, 0, sizeof arp_restart);
    memset(&arp_mpe_state, 0x5a, sizeof arp_mpe_state); /* power-on garbage */
    arp_tick_calls = helper_calls = 0;
}

/* Legacy path: Note On/Off reach the hooked entries; others are recorded. */
/* Stock RouteNoteOn/OffByLayer + TrackAndAllocate/ReleaseNote: Normal calls
 * the allocator once, Stack (A/B mode 1) once per layer, Split (mode 2) once
 * on the side of the split point (layer A parameter 0x1a8). */
static void legacy_layers(u32 note, int on)
{
    u32 mode = layer_params[0][L_AB_MODE];
    u32 first = selected_layer, last = selected_layer;
    if (mode == 1)
        first = 0, last = 1;
    else if (mode == 2)
        first = last = note < layer_params[0][0x1a8] ? 0 : 1;
    if (mode != 1 && arp_layer_on(first)) {
        /* RouteNoteOn/OffByLayer arp branch, then ArpNoteOn/ArpNoteOff. */
        if (on) {
            u32 before = arp_count[first];
            mpe_arp_list_add(first, note);
            if (before == 0 || arp_locked(first)) {
                arp_restart[first] = 1;
                model_arp_step(first);
            }
        } else {
            mpe_arp_list_remove(first, note);
        }
        return;
    }
    if (on)
        mpe_arp_list_add(mode == 1 ? 2 : first, note);
    else if (mode != 1)
        mpe_arp_list_remove(first, note);
    for (; first <= last; first++) {
        if (on)
            mpe_allocate_voice(first, 1, note);
        else
            mpe_release_voices(first, 1, note);
    }
}

void orig_midi_submit_event(u32 status, u32 channel, u32 d1, u32 d2, u32 extra)
{
    (void)extra; (void)d2;
    legacy_count++;
    if (channel != 0 && S.ctx != NONE) {
        fprintf(stderr, "member event reached legacy without normalized channel\n");
        exit(1);
    }
    if (status == EV_NOTE_ON)
        legacy_layers(d1, 1);
    else if (status == EV_NOTE_OFF)
        legacy_layers(d1, 0);
}

static int failures;
#define CHECK(cond, msg) do { if (!(cond)) { fprintf(stderr, "FAIL %s:%d %s\n", __FILE__, __LINE__, msg); failures++; } } while (0)

static void reset_all(void)
{
    memset((void *)voice_age, 0, sizeof voice_age);
    memset((void *)voice_note, 0, sizeof voice_note);
    memset(globals, 0, sizeof globals);
    memset(layer_params, 0, sizeof layer_params);
    selected_layer = 0;
    memset(&mpe_state, 0xa5, sizeof mpe_state); /* power-on garbage */
    legacy_count = native_alloc_count = native_release_count = broadcast_count = 0;
    program_sent = 0;
    arp_reset_model();
    log_reset();
}

static void note_on(u32 ch, u32 note) { mpe_midi_submit_event(EV_NOTE_ON, ch, note, 100, 0); }
static void note_off(u32 ch, u32 note) { mpe_midi_submit_event(EV_NOTE_OFF, ch, note, 0, 0); }

static int voice_of(u32 note)
{
    int v;
    for (v = 0; v < 12; v++)
        if (voice_age[v] && voice_note[v] == note)
            return v;
    return -1;
}

static int count_active(void)
{
    int v, n = 0;
    for (v = 0; v < 12; v++)
        n += voice_age[v] != 0;
    return n;
}

static int find_cmd(u32 target, u32 word)
{
    int i;
    for (i = 0; i < log_count; i++)
        if (log_target[i] == target && log_word[i] == word)
            return i;
    return -1;
}

static void test_six_notes_one_per_dsp(void)
{
    u32 ch, seen = 0;
    reset_all();
    for (ch = 1; ch <= 6; ch++)
        note_on(ch, 60 + ch);
    CHECK(count_active() == 6, "six voices active");
    for (ch = 1; ch <= 6; ch++) {
        int v = voice_of(60 + ch);
        CHECK(v >= 0 && v % 2 == 0, "Single mode uses the even voice");
        if (v >= 0)
            seen |= 1u << (v / 2);
    }
    CHECK(seen == 0x3f, "each DSP hosts exactly one note");
    CHECK(native_alloc_count == 0, "native allocator bypassed");
}

static void test_steal_oldest(void)
{
    u32 ch;
    int v1;
    reset_all();
    for (ch = 1; ch <= 6; ch++)
        note_on(ch, 60 + ch);
    v1 = voice_of(61);
    note_on(7, 70);
    CHECK(voice_of(61) < 0, "oldest note stolen");
    CHECK(voice_of(70) == v1, "new note takes the oldest note's DSP");
    CHECK(count_active() == 6, "still six voices");
    CHECK(S.member_dsp[1][0] == NONE, "victim member lost its DSP");
    note_off(1, 61); /* stale Note Off must not release anything */
    CHECK(count_active() == 6, "stale Note Off ignored");
}

static void test_duplicate_pitch_owner_scoped(void)
{
    reset_all();
    note_on(2, 64);
    note_on(3, 64);
    CHECK(count_active() == 2, "two instances of the same pitch");
    note_off(2, 64);
    CHECK(count_active() == 1, "only the owner's voice released");
    CHECK(S.member_dsp[3][0] != NONE, "other member keeps its DSP");
    note_off(3, 64);
    CHECK(count_active() == 0, "second release");
}

static void test_expression_routing(void)
{
    u32 d;
    reset_all();
    note_on(2, 60);
    d = S.member_dsp[2][0];
    log_reset();
    mpe_midi_submit_event(EV_PITCH_BEND, 2, 0, 0x3000, 0);
    CHECK(log_count == 1 && find_cmd(d, CMD_PITCH | 0x3000) == 0, "member bend to its DSP only");
    log_reset();
    mpe_midi_submit_event(EV_CHANNEL_PRESSURE, 2, 0, 90, 0);
    CHECK(log_count == 1 && find_cmd(d, CMD_PRESSURE | 90) == 0, "member pressure to its DSP, not to Expression");
    log_reset();
    mpe_midi_submit_event(EV_CONTROL, 2, 74, 20, 0);
    CHECK(log_count == 2 && find_cmd(d, CMD_CC74 | 20) == 0, "member CC74 to its DSP");
    CHECK(find_cmd(d, CMD_BREATH | 20) == 1, "Y doubled to Breath");
    log_reset();
    mpe_midi_submit_event(EV_PITCH_BEND, 0, 0, 0x2100, 0);
    CHECK(log_count == (int)NUM_DSP && find_cmd(d, CMD_PITCH | 0x3100) >= 0, "manager bend combined");
    CHECK(find_cmd((d + 1) % NUM_DSP, CMD_PITCH | 0x2100) >= 0, "manager bend alone on an unowned DSP");
    CHECK(broadcast_count == 0, "no legacy broadcast");
    log_reset();
    mpe_midi_submit_event(EV_PITCH_BEND, 5, 0, 0x1000, 0);
    CHECK(log_count == 0, "bend on a member without a note is cached only");
    log_reset();
    note_on(5, 67);
    d = S.member_dsp[5][0];
    CHECK(find_cmd(d, CMD_PITCH | 0x1100) >= 0, "cached bend sent at allocation");
    CHECK(find_cmd(d, CMD_PITCH | 0x1100) < find_cmd(d, 0x80000000u | 67 | 0x01f50000u),
          "expression precedes Note On");
}

static void test_member_controllers(void)
{
    u32 d;
    reset_all();
    note_on(3, 60);
    d = S.member_dsp[3][0];
    log_reset();
    mpe_midi_submit_event(EV_CONTROL, 3, 1, 11, 0);
    mpe_midi_submit_event(EV_CONTROL, 3, 2, 22, 0);
    mpe_midi_submit_event(EV_CONTROL, 3, 4, 44, 0);
    mpe_midi_submit_event(EV_CONTROL, 3, 11, 111 & 0x7f, 0);
    CHECK(log_count == 4, "four member controller words");
    CHECK(find_cmd(d, CMD_MOD_WHEEL | 11) == 0, "member CC1 -> Mod Wheel");
    CHECK(find_cmd(d, CMD_BREATH | 22) == 1, "member CC2 -> Breath");
    CHECK(find_cmd(d, CMD_FOOT | 44) == 2, "member CC4 -> Foot");
    CHECK(find_cmd(d, CMD_EXPRESSION | 111) == 3, "member CC11 -> Expression");
    CHECK(legacy_count == 1, "member CCs never reach the legacy path");
    mpe_midi_submit_event(EV_CONTROL, 6, 2, 99, 0); /* member without a note */
    log_reset();
    note_on(6, 64);
    d = S.member_dsp[6][0];
    CHECK(find_cmd(d, CMD_BREATH | 99) >= 0, "cached controller sent at allocation");
}

static void test_bend_range_override(void)
{
    reset_all();
    mpe_send_program_parameters();
    CHECK(program_sent == 1, "stock program transfer runs");
    CHECK(broadcast_count == 2 && last_broadcast == ((P_BEND_DOWN << 16) | MPE_BEND_RANGE),
          "bend range overridden on all DSPs");
    reset_all();
    mpe_reset();
    mpe_setting_set(SET_BEND, 0);
    broadcast_count = 0;
    mpe_send_program_parameters();
    CHECK(broadcast_count == 0, "bend setting 0 keeps the program range");
    reset_all();
    globals[G_MULTI_MODE] = 1;
    mpe_send_program_parameters();
    CHECK(program_sent == 1 && broadcast_count == 0, "no override when MPE is inactive");
    reset_all();
    layer_params[0][L_AB_MODE] = 1;
    mpe_send_program_parameters();
    CHECK(broadcast_count == 2, "Stack with layered MPE: range overridden");
}

static void test_settings_persistence(void)
{
    reset_all();
    mpe_reset();
    mpe_setting_set(SET_PAIR, 1);
    mpe_setting_set(SET_BEND, 24);
    mpe_setting_set(SET_WHEEL, 1);
    mpe_save_globals_page(GLOBALS_PAGE);
    CHECK(programmed == 1, "page programmed");
    CHECK(spi_flash[0x80] == 'M' && spi_flash[0x81] == 'P' && spi_flash[0x82] == 'E', "record magic");
    mpe_boot(); /* resets to defaults, then loads the record */
    CHECK(S.set[SET_PAIR] == 1 && S.set[SET_BEND] == 24 && S.set[SET_WHEEL] == 1,
          "settings restored at boot");
    {   /* version 1 records (ten-setting layout): test image 3 wrote eight
         * settings (..., Y > Bright, Y > Matrix, Z > Express, Wheel, Fallback) */
        static const u8 old[2][8] = {{1, 1, 12, 1, 1, 1, 1, 0}, {1, 1, 12, 0, 1, 1, 1, 0}};
        u32 i, k, sum;
        for (k = 0; k < 2; k++) {
            spi_flash[0x83] = 1;
            spi_flash[0x84] = 8;
            for (i = 0, sum = 0xa5; i < 8; i++) {
                spi_flash[0x85 + i] = old[k][i];
                sum = (sum + old[k][i]) & 0xff;
            }
            spi_flash[0x85 + 8] = (u8)sum;
            mpe_boot();
            CHECK(S.set[SET_BEND] == 12 && S.set[SET_Y] == (k ? Y_MATRIX : Y_MATRIX_CUT),
                  "version 1 record: the two Y rows become MPE Y > Timbre");
            CHECK(S.set[SET_WHEEL] == 1 && S.set[SET_FALLBACK] == 0 && S.set[SET_LAYERED] == MPE_LAYERED,
                  "version 1 record: Z > Express dropped, later settings shifted, new ones default");
        }
    }
    mpe_reset();
    CHECK(S.set[SET_PAIR] == (MPE_PAIR_MODE ? 1u : MPE_DSP_VOICE_BEND ? VOICES_TWELVE : 0u),
          "default MPE Voices: 12 Voice on a voice-bend build");
    mpe_save_globals_page(GLOBALS_PAGE);
    spi_flash[0x80 + 5 + SET_BEND] = 200; /* corrupt the record */
    mpe_boot();
    CHECK(S.set[SET_BEND] == MPE_BEND_RANGE, "corrupt record ignored");
    memset(spi_flash, 0, sizeof spi_flash);
    spi_page_buffer[0x80] = 0;
    mpe_save_globals_page(0xc6000); /* another page: no record */
    CHECK(spi_page_buffer[0x80] == 0, "record only on the Global page");
}

static void test_fallback_and_disable(void)
{
    reset_all();
    layer_params[0][L_UNISON] = 1; /* program that cannot use MPE */
    note_on(5, 60);
    CHECK(native_alloc_count == 1, "fallback plays member note");
    CHECK(legacy_count == 4, "fallback sends cached expression, then the note");
    mpe_midi_submit_event(EV_PITCH_BEND, 5, 0, 0x3000, 0);
    CHECK(legacy_count == 5, "fallback forwards the last note's expression");
    mpe_midi_submit_event(EV_PITCH_BEND, 6, 0, 0x1000, 0);
    CHECK(legacy_count == 5, "fallback caches other members' expression");
    note_on(6, 64);
    CHECK(legacy_count == 9 && S.fallback_last == 6, "new note takes over expression");
    CHECK(mpe_channel_accepted(5) == 1, "fallback accepts member channels");
    mpe_setting_set(SET_FALLBACK, 1);
    CHECK(mpe_channel_accepted(5) == 0, "silent fallback uses the stock filter");
    reset_all();
    note_on(2, 60);
    CHECK(count_active() == 1, "MPE note");
    mpe_setting_set(SET_ENABLED, 0);
    CHECK(count_active() == 0, "disabling MPE releases MPE notes");
    note_on(2, 62);
    CHECK(native_alloc_count == 1, "MPE off: stock allocation");
    reset_all();
    note_on(2, 60);
    mpe_setting_set(SET_WHEEL, 1);
    log_reset();
    mpe_local_wheel(CMD_PITCH | 0x2400);
    CHECK(log_count == 0 && broadcast_count == 0, "wheel ignored when set to Off");
}

static int dsp_of_voice(int v) { return v < 0 ? -1 : v / 2; }

static void test_stack_layered(void)
{
    u32 ch;
    int va, vb;
    reset_all();
    layer_params[0][L_AB_MODE] = 1; /* Stack */
    note_on(2, 60);
    CHECK(count_active() == 2, "Stack note uses one voice per layer");
    CHECK(S.member_dsp[2][0] < 3 && S.member_dsp[2][1] >= 3 && S.member_dsp[2][1] < 6,
          "Stack note owns one DSP in each group");
    log_reset();
    mpe_midi_submit_event(EV_PITCH_BEND, 2, 0, 0x3000, 0);
    CHECK(log_count == 2 && find_cmd(S.member_dsp[2][0], CMD_PITCH | 0x3000) >= 0 &&
          find_cmd(S.member_dsp[2][1], CMD_PITCH | 0x3000) >= 0, "Stack bend reaches both DSPs");
    for (ch = 3; ch <= 4; ch++)
        note_on(ch, 60 + ch);
    CHECK(count_active() == 6, "three Stack notes use six voices");
    note_on(5, 70);
    CHECK(count_active() == 6, "fourth Stack note steals");
    CHECK(S.member_dsp[2][0] == NONE && S.member_dsp[2][1] == NONE, "oldest Stack note stolen in both groups");
    note_off(5, 70);
    CHECK(count_active() == 4, "Stack Note Off releases both layers");
    va = -1; vb = -1;
    {
        int v;
        for (v = 0; v < 12; v++)
            if (voice_age[v] && voice_note[v] == 63) {
                if (v < 6) va = v; else vb = v;
            }
    }
    CHECK(dsp_of_voice(va) < 3 && dsp_of_voice(vb) >= 3, "layer voices stay in their ranges");
}

static void test_split_layered(void)
{
    u32 ch;
    reset_all();
    layer_params[0][L_AB_MODE] = 2; /* Split */
    layer_params[0][0x1a8] = 60;
    for (ch = 1; ch <= 3; ch++)
        note_on(ch, 40 + ch); /* left: layer A */
    for (ch = 4; ch <= 6; ch++)
        note_on(ch, 70 + ch); /* right: layer B */
    CHECK(count_active() == 6, "three notes per side");
    for (ch = 1; ch <= 3; ch++)
        CHECK(S.member_dsp[ch][0] < 3 && S.member_dsp[ch][1] == NONE, "left notes on DSPs 0-2");
    for (ch = 4; ch <= 6; ch++)
        CHECK(S.member_dsp[ch][1] >= 3 && S.member_dsp[ch][1] < 6, "right notes on DSPs 3-5");
    note_on(7, 45); /* fourth left note steals on the left only */
    CHECK(S.member_dsp[1][0] == NONE && S.member_dsp[4][1] != NONE, "steal stays on its side");
    reset_all();
    layer_params[0][L_AB_MODE] = 2;
    mpe_setting_set(SET_LAYERED, 0);
    CHECK(mpe_active() == 0, "Stack/Split = Other disables layered MPE");
    reset_all();
    layer_params[0][L_AB_MODE] = 1;
    layer_params[1][L_ARP] = 1;
    CHECK(mpe_active() == 0, "an arpeggiated layer blocks layered MPE");
}

static void test_implicit_note_off(void)
{
    reset_all();
    note_on(4, 60);
    note_on(4, 62);
    CHECK(count_active() == 1, "one note per member");
    CHECK(voice_of(60) < 0 && voice_of(62) >= 0, "previous note released");
}

static void test_nrpn_settings(void)
{
    reset_all();
    mpe_midi_submit_event(0xfd, 0, 433 + SET_Y, 0, 0);
    CHECK(S.set[SET_Y] == 0 && legacy_count == 0, "NRPN 436 sets MPE Y > Timbre, consumed");
    mpe_midi_submit_event(0xfd, 0, 433 + SET_BEND, 24, 0);
    CHECK(S.set[SET_BEND] == 24, "NRPN 435 sets the bend range");
    mpe_midi_submit_event(0xfd, 0, 433 + SET_BEND, 0xffff, 0);
    CHECK(S.set[SET_BEND] == 24 && legacy_count == 0, "increment (0xffff) ignored");
    mpe_midi_submit_event(0xfd, 0, 433 + SET_ENABLED, 0, 0);
    CHECK(S.set[SET_ENABLED] == 0, "NRPN 433 turns MPE off");
    mpe_midi_submit_event(0xfd, 0, 433 + SET_ENABLED, 1, 0);
    CHECK(S.set[SET_ENABLED] == 1, "and on again while inactive");
    mpe_midi_submit_event(0xfd, 0, 432, 5, 0);
    mpe_midi_submit_event(0xfd, 0, 433 + 10, 0, 0); /* reserved, no setting yet */
    mpe_midi_submit_event(0xfd, 0, 1024, 5, 0);
    mpe_midi_submit_event(0xfd, 0, 90, 40, 0);
    CHECK(legacy_count == 4, "432, unassigned 443, globals and program NRPNs take the stock path");
}

static void test_inactive_passthrough(void)
{
    reset_all();
    layer_params[0][L_UNISON] = 1;
    note_on(3, 60);
    CHECK(native_alloc_count == 1, "Unison: native allocator used");
    CHECK(mpe_channel_accepted(3) == 1, "Unison: Play fallback accepts member channels");
    reset_all();
    globals[G_MULTI_MODE] = 1;
    mpe_midi_submit_event(EV_PITCH_BEND, 0, 0, 0x3000, 0);
    CHECK(legacy_count == 1, "Multi: manager bend takes the legacy path");
    reset_all();
    layer_params[0][L_AB_MODE] = 2;
    mpe_setting_set(SET_LAYERED, 0);
    mpe_local_wheel(CMD_PITCH | 0x2800);
    CHECK(broadcast_count == 1 && last_broadcast == (CMD_PITCH | 0x2800),
          "Split with Stack/Split = Other: wheel broadcasts");
}

static void test_wheel_as_manager(void)
{
    u32 d;
    reset_all();
    note_on(2, 60);
    d = S.member_dsp[2][0];
    log_reset();
    mpe_local_wheel(CMD_PITCH | 0x2400);
    CHECK(broadcast_count == 0, "wheel does not broadcast in MPE");
    CHECK(log_count == (int)NUM_DSP && find_cmd(d, CMD_PITCH | 0x2400) >= 0, "wheel acts as manager bend");
    CHECK(mpe_channel_accepted(9) == 1, "member channels accepted");
}

/* Manager-channel notes take the stock path with no member context. */
static void test_manager_notes(void)
{
    reset_all();
    note_on(2, 60);
    note_on(0, 60);
    CHECK(native_alloc_count == 1, "manager Note On: stock allocator");
    CHECK(S.member_note[2] == 60 && S.dsp_owner[S.member_dsp[2][0]] == 2, "member note keeps its DSP");
    note_off(0, 60);
    CHECK(native_release_count == 1 && voice_of(60) >= 0, "manager Note Off: stock release, member note kept");
}

static void test_all_notes_off(void)
{
    u32 ch;
    reset_all();
    for (ch = 1; ch <= 4; ch++)
        note_on(ch, 50 + ch);
    mpe_midi_submit_event(EV_CONTROL, 0, 123, 0, 0);
    CHECK(count_active() == 0, "manager CC123 releases MPE notes");
    for (ch = 1; ch <= 4; ch++)
        CHECK(S.member_note[ch] == NONE && S.member_dsp[ch][0] == NONE, "ownership cleared");
}

/* ---- Arpeggiator ---- */
static void bend(u32 ch, u32 value) { mpe_midi_submit_event(EV_PITCH_BEND, ch, 0, value, 0); }

static int sounding_dsp(u32 note)
{
    int v = voice_of(note);
    return v < 0 ? -1 : v / 2;
}

static void test_arp_per_note(void)
{
    int d;
    reset_all();
    layer_params[0][L_ARP] = 1;
    CHECK(mpe_active(), "Normal arp program: MPE active with MPE Arp = Per-note");
    bend(2, 0x2100);
    note_on(2, 60);
    d = sounding_dsp(60);
    CHECK(d >= 0 && S.dsp_owner[d] == 2, "first step owned by its finger");
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | 0x2100) >= 0, "first step starts at its finger's bend");
    bend(3, 0x2200);
    note_on(3, 64);
    bend(4, 0x2300);
    note_on(4, 67);
    CHECK(count_active() == 1, "held notes do not sound outside the arp");
    log_reset();
    ArpTick(0);
    d = sounding_dsp(64);
    CHECK(d >= 0 && S.dsp_owner[d] == 3, "step 2 owned by member 3");
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | 0x2200) >= 0, "step 2 carries member 3's bend");
    CHECK(sounding_dsp(60) < 0, "previous step released");
    ArpTick(0);
    d = sounding_dsp(67);
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | 0x2300) >= 0, "step 3 carries member 4's bend");
    ArpTick(0);
    d = sounding_dsp(60);
    CHECK(d >= 0 && S.dsp_owner[d] == 2, "the cycle returns to member 2");
    log_reset();
    bend(2, 0x2500);
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | 0x2500) >= 0, "live bend follows the sounding step");
    bend(3, 0x2600);
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | 0x2600) < 0, "other members do not bend the step");
    CHECK(native_alloc_count == 0, "native allocator bypassed");
}

static void test_arp_release_and_remove(void)
{
    reset_all();
    layer_params[0][L_ARP] = 1;
    note_on(2, 60);
    note_on(3, 64);
    note_off(2, 60);
    CHECK(arp_count[0] == 1 && arp_mpe_state.tag[0][0] == 3, "mirror shifted with the list");
    ArpTick(0);
    CHECK(sounding_dsp(64) >= 0 && S.dsp_owner[sounding_dsp(64)] == 3, "remaining entry plays for member 3");
    note_off(3, 64);
    CHECK(arp_count[0] == 0 && count_active() == 0, "last key up releases the arp note");
}

static void test_arp_duplicate_note(void)
{
    reset_all();
    layer_params[0][L_ARP] = 1;
    note_on(2, 60);
    note_on(3, 60);
    note_off(2, 60);
    CHECK(arp_count[0] == 1 && arp_mpe_state.tag[0][0] == 3,
          "the releasing member's entry goes, not the last match");
}

static void test_arp_hold_freeze(void)
{
    int d;
    reset_all();
    layer_params[0][L_ARP] = 1;
    arp_hold[0] = 1;
    bend(2, 0x2100);
    note_on(2, 60);
    bend(3, 0x2200);
    note_on(3, 64);
    note_off(3, 64);
    CHECK(arp_count[0] == 2, "Hold keeps the entry");
    CHECK(is_virtual(arp_mpe_state.tag[0][1]), "lifted finger's entry is frozen");
    bend(3, 0x3000); /* the controller reuses channel 3 for a new finger */
    note_on(3, 72);
    log_reset();
    ArpTick(0);
    d = sounding_dsp(64);
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | 0x2200) >= 0, "latched note keeps its frozen bend");
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | 0x3000) < 0, "new finger does not bend the latched note");
    log_reset();
    bend(3, 0x3100);
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | 0x3100) < 0, "frozen owner ignores channel 3");
    ArpTick(0);
    d = sounding_dsp(72);
    CHECK(d >= 0 && S.dsp_owner[d] == 3, "new finger's entry is live");
    CHECK(sounding_dsp(64) < 0, "frozen step released by its virtual owner");
    log_reset();
    mpe_midi_submit_event(EV_PITCH_BEND, 0, 0, 0x2400, 0);
    ArpTick(0); /* back to 60 */
    ArpTick(0); /* 64, frozen: manager bend still applies */
    d = sounding_dsp(64);
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | (0x2200 + 0x400)) >= 0, "manager bend adds to frozen bend");
}

static void test_arp_lock_notes(void)
{
    int d;
    reset_all();
    layer_params[0][L_ARP] = 1;
    layer_params[0][L_ARP_LOCK] = 1;
    arp_note_list[0] = 48;
    arp_note_list[1] = 55;
    arp_count[0] = 2;
    bend(5, 0x2800);
    note_on(5, 60);
    d = sounding_dsp(48);
    CHECK(arp_mpe_state.lock_src[0] == 5, "transposing key recorded");
    CHECK(d >= 0 && find_cmd((u32)d, CMD_PITCH | 0x2800) >= 0, "locked step carries the key's expression");
    ArpTick(0);
    d = sounding_dsp(55);
    CHECK(d >= 0 && S.dsp_owner[d] == 5, "every locked step follows the key");
}

static void test_arp_activation(void)
{
    reset_all();
    layer_params[0][L_ARP] = 1;
    mpe_setting_set(SET_ARP, 1);
    CHECK(!mpe_active(), "MPE Arp = Fallback: arp program uses the fallback");
    reset_all();
    layer_params[0][L_AB_MODE] = 1;
    layer_params[1][L_ARP] = 1;
    CHECK(!mpe_active(), "Stack with an arp layer: fallback");
    reset_all();
    layer_params[0][L_AB_MODE] = 2;
    layer_params[0][0x1a8] = 60;
    layer_params[1][L_ARP] = 1;
    CHECK(mpe_active(), "Split with an arp layer: MPE active");
    note_on(2, 50);
    note_on(3, 70);
    CHECK(sounding_dsp(50) >= 0 && sounding_dsp(50) < 3, "non-arp side plays in group A");
    CHECK(sounding_dsp(70) >= 3 && S.dsp_owner[sounding_dsp(70)] == 3, "arp side steps in group B");
}

static void test_arp_external_clock(void)
{
    reset_all();
    layer_params[0][L_ARP] = 1;
    note_on(2, 60);
    arp_tick_calls = 0;
    mpe_ext_arp_tick(0);
    mpe_ext_arp_tick(0);
    CHECK(arp_tick_calls == 0, "ISR ticks are deferred while MPE is active");
    mpe_main_poll();
    CHECK(arp_tick_calls == 2 && helper_calls == 1, "main loop runs pending ticks, then the stock helper");
    mpe_main_poll();
    CHECK(arp_tick_calls == 2 && helper_calls == 2, "no tick runs twice");
    mpe_setting_set(SET_ENABLED, 0);
    mpe_ext_arp_tick(1);
    CHECK(arp_tick_calls == 3, "MPE off: the ISR runs ArpTick directly");
}

/* ---- Stock parser model for Control Changes (research/mpe_manager_and_mcm.md):
 * the hooked call sites, then the stock NRPN handling. CC100/CC101 do not
 * touch the NRPN number in the stock code (DIN: except the null RPN). ---- */
volatile u16 din_cc_number, din_nrpn_number, usb_nrpn_number, usb_rx_index;
volatile u8 din_channel;
volatile u8 usb_rx_packets[4 * 4];
static u32 din_next_byte;
static u16 stock_nrpn_msb[2], stock_nrpn_lsb[2];
u32 MidiReadDataByte(void) { return din_next_byte; }

static void midi_cc(u32 t, u32 ch, u32 cc, u32 v)
{
    volatile u16 *number = t ? &usb_nrpn_number : &din_nrpn_number;
    u32 n;
    if (t == 0) {
        if (!mpe_channel_accepted(ch)) /* DinMidiProcessRxRingBuffer */
            return;
        din_channel = (u8)ch;
        din_cc_number = (u16)cc;
        din_next_byte = v;
        v = mpe_din_cc_read() & 0xff;
    } else {
        usb_rx_index = 2;
        usb_rx_packets[8] = 0x0b;
        usb_rx_packets[9] = (u8)(0xb0 | ch);
        usb_rx_packets[10] = (u8)cc;
        usb_rx_packets[11] = (u8)v;
        if (!mpe_usb_cc_accepted(ch))
            return;
    }
    switch (cc) {
    case 99:
    case 98:
        if (cc == 99)
            stock_nrpn_msb[t] = (u16)(v << 7);
        else
            stock_nrpn_lsb[t] = (u16)v;
        n = stock_nrpn_msb[t] | stock_nrpn_lsb[t];
        *number = (u16)(t == 0 && n >= 0x430 ? 0x3fff : n);
        break;
    case 6:
        if (t == 0 ? *number != 0x3fff : *number < 0x430)
            mpe_midi_submit_event(EV_NRPN, ch, *number, v << 7, 0);
        break;
    case 100:
    case 101:
        if (t == 0)
            break; /* DIN: kept inside the parser */
        /* fall through: USB submits them as ordinary controllers */
    default:
        mpe_midi_submit_event(EV_CONTROL, ch, cc, v, 0);
        break;
    }
}

static void send_mcm(u32 t, u32 ch, u32 n)
{
    midi_cc(t, ch, 101, 0);
    midi_cc(t, ch, 100, 6);
    midi_cc(t, ch, 6, n);
}

static void test_mcm(u32 t)
{
    int lc;
    reset_all();
    din_nrpn_number = usb_nrpn_number = 0x3fff;
    stock_nrpn_msb[t] = stock_nrpn_lsb[t] = 0;

    /* The stock hazard: a Data Entry after an RPN selection hit the last NRPN. */
    midi_cc(t, 0, 99, 0);
    midi_cc(t, 0, 98, 90);
    midi_cc(t, 0, 6, 1);
    CHECK(legacy_count == 1, "NRPN 90 data entry reaches the stock handler");
    lc = legacy_count;
    midi_cc(t, 0, 101, 0);
    midi_cc(t, 0, 100, 0); /* RPN 0, pitch bend sensitivity */
    midi_cc(t, 0, 6, 48);
    midi_cc(t, 3, 6, 48);
    CHECK(legacy_count == lc + (t ? 2 : 0), "Data Entry after an RPN selection never reaches an NRPN");
    CHECK(S.set[SET_ENABLED] == 1 && S.members == MEMBER_COUNT, "RPN 0 is not an MCM");
    lc = legacy_count;
    midi_cc(t, 0, 98, 90);
    midi_cc(t, 0, 6, 2);
    CHECK(legacy_count == lc + 1, "CC98 selects the NRPN again");

    send_mcm(t, 0, 5);
    CHECK(S.set[SET_ENABLED] == 1 && S.members == 5, "MCM 5: members 2-6");
    note_on(5, 60);
    CHECK(S.member_note[5] == 60 && native_alloc_count == 0, "channel 6 is a member");
    note_on(6, 62);
    CHECK(native_alloc_count == 1 && S.member_note[6] == NONE, "channel 7 is an ordinary channel");
    CHECK(mpe_channel_accepted(5) == 1 && mpe_channel_accepted(6) == 0 && mpe_channel_accepted(0) == 1,
          "channels above the zone use the stock channel filter");
    send_mcm(t, 0, 5);
    CHECK(voice_of(60) >= 0, "same MCM again: nothing released");
    send_mcm(t, 0, 3);
    CHECK(S.members == 3 && voice_of(60) < 0 && S.member_note[5] == NONE, "MCM change releases the MPE notes");
    CHECK(mpe_setting_get(SET_ENABLED) == 1, "MPE Mode setting stays On");

    send_mcm(t, 0, 0);
    CHECK(mpe_setting_get(SET_ENABLED) == 0, "MCM 0 turns MPE Mode off");
    send_mcm(t, 0, 4);
    CHECK(mpe_setting_get(SET_ENABLED) == 1 && S.members == 4, "MCM 4 turns it on with members 2-5");
    send_mcm(t, 0, 100);
    CHECK(S.members == MEMBER_COUNT, "member count above 15 is limited to 15");
    send_mcm(t, 0, 4);
    mpe_midi_submit_event(EV_NRPN, 0, NRPN_SETTINGS + SET_ENABLED, 0, 0);
    mpe_midi_submit_event(EV_NRPN, 0, NRPN_SETTINGS + SET_ENABLED, 1, 0);
    CHECK(S.members == MEMBER_COUNT, "MPE Mode switched by NRPN 433 (or the menu): full zone");

    send_mcm(t, 3, 2);
    CHECK(S.members == MEMBER_COUNT, "RPN 6 on a member channel is not an MCM");
    midi_cc(t, 0, 101, 0);
    midi_cc(t, 0, 100, 6);
    midi_cc(t, 2, 100, 0); /* another channel selects its own RPN */
    midi_cc(t, 0, 6, 7);
    CHECK(S.members == MEMBER_COUNT, "RPN selection is per channel");
    midi_cc(t, 0, 101, 0);
    midi_cc(t, 0, 100, 6);
    midi_cc(t, 0, 98, 90); /* NRPN selected after the RPN */
    lc = legacy_count;
    midi_cc(t, 0, 6, 7);
    CHECK(S.members == MEMBER_COUNT && legacy_count == lc + 1, "Data Entry after CC98 goes to the NRPN, not the MCM");
    midi_cc(t, 0, 101, 0);
    midi_cc(t, 0, 100, 6);
    midi_cc(!t, 0, 6, 7);
    CHECK(S.members == MEMBER_COUNT, "RPN state is local to the transport");

    /* The saved record does not hold the member count. */
    send_mcm(t, 0, 6);
    mpe_save_globals_page(GLOBALS_PAGE);
    CHECK(spi_flash[0x84] == NUM_SETTINGS, "record format unchanged");
    mpe_boot();
    CHECK(S.members == MEMBER_COUNT && S.set[SET_ENABLED] == 1, "after boot: full zone");
}

/* ---- Manager-channel pressure and Y (research/mpe_manager_and_mcm.md) ---- */
static u32 w_pressure(u32 u, u32 v) { return twelve() ? voice_sel(u) | CMD_VOICE_CTL | v : CMD_PRESSURE | v; }
static u32 w_y(u32 u, u32 v) { return twelve() ? voice_sel(u) | CMD_VOICE_CTL | VCTL_Y | v : CMD_CC74 | v; }
static u32 w_breath(u32 u, u32 v) { return twelve() ? voice_sel(u) | CMD_VOICE_CTL | VCTL_BREATH | v : CMD_BREATH | v; }
static int has(u32 u, u32 word) { return find_cmd(unit_dsp(u), word) >= 0; }
static void manager(u32 status, u32 d1, u32 d2) { mpe_midi_submit_event(status, 0, d1, d2, 0); }

/* voices: 0 Single, 1 Pair, 2 12 Voice (voice-bend builds only). */
static void test_manager_zone(u32 voices)
{
    u32 d, u2, d3;
    int n, lc, bc;
    reset_all();
    mpe_setting_set(SET_PAIR, voices);
    CHECK(mpe_active() && (voices == VOICES_TWELVE) == (twelve() != 0), "requested voice mode");
    note_on(2, 60);
    d = S.member_dsp[2][0];
    u2 = (d + 2) % num_units(); /* unowned unit on another DSP */
    mpe_midi_submit_event(EV_CHANNEL_PRESSURE, 2, 0, 50, 0);
    mpe_midi_submit_event(EV_CONTROL, 2, 74, 30, 0);
    lc = legacy_count;
    bc = broadcast_count;

    log_reset();
    manager(EV_CHANNEL_PRESSURE, 0, 40);
    CHECK(has(d, w_pressure(d, 90)), "manager pressure adds to the member pressure");
    CHECK(!has(d, CMD_EXPRESSION | 90) && !has(u2, CMD_EXPRESSION | 40), "pressure is not doubled to Expression");
    CHECK(has(u2, w_pressure(u2, 40)), "manager pressure alone on an unowned unit");
    CHECK(legacy_count == lc && broadcast_count == bc, "manager pressure: no legacy path, no broadcast");
    log_reset();
    manager(EV_CHANNEL_PRESSURE, 0, 100);
    CHECK(has(d, w_pressure(d, 127)), "combined pressure clamps at 127");

    log_reset();
    manager(EV_CONTROL, 74, 20);
    CHECK(has(d, w_y(d, 50)), "manager CC74 adds to the member Y (MPE Y > Timbre: Matrix+Cut)");
    CHECK(has(d, w_breath(d, 50)), "and to the member's Breath source (the Y: Timbre source)");
    CHECK(has(u2, w_y(u2, 20)) && has(u2, w_breath(u2, 20)), "manager CC74 alone on an unowned unit");
    CHECK(legacy_count == lc && broadcast_count == bc, "manager CC74: no legacy path, no broadcast");
    log_reset();
    mpe_midi_submit_event(EV_CONTROL, 2, 74, 120, 0);
    CHECK(has(d, w_y(d, 127)) && has(d, w_breath(d, 127)), "member Y combines with the held manager Y, clamped");

    mpe_setting_set(SET_Y, Y_MATRIX);
    log_reset();
    manager(EV_CONTROL, 74, 10);
    CHECK(!has(d, w_y(d, 127)) && !has(u2, w_y(u2, 10)), "Y > Timbre Matrix: no manager Y to the cutoff term");
    CHECK(has(d, w_breath(d, 127)) && has(u2, w_breath(u2, 10)), "Matrix: the Breath source still follows");
    log_reset();
    mpe_setting_set(SET_Y, 0);
    CHECK(has(u2, w_breath(u2, 0)) && has(d, w_breath(d, 120)), "Y > Timbre Off removes the manager Y from Breath");
    log_reset();
    manager(EV_CONTROL, 74, 15);
    CHECK(log_count == 0 && S.manager_cc74 == 15, "Y > Timbre Off: manager CC74 only cached");
    log_reset();
    mpe_setting_set(SET_Y, Y_MATRIX_CUT);
    CHECK(has(u2, w_y(u2, 15)) && has(d, w_y(d, 127)), "Matrix+Cut applies the cached manager Y");

    /* A stock-path note (manager channel, keyboard) takes the member's unit. */
    log_reset();
    stock_voice = (int)first_voice(d);
    mpe_allocate_voice(0, 1, 72);
    CHECK(S.dsp_owner[d] == NONE, "stock note takes the unit");
    CHECK(has(d, w_pressure(d, 100)) && has(d, w_y(d, 15)), "stock note gets the manager values without the member part");
    log_reset();
    mpe_allocate_voice(0, 1, 73);
    stock_voice = -1;
    CHECK(!has(d, w_pressure(d, 100)), "unit already clean: pressure not rewritten (local aftertouch kept)");

    log_reset();
    manager(EV_CONTROL, 121, 0);
    CHECK(S.manager_pressure == 0 && S.manager_cc74 == 0, "manager CC121 resets the manager values");
    CHECK(has(u2, w_y(u2, 0)), "and clears the manager Y on unowned units");

    /* Poly pressure on a member channel = the member's pressure. */
    note_on(3, 64);
    d3 = S.member_dsp[3][0];
    log_reset();
    mpe_midi_submit_event(EV_POLY_PRESSURE, 3, 64, 70, 0);
    CHECK(has(d3, w_pressure(d3, 70)) && S.member_pressure[3] == 70, "member poly pressure for its note");
    n = log_count;
    mpe_midi_submit_event(EV_POLY_PRESSURE, 3, 65, 99, 0);
    CHECK(log_count == n && S.member_pressure[3] == 70, "poly pressure for another note is consumed");

    manager(EV_CHANNEL_PRESSURE, 0, 5);
    log_reset();
    mpe_send_program_parameters();
    CHECK(has(d3, w_pressure(d3, 75)), "program transfer re-sends the combined pressure");
    mpe_setting_set(SET_ENABLED, 0);
    CHECK(S.manager_pressure == 0 && S.manager_cc74 == 0, "MPE Mode change resets the manager values");

    reset_all();
    mpe_setting_set(SET_PAIR, voices);
    layer_params[0][L_UNISON] = 1; /* program that cannot use MPE */
    manager(EV_CHANNEL_PRESSURE, 0, 33);
    manager(EV_CONTROL, 74, 44);
    CHECK(legacy_count == 2 && S.manager_pressure == 33 && S.manager_cc74 == 44,
          "inactive: manager values take the stock path and are remembered");
}

static void test_manager_zone_all(void)
{
    test_manager_zone(0);
    test_manager_zone(1);
#if MPE_DSP_VOICE_BEND
    test_manager_zone(VOICES_TWELVE);
#endif
}

#if MPE_PAIR_MODE
static void test_pair_mode(void)
{
    u32 ch;
    reset_all();
    for (ch = 1; ch <= 6; ch++)
        note_on(ch, 60 + ch);
    CHECK(count_active() == 12, "Pair mode: six notes use twelve voices");
    note_off(3, 63);
    CHECK(count_active() == 10, "Pair mode: release frees both voices");
    note_on(7, 70);
    CHECK(count_active() == 12, "Pair mode: freed DSP reused");
}

int main(void)
{
    test_pair_mode();
    test_manager_zone_all();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("mpe harness (pair): all checks passed\n");
    return 0;
}
#elif MPE_DSP_VOICE_BEND
/* 12 Voice (research/mpe_12_voice.md): one voice per note. */
static void test_twelve_voice(void)
{
    u32 ch, v, d;
    reset_all();
    mpe_setting_set(SET_PAIR, VOICES_TWELVE);
    CHECK(twelve() && num_units() == 12, "12 Voice active with a fixed bend range");
    for (ch = 1; ch <= 6; ch++)
        note_on(ch, 40 + ch);
    for (ch = 1; ch <= 6; ch++)
        CHECK(S.member_dsp[ch][0] % 2 == 0, "first six notes: one per DSP");
    for (ch = 7; ch <= 12; ch++)
        note_on(ch, 40 + ch);
    CHECK(count_active() == 12, "twelve notes use twelve voices");
    for (ch = 1; ch <= 12; ch++)
        CHECK(S.dsp_owner[S.member_dsp[ch][0]] == ch, "each member owns its voice");

    v = S.member_dsp[9][0];
    log_reset();
    mpe_midi_submit_event(EV_PITCH_BEND, 9, 0, 0x3000, 0);
    CHECK(find_cmd(v / 2, ((v & 1) ? SEL_VOICE_B : SEL_VOICE_A) | CMD_VOICE_BEND | 0x9800) >= 0,
          "member bend to its own voice");
    log_reset();
    mpe_midi_submit_event(EV_CHANNEL_PRESSURE, 9, 0, 90, 0);
    CHECK(find_cmd(v / 2, voice_sel(v) | CMD_VOICE_CTL | 90) == 0, "member pressure to its own voice (0x1f6)");
    log_reset();
    mpe_midi_submit_event(EV_CONTROL, 9, 74, 33, 0);
    CHECK(find_cmd(v / 2, voice_sel(v) | CMD_VOICE_CTL | VCTL_Y | 33) == 0, "member Y to its own voice (0x1f6)");
    CHECK(find_cmd(v / 2, CMD_CC74 | 33) < 0, "no per-DSP CC74 in 12 Voice");
    CHECK(find_cmd(v / 2, voice_sel(v) | CMD_VOICE_CTL | VCTL_BREATH | 33) >= 0,
          "member Y to the Breath source of its own voice (MPE Y > Timbre)");
    CHECK(find_cmd(v / 2, CMD_BREATH | 33) < 0, "no per-DSP Breath in 12 Voice");
    log_reset();
    mpe_midi_submit_event(EV_CONTROL, 9, 2, 50, 0);
    CHECK(find_cmd(v / 2, voice_sel(v) | CMD_VOICE_CTL | VCTL_BREATH | 50) == 0 &&
          find_cmd(v / 2, CMD_BREATH | 50) < 0, "member CC2 to the Breath source of its own voice");
    log_reset();
    mpe_midi_submit_event(EV_CONTROL, 9, 1, 60, 0);
    CHECK(find_cmd(v / 2, CMD_MOD_WHEEL | 60) == 0, "member CC1 stays per DSP");
    CHECK(find_cmd(v / 2, CMD_PRESSURE | 90) < 0, "no per-DSP pressure in 12 Voice");

    note_on(13, 70);
    CHECK(count_active() == 12 && S.member_dsp[1][0] == NONE, "13th note steals the oldest");
    note_off(5, 45);
    CHECK(count_active() == 11, "release frees one voice");

    mpe_setting_set(SET_BEND, 0);
    CHECK(!twelve() && count_active() == 0, "Bend Range = Program: 12 Voice off, notes released");

    reset_all();
    mpe_setting_set(SET_PAIR, VOICES_TWELVE);
    note_on(2, 60);
    d = S.member_dsp[2][0];
    log_reset();
    stock_voice = (int)d; /* stock-path note (keyboard) steals the member's voice */
    mpe_allocate_voice(0, 1, 64);
    stock_voice = -1;
    CHECK(find_cmd(d / 2, voice_sel(d) | CMD_VOICE_CTL | VCTL_Y) >= 0, "stock note in 12 Voice: neutral Y on its voice");
    CHECK(find_cmd(d / 2, voice_sel(d) | CMD_VOICE_CTL | VCTL_BREATH) >= 0,
          "stock note in 12 Voice: neutral Breath on its voice");
    CHECK(S.dsp_owner[d] == NONE, "stock note takes the voice from the member");
    y_neutral_count = 0;
    mpe_setting_set(SET_PAIR, 0);
    CHECK(y_neutral_count == 1, "leaving 12 Voice clears the per-voice Y");
}

/* Stack in 12 Voice: each note owns one voice per layer (A: voices 0-5,
 * B: voices 6-11), so six Stack notes instead of three. */
static void test_twelve_voice_stack(void)
{
    u32 ch, va, vb;
    reset_all();
    mpe_setting_set(SET_PAIR, VOICES_TWELVE);
    layer_params[0][L_AB_MODE] = 1; /* Stack */
    for (ch = 1; ch <= 6; ch++)
        note_on(ch, 50 + ch);
    CHECK(count_active() == 12, "12 Voice Stack: six notes use twelve voices");
    for (ch = 1; ch <= 6; ch++)
        CHECK(S.member_dsp[ch][0] < 6 && S.member_dsp[ch][1] >= 6 && S.member_dsp[ch][1] < 12,
              "12 Voice Stack: one voice in each layer range");
    va = S.member_dsp[4][0];
    vb = S.member_dsp[4][1];
    log_reset();
    mpe_midi_submit_event(EV_CHANNEL_PRESSURE, 4, 0, 77, 0);
    CHECK(find_cmd(va / 2, voice_sel(va) | CMD_VOICE_CTL | 77) >= 0 &&
          find_cmd(vb / 2, voice_sel(vb) | CMD_VOICE_CTL | 77) >= 0, "12 Voice Stack: pressure to both layer voices");
    log_reset();
    mpe_midi_submit_event(EV_CONTROL, 4, 74, 20, 0);
    CHECK(find_cmd(va / 2, voice_sel(va) | CMD_VOICE_CTL | VCTL_Y | 20) >= 0 &&
          find_cmd(vb / 2, voice_sel(vb) | CMD_VOICE_CTL | VCTL_Y | 20) >= 0, "12 Voice Stack: Y to both layer voices");
    note_on(7, 70);
    CHECK(count_active() == 12 && S.member_dsp[1][0] == NONE && S.member_dsp[1][1] == NONE,
          "12 Voice Stack: seventh note steals the oldest in both layers");
    note_off(3, 53);
    CHECK(count_active() == 10, "12 Voice Stack: Note Off releases both layers");
}

int main(void)
{
    test_twelve_voice();
    test_twelve_voice_stack();
    test_manager_zone_all();
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("mpe harness (12 Voice): all checks passed\n");
    return 0;
}
#else
int main(void)
{
    test_six_notes_one_per_dsp();
    test_steal_oldest();
    test_duplicate_pitch_owner_scoped();
    test_expression_routing();
    test_member_controllers();
    test_bend_range_override();
    test_settings_persistence();
    test_fallback_and_disable();
    test_stack_layered();
    test_split_layered();
    test_implicit_note_off();
    test_nrpn_settings();
    test_inactive_passthrough();
    test_wheel_as_manager();
    test_manager_notes();
    test_all_notes_off();
    test_arp_per_note();
    test_arp_release_and_remove();
    test_arp_duplicate_note();
    test_arp_hold_freeze();
    test_arp_lock_notes();
    test_arp_activation();
    test_arp_external_clock();
    test_manager_zone_all();
    test_mcm(0);
    test_mcm(1);
    if (failures) {
        fprintf(stderr, "%d check(s) failed\n", failures);
        return 1;
    }
    printf("mpe harness: all checks passed\n");
    return 0;
}
#endif
