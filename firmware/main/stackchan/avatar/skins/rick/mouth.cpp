/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "rick.h"

using namespace uitk;
using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

LV_IMAGE_DECLARE(rick_mouth_drooling);
LV_IMAGE_DECLARE(rick_mouth_open);

static const Vector2i _mouth_base_pos   = Vector2i(0, 65);
static const Vector2i _mouth_min_offset = Vector2i(-24, -24);
static const Vector2i _mouth_max_offset = Vector2i(24, 24);

RickMouth::RickMouth(lv_obj_t* parent)
{
    _container = std::make_unique<Container>(parent);
    _container->setRadius(0);
    _container->setAlign(LV_ALIGN_CENTER);
    _container->setBorderWidth(0);
    _container->setBgOpa(0);
    _container->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    _image = std::make_unique<Image>(_container->get());
    _image->setSrc(&rick_mouth_drooling);
    _image->setAlign(LV_ALIGN_CENTER);
    _image->setPos(0, 0);
    _image->setPivot(rick_mouth_drooling.header.w / 2, rick_mouth_drooling.header.h / 2);
    _image->setScale((uint32_t)(256));

    setPosition(_position);
    setWeight(0);
    setRotation(0);
}

RickMouth::~RickMouth()
{
    _image.reset();
    _container.reset();
}

void RickMouth::setPosition(const Vector2i& position)
{
    Element::setPosition(position);

    auto pos_x = _mouth_base_pos.x + map_range(_position.x, -100, 100, _mouth_min_offset.x, _mouth_max_offset.x);
    auto pos_y = _mouth_base_pos.y + map_range(_position.y, -100, 100, _mouth_min_offset.y, _mouth_max_offset.y);

    _container->setPos(pos_x, pos_y);
}

void RickMouth::setWeight(int weight)
{
    Feature::setWeight(weight);

    static constexpr int kOpenThreshold = 20;

    if (_weight > kOpenThreshold && !_is_open) {
        _is_open = true;
        _image->setSrc(&rick_mouth_open);
        _image->setPivot(rick_mouth_open.header.w / 2, rick_mouth_open.header.h / 2);
    } else if (_weight <= kOpenThreshold && _is_open) {
        _is_open = false;
        _image->setSrc(&rick_mouth_drooling);
        _image->setPivot(rick_mouth_drooling.header.w / 2, rick_mouth_drooling.header.h / 2);
    }

    _image->setScale((uint32_t)(256));
}

void RickMouth::setRotation(int rotation)
{
    Element::setRotation(rotation);
    _image->setRotation(rotation);
}

void RickMouth::setEmotion(const Emotion& emotion)
{
    if (getIgnoreEmotion()) {
        return;
    }

    switch (emotion) {
        case Emotion::Neutral:
            setWeight(0);
            setRotation(0);
            break;
        case Emotion::Happy:
            setWeight(15);
            setRotation(-50);
            break;
        case Emotion::Angry:
            setWeight(0);
            setRotation(50);
            break;
        case Emotion::Sad:
            setWeight(5);
            setRotation(100);
            break;
        case Emotion::Doubt:
            setWeight(0);
            setRotation(-30);
            break;
        case Emotion::Sleepy:
            setWeight(0);
            setRotation(0);
            break;
        default:
            break;
    }
}

void RickMouth::setVisible(bool visible)
{
    Element::setVisible(visible);
    _container->setHidden(!visible);
}
