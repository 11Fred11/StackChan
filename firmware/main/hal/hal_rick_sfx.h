/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once

namespace stackchan {

/**
 * @brief Play a random Rick Sanchez SFX through the speaker.
 *
 * Picks one entry at random from the internal SFX pool using the hardware RNG
 * (esp_random) and forwards it to hal_bridge::app_play_sound.
 *
 * Extend the pool by adding new OGG/Opus files to assets/sfx/rick/ and
 * appending the corresponding OGG_RICK_* string_view to kRickSfxPool in
 * hal_rick_sfx.cpp.
 *
 * Must be called from a task that does NOT hold the LVGL lock (e.g. from
 * HeadPetModifier::_update which runs inside the stackchan update task).
 */
void playRandomRickSfx();

}  // namespace stackchan
