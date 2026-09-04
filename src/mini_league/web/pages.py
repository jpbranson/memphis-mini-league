"""Server-rendered pages (design doc section 7).

Public: the leaderboard and player pages (milestone 3).
Organizer: session creation and the day-of board (milestone 2).

Mobile-first Jinja2 templates driven by HTMX. Organizer actions re-render the
whole session board rather than patching pieces of it: at the field, a screen
that is always consistent beats one that is clever. The standings drawer is the
deliberate exception, since it must not disturb an in-progress team assignment.

Auth is milestone 7, so the organizer pages are currently open.
"""

from __future__ import annotations

import random
from datetime import date as date_type

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from trueskill import Rating

from .. import games as games_service
from .. import leaderboard as leaderboard_service
from .. import players as players_service
from .. import seasons as seasons_service
from .. import sessions as sessions_service
from .. import teams as teams_service
from ..models import LeagueSession, Player
from ..ratings import display_rating
from ..settings import TeamGenConfig
from .deps import get_db, templates

router = APIRouter()

DEFAULT_MIN_GAMES = 5


# --- shared helpers -------------------------------------------------------------


def load_session(db: Session, session_id: int) -> LeagueSession:
    try:
        return sessions_service.get_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def rating_lookup(db: Session, season_id: int):
    """Callable for templates: player id -> current rating and whether they are new."""
    snapshots = leaderboard_service.season_ratings(db, season_id)
    default = leaderboard_service.starting_rating()

    def info(player_id: int) -> dict:
        snapshot = snapshots.get(player_id)
        if snapshot is None or snapshot.games_played == 0:
            return {"rating": display_rating(default), "games": 0, "is_new": True}
        return {
            "rating": display_rating(Rating(snapshot.mu, snapshot.sigma)),
            "games": snapshot.games_played,
            "is_new": False,
        }

    return info


def session_history(db: Session, session_id: int) -> list[list[list[int]]]:
    """Team rosters of this session's games, oldest first, for the variety score."""
    games = sorted(
        games_service.session_games(db, session_id), key=lambda g: (g.played_at, g.id)
    )
    return [
        [list(t.player_ids) for t in sorted(g.teams, key=lambda t: t.team_index)]
        for g in games
    ]


def parse_assignments(form) -> dict[int, int]:
    """Map player_id -> team_index from `assign_<player_id>` form fields."""
    assignments: dict[int, int] = {}
    for key, value in form.multi_items():
        if not key.startswith("assign_"):
            continue
        if value in ("0", "1"):
            assignments[int(key.removeprefix("assign_"))] = int(value)
    return assignments


def matchup(db: Session, session: LeagueSession, assignments: dict[int, int]) -> dict:
    """Team strengths and predicted result for the current assignment."""
    side_a = [pid for pid, team in assignments.items() if team == 0]
    side_b = [pid for pid, team in assignments.items() if team == 1]
    if not side_a or not side_b:
        return {"ready": False, "count_a": len(side_a), "count_b": len(side_b)}

    ratings = leaderboard_service.current_ratings(
        db, session.season_id, side_a + side_b
    )
    summary = teams_service.describe_matchup(
        [ratings[p] for p in side_a], [ratings[p] for p in side_b]
    )

    # The board rating is deliberately pessimistic about newcomers, while the
    # prediction treats them as average. When a side has new players the two
    # disagree, so say which one to trust rather than leaving it looking wrong.
    rating_info = rating_lookup(db, session.season_id)
    new_a = sum(1 for pid in side_a if rating_info(pid)["is_new"])
    new_b = sum(1 for pid in side_b if rating_info(pid)["is_new"])

    summary.update(
        {
            "ready": True,
            "count_a": len(side_a),
            "count_b": len(side_b),
            "new_a": new_a,
            "new_b": new_b,
            "has_new": bool(new_a or new_b),
        }
    )
    return summary


# --- the session board ----------------------------------------------------------


def checkin_candidates(db: Session, session: LeagueSession, query: str = "") -> list[dict]:
    """Players the organizer can tap to check in.

    With no query this is every active player who is not already present, so
    check-in never requires typing. With a query it is the fuzzy search, which
    also reaches inactive players (design doc section 6.1).
    """
    roster = {sp.player_id: sp for sp in sessions_service.session_roster(db, session.id)}

    def is_present(player_id: int) -> bool:
        entry = roster.get(player_id)
        return entry is not None and entry.checked_out_at is None

    query = query.strip()
    if query:
        players = [m.player for m in players_service.search_players(db, query)]
    else:
        players = [p for p in players_service.list_players(db) if not is_present(p.id)]
    return [{"player": p, "present": is_present(p.id)} for p in players]


def board_context(
    db: Session,
    session: LeagueSession,
    *,
    error: str | None = None,
    notice: str | None = None,
    duplicate_name: str | None = None,
    duplicate_matches: list | None = None,
    assignment: dict[int, int] | None = None,
) -> dict:
    roster = sessions_service.session_roster(db, session.id)
    present = [sp for sp in roster if sp.checked_out_at is None]
    away = [sp for sp in roster if sp.checked_out_at is not None]
    all_games = games_service.session_games(db, session.id, include_deleted=True)
    assignment = assignment or {}
    return {
        "session": session,
        "season": session.season,
        "present": present,
        "away": away,
        "candidates": checkin_candidates(db, session),
        "query": "",
        "games": [g for g in all_games if g.deleted_at is None],
        "deleted_games": [g for g in all_games if g.deleted_at is not None],
        "player_name": lambda pid: db.get(Player, pid).name,
        "rating_of": rating_lookup(db, session.season_id),
        "assignment": assignment,
        "matchup": matchup(db, session, assignment),
        "error": error,
        "notice": notice,
        "duplicate_name": duplicate_name,
        "duplicate_matches": duplicate_matches or [],
    }


def render_board(
    request: Request,
    db: Session,
    session: LeagueSession,
    *,
    error: str | None = None,
    notice: str | None = None,
    duplicate_name: str | None = None,
    duplicate_matches: list | None = None,
    assignment: dict[int, int] | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    ctx = board_context(
        db,
        session,
        error=error,
        notice=notice,
        duplicate_name=duplicate_name,
        duplicate_matches=duplicate_matches,
        assignment=assignment,
    )
    ctx["request"] = request
    return templates.TemplateResponse(
        request, "partials/board.html", ctx, status_code=status_code
    )


# --- public pages (milestone 3) --------------------------------------------------


def leaderboard_context(
    db: Session, season_id: int | None, min_games: int, *, compact: bool = False
) -> dict:
    all_seasons = seasons_service.list_seasons(db)
    season = None
    if season_id is not None:
        season = next((s for s in all_seasons if s.id == season_id), None)
    if season is None:
        season = seasons_service.current_season(db) or (
            all_seasons[0] if all_seasons else None
        )

    rows = (
        leaderboard_service.leaderboard(db, season.id, min_games=min_games)
        if season
        else []
    )
    total = (
        len(leaderboard_service.leaderboard(db, season.id, min_games=0)) if season else 0
    )
    return {
        "season": season,
        "seasons": all_seasons,
        "rows": rows,
        "min_games": min_games,
        "hidden_count": total - len(rows),
        "compact": compact,
    }


@router.get("/", response_class=HTMLResponse)
def leaderboard_page(
    request: Request,
    season_id: int | None = None,
    min_games: int = Query(default=DEFAULT_MIN_GAMES, ge=0),
    db: Session = Depends(get_db),
):
    ctx = leaderboard_context(db, season_id, min_games)
    return templates.TemplateResponse(request, "leaderboard.html", ctx)


@router.get("/panel/leaderboard", response_class=HTMLResponse)
def leaderboard_panel(
    request: Request,
    season_id: int | None = None,
    min_games: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """Standings for the slide-out drawer.

    Rendered into the drawer only, so an organizer can check the table without
    losing a team assignment they are part way through.
    """
    ctx = leaderboard_context(db, season_id, min_games, compact=True)
    return templates.TemplateResponse(request, "partials/leaderboard_table.html", ctx)


@router.get("/players/{player_id}", response_class=HTMLResponse)
def player_page(
    request: Request,
    player_id: int,
    season_id: int | None = None,
    db: Session = Depends(get_db),
):
    player = db.get(Player, player_id)
    if player is None:
        raise HTTPException(status_code=404, detail=f"player {player_id} does not exist")

    summaries = leaderboard_service.player_seasons(db, player_id)
    chosen = None
    if season_id is not None:
        chosen = next((s for s in summaries if s.season.id == season_id), None)
    if chosen is None:
        chosen = summaries[0] if summaries else None

    history = (
        leaderboard_service.rating_history(db, player_id, chosen.season.id)
        if chosen
        else []
    )
    points = [
        {
            "game": index + 1,
            "mu": round(row.mu_after, 2),
            "sigma": round(row.sigma_after, 2),
            "rating": display_rating(Rating(row.mu_after, row.sigma_after)),
        }
        for index, row in enumerate(history)
    ]
    if history:
        first = history[0]
        points.insert(
            0,
            {
                "game": 0,
                "mu": round(first.mu_before, 2),
                "sigma": round(first.sigma_before, 2),
                "rating": display_rating(Rating(first.mu_before, first.sigma_before)),
            },
        )

    return templates.TemplateResponse(
        request,
        "player.html",
        {
            "player": player,
            "summaries": summaries,
            "current": chosen,
            "points": points,
            "appearances": (
                leaderboard_service.player_games(db, player_id, chosen.season.id)
                if chosen
                else []
            ),
            "all_time": leaderboard_service.all_time_record(db, player_id),
        },
    )


# --- organizer pages -------------------------------------------------------------


@router.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "admin_home.html",
        {
            "sessions": sessions_service.list_sessions(db),
            "seasons": seasons_service.list_seasons(db),
            "current_season": seasons_service.current_season(db),
            "today": date_type.today(),
        },
    )


@router.post("/admin/seasons", response_class=HTMLResponse)
def create_season(
    request: Request,
    name: str = Form(...),
    start_date: date_type = Form(...),
    db: Session = Depends(get_db),
):
    """Minimal season bootstrap. The full seasons screen is milestone 7."""
    try:
        seasons_service.create_season(db, name, start_date)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "admin_home.html",
            {
                "sessions": sessions_service.list_sessions(db),
                "seasons": seasons_service.list_seasons(db),
                "current_season": seasons_service.current_season(db),
                "today": date_type.today(),
                "error": str(exc),
            },
            status_code=400,
        )
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/session/new", response_class=HTMLResponse)
def new_session_form(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        request,
        "session_new.html",
        {"today": date_type.today(), "current_season": seasons_service.current_season(db)},
    )


@router.post("/admin/session/new")
def create_session(
    request: Request,
    date: date_type = Form(...),
    notes: str = Form(default=""),
    db: Session = Depends(get_db),
):
    try:
        session = sessions_service.create_session(db, date, notes=notes.strip() or None)
    except seasons_service.NoSeasonError as exc:
        return templates.TemplateResponse(
            request,
            "session_new.html",
            {"today": date_type.today(), "current_season": None, "error": str(exc)},
            status_code=400,
        )
    return RedirectResponse(f"/admin/session/{session.id}", status_code=303)


@router.get("/admin/session/{session_id}", response_class=HTMLResponse)
def session_detail(request: Request, session_id: int, db: Session = Depends(get_db)):
    session = load_session(db, session_id)
    ctx = board_context(db, session)
    ctx["request"] = request
    return templates.TemplateResponse(request, "session_detail.html", ctx)


@router.get("/admin/session/{session_id}/search", response_class=HTMLResponse)
def player_search(
    request: Request, session_id: int, q: str = "", db: Session = Depends(get_db)
):
    """Filter the check-in list. Empty query restores the full list."""
    session = load_session(db, session_id)
    return templates.TemplateResponse(
        request,
        "partials/checkin_list.html",
        {
            "session": session,
            "candidates": checkin_candidates(db, session, q),
            "query": q.strip(),
            "rating_of": rating_lookup(db, session.season_id),
        },
    )


@router.post("/admin/session/{session_id}/checkin", response_class=HTMLResponse)
def check_in(
    request: Request,
    session_id: int,
    player_id: int = Form(...),
    db: Session = Depends(get_db),
):
    session = load_session(db, session_id)
    try:
        sessions_service.check_in(db, session_id, player_id)
    except (LookupError, ValueError) as exc:
        return render_board(request, db, session, error=str(exc), status_code=400)
    return render_board(request, db, session)


@router.post("/admin/session/{session_id}/checkout", response_class=HTMLResponse)
def check_out(
    request: Request,
    session_id: int,
    player_id: int = Form(...),
    db: Session = Depends(get_db),
):
    session = load_session(db, session_id)
    try:
        sessions_service.check_out(db, session_id, player_id)
    except LookupError as exc:
        return render_board(request, db, session, error=str(exc), status_code=400)
    return render_board(request, db, session)


@router.post("/admin/session/{session_id}/players", response_class=HTMLResponse)
def add_player(
    request: Request,
    session_id: int,
    name: str = Form(...),
    force: bool = Form(default=False),
    db: Session = Depends(get_db),
):
    """Create a player and check them in, warning on a near-duplicate name."""
    session = load_session(db, session_id)
    try:
        player = players_service.create_player(db, name, force=force)
    except players_service.DuplicatePlayerError as exc:
        # Rendered inside the board so the organizer keeps the roster in view.
        return render_board(
            request,
            db,
            session,
            duplicate_name=name.strip(),
            duplicate_matches=exc.matches,
            status_code=409,
        )
    except ValueError as exc:
        return render_board(request, db, session, error=str(exc), status_code=400)

    sessions_service.check_in(db, session_id, player.id)
    return render_board(request, db, session, notice=f"Added {player.name} and checked them in.")


# --- team balancing --------------------------------------------------------------


def render_record_card(
    request: Request,
    db: Session,
    session: LeagueSession,
    assignment: dict[int, int],
    *,
    notice: str | None = None,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "partials/record_form.html",
        {
            "session": session,
            "present": [
                sp
                for sp in sessions_service.session_roster(db, session.id)
                if sp.checked_out_at is None
            ],
            "assignment": assignment,
            "matchup": matchup(db, session, assignment),
            "rating_of": rating_lookup(db, session.season_id),
            "balance_notice": notice,
        },
    )


@router.post("/admin/session/{session_id}/balance", response_class=HTMLResponse)
def balance_teams(request: Request, session_id: int, db: Session = Depends(get_db)):
    """Split the checked-in players into two balanced teams (design doc section 5).

    Chosen at random from the closest few splits, and biased against repeating
    this session's pairings, so the same group does not get the same teams
    every round. The organizer can still move anyone by hand afterwards.
    """
    session = load_session(db, session_id)
    present = sessions_service.checked_in_players(db, session_id)
    if len(present) < 2:
        return render_record_card(
            request, db, session, {}, notice="Check in at least two players first."
        )

    player_ids = [p.id for p in present]
    ratings = leaderboard_service.current_ratings(db, session.season_id, player_ids)
    split = teams_service.generate_teams(
        player_ids,
        ratings,
        history=session_history(db, session_id),
        team_config=TeamGenConfig(),
        rng=random.Random(),
    )
    assignment = {
        pid: index for index, roster in enumerate(split.teams) for pid in roster
    }
    return render_record_card(request, db, session, assignment)


@router.post("/admin/session/{session_id}/preview", response_class=HTMLResponse)
async def preview_matchup(request: Request, session_id: int, db: Session = Depends(get_db)):
    """Recompute the team strengths as the organizer moves players around."""
    session = load_session(db, session_id)
    form = await request.form()
    return templates.TemplateResponse(
        request,
        "partials/balance_panel.html",
        {"matchup": matchup(db, session, parse_assignments(form))},
    )


# --- recording results -----------------------------------------------------------


def build_teams(
    assignments: dict[int, int], winner: int, score_a: str, score_b: str
) -> list[games_service.TeamInput]:
    team_players: dict[int, list[int]] = {0: [], 1: []}
    for player_id, team_index in sorted(assignments.items()):
        team_players[team_index].append(player_id)

    def parse_score(raw: str) -> int | None:
        raw = (raw or "").strip()
        if not raw:
            return None
        try:
            return int(raw)
        except ValueError as exc:
            raise ValueError(f"score {raw!r} is not a number") from exc

    scores = [parse_score(score_a), parse_score(score_b)]
    return [
        games_service.TeamInput(
            player_ids=team_players[i], rank=1 if i == winner else 2, score=scores[i]
        )
        for i in (0, 1)
    ]


@router.post("/admin/session/{session_id}/games", response_class=HTMLResponse)
async def record_result(request: Request, session_id: int, db: Session = Depends(get_db)):
    session = load_session(db, session_id)
    form = await request.form()
    assignments = parse_assignments(form)
    winner_raw = form.get("winner")

    if winner_raw not in ("0", "1"):
        return render_board(
            request,
            db,
            session,
            error="Pick the winning team.",
            assignment=assignments,
            status_code=400,
        )

    try:
        teams = build_teams(
            assignments, int(winner_raw), form.get("score_0", ""), form.get("score_1", "")
        )
        on_field_raw = (form.get("players_on_field") or "").strip()
        game = games_service.record_game(
            db,
            session_id,
            teams,
            players_on_field=int(on_field_raw) if on_field_raw else None,
        )
    except ValueError as exc:
        # Keep the assignment so the organizer only has to fix what was wrong.
        return render_board(
            request, db, session, error=str(exc), assignment=assignments, status_code=400
        )

    return render_board(request, db, session, notice=f"Recorded round {game.round_number}.")


@router.post("/admin/games/{game_id}/delete", response_class=HTMLResponse)
def delete_game(request: Request, game_id: int, db: Session = Depends(get_db)):
    try:
        game = games_service.get_game(db, game_id)
        session = game.session
        games_service.delete_game(db, game_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        return render_board(request, db, session, error=str(exc), status_code=400)
    return render_board(request, db, session, notice="Round deleted. Ratings replayed.")


@router.post("/admin/games/{game_id}/restore", response_class=HTMLResponse)
def restore_game(request: Request, game_id: int, db: Session = Depends(get_db)):
    try:
        game = games_service.get_game(db, game_id)
        session = game.session
        games_service.restore_game(db, game_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        return render_board(request, db, session, error=str(exc), status_code=400)
    return render_board(request, db, session, notice="Round restored.")


def edit_context(db: Session, game, assignments: dict[int, int] | None = None) -> dict:
    game_teams = sorted(game.teams, key=lambda t: t.team_index)
    assignment = assignments or {
        pid: t.team_index for t in game_teams for pid in t.player_ids
    }
    candidates = {p.id: p for p in sessions_service.checked_in_players(db, game.session_id)}
    for team in game_teams:
        for pid in team.player_ids:
            candidates.setdefault(pid, db.get(Player, pid))
    return {
        "game": game,
        "session": game.session,
        "teams": game_teams,
        "assignment": assignment,
        "candidates": sorted(candidates.values(), key=lambda p: p.name),
        "rating_of": rating_lookup(db, game.session.season_id),
    }


@router.get("/admin/games/{game_id}/edit", response_class=HTMLResponse)
def edit_game_form(request: Request, game_id: int, db: Session = Depends(get_db)):
    try:
        game = games_service.get_game(db, game_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    ctx = edit_context(db, game)
    ctx["winner_index"] = next(t.team_index for t in ctx["teams"] if t.rank == 1)
    return templates.TemplateResponse(request, "game_edit.html", ctx)


@router.post("/admin/games/{game_id}/edit")
async def apply_game_edit(request: Request, game_id: int, db: Session = Depends(get_db)):
    try:
        game = games_service.get_game(db, game_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    form = await request.form()
    assignments = parse_assignments(form)
    winner_raw = form.get("winner")

    def rerender(message: str):
        ctx = edit_context(db, game, assignments or None)
        ctx["winner_index"] = int(winner_raw) if winner_raw in ("0", "1") else 0
        ctx["error"] = message
        return templates.TemplateResponse(request, "game_edit.html", ctx, status_code=400)

    if winner_raw not in ("0", "1"):
        return rerender("Pick the winning team.")

    try:
        teams = build_teams(
            assignments, int(winner_raw), form.get("score_0", ""), form.get("score_1", "")
        )
        on_field_raw = (form.get("players_on_field") or "").strip()
        games_service.edit_game(
            db,
            game_id,
            teams=teams,
            players_on_field=int(on_field_raw) if on_field_raw else None,
        )
    except ValueError as exc:
        return rerender(str(exc))

    return RedirectResponse(f"/admin/session/{game.session_id}", status_code=303)
