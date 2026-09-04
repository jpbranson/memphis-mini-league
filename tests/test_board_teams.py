"""Session board additions: ratings beside names, team balancing, matchup panel."""

from __future__ import annotations

import re


def text(response) -> str:
    return response.text


def check_in_all(client, session_id, player_ids):
    for pid in player_ids:
        client.post(f"/admin/session/{session_id}/checkin", data={"player_id": pid})


def assignments_in(body: str) -> dict[int, str]:
    """Which team each player's radio is preselected on."""
    found = {}
    for match in re.finditer(
        r'name="assign_(\d+)" value="(0|1|out)"\s*checked', re.sub(r"\s+", " ", body)
    ):
        found[int(match.group(1))] = match.group(2)
    return found


# --- ratings beside names --------------------------------------------------------


def test_new_players_show_as_new(client, page_session, make_api_players):
    (ada,) = make_api_players("Ada")
    body = text(client.post(f"/admin/session/{page_session}/checkin", data={"player_id": ada}))
    assert '<span class="rating">new</span>' in re.sub(r"\s+", " ", body)


def test_rating_appears_next_to_a_player_with_games(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    client.post(
        f"/admin/session/{page_session}/games",
        data={f"assign_{ada}": "0", f"assign_{ben}": "1", "winner": "0"},
    )
    rating = client.get("/api/leaderboard").json()
    ada_rating = next(r["rating"] for r in rating if r["player"]["id"] == ada)

    body = re.sub(r"\s+", " ", text(client.get(f"/admin/session/{page_session}")))
    assert f'<span class="rating">{ada_rating}</span>' in body
    assert "new" not in body.split("Checked in")[1].split("Check in a player")[0]


def test_ratings_show_in_the_check_in_list_too(client, page_session, make_api_players):
    make_api_players("Ada")
    body = re.sub(r"\s+", " ", text(client.get(f"/admin/session/{page_session}")))
    check_in_section = body.split('id="checkin-list"')[1]
    assert '<span class="rating">' in check_in_section


# --- balancing -------------------------------------------------------------------


def test_balance_button_is_offered(client, page_session, make_api_players):
    ids = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, ids)
    body = text(client.get(f"/admin/session/{page_session}"))
    assert "Balance teams" in body
    assert f'hx-post="/admin/session/{page_session}/balance"' in body


def test_balance_assigns_everyone_to_a_team(client, page_session, make_api_players):
    ids = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, ids)

    r = client.post(f"/admin/session/{page_session}/balance")
    assert r.status_code == 200
    chosen = assignments_in(text(r))
    assert set(chosen) == set(ids), "every checked-in player gets a team"
    assert sorted(chosen.values()) == ["0", "0", "1", "1"]


def test_balance_splits_uneven_groups(client, page_session, make_api_players):
    ids = make_api_players("A", "B", "C", "D", "E")
    check_in_all(client, page_session, ids)
    chosen = assignments_in(text(client.post(f"/admin/session/{page_session}/balance")))
    counts = sorted([list(chosen.values()).count("0"), list(chosen.values()).count("1")])
    assert counts == [2, 3]


def test_balance_needs_two_players(client, page_session, make_api_players):
    (ada,) = make_api_players("Ada")
    check_in_all(client, page_session, [ada])
    r = client.post(f"/admin/session/{page_session}/balance")
    assert "Check in at least two players first." in text(r)


def test_players_can_still_be_moved_by_hand_after_balancing(
    client, page_session, make_api_players
):
    ids = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, ids)
    balanced = text(client.post(f"/admin/session/{page_session}/balance"))
    # The radios are ordinary form controls, so nothing is locked in.
    assert balanced.count('type="radio"') >= len(ids) * 3
    assert "disabled" not in balanced

    # Overriding the split and recording still works.
    r = client.post(
        f"/admin/session/{page_session}/games",
        data={
            f"assign_{ids[0]}": "0",
            f"assign_{ids[1]}": "1",
            f"assign_{ids[2]}": "1",
            f"assign_{ids[3]}": "1",
            "winner": "0",
        },
    )
    assert r.status_code == 200
    assert "Recorded round 1." in text(r)


def test_balance_avoids_repeating_the_previous_pairing(
    client, page_session, make_api_players
):
    """With four equal players, round two should not reuse round one's teams."""
    ids = make_api_players("A", "B", "C", "D")
    check_in_all(client, page_session, ids)
    client.post(
        f"/admin/session/{page_session}/games",
        data={
            f"assign_{ids[0]}": "0",
            f"assign_{ids[1]}": "0",
            f"assign_{ids[2]}": "1",
            f"assign_{ids[3]}": "1",
            "winner": "0",
        },
    )
    chosen = assignments_in(text(client.post(f"/admin/session/{page_session}/balance")))
    team_a = {pid for pid, side in chosen.items() if side == "0"}
    assert team_a not in ({ids[0], ids[1]}, {ids[2], ids[3]})


# --- the matchup panel -----------------------------------------------------------


def test_panel_prompts_before_teams_are_picked(client, page_session, make_api_players):
    ids = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, ids)
    body = text(client.get(f"/admin/session/{page_session}"))
    assert "Put at least one player on each team" in body


def test_panel_reports_strengths_and_prediction(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    r = client.post(
        f"/admin/session/{page_session}/preview",
        data={f"assign_{ada}": "0", f"assign_{ben}": "1"},
    )
    assert r.status_code == 200
    body = re.sub(r"\s+", " ", text(r))
    assert "Team A" in body and "Team B" in body
    assert "Predicted 50% to 50%" in body
    assert "Even match" in body


def test_panel_shows_a_lopsided_match_as_such(client, page_session, make_api_players):
    """Give one player a run of wins, then put them against a newcomer."""
    strong, weak, filler = make_api_players("Strong", "Weak", "Filler")
    check_in_all(client, page_session, [strong, weak, filler])
    for _ in range(5):
        client.post(
            f"/admin/session/{page_session}/games",
            data={f"assign_{strong}": "0", f"assign_{filler}": "1", "winner": "0"},
        )
    body = re.sub(
        r"\s+",
        " ",
        text(
            client.post(
                f"/admin/session/{page_session}/preview",
                data={f"assign_{strong}": "0", f"assign_{weak}": "1"},
            )
        ),
    )
    assert "Even match" not in body
    assert "Predicted" in body


def test_panel_flags_uneven_rosters(client, page_session, make_api_players):
    ids = make_api_players("A", "B", "C")
    check_in_all(client, page_session, ids)
    body = client.post(
        f"/admin/session/{page_session}/preview",
        data={f"assign_{ids[0]}": "0", f"assign_{ids[1]}": "1", f"assign_{ids[2]}": "1"},
    ).text
    assert "Uneven rosters" in body


def test_panel_is_wired_to_refresh_on_change(client, page_session, make_api_players):
    ids = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, ids)
    body = text(client.get(f"/admin/session/{page_session}"))
    assert 'id="balance-preview"' in body
    assert f'hx-post="/admin/session/{page_session}/preview"' in body
    assert 'hx-trigger="change from:#record-form"' in body


def test_assignment_survives_a_rejected_result(client, page_session, make_api_players):
    """A validation error must not wipe the teams the organizer just set up."""
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    r = client.post(
        f"/admin/session/{page_session}/games",
        data={f"assign_{ada}": "0", f"assign_{ben}": "1"},  # no winner picked
    )
    assert r.status_code == 400
    chosen = assignments_in(text(r))
    assert chosen[ada] == "0"
    assert chosen[ben] == "1"


def test_panel_explains_why_totals_look_low_with_new_players(
    client, page_session, make_api_players
):
    """A new player rates 0 on the board but average in the prediction, so the
    totals and the percentages disagree. The panel must say which to trust."""
    veteran, filler, rookie = make_api_players("Veteran", "Filler", "Rookie")
    check_in_all(client, page_session, [veteran, filler, rookie])
    for _ in range(3):
        client.post(
            f"/admin/session/{page_session}/games",
            data={f"assign_{veteran}": "0", f"assign_{filler}": "1", "winner": "0"},
        )
    body = re.sub(
        r"\s+",
        " ",
        client.post(
            f"/admin/session/{page_session}/preview",
            data={f"assign_{veteran}": "0", f"assign_{rookie}": "1"},
        ).text,
    )
    assert "1 new player rated 0 until they have played" in body
    assert "trust the percentages" in body
    assert "Team B" in body


def test_panel_says_nothing_about_newcomers_when_everyone_is_rated(
    client, page_session, make_api_players
):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    client.post(
        f"/admin/session/{page_session}/games",
        data={f"assign_{ada}": "0", f"assign_{ben}": "1", "winner": "0"},
    )
    body = client.post(
        f"/admin/session/{page_session}/preview",
        data={f"assign_{ada}": "0", f"assign_{ben}": "1"},
    ).text
    assert "new player" not in body
