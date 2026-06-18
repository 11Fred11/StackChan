/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */

/*
 * ============================================================
 *  HOW TO USE YOUR OWN PORTAL PNG
 * ============================================================
 *
 * Step 1 — Convert the PNG to a C array
 *   Option A (online, easiest):
 *     1. Open https://lvgl.io/tools/imageconverter
 *     2. Upload your portal PNG.
 *     3. Choose:
 *          Color format  → CF_TRUE_COLOR_ALPHA  (gives RGB565 + alpha)
 *          Output format → C array
 *     4. Click "Convert" and download the .c file.
 *
 *   Option B (CLI):
 *     pip install lv_img_conv
 *     lv_img_conv portal.png --cf=CF_TRUE_COLOR_ALPHA --ofmt=C -o portal.c
 *
 * Step 2 — Replace the placeholder
 *   Copy the generated .c file over:
 *     firmware/main/assets/images/portal/portal.c
 *
 * Step 3 — Sync the variable names
 *   The descriptor at the end of the generated file must be called
 *   `portal_img` to match the declaration in portal.h, OR update portal.h
 *   to use whatever name the converter produced and rebuild.
 *
 * Step 4 — Rebuild
 *   idf.py build
 * ============================================================
 */

#include "hal_portal_intro.h"
#include <assets/images/portal/portal.h>
#include <lvgl.h>
#include <esp_log.h>
#include <functional>

static const char* TAG = "PortalIntro";

/* ========================================================================= */
/*  Internal state                                                            */
/* ========================================================================= */

/**
 * Heap-allocated per-animation state.  A pointer to this struct is threaded
 * through LVGL timer and animation user-data fields; it is freed by
 * on_fade_complete once the dismissal animation finishes.
 */
struct PortalState {
    lv_obj_t*             panel;        /**< The full-screen overlay panel    */
    std::function<void()> on_complete;  /**< User callback fired at the end   */
};

/* ========================================================================= */
/*  Animation exec callbacks (all run from the LVGL task)                    */
/* ========================================================================= */

/** Scale the portal image from 0 → 256 (256 = 100 %). */
static void img_scale_anim_cb(void* obj, int32_t val)
{
    lv_image_set_scale(static_cast<lv_obj_t*>(obj), static_cast<uint32_t>(val));
}

/** Fade the whole overlay panel in or out. */
static void panel_opa_anim_cb(void* obj, int32_t val)
{
    lv_obj_set_style_opa(static_cast<lv_obj_t*>(obj), static_cast<lv_opa_t>(val), 0);
}

/**
 * Sweep the arc indicator around the circle.
 * val is driven 0 → 360 (or 540 → 180) by the animation engine.
 * Using modulo keeps both directions seamless across the 0/360 boundary.
 */
static void arc_sweep_anim_cb(void* obj, int32_t val)
{
    auto* arc = static_cast<lv_obj_t*>(obj);
    lv_arc_set_start_angle(arc, static_cast<uint16_t>(val % 360));
    lv_arc_set_end_angle(arc,   static_cast<uint16_t>((val + 110) % 360));
}

/* ========================================================================= */
/*  Completion / cleanup                                                      */
/* ========================================================================= */

/** Called by LVGL once the fade-out animation finishes. */
static void on_fade_complete(lv_anim_t* anim)
{
    auto* state = static_cast<PortalState*>(lv_anim_get_user_data(anim));
    if (!state) {
        return;
    }
    ESP_LOGI(TAG, "portal intro complete — handing off to caller");
    state->on_complete();
    lv_obj_delete(state->panel);   /* also deletes all children + their anims */
    delete state;
}

/**
 * One-shot LVGL timer that fires ~2 500 ms after the intro starts.
 * Starts the 400 ms fade-out and then auto-deletes (repeat_count = 1).
 */
static void on_dismiss_timer(lv_timer_t* timer)
{
    auto* state = static_cast<PortalState*>(lv_timer_get_user_data(timer));

    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, state->panel);
    lv_anim_set_exec_cb(&a, panel_opa_anim_cb);
    lv_anim_set_values(&a, LV_OPA_COVER, LV_OPA_TRANSP);
    lv_anim_set_duration(&a, 400);
    lv_anim_set_user_data(&a, state);
    lv_anim_set_completed_cb(&a, on_fade_complete);
    lv_anim_start(&a);
}

/* ========================================================================= */
/*  Public API                                                                */
/* ========================================================================= */

void showPortalIntro(lv_obj_t* parent, std::function<void()> on_complete)
{
    ESP_LOGI(TAG, "starting portal intro");

    auto* state        = new PortalState();
    state->on_complete = std::move(on_complete);

    /* ------------------------------------------------------------------ */
    /* Full-screen black overlay panel                                      */
    /* ------------------------------------------------------------------ */
    state->panel = lv_obj_create(parent);
    lv_obj_set_size(state->panel, LV_HOR_RES, LV_VER_RES);
    lv_obj_align(state->panel, LV_ALIGN_CENTER, 0, 0);
    lv_obj_set_style_bg_color(state->panel, lv_color_hex(0x000000), 0);
    lv_obj_set_style_bg_opa(state->panel, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(state->panel, 0, 0);
    lv_obj_set_style_pad_all(state->panel, 0, 0);
    lv_obj_remove_flag(state->panel, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_move_foreground(state->panel);

    /* ------------------------------------------------------------------ */
    /* Portal image — scales in from 0 → 100 % over 600 ms with overshoot */
    /* ------------------------------------------------------------------ */
    lv_obj_t* img = lv_image_create(state->panel);
    lv_image_set_src(img, &portal_img);
    lv_obj_align(img, LV_ALIGN_CENTER, 0, 0);
    lv_image_set_scale(img, 0);

    {
        lv_anim_t a;
        lv_anim_init(&a);
        lv_anim_set_var(&a, img);
        lv_anim_set_exec_cb(&a, img_scale_anim_cb);
        lv_anim_set_values(&a, 0, 256);
        lv_anim_set_duration(&a, 600);
        lv_anim_set_path_cb(&a, lv_anim_path_overshoot);
        lv_anim_start(&a);
    }

    /* ------------------------------------------------------------------ */
    /* Outer glow arc  — 110° span, clockwise, 1.8 s / revolution          */
    /* ------------------------------------------------------------------ */
    lv_obj_t* arc1 = lv_arc_create(state->panel);
    lv_obj_set_size(arc1, 180, 180);
    lv_obj_align(arc1, LV_ALIGN_CENTER, 0, 0);
    lv_arc_set_bg_start_angle(arc1, 0);
    lv_arc_set_bg_end_angle(arc1, 360);
    lv_arc_set_start_angle(arc1, 0);
    lv_arc_set_end_angle(arc1, 110);

    /* Indicator style — bright portal green */
    lv_obj_set_style_arc_color(arc1, lv_color_hex(0x1AFF8C), LV_PART_INDICATOR);
    lv_obj_set_style_arc_width(arc1, 8,          LV_PART_INDICATOR);
    lv_obj_set_style_arc_opa(arc1,  LV_OPA_COVER, LV_PART_INDICATOR);

    /* Hide the background track */
    lv_obj_set_style_arc_opa(arc1, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc1, 0,          LV_PART_MAIN);

    /* Hide the knob */
    lv_obj_set_style_opa(arc1, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_bg_opa(arc1, LV_OPA_TRANSP, 0);

    {
        lv_anim_t a;
        lv_anim_init(&a);
        lv_anim_set_var(&a, arc1);
        lv_anim_set_exec_cb(&a, arc_sweep_anim_cb);
        lv_anim_set_values(&a, 0, 360);         /* clockwise, 0 → 360°   */
        lv_anim_set_duration(&a, 1800);
        lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
        lv_anim_start(&a);
    }

    /* ------------------------------------------------------------------ */
    /* Inner glow arc — 110° span, counter-clockwise feel, 1.2 s / rev    */
    /* ------------------------------------------------------------------ */
    lv_obj_t* arc2 = lv_arc_create(state->panel);
    lv_obj_set_size(arc2, 140, 140);
    lv_obj_align(arc2, LV_ALIGN_CENTER, 0, 0);
    lv_arc_set_bg_start_angle(arc2, 0);
    lv_arc_set_bg_end_angle(arc2, 360);
    lv_arc_set_start_angle(arc2, 180);
    lv_arc_set_end_angle(arc2, 290);

    /* Indicator style — cyan-teal */
    lv_obj_set_style_arc_color(arc2, lv_color_hex(0x00E5CC), LV_PART_INDICATOR);
    lv_obj_set_style_arc_width(arc2, 6,          LV_PART_INDICATOR);
    lv_obj_set_style_arc_opa(arc2,  LV_OPA_COVER, LV_PART_INDICATOR);

    /* Hide background track */
    lv_obj_set_style_arc_opa(arc2, LV_OPA_TRANSP, LV_PART_MAIN);
    lv_obj_set_style_arc_width(arc2, 0,          LV_PART_MAIN);

    /* Hide knob */
    lv_obj_set_style_opa(arc2, LV_OPA_TRANSP, LV_PART_KNOB);
    lv_obj_set_style_bg_opa(arc2, LV_OPA_TRANSP, 0);

    {
        lv_anim_t a;
        lv_anim_init(&a);
        lv_anim_set_var(&a, arc2);
        lv_anim_set_exec_cb(&a, arc_sweep_anim_cb);
        /*
         * Animate from 540 → 180 so the arc traverses its range in the
         * opposite rotational direction to arc1 (540%360=180 → 180%360=180,
         * but the intermediate values sweep counter-clockwise).
         */
        lv_anim_set_values(&a, 540, 180);
        lv_anim_set_duration(&a, 1200);
        lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
        lv_anim_start(&a);
    }

    /* ------------------------------------------------------------------ */
    /* Dismiss timer — fires after 2 500 ms, then starts 400 ms fade-out   */
    /* ------------------------------------------------------------------ */
    lv_timer_t* dismiss_timer = lv_timer_create(on_dismiss_timer, 2500, state);
    lv_timer_set_repeat_count(dismiss_timer, 1);  /* one-shot, auto-deleted */
}
