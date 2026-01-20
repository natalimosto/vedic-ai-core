# main.py
import os
from typing import Any, Dict

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Vedic AI Core", version="0.1.0")

# --- Config (ONLY via environment variables) ---
VEDASTRO_API_KEY = os.getenv("VEDASTRO_API_KEY")
VEDASTRO_BASE_URL = os.getenv("VEDASTRO_BASE_URL", "https://api.vedastro.org")

AUTH_HEADER_NAME = os.getenv("VEDASTRO_AUTH_HEADER", "Authorization")
AUTH_HEADER_PREFIX = os.getenv("VEDASTRO_AUTH_PREFIX", "Bearer")


def _auth_headers() -> Dict[str, str]:
    if not VEDASTRO_API_KEY:
        raise RuntimeError("VEDASTRO_API_KEY is not set")
    if AUTH_HEADER_PREFIX.strip():
        return {AUTH_HEADER_NAME: f"{AUTH_HEADER_PREFIX} {VEDASTRO_API_KEY}".strip()}
    return {AUTH_HEADER_NAME: VEDASTRO_API_KEY}


# --- Models ---
class ChartRequest(BaseModel):
    endpoint: str = Field(..., example="/chart")
    params: Dict[str, Any] = Field(default_factory=dict)


# --- Routes ---
@app.get("/")
def root():
    return {"status": "ok", "message": "Vedic AI Core is running"}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/vedastro")
async def vedastro_proxy(payload: ChartRequest):
    url = f"{VEDASTRO_BASE_URL.rstrip('/')}/{payload.endpoint.lstrip('/')}"
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            r = await client.get(
                url,
                params=payload.params,
                headers=_auth_headers()
            )
            r.raise_for_status()
            return r.json()

    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    except httpx.HTTPStatusError as e:
        try:
            body = e.response.json()
        except Exception:
            body = e.response.text
        raise HTTPException(status_code=e.response.status_code, detail=body)

    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"VedAstro request failed: {str(e)}")
