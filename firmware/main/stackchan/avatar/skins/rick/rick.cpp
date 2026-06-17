/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "rick.h"

using namespace uitk::lvgl_cpp;
using namespace stackchan::avatar;

void RickAvatar::init(lv_obj_t* parent, const lv_font_t* font)
{
    _pannel = std::make_unique<Container>(parent);
    _pannel->align(LV_ALIGN_CENTER, 0, 0);
    _pannel->setSize(320, 240);
    _pannel->setRadius(0);
    _pannel->setBorderWidth(0);
    _pannel->setBgColor(panelBgColor);
    _pannel->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

    _key_elements.leftEye  = std::make_unique<RickEyes>(_pannel->get(), true);
    _key_elements.rightEye = std::make_unique<RickEyes>(_pannel->get(), false);
    _key_elements.mouth    = std::make_unique<RickMouth>(_pannel->get());
    _key_elements.nose     = std::make_unique<RickNose>(_pannel->get());
    _key_elements.unibrow  = std::make_unique<RickUnibrow>(_pannel->get());
}

Container* RickAvatar::getPanel() const
{
    if (_pannel) {
        return _pannel.get();
    }
    return NULL;
}

void RickAvatar::setEmotion(const Emotion& emotion)
{
    Avatar::setEmotion(emotion);
}
