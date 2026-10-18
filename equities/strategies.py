"""Deterministic daily signals. Indicators express rules, not expected profitability."""

import math
from datetime import datetime
from itertools import pairwise
from statistics import fmean, pstdev
from zoneinfo import ZoneInfo


STRATEGIES = {
    "sma": {
        "name": "Cruce SMA 5/20",
        "bars": 21,
        "description": "Compra al cruce alcista 5/20; vende al cruce bajista.",
    },
    "mean_reversion": {
        "name": "Reversión a media",
        "bars": 20,
        "description": "Compra con z-score de 20 cierres ≤ -2; vende al volver a media.",
    },
    "bollinger": {
        "name": "Bollinger 20/2",
        "bars": 21,
        "description": "Compra al recuperar banda inferior tras cierre fuera; vende en media.",
    },
    "breakout": {
        "name": "Ruptura de máximos",
        "bars": 21,
        "description": "Compra sobre máximos de 20 sesiones previas; vende bajo mínimos de 10.",
    },
    "momentum": {
        "name": "Momentum y rotación",
        "bars": 64,
        "description": "Mantiene ganador positivo de 63 sesiones entre símbolos configurados.",
    },
    "macd": {
        "name": "MACD 12/26/9",
        "bars": 35,
        "description": "Compra al cruce alcista de MACD con señal; vende al cruce bajista.",
    },
}


def valid(data, minimum, ohlc=False):
    if len(data) < minimum:
        return False
    try:
        for bar in data:
            close = float(bar["c"])
            if not math.isfinite(close) or close <= 0:
                return False
            if ohlc:
                low, high = float(bar["l"]), float(bar["h"])
                if not all(math.isfinite(v) and v > 0 for v in (low, high)):
                    return False
                if not low <= close <= high:
                    return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


def result(action, reason):
    return {"signal": action, "reason": reason}


def cross(previous, current, label):
    if previous <= 0 < current:
        return result("buy", f"{label}: cruce alcista")
    if previous >= 0 > current:
        return result("sell", f"{label}: cruce bajista")
    return result("hold", f"{label}: sin cruce nuevo")


def ema(values, period):
    """SMA-seeded EMA, aligned by returning only defined values."""
    if len(values) < period:
        return []
    out = [fmean(values[:period])]
    alpha = 2 / (period + 1)
    for value in values[period:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def decide(strategy, data):
    if strategy not in STRATEGIES:
        raise ValueError("Estrategia desconocida.")
    minimum = STRATEGIES[strategy]["bars"]
    if not valid(data, minimum, ohlc=strategy == "breakout"):
        return result("hold", f"Faltan {minimum} sesiones válidas o hay datos inválidos")
    closes = [float(bar["c"]) for bar in data]
    if strategy == "sma":
        previous = fmean(closes[-6:-1]) - fmean(closes[-21:-1])
        current = fmean(closes[-5:]) - fmean(closes[-20:])
        return cross(previous, current, "SMA 5/20")
    if strategy == "mean_reversion":
        mean, std = fmean(closes[-20:]), pstdev(closes[-20:])
        if std == 0:
            return result("hold", "Sin dispersión en últimos 20 cierres")
        z = (closes[-1] - mean) / std
        return result(
            "buy" if z <= -2 else "sell" if closes[-1] >= mean else "hold",
            f"z-score {z:.2f}; media {mean:.2f}",
        )
    if strategy == "bollinger":
        previous = closes[-21:-1]
        current = closes[-20:]
        lower_before = fmean(previous) - 2 * pstdev(previous)
        lower_now = fmean(current) - 2 * pstdev(current)
        # Exit at the middle band takes precedence over an overshooting recovery.
        if closes[-1] >= fmean(current) and pstdev(current) > 0:
            return result("sell", "Cierre alcanza media de Bollinger")
        if closes[-2] < lower_before and closes[-1] >= lower_now:
            return result("buy", "Cierre recupera banda inferior 20/2")
        return result("hold", "Sin recuperación de banda ni salida a media")
    if strategy == "breakout":
        high = max(float(v["h"]) for v in data[-21:-1])
        low = min(float(v["l"]) for v in data[-11:-1])
        return result(
            "buy" if closes[-1] > high else "sell" if closes[-1] < low else "hold",
            f"Máximo previo 20: {high:.2f}; mínimo previo 10: {low:.2f}",
        )
    if strategy == "macd":
        fast, slow = ema(closes, 12), ema(closes, 26)
        line = [f - s for f, s in zip(fast[14:], slow, strict=True)]
        reference = ema(line, 9)
        return cross(line[-2] - reference[-2], line[-1] - reference[-1], "MACD 12/26/9")
    return result("hold", "Momentum requiere comparar universo completo")


def rotation(universe):
    if not universe or any(not valid(data, 64) for data in universe.values()):
        return {symbol: result("hold", "Universo incompleto; sin rotación") for symbol in universe}
    try:
        windows = [
            tuple(
                datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
                .astimezone(ZoneInfo("America/New_York"))
                .date()
                for bar in data[-64:]
            )
            for data in universe.values()
        ]
        aligned = len(set(windows)) == 1 and all(
            all(first < second for first, second in pairwise(window)) for window in windows
        )
    except (KeyError, TypeError, ValueError):
        aligned = False
    if not aligned:
        return {
            symbol: result("hold", "Sesiones no alineadas; sin rotación") for symbol in universe
        }
    scores = {
        symbol: float(data[-1]["c"]) / float(data[-64]["c"]) - 1
        for symbol, data in universe.items()
    }
    winner = min(scores, key=lambda symbol: (-scores[symbol], symbol))
    if scores[winner] <= 0:
        winner = None
    return {
        symbol: result(
            "buy" if symbol == winner else "sell",
            f"Retorno 63 sesiones: {score:.2%}; líder: {winner or 'ninguno positivo'}",
        )
        for symbol, score in scores.items()
    }
