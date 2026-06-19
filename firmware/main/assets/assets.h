/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include <lvgl.h>
#include <string_view>

LV_FONT_DECLARE(MontserratSemiBold26);

extern const char ogg_camera_shutter_start[] asm("_binary_camera_shutter_ogg_start");
extern const char ogg_camera_shutter_end[] asm("_binary_camera_shutter_ogg_end");
static const std::string_view OGG_CAMERA_SHUTTER{
    static_cast<const char*>(ogg_camera_shutter_start),
    static_cast<size_t>(ogg_camera_shutter_end - ogg_camera_shutter_start)};

extern const char ogg_new_notification_start[] asm("_binary_new_notification_ogg_start");
extern const char ogg_new_notification_end[] asm("_binary_new_notification_ogg_end");
static const std::string_view OGG_NEW_NOTIFICATION{
    static_cast<const char*>(ogg_new_notification_start),
    static_cast<size_t>(ogg_new_notification_end - ogg_new_notification_start)};

// ---- Rick Sanchez SFX (assets/sfx/rick/) ----
// Replace placeholder stubs with real OGG/Opus files (ffmpeg: -c:a libopus -b:a 24k -ar 16000)
extern const char ogg_burp_start[] asm("_binary_burp_ogg_start");
extern const char ogg_burp_end[] asm("_binary_burp_ogg_end");
static const std::string_view OGG_RICK_BURP{
    static_cast<const char*>(ogg_burp_start),
    static_cast<size_t>(ogg_burp_end - ogg_burp_start)};

extern const char ogg_wubba_lubba_start[] asm("_binary_wubba_lubba_ogg_start");
extern const char ogg_wubba_lubba_end[] asm("_binary_wubba_lubba_ogg_end");
static const std::string_view OGG_RICK_WUBBA_LUBBA{
    static_cast<const char*>(ogg_wubba_lubba_start),
    static_cast<size_t>(ogg_wubba_lubba_end - ogg_wubba_lubba_start)};

namespace assets {

lv_image_dsc_t get_image(std::string_view name);

}  // namespace assets
