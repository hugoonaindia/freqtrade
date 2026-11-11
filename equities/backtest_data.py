"""Read-only Alpaca history loader with complete-session and pagination checks."""

from datetime import date, datetime, timedelta

from .backtest import prepare, session_date
from .guards import NY, completed_session
from .history_validation import validate_daily, historical_bounds


def load_histories(broker, symbols, config, progress=lambda message: None):
    clock = broker.get("/v2/clock")
    today = datetime.fromisoformat(clock["timestamp"].replace("Z", "+00:00")).astimezone(NY).date()
    if date.fromisoformat(config.end) > today:
        raise ValueError("Fecha final futura; elige período terminado.")
    last_complete = completed_session(broker, clock).isoformat()
    earliest = (date.fromisoformat(config.start) - timedelta(days=400)).isoformat()
    calendar = broker.get("/v2/calendar", start=earliest, end=config.end)
    sessions = sorted(
        item["date"] for item in calendar if config.start <= item["date"] <= config.end
    )
    if len(sessions) < 2 or sessions[-1] > last_complete:
        raise ValueError("Período sin suficientes sesiones terminadas; ajusta fechas.")
    if config.feed == "sip" and sessions[-1] == today.isoformat():
        closing = next(item["close"] for item in calendar if item["date"] == sessions[-1])
        close_time = datetime.fromisoformat(sessions[-1] + "T" + closing).replace(tzinfo=NY)
        if datetime.fromisoformat(clock["timestamp"]) < close_time + timedelta(minutes=16):
            raise ValueError(
                "SIP histórico: espera al menos 16 minutos después del cierre para usar la sesión completa, o termina el período en la sesión anterior."
            )
    query_bounds = historical_bounds(earliest, min(config.end, last_complete))
    if config.feed == "sip":
        query_bounds["end"] = min(
            datetime.fromisoformat(query_bounds["end"]),
            datetime.fromisoformat(clock["timestamp"]) - timedelta(minutes=16),
        ).isoformat()
    output = {}
    for index, symbol in enumerate(symbols):
        progress(f"Descargando {symbol} ({index + 1}/{len(symbols)})")
        if broker.get("/v2/assets/" + symbol).get("class") != "us_equity":
            raise ValueError(f"{symbol}: solo acciones/ETF estadounidenses.")
        token = None
        seen = set()
        bars = []
        while True:
            params = {
                "data": True,
                "timeframe": "1Day",
                **query_bounds,
                "adjustment": "split",
                "feed": config.feed,
                "sort": "asc",
                "limit": 10000,
            }
            if token:
                params["page_token"] = token
            page = broker.get("/v2/stocks/" + symbol + "/bars", **params)
            bars.extend(page.get("bars", []))
            token = page.get("next_page_token")
            if len(bars) > 10000:
                raise ValueError("Demasiadas barras históricas.")
            if not token:
                break
            if token in seen or len(seen) >= 20:
                raise ValueError("Paginación histórica repetida/incompleta.")
            seen.add(token)
        days = sorted(
            session_date(bar) for bar in bars if config.start <= session_date(bar) <= config.end
        )
        if days != sessions:
            missing = sorted(set(sessions) - set(days))
            raise ValueError(
                f"{symbol}: historial incompleto; faltan {len(missing)} sesiones. "
                f"Primera: {missing[0] if missing else 'duplicada o fuera de calendario'}."
            )
        validate_daily(symbol, bars, calendar=calendar, end=config.end)
        output[symbol] = bars
    prepare(output, config, 64)
    return output
