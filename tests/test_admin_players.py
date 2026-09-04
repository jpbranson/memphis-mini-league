"""Milestone 4 through the web layer: player management, merge, audit, undo."""

from __future__ import annotations

import re


def text(response) -> str:
    return response.text


def squash(value: str) -> str:
    return re.sub(r"\s+", " ", value)


def check_in_all(client, session_id, player_ids):
    for pid in player_ids:
        client.post(f"/admin/session/{session_id}/checkin", data={"player_id": pid})


def play_round(client, session_id, winners, losers):
    data = {f"assign_{p}": "0" for p in winners}
    data.update({f"assign_{p}": "1" for p in losers})
    data["winner"] = "0"
    return client.post(f"/admin/session/{session_id}/games", data=data)


# --- players page ----------------------------------------------------------------


def test_players_page_lists_everyone(client, api_season, make_api_players):
    make_api_players("Ada", "Priya")
    body = text(client.get("/admin/players"))
    assert "Ada" in body and "Priya" in body
    assert "Rename" in body and "Retire" in body and "Merge" in body


def test_players_page_is_reachable_from_the_organizer_home(client, api_season):
    assert 'href="/admin/players"' in text(client.get("/admin"))


def test_player_search_filters_the_list(client, api_season, make_api_players):
    make_api_players("Ada", "Priya")
    body = text(client.get("/admin/players/search", params={"q": "Ada"}))
    assert "Ada" in body
    assert "Priya" not in body


def test_rename_from_the_page(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    r = client.post(f"/admin/players/{ada}/rename", data={"name": "Ada L."})
    assert r.status_code == 200
    assert "Renamed to Ada L." in text(r)
    assert client.get(f"/api/players/{ada}").json()["player"]["name"] == "Ada L."


def test_rename_clash_is_shown(client, api_season, make_api_players):
    ada, priya = make_api_players("Ada", "Priya")
    r = client.post(f"/admin/players/{ada}/rename", data={"name": "Priya"})
    assert r.status_code == 400
    assert "already named" in text(r)


def test_retire_and_reinstate_from_the_page(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    r = client.post(f"/admin/players/{ada}/active", data={"active": "false"})
    assert "Ada deactivated." in text(r)
    assert "retired" in text(r)

    r = client.post(f"/admin/players/{ada}/active", data={"active": "true"})
    assert "Ada reactivated." in text(r)


def test_retired_player_leaves_the_check_in_list(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    client.post(f"/admin/players/{ada}/active", data={"active": "false"})
    board = text(client.get(f"/admin/session/{page_session}"))
    check_in_list = board.split('id="checkin-list"')[1].split("</div>")[0]
    assert "Ben" in check_in_list
    assert "Ada" not in check_in_list


# --- merge flow ------------------------------------------------------------------


def test_merge_form_offers_candidates_closest_first(client, api_season, make_api_players):
    justin_m, priya, justin = make_api_players("Justin M.", "Priya", "Justin")
    body = text(client.get(f"/admin/players/{justin}/merge"))
    assert "Merge Justin" in body
    assert body.index("Justin M.") < body.index("Priya")


def test_merge_confirmation_shows_the_consequences(
    client, page_session, make_api_players
):
    justin_m, justin, other = make_api_players("Justin M.", "Justin", "Other")
    check_in_all(client, page_session, [justin, other])
    play_round(client, page_session, [justin], [other])

    body = squash(text(client.get(f"/admin/players/{justin}/merge?target_id={justin_m}")))
    assert "Merge Justin into Justin M.?" in body
    assert "Games moving across" in body
    assert "Session check-ins moving across" in body
    assert "All ratings are recomputed" in body


def test_merge_confirmation_refuses_opponents(client, page_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    body = squash(text(client.get(f"/admin/players/{ada}/merge?target_id={ben}")))
    assert "cannot be the same person" in body
    assert "Merge into" not in body, "no confirm button on an impossible merge"


def test_merge_applies_and_redirects_to_the_audit_log(
    client, page_session, make_api_players
):
    dup, keep, other = make_api_players("Justin", "Justin M.", "Other")
    check_in_all(client, page_session, [dup, other])
    play_round(client, page_session, [dup], [other])

    r = client.post(
        f"/admin/players/{dup}/merge", data={"target_id": keep}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/audit"

    detail = client.get(f"/api/players/{keep}").json()
    assert detail["all_time"]["games_played"] == 1
    assert client.get(f"/api/players/{dup}").json()["player"]["merged_into"] == keep


def test_merge_post_of_an_impossible_pair_is_refused(
    client, page_session, make_api_players
):
    ada, ben = make_api_players("Ada", "Ben")
    check_in_all(client, page_session, [ada, ben])
    play_round(client, page_session, [ada], [ben])

    r = client.post(f"/admin/players/{ada}/merge", data={"target_id": ben})
    assert r.status_code == 400
    assert "played against each other" in squash(text(r))
    assert client.get(f"/api/players/{ada}").json()["player"]["merged_into"] is None


def test_merged_player_offers_no_further_actions(client, api_season, make_api_players):
    dup, keep = make_api_players("Justin", "Justin M.")
    client.post(f"/admin/players/{dup}/merge", data={"target_id": keep})
    body = text(client.get("/admin/players"))
    assert "merged away" in body


# --- audit log and undo ----------------------------------------------------------


def test_audit_log_describes_what_happened(client, api_season, make_api_players):
    dup, keep = make_api_players("Justin", "Justin M.")
    client.post(f"/admin/players/{keep}/rename", data={"name": "Justin Moore"})
    client.post(f"/admin/players/{dup}/merge", data={"target_id": keep})

    body = squash(text(client.get("/admin/audit")))
    assert "Merged Justin into Justin Moore" in body
    assert "Renamed Justin M. to Justin Moore" in body
    assert "Undo this merge" in body


def test_undo_from_the_audit_log_restores_the_player(
    client, page_session, make_api_players
):
    dup, keep, other = make_api_players("Justin", "Justin M.", "Other")
    check_in_all(client, page_session, [dup, other])
    play_round(client, page_session, [dup], [other])
    client.post(f"/admin/players/{dup}/merge", data={"target_id": keep})

    audit = client.get("/api/admin/audit").json()
    merge_id = next(e["id"] for e in audit if e["action"] == "merge_players")

    r = client.post(f"/admin/audit/{merge_id}/undo")
    assert r.status_code == 200
    assert "Merge undone. Ratings replayed." in text(r)

    assert client.get(f"/api/players/{dup}").json()["player"]["merged_into"] is None
    assert client.get(f"/api/players/{dup}").json()["all_time"]["games_played"] == 1
    assert client.get(f"/api/players/{keep}").json()["all_time"]["games_played"] == 0


def test_undo_is_offered_once_only(client, api_season, make_api_players):
    dup, keep = make_api_players("Justin", "Justin M.")
    client.post(f"/admin/players/{dup}/merge", data={"target_id": keep})
    merge_id = next(
        e["id"] for e in client.get("/api/admin/audit").json() if e["action"] == "merge_players"
    )
    client.post(f"/admin/audit/{merge_id}/undo")

    body = squash(text(client.get("/admin/audit")))
    assert "already undone" in body
    assert "Undo this merge" not in body

    r = client.post(f"/admin/audit/{merge_id}/undo")
    assert r.status_code == 400
    assert "already been undone" in text(r)


def test_audit_log_starts_empty(client):
    assert "Nothing recorded yet." in text(client.get("/admin/audit"))


# --- API -------------------------------------------------------------------------


def test_api_patch_player_renames_and_deactivates(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    r = client.patch(f"/api/players/{ada}", json={"name": "Ada L.", "active": False})
    assert r.status_code == 200
    assert r.json()["name"] == "Ada L."
    assert r.json()["active"] is False


def test_api_patch_player_validation(client, api_season, make_api_players):
    ada, priya = make_api_players("Ada", "Priya")
    assert client.patch(f"/api/players/{ada}", json={}).status_code == 400
    assert client.patch(f"/api/players/{ada}", json={"name": "Priya"}).status_code == 400
    assert client.patch("/api/players/999", json={"name": "X"}).status_code == 404


def test_api_merge_and_undo(client, api_session, make_api_players):
    dup, keep, other = make_api_players("Justin", "Justin M.", "Other")
    client.post(
        f"/api/sessions/{api_session}/games",
        json={
            "teams": [
                {"player_ids": [dup], "rank": 1},
                {"player_ids": [other], "rank": 2},
            ]
        },
    )

    r = client.post(f"/api/players/{dup}/merge-into", json={"target_player_id": keep})
    assert r.status_code == 200
    entry = r.json()
    assert entry["action"] == "merge_players"
    assert entry["payload"]["target"]["id"] == keep
    assert client.get(f"/api/players/{keep}").json()["all_time"]["games_played"] == 1

    r = client.post(f"/api/admin/audit/{entry['id']}/undo")
    assert r.status_code == 200
    assert r.json()["action"] == "undo_merge"
    assert client.get(f"/api/players/{dup}").json()["all_time"]["games_played"] == 1


def test_api_merge_conflict_is_a_409(client, api_session, make_api_players):
    ada, ben = make_api_players("Ada", "Ben")
    client.post(
        f"/api/sessions/{api_session}/games",
        json={
            "teams": [{"player_ids": [ada], "rank": 1}, {"player_ids": [ben], "rank": 2}]
        },
    )
    r = client.post(f"/api/players/{ada}/merge-into", json={"target_player_id": ben})
    assert r.status_code == 409
    assert "played against each other" in r.json()["detail"]


def test_api_merge_validation(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    assert (
        client.post(f"/api/players/{ada}/merge-into", json={"target_player_id": ada}).status_code
        == 400
    )
    assert (
        client.post("/api/players/999/merge-into", json={"target_player_id": ada}).status_code
        == 404
    )


def test_api_audit_list_and_undo_validation(client, api_season, make_api_players):
    (ada,) = make_api_players("Ada")
    assert client.get("/api/admin/audit").json() == []
    client.patch(f"/api/players/{ada}", json={"name": "Renamed"})

    entries = client.get("/api/admin/audit").json()
    assert [e["action"] for e in entries] == ["rename_player"]

    assert client.post(f"/api/admin/audit/{entries[0]['id']}/undo").status_code == 400
    assert client.post("/api/admin/audit/999/undo").status_code == 404
