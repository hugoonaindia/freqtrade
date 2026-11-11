"""Pure historical portfolio simulation. This module cannot place broker orders."""

import math
import re
from bisect import bisect_left
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from .backtest_metrics import capture, grouped_returns, metrics, protection
from .history_validation import session_date, validate_daily
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
    profile: str = "budget"
    execution: str = "daily"
    execution_strategy: str = "sma"
    feed: str = "iex"
    latency_ms: float = 1000
    participation_pct: float = 1
    spread_bps: float = 10
    quote_sample_ms: float = 100
    max_position_pct: float = 100

    def __post_init__(self):
        try:
            if self.profile not in ("budget", "swing"):
                raise ValueError
            if self.feed not in ("iex", "sip"):
                raise ValueError
            if self.execution_strategy not in STRATEGIES:
                raise ValueError
            if self.execution not in ("daily", "intraday") or (
                self.execution == "intraday" and self.profile != "swing"
            ):
                raise ValueError
            for key, maximum in (
                ("latency_ms", 60000),
                ("participation_pct", 100),
                ("spread_bps", 100),
                ("quote_sample_ms", 1000),
                ("max_position_pct", 100),
            ):
                value = getattr(self, key)
                if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= maximum:
                    raise ValueError
            if self.participation_pct <= 0:
                raise ValueError
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
                "Parámetros inválidos: máximo 10 años y 100 pb por coste; intradía requiere perfil swing, latencia 0–60000 ms y participación >0 hasta 100%."
            ) from None


class SessionHistory(Mapping):
    """Validated history with an immutable date index for repeated window lookups."""

    def __init__(self, values):
        self._values = dict(values)
        self.dates = tuple(sorted(values))
        self.bars = tuple(values[stamp] for stamp in self.dates)

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)


def prepare(histories, config, minimum):
    if not 1 <= len(histories) <= 8:
        raise ValueError("Elige entre 1 y 8 símbolos.")
    prepared = {}
    sessions = None
    for symbol, bars in sorted(histories.items()):
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
            raise ValueError("Símbolo no válido.")
        by_date = validate_daily(symbol, bars)
        days = sorted(day for day in by_date if config.start <= day <= config.end)
        if not days or (sessions is not None and days != sessions):
            raise ValueError(
                "Faltan sesiones comunes del período; no se descartan fechas silenciosamente."
            )
        if sum(day < days[0] for day in by_date) < minimum:
            raise ValueError(f"{symbol}: faltan {minimum} sesiones previas de calentamiento.")
        prepared[symbol] = SessionHistory(by_date)
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


def past_bars(by_date, day, limit=None):
    start = (date.fromisoformat(day) - timedelta(days=400)).isoformat()
    if isinstance(by_date, SessionHistory):
        left = bisect_left(by_date.dates, start)
        right = bisect_left(by_date.dates, day)
        if limit is not None:
            left = max(left, right - limit)
        return list(by_date.bars[left:right])
    values = [by_date[stamp] for stamp in sorted(by_date) if start <= stamp < day]
    return values[-limit:] if limit is not None else values


def simulate(histories, config, strategy, *, _prepared=None):
    if strategy not in STRATEGIES:
        raise ValueError("Estrategia desconocida.")
    if config.profile == "swing":
        from .swing_backtest import simulate_swing

        return simulate_swing(histories, config, strategy, _prepared=_prepared)
    universe, days = _prepared or prepare(histories, config, STRATEGIES[strategy]["bars"])
    portfolio = Portfolio(config)
    for day in days:
        limit = None if strategy == "macd" else STRATEGIES[strategy]["bars"]
        past = {symbol: past_bars(data, day, limit) for symbol, data in universe.items()}
        decisions = (
            rotation(past, _validated=True)
            if strategy == "momentum"
            else {symbol: decide(strategy, data, _validated=True) for symbol, data in past.items()}
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
        past = [past_bars(data, day, 21) for data in universe.values()]
        if any(len(values) < 21 for values in past):
            labels.append("unclassified")
            continue
        trend = sum(values[-1]["c"] / values[-21]["c"] - 1 for values in past) / len(past)
        labels.append("bull" if trend > 0.02 else "bear" if trend < -0.02 else "sideways")
    return labels


def compare(histories, config, execution_data=None, *, strategy=None):
    universe, days = prepare(histories, config, max(s["bars"] for s in STRATEGIES.values()))
    if config.execution == "intraday" and execution_data is None:
        raise ValueError("Faltan datos intradía: no se sustituye esta prueba por velas diarias.")
    benchmark_universe = (
        execution_data.archive["daily"] if config.execution == "intraday" else universe
    )
    benchmarks = {
        key: buy_hold(benchmark_universe, days, config, full)
        for key, full in [("allocated", False), ("fully_invested", True)]
    }
    labels = regimes(universe, days)
    if strategy is not None and strategy not in STRATEGIES:
        raise ValueError("Estrategia de ejecución desconocida.")
    selected = (
        [strategy]
        if strategy
        else ([config.execution_strategy] if config.execution == "intraday" else STRATEGIES)
    )
    if config.execution == "intraday":
        from .execution_replay import simulate_execution

        strategies = {
            key: simulate_execution(universe, days, config, key, execution_data) for key in selected
        }
    else:
        strategies = {
            key: simulate(histories, config, key, _prepared=(universe, days)) for key in STRATEGIES
        }
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
        "validation": {
            "quality": "intraday_scenario" if config.execution == "intraday" else "exploratory",
            "out_of_sample": False,
            "live_execution_equivalent": False,
        },
        "assumptions": (
            [
                "ESCENARIO INTRADÍA: señales diarias ajustadas por splits; ejecución y cartera en precios nominales raw.",
                "Se rechazan splits dentro del período; no se inventan ajustes de cantidades.",
                "Entradas: cotizaciones históricas muestreadas causalmente cada 100 ms, latencia configurada, límite que nunca se supera y caducidad de 120 segundos. El muestreo puede omitir oportunidades breves.",
                "Liquidez estimada: fracción configurada del tamaño ask y volumen del minuto; no representa cola, profundidad ni fills garantizados.",
                "Parcial: un fill estimado y cancelación del resto supuesta a los 30 segundos; no se conoce la carrera real entre fill y cancelación.",
                "Stops con mínimos de un minuto: orden intraminuto desconocido y contabilizado como ambiguo. Gaps salen al precio de apertura con costes.",
                "Salidas de estrategia usan bid histórico; stops y cierres globales usan spread configurado cuando no hay bid simultáneo.",
                "La parada de cuenta se muestrea en apertura/cierre de cada minuto; no replica el supervisor de 30 segundos.",
                "Las métricas principales siguen siendo de cierre diario; se añade caída intradía muestreada en execution.",
                "Precios raw, dividendos excluidos; referencias usan las mismas sesiones y precios nominales.",
            ]
            if config.execution == "intraday"
            else (
                [
                    "Perfil swing: capital disponible, stop individual 2% y límite global 2% inicial.",
                    "Entrada limitada aproximada con cierre anterior; sin quotes históricos.",
                    "No reproduce cancelación tras 120 segundos ni ejecución intradía de Alpaca.",
                    "Stop individual usa mínimo diario; con gap se ejecuta al peor precio de apertura.",
                    "Límite global se comprueba en apertura y cierre; salida en próxima apertura.",
                    "Las salidas de apertura preceden a los mínimos posteriores de esa sesión.",
                    "Orden intradía desconocido: las entradas y stops diarios son aproximaciones.",
                    "Stop simulado sobre el fill estimado; OTO Paper inicial sobre el límite de compra.",
                    "Las referencias mantienen sus asignaciones originales y no usan stops.",
                ]
                if config.profile == "swing"
                else []
            )
            + [
                "Precios ajustados por splits; dividendos, impuestos e interés de efectivo excluidos.",
                (
                    "Cantidades enteras en unidades ajustadas; "
                    "no reconstruye cantidades nominales de splits."
                ),
                "Señal de sesión anterior; simulación de ejecución con costes.",
                "Perfil presupuesto: entrada estimada; los gaps pueden superar el importe.",
                "Caídas calculadas con patrimonio al cierre; no representan mínimo intradía.",
                "Universo elegido hoy: posible sesgo de supervivencia.",
                "Dos mitades son diagnóstico temporal, no validación fuera de muestra ni optimización.",
                "Posiciones finales valoradas a cierre; no se fuerza venta ni coste de liquidación.",
                "Regímenes: retorno previo de 20 sesiones de cesta equiponderada; umbrales +/-2%.",
                (
                    "Captura diaria: cociente de rentabilidades medias "
                    "en días de referencia positivos/negativos."
                ),
            ]
        ),
    }
