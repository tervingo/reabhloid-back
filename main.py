from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="Reabhloid API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://reabhloid.netlify.app",
        "http://localhost:5173",
        "http://localhost:4173",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("DB_NAME", "reabhloid")

client = AsyncIOMotorClient(MONGODB_URI)
db = client[DB_NAME]


# --- Models ---

class RunSettings(BaseModel):
    gridWidth: int
    gridHeight: int
    initialMutationRate: float
    reproThreshold: float
    seasonPeriod: int
    seasonAmplitude: float
    zoneBaseTemps: list[float]
    zoneRegen: list[float]
    tempStressIntensity: float = 0.1
    worldType: str = "STANDARD"

class StartRun(BaseModel):
    run_id: str
    settings: RunSettings

class FounderTraits(BaseModel):
    tempOpt: float
    maxAge: int
    predationIndex: float
    mutationRate: float

class NewSpeciesEvent(BaseModel):
    tick: int
    speciesId: int
    parentSpeciesId: Optional[int] = None
    founderTraits: FounderTraits
    zone: int
    x: int
    y: int

class SpeciesSnapshot(BaseModel):
    speciesId: int
    population: int
    meanTempOpt: float
    meanPredationIndex: float
    meanMutationRate: float
    meanMaxAge: float
    meanEnergy: float
    dominantZone: int
    activePredators: int
    metabolicType: str = "aerobic"

class Snapshot(BaseModel):
    tick: int
    species: list[SpeciesSnapshot]

class ExtinctionEvent(BaseModel):
    tick: int
    speciesId: int
    lastPopulation: int

class FinalCell(BaseModel):
    x: int
    y: int
    speciesId: int
    energy: float
    tempOpt: float
    predationIndex: float

class EndRun(BaseModel):
    tick: int
    reason: str  # "max_ticks" | "extinction" | "dominance" | "manual"
    dominantSpeciesId: Optional[int] = None
    finalBoard: Optional[list[FinalCell]] = None

class UpdateRun(BaseModel):
    comment: str
    rating: int  # 0 = sin valoración, 1-3 = estrellas


# --- Write endpoints ---

@app.post("/runs")
async def start_run(body: StartRun):
    doc = {
        "_id": body.run_id,
        "startedAt": datetime.utcnow(),
        "settings": body.settings.model_dump(),
        "worldType": body.settings.worldType,
        "endedAt": None,
        "endReason": None,
        "comment": "",
        "rating": 0,
    }
    await db.runs.insert_one(doc)
    return {"ok": True, "run_id": body.run_id}

@app.put("/runs/{run_id}/end")
async def end_run(run_id: str, body: EndRun):
    update: dict = {
        "endedAt": datetime.utcnow(),
        "endReason": body.reason,
        "endTick": body.tick,
        "dominantSpeciesId": body.dominantSpeciesId,
    }
    if body.finalBoard is not None:
        update["finalBoard"] = [c.model_dump() for c in body.finalBoard]
    result = await db.runs.update_one({"_id": run_id}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(404, "Run not found")
    return {"ok": True}

@app.post("/runs/{run_id}/species")
async def new_species(run_id: str, body: NewSpeciesEvent):
    doc = {"run_id": run_id, **body.model_dump()}
    await db.species_events.insert_one(doc)
    return {"ok": True}

@app.post("/runs/{run_id}/snapshots")
async def add_snapshot(run_id: str, body: Snapshot):
    doc = {"run_id": run_id, **body.model_dump()}
    await db.snapshots.insert_one(doc)
    return {"ok": True}

@app.post("/runs/{run_id}/extinctions")
async def add_extinction(run_id: str, body: ExtinctionEvent):
    doc = {"run_id": run_id, **body.model_dump()}
    await db.extinctions.insert_one(doc)
    return {"ok": True}


@app.patch("/runs/{run_id}")
async def update_run(run_id: str, body: UpdateRun):
    if body.rating not in (0, 1, 2, 3):
        raise HTTPException(400, "rating must be 0, 1, 2 or 3")
    result = await db.runs.update_one(
        {"_id": run_id},
        {"$set": {"comment": body.comment, "rating": body.rating}}
    )
    if result.matched_count == 0:
        raise HTTPException(404, "Run not found")
    return {"ok": True}


# --- Delete endpoints ---

@app.delete("/runs/{run_id}")
async def delete_run(run_id: str):
    await db.snapshots.delete_many({"run_id": run_id})
    await db.species_events.delete_many({"run_id": run_id})
    await db.extinctions.delete_many({"run_id": run_id})
    result = await db.runs.delete_one({"_id": run_id})
    if result.deleted_count == 0:
        raise HTTPException(404, "Run not found")
    return {"ok": True}


# --- Query endpoints ---

@app.get("/runs")
async def list_runs():
    runs = await db.runs.find({}, {"settings": 0}).sort("startedAt", -1).to_list(100)
    for r in runs:
        r["id"] = str(r.pop("_id"))
    return runs

@app.get("/runs/{run_id}")
async def get_run(run_id: str):
    run = await db.runs.find_one({"_id": run_id})
    if not run:
        raise HTTPException(404, "Run not found")
    run["id"] = str(run.pop("_id"))
    return run

@app.get("/runs/{run_id}/species")
async def get_species_events(run_id: str):
    events = await db.species_events.find(
        {"run_id": run_id}, {"_id": 0}
    ).sort("tick", 1).to_list(10000)
    return events

@app.get("/runs/{run_id}/snapshots")
async def get_snapshots(run_id: str, species_id: Optional[int] = None):
    snaps = await db.snapshots.find(
        {"run_id": run_id}, {"_id": 0}
    ).sort("tick", 1).to_list(100000)
    if species_id is not None:
        for s in snaps:
            s["species"] = [sp for sp in s["species"] if sp["speciesId"] == species_id]
        snaps = [s for s in snaps if s["species"]]
    return snaps

@app.get("/runs/{run_id}/extinctions")
async def get_extinctions(run_id: str):
    events = await db.extinctions.find(
        {"run_id": run_id}, {"_id": 0}
    ).sort("tick", 1).to_list(10000)
    return events
