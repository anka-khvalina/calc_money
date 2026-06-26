"""
Мини-API для вкладки «История»: прокси коэффициентов с userbet.info.

Запуск:
  pip install -r requirements-api.txt
  python3 -m uvicorn history_api:app --app-dir app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import os
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import userbet_odds as ubo

_DEFAULT_CORS = os.environ.get("HISTORY_API_CORS", "*").split(",")

app = FastAPI(title="FairOddsCalc History API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _DEFAULT_CORS if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class FetchOddsRequest(BaseModel):
    id_fixture: str = Field(..., min_length=1, description="id матча с сайта userbet.info")


class FetchOddsResponse(BaseModel):
    odds: Dict[str, float]


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/api/history/fetch-odds", response_model=FetchOddsResponse)
def history_fetch_odds(body: FetchOddsRequest) -> FetchOddsResponse:
    try:
        odds = ubo.fetch_odds(body.id_fixture)
    except ubo.UserbetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return FetchOddsResponse(odds=odds)
