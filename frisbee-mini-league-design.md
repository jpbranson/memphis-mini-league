# Frisbee Mini League — Design Document

## 1. Overview

A small web app for running pickup-style ultimate frisbee mini-leagues. On a given morning, whoever shows up gets entered into the app, which generates balanced teams. Games are played, results are logged, player ratings update, and the next round of teams is generated. A leaderboard shows current rankings, and each player has a rating history over time.

**Core loop (one "session"):**

1. Organizer opens the app, creates a session, and checks in the players present.
2. App generates balanced teams for the round.
3. Players play.
4. Organizer records the result (winner, optional score).
5. Ratings update; leaderboard refreshes.
6. Organizer generates the next round of teams. Repeat until the session ends.

**Key constraint:** team sizes vary from morning to morning (2v2, 3v3, 4v4, occasionally uneven like 3v4). The rating system and team generator must handle any team size, including uneven teams.

## 2. Goals and non-goals

**Goals**
- Fast, low-friction data entry on a phone at the field.
- A rating system that is fair across variable team sizes and converges quickly with few games.
- Team generation that is both balanced (close ratings) and varied (people don't play with the same teammates every round).
- A public, read-only leaderboard and per-player rating history.
- Simple enough to run on a free/cheap host with a single organizer account.

**Non-goals (for now)**
- Player self-registration, logins for every player, or per-player permissions.
- Multi-league / multi-organization support.
- Native mobile apps (responsive web only).
- Stat tracking beyond wins, losses, and scores (no goals, assists, etc.).

## 3. Users and roles

| Role | What they do | Auth |
|---|---|---|
| **Organizer** | Creates sessions, checks players in, generates teams, records results, edits/deletes mistakes, adds new players | Single shared password or a small allowlist of accounts |
| **Player / spectator** | Views leaderboard, player pages, session history | None (public read) |

There is no per-player login. Anyone can view; only the organizer can write.

## 4. Rating algorithm

### 4.1 Choice: TrueSkill

Use **TrueSkill** (Microsoft's Bayesian rating system). It is the right fit because:

- It natively supports **teams of any size** — each team's skill is the sum of its players' skills — and supports **partial play weights** for the case where one team carries a substitute (see §5.3).
- It represents each player as a distribution — a mean (**μ**, "mu", best estimate of skill) and a standard deviation (**σ**, "sigma", how uncertain that estimate is). New players have high σ and move quickly; established players move slowly. This is important for a league with few games per player.
- Well-documented, with mature libraries in Python (`trueskill`) and JavaScript/TypeScript (`ts-trueskill`).

Elo is not appropriate: it is a two-player system with no principled way to handle teams of varying size.

### 4.2 Parameters

Use the TrueSkill defaults unless simulation shows a reason to change them:

| Parameter | Default | Meaning |
|---|---|---|
| μ₀ (initial mean) | 25 | Starting skill estimate for a new player |
| σ₀ (initial std dev) | 25/3 ≈ 8.33 | Starting uncertainty |
| β (beta) | 25/6 ≈ 4.17 | How much skill difference translates to win probability. Lower = more "deterministic" games |
| τ (tau) | 25/300 ≈ 0.083 | Small amount of uncertainty added before each game, so ratings never fully freeze |
| draw probability | 0 | Ties are impossible (games are played to 3 or 5) |

Make these configurable in a single settings file so they can be tuned later.

### 4.3 Displayed rating

Show players a single number: **conservative rating = μ − 3σ** (rounded, optionally rescaled to a friendlier range, e.g. multiply by 40 and add 1000 so new players start near 0 and typical established players sit around 1000–1500). This penalizes uncertainty, so a brand-new player who won one game doesn't jump to the top of the leaderboard.

Also store and expose raw μ and σ for the player page.

### 4.4 Scores

Scores are **stored but do not affect ratings**. Only win/loss feeds TrueSkill. Scores appear on session and player pages for interest.

### 4.5 Seasons

Ratings can be **reset per season** while keeping all history.

- A `seasons` table holds named date ranges (e.g. "Fall 2026"). Every session belongs to exactly one season.
- Ratings are computed **within a season**: at the start of a season every player restarts at μ₀/σ₀, and only that season's games are replayed.
- The leaderboard defaults to the current season, with a season selector to view past seasons' final standings. Player pages show rating history per season, plus an all-time record (total W-L, seasons played).
- Starting a new season is an organizer action (`/admin/seasons`). It does not delete or alter anything — old sessions, games, and rating history remain queryable.
- Optional setting for later: carry-over, where a new season starts each player at their previous μ but with σ reset to σ₀ (a "soft reset"). Not needed for v1.

### 4.6 Rating recomputation

Ratings must be **recomputable from scratch** by replaying all games in chronological order. This is required for:
- Correcting a mis-recorded result (edit the game, replay everything after it).
- Merging duplicate players (see §6.1).
- Changing algorithm parameters.
- Rebuilding history if the rating table is ever corrupted.

Implement a single `recompute_ratings(season_id)` function that clears that season's rating history and replays its games, plus `recompute_all_ratings()` for every season. Every write path (record game, edit game, delete game, merge players, move game between seasons) should call it. Performance is not a concern at this scale (hundreds of games).

## 5. Team generation

### 5.1 Inputs
- The list of checked-in players for the session, with their current μ and σ, and their designations for the day (see §5.4).
- Desired format: number of teams (usually 2, but support 2+ for round-robin mornings) and team size, or "auto" (fill evenly, allow one team to be one player larger if odd).
- History of who has played together in this session (to encourage variety).

### 5.2 Algorithm

1. **Enumerate or sample candidate splits.** For ≤ 12 players and 2 teams, enumerate all splits (at most a few thousand). For larger groups, randomly sample ~5,000 splits.
2. **Score each split** with a cost function:
   - `balance_cost`: difference between the teams' predicted win probabilities (TrueSkill gives this directly via team skill sums and variances). Target is 50/50.
   - `variety_cost`: number of teammate pairs that have already been on the same team in this session, weighted by how recent.
   - `designation_cost`: how unevenly WMPs and MMPs are spread across the teams (see §5.4). Only scored when the organizer has asked for an even coed split; otherwise the term is absent, not zero.
   - `total = w_balance × balance_cost + w_variety × variety_cost + w_designation × designation_cost` (start with `w_balance = 1.0`, `w_variety = 0.3`, `w_designation = 0.8`).
3. **Pick randomly among the top N** (e.g. top 5) splits rather than always the single best, so teams aren't deterministic.
4. Present the teams with the predicted win probability (e.g. "Team A 52% – Team B 48%") so the organizer can sanity-check.
5. Allow the organizer to **manually swap two players** and see the updated win probability, or **regenerate**.

### 5.3 Edge cases
- Odd number of players (e.g. 7 → 3v3 with one team carrying a sub): games are always played with equal numbers on the field, so the larger team must **not** be treated as stronger. Use TrueSkill's **partial play weights**: each player on the larger team gets weight `on_field / roster_size` (e.g. 3/4 = 0.75), players on the full-strength team get weight 1.0. This makes the team skill sums comparable and shrinks the sub team's rating updates proportionally. Store `players_on_field` on each game so weights can be derived on replay. All roster members still get the W/L on their record.
- The team generator should treat the sub team's predicted strength using the same weights, and should prefer assigning the extra player to whichever split keeps win probability closest to 50/50.
- Player arrives mid-session: check them in; they're included in the next generation.
- Player leaves mid-session: check them out; excluded from next generation. Past games are unaffected.
- Player sitting out a round (too many players): support a "bench" list; prioritize benching players who have played the most rounds this session.

### 5.4 Designations (coed rounds)

The league sometimes plays coed, which means matching people up by designation as well as by rating. Two exist: **WMP** (woman matching player) and **MMP** (man matching player). A designation says who you are matched up against and nothing else — it never touches a rating, a result, or a replay.

- A player optionally has one **standing designation**. Most leagues never set one, and the app works exactly as it did when nobody has.
- A session can **override** a player's designation for the day, because someone can turn up and play the other side of a match-up without that being a permanent change to their record. The override has three answers: WMP, MMP, or *none today* — the last of which is different from having no override at all.
- The **even-up toggle** on the day-of board is per round and off by default. Ticked, the generator scores `designation_cost`; unticked, it scores exactly what it scored before designations existed.
- `designation_cost` counts both designations rather than one, because with undesignated players an even split of WMPs does not imply an even split of MMPs. It is divided by the number of players so it lands in 0..1 alongside the other two costs.
- Benching still goes by rounds played, not designation. With a lopsided turnout the generator can only work with whoever is left on the pitch; the organizer moves people by hand if the split it finds is not the one they want.

## 6. Data model

```
players
  id            integer PK
  name          text, unique among active players
  created_at    timestamp
  active        boolean          -- soft delete / retired
  merged_into   FK players nullable  -- set when this record was merged into another
  designation   text nullable    -- WMP, MMP or null; matchmaking only, see 5.4

seasons
  id            integer PK
  name          text             -- "Fall 2026"
  start_date    date
  end_date      date nullable    -- null = current season
  created_at    timestamp

sessions
  id            integer PK
  season_id     FK seasons
  date          date
  notes         text nullable
  pending_teams json nullable    -- teams picked but not yet played, so a locked
                                 -- phone does not lose the line-up
  created_at    timestamp

session_players                  -- who was checked in
  session_id    FK sessions
  player_id     FK players
  checked_in_at timestamp
  checked_out_at timestamp nullable
  designation_override text nullable  -- this morning only: WMP, MMP, or NONE
                                      -- meaning none today, which is not the
                                      -- same as having no override
  PK (session_id, player_id)

games
  id            integer PK
  session_id    FK sessions
  round_number  integer          -- 1, 2, 3... within the session
  players_on_field integer       -- per team, e.g. 3 for 3v3; drives partial-play weights
  played_at     timestamp
  created_at    timestamp
  deleted_at    timestamp nullable  -- soft delete

game_teams
  id            integer PK
  game_id       FK games
  team_index    integer          -- 0, 1 (or more for round-robin)
  score         integer nullable
  rank          integer          -- 1 = winner, 2 = loser; ties share a rank

game_team_players
  game_team_id  FK game_teams
  player_id     FK players
  PK (game_team_id, player_id)

rating_history                   -- one row per player per game they played
  id            integer PK
  player_id     FK players
  game_id       FK games
  season_id     FK seasons
  mu_before     real
  sigma_before  real
  mu_after      real
  sigma_after   real
  created_at    timestamp

player_season_ratings            -- current snapshot per season (derived, rebuilt on recompute)
  player_id     FK players
  season_id     FK seasons
  mu            real
  sigma         real
  games_played  integer
  wins          integer
  losses        integer
  updated_at    timestamp
  PK (player_id, season_id)

audit_log                        -- who changed what, for undoing mistakes
  id            integer PK
  action        text             -- "merge_players", "edit_game", "delete_game", ...
  payload       json             -- before/after details
  created_at    timestamp
```

**Notes**
- `players.designation` and `session_players.designation_override` are both nullable and hold `WMP`, `MMP`, or (for the override only) `NONE` meaning no designation today. Neither is an input to any rating.
- `rating_history` and `player_season_ratings` are derived tables; `games` + `game_teams` + `game_team_players` are the source of truth.
- Use soft deletes for games so mistakes can be undone and the audit trail is preserved.
- A single session can contain multiple simultaneous games (e.g. 12 people playing two 3v3 games at once). `round_number` groups them.

### 6.1 Player management and merging

Organizers add players on the day, often by name only, so duplicates will happen (e.g. "Justin" added today when "Justin M." already exists). The app must make this easy to prevent and easy to fix.

**Prevention at check-in**
- The "add new player" field does a live fuzzy search over existing players (including inactive ones) and shows matches before creating. Tapping a match checks that player in instead of creating a new one.
- Warn on near-duplicate names ("A player named 'Justin M.' already exists — check them in instead, or create a new player anyway?").

**Merge (fix after the fact)**
- `/admin/players` has a **Merge** action: choose a source player (the duplicate) and a target player (the one to keep).
- On merge:
  1. Reassign every `game_team_players`, `session_players`, and `rating_history` row from source to target.
  2. Validate that no single game ends up with the target player on both teams (if so, refuse with a clear error).
  3. Mark the source as inactive and set `merged_into = target`.
  4. Run `recompute_all_ratings()` so the target's rating reflects the combined game history.
  5. Write an `audit_log` entry with the full before-state so the merge can be reversed.
- Provide **Undo merge** (reads the audit entry, splits the rows back, recomputes).

**Designations on merge**
- The target keeps its own standing designation: the merge says these two records are one person, and the target is the record being kept.
- A day-of override belongs to the `session_players` row it was set on, so where only the source was checked in the override moves across with the row, and where both were the target's row survives. The discarded override is kept in the audit snapshot so undo restores it.

**Other edits**
- Rename a player (all history follows the id, so this is free).
- Set or clear a player's standing designation (logged, since it changes how the balancer treats them from then on).
- Deactivate/reactivate a player (hidden from check-in list and leaderboard, history preserved).
- Move a session to a different season (rare; recompute both seasons).
- Edit a past game's teams/result, or delete it (already covered in §7); each triggers recompute.

## 7. Pages and screens

All pages mobile-first; the organizer will mostly use this on a phone at the field.

### Public
- **`/` Leaderboard** — ranked table for the current season: rank, name, displayed rating, W-L, games played. Season selector to view past seasons' final standings. Toggle to hide players with fewer than N games (default 5) so new players don't clutter the top. Small sparkline of rating trend optional.
- **`/players/:id` Player page** — rating over time for the selected season (line chart of conservative rating and μ with a ±σ band), season W-L, all-time W-L and seasons played, recent games with scores, teammates, and opponents, head-to-head vs other players (v2).
- **`/sessions` Session list** — date, number of players, number of games.
- **`/sessions/:id` Session detail** — each round's teams and result.

### Organizer (behind login)
- **`/admin/session/new`** — pick date (defaults to today; season inferred from date), check in players from a searchable list, add a new player inline with duplicate warning (see §6.1).
- **`/admin/session/:id`** — the main "day of" screen:
  - Checked-in player list with check-in/check-out toggles, each row showing that player's designation for the day with buttons to change it, drop it, or hand them back to their standing one.
  - Format picker: team count, team size / auto, and an **even up WMP/MMP** toggle for coed rounds.
  - **Generate teams** button → shows teams with predicted win % and, when anyone on the pitch has one, each side's designation counts; swap and regenerate controls.
  - **Record result** form: pick winner (tap a team), optional score, submit. On submit, ratings update and the screen is ready for the next round.
  - List of completed rounds this session with edit/delete.
- **`/admin/players`** — add, rename, set or clear a standing designation, deactivate/reactivate, **merge duplicates** with confirmation and undo.
- **`/admin/seasons`** — list seasons, start a new season (ends the current one today), rename.
- **`/admin/settings`** — TrueSkill parameters, team-gen weights, "recompute all ratings" button, audit log view.

## 8. API (if frontend and backend are separate)

```
GET    /api/seasons
GET    /api/leaderboard?season_id=&min_games=5
GET    /api/players?q=justin                -- fuzzy search, includes inactive
GET    /api/players/:id
GET    /api/players/:id/history?season_id=
GET    /api/sessions?season_id=
GET    /api/sessions/:id

POST   /api/sessions                      { date, notes }
POST   /api/sessions/:id/checkin          { player_id }
POST   /api/sessions/:id/checkout         { player_id }
POST   /api/sessions/:id/generate-teams   { team_count, team_size | "auto", exclude_player_ids[] }
        → { teams: [[player_ids]], win_probabilities: [...] }
POST   /api/sessions/:id/games            { teams: [{ player_ids, score?, rank }] }
PATCH  /api/games/:id                     { teams: [...] }   -- triggers recompute
DELETE /api/games/:id                                        -- soft delete, triggers recompute
POST   /api/players                       { name, force?: true }  -- 409 with matches if near-duplicate and !force
PATCH  /api/players/:id                   { name?, active? }
POST   /api/players/:id/merge-into        { target_player_id }    -- triggers recompute, logs audit entry
POST   /api/admin/audit/:id/undo
POST   /api/seasons                       { name, start_date }    -- closes current season
POST   /api/admin/recompute
```

Organizer endpoints require an auth session cookie (single organizer password for v1).

## 9. Tech stack

Single deployable Python app with a file-based database.

- **Backend:** FastAPI
- **Database:** SQLite via SQLAlchemy, migrations with Alembic
- **Ratings:** the `trueskill` package (supports partial-play `weights` for the substitute case in §5.3)
- **Frontend:** server-rendered Jinja2 templates + HTMX for interactivity (check-in toggles, team generation, result submission without full page reloads). No JS build step.
- **Charts:** Chart.js loaded from a CDN for the player rating history chart
- **Auth:** single organizer password, verified server-side, stored as a signed session cookie (e.g. `itsdangerous`)
- **Package/env:** `uv` (or `pip` + `venv`), `pytest` for tests
- **Host:** Fly.io or Railway with a persistent volume for the SQLite file

Structure:
- Ratings logic lives in one isolated module (`ratings.py`) with **unit tests** (known inputs → known TrueSkill outputs, including weighted cases), independent of FastAPI.
- Team generation is a pure function in `teams.py` (players + history + config → candidate splits) so it's easy to test and to reuse in the simulator.
- The simulator (§10) is a standalone script that imports `ratings.py` and `teams.py` directly.

## 10. Simulator / validation

Before trusting the league with real people, validate the rating system with a simulator (a separate script, same rating module):

- Generate N players with hidden "true" skills.
- Simulate mornings: random attendance, team generation using the app's algorithm, game outcomes drawn from the true skills.
- Measure: how many games until the leaderboard's rank order correlates strongly with true skill (e.g. Spearman > 0.9)? Does variable team size (2v2 vs 4v4) bias anyone? Do uneven teams (3v4) systematically favor one side?
- Use results to tune β, τ, initial σ, and team-gen weights.

## 11. Build plan (milestones)

1. **Core + tests** — data model (incl. seasons), migrations, TrueSkill module with unit tests, `recompute_ratings()`.
2. **Organizer flow** — create session, check in with fuzzy search + duplicate warning, record result (manual team entry, no generation yet), edit/delete game.
3. **Leaderboard + player page** — read-only pages with season selector, rating history chart.
4. **Player management** — rename, deactivate, merge with undo, audit log.
5. **Team generation** — balance + variety, swap/regenerate UI, predicted win %.
6. **Simulator** — validate and tune parameters.
7. **Polish** — auth, mobile layout, session history pages, seasons/settings pages, deploy.
8. **v2 candidates** — soft-reset carry-over between seasons, head-to-head stats, benching logic, CSV export, multiple concurrent games per round UI, multiple organizer accounts.

## 12. Decisions made

- **No ties.** Games are played to 3 or 5; `draw_probability = 0`.
- **Seasons.** Ratings reset per season; all past sessions, games, and standings stay viewable.
- **Scores stored, not used.** Only win/loss affects ratings.
- **One organizer account** (single password) for v1.
- **Designations are for matchmaking only.** WMP and MMP change who ends up on which side and nothing else. No rating, result, or replay reads them, so a league can adopt them, drop them, or ignore them entirely without disturbing a single standing.
- **Day-of player entry is the norm.** Duplicate prevention at check-in and a reversible merge tool are v1 features, not extras.
