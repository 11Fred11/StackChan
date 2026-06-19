/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once

namespace rick_sfx {

/**
 * @brief Play a random Rick SFX (burp or "Wubba Lubba Dub Dub").
 *
 * No-op (returns false) when:
 *   - SFX is already playing (_playing guard prevents overlap / re-trigger)
 *   - SFX has been disabled via setEnabled(false) (screensaver / clock-idle gate)
 *
 * @return true  if playback was initiated
 * @return false if skipped (already playing or disabled)
 */
bool playRandom();

/**
 * @brief Returns true while an SFX is considered in-flight.
 *
 * Cleared by a one-shot LVGL timer ~2.5 s after playback starts
 * (conservative upper-bound covering all clips).
 */
bool isPlaying();

/**
 * @brief Enable or disable SFX playback.
 *
 * Call setEnabled(false) when the launcher screensaver activates;
 * setEnabled(true) when it deactivates.  The XiaoZhi standby/clock gate
 * is handled separately in head_pet.h via hal_bridge::is_xiaozhi_idle().
 */
void setEnabled(bool enabled);

/** @brief Returns the current enabled state. */
bool isEnabled();

}  // namespace rick_sfx
