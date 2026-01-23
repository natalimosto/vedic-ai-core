from fastapi import FastAPI, HTTPException, Query, UploadFile, File, status, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import os
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import uuid
import httpx
from math import floor
from typing import Optional

from ai_interpretation import (
    BirthData,
    DEFAULT_REQUIRED_OUTPUTS,
    NatalChartInput,
    PlanetPosition,
    build_prompt_from_chart,
    interpret_chart,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
TZ_FINDER = TimezoneFinder()

app = FastAPI(title="Vedic AI Core")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

AYANAMSA = os.getenv("AYANAMSA", "LAHIRI")

VEDASTRO_BASE = "https://api.vedastro.org/api/Calculate"


def get_vedastro_api_key() -> str:
    key = os.getenv("VEDASTRO_API_KEY")
    if not key:
        raise HTTPException(status_code=500, detail="VEDASTRO_API_KEY not set")
    return key

# -------------------------
# Helpers: Panchang math
# -------------------------

NAKSHATRA_NAMES = [
    "Ashwini","Bharani","Krittika","Rohini","Mrigashirsha","Ardra","Punarvasu","Pushya","Ashlesha",
    "Magha","Purva Phalguni","Uttara Phalguni","Hasta","Chitra","Swati","Vishakha","Anuradha","Jyeshtha",
    "Mula","Purva Ashadha","Uttara Ashadha","Shravana","Dhanishta","Shatabhisha","Purva Bhadrapada","Uttara Bhadrapada","Revati"
]

TITHI_NAMES = [
    # Shukla 1-15
    "Pratipada","Dvitiya","Tritiya","Chaturthi","Panchami","Shashthi","Saptami","Ashtami","Navami","Dashami",
    "Ekadashi","Dvadashi","Trayodashi","Chaturdashi","Purnima",
    # Krishna 1-15
    "Pratipada","Dvitiya","Tritiya","Chaturthi","Panchami","Shashthi","Saptami","Ashtami","Navami","Dashami",
    "Ekadashi","Dvadashi","Trayodashi","Chaturdashi","Amavasya"
]

YOGA_NAMES = [
    "Vishkumbha","Priti","Ayushman","Saubhagya","Shobhana","Atiganda","Sukarman","Dhriti","Shoola","Ganda",
    "Vriddhi","Dhruva","Vyaghata","Harshana","Vajra","Siddhi","Vyatipata","Variyana","Parigha","Shiva",
    "Siddha","Sadhya","Shubha","Shukla","Brahma","Indra","Vaidhriti"
]

KARANA_NAMES = [
    # Special:
    "Kimstughna",
    # Repeating 7:
    "Bava","Balava","Kaulava","Taitila","Garaja","Vanija","Vishti",
    # Last 3:
    "Shakuni","Chatushpada","Naga"
]

RASHI_NAMES = [
    "Aries","Taurus","Gemini","Cancer","Leo","Virgo",
    "Libra","Scorpio","Sagittarius","Capricorn","Aquarius","Pisces"
]

def deg_norm(x: float) -> float:
    """Normalize degrees to [0,360)."""
    x = x % 360.0
    if x < 0:
        x += 360.0
    return x


def compute_rashi(longitude: float):
    lon = deg_norm(longitude)
    idx0 = int(floor(lon / 30.0))
    name = RASHI_NAMES[idx0]
    degree_in_sign = lon - (idx0 * 30.0)
    return {"index": idx0 + 1, "name": name, "degree_in_sign": round(degree_in_sign, 4)}


def chunk_text(text: str, chunk_size: int, overlap: int):
    if chunk_size <= 0:
        raise HTTPException(status_code=422, detail="chunk_size must be > 0")
    if overlap < 0:
        raise HTTPException(status_code=422, detail="overlap must be >= 0")
    if overlap >= chunk_size:
        raise HTTPException(status_code=422, detail="overlap must be smaller than chunk_size")

    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end - overlap
        if start < 0:
            start = 0
        if start >= text_len:
            break
    return chunks

def compute_tithi(sun_deg: float, moon_deg: float):
    diff = deg_norm(moon_deg - sun_deg)              # 0..360
    tithi_index = int(floor(diff / 12.0)) + 1        # 1..30
    paksha = "Shukla" if tithi_index <= 15 else "Krishna"
    name = TITHI_NAMES[tithi_index - 1]
    # progress in current tithi (0..1)
    progress = (diff % 12.0) / 12.0
    return {"index": tithi_index, "name": name, "paksha": paksha, "progress": round(progress, 4)}

def compute_nakshatra(moon_deg: float):
    seg = 13.0 + (20.0 / 60.0)  # 13°20' = 13.333333...
    idx0 = int(floor(deg_norm(moon_deg) / seg))      # 0..26
    name = NAKSHATRA_NAMES[idx0]
    within = deg_norm(moon_deg) - (idx0 * seg)       # 0..seg
    pada = int(floor(within / (seg / 4.0))) + 1      # 1..4
    progress = within / seg
    return {"index": idx0 + 1, "name": name, "pada": pada, "progress": round(progress, 4)}

def compute_yoga(sun_deg: float, moon_deg: float):
    total = deg_norm(sun_deg + moon_deg)
    seg = 13.0 + (20.0 / 60.0)
    idx0 = int(floor(total / seg))                   # 0..26
    name = YOGA_NAMES[idx0]
    within = total - (idx0 * seg)
    progress = within / seg
    return {"index": idx0 + 1, "name": name, "progress": round(progress, 4)}

def compute_karana(sun_deg: float, moon_deg: float):
    # Karana changes every 6 degrees of (moon - sun)
    diff = deg_norm(moon_deg - sun_deg)              # 0..360
    half_tithi = int(floor(diff / 6.0)) + 1          # 1..60

    # Rules:
    # 1st half of Shukla Pratipada => Kimstughna
    # Last halves near end:
    # 58 => Shakuni, 59 => Chatushpada, 60 => Naga
    if half_tithi == 1:
        name = "Kimstughna"
    elif half_tithi in (58, 59, 60):
        name = {58: "Shakuni", 59: "Chatushpada", 60: "Naga"}[half_tithi]
    else:
        # repeating 7 from half_tithi 2..57
        repeating = ["Bava","Balava","Kaulava","Taitila","Garaja","Vanija","Vishti"]
        name = repeating[(half_tithi - 2) % 7]

    progress = (diff % 6.0) / 6.0
    return {"index": half_tithi, "name": name, "progress": round(progress, 4)}

# -------------------------
# VedAstro fetch
# -------------------------

async def fetch_all_planets(location: str, time_str: str):
    api_key = get_vedastro_api_key()

    # VedAstro format expects:
    # Location: "lat,lon"
    # Time: "HH:MM/DD/MM/YYYY/+TZ"
    # We'll build: /AllPlanetData/PlanetName/All/Location/{location}/Time/{time_str}/Ayanamsa/{AYANAMSA}/APIKey/{KEY}
    url = f"{VEDASTRO_BASE}/AllPlanetData/PlanetName/All/Location/{location}/Time/{time_str}/Ayanamsa/{AYANAMSA}/APIKey/{api_key}"
    return await fetch_vedastro_url(url, "AllPlanetData")


async def fetch_vedastro_url(url: str, label: str):
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"VedAstro {label} error {r.status_code}: {r.text[:200]}")
        data = r.json()
        if isinstance(data, dict):
            status = data.get("Status")
            payload = data.get("Payload")
            if status and status != "Pass":
                raise HTTPException(status_code=502, detail=f"VedAstro {label} status {status}")
            if isinstance(payload, str):
                message = payload.strip().replace("\n", " ")
                raise HTTPException(status_code=502, detail=f"VedAstro {label} error: {message[:200]}")
        return data


async def fetch_vedastro_candidates(urls: list[str], label: str):
    last_error = None
    for url in urls:
        try:
            return await fetch_vedastro_url(url, label)
        except HTTPException as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise HTTPException(status_code=502, detail=f"VedAstro {label} error: no candidate URL succeeded")


async def safe_fetch(label: str, coro):
    try:
        return {"ok": True, "data": await coro}
    except HTTPException as exc:
        return {"ok": False, "error": f"{label}: {exc.detail}"}


def _build_vedastro_url(parts: list[str]) -> str:
    return f"{VEDASTRO_BASE}/" + "/".join(parts)


async def fetch_all_houses(location: str, time_str: str):
    api_key = get_vedastro_api_key()

    candidates = [
        _build_vedastro_url(
            [
                "AllHouseData",
                "HouseName",
                "All",
                "Location",
                location,
                "Time",
                time_str,
                "Ayanamsa",
                AYANAMSA,
                "APIKey",
                api_key,
            ]
        ),
        _build_vedastro_url(
            [
                "AllHouseData",
                "Location",
                location,
                "Time",
                time_str,
                "Ayanamsa",
                AYANAMSA,
                "APIKey",
                api_key,
            ]
        ),
    ]
    return await fetch_vedastro_candidates(candidates, "AllHouseData")


async def fetch_indian_chart(chart_type: str, location: str, time_str: str):
    api_key = get_vedastro_api_key()

    candidates = [
        _build_vedastro_url(
            [
                "IndianChart",
                "ChartType",
                chart_type,
                "Location",
                location,
                "Time",
                time_str,
                "Ayanamsa",
                AYANAMSA,
                "APIKey",
                api_key,
            ]
        ),
        _build_vedastro_url(
            [
                "IndianChart",
                "Division",
                chart_type,
                "Location",
                location,
                "Time",
                time_str,
                "Ayanamsa",
                AYANAMSA,
                "APIKey",
                api_key,
            ]
        ),
        _build_vedastro_url(
            [
                "IndianChart",
                chart_type,
                "Location",
                location,
                "Time",
                time_str,
                "Ayanamsa",
                AYANAMSA,
                "APIKey",
                api_key,
            ]
        ),
    ]
    return await fetch_vedastro_candidates(candidates, f"IndianChart {chart_type}")


async def fetch_dasa_at_range(
    location: str,
    time_str: str,
    from_date: str,
    to_date: str,
):
    api_key = get_vedastro_api_key()

    candidates = [
        _build_vedastro_url(
            [
                "DasaAtRange",
                "Location",
                location,
                "Time",
                time_str,
                "From",
                from_date,
                "To",
                to_date,
                "Ayanamsa",
                AYANAMSA,
                "APIKey",
                api_key,
            ]
        ),
        _build_vedastro_url(
            [
                "DasaAtRange",
                "From",
                from_date,
                "To",
                to_date,
                "Location",
                location,
                "Time",
                time_str,
                "Ayanamsa",
                AYANAMSA,
                "APIKey",
                api_key,
            ]
        ),
    ]
    return await fetch_vedastro_candidates(candidates, "DasaAtRange")


def _looks_like_lat_lon(place: str) -> Optional[str]:
    parts = [p.strip() for p in place.split(",")]
    if len(parts) != 2:
        return None
    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except ValueError:
        return None
    return f"{lat},{lon}"


def _parse_lat_lon(location: str):
    parts = [p.strip() for p in location.split(",")]
    if len(parts) != 2:
        return None
    try:
        lat = float(parts[0])
        lon = float(parts[1])
    except ValueError:
        return None
    return lat, lon


async def geocode_place(place: str) -> str:
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": place, "format": "json", "limit": 1}
    headers = {"User-Agent": "vedic-ai-core/0.1"}

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=params, headers=headers)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail="Geocoding error")
        results = r.json()

    if not results:
        raise HTTPException(status_code=404, detail="Place not found for geocoding")

    lat = results[0].get("lat")
    lon = results[0].get("lon")
    if lat is None or lon is None:
        raise HTTPException(status_code=502, detail="Geocoding response incomplete")
    return f"{lat},{lon}"


async def resolve_location(place: str) -> str:
    lat_lon = _looks_like_lat_lon(place)
    if lat_lon:
        return lat_lon
    return await geocode_place(place)


def _parse_local_datetime(date: str, time: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(f"{date} {time}", fmt)
        except ValueError:
            continue
    raise HTTPException(status_code=422, detail="time must be HH:MM or HH:MM:SS")


def resolve_timezone_offset(lat: float, lon: float, date: str, time: str):
    tz_name = TZ_FINDER.timezone_at(lat=lat, lng=lon)
    if not tz_name:
        raise HTTPException(status_code=422, detail="Could not resolve timezone for location")
    local_dt = _parse_local_datetime(date, time)
    tz = ZoneInfo(tz_name)
    local_dt = local_dt.replace(tzinfo=tz)
    offset = local_dt.utcoffset()
    if offset is None:
        raise HTTPException(status_code=422, detail="Could not determine timezone offset")
    total_minutes = int(offset.total_seconds() / 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours = total_minutes // 60
    minutes = total_minutes % 60
    return f"{sign}{hours:02d}:{minutes:02d}", tz_name


def build_vedastro_time_string(date: str, time: str, timezone: str) -> str:
    try:
        year, month, day = [part.strip() for part in date.split("-")]
    except ValueError:
        raise HTTPException(status_code=422, detail="date must be YYYY-MM-DD")
    if not time:
        raise HTTPException(status_code=422, detail="time is required")
    if not timezone:
        raise HTTPException(status_code=422, detail="timezone is required")
    return f"{time}/{day}/{month}/{year}/{timezone}"

def extract_sun_moon_longitudes(all_planets_json):
    """
    We try to be tolerant to structure differences.
    We search for objects having PlanetName.Name == 'Sun'/'Moon'
    and then read PlanetNirayanaLongitude.TotalDegrees (float).
    """
    def iter_objects(x):
        if isinstance(x, dict):
            yield x
            for v in x.values():
                yield from iter_objects(v)
        elif isinstance(x, list):
            for i in x:
                yield from iter_objects(i)

    def find_total_degrees(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "TotalDegrees":
                    try:
                        return float(v)
                    except Exception:
                        return None
                found = find_total_degrees(v)
                if found is not None:
                    return found
        elif isinstance(x, list):
            for item in x:
                found = find_total_degrees(item)
                if found is not None:
                    return found
        return None

    sun_deg = None
    moon_deg = None

    # Fast path for Payload -> AllPlanetData list structure
    if isinstance(all_planets_json, dict):
        payload = all_planets_json.get("Payload")
        if isinstance(payload, dict):
            all_planets = payload.get("AllPlanetData")
            if isinstance(all_planets, list):
                for entry in all_planets:
                    if not isinstance(entry, dict):
                        continue
                    if "Sun" in entry and sun_deg is None:
                        sun_deg = find_total_degrees(entry.get("Sun"))
                    if "Moon" in entry and moon_deg is None:
                        moon_deg = find_total_degrees(entry.get("Moon"))

    for obj in iter_objects(all_planets_json):
        pn = obj.get("PlanetName")
        if isinstance(pn, dict) and pn.get("Name") in ("Sun", "Moon"):
            name = pn.get("Name")
            lng = obj.get("PlanetNirayanaLongitude") or {}
            td = lng.get("TotalDegrees")
            try:
                val = float(td) if td is not None else None
            except Exception:
                val = None

            if name == "Sun" and val is not None:
                sun_deg = val
            if name == "Moon" and val is not None:
                moon_deg = val

    # Fallback: search in any dict that contains planet name keys
    if sun_deg is None or moon_deg is None:
        for obj in iter_objects(all_planets_json):
            if not isinstance(obj, dict):
                continue
            for name in ("Sun", "Moon"):
                if name in obj:
                    val = find_total_degrees(obj.get(name))
                    if name == "Sun" and sun_deg is None and val is not None:
                        sun_deg = val
                    if name == "Moon" and moon_deg is None and val is not None:
                        moon_deg = val

    if sun_deg is None or moon_deg is None:
        raise HTTPException(status_code=500, detail="Could not extract Sun/Moon Nirayana longitudes from VedAstro response")
    return sun_deg, moon_deg


def extract_planet_longitudes(all_planets_json):
    def get_nirayana_total(x):
        if not isinstance(x, dict):
            return None
        lng = x.get("PlanetNirayanaLongitude")
        if isinstance(lng, dict) and "TotalDegrees" in lng:
            try:
                return float(lng["TotalDegrees"])
            except Exception:
                return None
        return None

    def find_total_degrees(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "TotalDegrees":
                    try:
                        return float(v)
                    except Exception:
                        return None
                found = find_total_degrees(v)
                if found is not None:
                    return found
        elif isinstance(x, list):
            for item in x:
                found = find_total_degrees(item)
                if found is not None:
                    return found
        return None

    result = {}
    names = ["Sun","Moon","Mars","Mercury","Jupiter","Venus","Saturn","Rahu","Ketu"]

    if isinstance(all_planets_json, dict):
        payload = all_planets_json.get("Payload")
        if isinstance(payload, dict):
            all_planets = payload.get("AllPlanetData")
            if isinstance(all_planets, list):
                for entry in all_planets:
                    if not isinstance(entry, dict):
                        continue
                    for name in names:
                        if name in entry and name not in result:
                            val = get_nirayana_total(entry.get(name))
                            if val is None:
                                val = find_total_degrees(entry.get(name))
                            if val is not None:
                                result[name] = val

    return result

# -------------------------
# Routes
# -------------------------

@app.get("/")
def root():
    return {"status": "online", "service": "vedic-ai-core"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/debug/planets")
async def debug_planets(
    location: str = "48.85,2.35",
    time: str = "00:12/04/02/2025/+01:00",
):
    data = await fetch_all_planets(location=location, time_str=time)
    sun_deg, moon_deg = extract_sun_moon_longitudes(data)
    return {"sun_deg": sun_deg, "moon_deg": moon_deg, "raw": data}

@app.get("/panchang")
async def panchang(
    location: str = "48.85,2.35",
    time: str = "00:12/04/02/2025/+01:00",
):
    data = await fetch_all_planets(location=location, time_str=time)
    sun_deg, moon_deg = extract_sun_moon_longitudes(data)

    sun_deg = deg_norm(sun_deg)
    moon_deg = deg_norm(moon_deg)

    result = {
        "input": {"location": location, "time": time, "ayanamsa": AYANAMSA},
        "sun": {"nirayana_longitude_total_degrees": sun_deg},
        "moon": {"nirayana_longitude_total_degrees": moon_deg},
        "tithi": compute_tithi(sun_deg, moon_deg),
        "nakshatra": compute_nakshatra(moon_deg),
        "yoga": compute_yoga(sun_deg, moon_deg),
        "karana": compute_karana(sun_deg, moon_deg),
    }
    return result

class PanchangFromChartInput(BaseModel):
    sun_deg: Optional[float] = Field(default=None, description="Nirayana longitude in degrees")
    moon_deg: Optional[float] = Field(default=None, description="Nirayana longitude in degrees")
    chart: Optional[NatalChartInput] = None


def _extract_sun_moon_from_chart(chart: NatalChartInput):
    sun_deg = None
    moon_deg = None
    for planet in chart.planets:
        if planet.name.lower() == "sun" and planet.longitude is not None:
            sun_deg = planet.longitude
        if planet.name.lower() == "moon" and planet.longitude is not None:
            moon_deg = planet.longitude
    return sun_deg, moon_deg


@app.post("/panchang")
async def panchang_from_chart(payload: PanchangFromChartInput):
    sun_deg = payload.sun_deg
    moon_deg = payload.moon_deg

    if (sun_deg is None or moon_deg is None) and payload.chart:
        chart_sun, chart_moon = _extract_sun_moon_from_chart(payload.chart)
        sun_deg = sun_deg if sun_deg is not None else chart_sun
        moon_deg = moon_deg if moon_deg is not None else chart_moon

    if sun_deg is None or moon_deg is None:
        raise HTTPException(
            status_code=422,
            detail="Provide sun_deg and moon_deg or include them in chart.planets.",
        )

    sun_deg = deg_norm(sun_deg)
    moon_deg = deg_norm(moon_deg)

    return {
        "input": {
            "sun_deg": sun_deg,
            "moon_deg": moon_deg,
            "ayanamsa": payload.chart.ayanamsa if payload.chart else AYANAMSA,
        },
        "sun": {"nirayana_longitude_total_degrees": sun_deg},
        "moon": {"nirayana_longitude_total_degrees": moon_deg},
        "tithi": compute_tithi(sun_deg, moon_deg),
        "nakshatra": compute_nakshatra(moon_deg),
        "yoga": compute_yoga(sun_deg, moon_deg),
        "karana": compute_karana(sun_deg, moon_deg),
    }


@app.get("/panchang/vedastro")
async def get_panchang(location: str, date: str, time: str, tz: str = "+01:00"):
    api_key = get_vedastro_api_key()

    url = (
        f"{VEDASTRO_BASE}/Panchang"
        f"/Location/{location}"
        f"/Time/{time}/{date}/{tz}"
        f"/Ayanamsa/{AYANAMSA}"
        f"/APIKey/{api_key}"
    )

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)

    return r.json()


@app.post("/natal-chart/prompt")
async def natal_chart_prompt(chart: NatalChartInput):
    return {"prompt": build_prompt_from_chart(chart)}


@app.post("/natal-chart/interpretation")
async def natal_chart_interpretation(chart: NatalChartInput):
    return interpret_chart(chart)


class ChartGenerateInput(BaseModel):
    date: str = Field(description="YYYY-MM-DD")
    time: str = Field(description="HH:MM")
    place: str = Field(description="City, country or 'lat,lon'")
    timezone: Optional[str] = Field(default=None, description="Timezone offset, e.g. +03:00")
    auto_timezone: bool = Field(default=True, description="Auto-resolve timezone from place and date")


@app.post("/chart/generate")
async def generate_chart(payload: ChartGenerateInput):
    location = await resolve_location(payload.place)
    lat_lon = _parse_lat_lon(location)
    if not lat_lon:
        raise HTTPException(status_code=422, detail="Could not parse location coordinates")
    tz_offset = payload.timezone
    tz_name = None
    if payload.auto_timezone or not tz_offset:
        tz_offset, tz_name = resolve_timezone_offset(lat_lon[0], lat_lon[1], payload.date, payload.time)
    time_str = build_vedastro_time_string(payload.date, payload.time, tz_offset)

    data = await fetch_all_planets(location=location, time_str=time_str)
    sun_deg, moon_deg = extract_sun_moon_longitudes(data)

    chart = NatalChartInput(
        birth=BirthData(
            date=payload.date,
            time=payload.time,
            timezone=payload.timezone,
            location=payload.place,
        ),
        ayanamsa=AYANAMSA,
        planets=[
            PlanetPosition(name="Sun", longitude=sun_deg),
            PlanetPosition(name="Moon", longitude=moon_deg),
        ],
        interpretation_style="deep psychological and symbolic; avoid generic astrology.",
        required_outputs=DEFAULT_REQUIRED_OUTPUTS,
    )

    return {
        "input": payload.model_dump(),
        "vedastro": {
            "location": location,
            "time": time_str,
            "ayanamsa": AYANAMSA,
            "timezone": tz_offset,
            "timezone_name": tz_name,
        },
        "sun_moon": {"sun_deg": sun_deg, "moon_deg": moon_deg},
        "prompt": build_prompt_from_chart(chart),
        "interpretation": interpret_chart(chart),
        "raw": data,
    }


class ChartFullInput(ChartGenerateInput):
    include_dasa: bool = Field(default=False, description="Include DasaAtRange data")
    dasa_from: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    dasa_to: Optional[str] = Field(default=None, description="YYYY-MM-DD")


@app.post("/chart/full")
async def chart_full(payload: ChartFullInput):
    location = await resolve_location(payload.place)
    lat_lon = _parse_lat_lon(location)
    if not lat_lon:
        raise HTTPException(status_code=422, detail="Could not parse location coordinates")
    tz_offset = payload.timezone
    tz_name = None
    if payload.auto_timezone or not tz_offset:
        tz_offset, tz_name = resolve_timezone_offset(lat_lon[0], lat_lon[1], payload.date, payload.time)
    time_str = build_vedastro_time_string(payload.date, payload.time, tz_offset)

    planets_result = await safe_fetch(
        "AllPlanetData", fetch_all_planets(location=location, time_str=time_str)
    )
    if not planets_result["ok"]:
        raise HTTPException(status_code=502, detail=planets_result["error"])

    houses_result = await safe_fetch(
        "AllHouseData", fetch_all_houses(location=location, time_str=time_str)
    )
    d1_result = await safe_fetch(
        "IndianChart D1", fetch_indian_chart("D1", location=location, time_str=time_str)
    )
    d9_result = await safe_fetch(
        "IndianChart D9", fetch_indian_chart("D9", location=location, time_str=time_str)
    )
    d12_result = await safe_fetch(
        "IndianChart D12", fetch_indian_chart("D12", location=location, time_str=time_str)
    )

    planets_raw = planets_result["data"]
    sun_deg, moon_deg = extract_sun_moon_longitudes(planets_raw)
    panchang = {
        "tithi": compute_tithi(sun_deg, moon_deg),
        "nakshatra": compute_nakshatra(moon_deg),
        "yoga": compute_yoga(sun_deg, moon_deg),
        "karana": compute_karana(sun_deg, moon_deg),
    }

    dasa_raw = None
    if payload.include_dasa:
        if not payload.dasa_from or not payload.dasa_to:
            raise HTTPException(status_code=422, detail="dasa_from and dasa_to are required when include_dasa is true")
        dasa_result = await safe_fetch(
            "DasaAtRange",
            fetch_dasa_at_range(
                location=location,
                time_str=time_str,
                from_date=payload.dasa_from,
                to_date=payload.dasa_to,
            ),
        )
        dasa_raw = dasa_result

    return {
        "input": payload.model_dump(),
        "vedastro": {
            "location": location,
            "time": time_str,
            "ayanamsa": AYANAMSA,
            "timezone": tz_offset,
            "timezone_name": tz_name,
        },
        "data": {
            "all_planets": planets_raw,
            "all_houses": houses_result,
            "indian_chart": {"D1": d1_result, "D9": d9_result, "D12": d12_result},
            "panchang": panchang,
            "dasa_at_range": dasa_raw,
        },
    }


@app.post("/chart/summary")
async def chart_summary(payload: ChartGenerateInput):
    location = await resolve_location(payload.place)
    lat_lon = _parse_lat_lon(location)
    if not lat_lon:
        raise HTTPException(status_code=422, detail="Could not parse location coordinates")
    tz_offset = payload.timezone
    tz_name = None
    if payload.auto_timezone or not tz_offset:
        tz_offset, tz_name = resolve_timezone_offset(lat_lon[0], lat_lon[1], payload.date, payload.time)
    time_str = build_vedastro_time_string(payload.date, payload.time, tz_offset)

    planets_raw = await fetch_all_planets(location=location, time_str=time_str)
    planet_lons = extract_planet_longitudes(planets_raw)

    summary_planets = {}
    for name, lon in planet_lons.items():
        summary_planets[name] = {
            "nirayana_longitude_total_degrees": round(lon, 6),
            "rashi": compute_rashi(lon),
            "nakshatra": compute_nakshatra(lon),
        }

    sun_deg = planet_lons.get("Sun")
    moon_deg = planet_lons.get("Moon")
    if sun_deg is None or moon_deg is None:
        raise HTTPException(status_code=500, detail="Sun/Moon missing in AllPlanetData")

    panchang = {
        "tithi": compute_tithi(sun_deg, moon_deg),
        "nakshatra": compute_nakshatra(moon_deg),
        "yoga": compute_yoga(sun_deg, moon_deg),
        "karana": compute_karana(sun_deg, moon_deg),
    }

    summary = {
        "input": payload.model_dump(),
        "vedastro": {
            "location": location,
            "time": time_str,
            "ayanamsa": AYANAMSA,
            "timezone": tz_offset,
            "timezone_name": tz_name,
        },
        "planets": summary_planets,
        "panchang": panchang,
    }
    outputs_dir = os.path.join(BASE_DIR, "outputs")
    os.makedirs(outputs_dir, exist_ok=True)
    filename = f"summary-{payload.date}-{payload.time.replace(':','')}.json"
    file_path = os.path.join(outputs_dir, filename)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=True, indent=2)
    summary["saved_to"] = file_path
    return summary


class KnowledgeIngestInput(BaseModel):
    input_dir: str = Field(default="knowledge/sources")
    output_dir: str = Field(default="knowledge/chunks")
    chunk_size: int = Field(default=1200)
    overlap: int = Field(default=150)
    max_pages: Optional[int] = Field(default=None, description="Optional limit for pages per PDF")
    start_page: Optional[int] = Field(default=None, description="Start page (1-based)")


@app.post("/knowledge/ingest")
def knowledge_ingest(payload: KnowledgeIngestInput):
    try:
        from pypdf import PdfReader
    except Exception:  # pragma: no cover - fallback if pypdf not installed
        try:
            from PyPDF2 import PdfReader  # type: ignore
        except Exception:
            raise HTTPException(status_code=500, detail="PDF reader not installed. Install pypdf.")

    input_dir = os.path.join(BASE_DIR, payload.input_dir)
    output_dir = os.path.join(BASE_DIR, payload.output_dir)
    if not os.path.isdir(input_dir):
        raise HTTPException(status_code=422, detail="Input directory not found")
    os.makedirs(output_dir, exist_ok=True)

    pdf_files = [f for f in os.listdir(input_dir) if f.lower().endswith(".pdf")]
    if not pdf_files:
        raise HTTPException(status_code=422, detail="No PDF files found in input directory")

    output_files = []
    total_chunks = 0
    for filename in sorted(pdf_files):
        pdf_path = os.path.join(input_dir, filename)
        reader = PdfReader(pdf_path)
        out_path = os.path.join(output_dir, f"{os.path.splitext(filename)[0]}.jsonl")
        pages_processed = 0
        chunks_written = 0
        empty_pages = 0
        with open(out_path, "w", encoding="utf-8") as f:
            for page_index, page in enumerate(reader.pages, start=1):
                if payload.start_page and page_index < payload.start_page:
                    continue
                if payload.max_pages and page_index > payload.max_pages:
                    break
                text = page.extract_text() or ""
                pages_processed += 1
                if not text.strip():
                    empty_pages += 1
                    continue
                for idx, chunk in enumerate(
                    chunk_text(text, payload.chunk_size, payload.overlap)
                ):
                    record = {
                        "source": filename,
                        "page": page_index,
                        "chunk_index": idx,
                        "text": chunk,
                    }
                    f.write(json.dumps(record, ensure_ascii=True) + "\n")
                    chunks_written += 1
                    total_chunks += 1
        output_files.append(out_path)

    result = {
        "processed": len(output_files),
        "outputs": output_files,
        "total_chunks": total_chunks,
    }
    if total_chunks == 0:
        result["warning"] = "No text chunks extracted. The PDF may be scanned; OCR may be required."
    return result


@app.get("/knowledge/ingest-run")
def knowledge_ingest_run(
    input_dir: str = Query(default="knowledge/sources"),
    output_dir: str = Query(default="knowledge/chunks"),
    chunk_size: int = Query(default=1200),
    overlap: int = Query(default=150),
    max_pages: Optional[int] = Query(default=None),
    start_page: Optional[int] = Query(default=None),
):
    payload = KnowledgeIngestInput(
        input_dir=input_dir,
        output_dir=output_dir,
        chunk_size=chunk_size,
        overlap=overlap,
        max_pages=max_pages,
        start_page=start_page,
    )
    return knowledge_ingest(payload)


@app.post("/knowledge/upload")
async def knowledge_upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Only PDF files are supported")

    target_dir = os.path.join(BASE_DIR, "knowledge", "sources")
    os.makedirs(target_dir, exist_ok=True)
    target_path = os.path.join(target_dir, file.filename)

    with open(target_path, "wb") as f:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)

    return {"saved_to": target_path}


def _write_job_status(job_path: str, payload: dict):
    with open(job_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)


def _run_knowledge_ingest_job(job_path: str, payload: KnowledgeIngestInput):
    _write_job_status(job_path, {"status": "running"})
    try:
        result = knowledge_ingest(payload)
        _write_job_status(job_path, {"status": "completed", "result": result})
    except Exception as exc:  # pragma: no cover - best effort background logging
        _write_job_status(job_path, {"status": "failed", "error": str(exc)})


@app.post("/knowledge/ingest-start")
def knowledge_ingest_start(payload: KnowledgeIngestInput, background_tasks: BackgroundTasks):
    jobs_dir = os.path.join(BASE_DIR, "knowledge", "jobs")
    os.makedirs(jobs_dir, exist_ok=True)
    job_id = uuid.uuid4().hex
    job_path = os.path.join(jobs_dir, f"{job_id}.json")
    background_tasks.add_task(_run_knowledge_ingest_job, job_path, payload)
    return {"job_id": job_id, "status_url": f"/knowledge/ingest-status?job_id={job_id}"}


@app.get("/knowledge/ingest-status")
def knowledge_ingest_status(job_id: str = Query(...)):
    jobs_dir = os.path.join(BASE_DIR, "knowledge", "jobs")
    job_path = os.path.join(jobs_dir, f"{job_id}.json")
    if not os.path.exists(job_path):
        raise HTTPException(status_code=404, detail="Job not found")
    with open(job_path, "r", encoding="utf-8") as f:
        return json.load(f)

