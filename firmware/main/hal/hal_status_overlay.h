/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once

class Hal;

/**
 * @brief Build and display the full-screen device-status LVGL overlay.
 *
 * Must be called while the LVGL lock is already held.
 * The overlay auto-dismisses after 10 seconds; calling again while one is
 * visible replaces it and resets the timer.
 */
void showStatusOverlay(Hal& hal);
