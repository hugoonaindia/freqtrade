"""Chronological holdout with strategy chosen before the evaluation starts."""

from dataclasses import asdict, replace
from datetime import date, timedelta

from .backtest import compare, session_date
from .strategies import STRATEGIES
from .temporal_policy import year_before, enforce_development


def validate_holdout(config, specification):
    if not isinstance(specification, dict) or set(specification) != {"start", "strategy"}:
        raise ValueError(
            "Fuera de muestra: indica fecha de inicio y estrategia fijada de antemano."
        )
    try:
        split = date.fromisoformat(specification["start"])
        if not date.fromisoformat(config.start) < split <= date.fromisoformat(config.end):
            raise ValueError
        if specification["strategy"] not in STRATEGIES:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(
            "Fuera de muestra: la fecha debe ser posterior al inicio y no superar el final; elige una estrategia válida."
        ) from None
    if split > year_before(date.fromisoformat(config.end)):
        raise ValueError(
            "Fuera de muestra: reserva al menos un año natural completo entre el inicio de la reserva y la fecha final. Amplía el período o adelanta el inicio de la reserva."
        )
    enforce_development(replace(config, end=(split - timedelta(days=1)).isoformat()))
    return {"start": split.isoformat(), "strategy": specification["strategy"]}


def evaluate_holdout(
    histories, config, specification, progress=lambda message: None, *, execution_data=None
):
    specification = validate_holdout(config, specification)
    split = specification["start"]
    development_config = replace(
        config, end=(date.fromisoformat(split) - timedelta(days=1)).isoformat()
    )
    evaluation_config = replace(config, start=split)
    # Development never receives bars from the reserved period, even for validation.
    development_data = {
        symbol: [bar for bar in bars if session_date(bar) < split]
        for symbol, bars in histories.items()
    }
    progress("Evaluando desarrollo, sin acceder a las sesiones reservadas…")
    development = (
        compare(
            development_data, development_config, execution_data, strategy=specification["strategy"]
        )
        if execution_data
        else compare(development_data, development_config)
    )
    progress("Evaluando fuera de muestra con estrategia y costes fijados…")
    evaluation = (
        compare(histories, evaluation_config, execution_data, strategy=specification["strategy"])
        if execution_data
        else compare(histories, evaluation_config)
    )
    if date.fromisoformat(evaluation["actual_start"]) > year_before(
        date.fromisoformat(evaluation["actual_end"])
    ):
        raise ValueError(
            "Fuera de muestra: las sesiones disponibles no cubren un año completo. Adelanta el inicio de la reserva para compensar festivos y fines de semana."
        )
    selected = specification["strategy"]
    evaluation["strategies"] = {selected: evaluation["strategies"][selected]}
    evaluation["validation"].update(
        out_of_sample=True, method="chronological_holdout", strategy_fixed_before_run=selected
    )
    evaluation["out_of_sample"] = {
        "specification": specification,
        "requested_config": asdict(config),
        "development": development,
        "selected_strategy": selected,
        "development_metrics": development["strategies"][selected]["metrics"],
        "evaluation_metrics": evaluation["strategies"][selected]["metrics"],
        "notes": [
            "Estrategia elegida antes de ejecutar; no se selecciona por resultados del tramo reservado.",
            "Ambos tramos empiezan con el mismo capital, sin posiciones ni órdenes heredadas.",
            "Los indicadores del tramo reservado usan sesiones anteriores como calentamiento; cada señal usa solo el pasado.",
            "No hay optimización, walk-forward ni prueba estadística de rentabilidad.",
            "Repetir o ajustar tras ver el resultado contamina la reserva; utiliza otro período intacto.",
            "La ejecución simulada es un escenario; no equivale a fills confirmados por Alpaca.",
        ],
    }
    evaluation["assumptions"] += evaluation["out_of_sample"]["notes"]
    return evaluation
