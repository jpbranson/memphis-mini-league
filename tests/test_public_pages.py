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
    assert "Leaderboard" in body
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
    assert "hidden with fewer than 5 games" in squash(body)
    assert text(client.get("/?min_games=1")).count('href="/players/') == 2


def test_leaderboard_season_selector_lists_seasons(client, api_season):
    body = text(client.get("/"))
    assert 'name="season_id"' in body
    assert "Fall 2026" in body
    assert "(current)" in body


def test_leaderboard_with_no_seasons(client):
    assert "No seasons yet" in text(client.get("/"))


def test_leaderboard_explains_the_rating(client, api_season):
    assert "conservative estimate" in text(client.get("/"))


# --- player page -----------------------------------------------------------------


def test_player_page_shows_record_and_games(client, page_session, make_api_players):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [ada, ben, cleo, dev])
    play_round(client, page_session, [ada, ben], [cleo, dev], score=(5, 3))

    body = squash(text(client.get(f"/players/{ada}")))
    assert "Ada" in body
    assert "All time 1-0" in body
    assert "1 season" in body
    assert "Won" in body and "5-3" in body
    assert "With Ben" in body
    assert "against Cleo, Dev" in body or "against Dev, Cleo" in body
    assert "Skill estimate (mu)" in body
    assert "Uncertainty (sigma)" in body


def test_player_page_without_games(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    body = text(client.get(f"/players/{ada}"))
    assert "No games recorded for this player yet." in body


def test_player_page_chart_data_is_embedded(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])
    play_round(client, page_session, [ada], [ben])

    body = text(client.get(f"/players/{ada}"))
    assert "Rating over time" in body
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
    assert "Rating over time" not in body
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
    assert "keep your place in the session" in drawer


def test_full_page_links_stay_in_the_same_tab(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    page = squash(text(client.get("/?min_games=0")))
    assert f'<a href="/players/{ada}" >' in page or f'<a href="/players/{ada}">' in page
    assert "target=\"_blank\"" not in page
