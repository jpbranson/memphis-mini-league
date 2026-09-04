"""Milestone 3: the public leaderboard, player pages, and the standings drawer."""

from __future__ import annotations

import json
import re


def text(response) -> str:
    return response.text


def squash(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def check_in_all(client, session_id, player_ids):
    for pid in player_ids:
        client.post(f"/admin/session/{session_id}/checkin", data={"player_id": pid})


def play_round(client, session_id, winners, losers, score=None):
    data = {f"assign_{p}": "0" for p in winners}
    data.update({f"assign_{p}": "1" for p in losers})
    data["winner"] = "0"
    if score:
        data["score_0"], data["score_1"] = str(score[0]), str(score[1])
    return client.post(f"/admin/session/{session_id}/games", data=data)


# --- leaderboard page ------------------------------------------------------------


def test_leaderboard_lists_players_in_rank_order(client, page_session, make_api_players):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [ada, ben, cleo, dev])
    play_round(client, page_session, [ada, ben], [cleo, dev], score=(5, 3))
    play_round(client, page_session, [ada, ben], [cleo, dev], score=(5, 1))

    body = text(client.get("/?min_games=0"))
    assert "Fall 2026" in body
    assert "Fall 2026" in body
    for name in ("Ada", "Ben", "Cleo", "Dev"):
        assert name in body
    # Winners appear above losers in the table.
    assert body.index("Ada") < body.index("Cleo")
    assert 'href="/players/' in body


def test_leaderboard_hides_players_below_the_games_threshold(
    client, page_session, make_api_players
):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    body = text(client.get("/"))  # default minimum is 5 games
    assert "Nobody has 5 games yet" in squash(body)
    assert text(client.get("/?min_games=1")).count('href="/players/') == 2


def test_leaderboard_season_selector_appears_only_with_more_than_one(client, api_season):
    """With a single season there is nothing to choose between, so no picker."""
    assert 'name="season_id"' not in text(client.get("/"))

    client.post("/admin/seasons", data={"name": "Spring 2027", "start_date": "2027-03-01"})
    body = text(client.get("/"))
    assert 'name="season_id"' in body
    assert "Fall 2026" in body and "Spring 2027" in body


def test_leaderboard_with_no_seasons(client):
    assert "No seasons yet" in text(client.get("/"))


def test_leaderboard_explains_the_rating(client, api_season):
    assert "skill minus three times the uncertainty" in text(client.get("/"))


# --- player page -----------------------------------------------------------------


def test_player_page_shows_record_and_games(client, page_session, make_api_players):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [ada, ben, cleo, dev])
    play_round(client, page_session, [ada, ben], [cleo, dev], score=(5, 3))

    body = squash(text(client.get(f"/players/{ada}")))
    assert "Ada" in body
    assert "1&ndash;0 all time" in body
    assert "Won" in body and "5&ndash;3" in body
    assert "with Ben" in body
    assert "against Cleo, Dev" in body or "against Dev, Cleo" in body
    assert "skill" in body and "uncertainty" in body


def test_player_page_without_games(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    body = text(client.get(f"/players/{ada}"))
    assert "Not played yet." in body


def test_player_page_chart_data_is_embedded(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])
    play_round(client, page_session, [ada], [ben])

    body = text(client.get(f"/players/{ada}"))
    assert 'id="ratingChart"' in body
    assert 'id="rating-points"' in body
    assert "/static/chart.min.js" in body

    payload = re.search(
        r'<script id="rating-points" type="application/json">(.*?)</script>', body, re.S
    )
    points = json.loads(payload.group(1))
    # A starting point plus one per game, rising as Ada wins.
    assert len(points) == 3
    assert points[0]["game"] == 0
    assert points[0]["mu"] == 25.0
    assert points[-1]["mu"] > points[0]["mu"]
    assert all({"game", "mu", "sigma", "rating"} <= set(p) for p in points)


def test_player_page_charts_a_single_game_as_start_plus_one_point(
    client, page_session, make_api_players
):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    body = text(client.get(f"/players/{ada}"))
    payload = re.search(
        r'<script id="rating-points" type="application/json">(.*?)</script>', body, re.S
    )
    points = json.loads(payload.group(1))
    assert [p["game"] for p in points] == [0, 1]


def test_player_page_has_no_chart_without_games(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    body = text(client.get(f"/players/{ada}"))
    assert 'id="ratingChart"' not in body
    assert "chart.min.js" not in body


def test_unknown_player_is_a_404(client):
    assert client.get("/players/999").status_code == 404


# --- standings drawer ------------------------------------------------------------


def test_every_page_offers_the_standings_drawer(client, page_session):
    for path in ("/", "/admin", f"/admin/session/{page_session}"):
        body = text(client.get(path))
        assert 'id="menu-btn"' in body, path
        assert 'hx-get="/panel/leaderboard"' in body, path
        assert 'id="drawer"' in body and 'id="drawer-body"' in body, path


def test_drawer_starts_hidden_and_is_an_overlay(client):
    body = squash(text(client.get("/")))
    assert '<aside id="drawer" hidden>' in body
    assert "#drawer { position: fixed" in body


def test_drawer_panel_returns_only_the_table(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    r = client.get("/panel/leaderboard")
    assert r.status_code == 200
    body = text(r)
    assert "Ada" in body and "Ben" in body
    # A fragment, not a page: nothing here can replace the board.
    assert "<html" not in body
    assert 'id="board"' not in body
    assert 'id="record-form"' not in body


def test_drawer_shows_everyone_regardless_of_the_page_filter(
    client, page_session, make_api_players
):
    """The organizer wants today's players, who will have very few games."""
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    assert "Ada" not in text(client.get("/"))  # hidden by the 5-game default
    assert "Ada" in text(client.get("/panel/leaderboard"))


def test_opening_the_drawer_does_not_disturb_the_session(
    client, page_session, make_api_players
):
    """Checking the standings must not cost the organizer their team setup."""
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])

    before = text(client.get(f"/admin/session/{page_session}"))
    client.get("/panel/leaderboard")
    after = text(client.get(f"/admin/session/{page_session}"))
    assert before == after

    # And the panel request changes nothing on the server either.
    session = client.get(f"/api/sessions/{page_session}").json()
    assert len(session["players"]) == 2
    assert session["games"] == []


def test_drawer_links_open_in_a_new_tab(client, page_session, make_api_players):
    """Following a link from the drawer would abandon the session being set up."""
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    drawer = squash(text(client.get("/panel/leaderboard")))
    assert f'<a href="/players/{ada}" target="_blank" rel="noopener">' in drawer
    assert "Names open in a new tab" in drawer


def test_full_page_links_stay_in_the_same_tab(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    page = squash(text(client.get("/?min_games=0")))
    assert f'<a href="/players/{ada}" >' in page or f'<a href="/players/{ada}">' in page
    assert "target=\"_blank\"" not in page


# --- rating explanation on the player page ---------------------------------------


def test_player_page_explains_rating_and_skill(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    body = squash(text(client.get(f"/players/{ada}")))
    assert "How the rating works" in body
    # Each term is explained, and the parameters are named rather than magic.
    assert "Skill starts at 25" in body
    assert "Uncertainty starts at 8.33" in body
    assert "skill minus 3 times the uncertainty, times 40" in body
    # And why the subtraction exists at all.
    assert "one good morning should not put you top" in body
    assert "Only wins and losses count" in body


def test_rating_explanation_works_through_the_players_own_numbers(
    client, page_session, make_api_players
):
    """The worked example must use this player's real figures and come out right."""
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    detail = client.get(f"/api/players/{ada}").json()["seasons"][0]
    mu, sigma, rating = detail["mu"], detail["sigma"], detail["rating"]

    body = squash(text(client.get(f"/players/{ada}")))
    assert f"For Ada: ({mu:.2f} &minus; 3 &times; {sigma:.2f}) &times; 40 = " in body
    assert f"<b>{rating}</b>." in body
    assert round((mu - 3 * sigma) * 40) == rating


def test_no_explanation_for_a_player_without_games(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    assert "How the rating works" not in text(client.get(f"/players/{ada}"))


def test_the_longer_explanation_is_folded_away_until_asked_for(
    client, page_session, make_api_players
):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    body = squash(text(client.get(f"/players/{ada}")))
    assert '<details class="explain">' in body
    assert "What the numbers actually mean" in body
    # Folded: someone who only wants the score never has to read it.
    assert "<details class=\"explain\" open>" not in body


def test_the_explanation_separates_the_floor_from_the_best_guess(
    client, page_session, make_api_players
):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    detail = client.get(f"/api/players/{ada}").json()["seasons"][0]
    body = squash(text(client.get(f"/players/{ada}")))

    assert "is <em>at least</em> this good" in body
    # The unmarked-down figure is quoted, and it is higher than the rating shown.
    guess = round(detail["mu"] * 40)
    assert f"would put Ada at {guess} rather than {detail['rating']}" in body
    assert guess > detail["rating"]


def test_the_explanation_quotes_odds_for_the_format_the_league_plays(
    client, page_session, make_api_players
):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [ada, ben, cleo, dev])
    play_round(client, page_session, [ada, ben], [cleo, dev])

    body = squash(text(client.get(f"/players/{ada}")))
    assert "Wins a 1v1" in body
    assert "Wins a 2v2" in body  # this league's own format, not a hardcoded 3v3
    for gap in (100, 200, 400):
        assert f"{gap} points" in body

    # The 1v1 column beats the team column on every row: the edge dilutes.
    rows = re.findall(
        r"<td>(\d+) points</td> <td class=\"num\">(\d+)%</td> <td class=\"num\">(\d+)%</td>",
        body,
    )
    assert len(rows) == 3
    assert all(int(solo) > int(team) > 50 for _, solo, team in rows)


def test_a_player_with_no_games_is_not_offered_the_explanation(
    client, api_season, make_api_players
):
    (ada,) = make_api_players("Ada")
    body = text(client.get(f"/players/{ada}"))
    assert "What the numbers actually mean" not in body


def test_the_explanation_sits_between_the_chart_and_the_games(
    client, page_session, make_api_players
):
    """Read the shape of your season, then what it means, then the detail."""
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])
    play_round(client, page_session, [ada], [ben])

    body = text(client.get(f"/players/{ada}"))
    assert (
        body.index('id="ratingChart"')
        < body.index("How the rating works")
        < body.index("What the numbers actually mean")
        < body.index("<h2>Games</h2>")
    )


# --- the standing designation, set from the player's own page --------------------


def today_says(client, session_id, player_id):
    """What a checked-in player counts as for one session."""
    roster = client.get(f"/api/sessions/{session_id}").json()["players"]
    return [e["designation"] for e in roster if e["player"]["id"] == player_id]


def standing(client, player_id):
    """The player's standing designation, straight from the API."""
    return client.get(f"/api/players/{player_id}").json()["player"]["designation"]


def make_player(client, name, designation=None):
    response = client.post(
        "/api/players", json={"name": name, "designation": designation, "force": True}
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_player_page_picker_starts_on_what_the_player_was_created_with(client):
    ada = make_player(client, "Ada", "WMP")
    body = squash(text(client.get(f"/players/{ada}")))
    assert f'hx-post="/admin/players/{ada}/designation"' in body
    assert '<input type="radio" name="standing_designation" value="WMP" checked' in body
    assert '<input type="radio" name="standing_designation" value="MMP" checked' not in body


def test_a_player_created_without_one_starts_on_the_blank_option(client):
    ada = make_player(client, "Ada")
    body = squash(text(client.get(f"/players/{ada}")))
    assert '<input type="radio" name="standing_designation" value="" checked' in body


def test_a_visitor_sees_the_designation_but_cannot_change_it(client, visitor):
    ada = make_player(client, "Ada", "WMP")
    body = squash(text(visitor.get(f"/players/{ada}")))
    assert '<span class="tag">WMP</span>' in body
    assert "standing_designation" not in body


def test_changing_it_on_the_player_page_returns_only_that_block(client):
    ada = make_player(client, "Ada", "WMP")
    response = client.post(
        f"/admin/players/{ada}/designation", data={"designation": "MMP", "view": "player"}
    )
    assert response.status_code == 200
    body = squash(text(response))
    assert "Ada is now MMP." in body
    assert '<input type="radio" name="standing_designation" value="MMP" checked' in body
    # The block, not the whole page, so it can be swapped in place.
    assert "<h1>" not in body


def test_the_player_page_change_becomes_the_new_default(client, api_season):
    ada = make_player(client, "Ada", "WMP")
    client.post(
        f"/admin/players/{ada}/designation", data={"designation": "MMP", "view": "player"}
    )

    assert standing(client, ada) == "MMP"
    # A session started afterwards picks the new default up on its own.
    session_id = int(
        client.post(
            "/admin/session/new", data={"date": "2026-09-05"}, follow_redirects=False
        ).headers["location"].rsplit("/", 1)[1]
    )
    client.post(f"/admin/session/{session_id}/checkin", data={"player_id": ada})
    assert today_says(client, session_id, ada) == ["MMP"]


def test_a_session_override_still_lasts_only_that_session(client, page_session):
    """The two pickers look alike, so this is the difference worth pinning down."""
    ada = make_player(client, "Ada", "WMP")
    client.post(f"/admin/session/{page_session}/checkin", data={"player_id": ada})
    client.post(
        f"/admin/session/{page_session}/designation",
        data={"player_id": ada, "designation": "MMP"},
    )

    # Today says MMP, but the player is still standing as a WMP...
    assert today_says(client, page_session, ada) == ["MMP"]
    assert standing(client, ada) == "WMP"

    # ...so the picker on their page still reads WMP, and the next session does too.
    body = squash(text(client.get(f"/players/{ada}")))
    assert '<input type="radio" name="standing_designation" value="WMP" checked' in body
    later = int(
        client.post(
            "/admin/session/new", data={"date": "2026-09-12"}, follow_redirects=False
        ).headers["location"].rsplit("/", 1)[1]
    )
    client.post(f"/admin/session/{later}/checkin", data={"player_id": ada})
    assert today_says(client, later, ada) == ["WMP"]


def test_the_player_page_can_clear_the_designation(client):
    ada = make_player(client, "Ada", "WMP")
    body = squash(text(client.post(
        f"/admin/players/{ada}/designation", data={"designation": "", "view": "player"}
    )))
    assert "Ada has no designation." in body
    assert standing(client, ada) is None


def test_a_bad_designation_leaves_the_player_page_block_unchanged(client):
    ada = make_player(client, "Ada", "WMP")
    response = client.post(
        f"/admin/players/{ada}/designation", data={"designation": "WNP", "view": "player"}
    )
    assert response.status_code == 400
    body = squash(text(response))
    assert "not a designation" in body
    assert '<input type="radio" name="standing_designation" value="WMP" checked' in body
    assert standing(client, ada) == "WMP"


def test_a_visitor_cannot_post_a_designation(visitor):
    response = visitor.post(
        "/admin/players/1/designation",
        data={"designation": "WMP", "view": "player"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
