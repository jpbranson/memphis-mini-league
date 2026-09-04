"""Milestone 5: format picker, bench, and swap (design doc 5.1, 5.2, 5.3)."""

from __future__ import annotations

import random
import re

import pytest

from mini_league.teams import on_field_for, playing_count_for, select_bench


def text(response) -> str:
    return response.text


def squash(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def check_in_all(client, session_id, player_ids):
    for pid in player_ids:
        client.post(f"/admin/session/{session_id}/checkin", data={"player_id": pid})


def assignments_in(body: str) -> dict[int, str]:
    """Only players actually put on a team; anyone on "Out" is left out."""
    return {
        int(m.group(1)): m.group(2)
        for m in re.finditer(r'name="assign_(\d+)" value="(0|1)"\s*checked', squash(body))
    }


def sitting_out(body: str) -> set[int]:
    return {
        int(m.group(1))
        for m in re.finditer(r'name="assign_(\d+)" value="out"\s*checked', squash(body))
    }


def bench_names(body: str) -> list[str]:
    """The names printed after the "Sitting out" label."""
    match = re.search(r"Sitting out:(.*?)</p>", squash(body), re.S)
    if not match:
        return []
    text_only = re.sub(r"<[^>]+>", " ", match.group(1))
    text_only = re.sub(r"(&mdash;|—).*", "", text_only)
    return [name.strip() for name in text_only.split(",") if name.strip()]


# --- pure helpers ----------------------------------------------------------------


def test_playing_count_auto_uses_everyone():
    assert playing_count_for(9, None) == 9
    assert playing_count_for(4, None) == 4


def test_playing_count_respects_team_size():
    assert playing_count_for(11, 4) == 8
    assert playing_count_for(6, 4) == 6, "never invents players who are not there"
    assert playing_count_for(10, 5) == 10


def test_playing_count_validation():
    with pytest.raises(ValueError, match="at least two players"):
        playing_count_for(1, None)
    with pytest.raises(ValueError, match="team size"):
        playing_count_for(8, 0)


def test_on_field_is_the_smaller_roster_capped():
    assert on_field_for([5, 5], 5) == 5
    assert on_field_for([7, 7], 5) == 5, "big rosters rotate substitutes"
    assert on_field_for([4, 3], 5) == 3, "equal numbers on the field"
    assert on_field_for([7, 7], None) == 7
    with pytest.raises(ValueError):
        on_field_for([5, 5], 0)


def test_bench_prefers_whoever_has_played_most():
    played = {1: 3, 2: 3, 3: 0, 4: 1, 5: 0}
    playing, benched = select_bench([1, 2, 3, 4, 5], 3, played, random.Random(0))
    assert sorted(benched) == [1, 2]
    assert sorted(playing) == [3, 4, 5]


def test_bench_is_empty_when_everyone_fits():
    playing, benched = select_bench([1, 2, 3, 4], 4, {}, random.Random(0))
    assert benched == []
    assert playing == [1, 2, 3, 4]


def test_bench_breaks_ties_at_random_not_by_id():
    """Otherwise the same people would sit out every single round."""
    ids = list(range(1, 9))
    seen = set()
    for seed in range(30):
        _, benched = select_bench(ids, 6, {}, random.Random(seed))
        seen.add(frozenset(benched))
    assert len(seen) > 1


def test_bench_keeps_the_playing_list_in_order():
    playing, _ = select_bench([5, 1, 3, 2], 2, {5: 9, 1: 9}, random.Random(0))
    assert playing == [3, 2]


def test_bench_validation():
    with pytest.raises(ValueError, match="at least two players"):
        select_bench([1, 2, 3], 1, {}, random.Random(0))


# --- format picker through the board ---------------------------------------------


def test_board_offers_the_format_picker(client, page_session, make_api_players):
    ids = make_api_players("A", "B")
    check_in_all(client, page_session, ids)
    body = text(client.get(f"/admin/session/{page_session}"))
    assert 'name="team_size"' in body
    assert 'name="max_on_field"' in body
    assert "Make teams" in body
    assert 'placeholder="all"' in body


def test_auto_puts_everyone_in_a_team(client, page_session, make_api_players):
    ids = make_api_players("A", "B", "C", "D", "E")
    check_in_all(client, page_session, ids)
    r = client.post(
        f"/admin/session/{page_session}/balance", data={"team_size": "", "max_on_field": "5"}
    )
    chosen = assignments_in(text(r))
    assert set(chosen) == set(ids)
    assert "Sitting out:" not in text(r)


def test_team_size_benches_the_extra_players(client, page_session, make_api_players):
    ids = make_api_players("A", "B", "C", "D", "E", "F", "G")
    check_in_all(client, page_session, ids)
    r = client.post(
        f"/admin/session/{page_session}/balance", data={"team_size": "3", "max_on_field": "5"}
    )
    body = text(r)
    chosen = assignments_in(body)
    assert len(chosen) == 6, "three a side"
    assert sorted(chosen.values()) == ["0"] * 3 + ["1"] * 3
    assert "Sitting out:" in body
    assert "most rounds so far" in squash(body)


def test_bench_picks_whoever_has_played_most_today(client, page_session, make_api_players):
    ids = make_api_players("A", "B", "C", "D", "E")
    check_in_all(client, page_session, ids)
    # A and B play a round; with a 2-a-side format they should sit the next one.
    client.post(
        f"/admin/session/{page_session}/games",
        data={f"assign_{ids[0]}": "0", f"assign_{ids[1]}": "1", "winner": "0"},
    )
    r = client.post(
        f"/admin/session/{page_session}/balance", data={"team_size": "2", "max_on_field": "5"}
    )
    body = text(r)
    chosen = assignments_in(body)
    benched = sitting_out(body)
    # Five players at two a side means exactly one sits, and it has to be one
    # of the two who already played.
    assert len(chosen) == 4
    assert len(benched) == 1
    assert benched <= {ids[0], ids[1]}


def test_large_turnout_uses_substitutes_not_a_bench(client, page_session, make_api_players):
    """Twelve players on a five-a-side pitch is 6v6 rosters, nobody sent home."""
    ids = make_api_players(*[f"P{i}" for i in range(12)])
    check_in_all(client, page_session, ids)
    r = client.post(
        f"/admin/session/{page_session}/balance", data={"team_size": "", "max_on_field": "5"}
    )
    body = text(r)
    chosen = assignments_in(body)
    assert len(chosen) == 12
    assert "Sitting out:" not in body


def test_rounds_played_is_shown_next_to_players(client, page_session, make_api_players):
    ids = make_api_players("A", "B", "C")
    check_in_all(client, page_session, ids)
    client.post(
        f"/admin/session/{page_session}/games",
        data={f"assign_{ids[0]}": "0", f"assign_{ids[1]}": "1", "winner": "0"},
    )
    body = squash(text(client.get(f"/admin/session/{page_session}")))
    assert "&times;1" in body or "�1" in body


def test_bad_format_input_is_reported(client, page_session, make_api_players):
    ids = make_api_players("A", "B")
    check_in_all(client, page_session, ids)
    r = client.post(
        f"/admin/session/{page_session}/balance",
        data={"team_size": "lots", "max_on_field": "5"},
    )
    assert "is not a number" in text(r)


def test_format_choices_survive_a_balance(client, page_session, make_api_players):
    ids = make_api_players("A", "B", "C", "D", "E", "F")
    check_in_all(client, page_session, ids)
    body = text(
        client.post(
            f"/admin/session/{page_session}/balance",
            data={"team_size": "2", "max_on_field": "4"},
        )
    )
    assert 'name="team_size" value="2"' in squash(body)
    assert 'name="max_on_field" value="4"' in squash(body)


# --- swap ------------------------------------------------------------------------


def test_swap_control_appears_once_teams_exist(client, page_session, make_api_players):
    ids = make_api_players("A", "B", "C", "D")
    check_in_all(client, page_session, ids)
    assert "Swap a pair" not in text(client.get(f"/admin/session/{page_session}"))

    body = text(client.post(f"/admin/session/{page_session}/balance", data={}))
    assert "Swap a pair" in body
    assert 'name="swap_a"' in body and 'name="swap_b"' in body


def test_swap_exchanges_the_two_players(client, page_session, make_api_players):
    a, b, c, d = make_api_players("A", "B", "C", "D")
    check_in_all(client, page_session, [a, b, c, d])
    r = client.post(
        f"/admin/session/{page_session}/swap",
        data={
            f"assign_{a}": "0",
            f"assign_{b}": "0",
            f"assign_{c}": "1",
            f"assign_{d}": "1",
            "swap_a": str(a),
            "swap_b": str(c),
        },
    )
    assert r.status_code == 200
    chosen = assignments_in(text(r))
    assert chosen[a] == "1" and chosen[c] == "0"
    assert chosen[b] == "0" and chosen[d] == "1"


def test_swap_updates_the_prediction(client, page_session, make_api_players):
    """Moving a strong player across should move the predicted win percentage."""
    strong, weak, x, y = make_api_players("Strong", "Weak", "X", "Y")
    check_in_all(client, page_session, [strong, weak, x, y])
    for _ in range(4):
        client.post(
            f"/admin/session/{page_session}/games",
            data={f"assign_{strong}": "0", f"assign_{x}": "1", "winner": "0"},
        )

    base = {f"assign_{strong}": "0", f"assign_{weak}": "0", f"assign_{x}": "1", f"assign_{y}": "1"}
    before = squash(
        text(client.post(f"/admin/session/{page_session}/preview", data=base))
    )
    after = squash(
        text(
            client.post(
                f"/admin/session/{page_session}/swap",
                data={**base, "swap_a": str(strong), "swap_b": str(x)},
            )
        )
    )
    before_pct = re.search(r"(\d+)&ndash;\d+", before).group(1)
    after_pct = re.search(r"(\d+)&ndash;\d+", after).group(1)
    assert before_pct != after_pct


def test_swap_rejects_two_players_from_the_same_team(client, page_session, make_api_players):
    a, b, c, d = make_api_players("A", "B", "C", "D")
    check_in_all(client, page_session, [a, b, c, d])
    r = client.post(
        f"/admin/session/{page_session}/swap",
        data={
            f"assign_{a}": "0",
            f"assign_{b}": "0",
            f"assign_{c}": "1",
            f"assign_{d}": "1",
            "swap_a": str(a),
            "swap_b": str(b),
        },
    )
    assert "Pick one player from Team A and one from Team B." in text(r)
    chosen = assignments_in(text(r))
    assert chosen[a] == "0" and chosen[b] == "0", "nothing moved"


def test_swap_keeps_the_format_choices(client, page_session, make_api_players):
    a, b, c, d = make_api_players("A", "B", "C", "D")
    check_in_all(client, page_session, [a, b, c, d])
    body = squash(
        text(
            client.post(
                f"/admin/session/{page_session}/swap",
                data={
                    f"assign_{a}": "0",
                    f"assign_{c}": "1",
                    "swap_a": str(a),
                    "swap_b": str(c),
                    "team_size": "2",
                    "max_on_field": "4",
                },
            )
        )
    )
    assert 'name="team_size" value="2"' in body
    assert 'name="max_on_field" value="4"' in body


def test_sitting_out_survives_a_swap(client, page_session, make_api_players):
    """The organizer should not lose track of who is resting after moving people."""
    ids = make_api_players("A", "B", "C", "D", "E")
    check_in_all(client, page_session, ids)
    balanced = text(
        client.post(
            f"/admin/session/{page_session}/balance",
            data={"team_size": "2", "max_on_field": "5"},
        )
    )
    assert "Sitting out:" in balanced
    assignment = assignments_in(balanced)
    team_a = [pid for pid, side in assignment.items() if side == "0"]
    team_b = [pid for pid, side in assignment.items() if side == "1"]

    data = {f"assign_{pid}": side for pid, side in assignment.items()}
    data.update({"swap_a": str(team_a[0]), "swap_b": str(team_b[0]), "team_size": "2"})
    after = text(client.post(f"/admin/session/{page_session}/swap", data=data))
    assert "Sitting out:" in after
    assert sitting_out(after) == sitting_out(balanced)


def test_manual_out_is_listed_without_claiming_it_was_automatic(
    client, page_session, make_api_players
):
    a, b, c = make_api_players("A", "B", "C")
    check_in_all(client, page_session, [a, b, c])
    body = squash(
        text(
            client.post(
                f"/admin/session/{page_session}/swap",
                data={
                    f"assign_{a}": "0",
                    f"assign_{b}": "1",
                    "swap_a": str(a),
                    "swap_b": str(b),
                },
            )
        )
    )
    assert bench_names(body) == ["C"]
    assert "most rounds so far" not in body


# --- the on-field limit actually reaches the recorded game -----------------------


def stored_on_field(client, session_id: int) -> int:
    return client.get(f"/api/sessions/{session_id}").json()["games"][0]["players_on_field"]


def record_balanced(client, session_id, ids, max_on_field="5", team_size=""):
    balanced = text(
        client.post(
            f"/admin/session/{session_id}/balance",
            data={"team_size": team_size, "max_on_field": max_on_field},
        )
    )
    data = {f"assign_{pid}": side for pid, side in assignments_in(balanced).items()}
    data.update({"winner": "0", "max_on_field": max_on_field, "team_size": team_size})
    return client.post(f"/admin/session/{session_id}/games", data=data)


def test_on_field_limit_is_applied_when_recording(client, page_session, make_api_players):
    """Twelve players at five a side must record as five, not six."""
    ids = make_api_players(*[f"P{i}" for i in range(12)])
    check_in_all(client, page_session, ids)
    r = record_balanced(client, page_session, ids, max_on_field="5")
    assert r.status_code == 200
    assert stored_on_field(client, page_session) == 5


def test_smaller_rosters_record_their_own_size(client, page_session, make_api_players):
    """A 3v3 under a limit of 5 is three a side, not five."""
    ids = make_api_players(*[f"P{i}" for i in range(6)])
    check_in_all(client, page_session, ids)
    record_balanced(client, page_session, ids, max_on_field="5")
    assert stored_on_field(client, page_session) == 3


def test_uneven_rosters_record_the_smaller_side(client, page_session, make_api_players):
    ids = make_api_players(*[f"P{i}" for i in range(7)])
    check_in_all(client, page_session, ids)
    record_balanced(client, page_session, ids, max_on_field="5")
    assert stored_on_field(client, page_session) == 3


def test_blank_limit_means_everyone_on_the_roster_plays(client, page_session, make_api_players):
    ids = make_api_players(*[f"P{i}" for i in range(12)])
    check_in_all(client, page_session, ids)
    record_balanced(client, page_session, ids, max_on_field="")
    assert stored_on_field(client, page_session) == 6


def test_the_prediction_uses_the_same_limit(client, page_session, make_api_players):
    """Otherwise the panel would promise one thing and the game record another."""
    ids = make_api_players(*[f"P{i}" for i in range(12)])
    check_in_all(client, page_session, ids)
    assignment = {f"assign_{pid}": ("0" if i < 6 else "1") for i, pid in enumerate(ids)}

    capped = squash(
        text(
            client.post(
                f"/admin/session/{page_session}/preview",
                data={**assignment, "max_on_field": "5"},
            )
        )
    )
    uncapped = squash(
        text(
            client.post(
                f"/admin/session/{page_session}/preview",
                data={**assignment, "max_on_field": ""},
            )
        )
    )
    assert "5 a side" in capped
    assert "6 a side" in uncapped


def test_the_duplicate_lower_field_is_gone(client, page_session, make_api_players):
    """One place to say how many are on the field, not two that disagree."""
    ids = make_api_players("A", "B")
    check_in_all(client, page_session, ids)
    body = text(client.get(f"/admin/session/{page_session}"))
    assert 'name="players_on_field"' not in body
    assert body.count('name="max_on_field"') == 1


def test_record_form_submits_the_format_fields(client, page_session, make_api_players):
    ids = make_api_players("A", "B")
    check_in_all(client, page_session, ids)
    body = squash(text(client.get(f"/admin/session/{page_session}")))
    assert 'id="record-form"' in body
    assert 'hx-include="#format-form"' in body


def test_edit_page_uses_the_same_limit(client, page_session, make_api_players):
    ids = make_api_players(*[f"P{i}" for i in range(12)])
    check_in_all(client, page_session, ids)
    record_balanced(client, page_session, ids, max_on_field="5")

    board = text(client.get(f"/admin/session/{page_session}"))
    game_id = int(re.search(r"/admin/games/(\d+)/edit", board).group(1))
    edit_page = text(client.get(f"/admin/games/{game_id}/edit"))
    assert 'name="players_on_field"' not in edit_page
    assert 'name="max_on_field" value="5"' in squash(edit_page)

    assignment = {
        int(m.group(1)): m.group(2)
        for m in re.finditer(
            r'name="assign_(\d+)" value="(0|1)"\s*checked', squash(edit_page)
        )
    }
    data = {f"assign_{pid}": side for pid, side in assignment.items()}
    data.update({"winner": "0", "max_on_field": "4"})
    r = client.post(f"/admin/games/{game_id}/edit", data=data, follow_redirects=False)
    assert r.status_code == 303
    assert stored_on_field(client, page_session) == 4
