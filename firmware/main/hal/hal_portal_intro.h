/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <lvgl.h>
#include <functional>

/**
 * @brief Show the Rick and Morty portal startup animation.
 *
 * Displays a full-screen green/teal portal animation on top of whatever is
 * already on screen.  After ~2.5 s the overlay fades out and on_complete is
 * called.
 *
 * IMPORTANT: Must be called with the LVGL lock already held (e.g. inside a
 * LvglLockGuard scope or between a Lock/Unlock pair).  All internal timers
 * and animation callbacks execute from the LVGL task and do not re-acquire
 * the lock.
 *
 * @param parent      LVGL parent object — typically lv_screen_active().
 * @param on_complete Callback fired once the fade-out completes and the panel
 *                    has been deleted.  Runs from the LVGL task.
 */
void showPortalIntro(lv_obj_t* parent, std::function<void()> on_complete);
