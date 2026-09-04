"""WMP and MMP designations (design doc section 5.4).

The load-bearing claim these tests defend is that a designation is matchmaking
information and nothing else: it moves people between sides and never reaches a
rating.
"""

from __future__ import annotations

import random

import pytest
from trueskill import Rating

from mini_league import designations
from mini_league.designations import MMP, NONE, WMP, UnknownDesignationError
from mini_league.games import TeamInput, record_game
from mini_league.leaderboard import leaderboard
from mini_league.players import create_player, set_designation
from mini_league.sessions import (
    check_in,
    clear_session_designation,
    session_designations,
    set_session_designation,
)
from mini_league.settings import TeamGenConfig
from mini_league.teams import candidate_splits, generate_teams


# --- parsing and resolution ------------------------------------------------------


@pytest.mark.parametrize("raw", ["WMP", "wmp", "  Wmp  "])
def test_parse_accepts_any_casing(raw):
    assert designations.parse(raw) == WMP


@pytest.mark.parametrize("raw", ["", "   ", None, "-", "none"])
def test_parse_treats_blank_as_no_designation(raw):
    assert designations.parse(raw) is None


def test_parse_refuses_anything_else():
    with pytest.raises(UnknownDesignationError, match="not a designation"):
        designations.parse("WNP")


def test_an_override_can_say_none_which_blank_cannot():
    """The whole reason the override column has a third value."""
    assert designations.parse_override("none") == NONE
    assert designations.parse_override("") is None
    assert designations.parse_override("MMP") == MMP


def test_resolution_prefers_the_day_over_the_player():
    assert designations.resolve(WMP, None) == WMP  # no override: theirs stands
    assert designations.resolve(WMP, MMP) == MMP  # today they match up the other way
    assert designations.resolve(WMP, NONE) is None  # today they have none
    assert designations.resolve(None, WMP) == WMP  # today they have one


def test_counts_bucket_the_undesignated_separately():
    assert designations.counts([WMP, WMP, MMP, None]) == {"WMP": 2, "MMP": 1, "none": 1}


# --- the cost term ---------------------------------------------------------------


def test_an_even_split_costs_nothing():
    held = {1: WMP, 2: MMP, 3: WMP, 4: MMP}
    assert designations.imbalance([[1, 2], [3, 4]], held) == 0.0


def test_a_lopsided_split_costs_more_than_a_close_one():
    held = {1: WMP, 2: WMP, 3: MMP, 4: MMP}
    worst = designations.imbalance([[1, 2], [3, 4]], held)
    better = designations.imbalance([[1, 3], [2, 4]], held)
    assert worst > better == 0.0
    assert 0.0 < worst <= 1.0


def test_both_designations_are_counted_not_just_one():
    """With undesignated players, even WMPs do not imply even MMPs."""
    held = {1: WMP, 2: MMP, 3: WMP, 4: None}
    # One WMP each way, but one side carries the MMP and the other the unmarked
    # player, so this is not actually an even coed split.
    assert designations.imbalance([[1, 2], [3, 4]], held) > 0.0


def test_players_with_no_designation_at_all_cost_nothing():
    held = {1: None, 2: None, 3: None, 4: None}
    assert designations.imbalance([[1, 2], [3, 4]], held) == 0.0


# --- team generation -------------------------------------------------------------


def even_ratings(player_ids):
    """Identical ratings, so only the designation term can decide the split."""
    return {pid: Rating(25.0, 2.0) for pid in player_ids}


def test_asking_for_an_even_split_gets_one():
    players = [1, 2, 3, 4, 5, 6]
    held = {1: WMP, 2: WMP, 3: MMP, 4: MMP, 5: MMP, 6: MMP}

    for seed in range(8):  # the generator picks at random among the best few
        split = generate_teams(
            players,
            even_ratings(players),
            designations=held,
            rng=random.Random(seed),
        )
        for roster in split.teams:
            assert sum(1 for pid in roster if held[pid] == WMP) == 1
            assert sum(1 for pid in roster if held[pid] == MMP) == 2


def test_an_odd_count_is_split_as_evenly_as_it_goes():
    """Three WMPs cannot go two and two; one side has to carry the extra."""
    players = [1, 2, 3, 4, 5, 6]
    held = {1: WMP, 2: WMP, 3: WMP, 4: MMP, 5: MMP, 6: MMP}

    split = generate_teams(
        players, even_ratings(players), designations=held, rng=random.Random(1)
    )
    per_team = sorted(
        sum(1 for pid in roster if held[pid] == WMP) for roster in split.teams
    )
    assert per_team == [1, 2]


def test_not_asking_leaves_the_term_out_entirely():
    """A round that never mentions designations is scored exactly as before."""
    players = [1, 2, 3, 4]
    ratings = even_ratings(players)
    held = {1: WMP, 2: WMP, 3: MMP, 4: MMP}

    without = candidate_splits(players, ratings)
    with_them = candidate_splits(players, ratings, designations=held)

    assert all(s.designation_cost == 0.0 for s in without)
    assert any(s.designation_cost > 0.0 for s in with_them)
    # And with the term absent, every split still costs what it always did.
    assert {s.total_cost for s in without} == {
        s.balance_cost * 1.0 + s.variety_cost * 0.3 for s in without
    }


def test_the_term_can_be_turned_down_to_nothing():
    players = [1, 2, 3, 4]
    held = {1: WMP, 2: WMP, 3: MMP, 4: MMP}
    config = TeamGenConfig(w_designation=0.0)

    splits = candidate_splits(
        players, even_ratings(players), designations=held, team_config=config
    )
    assert any(s.designation_cost > 0 for s in splits)
    assert {round(s.total_cost, 9) for s in splits} == {
        round(s.balance_cost + 0.3 * s.variety_cost, 9) for s in splits
    }


def test_balance_still_outranks_an_even_coed_split():
    """Designations must not be allowed to force a lopsided game."""
    players = [1, 2, 3, 4]
    held = {1: WMP, 2: WMP, 3: MMP, 4: MMP}
    # The two WMPs are far and away the strongest, so splitting them is both the
    # balanced answer and the coed one; make them uneven and balance must win.
    ratings = {
        1: Rating(40.0, 1.0),
        2: Rating(24.0, 1.0),
        3: Rating(40.0, 1.0),
        4: Rating(24.0, 1.0),
    }
    best = candidate_splits(players, ratings, designations=held)[0]
    strong = {1, 3}
    assert len(strong & set(best.teams[0])) == 1  # one strong player each way


# --- through the database --------------------------------------------------------


def test_a_player_can_be_created_with_or_without_one(db):
    with_one = create_player(db, "Ada", designation=WMP)
    without = create_player(db, "Ben")
    assert with_one.designation == WMP
    assert without.designation is None


def test_setting_a_standing_designation_is_logged(db):
    from mini_league.models import AuditLog

    player = create_player(db, "Ada")
    set_designation(db, player.id, WMP)
    set_designation(db, player.id, "")

    assert player.designation is None
    actions = [row.action for row in db.query(AuditLog).all()]
    assert actions == ["set_designation", "set_designation"]


def test_setting_the_same_designation_again_logs_nothing(db):
    from mini_league.models import AuditLog

    player = create_player(db, "Ada", designation=WMP)
    set_designation(db, player.id, WMP)
    assert db.query(AuditLog).count() == 0


def test_a_session_override_beats_the_standing_one(db, league_session):
    player = create_player(db, "Ada", designation=WMP)
    check_in(db, league_session.id, player.id)

    entry = set_session_designation(db, league_session.id, player.id, MMP)
    assert entry.designation == MMP
    assert entry.designation_is_for_today
    assert player.designation == WMP  # their record is untouched


def test_an_override_can_remove_a_designation_for_the_day(db, league_session):
    player = create_player(db, "Ada", designation=WMP)
    check_in(db, league_session.id, player.id)

    entry = set_session_designation(db, league_session.id, player.id, "none")
    assert entry.designation_override == NONE
    assert entry.designation is None
    assert entry.designation_is_for_today

    # Clearing the override is a different thing again: it hands them back.
    entry = clear_session_designation(db, league_session.id, player.id)
    assert entry.designation_override is None
    assert entry.designation == WMP
    assert not entry.designation_is_for_today


def test_session_designations_reads_the_effective_value(db, league_session):
    ada = create_player(db, "Ada", designation=WMP)
    ben = create_player(db, "Ben", designation=MMP)
    cleo = create_player(db, "Cleo")
    for player in (ada, ben, cleo):
        check_in(db, league_session.id, player.id)
    set_session_designation(db, league_session.id, ben.id, "none")
    set_session_designation(db, league_session.id, cleo.id, WMP)

    assert session_designations(db, league_session.id) == {
        ada.id: WMP,
        ben.id: None,
        cleo.id: WMP,
    }


def test_designations_do_not_touch_ratings(db, season, league_session):
    """The claim the whole feature rests on."""
    ada = create_player(db, "Ada", designation=WMP)
    ben = create_player(db, "Ben", designation=MMP)
    for player in (ada, ben):
        check_in(db, league_session.id, player.id)
    record_game(
        db,
        league_session.id,
        [TeamInput([ada.id], rank=1), TeamInput([ben.id], rank=2)],
    )
    before = {row.player.name: (row.mu, row.sigma) for row in leaderboard(db, season.id)}

    set_designation(db, ada.id, MMP)
    set_session_designation(db, league_session.id, ben.id, "none")

    after = {row.player.name: (row.mu, row.sigma) for row in leaderboard(db, season.id)}
    assert before == after
