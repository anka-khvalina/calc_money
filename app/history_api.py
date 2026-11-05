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
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import ideal_predictions as ip
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


# ----- Ideal predictions -----


class IdealPredictionBody(BaseModel):
    matchId: int = Field(..., gt=0)
    predictionData: Dict[str, Any]
    calculationSettings: Dict[str, Any]


class IdealPredictionUpdateBody(BaseModel):
    predictionData: Dict[str, Any]
    calculationSettings: Dict[str, Any]


class IdealResolveBody(BaseModel):
    leagueId: str
    homeTeamId: int
    awayTeamId: int
    seasonId: Optional[int] = None


def _ideal_http(exc: ip.IdealPredictionError) -> HTTPException:
    return HTTPException(
        status_code=exc.status,
        detail={"code": exc.code, "message": exc.message},
    )


@app.post("/api/ideal-predictions")
async def ideal_predictions_create(body: IdealPredictionBody, response: Response) -> dict:
    try:
        out = await asyncio.to_thread(
            ip.create_ideal,
            body.matchId,
            body.predictionData,
            body.calculationSettings,
        )
        response.status_code = 201
        return out
    except ip.IdealPredictionError as exc:
        raise _ideal_http(exc) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("ideal create error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.put("/api/ideal-predictions/{match_id}")
async def ideal_predictions_update(match_id: int, body: IdealPredictionUpdateBody) -> dict:
    try:
        return await asyncio.to_thread(
            ip.update_ideal,
            match_id,
            body.predictionData,
            body.calculationSettings,
        )
    except ip.IdealPredictionError as exc:
        raise _ideal_http(exc) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("ideal update error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/ideal-predictions")
async def ideal_predictions_list(
    league: Optional[str] = None,
    season: Optional[str] = None,
    team: Optional[str] = None,
    dateFrom: Optional[str] = None,
    dateTo: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    pageSize: int = Query(50, ge=1, le=200),
) -> dict:
    try:
        return await asyncio.to_thread(
            ip.list_ideals,
            league=league,
            season=season,
            team=team,
            date_from=dateFrom,
            date_to=dateTo,
            status=status,
            page=page,
            page_size=pageSize,
        )
    except Exception as e:
        # Table may not exist yet
        detail = str(e)
        if "ideal_predictions" in detail or "PGRST" in detail or "42P01" in detail:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "TABLE_MISSING",
                    "message": "Таблица ideal_predictions ещё не создана. Выполните docs/sql/ideal_predictions.sql",
                },
            ) from e
        log.exception("ideal list error")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/api/ideal-predictions/resolve-match")
async def ideal_predictions_resolve(body: IdealResolveBody) -> dict:
    """Resolve calendar match for Line save UI. Always uses current league season."""
    try:
        cur_id, _cur_lab = await asyncio.to_thread(
            ip.current_season_for_league, body.leagueId
        )
        season_id = cur_id if cur_id is not None else body.seasonId
        if season_id is None:
            raise ip.IdealPredictionError(
                "MATCH_NOT_FOUND",
                "Будущая встреча этих команд не найдена в календаре. Необходима ручная проверка раздела \"История\".",
                404,
            )
        m = await asyncio.to_thread(
            ip.find_upcoming_match,
            league_id=body.leagueId,
            season_id=int(season_id),
            home_team_id=body.homeTeamId,
            away_team_id=body.awayTeamId,
        )
        # Re-check saveability (closing / season) before offering UI
        try:
            ip.assert_saveable(m)
            can_save = True
            block_code = None
            block_message = None
        except ip.IdealPredictionError as exc:
            can_save = False
            block_code = exc.code
            block_message = exc.message
        existing = await asyncio.to_thread(ip.get_ideal_by_match, int(m["match_id"]))
        return {
            "matchId": int(m["match_id"]),
            "league": m.get("league_name"),
            "season": m.get("season_label"),
            "homeTeam": m.get("home_team"),
            "awayTeam": m.get("away_team"),
            "matchDate": m.get("match_date"),
            "hasClosingOdds": ip.has_closing_odds(m),
            "canSave": can_save,
            "blockCode": block_code,
            "blockMessage": block_message,
            "existingIdealId": existing.get("id") if existing else None,
        }
    except ip.IdealPredictionError as exc:
        raise _ideal_http(exc) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("ideal resolve error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/api/ideal-predictions/{match_id}")
async def ideal_predictions_detail(match_id: int) -> dict:
    try:
        return await asyncio.to_thread(ip.get_ideal_detail, match_id)
    except ip.IdealPredictionError as exc:
        raise _ideal_http(exc) from exc
    except Exception as exc:  # pragma: no cover
        log.exception("ideal detail error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
