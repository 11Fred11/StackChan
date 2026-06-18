/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include "../../avatar/avatar.h"
#include "../../avatar/elements/feature.h"
#include <lvgl.h>
#include <smooth_lvgl.hpp>
#include <memory>

namespace stackchan::avatar {

class RickAvatar : public Avatar {
public:
    lv_color_t panelBgColor = lv_color_hex(0xD7CFC6);

    void init(lv_obj_t* parent, const lv_font_t* font = &lv_font_montserrat_16);
    uitk::lvgl_cpp::Container* getPanel() const;

    void setEmotion(const Emotion& emotion) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Container> _pannel;
};

class RickEyes : public Feature {
public:
    RickEyes(lv_obj_t* parent, bool isLeftEye);
    ~RickEyes();

    void setPosition(const uitk::Vector2i& position) override;
    void setWeight(int weight) override;
    void setRotation(int rotation) override;
    void setEmotion(const Emotion& emotion) override;
    void setVisible(bool visible) override;
    void setSize(int size) override;

private:
    bool _is_left_eye = false;
    std::unique_ptr<uitk::lvgl_cpp::Container> _container;
    std::unique_ptr<uitk::lvgl_cpp::Image> _image;
};

class RickMouth : public Feature {
public:
    RickMouth(lv_obj_t* parent);
    ~RickMouth();

    void setPosition(const uitk::Vector2i& position) override;
    void setWeight(int weight) override;
    void setRotation(int rotation) override;
    void setEmotion(const Emotion& emotion) override;
    void setVisible(bool visible) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Container> _container;
    std::unique_ptr<uitk::lvgl_cpp::Image> _image;
    bool _is_open = false;
};

class RickNose : public Feature {
public:
    RickNose(lv_obj_t* parent);
    ~RickNose();

    void setPosition(const uitk::Vector2i& position) override;
    void setVisible(bool visible) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Container> _container;
    std::unique_ptr<uitk::lvgl_cpp::Image> _image;
};

class RickUnibrow : public Feature {
public:
    RickUnibrow(lv_obj_t* parent);
    ~RickUnibrow();

    void setPosition(const uitk::Vector2i& position) override;
    void setRotation(int rotation) override;
    void setEmotion(const Emotion& emotion) override;
    void setVisible(bool visible) override;

private:
    std::unique_ptr<uitk::lvgl_cpp::Container> _container;
    std::unique_ptr<uitk::lvgl_cpp::Image> _image;
};

}  // namespace stackchan::avatar
