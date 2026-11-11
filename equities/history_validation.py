"""Shared daily OHLC validation for research and trading inputs."""

import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")


def historical_bounds(first, last):
    """Explicit UTC bounds: Alpaca date-only inclusive endpoints can include the next day."""
    start = datetime.fromisoformat(first).replace(tzinfo=NY)
    end = datetime.fromisoformat(last).replace(tzinfo=NY) + timedelta(days=1, microseconds=-1)
    return {"start": start.astimezone(UTC).isoformat(), "end": end.astimezone(UTC).isoformat()}


class HistoryError(ValueError):
    """Safe, actionable explanation for invalid market data."""


def session_date(bar):
    try:
        stamp = datetime.fromisoformat(bar["t"])
        if stamp.tzinfo is None:
            raise ValueError
        return stamp.astimezone(NY).date().isoformat()
    except (KeyError, TypeError, ValueError):
        raise HistoryError(
            "La barra diaria no tiene una fecha válida con zona horaria. Vuelve a descargar el histórico."
        ) from None


def validate_daily(symbol, bars, *, calendar=None, end=None):
    by_date = {}
    for bar in bars:
        try:
            day = session_date(bar)
        except HistoryError as exc:
            raise HistoryError(f"{symbol}: {exc}") from None
        if day in by_date:
            raise HistoryError(
                f"{symbol}: hay dos barras para la sesión {day}. No se puede elegir una sin verificar los datos; vuelve a descargar el histórico."
            )
        labels = {"o": "apertura", "h": "máximo", "l": "mínimo", "c": "cierre"}
        values = {}
        for key, label in labels.items():
            try:
                raw = bar[key]
                if isinstance(raw, bool):
                    raise ValueError
                value = float(raw)
                if not math.isfinite(value) or value <= 0:
                    raise ValueError
            except (KeyError, TypeError, ValueError, OverflowError):
                raise HistoryError(
                    f"{symbol}, {day}: el precio de {label} falta o no es un número positivo finito. Vuelve a descargar esa sesión antes de calcular señales."
                ) from None
            values[key] = value
        if (
            not values["l"]
            <= min(values["o"], values["c"])
            <= max(values["o"], values["c"])
            <= values["h"]
        ):
            raise HistoryError(
                f"{symbol}, {day}: apertura o cierre fuera del rango mínimo–máximo, o mínimo superior al máximo. Revisa los precios de esa sesión; el histórico se ha rechazado."
            )
        if end and day > end:
            raise HistoryError(
                f"{symbol}: se recibió la sesión {day}, posterior al límite {end}. Comprueba las fechas de descarga; no se utilizarán datos posteriores."
            )
        by_date[day] = {**bar, **values}
    if calendar is not None:
        if not by_date:
            raise HistoryError(
                f"{symbol}: Alpaca no devolvió barras históricas. Revisa el símbolo, el período y los permisos de datos."
            )
        dates = sorted(by_date)
        expected = sorted(item["date"] for item in calendar if dates[0] <= item["date"] <= end)
        if not expected:
            raise HistoryError(
                f"{symbol}: el calendario no contiene sesiones para el histórico recibido. Comprueba el período y la respuesta del calendario de Alpaca."
            )
        if len(expected) != len(set(expected)):
            raise HistoryError(
                f"{symbol}: el calendario de Alpaca contiene sesiones duplicadas. Vuelve a consultar el calendario antes de operar."
            )
        missing = sorted(set(expected) - set(dates))
        unexpected = sorted(set(dates) - set(expected))
        if missing or unexpected:
            details = []
            if missing:
                details.append(f"faltan {len(missing)} sesiones bursátiles; primera: {missing[0]}")
            if unexpected:
                details.append(
                    f"hay {len(unexpected)} fechas fuera del calendario; primera: {unexpected[0]}"
                )
            raise HistoryError(
                f"{symbol}: {'; '.join(details)}. No se calculan señales con este histórico. Vuelve a descargar los datos y comprueba su disponibilidad en Alpaca."
            )
    return by_date
