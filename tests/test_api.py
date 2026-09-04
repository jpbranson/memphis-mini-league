"""JSON API for the organizer flow (design doc section 8)."""

from __future__ import annotations

import pytest


def test_create_season_and_session(client):
    r = client.post("/api/seasons", json={"name": "Fall 2026", "start_date": "2026-09-01"})
    assert r.status_code == 201
    assert r.json()["name"] == "Fall 2026"

    r = client.post("/api/sessions", json={"date": "2026-09-05", "notes": "Windy"})
    assert r.status_code == 201
    body = r.json()
    assert body["date"] == "2026-09-05"
    assert body["notes"] == "Windy"
    assert body["players"] == []
    assert body["games"] == []


def test_session_without_season_is_a_400(client):
    r = client.post("/api/sessions", json={"date": "2026-09-05"})
    assert r.status_code == 400
    assert "no season covers" in r.json()["detail"]


def test_duplicate_season_name_is_a_400(client, api_season):
    r = client.post("/api/seasons", json={"name": "Fall 2026", "start_date": "2027-01-01"})
    assert r.status_code == 400


def test_create_player(client, api_season):
    r = client.post("/api/players", json={"name": "Justin M."})
    assert r.status_code == 201
    assert r.json()["name"] == "Justin M."
    assert r.json()["active"] is True


def test_near_duplicate_player_returns_409_with_matches(client, api_season):
    client.post("/api/players", json={"name": "Justin M."})
    r = client.post("/api/players", json={"name": "Justin"})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "check them in instead" in detail["message"]
    assert [m["player"]["name"] for m in detail["matches"]] == ["Justin M."]


def test_force_creates_the_duplicate(client, api_season):
    client.post("/api/players", json={"name": "Justin M."})
    r = client.post("/api/players", json={"name": "Justin", "force": True})
    assert r.status_code == 201


def test_blank_player_name_is_a_400(client, api_season):
    assert client.post("/api/players", json={"name": "  "}).status_code == 400


def test_player_search(client, api_season):
    client.post("/api/players", json={"name": "Justin M."})
    client.post("/api/players", json={"name": "Priya"})

    r = client.get("/api/players", params={"q": "justin"})
    assert r.status_code == 200
    body = r.json()
    assert [m["player"]["name"] for m in body] == ["Justin M."]
    assert body[0]["is_duplicate"] is True

    assert client.get("/api/players", params={"q": ""}).json() == []


def test_checkin_and_checkout(client, api_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")

    r = client.post(f"/api/sessions/{api_session}/checkin", json={"player_id": ada})
    assert r.status_code == 200
    assert [p["player"]["name"] for p in r.json()["players"]] == ["Ada"]

    client.post(f"/api/sessions/{api_session}/checkin", json={"player_id": ben})
    r = client.post(f"/api/sessions/{api_session}/checkout", json={"player_id": ben})
    players = {p["player"]["name"]: p for p in r.json()["players"]}
    assert players["Ada"]["checked_out_at"] is None
    assert players["Ben"]["checked_out_at"] is not None


def test_checkin_unknown_player_is_a_404(client, api_session):
    r = client.post(f"/api/sessions/{api_session}/checkin", json={"player_id": 999})
    assert r.status_code == 404


def test_unknown_session_is_a_404(client):
    assert client.get("/api/sessions/999").status_code == 404


def test_record_game_and_read_it_back(client, api_session, make_api_players):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    r = client.post(
        f"/api/sessions/{api_session}/games",
        json={
            "teams": [
                {"player_ids": [ada, ben], "rank": 1, "score": 5},
                {"player_ids": [cleo, dev], "rank": 2, "score": 3},
            ]
        },
    )
    assert r.status_code == 201
    game = r.json()
    assert game["round_number"] == 1
    assert game["players_on_field"] == 2
    assert [t["rank"] for t in game["teams"]] == [1, 2]

    session = client.get(f"/api/sessions/{api_session}").json()
    assert [g["id"] for g in session["games"]] == [game["id"]]


def test_record_uneven_game_sets_players_on_field(client, api_session, make_api_players):
    ids = make_api_players("A", "B", "C", "D", "E", "F", "G")
    r = client.post(
        f"/api/sessions/{api_session}/games",
        json={
            "teams": [
                {"player_ids": ids[:3], "rank": 1},
                {"player_ids": ids[3:], "rank": 2},
            ]
        },
    )
    assert r.status_code == 201
    assert r.json()["players_on_field"] == 3


@pytest.mark.parametrize(
    "teams, expected_status",
    [
        ([{"player_ids": [1], "rank": 1}], 422),  # fewer than two teams
        ([{"player_ids": [], "rank": 1}, {"player_ids": [2], "rank": 2}], 422),
        ([{"player_ids": [1], "rank": 1}, {"player_ids": [2], "rank": 1}], 400),  # tie
        ([{"player_ids": [1], "rank": 2}, {"player_ids": [2], "rank": 3}], 400),  # no winner
        ([{"player_ids": [1], "rank": 1}, {"player_ids": [1], "rank": 2}], 400),  # same player
    ],
)
def test_record_game_validation(client, api_session, make_api_players, teams, expected_status):
    make_api_players("Ada", "Ben")
    r = client.post(f"/api/sessions/{api_session}/games", json={"teams": teams})
    assert r.status_code == expected_status


def test_edit_game(client, api_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    game = client.post(
        f"/api/sessions/{api_session}/games",
        json={
            "teams": [
                {"player_ids": [ada], "rank": 1, "score": 5},
                {"player_ids": [ben], "rank": 2, "score": 2},
            ]
        },
    ).json()

    r = client.patch(
        f"/api/games/{game['id']}",
        json={
            "teams": [
                {"player_ids": [ada], "rank": 2, "score": 2},
                {"player_ids": [ben], "rank": 1, "score": 5},
            ]
        },
    )
    assert r.status_code == 200
    assert [t["rank"] for t in r.json()["teams"]] == [2, 1]


def test_delete_and_restore_game(client, api_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    game = client.post(
        f"/api/sessions/{api_session}/games",
        json={"teams": [{"player_ids": [ada], "rank": 1}, {"player_ids": [ben], "rank": 2}]},
    ).json()

    r = client.delete(f"/api/games/{game['id']}")
    assert r.status_code == 200
    assert r.json()["deleted_at"] is not None
    assert client.get(f"/api/sessions/{api_session}").json()["games"] == []

    r = client.post(f"/api/games/{game['id']}/restore")
    assert r.status_code == 200
    assert r.json()["deleted_at"] is None
    assert len(client.get(f"/api/sessions/{api_session}").json()["games"]) == 1


def test_delete_unknown_game_is_a_404(client):
    assert client.delete("/api/games/999").status_code == 404
    assert client.patch("/api/games/999", json={}).status_code == 404


def test_double_delete_is_a_400(client, api_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    game = client.post(
        f"/api/sessions/{api_session}/games",
        json={"teams": [{"player_ids": [ada], "rank": 1}, {"player_ids": [ben], "rank": 2}]},
    ).json()
    client.delete(f"/api/games/{game['id']}")
    assert client.delete(f"/api/games/{game['id']}").status_code == 400


def test_openapi_schema_is_served(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for path in [
        "/api/seasons",
        "/api/players",
        "/api/sessions",
        "/api/sessions/{session_id}/checkin",
        "/api/sessions/{session_id}/games",
        "/api/games/{game_id}",
    ]:
        assert path in paths


# --- milestone 3: leaderboard and player detail ---------------------------------


def play_api(client, session_id, winners, losers, score=None):
    teams = [
        {"player_ids": winners, "rank": 1, "score": score[0] if score else None},
        {"player_ids": losers, "rank": 2, "score": score[1] if score else None},
    ]
    return client.post(f"/api/sessions/{session_id}/games", json={"teams": teams})


def test_leaderboard_endpoint(client, api_session, make_api_players):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    play_api(client, api_session, [ada, ben], [cleo, dev], score=(5, 3))

    rows = client.get("/api/leaderboard").json()
    assert [r["rank"] for r in rows] == [1, 2, 3, 4]
    assert rows[0]["rating"] >= rows[-1]["rating"]
    winners = {r["player"]["id"] for r in rows[:2]}
    assert winners == {ada, ben}
    assert rows[0]["wins"] == 1 and rows[0]["losses"] == 0
    assert rows[0]["games_played"] == 1


def test_leaderboard_min_games_filter(client, api_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    play_api(client, api_session, [ada], [ben])
    assert len(client.get("/api/leaderboard", params={"min_games": 1}).json()) == 2
    assert client.get("/api/leaderboard", params={"min_games": 2}).json() == []


def test_leaderboard_without_a_season_is_empty(client):
    assert client.get("/api/leaderboard").json() == []


def test_player_detail_endpoint(client, api_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    play_api(client, api_session, [ada], [ben])

    body = client.get(f"/api/players/{ada}").json()
    assert body["player"]["name"] == "Ada"
    assert len(body["seasons"]) == 1
    assert body["seasons"][0]["season"]["name"] == "Fall 2026"
    assert body["seasons"][0]["wins"] == 1
    assert body["all_time"] == {
        "wins": 1,
        "losses": 0,
        "games_played": 1,
        "seasons_played": 1,
    }


def test_player_history_endpoint(client, api_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    play_api(client, api_session, [ada], [ben])
    play_api(client, api_session, [ada], [ben])

    points = client.get(f"/api/players/{ada}/history").json()
    assert len(points) == 2
    assert points[0]["mu_before"] == 25.0
    assert points[1]["mu_before"] == points[0]["mu_after"]
    assert points[1]["mu_after"] > points[0]["mu_after"]
    assert points[1]["rating_after"] > points[0]["rating_after"]


def test_player_endpoints_404_for_unknown_player(client):
    assert client.get("/api/players/999").status_code == 404
    assert client.get("/api/players/999/history").status_code == 404


def test_player_history_is_empty_for_an_unplayed_player(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    assert client.get(f"/api/players/{ada}/history").json() == []
