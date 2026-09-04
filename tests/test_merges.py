"""Player management: rename, deactivate, merge duplicates, undo (design doc 6.1)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from mini_league.games import TeamInput, record_game
from mini_league.merges import (
    MergeConflictError,
    audit_entries,
    is_merge_undone,
    merge_candidates,
    merge_players,
    plan_merge,
    rename_player,
    set_player_active,
    undo_merge,
)
from mini_league.models import (
    AuditLog,
    GameTeamPlayer,
    Player,
    PlayerSeasonRating,
    SessionPlayer,
)
from mini_league.players import create_player
from mini_league.sessions import check_in


def snapshot(db, player_id, season_id):
    return db.get(PlayerSeasonRating, (player_id, season_id))


def play(db, session_id, winners, losers, score=None):
    return record_game(
        db,
        session_id,
        [
            TeamInput([p.id for p in winners], 1, score[0] if score else None),
            TeamInput([p.id for p in losers], 2, score[1] if score else None),
        ],
    )


# --- rename ----------------------------------------------------------------------


def test_rename_keeps_all_history(db, season, league_session, make_players):
    a, b = make_players(2)
    play(db, league_session.id, [a], [b])
    before = snapshot(db, a.id, season.id).mu

    rename_player(db, a.id, "Renamed")
    db.refresh(a)
    assert a.name == "Renamed"
    assert snapshot(db, a.id, season.id).mu == pytest.approx(before)
    assert snapshot(db, a.id, season.id).games_played == 1


def test_rename_trims_and_rejects_blank(db, make_players):
    (a,) = make_players(1)
    assert rename_player(db, a.id, "  Spaced  ").name == "Spaced"
    with pytest.raises(ValueError, match="name is required"):
        rename_player(db, a.id, "   ")


def test_rename_cannot_collide_with_an_active_player(db, make_players):
    a, b = make_players(2)
    with pytest.raises(ValueError, match="already named"):
        rename_player(db, a.id, b.name)


def test_rename_may_reuse_an_inactive_name(db, make_players):
    a, b = make_players(2)
    set_player_active(db, b.id, False)
    assert rename_player(db, a.id, b.name).name == b.name


def test_rename_writes_an_audit_entry(db, make_players):
    (a,) = make_players(1)
    original = a.name
    rename_player(db, a.id, "New Name")
    entry = db.scalars(select(AuditLog).where(AuditLog.action == "rename_player")).one()
    assert entry.payload == {"player_id": a.id, "before": original, "after": "New Name"}


# --- activation ------------------------------------------------------------------


def test_deactivate_and_reactivate(db, season, league_session, make_players):
    a, b = make_players(2)
    play(db, league_session.id, [a], [b])

    set_player_active(db, a.id, False)
    db.refresh(a)
    assert a.active is False
    assert snapshot(db, a.id, season.id) is not None, "history is preserved"

    set_player_active(db, a.id, True)
    db.refresh(a)
    assert a.active is True


def test_reactivating_into_a_name_clash_is_refused(db, make_players):
    (a,) = make_players(1)
    set_player_active(db, a.id, False)
    create_player(db, a.name, force=True)
    with pytest.raises(ValueError, match="rename one of them first"):
        set_player_active(db, a.id, True)


def test_a_merged_player_cannot_simply_be_reactivated(db, make_players):
    a, b = make_players(2)
    merge_players(db, a.id, b.id)
    with pytest.raises(ValueError, match="undo the merge instead"):
        set_player_active(db, a.id, True)


# --- merge planning --------------------------------------------------------------


def test_plan_reports_what_would_move(db, league_session, make_players):
    dup, keep, other = make_players(3)
    play(db, league_session.id, [dup], [other])
    check_in(db, league_session.id, keep.id)

    plan = plan_merge(db, dup.id, keep.id)
    assert plan.is_safe
    assert len(plan.moved_game_teams) == 1
    assert plan.removed_game_teams == []
    assert plan.games_affected == 1


def test_plan_detects_players_who_faced_each_other(db, league_session, make_players):
    a, b = make_players(2)
    game = play(db, league_session.id, [a], [b])
    plan = plan_merge(db, a.id, b.id)
    assert plan.is_safe is False
    assert plan.conflicts == [game.id]


def test_plan_spots_a_player_listed_twice_on_one_team(db, league_session, make_players):
    dup, keep, other = make_players(3)
    play(db, league_session.id, [dup, keep], [other])
    plan = plan_merge(db, dup.id, keep.id)
    assert plan.is_safe
    assert len(plan.removed_game_teams) == 1
    assert plan.moved_game_teams == []


# --- merge -----------------------------------------------------------------------


def test_merge_combines_the_game_history(db, season, league_session, make_players):
    dup, keep, x, y = make_players(4)
    play(db, league_session.id, [dup], [x])
    play(db, league_session.id, [keep], [y])

    assert snapshot(db, keep.id, season.id).games_played == 1
    merge_players(db, dup.id, keep.id)

    combined = snapshot(db, keep.id, season.id)
    assert combined.games_played == 2
    assert combined.wins == 2
    assert snapshot(db, dup.id, season.id) is None, "the duplicate holds no rating"


def test_merge_marks_the_source_and_leaves_the_target_alone(db, make_players):
    dup, keep = make_players(2)
    merge_players(db, dup.id, keep.id)
    db.refresh(dup)
    db.refresh(keep)
    assert dup.active is False
    assert dup.merged_into == keep.id
    assert keep.active is True
    assert keep.merged_into is None


def test_merge_refuses_players_who_played_against_each_other(db, league_session, make_players):
    a, b = make_players(2)
    play(db, league_session.id, [a], [b])
    with pytest.raises(MergeConflictError, match="played against each other"):
        merge_players(db, a.id, b.id)
    db.refresh(a)
    assert a.merged_into is None, "nothing changed"


def test_merge_collapses_a_double_listing_on_one_team(
    db, season, league_session, make_players
):
    dup, keep, x, y = make_players(4)
    play(db, league_session.id, [dup, keep], [x, y])

    merge_players(db, dup.id, keep.id)
    rows = db.scalars(select(GameTeamPlayer)).all()
    keep_rows = [r for r in rows if r.player_id == keep.id]
    assert len(keep_rows) == 1, "one roster slot, not two"
    # A 2v2 becomes a 1v2; the game itself still replays cleanly.
    assert snapshot(db, keep.id, season.id).games_played == 1


def test_merge_combines_session_checkins(db, league_session, make_players):
    dup, keep = make_players(2)
    check_in(db, league_session.id, dup.id)
    check_in(db, league_session.id, keep.id)

    merge_players(db, dup.id, keep.id)
    rows = db.scalars(
        select(SessionPlayer).where(SessionPlayer.session_id == league_session.id)
    ).all()
    assert [r.player_id for r in rows] == [keep.id]


def test_merge_moves_a_checkin_the_target_did_not_have(db, league_session, make_players):
    dup, keep = make_players(2)
    check_in(db, league_session.id, dup.id)

    merge_players(db, dup.id, keep.id)
    rows = db.scalars(
        select(SessionPlayer).where(SessionPlayer.session_id == league_session.id)
    ).all()
    assert [r.player_id for r in rows] == [keep.id]


def test_merge_validation(db, make_players):
    a, b, c = make_players(3)
    with pytest.raises(ValueError, match="merged into themselves"):
        merge_players(db, a.id, a.id)
    with pytest.raises(LookupError, match="player 999"):
        merge_players(db, 999, a.id)

    merge_players(db, a.id, b.id)
    with pytest.raises(ValueError, match="already been merged"):
        merge_players(db, a.id, c.id)
    with pytest.raises(ValueError, match="itself been merged"):
        merge_players(db, c.id, a.id)


def test_merge_writes_a_reversible_audit_entry(db, league_session, make_players):
    dup, keep, other = make_players(3)
    play(db, league_session.id, [dup], [other])
    check_in(db, league_session.id, dup.id)

    entry = merge_players(db, dup.id, keep.id)
    assert entry.action == "merge_players"
    assert entry.payload["source"]["id"] == dup.id
    assert entry.payload["source"]["name"] == dup.name
    assert entry.payload["target"]["id"] == keep.id
    assert len(entry.payload["moved_game_teams"]) == 1
    assert entry.payload["moved_sessions"] == [league_session.id]


# --- undo ------------------------------------------------------------------------


def test_undo_restores_ratings_exactly(db, season, league_session, make_players):
    dup, keep, x, y = make_players(4)
    play(db, league_session.id, [dup], [x])
    play(db, league_session.id, [keep], [y])

    before = {
        p.id: (snapshot(db, p.id, season.id).mu, snapshot(db, p.id, season.id).sigma)
        for p in (dup, keep, x, y)
    }

    entry = merge_players(db, dup.id, keep.id)
    assert snapshot(db, keep.id, season.id).games_played == 2

    undo_merge(db, entry.id)
    for player_id, (mu, sigma) in before.items():
        after = snapshot(db, player_id, season.id)
        assert after.mu == pytest.approx(mu)
        assert after.sigma == pytest.approx(sigma)
        assert after.games_played == 1


def test_undo_restores_the_player_record(db, make_players):
    dup, keep = make_players(2)
    entry = merge_players(db, dup.id, keep.id)
    undo_merge(db, entry.id)
    db.refresh(dup)
    assert dup.active is True
    assert dup.merged_into is None


def test_undo_restores_collapsed_rows(db, league_session, make_players):
    dup, keep, x, y = make_players(4)
    play(db, league_session.id, [dup, keep], [x, y])
    check_in(db, league_session.id, dup.id)
    check_in(db, league_session.id, keep.id)

    entry = merge_players(db, dup.id, keep.id)
    undo_merge(db, entry.id)

    team_rows = db.scalars(select(GameTeamPlayer)).all()
    assert sorted(r.player_id for r in team_rows) == sorted([dup.id, keep.id, x.id, y.id])
    session_rows = db.scalars(
        select(SessionPlayer).where(SessionPlayer.session_id == league_session.id)
    ).all()
    assert sorted(r.player_id for r in session_rows) == sorted([dup.id, keep.id])


def test_undo_preserves_the_original_checkin_times(db, league_session, make_players):
    dup, keep = make_players(2)
    entry_in = check_in(db, league_session.id, dup.id)
    original = entry_in.checked_in_at
    check_in(db, league_session.id, keep.id)

    merge_entry = merge_players(db, dup.id, keep.id)
    undo_merge(db, merge_entry.id)

    restored = db.get(SessionPlayer, (league_session.id, dup.id))
    assert restored is not None
    assert restored.checked_in_at == original


def test_a_merge_cannot_be_undone_twice(db, make_players):
    dup, keep = make_players(2)
    entry = merge_players(db, dup.id, keep.id)
    assert is_merge_undone(db, entry.id) is False
    undo_merge(db, entry.id)
    assert is_merge_undone(db, entry.id) is True
    with pytest.raises(ValueError, match="already been undone"):
        undo_merge(db, entry.id)


def test_undo_validation(db, make_players):
    (a,) = make_players(1)
    rename_player(db, a.id, "Something")
    rename_entry = db.scalars(
        select(AuditLog).where(AuditLog.action == "rename_player")
    ).one()
    with pytest.raises(ValueError, match="is not a merge"):
        undo_merge(db, rename_entry.id)
    with pytest.raises(LookupError, match="audit entry 999"):
        undo_merge(db, 999)


def test_merge_and_undo_can_be_repeated(db, season, league_session, make_players):
    dup, keep, x = make_players(3)
    play(db, league_session.id, [dup], [x])

    for _ in range(3):
        entry = merge_players(db, dup.id, keep.id)
        assert snapshot(db, keep.id, season.id).games_played == 1
        undo_merge(db, entry.id)
        assert snapshot(db, dup.id, season.id).games_played == 1
        assert snapshot(db, keep.id, season.id) is None


# --- helpers ---------------------------------------------------------------------


def test_merge_candidates_puts_similar_names_first(db):
    justin_m = create_player(db, "Justin M.")
    create_player(db, "Priya")
    justin = create_player(db, "Justin", force=True)

    names = [p.name for p in merge_candidates(db, justin.id)]
    assert names[0] == "Justin M."
    assert "Justin" not in names, "a player is never their own merge target"


def test_merge_candidates_exclude_already_merged_players(db, make_players):
    a, b, c = make_players(3)
    merge_players(db, a.id, b.id)
    assert a.name not in [p.name for p in merge_candidates(db, c.id)]


def test_audit_entries_are_newest_first(db, make_players):
    a, b = make_players(2)
    rename_player(db, a.id, "First Change")
    merge_players(db, a.id, b.id)
    actions = [e.action for e in audit_entries(db)]
    assert actions[0] == "merge_players"
    assert "rename_player" in actions
