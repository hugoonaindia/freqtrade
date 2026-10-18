"""Standalone historical evaluation without starting or stopping Paper trading."""

import argparse
import json
from pathlib import Path

from .backtest import Config, compare
from .backtest_data import load_histories
from .backtest_jobs import historical_client


def main():
    parser = argparse.ArgumentParser(
        description="Backtest de seis estrategias de bolsa, sin órdenes."
    )
    parser.add_argument("--symbols", default="AAPL,MSFT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--capital", type=float, default=10000)
    parser.add_argument("--budget", type=float, default=500)
    parser.add_argument("--fee-bps", type=float, default=5)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--output", default=".equities-state/backtest.json")
    args = parser.parse_args()
    try:
        from .backtest_jobs import parameters

        symbols, config = parameters(
            {
                "symbols": args.symbols.upper().split(","),
                **{key: getattr(args, key) for key in Config.__dataclass_fields__},
            }
        )
        data = load_histories(historical_client(), symbols, config, print)
        report = compare(data, config)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False))
        print("Estrategia | Retorno % | Caída máxima % | Reducción caída (pp)")
        for name, row in report["strategies"].items():
            print(
                f"{name}: {row['metrics']['return_pct']:.2f} | "
                f"{row['metrics']['max_drawdown_pct']:.2f} | "
                f"{row['protection']['drawdown_reduction_pp']:.2f}"
            )
        print("Informe:", target.resolve())
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
