"""Fuzzy search and duplicate prevention (design doc section 6.1)."""

from __future__ import annotations

import pytest

from mini_league.models import Player
from mini_league.players import (
    DUPLICATE_THRESHOLD,
    DuplicatePlayerError,
    create_player,
    find_duplicates,
    list_players,
    normalize,
    search_players,
    similarity,
)


def test_normalize_collapses_case_and_whitespace():
    assert normalize("  Justin   M.  ") == "justin m."
    assert normalize("JUSTIN") == normalize("justin")


@pytest.mark.parametrize(
    "a, b",
    [
        ("Justin", "justin"),
        ("Justin", "Justin M."),  # the exact case from the design doc
        ("Justin M.", "Justin"),
        ("Jon", "John"),
        ("Sara", "Sarah"),
        ("Mike", "Mike "),
    ],
)
def test_names_that_should_be_flagged_as_duplicates(a, b):
    assert similarity(a, b) >= DUPLICATE_THRESHOLD


@pytest.mark.parametrize(
    "a, b",
    [
        ("Justin", "Priya"),
        ("Tom", "Alex"),
        ("Chris", "Katie"),
    ],
)
def test_clearly_different_names_are_not_duplicates(a, b):
    assert similarity(a, b) < DUPLICATE_THRESHOLD


def test_similarity_is_symmetric_and_bounded():
    assert similarity("Justin", "Justin M.") == similarity("Justin M.", "Justin")
    assert similarity("Ada", "Ada") == 1.0
    assert similarity("", "Ada") == 0.0


def test_create_player_refuses_near_duplicate(db):
    create_player(db, "Justin M.")
    with pytest.raises(DuplicatePlayerError) as excinfo:
        create_player(db, "Justin")
    assert [m.player.name for m in excinfo.value.matches] == ["Justin M."]
    assert "check them in instead" in str(excinfo.value)
    assert db.query(Player).count() == 1


def test_create_player_allows_duplicate_when_forced(db):
    create_player(db, "Justin M.")
    forced = create_player(db, "Justin", force=True)
    assert forced.id is not None
    assert db.query(Player).count() == 2


def test_force_still_cannot_duplicate_an_active_name(db):
    create_player(db, "Ada")
    with pytest.raises(ValueError, match="already named"):
        create_player(db, "Ada", force=True)


def test_creating_a_clearly_new_name_needs_no_force(db):
    create_player(db, "Justin M.")
    priya = create_player(db, "Priya")
    assert priya.id is not None


def test_blank_name_rejected(db):
    with pytest.raises(ValueError, match="name is required"):
        create_player(db, "   ")


def test_name_is_trimmed(db):
    player = create_player(db, "  Ada  ")
    assert player.name == "Ada"


def test_search_ranks_exact_match_first(db):
    create_player(db, "Ada Lovelace")
    create_player(db, "Ada", force=True)  # deliberately a near-duplicate
    results = search_players(db, "Ada")
    assert results[0].player.name == "Ada"
    assert results[0].score == 1.0
    assert {m.player.name for m in results} == {"Ada", "Ada Lovelace"}


def test_search_includes_inactive_players(db):
    retired = create_player(db, "Gus")
    retired.active = False
    db.commit()
    results = search_players(db, "Gus")
    assert [m.player.name for m in results] == ["Gus"]
    assert search_players(db, "Gus", include_inactive=False) == []


def test_search_empty_query_returns_nothing(db):
    create_player(db, "Ada")
    assert search_players(db, "") == []
    assert search_players(db, "   ") == []


def test_search_excludes_unrelated_names(db):
    create_player(db, "Ada")
    create_player(db, "Priya")
    assert [m.player.name for m in search_players(db, "Ada")] == ["Ada"]


def test_search_respects_limit(db):
    for i in range(8):
        create_player(db, f"Sam {i}", force=True)
    assert len(search_players(db, "Sam", limit=3)) == 3


def test_find_duplicates_only_returns_close_matches(db):
    create_player(db, "Justin M.")
    create_player(db, "Priya")
    assert [m.player.name for m in find_duplicates(db, "Justin")] == ["Justin M."]
    assert find_duplicates(db, "Priyanka") != []  # shares a prefix, worth warning
    assert find_duplicates(db, "Wanda") == []


def test_list_players_hides_inactive_by_default(db):
    create_player(db, "Ada")
    gone = create_player(db, "Gus")
    gone.active = False
    db.commit()
    assert [p.name for p in list_players(db)] == ["Ada"]
    assert [p.name for p in list_players(db, include_inactive=True)] == ["Ada", "Gus"]
