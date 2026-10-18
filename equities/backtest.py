"""Pure historical portfolio simulation. This module cannot place broker orders."""

import math
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .backtest_metrics import capture, grouped_returns, metrics, protection
from .strategies import STRATEGIES, decide, rotation


NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Config:
    start: str = "2021-01-01"
    end: str = "2025-12-31"
    capital: float = 10000
    budget: float = 500
    fee_bps: float = 5
    slippage_bps: float = 5

    def __post_init__(self):
        try:
            first, last = date.fromisoformat(self.start), date.fromisoformat(self.end)
            if first > last or (last - first).days > 3660:
                raise ValueError
            for key in ("capital", "budget", "fee_bps", "slippage_bps"):
                value = getattr(self, key)
                if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                    raise ValueError
            if min(self.capital, self.budget) <= 0 or max(self.fee_bps, self.slippage_bps) > 100:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(
                "Fechas/capital/costes inválidos: máximo 10 años y 100 pb por coste."
            ) from None


def session_date(bar):
    stamp = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Barras requieren zona horaria.")
    return stamp.astimezone(NY).date().isoformat()


def prepare(histories, config, minimum):
    if not 1 <= len(histories) <= 8:
        raise ValueError("Elige entre 1 y 8 símbolos.")
    prepared = {}
    sessions = None
    for symbol, bars in sorted(histories.items()):
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError("Símbolo no válido.")
        by_date = {}
        for bar in bars:
            day = session_date(bar)
            if day in by_date:
                raise ValueError(f"{symbol}: sesión duplicada {day}.")
            values = {key: float(bar[key]) for key in ("o", "h", "l", "c")}
            if (
                any(not math.isfinite(value) or value <= 0 for value in values.values())
                or not values["l"]
                <= min(values["o"], values["c"])
                <= max(values["o"], values["c"])
                <= values["h"]
            ):
                raise ValueError(f"{symbol}: OHLC inválido en {day}.")
            by_date[day] = {**bar, **values}
        days = sorted(day for day in by_date if config.start <= day <= config.end)
        if not days or (sessions is not None and days != sessions):
            raise ValueError(
                "Faltan sesiones comunes del período; no se descartan fechas silenciosamente."
            )
        if sum(day < days[0] for day in by_date) < minimum:
            raise ValueError(f"{symbol}: faltan {minimum} sesiones previas de calentamiento.")
        prepared[symbol] = by_date
        sessions = days
    if len(sessions) < 2:
        raise ValueError("Se necesitan al menos dos sesiones de evaluación.")
    return prepared, sessions


class Portfolio:
    def __init__(self, config):
        self.config = config
        self.cash = config.capital
        self.positions = {}
        self.entry_cost = {}
        self.trades = []
        self.curve = []

    def fill(self, symbol, side, qty, bar, day, signal_day):
        sign = 1 if side == "buy" else -1
        price = bar["o"] * (1 + sign * self.config.slippage_bps / 10000)
        rate = self.config.fee_bps / 10000
        if side == "buy":
            qty = min(qty, math.floor(self.cash / (price * (1 + rate))))
        else:
            qty = min(qty, self.positions.get(symbol, 0))
        if qty <= 0:
            return
        value, fee = qty * price, qty * price * rate
        pnl = None
        if side == "buy":
            self.cash -= value + fee
            self.positions[symbol] = qty
            self.entry_cost[symbol] = value + fee
        else:
            self.cash += value - fee
            pnl = value - fee - self.entry_cost.pop(symbol)
            self.positions.pop(symbol)
        self.trades.append(
            {
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "price": price,
                "fee": fee,
                "date": day,
                "signal_date": signal_day,
                "realized_pnl": pnl,
            }
        )

    def mark(self, day, prices):
        invested = sum(qty * prices[symbol]["c"] for symbol, qty in self.positions.items())
        equity = self.cash + invested
        self.curve.append(
            {
                "date": day,
                "equity": equity,
                "cash": self.cash,
                "exposure": invested / equity if equity else 0,
            }
        )

    def report(self):
        return {
            "metrics": metrics(self.curve, self.config.capital, self.trades),
            "curve": self.curve,
            "trades": self.trades,
            "open_positions": dict(self.positions),
        }


def past_bars(by_date, day):
    start = (date.fromisoformat(day) - timedelta(days=400)).isoformat()
    return [by_date[stamp] for stamp in sorted(by_date) if start <= stamp < day]


def simulate(histories, config, strategy):
    if strategy not in STRATEGIES:
        raise ValueError("Estrategia desconocida.")
    universe, days = prepare(histories, config, STRATEGIES[strategy]["bars"])
    portfolio = Portfolio(config)
    for day in days:
        past = {symbol: past_bars(data, day) for symbol, data in universe.items()}
        decisions = (
            rotation(past)
            if strategy == "momentum"
            else {symbol: decide(strategy, data) for symbol, data in past.items()}
        )
        # Snapshot cash and positions: same cycle cannot reinvest unconfirmed sale proceeds.
        spendable = portfolio.cash
        held = dict(portfolio.positions)
        for symbol, data in past.items():
            action = decisions[symbol]["signal"]
            if action == "buy" and symbol not in held:
                estimate = data[-1]["c"] * 1.05
                qty = math.floor(min(config.budget, spendable) / estimate)
                before = portfolio.cash
                # Limit spend to the original cycle cash despite earlier simulated sales.
                rate = config.fee_bps / 10000
                fill_price = universe[symbol][day]["o"] * (1 + config.slippage_bps / 10000)
                qty = min(qty, math.floor(spendable / (fill_price * (1 + rate))))
                portfolio.fill(
                    symbol, "buy", qty, universe[symbol][day], day, session_date(data[-1])
                )
                spendable -= before - portfolio.cash
            elif action == "sell" and symbol in held:
                portfolio.fill(
                    symbol, "sell", held[symbol], universe[symbol][day], day, session_date(data[-1])
                )
        portfolio.mark(day, {symbol: data[day] for symbol, data in universe.items()})
    return portfolio.report()


def buy_hold(universe, days, config, fully_invested):
    portfolio = Portfolio(config)
    allocation = (
        config.capital / len(universe)
        if fully_invested
        else min(config.budget, config.capital / len(universe))
    )
    for symbol, data in universe.items():
        price = data[days[0]]["o"] * (1 + config.slippage_bps / 10000)
        qty = math.floor(allocation / (price * (1 + config.fee_bps / 10000)))
        portfolio.fill(symbol, "buy", qty, data[days[0]], days[0], None)
    for day in days:
        portfolio.mark(day, {symbol: data[day] for symbol, data in universe.items()})
    return portfolio.report()


def regimes(universe, days):
    labels = []
    for day in days:
        past = [past_bars(data, day) for data in universe.values()]
        if any(len(values) < 21 for values in past):
            labels.append("unclassified")
            continue
        trend = sum(values[-1]["c"] / values[-21]["c"] - 1 for values in past) / len(past)
        labels.append("bull" if trend > 0.02 else "bear" if trend < -0.02 else "sideways")
    return labels


def compare(histories, config):
    universe, days = prepare(histories, config, max(s["bars"] for s in STRATEGIES.values()))
    benchmarks = {
        key: buy_hold(universe, days, config, full)
        for key, full in [("allocated", False), ("fully_invested", True)]
    }
    labels = regimes(universe, days)
    strategies = {key: simulate(histories, config, key) for key in STRATEGIES}
    for report in [*benchmarks.values(), *strategies.values()]:
        report["regimes"] = grouped_returns(report["curve"], config.capital, labels)
        report["years"] = grouped_returns(report["curve"], config.capital, [d[:4] for d in days])
        midpoint = len(days) // 2
        report["halves"] = {
            "first": metrics(report["curve"][:midpoint], config.capital, []),
            "second": metrics(
                report["curve"][midpoint:], report["curve"][midpoint - 1]["equity"], []
            ),
        }
    for report in strategies.values():
        report["protection"] = protection(report["metrics"], benchmarks["allocated"]["metrics"])
        report["protection_full"] = protection(
            report["metrics"], benchmarks["fully_invested"]["metrics"]
        )
        report["capture"] = capture(
            report["curve"], benchmarks["fully_invested"]["curve"], config.capital
        )
    return {
        "config": asdict(config),
        "symbols": list(universe),
        "sessions": len(days),
        "actual_start": days[0],
        "actual_end": days[-1],
        "strategies": strategies,
        "benchmarks": benchmarks,
        "assumptions": [
            "Precios ajustados por splits; dividendos, impuestos e interés de efectivo excluidos.",
            "Cantidades enteras en unidades ajustadas; "
            "no reconstruye cantidades nominales de splits.",
            "Señal de sesión anterior; ejecución teórica en apertura siguiente con costes.",
            "Presupuesto de entrada estimado con cierre previo; gaps pueden superar ese importe.",
            "Caídas calculadas con patrimonio al cierre; no representan mínimo intradía.",
            "Universo elegido hoy: posible sesgo de supervivencia.",
            "Dos mitades son diagnóstico temporal, no validación fuera de muestra ni optimización.",
            "Posiciones finales valoradas a cierre; no se fuerza venta ni coste de liquidación.",
            "Regímenes: retorno previo de 20 sesiones de cesta equiponderada; umbrales +/-2%.",
            "Captura diaria: cociente de rentabilidades medias "
            "en días de referencia positivos/negativos.",
        ],
    }
