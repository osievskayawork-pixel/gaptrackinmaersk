"""
GAP Logistics — Container Tracker API
FastAPI + Supabase + Maersk Ocean Track & Trace API
Хостинг: Render.com (free tier)
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import httpx
import os
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title="GAP Logistics Tracker", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Supabase ──────────────────────────────────────────
supabase: Client = create_client(
    os.environ["SUPABASE_URL"],
    os.environ["SUPABASE_KEY"]
)

# ── Maersk Ocean Track & Trace API ────────────────────
# Consumer Key з integration.maersk.com → Apps and Keys
MAERSK_KEY = os.environ.get("MAERSK_CONSUMER_KEY", "")
MAERSK_URL = "https://api.maersk.com/track-and-trace-private/containers"


async def fetch_maersk(number: str) -> dict:
    """Запит до офіційного Maersk Track & Trace API"""
    headers = {
        "Consumer-Key": MAERSK_KEY,
        "Accept":       "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{MAERSK_URL}/{number.upper()}",
            headers=headers
        )
        resp.raise_for_status()
        return resp.json()


def parse_maersk(raw: dict, number: str) -> dict:
    """Парсимо відповідь Maersk Track & Trace (DCSA v2.2)"""
    result = {
        "number":           number.upper(),
        "status":           "UNKNOWN",
        "vessel_name":      None,
        "current_location": None,
        "destination":      None,
        "eta":              None,
        "etd":              None,
        "last_event":       None,
        "last_updated":     datetime.utcnow().isoformat(),
    }

    try:
        # ── Transport Plan (маршрут) ──────────────────
        legs = raw.get("transportPlan", [])
        if legs:
            last_leg = legs[-1]
            pod = last_leg.get("portOfDischarge", {})
            result["destination"] = pod.get("city") or pod.get("UNLocationCode")
            result["eta"] = (
                last_leg.get("vesselArrival") or
                last_leg.get("plannedArrivalDate")
            )

        # Поточний рейс (судно + ETD)
        for leg in legs:
            if leg.get("transportMode") == "VESSEL":
                result["vessel_name"] = leg.get("vesselName")
                result["etd"] = (
                    leg.get("vesselDeparture") or
                    leg.get("plannedDepartureDate")
                )
                break

        # ── Milestones (події) ────────────────────────
        containers_data = raw.get("containers", [{}])
        milestones = containers_data[0].get("milestones", []) if containers_data else []

        if milestones:
            latest = milestones[-1]
            result["last_event"] = latest.get("description", "")

            loc = latest.get("location") or {}
            result["current_location"] = (
                loc.get("city") or loc.get("UNLocationCode")
            )

            # Маппінг статусів DCSA → наші
            status_map = {
                "GATE_IN":         "GATE_IN",
                "LOADED":          "ON_VESSEL",
                "DEPARTED":        "DEPARTED",
                "ARRIVED":         "ARRIVED",
                "DISCHARGED":      "DISCHARGED",
                "GATE_OUT":        "GATE_OUT",
                "IN_TRANSIT":      "IN_TRANSIT",
            }
            raw_st = latest.get("statusCode", "")
            result["status"] = status_map.get(raw_st, raw_st or "UNKNOWN")

    except Exception as e:
        log.warning(f"Parse error {number}: {e}")

    return result


# ── Щоденне оновлення (06:00 UTC = 09:00 Київ) ───────
async def refresh_all():
    log.info("🔄 Оновлення контейнерів...")
    rows = supabase.table("containers").select("number").execute()
    for row in (rows.data or []):
        num = row["number"]
        try:
            raw    = await fetch_maersk(num)
            parsed = parse_maersk(raw, num)
            supabase.table("containers").update(parsed).eq("number", num).execute()
            log.info(f"  ✓ {num} — {parsed['status']}")
        except Exception as e:
            log.error(f"  ✗ {num}: {e}")
    log.info("✅ Готово")


scheduler = AsyncIOScheduler(timezone="UTC")

@app.on_event("startup")
async def startup():
    scheduler.add_job(refresh_all, "cron", hour=6, minute=0)
    scheduler.start()
    log.info("Scheduler запущено — щодня о 06:00 UTC")

@app.on_event("shutdown")
async def shutdown():
    scheduler.shutdown()


# ── Endpoints ─────────────────────────────────────────

@app.get("/api/containers")
async def get_containers():
    result = supabase.table("containers").select("*").order("created_at").execute()
    return result.data


class AddContainer(BaseModel):
    number:     str
    cargo_name: str = "Вантаж"
    batch:      str = ""
    weight:     str = ""


@app.post("/api/containers", status_code=201)
async def add_container(body: AddContainer):
    number = body.number.upper().strip()
    existing = supabase.table("containers").select("number").eq("number", number).execute()
    if existing.data:
        raise HTTPException(400, f"Контейнер {number} вже існує")
    try:
        raw    = await fetch_maersk(number)
        data   = parse_maersk(raw, number)
    except Exception as e:
        log.warning(f"Maersk API недоступний для {number}: {e}")
        data = {"number": number, "status": "UNKNOWN", "last_updated": datetime.utcnow().isoformat()}
    data["cargo_name"] = body.cargo_name
    data["batch"]      = body.batch
    data["weight"]     = body.weight
    result = supabase.table("containers").insert(data).execute()
    return result.data[0]


@app.delete("/api/containers/{number}")
async def remove_container(number: str):
    supabase.table("containers").delete().eq("number", number.upper()).execute()
    return {"ok": True}


@app.post("/api/refresh")
async def manual_refresh():
    await refresh_all()
    return {"ok": True, "time": datetime.utcnow().isoformat()}


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}
