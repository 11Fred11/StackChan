/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "hal_status_overlay.h"
#include "hal.h"
#include <mooncake_log.h>
#include <esp_wifi.h>
#include <cstring>

static const std::string_view _tag = "StatusOverlay";

// Guard pointers for the device-status overlay; non-null while it is on-screen.
static lv_obj_t*   _status_overlay = nullptr;
static lv_timer_t* _status_timer   = nullptr;

// LVGL timer callback — called from the LVGL task, no extra lock needed.
static void _status_overlay_dismiss_cb(lv_timer_t* /*timer*/)
{
    if (_status_overlay != nullptr) {
        lv_obj_delete(_status_overlay);
        _status_overlay = nullptr;
    }
    _status_timer = nullptr;
}

void showStatusOverlay(Hal& hal)
{
    // --- Gather status data (before touching LVGL objects) ---

    wifi_ap_record_t ap_info = {};
    int rssi                 = -100;
    char ssid_str[33]        = "Not connected";
    if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
        rssi = ap_info.rssi;
        snprintf(ssid_str, sizeof(ssid_str), "%s", reinterpret_cast<const char*>(ap_info.ssid));
    }

    // Signal-strength indicator (▂▄▆█ Unicode block elements)
    const char* bars;
    if (rssi >= -50)
        bars = "\xe2\x96\x82\xe2\x96\x84\xe2\x96\x86\xe2\x96\x88";  // ▂▄▆█
    else if (rssi >= -60)
        bars = "\xe2\x96\x82\xe2\x96\x84\xe2\x96\x86";  // ▂▄▆
    else if (rssi >= -70)
        bars = "\xe2\x96\x82\xe2\x96\x84";  // ▂▄
    else if (rssi >= -80)
        bars = "\xe2\x96\x82";  // ▂
    else
        bars = "----";

    int  battery  = hal.getBatteryLevel();
    int  volume   = hal.getSpeakerVolume();
    int  bright   = hal.getBackLightBrightness();
    auto account  = hal.getUserAccountInfo();

    auto wifi_val = fmt::format("{} {}dBm {}", ssid_str, rssi, bars);
    auto batt_val = fmt::format("{}%{}", battery, hal.isBatteryCharging() ? " +" : "");
    auto vol_val  = fmt::format("{}%", volume);
    auto bri_val  = fmt::format("{}%", bright);
    auto dev_val  = account.deviceName.empty() ? std::string("StackChan") : account.deviceName;

    // --- Build LVGL overlay ---

    // Dismiss any existing overlay (and cancel its timer) first
    if (_status_timer != nullptr) {
        lv_timer_delete(_status_timer);
        _status_timer = nullptr;
    }
    if (_status_overlay != nullptr) {
        lv_obj_delete(_status_overlay);
        _status_overlay = nullptr;
    }

    const lv_color_t C_TEAL  = lv_color_hex(0x00BCD4);
    const lv_color_t C_WHITE = lv_color_hex(0xFFFFFF);
    const lv_color_t C_DIM   = lv_color_hex(0x556677);
    const lv_color_t C_BG    = lv_color_hex(0x0A1628);

    // Full-screen dark panel
    _status_overlay = lv_obj_create(lv_screen_active());
    lv_obj_set_size(_status_overlay, 320, 240);
    lv_obj_align(_status_overlay, LV_ALIGN_CENTER, 0, 0);
    lv_obj_set_style_bg_color(_status_overlay, C_BG, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(_status_overlay, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_color(_status_overlay, C_TEAL, LV_PART_MAIN);
    lv_obj_set_style_border_width(_status_overlay, 2, LV_PART_MAIN);
    lv_obj_set_style_radius(_status_overlay, 6, LV_PART_MAIN);
    lv_obj_set_style_pad_all(_status_overlay, 0, LV_PART_MAIN);
    lv_obj_remove_flag(_status_overlay, LV_OBJ_FLAG_SCROLLABLE);

    // Title
    lv_obj_t* title = lv_label_create(_status_overlay);
    lv_label_set_text(title, "[ DEVICE STATUS ]");
    lv_obj_set_style_text_color(title, C_TEAL, LV_PART_MAIN);
    lv_obj_set_style_text_font(title, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_set_width(title, 300);
    lv_obj_set_style_text_align(title, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_align(title, LV_ALIGN_TOP_MID, 0, 8);

    // Teal separator line below title
    lv_obj_t* sep = lv_obj_create(_status_overlay);
    lv_obj_set_size(sep, 296, 2);
    lv_obj_align(sep, LV_ALIGN_TOP_MID, 0, 30);
    lv_obj_set_style_bg_color(sep, C_TEAL, LV_PART_MAIN);
    lv_obj_set_style_bg_opa(sep, LV_OPA_COVER, LV_PART_MAIN);
    lv_obj_set_style_border_width(sep, 0, LV_PART_MAIN);
    lv_obj_set_style_pad_all(sep, 0, LV_PART_MAIN);
    lv_obj_remove_flag(sep, LV_OBJ_FLAG_SCROLLABLE);

    // Helper lambda: add one teal-key / white-value row
    int row_y = 38;
    static constexpr int KEY_X = 12;
    static constexpr int VAL_X = 106;
    static constexpr int KEY_W = 88;
    static constexpr int VAL_W = 202;
    static constexpr int ROW_H = 26;

    auto add_row = [&](const char* key, const std::string& val) {
        lv_obj_t* kl = lv_label_create(_status_overlay);
        lv_label_set_text(kl, key);
        lv_obj_set_style_text_color(kl, C_TEAL, LV_PART_MAIN);
        lv_obj_set_style_text_font(kl, &lv_font_montserrat_16, LV_PART_MAIN);
        lv_obj_set_width(kl, KEY_W);
        lv_obj_align(kl, LV_ALIGN_TOP_LEFT, KEY_X, row_y);

        lv_obj_t* vl = lv_label_create(_status_overlay);
        lv_label_set_text(vl, val.c_str());
        lv_obj_set_style_text_color(vl, C_WHITE, LV_PART_MAIN);
        lv_obj_set_style_text_font(vl, &lv_font_montserrat_16, LV_PART_MAIN);
        lv_obj_set_width(vl, VAL_W);
        lv_label_set_long_mode(vl, LV_LABEL_LONG_CLIP);
        lv_obj_align(vl, LV_ALIGN_TOP_LEFT, VAL_X, row_y);

        row_y += ROW_H;
    };

    add_row("WiFi:",    wifi_val);
    add_row("Battery:", batt_val);
    add_row("Volume:",  vol_val);
    add_row("Bright:",  bri_val);
    add_row("Version:", std::string(FIRMWARE_VERSION));
    add_row("Device:",  dev_val);

    // Dim footer hint
    lv_obj_t* footer = lv_label_create(_status_overlay);
    lv_label_set_text(footer, "Auto-dismiss in 10s");
    lv_obj_set_style_text_color(footer, C_DIM, LV_PART_MAIN);
    lv_obj_set_style_text_font(footer, &lv_font_montserrat_16, LV_PART_MAIN);
    lv_obj_set_width(footer, 300);
    lv_obj_set_style_text_align(footer, LV_TEXT_ALIGN_CENTER, LV_PART_MAIN);
    lv_obj_align(footer, LV_ALIGN_BOTTOM_MID, 0, -6);

    // Float above all avatar / clock layers
    lv_obj_move_foreground(_status_overlay);

    // One-shot 10-second dismiss timer
    _status_timer = lv_timer_create(_status_overlay_dismiss_cb, 10000, nullptr);
    lv_timer_set_repeat_count(_status_timer, 1);

    mclog::tagInfo(_tag, "show_status: overlay created (WiFi={} {}dBm, batt={}%)", ssid_str, rssi, battery);
}
