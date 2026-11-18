/* Copyright (c) 2026 nordseele. GPL-3.0-or-later with an additional permission: see LICENSE and LICENSE-EXCEPTION.md. */
/*
 * MPE settings rows appended to the stock Global menu (test image 3).
 *
 * research/mpe_menu.md describes the stock menu and the patch:
 *   - the row-name and row-map tables are relocated to the copies below,
 *     which add MPE_ROWS rows after the 34 stock rows;
 *   - the cursor limit in GlobalMenuStateHandler is raised to the last row;
 *   - new rows map to global index 0x26, the stock "action row" marker, so
 *     the stock code never edits or formats a value for them;
 *   - the entry hooks below edit the MPE values with soft encoders 2 and 3,
 *     like stock value rows, and draw their values and header label.
 * Settings are saved with the Global settings (mpe.c).
 */

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;

#include "stock_menu_tables.h"

#define FIRST_MPE_ROW STOCK_GLOBAL_ROWS /* 0x22 */
#define MPE_ROWS 8u
#define ACTION_ROW_MARKER 0x26u
#define BOX_WIDTH 0x3f
#define VALUE_BOX_X (2 * BOX_WIDTH + 1) /* FUN_9d02a764(2) */
#define ROW_Y0 0x18
#define ROW_PITCH 10
#define PAGE_GLOBAL 0x27u
#define EVENT_DRAW 0x0c

#define MENU_ROWS (FIRST_MPE_ROW + MPE_ROWS)

/* Stock firmware (research/mpe_menu.md, ui_and_parameter_access.md). */
extern u32 GetMenuCursor(void);                         /* 0x9d019fb0 */
extern u32 GetMenuTopRow(void);                         /* 0x9d019fe0 */
extern void PostPageEvent(void *object, u32 page, u32 arg); /* 0x9d02128c */
extern void SetFont(const void *font);
extern u32 DrawTextAt(u32 x, u32 y, const char *s);
extern void DrawTextInBox(u32 box, u32 align, const char *s); /* 0x9d02ab70 */
extern u32 TextWidth(const char *s, const void *font); /* 0x9d02f3e8 */
extern volatile u16 draw_colour;
extern volatile u8 frame_buffer[64 * 256];
extern const u8 stock_font[];

extern u32 orig_global_menu_handler(void *state, void *event);
extern u32 orig_global_menu_display(void *object, void *event);

/* mpe.c */
extern u32 mpe_setting_get(u32 i);
extern u32 mpe_setting_max(u32 i);
extern void mpe_setting_set(u32 i, u32 value);


static const char row_name_0[] = "MPE Mode";
static const char row_name_1[] = "MPE Voices";
static const char row_name_2[] = "MPE Bend Range";
static const char row_name_3[] = "MPE Y > Timbre";
static const char row_name_4[] = "MPE Pitch Wheel";
static const char row_name_5[] = "MPE Fallback";
static const char row_name_6[] = "MPE Stack/Split";
static const char row_name_7[] = "MPE Arp";


/* Relocated tables, read directly by the stock menu code. */
const char *const mpe_global_names[MENU_ROWS] = {
    STOCK_GLOBAL_NAME_POINTERS,
    row_name_0, row_name_1, row_name_2, row_name_3,
    row_name_4, row_name_5, row_name_6, row_name_7,
};

const u8 mpe_global_map[MENU_ROWS] = {
    STOCK_GLOBAL_ROW_MAP,
    ACTION_ROW_MARKER, ACTION_ROW_MARKER, ACTION_ROW_MARKER, ACTION_ROW_MARKER,
    ACTION_ROW_MARKER, ACTION_ROW_MARKER, ACTION_ROW_MARKER, ACTION_ROW_MARKER,
};

static const char *const header_label[MPE_ROWS] = {
    "MPE", "Voices", "Bend", "Timbre", "Wheel", "Fallback", "Layers", "Arp",
};

static char *put_str(char *p, const char *s)
{
    while (*s)
        *p++ = *s++;
    return p;
}

static void format_value(u32 setting, char *out)
{
    u32 v = mpe_setting_get(setting);
    char *p = out;
    switch (setting) {
    case 1:
        p = put_str(p, v == 2 ? "12 Voices" : v ? "Pair" : "Single");
        break;
    case 2:
        if (v == 0) {
            p = put_str(p, "Program");
        } else {
            if (v >= 10)
                *p++ = (char)('0' + v / 10);
            *p++ = (char)('0' + v % 10);
        }
        break;
    case 3:
        p = put_str(p, v == 2 ? "Matrix+Cut" : v ? "Matrix" : "Off");
        break;
    case 4:
        p = put_str(p, v ? "Off" : "Bend All");
        break;
    case 5:
        p = put_str(p, v ? "Silent" : "Play");
        break;
    case 6:
        p = put_str(p, v ? "MPE" : "Fallback");
        break;
    case 7:
        p = put_str(p, v ? "Fallback" : "Per-note");
        break;
    default:
        p = put_str(p, v ? "On" : "Off");
        break;
    }
    *p = 0;
}

static int mpe_row(u32 row, u32 *setting)
{
    if (row < FIRST_MPE_ROW || row >= FIRST_MPE_ROW + MPE_ROWS)
        return 0;
    *setting = row - FIRST_MPE_ROW;
    return 1;
}

/* ---- Hook: GlobalMenuStateHandler (0x9d00a4e8) ---- */
u32 mpe_global_menu_handler(void *state, void *event)
{
    u16 type = *(volatile u16 *)event;
    u8 control = *((volatile u8 *)event + 4);
    u32 cursor = *(volatile u16 *)((u8 *)state + 0x6a);
    u32 setting, value, max;
    int delta;

    if (type == 5 && (control == 0x0d || control == 0x0e) && mpe_row(cursor, &setting)) {
        delta = *(volatile int *)((u8 *)event + 8);
        value = mpe_setting_get(setting);
        max = mpe_setting_max(setting);
        if (delta < 0 && value > 0)
            mpe_setting_set(setting, value - 1);
        else if (delta > 0 && value < max)
            mpe_setting_set(setting, value + 1);
        PostPageEvent((u8 *)state + 0x140, PAGE_GLOBAL, 0); /* redraw, as stock */
        return 0;
    }
    return orig_global_menu_handler(state, event);
}

static void draw_values(void)
{
    char text[12];
    u32 top = GetMenuTopRow() & 0xffff;
    u32 i, setting, width;
    for (i = 0; i < 3; i++) {
        if (!mpe_row(top + i, &setting))
            continue;
        format_value(setting, text);
        SetFont(stock_font);
        width = TextWidth(text, stock_font) & 0xff;
        draw_colour = 0xf;
        DrawTextAt((u32)((BOX_WIDTH - (int)width) / 2 + VALUE_BOX_X), ROW_Y0 + ROW_PITCH * i, text);
    }
}


static void draw_header(void)
{
    u32 setting, x, y;
    u8 background;
    if (!mpe_row(GetMenuCursor() & 0xffff, &setting))
        return;
    /* Replace the stock label ("Pitch", from global metadata 0x26) in header
     * box 2: paint the box with its own background, then draw ours. */
    background = frame_buffer[5 * 256 + VALUE_BOX_X];
    /* Rows 1-8: hardware showed that rows 9-10 lie below the header bar. */
    for (y = 1; y <= 8; y++)
        for (x = VALUE_BOX_X; x < VALUE_BOX_X + BOX_WIDTH - 1; x++)
            frame_buffer[y * 256 + x] = background;
    draw_colour = 0;
    DrawTextInBox(2, 1, header_label[setting]);
    draw_colour = 0xf;
}

/* ---- Hook: Global menu page drawing, FUN_9d028a5c ----
 * The stock routine draws one step per call (0 clear, 1 frame, 2 header,
 * 3 rows) and clears that step's pending flag at object+0xfa+2*step. The
 * step is selected inside the call, so the wrapper compares the flags of
 * steps 2 and 3 before and after the call. */
u32 mpe_global_menu_display(void *object, void *event)
{
    volatile u16 *pending = (volatile u16 *)((u8 *)object + 0xfa);
    u16 header_before, rows_before;
    u32 result, step;
    header_before = pending[2];
    rows_before = pending[3];
    result = orig_global_menu_display(object, event);
    if (*(volatile short *)event != EVENT_DRAW)
        return result;
    step = *(volatile u16 *)((u8 *)object + 10);
    if (step == 3 && rows_before && !pending[3])
        draw_values();
    else if (step == 2 && header_before && !pending[2])
        draw_header();
    return result;
}
