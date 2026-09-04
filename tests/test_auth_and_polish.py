"""Milestone 7: sign-in, session history, seasons, settings (design doc 3, 7)."""

from __future__ import annotations

import re

import pytest

from mini_league.web.auth import needs_organizer


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


# --- which routes are protected --------------------------------------------------


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/admin"),
        ("GET", "/admin/players"),
        ("GET", "/admin/settings"),
        ("GET", "/admin/seasons"),
        ("POST", "/admin/session/new"),
        ("GET", "/api/admin/audit"),
        ("POST", "/api/players"),
        ("PATCH", "/api/games/1"),
        ("DELETE", "/api/games/1"),
    ],
)
def test_changing_things_needs_an_organizer(method, path):
    assert needs_organizer(method, path) is True


@pytest.mark.parametrize(
    "method, path",
    [
        ("GET", "/"),
        ("GET", "/players/1"),
        ("GET", "/sessions"),
        ("GET", "/sessions/1"),
        ("GET", "/panel/leaderboard"),
        ("GET", "/api/leaderboard"),
        ("GET", "/api/players/1"),
        ("GET", "/api/sessions/1"),
        ("GET", "/static/htmx.min.js"),
        ("GET", "/login"),
        ("POST", "/login"),
        ("GET", "/health"),
    ],
)
def test_reading_stays_open(method, path):
    assert needs_organizer(method, path) is False


def test_a_lookalike_path_is_not_treated_as_admin():
    """/administrators would not be an admin route just by sharing a prefix."""
    assert needs_organizer("GET", "/administrators") is False
    assert needs_organizer("GET", "/players/1/admin") is False


# --- signing in ------------------------------------------------------------------


def test_public_pages_work_without_signing_in(visitor):
    for path in ("/", "/sessions", "/login", "/health"):
        assert visitor.get(path).status_code == 200, path


def test_admin_pages_redirect_a_visitor_to_sign_in(visitor):
    r = visitor.get("/admin", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login?next=/admin"


def test_the_redirect_remembers_where_you_were_going(visitor):
    r = visitor.get("/admin/session/3?x=1", follow_redirects=False)
    assert r.headers["location"] == "/login?next=/admin/session/3?x=1"


def test_the_api_answers_a_visitor_with_401_not_a_redirect(visitor):
    r = visitor.post("/api/players", json={"name": "Ada"})
    assert r.status_code == 401
    assert r.json()["detail"] == "organizer sign-in required"


def test_signing_in_and_out(visitor):
    r = visitor.post(
        "/login", data={"password": "test-password", "next": "/admin"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"
    assert visitor.get("/admin").status_code == 200

    r = visitor.post("/logout", follow_redirects=False)
    assert r.status_code == 303
    assert visitor.get("/admin", follow_redirects=False).status_code == 303


def test_a_wrong_password_is_refused(visitor):
    r = visitor.post("/login", data={"password": "guess", "next": "/admin"})
    assert r.status_code == 401
    assert "not right" in text(r)
    assert visitor.get("/admin", follow_redirects=False).status_code == 303


def test_an_empty_password_never_signs_you_in(visitor):
    assert visitor.post("/login", data={"password": ""}).status_code == 401


@pytest.mark.parametrize(
    "destination",
    [
        "https://example.com/steal",
        "//example.com/steal",       # protocol-relative: a browser leaves the site
        "/\example.com/steal",       # some browsers normalise the backslash
        "",
    ],
)
def test_sign_in_only_follows_local_destinations(visitor, destination):
    """An off-site next= must not turn the login form into an open redirect."""
    r = visitor.post(
        "/login",
        data={"password": "test-password", "next": destination},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin"


def test_sign_in_does_follow_a_genuine_local_destination(visitor):
    r = visitor.post(
        "/login",
        data={"password": "test-password", "next": "/admin/players"},
        follow_redirects=False,
    )
    assert r.headers["location"] == "/admin/players"


def test_the_login_page_also_refuses_an_off_site_destination(visitor):
    body = visitor.get("/login", params={"next": "//example.com"}).text
    assert 'value="/admin"' in body
    assert "example.com" not in body


def test_the_header_offers_the_right_action(visitor, client):
    assert "Sign in" in text(visitor.get("/"))
    signed_in = text(client.get("/"))
    assert "Sign out" in signed_in
    assert 'href="/admin"' in signed_in


# --- an instance with no password at all -----------------------------------------


def test_without_a_password_the_organizer_screens_are_closed(unconfigured):
    r = unconfigured.get("/admin")
    assert r.status_code == 503
    assert "No organizer password is set" in text(r)


def test_without_a_password_the_api_refuses_writes(unconfigured):
    r = unconfigured.post("/api/players", json={"name": "Ada"})
    assert r.status_code == 503
    assert "MINI_LEAGUE_PASSWORD" in r.json()["detail"]


def test_without_a_password_reading_still_works(unconfigured):
    assert unconfigured.get("/").status_code == 200
    assert unconfigured.get("/api/leaderboard").status_code == 200


def test_without_a_password_you_cannot_sign_in(unconfigured):
    assert unconfigured.post("/login", data={"password": "anything"}).status_code == 401
    assert "not possible" in text(unconfigured.get("/login"))


# --- session history -------------------------------------------------------------


def test_session_list_shows_counts(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben], score=(5, 3))

    body = squash(text(client.get("/sessions")))
    assert "2026-09-05" in body
    assert f'href="/sessions/{page_session}"' in body
    assert "Fall 2026" in body


def test_session_detail_shows_each_round(client, page_session, make_api_players):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [ada, ben, cleo, dev])
    play_round(client, page_session, [ada, ben], [cleo, dev], score=(5, 3))

    body = squash(text(client.get(f"/sessions/{page_session}")))
    assert "shirt-light" in body and "shirt-dark" in body
    assert "Ada" in body and "Cleo" in body
    assert "<b>won</b>" in body
    assert "2 a side" in body
    assert "Who was there (4)" in body


def test_session_detail_notes_substitutes(client, page_session, make_api_players):
    ids = make_api_players(*[f"P{i}" for i in range(7)])
    check_in_all(client, page_session, ids)
    play_round(client, page_session, ids[:4], ids[4:])
    assert "subs rotating" in squash(text(client.get(f"/sessions/{page_session}")))


def test_session_detail_shows_what_each_side_was_rated_beforehand(
    client, page_session, make_api_players
):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [ada, ben, cleo, dev])
    play_round(client, page_session, [ada, ben], [cleo, dev], score=(5, 3))

    body = squash(text(client.get(f"/sessions/{page_session}")))
    assert "collective rating as it stood before that round" in body
    # Nobody had played, so both sides were two starting ratings.
    assert body.count('<span class="rating" style="white-space:nowrap">0</span>') == 2


def test_session_detail_ratings_are_the_ones_from_the_time_not_todays(
    client, page_session, make_api_players
):
    """Read back later, a round still shows the standing its teams were picked on."""
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [ada, ben, cleo, dev])
    play_round(client, page_session, [ada, ben], [cleo, dev], score=(5, 3))
    play_round(client, page_session, [ada, ben], [cleo, dev], score=(5, 1))

    body = squash(text(client.get(f"/sessions/{page_session}")))
    shown = [
        int(n)
        for n in re.findall(r'class="rating" style="white-space:nowrap">(-?\d+)<', body)
    ]
    assert len(shown) == 4
    # Round one: everyone new, so both sides at the starting rating. Round two:
    # the winners are worth more than they were and the losers less.
    assert shown[0] == shown[1] == 0
    assert shown[2] > shown[0]
    assert shown[3] < shown[1]

    # And none of them is the pair's rating as it stands now, after both rounds.
    current = sum(
        client.get(f"/api/players/{pid}").json()["seasons"][0]["rating"]
        for pid in (ada, ben)
    )
    assert current not in shown


def test_session_detail_leaves_out_a_deleted_round(client, page_session, make_api_players):
    ada, ben, cleo, dev = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [ada, ben, cleo, dev])
    play_round(client, page_session, [ada, ben], [cleo, dev], score=(5, 3))
    game_id = client.get(f"/api/sessions/{page_session}").json()["games"][0]["id"]
    client.post(f"/admin/games/{game_id}/delete")

    body = squash(text(client.get(f"/sessions/{page_session}")))
    assert 'class="rating" style="white-space:nowrap"' not in body


def test_session_history_is_public(visitor, tmp_path):
    assert visitor.get("/sessions").status_code == 200


def test_unknown_session_is_a_404(client):
    assert client.get("/sessions/999").status_code == 404


# --- seasons ---------------------------------------------------------------------


def test_seasons_page_lists_and_counts(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    body = squash(text(client.get("/admin/seasons")))
    assert "Fall 2026" in body
    assert "1 session" in body
    assert "2 rated players" in body
    assert "current" in body


def test_starting_a_new_season_from_the_page(client, api_season):
    r = client.post(
        "/admin/seasons",
        data={"name": "Spring 2027", "start_date": "2027-03-01"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    seasons = client.get("/api/seasons").json()
    assert {s["name"] for s in seasons} == {"Fall 2026", "Spring 2027"}
    closed = next(s for s in seasons if s["name"] == "Fall 2026")
    assert closed["end_date"] == "2027-02-28"


def test_renaming_a_season(client, api_season):
    r = client.post(
        f"/admin/seasons/{api_season}/rename",
        data={"name": "Autumn 2026"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert client.get("/api/seasons").json()[0]["name"] == "Autumn 2026"


def test_renaming_into_a_clash_is_refused(client, api_season):
    client.post("/admin/seasons", data={"name": "Spring 2027", "start_date": "2027-03-01"})
    r = client.post(f"/admin/seasons/{api_season}/rename", data={"name": "Spring 2027"})
    assert "already exists" in text(r)
    assert {s["name"] for s in client.get("/api/seasons").json()} == {
        "Fall 2026",
        "Spring 2027",
    }


# --- settings --------------------------------------------------------------------


def test_settings_shows_the_parameters_in_use(client, api_season):
    body = squash(text(client.get("/admin/settings")))
    assert "Starting skill (mu)" in body
    assert "25.00" in body
    assert "Skill to win chance (beta)" in body
    assert "Variety weight" in body
    assert "MINI_LEAGUE_BETA" in body


def test_settings_offers_a_replay(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])
    before = client.get("/api/leaderboard", params={"min_games": 0}).json()

    r = client.post("/admin/settings/recompute")
    assert r.status_code == 200
    assert "Every season has been replayed." in text(r)

    after = client.get("/api/leaderboard", params={"min_games": 0}).json()
    assert [(row["player"]["id"], row["rating"]) for row in after] == [
        (row["player"]["id"], row["rating"]) for row in before
    ]


def test_settings_warns_when_no_password_is_set(unconfigured):
    """Unreachable in practice, since the page is closed, but the warning
    exists for an instance started with the gate disabled."""
    assert unconfigured.get("/admin/settings").status_code == 503


def test_organizer_home_links_to_the_new_screens(client, api_season):
    body = text(client.get("/admin"))
    for path in ("/admin/players", "/admin/seasons", "/admin/settings"):
        assert f'href="{path}"' in body


# --- health ----------------------------------------------------------------------


def test_health_reports_the_database_and_schema(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    # A migrated database names its revision; the test schema is created
    # directly, so either a revision or "unmigrated" is correct here.
    assert body["schema"]


def test_health_needs_no_sign_in(visitor, unconfigured):
    assert visitor.get("/health").json()["status"] == "ok"
    assert unconfigured.get("/health").json()["status"] == "ok"


# --- editing from the session history page ---------------------------------------


def played_session(client, page_session, make_api_players):
    a, b, c, d = make_api_players("Ada", "Ben", "Cleo", "Dev")
    check_in_all(client, page_session, [a, b, c, d])
    play_round(client, page_session, [a, b], [c, d], score=(5, 3))
    body = text(client.get(f"/sessions/{page_session}"))
    return int(re.search(r"/admin/games/(\d+)/", body).group(1))


def test_a_visitor_sees_no_edit_controls(visitor, client, page_session, make_api_players):
    played_session(client, page_session, make_api_players)
    body = text(visitor.get(f"/sessions/{page_session}"))
    assert "Ada" in body, "the history itself stays public"
    # Assert on the controls themselves; the shared script and stylesheet
    # mention these words on every page.
    assert 'href="/admin/games/' not in body
    assert 'action="/admin/games/' not in body
    assert "<span data-panel hidden>" not in squash(body)


def test_an_organizer_gets_edit_and_delete_on_the_history_page(
    client, page_session, make_api_players
):
    game_id = played_session(client, page_session, make_api_players)
    body = squash(text(client.get(f"/sessions/{page_session}")))
    assert f'href="/admin/games/{game_id}/edit"' in body
    assert f'action="/admin/games/{game_id}/delete"' in body
    assert "<span data-panel hidden>" in body, "confirmation starts hidden"
    assert f'name="return_to" value="/sessions/{page_session}"' in body


def test_deleting_from_the_history_page_returns_there(
    client, page_session, make_api_players
):
    game_id = played_session(client, page_session, make_api_players)
    r = client.post(
        f"/admin/games/{game_id}/delete",
        data={"return_to": f"/sessions/{page_session}"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/sessions/{page_session}"
    assert client.get(f"/api/sessions/{page_session}").json()["games"] == []


def test_undo_is_offered_on_the_history_page_and_works(
    client, page_session, make_api_players
):
    game_id = played_session(client, page_session, make_api_players)
    client.post(f"/admin/games/{game_id}/delete",
                data={"return_to": f"/sessions/{page_session}"})

    body = squash(text(client.get(f"/sessions/{page_session}")))
    assert "Round 1, not counted" in body
    assert f'action="/admin/games/{game_id}/restore"' in body

    r = client.post(
        f"/admin/games/{game_id}/restore",
        data={"return_to": f"/sessions/{page_session}"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert len(client.get(f"/api/sessions/{page_session}").json()["games"]) == 1


def test_return_to_cannot_send_you_off_the_site(client, page_session, make_api_players):
    game_id = played_session(client, page_session, make_api_players)
    r = client.post(
        f"/admin/games/{game_id}/delete",
        data={"return_to": "//example.com/steal"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/sessions/{page_session}"


def test_the_board_delete_still_returns_the_board(client, page_session, make_api_players):
    game_id = played_session(client, page_session, make_api_players)
    r = client.post(f"/admin/games/{game_id}/delete")
    assert r.status_code == 200
    assert "Round deleted" in text(r)


def test_settings_lists_the_designation_weight(client, api_season):
    body = squash(text(client.get("/admin/settings")))
    assert "Designation weight" in body
    assert "0.80" in body
