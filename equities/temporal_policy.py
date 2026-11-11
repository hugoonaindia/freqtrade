"""Keep the most recent calendar year reserved for evaluation."""

from datetime import datetime, timedelta
from .guards import NY


def year_before(day):
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def temporal_policy(today=None):
    today = today or datetime.now(NY).date()
    reserve = year_before(today) - timedelta(days=7)
    return {
        "as_of": today.isoformat(),
        "minimum_reserve_years": 1,
        "session_boundary_buffer_days": 7,
        "development_end_max": (reserve - timedelta(days=1)).isoformat(),
        "reserve_start": reserve.isoformat(),
        "evaluation_end_max": today.isoformat(),
    }


def enforce_development(config, policy=None):
    policy = policy or temporal_policy()
    if config.end > policy["development_end_max"]:
        raise ValueError(
            f"Desarrollo: el último año queda reservado fuera de muestra. La fecha final máxima es {policy['development_end_max']}; elige una fecha anterior o activa Fuera de muestra."
        )
