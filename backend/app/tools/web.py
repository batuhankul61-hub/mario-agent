"""Web/API commands. Weather comes from Open-Meteo, which needs no key.
General web questions go to Gemini's built-in Google Search tool instead."""

import datetime as dt

import httpx

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# WMO weather codes, grouped the way people say them.
WEATHER = {
    0: "açık", 1: "çoğunlukla açık", 2: "parçalı bulutlu", 3: "kapalı", 45: "sisli", 48: "kırağılı sis",
    51: "hafif çiseleme", 53: "çiseleme", 55: "yoğun çiseleme", 61: "hafif yağmurlu", 63: "yağmurlu",
    65: "şiddetli yağmurlu", 66: "dondurucu yağmur", 67: "dondurucu yağmur", 71: "hafif karlı", 73: "karlı",
    75: "yoğun karlı", 77: "kar taneli", 80: "sağanak", 81: "sağanak", 82: "şiddetli sağanak",
    85: "kar sağanağı", 86: "yoğun kar sağanağı", 95: "gök gürültülü fırtına", 96: "dolulu fırtına",
    99: "dolulu fırtına",
}

DAYS = ["Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar"]
MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"]

# Tests swap this for an httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None


async def get_weather(city: str) -> dict:
    city = city.strip()
    if not city:
        return {"status": "error", "message": "Hangi şehir?"}
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            geo = await client.get(GEOCODE_URL, params={"name": city, "count": 1, "language": "tr"})
            geo.raise_for_status()
            places = geo.json().get("results") or []
            if not places:
                return {"status": "error", "message": f"'{city}' diye bir yer bulamadım."}
            place = places[0]
            fc = await client.get(
                FORECAST_URL,
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "timezone": "auto",
                    "forecast_days": 1,
                },
            )
            fc.raise_for_status()
    except httpx.HTTPError as exc:
        return {"status": "error", "message": f"Hava durumu servisine ulaşılamadı ({exc.__class__.__name__})."}

    data = fc.json()
    cur, daily = data["current"], data["daily"]
    sky = WEATHER.get(cur["weather_code"], "bilinmiyor")
    rain = (daily.get("precipitation_probability_max") or [None])[0]
    message = (
        f"{place['name']}: {sky}, {round(cur['temperature_2m'])} derece "
        f"(hissedilen {round(cur['apparent_temperature'])}). Bugün en düşük {round(daily['temperature_2m_min'][0])}, "
        f"en yüksek {round(daily['temperature_2m_max'][0])} derece"
        + (f", yağış ihtimali yüzde {rain}." if rain is not None else ".")
    )
    return {
        "status": "ok",
        "message": message,
        "place": place["name"],
        "country": place.get("country"),
        "temperature_c": cur["temperature_2m"],
        "wind_kmh": cur["wind_speed_10m"],
    }


async def get_time(now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now()
    return {
        "status": "ok",
        "message": f"{now.day} {MONTHS[now.month - 1]} {now.year} {DAYS[now.weekday()]}, saat {now:%H:%M}.",
        "iso": now.isoformat(timespec="seconds"),
    }
