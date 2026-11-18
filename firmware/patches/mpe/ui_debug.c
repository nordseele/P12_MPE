/*
 * UI event monitor (debug build option, research/ui_and_parameter_access.md).
 *
 * Wraps the three front-panel event producers and draws the last event of
 * each kind in the top 24 rows of the frame buffer, after the stock handler
 * has drawn its own screen:
 *
 *   BTN <code> <pressed>     buttons      (FUN_9d01a24c, code, pressed)
 *   ENC <id> <delta>         encoders     (FUN_9d01a298, id, delta)
 *   POT <id> <value>         pots, sliders, wheels (FUN_9d01a2ec, id, value)
 *
 * Codes and ids are hexadecimal, values decimal. Pot events are skipped for
 * changes below 8 counts on the same id, to limit work from ADC noise.
 *
 * Every reported event is also sent as a DIN SysEx message, together with the
 * first program or global parameter that the stock handler set while
 * processing it (tools/decode_ui_log.py resolves the names):
 *
 *   F0 7D 50 31 kind seq id_lo id_hi sign m0 m1 m2 scope p_lo p_hi count F7
 *
 *   kind  1 button, 2 encoder, 3 pot
 *   seq   7-bit event counter (detects dropped messages)
 *   value sign (0/1) and 21-bit magnitude, 7 bits per byte, low first
 *   scope 0 none, 1-3 layer parameter (layer argument 0, 1, 2 plus 1),
 *         4 global parameter; p = parameter index; count = parameters set
 */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

/* Stock display primitives and state (gp = 0xa00086f0). */
extern void SetFont(const void *font);           /* 0x9d02edbc */
extern u32 DrawTextAt(u32 x, u32 y, const char *s); /* 0x9d02f2dc */
extern volatile u16 draw_colour;                 /* gp-0x7e04 = 0xa00008ec */
extern const void *volatile current_font;        /* 0xa000ded8 */
extern volatile u8 frame_buffer[64 * 256];       /* 0xa0003880 */
extern const u8 stock_font[];                    /* 0x9d07003c */

extern void DinMidiTxEnqueue(u32 byte);          /* 0x9d02b4a4 */

/* Relocated entries (ui_debug_entry.S). */
extern void orig_ui_button(u32 code, u32 pressed);
extern void orig_ui_encoder(u32 id, int delta);
extern void orig_ui_pot(u32 id, u32 value, u32 previous);
extern void orig_set_layer_parameter(u32 src, u32 layer, u32 arg, u32 index, u32 value);
extern void orig_set_global_parameter(u32 silent, u32 index, u32 value);

#define OVERLAY_ROWS 24u

struct UiDebugState {
    u32 magic;
    u8 btn_code, btn_pressed, enc_id, pot_id;
    int enc_delta;
    u16 pot_value, pad;
    u8 have_btn, have_enc, have_pot, seq;
    u8 capturing, scope, count, pad3;
    u16 param, pad4;
};
extern struct UiDebugState ui_debug_state; /* fixed RAM, mpe.ld */
#define U (ui_debug_state)
#define UI_MAGIC 0x47424455u /* "UDBG" */

static void ensure(void)
{
    if (U.magic != UI_MAGIC) {
        U.have_btn = U.have_enc = U.have_pot = 0;
        U.seq = 0;
        U.capturing = 0;
        U.magic = UI_MAGIC;
    }
}

static void capture_begin(void)
{
    ensure();
    U.scope = 0;
    U.count = 0;
    U.param = 0;
    U.capturing = 1;
}

static void capture_param(u32 scope, u32 index)
{
    if (U.magic != UI_MAGIC || !U.capturing)
        return;
    if (U.count == 0) {
        U.scope = (u8)scope;
        U.param = (u16)index;
    }
    if (U.count < 127)
        U.count++;
}

static void send_event(u32 kind, u32 id, int value)
{
    u32 mag = value < 0 ? (u32)(-value) : (u32)value;
    DinMidiTxEnqueue(0xf0);
    DinMidiTxEnqueue(0x7d);
    DinMidiTxEnqueue(0x50);
    DinMidiTxEnqueue(0x31);
    DinMidiTxEnqueue(kind);
    DinMidiTxEnqueue(U.seq & 0x7f);
    DinMidiTxEnqueue(id & 0x7f);
    DinMidiTxEnqueue((id >> 7) & 0x7f);
    DinMidiTxEnqueue(value < 0 ? 1 : 0);
    DinMidiTxEnqueue(mag & 0x7f);
    DinMidiTxEnqueue((mag >> 7) & 0x7f);
    DinMidiTxEnqueue((mag >> 14) & 0x7f);
    DinMidiTxEnqueue(U.scope & 0x7f);
    DinMidiTxEnqueue(U.param & 0x7f);
    DinMidiTxEnqueue((U.param >> 7) & 0x7f);
    DinMidiTxEnqueue(U.count & 0x7f);
    DinMidiTxEnqueue(0xf7);
    U.seq = (u8)((U.seq + 1) & 0x7f);
}

/* ---- Parameter setters: record what the stock handler changes ---- */
void ui_debug_set_layer_parameter(u32 src, u32 layer, u32 arg, u32 index, u32 value)
{
    capture_param(1 + (layer & 0xff), index & 0xffff);
    orig_set_layer_parameter(src, layer, arg, index, value);
}

void ui_debug_set_global_parameter(u32 silent, u32 index, u32 value)
{
    capture_param(4, index & 0xffff);
    orig_set_global_parameter(silent, index, value);
}

static char *put_hex2(char *p, u32 v)
{
    static const char digits[] = "0123456789ABCDEF";
    *p++ = digits[(v >> 4) & 15];
    *p++ = digits[v & 15];
    return p;
}

static char *put_dec(char *p, int v)
{
    char tmp[12];
    int n = 0;
    u32 u;
    if (v < 0) {
        *p++ = '-';
        u = (u32)(-v);
    } else {
        u = (u32)v;
    }
    do {
        tmp[n++] = (char)('0' + u % 10);
        u /= 10;
    } while (u && n < 11);
    while (n)
        *p++ = tmp[--n];
    return p;
}

static char *put_str(char *p, const char *s)
{
    while (*s)
        *p++ = *s++;
    return p;
}

static void draw_overlay(void)
{
    char line1[40], line2[24];
    char *p;
    u32 i;
    u16 saved_colour = draw_colour;
    const void *saved_font = current_font;

    p = line1;
    if (U.have_btn) {
        p = put_str(p, "BTN ");
        p = put_hex2(p, U.btn_code);
        *p++ = ' ';
        p = put_dec(p, U.btn_pressed);
        p = put_str(p, "   ");
    }
    if (U.have_enc) {
        p = put_str(p, "ENC ");
        p = put_hex2(p, U.enc_id);
        *p++ = ' ';
        p = put_dec(p, U.enc_delta);
    }
    *p = 0;
    p = line2;
    if (U.have_pot) {
        p = put_str(p, "POT ");
        p = put_hex2(p, U.pot_id);
        *p++ = ' ';
        p = put_dec(p, U.pot_value);
    }
    *p = 0;

    for (i = 0; i < OVERLAY_ROWS * 256u; i++)
        frame_buffer[i] = 0;
    SetFont(stock_font);
    draw_colour = 0xf;
    if (line1[0])
        DrawTextAt(1, 1, line1);
    if (line2[0])
        DrawTextAt(1, 12, line2);
    draw_colour = saved_colour;
    if (saved_font)
        SetFont(saved_font);
}

void ui_debug_button(u32 code, u32 pressed)
{
    capture_begin();
    orig_ui_button(code, pressed);
    U.capturing = 0;
    U.btn_code = (u8)code;
    U.btn_pressed = (u8)pressed;
    U.have_btn = 1;
    send_event(1, code & 0xff, (int)(pressed & 0xff));
    draw_overlay();
}

void ui_debug_encoder(u32 id, int delta)
{
    capture_begin();
    orig_ui_encoder(id, delta);
    U.capturing = 0;
    U.enc_id = (u8)id;
    U.enc_delta = delta;
    U.have_enc = 1;
    send_event(2, id & 0xff, delta);
    draw_overlay();
}

void ui_debug_pot(u32 id, u32 value, u32 previous)
{
    int diff;
    capture_begin();
    orig_ui_pot(id, value, previous);
    U.capturing = 0;
    id &= 0xff;
    value &= 0xffff;
    diff = (int)value - (int)U.pot_value;
    if (U.have_pot && U.pot_id == id && diff > -8 && diff < 8)
        return;
    U.pot_id = (u8)id;
    U.pot_value = (u16)value;
    U.have_pot = 1;
    send_event(3, id, (int)value);
    draw_overlay();
}
