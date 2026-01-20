from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import httpx

app = FastAPI(title="Vedic AI Core")

VEDASTRO_API_KEY = os.getenv("VEDASTRO_API_KEY")
VEDASTRO_BASE = "https://api.vedastro.org/api"

# ------------------------
# MODELS
# ------------------------

class BirthData(BaseModel):
    date: str       # YYYY-MM-DD
    time: str       # HH:MM
    location: str   # city name


# ------------------------
# CORE
# ------------------------

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "vedic-ai-core",
        "message": "Vedic AI Core is running"
    }


@app.get("/health")
def health():
    return {"ok": True}


# ------------------------
# ASTRO ENDPOINT
# ------------------------

@app.post("/chart")
async def get_chart(data: BirthData):
    if not VEDASTRO_API_KEY:
        raise HTTPException(status_code=500, detail="VEDASTRO_API_KEY not set")

    params = {
        "date": data.date,
        "time": data.time,
        "location": data.location,
        "ayanamsa": "lahiri",
        "system": "whole_sign",
        "key": VEDASTRO_API_KEY
    }

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(
            f"{VEDASTRO_BASE}/Chart",
            params=params
        )

    if r.status_code != 200:
        raise HTTPException(status_code=500, detail=r.text)

    return r.json()
