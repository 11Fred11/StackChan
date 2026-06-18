/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "hal_rick_sfx.h"
#include <assets/assets.h>
#include <hal/board/hal_bridge.h>
#include <esp_random.h>
#include <array>
#include <string_view>

namespace stackchan {

// ---------------------------------------------------------------------------
// SFX pool — add entries here to extend the pool.
// Each entry must correspond to an OGG/Opus file in assets/sfx/rick/.
// Encode with: ffmpeg -i input.mp3 -c:a libopus -b:a 24k -ar 16000 output.ogg
// ---------------------------------------------------------------------------
static const std::array<std::string_view, 2> kRickSfxPool = {
    OGG_RICK_BURP,          // assets/sfx/rick/burp.ogg
    OGG_RICK_WUBBA_LUBBA,   // assets/sfx/rick/wubba_lubba.ogg
};

void playRandomRickSfx()
{
    if (kRickSfxPool.empty()) {
        return;
    }
    const uint32_t idx = esp_random() % static_cast<uint32_t>(kRickSfxPool.size());
    hal_bridge::app_play_sound(kRickSfxPool[idx]);
}

}  // namespace stackchan
