from fastapi import FastAPI, HTTPException
import os
import httpx
from math import floor

app = FastAPI(title="Vedic AI Core")

VEDASTRO_API_KEY = os.getenv("VEDASTRO_API_KEY")
AYANAMSA = os.getenv("AYANAMSA", "LAHIRI")

VEDASTRO_BASE = "https://api.vedastro.org/api/Calculate"

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

def deg_norm(x: float) -> float:
    """Normalize degrees to [0,360)."""
    x = x % 360.0
    if x < 0:
        x += 360.0
    return x

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
    if not VEDASTRO_API_KEY:
        raise HTTPException(status_code=500, detail="VEDASTRO_API_KEY not set")

    # VedAstro format expects:
    # Location: "lat,lon"
    # Time: "HH:MM/DD/MM/YYYY/+TZ"
    # We'll build: /AllPlanetData/PlanetName/All/Location/{location}/Time/{time_str}/Ayanamsa/{AYANAMSA}/APIKey/{KEY}
    url = f"{VEDASTRO_BASE}/AllPlanetData/PlanetName/All/Location/{location}/Time/{time_str}/Ayanamsa/{AYANAMSA}/APIKey/{VEDASTRO_API_KEY}"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"VedAstro error {r.status_code}: {r.text[:200]}")
        return r.json()

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

    sun_deg = None
    moon_deg = None

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

    if sun_deg is None or moon_deg is None:
        raise HTTPException(status_code=500, detail="Could not extract Sun/Moon Nirayana longitudes from VedAstro response")
    return sun_deg, moon_deg

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
