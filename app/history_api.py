"""
Мини-API для вкладки «История»: прокси коэффициентов с userbet.info.

Запуск:
  pip install -r requirements-api.txt
  python3 -m uvicorn history_api:app --app-dir app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import userbet_odds as ubo

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("history_api")

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
async def history_fetch_odds(body: FetchOddsRequest) -> FetchOddsResponse:
    log.info("fetch-odds start id_fixture=%s", body.id_fixture)
    try:
        odds = await asyncio.to_thread(ubo.fetch_odds, body.id_fixture)
    except ubo.UserbetError as exc:
        log.warning("fetch-odds userbet error id=%s: %s", body.id_fixture, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("fetch-odds server error id=%s", body.id_fixture)
        raise HTTPException(
            status_code=500,
            detail=f"Ошибка сервера при запросе к userbet: {exc}",
        ) from exc
    log.info("fetch-odds ok id=%s fields=%d", body.id_fixture, len(odds))
    return FetchOddsResponse(odds=odds)
