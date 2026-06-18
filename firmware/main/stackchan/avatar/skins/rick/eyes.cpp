/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "rick.h"

using namespace uitk;
using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

LV_IMAGE_DECLARE(rick_eye);

static const Vector2i _eye_base_pos   = Vector2i(-40, -20);
static const Vector2i _eye_min_offset = Vector2i(-32, -24);
static const Vector2i _eye_max_offset = Vector2i(32, 24);

RickEyes::RickEyes(lv_obj_t* parent, bool isLeftEye)
{
    _is_left_eye = isLeftEye;

    _container = std::make_unique<Container>(parent);
    _container->setRadius(0);
    _container->setAlign(LV_ALIGN_CENTER);
    _container->setBorderWidth(0);
    _container->setBgOpa(0);
    _container->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    _image = std::make_unique<Image>(_container->get());
    _image->setSrc(&rick_eye);
    _image->setAlign(LV_ALIGN_CENTER);
    _image->setPos(0, 0);
    _image->setPivot(rick_eye.header.w / 2, rick_eye.header.h / 2);
    if (_is_left_eye) {
        _image->setScaleX((uint32_t)(int32_t)(-256));
    }

    setSize(0);
    setWeight(100);
    setPosition(_position);
    setRotation(0);
}

RickEyes::~RickEyes()
{
    _image.reset();
    _container.reset();
}

void RickEyes::setPosition(const Vector2i& position)
{
    Element::setPosition(position);

    auto pos_x = _is_left_eye ? _eye_base_pos.x : -_eye_base_pos.x;
    pos_x += map_range(_position.x, -100, 100, _eye_min_offset.x, _eye_max_offset.x);
    auto pos_y = _eye_base_pos.y + map_range(_position.y, -100, 100, _eye_min_offset.y, _eye_max_offset.y);

    _container->setPos(pos_x, pos_y);
}

void RickEyes::setWeight(int weight)
{
    Feature::setWeight(weight);

    float blink_scale = map_range(_weight, 0, 100, 0.0f, 1.0f);
    _image->setScaleY((uint32_t)(blink_scale * 256));
}

void RickEyes::setRotation(int rotation)
{
    Element::setRotation(rotation);
    _image->setRotation(rotation);
}

void RickEyes::setEmotion(const Emotion& emotion)
{
    if (getIgnoreEmotion()) {
        return;
    }

    setWeight(100);
    setRotation(0);
}

void RickEyes::setVisible(bool visible)
{
    Element::setVisible(visible);
    _container->setHidden(!visible);
}

void RickEyes::setSize(int size)
{
    Feature::setSize(size);

    float scale = map_range(_size, -100, 100, 0.5f, 1.5f);
    _image->setScale((uint32_t)(scale * 256));
}
