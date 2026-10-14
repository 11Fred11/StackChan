"""Gateway-side tools (weather, gestures) and the intent router that makes tool use reliable.

Small, fast models in character often *talk about* doing something instead of calling the
tool ("Fine, let's see what's over there"). When the user's words clearly ask for an
action, the router tells the brain it must call one of the matching tools on its first step.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable

import httpx

log = logging.getLogger(__name__)

# user intent -> tools (llm-safe names) the brain must choose from
INTENTS: list[tuple[re.Pattern, list[str]]] = [
    # checked in order: settings before movement, so "turn the volume down" isn't a head turn
    (re.compile(r"\b(volume|louder|quieter|too loud|speak up)\b", re.I),
     ["self_audio_speaker_set_volume"]),
    (re.compile(r"\b(brightness|brighter|dimmer|screen (on|off|dark))\b", re.I),
     ["self_screen_set_brightness"]),
    (re.compile(r"\b(led|lights?|glow)\b.*\b(red|green|blue|white|off|colou?r)\b", re.I),
     ["self_robot_set_led_color"]),
    (re.compile(r"\b(device|system)? ?status\b|\bbattery\b|\bwi-?fi\b|\bdiagnostic", re.I),
     ["self_device_show_status", "self_get_device_status"]),
    (re.compile(r"\b(photo|picture|camera|what do you see|can you see|look at (me|this))\b", re.I),
     ["self_camera_take_photo"]),
    (re.compile(r"\b(nod|shake (your|ur) head|look around|wiggle|dance)\b", re.I),
     ["gesture"]),
    (re.compile(r"\b(weather|temperature|rain(ing|y)?|snow|sunny|forecast|cold|hot) (today|outside|tomorrow)?|\bweather\b", re.I),
     ["get_weather"]),
    (re.compile(r"\b(move|turn|look|rotate|tilt|swivel|spin)\b.*\b(head|left|right|up|down|me|around|motors?)\b|\bmotors?\b", re.I),
     ["self_robot_set_head_angles", "gesture"]),
]


def required_tools(text: str, available: set[str]) -> list[str] | None:
    """Tools the brain must pick from for this request, or None to let it decide."""
    for pattern, tools in INTENTS:
        if pattern.search(text):
            usable = [t for t in tools if t in available]
            if usable:
                return usable
    return None


WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Current weather and today's/tomorrow's forecast. Use for any question about weather, temperature or rain.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name; leave empty for the user's home city."}},
        },
    },
}

GESTURE_TOOL = {
    "type": "function",
    "function": {
        "name": "gesture",
        "description": "Move your head expressively: nod (yes), shake (no), look_around, or center.",
        "parameters": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["nod", "shake", "look_around", "center"]}},
            "required": ["kind"],
        },
    },
}

LOCAL_TOOLS = [WEATHER_TOOL, GESTURE_TOOL]

WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
    66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
    80: "rain showers", 81: "rain showers", 82: "violent rain showers", 85: "snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
}


async def get_weather(city: str, home: dict) -> str:
    """Open-Meteo: free, no API key."""
    async with httpx.AsyncClient(timeout=10) as client:
        if city:
            geo = (await client.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1})).json()
            if not geo.get("results"):
                return f"Couldn't find a place called {city}."
            place = geo["results"][0]
            lat, lon, name = place["latitude"], place["longitude"], f"{place['name']}, {place.get('country', '')}"
        elif home.get("latitude") is not None:
            lat, lon, name = home["latitude"], home["longitude"], home.get("city", "home")
        else:
            return "No home city is configured. Ask the user which city they mean."
        data = (await client.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code",
        })).json()
    cur, day = data["current"], data["daily"]
    return (
        f"{name}: now {cur['temperature_2m']:.0f}°C (feels {cur['apparent_temperature']:.0f}°C), "
        f"{WMO.get(cur['weather_code'], 'unknown')}, wind {cur['wind_speed_10m']:.0f} km/h. "
        f"Today {day['temperature_2m_min'][0]:.0f}–{day['temperature_2m_max'][0]:.0f}°C, "
        f"{day['precipitation_probability_max'][0]}% chance of rain, {WMO.get(day['weather_code'][0], '')}. "
        f"Tomorrow {day['temperature_2m_min'][1]:.0f}–{day['temperature_2m_max'][1]:.0f}°C, "
        f"{day['precipitation_probability_max'][1]}% chance of rain, {WMO.get(day['weather_code'][1], '')}."
    )


GESTURES = {
    "nod": [(0, 30), (0, 5), (0, 30), (0, 15)],
    "shake": [(-25, 15), (25, 15), (-25, 15), (0, 15)],
    "look_around": [(-50, 20), (50, 20), (0, 15)],
    "center": [(0, 15)],
}


async def gesture(kind: str, move: Callable[[int, int], Awaitable[str]]) -> str:
    steps = GESTURES.get(kind)
    if not steps:
        return f"Unknown gesture {kind}"
    for yaw, pitch in steps:
        await move(yaw, pitch)
        await asyncio.sleep(0.35)
    return f"done: {kind}"
