/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "hal_rick_sfx.h"
#include <hal/board/hal_bridge.h>
#include <lvgl.h>
#include <mooncake_log.h>
#include <cstdlib>

static const std::string_view _tag = "RickSFX";

/* -------------------------------------------------------------------------- */
/*  Embedded OGG data — provided by IDF EMBED_FILES (CMakeLists adds         */
/*  assets/sfx/rick/*.ogg to COMMON_SOUNDS).                                  */
/* -------------------------------------------------------------------------- */
extern const uint8_t _sfx_burp_start[]  asm("_binary_assets_sfx_rick_burp_ogg_start");
extern const uint8_t _sfx_burp_end[]    asm("_binary_assets_sfx_rick_burp_ogg_end");

extern const uint8_t _sfx_wubba_start[] asm("_binary_assets_sfx_rick_wubba_lubba_ogg_start");
extern const uint8_t _sfx_wubba_end[]   asm("_binary_assets_sfx_rick_wubba_lubba_ogg_end");

/* These string_views point directly at flash/DRAM — zero-copy playback. */
static const std::string_view _sfx_burp{
    reinterpret_cast<const char*>(_sfx_burp_start),
    static_cast<std::size_t>(_sfx_burp_end - _sfx_burp_start)
};
static const std::string_view _sfx_wubba{
    reinterpret_cast<const char*>(_sfx_wubba_start),
    static_cast<std::size_t>(_sfx_wubba_end - _sfx_wubba_start)
};

/* -------------------------------------------------------------------------- */
/*  State                                                                     */
/* -------------------------------------------------------------------------- */

/* How long to hold the "playing" lock after triggering a clip.
 * Conservative upper-bound: burp ≈ 1.0 s, wubba_lubba ≈ 1.7 s → 2.5 s.    */
static constexpr uint32_t _SFX_BLOCK_MS = 2500;

static volatile bool _playing = false;
static volatile bool _enabled = true;
static lv_timer_t*  _clear_timer = nullptr;

/* -------------------------------------------------------------------------- */
/*  Timer callback — runs in the LVGL task, no extra lock needed              */
/* -------------------------------------------------------------------------- */
static void on_sfx_done(lv_timer_t* /*t*/)
{
    _playing    = false;
    _clear_timer = nullptr;
    mclog::tagInfo(_tag, "SFX block cleared");
}

/* -------------------------------------------------------------------------- */
/*  Public API                                                                */
/* -------------------------------------------------------------------------- */
namespace rick_sfx {

bool isPlaying()
{
    return _playing;
}

void setEnabled(bool enabled)
{
    _enabled = enabled;
}

bool isEnabled()
{
    return _enabled;
}

bool playRandom()
{
    if (_playing || !_enabled) {
        return false;
    }

    /* Cancel any leftover timer from a previous (very quick) clip. */
    if (_clear_timer != nullptr) {
        lv_timer_delete(_clear_timer);
        _clear_timer = nullptr;
    }

    /* Pick a clip at random; simple alternating counter avoids stdlib rand(). */
    static uint8_t _pick = 0;
    _pick ^= 1u;
    const std::string_view& sound = (_pick == 0) ? _sfx_burp : _sfx_wubba;

    /* Set the guard BEFORE firing audio so concurrent callers are blocked. */
    _playing = true;

    /* One-shot LVGL timer clears the flag after the audio should have ended.
     * Called from within the LVGL task (head-pet modifier update), so
     * lv_timer_create is safe here.                                          */
    _clear_timer = lv_timer_create(on_sfx_done, _SFX_BLOCK_MS, nullptr);
    lv_timer_set_repeat_count(_clear_timer, 1);

    mclog::tagInfo(_tag, "playRandom: {} bytes, clip {}", sound.size(), (int)_pick);

    /* PlaySound is non-blocking — it queues the audio on the XiaoZhi audio
     * service and returns immediately.  Calling it while holding the LVGL
     * lock is safe because it routes through the XiaoZhi task queue and does
     * not itself try to acquire the LVGL lock.                               */
    hal_bridge::app_play_sound(sound);

    return true;
}

}  // namespace rick_sfx
