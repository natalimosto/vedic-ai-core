from fastapi import FastAPI
import os
import httpx

app = FastAPI(title="Vedic AI Core")

VEDASTRO_API_KEY = os.getenv("VEDASTRO_API_KEY")
VEDASTRO_URL = "https://api.vedastro.org/..."  # потім замінимо на реальний endpoint


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


@app.get("/test-vedastro")
async def test_vedastro():
    if not VEDASTRO_API_KEY:
        return {"error": "VEDASTRO_API_KEY not set"}

    async with httpx.AsyncClient() as client:
        r = await client.get(
            VEDASTRO_URL,
            headers={"Authorization": f"Bearer {VEDASTRO_API_KEY}"}
        )
        return r.json()
