"""Validate and tune the rating system (design doc section 10, milestone 6).

Runs the checks the design doc asks for before trusting the league with real
people, and sweeps the parameters it names. Nothing here touches a database.

    uv run python scripts/validate_ratings.py                # every check
    uv run python scripts/validate_ratings.py --check sweep  # just one

Not to be confused with scripts/simulate.py, which fills a real database with
plausible sessions to look at in the app.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import fmean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mini_league.settings import RatingConfig, TeamGenConfig  # noqa: E402
from mini_league.simulation import SimulationConfig, simulate_league  # noqa: E402

REPEATS = 5
RULE = "-" * 66
SWEEP_MARK = 20  # games per player at which configurations are compared


def heading(title: str) -> None:
    print(f"\n{title}\n{RULE}")


def repeated(base: SimulationConfig, repeats: int | None = None, **overrides):
    """Run the same experiment on several seeds so noise does not mislead.

    The count is read at call time, not bound as a default: a default would be
    fixed at import and quietly ignore --repeats.
    """
    results = []
    for seed in range(repeats if repeats is not None else REPEATS):
        fields = {**base.__dict__, **overrides, "seed": seed}
        results.append(simulate_league(SimulationConfig(**fields)))
    return results


def convergence_check(base: SimulationConfig) -> None:
    heading("How many games before the leaderboard is right?")
    results = repeated(base)
    print(f"{'games each':>12}{'rank match':>13}")
    curves = [dict(r.convergence) for r in results]
    marks = [3, 5, 8, 12, 16, 20, 25, 30]
    for mark in marks:
        values = []
        for curve in curves:
            nearest = [rho for games, rho in curve.items() if games >= mark]
            if nearest:
                values.append(nearest[0])
        if values:
            print(f"{mark:>12}{fmean(values):>13.3f}")

    reached = [r.games_to_reach(0.9) for r in results]
    hit = [g for g in reached if g is not None]
    if hit:
        print(
            f"\nRank order passes 0.90 after about {fmean(hit):.1f} games each, "
            f"in {len(hit)} of {len(results)} runs."
        )
    else:
        print("\nRank order never reached 0.90 in these runs.")
    print(
        f"Final agreement {fmean([r.final_spearman for r in results]):.3f}, "
        f"average skill error {fmean([r.mean_absolute_error for r in results]):.2f}."
    )


def team_size_check(base: SimulationConfig) -> None:
    heading("Does the size of the game bias anyone?")
    print(f"{'format':>10}{'rank match':>13}{'skill error':>13}{'size bias':>12}")
    for size in (2, 3, 4, 5):
        results = repeated(
            base,
            team_size=size,
            max_on_field=size,
            attendance=(size * 2, min(size * 2 + 2, base.player_count)),
        )
        print(
            f"{f'{size}v{size}':>10}"
            f"{fmean([r.final_spearman for r in results]):>13.3f}"
            f"{fmean([r.mean_absolute_error for r in results]):>13.2f}"
            f"{fmean([r.team_size_bias for r in results]):>12.3f}"
        )
    mixed = repeated(base)
    print(
        f"{'mixed':>10}"
        f"{fmean([r.final_spearman for r in mixed]):>13.3f}"
        f"{fmean([r.mean_absolute_error for r in mixed]):>13.2f}"
        f"{fmean([r.team_size_bias for r in mixed]):>12.3f}"
    )
    print(
        "\nSize bias is the correlation between a player's rating error and the\n"
        "sizes they played at. Near zero means small games and big games leave\n"
        "a player equally well rated."
    )


def uneven_check(base: SimulationConfig) -> None:
    heading("Do uneven teams favour the bigger roster?")
    # Odd turnouts force 4v3, 6v5 and so on.
    results = repeated(base, attendance=(5, 11))
    rates = [r.bigger_roster_win_rate for r in results if r.bigger_roster_win_rate]
    total = sum(r.uneven_games for r in results)
    if rates:
        margin = (0.25 / total) ** 0.5 * 2 if total else 0
        print(
            f"The bigger roster won {fmean(rates):.1%} of {total} uneven games, "
            f"give or take {margin:.1%}.\n"
            "Fifty percent is the target: the extra player is a substitute, not\n"
            "an advantage, and partial play is meant to cancel it out."
        )
    else:
        print("No uneven games occurred.")

    control = repeated(base, attendance=(5, 11), use_partial_play=False)

    def bias_row(label: str, runs) -> str:
        values = [r.substitute_bias for r in runs]
        spread = stdev(values) / len(values) ** 0.5 if len(values) > 1 else 0.0
        return (
            f"\n{label:>18}{fmean(values):>+11.3f} +/- {2 * spread:<7.3f}"
            f"{fmean([r.mean_absolute_error for r in runs]):>14.2f}"
        )

    print(f"\n{'':>18}{'substitute bias':>23}{'skill error':>14}")
    print(
        (bias_row("partial play on", results) + bias_row("partial play off", control)).lstrip("\n")
    )
    print(
        "\nSubstitute bias is the correlation between a player's rating error and\n"
        "how often they were on an oversized roster. Zero means rotating off the\n"
        "field costs them nothing; the margin shown is two standard errors, so a\n"
        "figure whose range spans zero is not evidence of anything. The second row\n"
        "is the same world with the rating system blind to substitutions, which is\n"
        "what the feature prevents. Use --repeats 20 or more to narrow the margins."
    )


def calibration_check(base: SimulationConfig) -> None:
    heading("Are the predicted win percentages honest?")
    results = repeated(base)
    merged: dict[float, list[tuple[float, int]]] = {}
    for result in results:
        for predicted, actual, count in result.calibration:
            merged.setdefault(predicted, []).append((actual, count))
    print(f"{'predicted':>11}{'actual':>10}{'games':>9}")
    for predicted in sorted(merged):
        rows = merged[predicted]
        total = sum(count for _, count in rows)
        actual = sum(a * count for a, count in rows) / total
        print(f"{predicted:>11.0%}{actual:>10.0%}{total:>9}")
    print(
        f"\nAverage gap {fmean([r.calibration_error for r in results]):.3f}. "
        "Small means a\npredicted 70% really does win about seven times in ten."
    )


def sweep_check(base: SimulationConfig) -> None:
    heading("Which parameters converge fastest?")
    print(
        "The simulated world keeps a fixed amount of game-to-game variation, so\n"
        "these rows show which assumption suits it best, not which one wins by\n"
        "matching itself."
    )
    truth = base.rating.beta  # the world's real noise, held constant

    def run(label: str, rating: RatingConfig, teams: TeamGenConfig | None = None):
        results = repeated(
            base,
            rating=rating,
            performance_sigma=truth,
            **({"team_gen": teams} if teams else {}),
        )
        # Compared at a fixed number of games rather than by when each run
        # first crossed a threshold, which several runs never do.
        early = [r.spearman_at(SWEEP_MARK) for r in results]
        early = [rho for rho in early if rho is not None]
        print(
            f"{label:>22}"
            f"{fmean([r.final_spearman for r in results]):>12.3f}"
            f"{fmean([r.mean_absolute_error for r in results]):>12.2f}"
            f"{(f'{fmean(early):.3f}' if early else 'n/a'):>10}"
            f"{fmean([r.calibration_error for r in results]):>10.3f}"
        )

    header = (
        f"{'setting':>22}{'rank match':>12}{'skill error':>12}"
        f"{f'@{SWEEP_MARK} games':>10}{'calib':>10}"
    )
    print(f"\n{header}")
    for factor in (0.5, 0.75, 1.0, 1.5, 2.0):
        run(f"beta x{factor}", RatingConfig(beta=truth * factor))
    print()
    for tau_factor in (0.0, 1.0, 4.0):
        run(f"tau x{tau_factor}", RatingConfig(tau=DEFAULT_TAU * tau_factor))
    print()
    for sigma_factor in (0.5, 1.0, 1.5):
        run(
            f"start sigma x{sigma_factor}",
            RatingConfig(sigma=(25 / 3) * sigma_factor),
        )
    print()
    for variety in (0.0, 0.3, 1.0):
        run(
            f"variety weight {variety}",
            RatingConfig(),
            TeamGenConfig(w_variety=variety, sample_size=400),
        )


DEFAULT_TAU = 25 / 300

CHECKS = {
    "convergence": convergence_check,
    "team-size": team_size_check,
    "uneven": uneven_check,
    "calibration": calibration_check,
    "sweep": sweep_check,
}


def main() -> int:
    global REPEATS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", choices=sorted(CHECKS), action="append")
    parser.add_argument("--players", type=int, default=24)
    parser.add_argument("--sessions", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=REPEATS)
    args = parser.parse_args()

    REPEATS = args.repeats

    base = SimulationConfig(
        player_count=args.players,
        sessions=args.sessions,
        attendance=(4, min(14, args.players)),
    )
    print(
        f"{args.players} players, {args.sessions} sessions per run, "
        f"{args.repeats} runs per row."
    )

    for name in args.check or sorted(CHECKS):
        CHECKS[name](base)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
