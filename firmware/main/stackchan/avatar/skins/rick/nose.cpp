/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "rick.h"

using namespace uitk;
using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

LV_IMAGE_DECLARE(rick_nose);

static const Vector2i _nose_base_pos   = Vector2i(0, 15);
static const Vector2i _nose_min_offset = Vector2i(-12, -12);
static const Vector2i _nose_max_offset = Vector2i(12, 12);

RickNose::RickNose(lv_obj_t* parent)
{
    _container = std::make_unique<Container>(parent);
    _container->setRadius(0);
    _container->setAlign(LV_ALIGN_CENTER);
    _container->setBorderWidth(0);
    _container->setBgOpa(0);
    _container->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    _image = std::make_unique<Image>(_container->get());
    _image->setSrc(&rick_nose);
    _image->setAlign(LV_ALIGN_CENTER);
    _image->setPos(0, 0);
    _image->setPivot(rick_nose.header.w / 2, rick_nose.header.h / 2);
    _image->setScale((uint32_t)(256));

    setPosition(_position);
}

RickNose::~RickNose()
{
    _image.reset();
    _container.reset();
}

void RickNose::setPosition(const Vector2i& position)
{
    Element::setPosition(position);

    auto pos_x = _nose_base_pos.x + map_range(_position.x, -100, 100, _nose_min_offset.x, _nose_max_offset.x);
    auto pos_y = _nose_base_pos.y + map_range(_position.y, -100, 100, _nose_min_offset.y, _nose_max_offset.y);

    _container->setPos(pos_x, pos_y);
}

void RickNose::setVisible(bool visible)
{
    Element::setVisible(visible);
    _container->setHidden(!visible);
}
