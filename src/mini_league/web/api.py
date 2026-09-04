"""JSON API for the organizer flow (design doc section 8, milestone 2 subset).

Auth is milestone 7, so these endpoints are currently open. Leaderboard and
player-history endpoints are milestone 3; merge is milestone 4; team generation
is milestone 5.
"""

from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import games as games_service
from .. import leaderboard as leaderboard_service
from .. import players as players_service
from .. import seasons as seasons_service
from .. import sessions as sessions_service
from ..models import Game, LeagueSession, Player
from ..ratings import display_rating
from .deps import get_db
from .schemas import (
    CheckInRequest,
    GameCreate,
    GameOut,
    GameUpdate,
    LeaderboardRowOut,
    PlayerCreate,
    PlayerDetailOut,
    PlayerMatchOut,
    PlayerOut,
    RatingPointOut,
    SeasonCreate,
    SeasonOut,
    SeasonSummaryOut,
    SessionCreate,
    SessionOut,
    TeamOut,
)

router = APIRouter(prefix="/api")


def game_out(game: Game) -> GameOut:
    return GameOut(
        id=game.id,
        session_id=game.session_id,
        round_number=game.round_number,
        players_on_field=game.players_on_field,
        played_at=game.played_at,
        deleted_at=game.deleted_at,
        teams=[
            TeamOut(
                team_index=t.team_index,
                rank=t.rank,
                score=t.score,
                player_ids=list(t.player_ids),
            )
            for t in sorted(game.teams, key=lambda t: t.team_index)
        ],
    )


def session_out(db: Session, session: LeagueSession) -> SessionOut:
    return SessionOut(
        id=session.id,
        season_id=session.season_id,
        date=session.date,
        notes=session.notes,
        players=[
            {
                "player": sp.player,
                "checked_in_at": sp.checked_in_at,
                "checked_out_at": sp.checked_out_at,
            }
            for sp in sessions_service.session_roster(db, session.id)
        ],
        games=[game_out(g) for g in games_service.session_games(db, session.id)],
    )


def to_team_inputs(teams) -> list[games_service.TeamInput]:
    return [
        games_service.TeamInput(player_ids=list(t.player_ids), rank=t.rank, score=t.score)
        for t in teams
    ]


# --- seasons -------------------------------------------------------------------


@router.get("/seasons", response_model=list[SeasonOut])
def list_seasons(db: Session = Depends(get_db)):
    return seasons_service.list_seasons(db)


@router.post("/seasons", response_model=SeasonOut, status_code=201)
def create_season(payload: SeasonCreate, db: Session = Depends(get_db)):
    try:
        return seasons_service.create_season(db, payload.name, payload.start_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- players -------------------------------------------------------------------


@router.get("/players", response_model=list[PlayerMatchOut])
def search_players(
    q: str = Query(default="", description="fuzzy name search; includes inactive players"),
    db: Session = Depends(get_db),
):
    matches = players_service.search_players(db, q)
    return [
        PlayerMatchOut(
            player=PlayerOut.model_validate(m.player, from_attributes=True),
            score=round(m.score, 3),
            is_duplicate=m.is_duplicate,
        )
        for m in matches
    ]


@router.get("/leaderboard", response_model=list[LeaderboardRowOut])
def get_leaderboard(
    season_id: int | None = None,
    min_games: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Standings for a season, best first. Defaults to the current season."""
    if season_id is None:
        season = seasons_service.current_season(db)
        if season is None:
            return []
        season_id = season.id
    return [
        LeaderboardRowOut(
            rank=row.rank,
            player=PlayerOut.model_validate(row.player, from_attributes=True),
            rating=row.rating,
            mu=round(row.mu, 4),
            sigma=round(row.sigma, 4),
            games_played=row.games_played,
            wins=row.wins,
            losses=row.losses,
        )
        for row in leaderboard_service.leaderboard(db, season_id, min_games=min_games)
    ]


@router.get("/players/{player_id}", response_model=PlayerDetailOut)
def get_player(player_id: int, db: Session = Depends(get_db)):
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail=f"player {player_id} does not exist")
    return PlayerDetailOut(
        player=PlayerOut.model_validate(player, from_attributes=True),
        seasons=[
            SeasonSummaryOut(
                season=SeasonOut.model_validate(s.season, from_attributes=True),
                rating=s.rating,
                mu=round(s.mu, 4),
                sigma=round(s.sigma, 4),
                games_played=s.games_played,
                wins=s.wins,
                losses=s.losses,
            )
            for s in leaderboard_service.player_seasons(db, player_id)
        ],
        all_time=leaderboard_service.all_time_record(db, player_id),
    )


@router.get("/players/{player_id}/history", response_model=list[RatingPointOut])
def get_player_history(
    player_id: int, season_id: int | None = None, db: Session = Depends(get_db)
):
    if db.get(Player, player_id) is None:
        raise HTTPException(status_code=404, detail=f"player {player_id} does not exist")
    if season_id is None:
        summaries = leaderboard_service.player_seasons(db, player_id)
        if not summaries:
            return []
        season_id = summaries[0].season.id
    from trueskill import Rating

    return [
        RatingPointOut(
            game_id=row.game_id,
            mu_before=round(row.mu_before, 4),
            sigma_before=round(row.sigma_before, 4),
            mu_after=round(row.mu_after, 4),
            sigma_after=round(row.sigma_after, 4),
            rating_after=display_rating(Rating(row.mu_after, row.sigma_after)),
        )
        for row in leaderboard_service.rating_history(db, player_id, season_id)
    ]


@router.post("/players", response_model=PlayerOut, status_code=201)
def create_player(payload: PlayerCreate, db: Session = Depends(get_db)):
    """409 with the candidate matches when the name looks like a duplicate."""
    try:
        return players_service.create_player(db, payload.name, force=payload.force)
    except players_service.DuplicatePlayerError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "matches": [
                    {
                        "player": PlayerOut.model_validate(
                            m.player, from_attributes=True
                        ).model_dump(),
                        "score": round(m.score, 3),
                    }
                    for m in exc.matches
                ],
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# --- sessions ------------------------------------------------------------------


@router.post("/sessions", response_model=SessionOut, status_code=201)
def create_session(payload: SessionCreate, db: Session = Depends(get_db)):
    on = payload.date or date_type.today()
    try:
        session = sessions_service.create_session(db, on, notes=payload.notes)
    except seasons_service.NoSeasonError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return session_out(db, session)


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: int, db: Session = Depends(get_db)):
    try:
        session = sessions_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return session_out(db, session)


@router.post("/sessions/{session_id}/checkin", response_model=SessionOut)
def check_in(session_id: int, payload: CheckInRequest, db: Session = Depends(get_db)):
    try:
        sessions_service.check_in(db, session_id, payload.player_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return session_out(db, sessions_service.get_session(db, session_id))


@router.post("/sessions/{session_id}/checkout", response_model=SessionOut)
def check_out(session_id: int, payload: CheckInRequest, db: Session = Depends(get_db)):
    try:
        sessions_service.check_out(db, session_id, payload.player_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return session_out(db, sessions_service.get_session(db, session_id))


# --- games ---------------------------------------------------------------------


@router.post("/sessions/{session_id}/games", response_model=GameOut, status_code=201)
def record_game(session_id: int, payload: GameCreate, db: Session = Depends(get_db)):
    try:
        game = games_service.record_game(
            db,
            session_id,
            to_team_inputs(payload.teams),
            players_on_field=payload.players_on_field,
            round_number=payload.round_number,
            played_at=payload.played_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return game_out(game)


@router.patch("/games/{game_id}", response_model=GameOut)
def edit_game(game_id: int, payload: GameUpdate, db: Session = Depends(get_db)):
    try:
        game = games_service.edit_game(
            db,
            game_id,
            teams=to_team_inputs(payload.teams) if payload.teams is not None else None,
            players_on_field=payload.players_on_field,
            round_number=payload.round_number,
            played_at=payload.played_at,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return game_out(game)


@router.delete("/games/{game_id}", response_model=GameOut)
def delete_game(game_id: int, db: Session = Depends(get_db)):
    try:
        return game_out(games_service.delete_game(db, game_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/games/{game_id}/restore", response_model=GameOut)
def restore_game(game_id: int, db: Session = Depends(get_db)):
    try:
        return game_out(games_service.restore_game(db, game_id))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
