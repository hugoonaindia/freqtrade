"""Bounded historical execution scenarios; quote sizes do not reveal queue priority."""

import math
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

from .guards import NY
from .history_validation import validate_daily, historical_bounds
from .strategies import STRATEGIES, decide, rotation


def stamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("Ejecución histórica: timestamp sin zona horaria.")
    return result.astimezone(UTC)


class ExecutionData:
    def __init__(self, broker, histories, config, progress=lambda message: None):
        self.broker, self.config, self.progress = broker, config, progress
        self.last_read = 0
        self.rows = 0
        self.archive = {
            "daily": {},
            "minutes": {},
            "quotes": {},
            "feed": config.feed,
            "adjustment": "raw",
        }
        calendar = self.read("/v2/calendar", start=config.start, end=config.end)
        self.calendar = {item["date"]: item for item in calendar}
        daily_bounds = historical_bounds(config.start, config.end)
        if config.feed == "sip":
            clock = self.read("/v2/clock")
            daily_bounds["end"] = min(
                stamp(daily_bounds["end"]), stamp(clock["timestamp"]) - timedelta(minutes=16)
            ).isoformat()
        for symbol, bars in histories.items():
            raw = self.pages(
                symbol,
                "bars",
                timeframe="1Day",
                adjustment="raw",
                **daily_bounds,
            )
            daily = validate_daily(symbol, raw, calendar=calendar, end=config.end)
            ratios = [
                float(bar["c"]) / daily[stamp(bar["t"]).astimezone(NY).date().isoformat()]["c"]
                for bar in bars
                if config.start <= stamp(bar["t"]).astimezone(NY).date().isoformat() <= config.end
            ]
            if not ratios or max(ratios) / min(ratios) - 1 > 0.0001:
                raise ValueError(
                    f"{symbol}: hay un split dentro del período. El modelo nominal intradía no reconstruye esta acción corporativa; elige un período sin splits."
                )
            self.archive["daily"][symbol] = daily

    def read(self, path, **params):
        delay = 0.35 - (monotonic() - self.last_read)
        if delay > 0:
            sleep(delay)
        self.last_read = monotonic()
        return self.broker.get(path, **params)

    def pages(self, symbol, kind, **params):
        result, seen, token = [], set(), None
        previous_time = previous_bucket = None
        sample_ms = self.config.quote_sample_ms
        if sample_ms <= 0:
            raise ValueError("quote_sample_ms debe ser positivo")
        bucket_factor = max(1, int(1000 / sample_ms))
        quality = {
            "received_quotes": 0,
            "non_executable_quotes": 0,
            "sampled_out_quotes": 0,
            "quote_sample_ms": sample_ms,
        }
        for _ in range(100):
            args = dict(data=True, feed=self.config.feed, sort="asc", limit=10000, **params)
            if token:
                args["page_token"] = token
            response = self.read(f"/v2/stocks/{symbol}/{kind}", **args)
            values = response.get(kind, [])
            if kind == "quotes":
                quality["received_quotes"] += len(values)
                for quote in values:
                    now = stamp(quote["t"])
                    if (previous_time is not None and now < previous_time) or not stamp(
                        params["start"]
                    ) <= now <= stamp(params["end"]):
                        raise ValueError("Cotizaciones fuera de orden o del período solicitado.")
                    previous_time = now
                    bid, ask, size = float(quote["bp"]), float(quote["ap"]), float(quote["as"])
                    if not all(math.isfinite(v) and v >= 0 for v in (bid, ask, size)):
                        raise ValueError("Cotización con precio o tamaño inválido.")
                    if not bid or not ask or not size or ask < bid:
                        quality["non_executable_quotes"] += 1
                        continue
                    bucket = int(now.timestamp() * bucket_factor)
                    if bucket == previous_bucket:
                        quality["sampled_out_quotes"] += 1
                        continue
                    previous_bucket = bucket
                    result.append(quote)
            else:
                result.extend(values)
            self.rows += len(values)
            if self.rows > 5000000:
                raise ValueError(
                    "Ejecución histórica: límite de cinco millones de registros leídos. Reduce símbolos o período."
                )
            token = response.get("next_page_token")
            if not token:
                if kind == "quotes":
                    key = symbol + ":" + stamp(params["start"]).astimezone(NY).date().isoformat()
                    self.archive.setdefault("quality", {})[key] = quality
                return result
            if token in seen:
                raise ValueError(
                    "Ejecución histórica: paginación repetida, no se usan datos incompletos."
                )
            seen.add(token)
        raise ValueError("Ejecución histórica: demasiadas páginas; reduce el período.")

    def bounds(self, day):
        session = self.calendar[day]
        return tuple(
            datetime.fromisoformat(day + "T" + session[key]).replace(tzinfo=NY).astimezone(UTC)
            for key in ("open", "close")
        )

    def minutes(self, symbol, day):
        key = symbol + ":" + day
        if key in self.archive["minutes"]:
            return self.archive["minutes"][key]
        opening, closing = self.bounds(day)
        self.progress(f"Ejecución: barras de un minuto de {symbol}, {day}…")
        bars = self.pages(
            symbol,
            "bars",
            timeframe="1Min",
            adjustment="raw",
            start=opening.isoformat(),
            end=(closing - timedelta(microseconds=1)).isoformat(),
        )
        expected = [
            opening + timedelta(minutes=i)
            for i in range(int((closing - opening).total_seconds() / 60))
        ]
        times = [stamp(bar["t"]) for bar in bars]
        if times != expected:
            raise ValueError(
                f"{symbol}, {day}: faltan minutos o hay duplicados. No se rellenan huecos del feed {self.config.feed.upper()}; no se puede completar esta simulación."
            )
        for bar in bars:
            values = [float(bar[k]) for k in ("o", "h", "l", "c", "v")]
            if (
                not all(math.isfinite(v) for v in values)
                or min(values[:4]) <= 0
                or values[4] < 0
                or not values[2]
                <= min(values[0], values[3])
                <= max(values[0], values[3])
                <= values[1]
            ):
                raise ValueError(f"{symbol}, {day}: minuto con precios o volumen inválidos.")
        self.archive["minutes"][key] = bars
        return bars

    def quotes(self, symbol, day):
        key = symbol + ":" + day
        if key in self.archive["quotes"]:
            return self.archive["quotes"][key]
        opening, _ = self.bounds(day)
        self.progress(f"Ejecución: cotizaciones de {symbol}, {day}…")
        quotes = self.pages(
            symbol,
            "quotes",
            start=opening.isoformat(),
            end=(opening + timedelta(minutes=4)).isoformat(),
        )
        previous = opening
        executable = []
        excluded = 0
        for quote in quotes:
            now = stamp(quote["t"])
            bid, ask = float(quote["bp"]), float(quote["ap"])
            size = float(quote["as"])
            if now < previous or not opening <= now <= opening + timedelta(minutes=4):
                raise ValueError(f"{symbol}, {day}: cotizaciones fuera de orden o período.")
            if (
                not all(math.isfinite(v) for v in (bid, ask, size))
                or bid < 0
                or ask < 0
                or size < 0
            ):
                raise ValueError(f"{symbol}, {day}: cotización o tamaño inválido.")
            previous = now
            if bid == 0 or ask == 0 or size == 0 or ask < bid:
                excluded += 1
                continue  # A withdrawn/crossed book is not an executable ask.
            executable.append(quote)
        previous_quality = self.archive.setdefault("quality", {}).get(key, {})
        self.archive["quality"][key] = {
            **previous_quality,
            "non_executable_quotes": previous_quality.get("non_executable_quotes", 0) + excluded,
            "received_quotes": previous_quality.get("received_quotes", len(quotes)),
        }
        # Fixed causal 100ms sampling bounds memory; keep the first valid update,
        # never choose the most favorable price after seeing the bucket.
        sampled, previous_bucket = [], None
        for quote in executable:
            bucket = int(stamp(quote["t"]).timestamp() * 10)
            if bucket != previous_bucket:
                sampled.append(quote)
                previous_bucket = bucket
        self.archive["quality"][key]["sampled_out_quotes"] = (
            previous_quality.get("sampled_out_quotes", 0) + len(executable) - len(sampled)
        )
        self.archive["quality"][key]["quote_sample_ms"] = 100
        quotes = sampled
        if not quotes or stamp(quotes[0]["t"]) - opening > timedelta(seconds=60):
            raise ValueError(
                f"{symbol}, {day}: no hay cotización inicial fresca. No se supone un spread ni una entrada ficticios."
            )
        self.archive["quotes"][key] = quotes
        return quotes


def simulate_execution(universe, days, config, strategy, data):
    from .backtest import Portfolio, past_bars, session_date
    from .swing import price_text

    portfolio = Portfolio(config)
    stops, partial_until = {}, {}
    tripped = None
    events = []
    ambiguous = 0
    peak = config.capital
    intraday_drawdown = 0
    factor = config.slippage_bps / 10000

    def sell(symbol, price, day, when, reason):
        # Fallback spread is explicit: minute trades do not provide a simultaneous bid.
        adjusted = price * (1 - config.spread_bps / 20000)
        portfolio.fill(symbol, "sell", portfolio.positions[symbol], {"o": adjusted}, day, None)
        portfolio.trades[-1].update(
            timestamp=when.isoformat(),
            reason=reason,
            spread_source="assumed_bps",
            spread_bps=config.spread_bps,
        )
        stops.pop(symbol, None)
        partial_until.pop(symbol, None)

    for day in days:
        past = {
            symbol: past_bars(
                bars, day, None if strategy == "macd" else STRATEGIES[strategy]["bars"]
            )
            for symbol, bars in universe.items()
        }
        decisions = (
            rotation(past, _validated=True)
            if strategy == "momentum"
            else {symbol: decide(strategy, bars, _validated=True) for symbol, bars in past.items()}
        )
        cash = portfolio.cash
        allocation = (portfolio.curve[-1]["equity"] if portfolio.curve else config.capital) / (
            1 if strategy == "momentum" else len(universe)
        )
        candidates = [
            s
            for s, d in decisions.items()
            if d["signal"] == "buy" and s not in portfolio.positions and not tripped
        ]
        active = sorted(set(portfolio.positions) | set(candidates))
        minutes = {s: data.minutes(s, day) for s in active} if not tripped else {}
        quotes = (
            {
                s: data.quotes(s, day)
                for s in active
                if s in candidates or decisions[s]["signal"] == "sell"
            }
            if not tripped
            else {}
        )
        pending = {}
        for symbol in candidates:
            if symbol not in quotes:
                continue
            q = quotes[symbol][0]
            ask, bid = float(q["ap"]), float(q["bp"])
            if (ask - bid) / ask > 0.01:
                events.append({"symbol": symbol, "date": day, "status": "spread_rejected"})
                continue
            sent = stamp(q["t"]) + timedelta(milliseconds=config.latency_ms)
            limit = float(price_text(ask))
            qty = math.floor(min(allocation, cash) / (limit * 1.01))
            if qty <= 0:
                continue
            cash -= qty * limit * 1.01
            pending[symbol] = {
                "sent": sent,
                "deadline": sent + timedelta(seconds=120),
                "limit": limit,
                "qty": qty,
                "quote": q,
            }
        opening, closing = data.bounds(day)
        if tripped:
            for symbol in list(portfolio.positions):
                sell(
                    symbol,
                    data.archive["daily"][symbol][day]["o"],
                    day,
                    opening,
                    "account_stop_next_open",
                )
        else:
            count = int((closing - opening).total_seconds() / 60)
            for index in range(count):
                now = opening + timedelta(minutes=index)
                end = now + timedelta(minutes=1)
                opening_equity = portfolio.cash + sum(
                    qty * float(minutes[s][index]["o"]) for s, qty in portfolio.positions.items()
                )
                if opening_equity <= config.capital * 0.98:
                    tripped = tripped or now.isoformat()
                for symbol in list(portfolio.positions):
                    bar = minutes[symbol][index]
                    if tripped or (
                        float(bar["o"]) <= stops[symbol] and now >= partial_until[symbol]
                    ):
                        sell(
                            symbol,
                            float(bar["o"]),
                            day,
                            now,
                            "account_stop" if tripped else "gap_stop",
                        )
                    elif decisions[symbol]["signal"] == "sell":
                        q = next(
                            (
                                q
                                for q in quotes[symbol]
                                if now <= stamp(q["t"]) < end
                                and stamp(q["t"])
                                >= opening + timedelta(milliseconds=config.latency_ms)
                            ),
                            None,
                        )
                        if q is None:
                            if index >= 4:
                                raise ValueError(
                                    f"{symbol}, {day}: falta cotización para salida de estrategia."
                                )
                            continue
                        if float(bar["l"]) <= stops[symbol]:
                            ambiguous += 1
                        # Bid already includes spread, so do not charge the fallback twice.
                        portfolio.fill(
                            symbol,
                            "sell",
                            portfolio.positions[symbol],
                            {"o": float(q["bp"])},
                            day,
                            session_date(past[symbol][-1]),
                        )
                        portfolio.trades[-1].update(
                            timestamp=q["t"], reason="strategy", spread_source="historical_bid"
                        )
                        stops.pop(symbol, None)
                if tripped:
                    pending.clear()
                for symbol, order in list(pending.items()):
                    if now >= order["deadline"]:
                        events.append(
                            {
                                "symbol": symbol,
                                "date": day,
                                "status": "expired",
                                "sent_at": order["sent"].isoformat(),
                                "deadline_at": order["deadline"].isoformat(),
                            }
                        )
                        del pending[symbol]
                        continue
                    q = next(
                        (
                            q
                            for q in quotes[symbol]
                            if now <= stamp(q["t"]) < end
                            and order["sent"] <= stamp(q["t"]) < order["deadline"]
                            and float(q["ap"]) * (1 + factor) <= order["limit"]
                        ),
                        None,
                    )
                    if q is None:
                        continue
                    bar = minutes[symbol][index]
                    capacity = min(
                        math.floor(float(q["as"]) * 100 * config.participation_pct / 100),
                        math.floor(float(bar["v"]) * config.participation_pct / 100),
                    )
                    qty = min(order["qty"], capacity)
                    if qty <= 0:
                        continue
                    price = float(q["ap"]) * (1 + factor)
                    portfolio.fill(
                        symbol,
                        "buy",
                        qty,
                        {"o": float(q["ap"])},
                        day,
                        session_date(past[symbol][-1]),
                    )
                    stops[symbol] = float(price_text(order["limit"] * 0.98))
                    partial = qty < order["qty"]
                    partial_until[symbol] = (
                        stamp(q["t"]) + timedelta(seconds=30) if partial else stamp(q["t"])
                    )
                    portfolio.trades[-1].update(
                        timestamp=q["t"],
                        sent_at=order["sent"].isoformat(),
                        deadline_at=order["deadline"].isoformat(),
                        quote_at=order["quote"]["t"],
                        limit_price=order["limit"],
                        stop_price=stops[symbol],
                        partial=partial,
                        requested_qty=order["qty"],
                        spread_bps=(float(q["ap"]) - float(q["bp"]))
                        / ((float(q["ap"]) + float(q["bp"])) / 2)
                        * 10000,
                        slippage_bps=(price / float(order["quote"]["ap"]) - 1) * 10000,
                    )
                    events.append(
                        {
                            "symbol": symbol,
                            "date": day,
                            "status": "partial_then_cancel_assumed"
                            if partial
                            else "filled_estimate",
                            "remaining_qty": order["qty"] - qty,
                        }
                    )
                    del pending[symbol]
                    if float(bar["l"]) <= stops[symbol]:
                        ambiguous += 1
                for symbol in list(portfolio.positions):
                    bar = minutes[symbol][index]
                    if float(bar["l"]) <= stops[symbol] and end >= partial_until[symbol]:
                        sell(
                            symbol,
                            min(float(bar["o"]), stops[symbol]),
                            day,
                            end,
                            "minute_stop_estimate",
                        )
                equity = portfolio.cash + sum(
                    qty * float(minutes[s][index]["c"]) for s, qty in portfolio.positions.items()
                )
                peak = max(peak, equity)
                intraday_drawdown = max(intraday_drawdown, (1 - equity / peak) * 100)
                if equity <= config.capital * 0.98:
                    tripped = tripped or end.isoformat()
            for symbol, order in pending.items():
                events.append(
                    {
                        "symbol": symbol,
                        "date": day,
                        "status": "expired",
                        "deadline_at": order["deadline"].isoformat(),
                    }
                )
        portfolio.mark(day, {s: bars[day] for s, bars in data.archive["daily"].items()})
    report = portfolio.report()
    report["risk"] = {
        "profile": "swing",
        "account_stop_date": tripped,
        "liquidation_pending": bool(tripped and portfolio.positions),
    }
    report["execution"] = {
        "quality": "intraday_scenario",
        "resolution": "1Min+historical_quotes",
        "entry_timeout_modeled": True,
        "entry_timeout_seconds": 120,
        "latency_ms": config.latency_ms,
        "participation_pct": config.participation_pct,
        "ambiguous_events": ambiguous,
        "intraday_sampled_drawdown_pct": intraday_drawdown,
        "orders": events,
        "partial_fill_model": "single_fill_then_cancel_remainder_after_30s_assumed",
        "exit_spread_fallback_bps": config.spread_bps,
        "queue_priority_known": False,
        "quote_sample_ms": 100,
        "feed": config.feed,
    }
    return report
