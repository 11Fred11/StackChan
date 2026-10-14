/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "rick.h"

using namespace uitk;
using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

LV_IMAGE_DECLARE(rick_unibrow);

static const Vector2i _brow_base_pos   = Vector2i(0, -70);
static const Vector2i _brow_min_offset = Vector2i(-24, -12);
static const Vector2i _brow_max_offset = Vector2i(24, 12);

RickUnibrow::RickUnibrow(lv_obj_t* parent)
{
    _container = std::make_unique<Container>(parent);
    _container->setRadius(0);
    _container->setAlign(LV_ALIGN_CENTER);
    _container->setBorderWidth(0);
    _container->setBgOpa(0);
    _container->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    _image = std::make_unique<Image>(_container->get());
    _image->setSrc(&rick_unibrow);
    _image->setAlign(LV_ALIGN_CENTER);
    _image->setPos(0, 0);
    _image->setPivot(rick_unibrow.header.w / 2, rick_unibrow.header.h / 2);
    _image->setScaleX((uint32_t)(256));
    _image->setScaleY((uint32_t)(256));

    setPosition(_position);
    setRotation(0);
}

RickUnibrow::~RickUnibrow()
{
    _image.reset();
    _container.reset();
}

void RickUnibrow::setPosition(const Vector2i& position)
{
    Element::setPosition(position);

    auto pos_x = _brow_base_pos.x + map_range(_position.x, -100, 100, _brow_min_offset.x, _brow_max_offset.x);
    auto pos_y = _brow_base_pos.y + map_range(_position.y, -100, 100, _brow_min_offset.y, _brow_max_offset.y);

    _container->setPos(pos_x, pos_y);
}

void RickUnibrow::setRotation(int rotation)
{
    Element::setRotation(rotation);
    _image->setRotation(rotation);
}

void RickUnibrow::setEmotion(const Emotion& emotion)
{
    if (getIgnoreEmotion()) {
        return;
    }

    switch (emotion) {
        case Emotion::Neutral:
            setRotation(0);
            break;
        case Emotion::Happy:
            setRotation(50);
            break;
        case Emotion::Angry:
            setRotation(-600);
            break;
        case Emotion::Sad:
            setRotation(400);
            break;
        case Emotion::Doubt:
            setRotation(100);
            break;
        case Emotion::Sleepy:
            setRotation(-200);
            break;
        case Emotion::Burp:
            setRotation(0);
            break;
        default:
            break;
    }
}

void RickUnibrow::setVisible(bool visible)
{
    Element::setVisible(visible);
    _container->setHidden(!visible);
}
