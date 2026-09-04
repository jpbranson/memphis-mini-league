"""Organizer pages and their HTMX partials (design doc section 7)."""

from __future__ import annotations

import re


def text(response) -> str:
    return response.text


def test_root_is_the_public_leaderboard(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Mini League" in text(r)
    assert "No seasons yet" in text(r)


def test_admin_home_asks_for_a_season_when_none_exists(client):
    r = client.get("/admin")
    assert r.status_code == 200
    assert "Start a season first" in text(r)


def test_create_season_from_the_page(client):
    r = client.post(
        "/admin/seasons",
        data={"name": "Fall 2026", "start_date": "2026-09-01"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Fall 2026" in text(r)
    assert "New session" in text(r)


def test_duplicate_season_shows_an_error(client, api_season):
    r = client.post("/admin/seasons", data={"name": "Fall 2026", "start_date": "2027-01-01"})
    assert r.status_code == 400
    assert "already exists" in text(r)


def test_new_session_form_defaults_to_today(client, api_season):
    r = client.get("/admin/session/new")
    assert r.status_code == 200
    assert 'name="date"' in text(r)
    assert "Fall 2026" in text(r)


def test_create_session_redirects_to_the_board(client, api_season):
    r = client.post(
        "/admin/session/new",
        data={"date": "2026-09-05", "notes": "Windy"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert re.match(r"/admin/session/\d+", r.headers["location"])

    board = client.get(r.headers["location"])
    assert "2026-09-05" in text(board)
    assert "Windy" in text(board)
    assert "Nobody yet" in text(board)


def test_create_session_without_a_season_shows_an_error(client):
    r = client.post("/admin/session/new", data={"date": "2026-09-05"})
    assert r.status_code == 400
    assert "no season covers" in text(r)


def test_search_finds_an_existing_player(client, page_session, make_api_players):
    make_api_players("Justin M.")
    r = client.get(f"/admin/session/{page_session}/search", params={"q": "justin"})
    assert r.status_code == 200
    assert "Justin M." in text(r)
    assert "Check in" in text(r)


def test_search_offers_to_create_when_nothing_matches(client, page_session):
    r = client.get(f"/admin/session/{page_session}/search", params={"q": "Priya"})
    assert 'No player matches "Priya"' in text(r)
    assert 'Create new player "Priya"' in text(r)


def test_empty_search_restores_the_full_check_in_list(client, page_session, make_api_players):
    make_api_players("Ada", "Priya")
    r = client.get(f"/admin/session/{page_session}/search", params={"q": ""})
    body = text(r)
    assert "Ada" in body and "Priya" in body
    # With no query there is nothing to create, so no create button.
    assert "Create new player" not in body


def test_players_are_checkinable_without_searching(client, page_session, make_api_players):
    """Report: 'I am not able to check players in.' The board must offer a
    tappable list, not only a search box."""
    ada, ben = make_api_players("Ada", "Ben")
    body = text(client.get(f"/admin/session/{page_session}"))
    assert "Check in" in body
    for pid in (ada, ben):
        assert f'"player_id": {pid}' in body
    assert body.count('hx-post="/admin/session/%d/checkin"' % page_session) >= 2


def test_checked_in_players_leave_the_check_in_list(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    r = client.post(f"/admin/session/{page_session}/checkin", data={"player_id": ada})
    body = text(r)
    check_in_list = body.split('id="checkin-list"')[1]
    assert "Ben" in check_in_list
    assert "Ada" not in check_in_list.split("This round")[0]


def test_search_filters_the_check_in_list(client, page_session, make_api_players):
    make_api_players("Ada", "Priya")
    body = text(client.get(f"/admin/session/{page_session}/search", params={"q": "Ada"}))
    assert "Ada" in body
    assert "Priya" not in body


def test_add_player_checks_them_in(client, page_session):
    r = client.post(f"/admin/session/{page_session}/players", data={"name": "Ada"})
    assert r.status_code == 200
    assert "Added Ada and checked them in." in text(r)
    assert "Here (1)" in text(r)


def test_add_near_duplicate_shows_the_warning_instead(client, page_session):
    client.post(f"/admin/session/{page_session}/players", data={"name": "Justin M."})
    r = client.post(f"/admin/session/{page_session}/players", data={"name": "Justin"})
    assert r.status_code == 409
    body = text(r)
    assert "That name looks like someone already here" in body
    assert "Check in Justin M." in body
    assert 'Create "Justin" anyway' in body
    # The warning is rendered inside the board, so the roster stays in view.
    assert "Here (1)" in body
    assert "Check in" in body


def test_forcing_past_the_duplicate_warning_works(client, page_session):
    client.post(f"/admin/session/{page_session}/players", data={"name": "Justin M."})
    r = client.post(
        f"/admin/session/{page_session}/players", data={"name": "Justin", "force": "true"}
    )
    assert r.status_code == 200
    assert "Here (2)" in text(r)


def test_check_out_and_back_in(client, page_session, make_api_players):
    (ada,) = make_api_players("Ada")
    client.post(f"/admin/session/{page_session}/checkin", data={"player_id": ada})

    r = client.post(f"/admin/session/{page_session}/checkout", data={"player_id": ada})
    assert "Here (0)" in text(r)
    assert "Gone (1)" in text(r)

    r = client.post(f"/admin/session/{page_session}/checkin", data={"player_id": ada})
    assert "Here (1)" in text(r)


def test_record_form_appears_only_with_two_players(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    r = client.post(f"/admin/session/{page_session}/checkin", data={"player_id": ada})
    assert "This round" not in text(r)
    r = client.post(f"/admin/session/{page_session}/checkin", data={"player_id": ben})
    assert "This round" in text(r)


def check_in_all(client, session_id, player_ids):
    for pid in player_ids:
        client.post(f"/admin/session/{session_id}/checkin", data={"player_id": pid})


def test_record_a_result_from_the_form(client, page_session, make_api_players):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [ada, ben, cleo, dev])

    r = client.post(
        f"/admin/session/{page_session}/games",
        data={
            f"assign_{ada}": "0",
            f"assign_{ben}": "0",
            f"assign_{cleo}": "1",
            f"assign_{dev}": "1",
            "winner": "0",
            "score_0": "5",
            "score_1": "3",
        },
    )
    assert r.status_code == 200
    body = text(r)
    assert "Recorded round 1." in body
    assert "Rounds (1)" in body
    assert "Ada, Ben" in body
    assert "shirt-light" in body


def test_players_left_on_out_are_excluded(client, page_session, make_api_players):
    ada, ben, cleo, dev, erin = make_api_players("Ada", "Ben", "Cleo", "Dev", "Erin")
    check_in_all(client, page_session, [ada, ben, cleo, dev, erin])

    r = client.post(
        f"/admin/session/{page_session}/games",
        data={
            f"assign_{ada}": "0",
            f"assign_{ben}": "0",
            f"assign_{cleo}": "1",
            f"assign_{dev}": "1",
            f"assign_{erin}": "out",
            "winner": "1",
        },
    )
    assert r.status_code == 200
    assert "Erin" not in text(r).split("Rounds (1)")[1]


def test_recording_without_a_winner_is_rejected(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    r = client.post(
        f"/admin/session/{page_session}/games",
        data={f"assign_{ada}": "0", f"assign_{ben}": "1"},
    )
    assert r.status_code == 400
    assert "Pick the winning team." in text(r)


def test_recording_with_an_empty_team_is_rejected(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    r = client.post(
        f"/admin/session/{page_session}/games",
        data={f"assign_{ada}": "0", f"assign_{ben}": "0", "winner": "0"},
    )
    assert r.status_code == 400
    assert "team 1 has no players" in text(r)


def test_score_disagreeing_with_the_winner_is_rejected(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    r = client.post(
        f"/admin/session/{page_session}/games",
        data={
            f"assign_{ada}": "0",
            f"assign_{ben}": "1",
            "winner": "0",
            "score_0": "2",
            "score_1": "5",
        },
    )
    assert r.status_code == 400
    assert "is down as winning but scored" in text(r)


def test_uneven_teams_via_players_on_field(client, page_session, make_api_players):
    ids = make_api_players("A", "B", "C", "D", "E", "F", "G")
    check_in_all(client, page_session, ids)
    data = {f"assign_{pid}": ("0" if i < 3 else "1") for i, pid in enumerate(ids)}
    data.update({"winner": "0", "players_on_field": "3"})
    r = client.post(f"/admin/session/{page_session}/games", data=data)
    assert r.status_code == 200
    assert "3 a side" in text(r)


def record_simple_game(client, session_id, ada, ben) -> int:
    client.post(
        f"/admin/session/{session_id}/games",
        data={f"assign_{ada}": "0", f"assign_{ben}": "1", "winner": "0"},
    )
    board = client.get(f"/admin/session/{session_id}")
    match = re.search(r"/admin/games/(\d+)/edit", text(board))
    assert match, "expected an edit link on the board"
    return int(match.group(1))


def test_delete_and_undo_a_round(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    game_id = record_simple_game(client, page_session, ada, ben)

    r = client.post(f"/admin/games/{game_id}/delete")
    assert "Round deleted. Ratings replayed." in text(r)
    assert "Rounds (0)" in text(r)
    assert "Undo" in text(r)

    r = client.post(f"/admin/games/{game_id}/restore")
    assert "Round restored." in text(r)
    assert "Rounds (1)" in text(r)


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def test_edit_form_is_prefilled(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    game_id = record_simple_game(client, page_session, ada, ben)

    body = normalize_ws(text(client.get(f"/admin/games/{game_id}/edit")))
    assert "Edit round 1" in body
    # Ada was on team A, Ben on team B, and Ada won: each is preselected.
    assert f'name="assign_{ada}" value="0" checked' in body
    assert f'name="assign_{ben}" value="1" checked' in body
    assert 'name="winner" value="0" checked' in body


def test_edit_flips_the_winner_from_the_form(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    game_id = record_simple_game(client, page_session, ada, ben)

    r = client.post(
        f"/admin/games/{game_id}/edit",
        data={f"assign_{ada}": "0", f"assign_{ben}": "1", "winner": "1"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/admin/session/{page_session}"

    teams = client.get(f"/api/sessions/{page_session}").json()["games"][0]["teams"]
    winner = next(t for t in teams if t["rank"] == 1)
    assert winner["player_ids"] == [ben]
    # And the form now comes back preselected on team B.
    body = normalize_ws(text(client.get(f"/admin/games/{game_id}/edit")))
    assert 'name="winner" value="1" checked' in body


def test_edit_without_a_winner_shows_an_error(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    game_id = record_simple_game(client, page_session, ada, ben)
    r = client.post(
        f"/admin/games/{game_id}/edit",
        data={f"assign_{ada}": "0", f"assign_{ben}": "1"},
    )
    assert r.status_code == 400
    assert "Pick the winning team." in text(r)


def test_edit_unknown_game_is_a_404(client):
    assert client.get("/admin/games/999/edit").status_code == 404


def test_board_shows_the_htmx_targets(client, page_session, make_api_players):
    (ada,) = make_api_players("Ada")
    client.post(f"/admin/session/{page_session}/checkin", data={"player_id": ada})
    body = text(client.get(f"/admin/session/{page_session}"))
    assert 'id="board"' in body
    assert 'hx-target="#board"' in body
    assert "htmx.min.js" in body


def test_htmx_is_served_by_this_app_not_a_cdn(client):
    """A blocked or unreachable CDN would make every button silently dead."""
    body = text(client.get("/admin"))
    assert 'src="/static/htmx.min.js"' in body
    assert "cdnjs" not in body and "cdn." not in body

    asset = client.get("/static/htmx.min.js")
    assert asset.status_code == 200
    assert len(asset.content) > 10_000


def test_validation_errors_are_swapped_in_despite_being_4xx(client):
    """htmx drops 4xx bodies by default, which would hide every error message."""
    body = text(client.get("/admin"))
    assert "htmx:beforeSwap" in body
    assert "shouldSwap = true" in body
    # And a guard so a failure to load htmx is visible rather than silent.
    assert "did not load fully" in body


def test_edit_rejects_a_winner_that_contradicts_the_scores(
    client, page_session, make_api_players
):
    """Report: 'I tried to edit past games and it wouldn't let me.' Flipping the
    winner without swapping the scores is refused, and must say why."""
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    client.post(
        f"/admin/session/{page_session}/games",
        data={
            f"assign_{ada}": "0",
            f"assign_{ben}": "1",
            "winner": "0",
            "score_0": "5",
            "score_1": "3",
        },
    )
    board = client.get(f"/admin/session/{page_session}")
    game_id = int(re.search(r"/admin/games/(\d+)/edit", text(board)).group(1))

    r = client.post(
        f"/admin/games/{game_id}/edit",
        data={
            f"assign_{ada}": "0",
            f"assign_{ben}": "1",
            "winner": "1",  # flipped, but the scores still say A won
            "score_0": "5",
            "score_1": "3",
        },
    )
    assert r.status_code == 400
    body = text(r)
    assert "The dark team is down as winning but scored 3 to 5" in body
    assert "Change the winner or the scores" in body

    # Swapping the scores as instructed lets the edit through.
    r = client.post(
        f"/admin/games/{game_id}/edit",
        data={
            f"assign_{ada}": "0",
            f"assign_{ben}": "1",
            "winner": "1",
            "score_0": "3",
            "score_1": "5",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    teams = client.get(f"/api/sessions/{page_session}").json()["games"][0]["teams"]
    assert next(t for t in teams if t["rank"] == 1)["player_ids"] == [ben]
