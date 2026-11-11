"""Standalone historical evaluation without starting or stopping Paper trading."""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

from .backtest import Config, compare
from .backtest_data import load_histories
from .backtest_jobs import historical_client
from .temporal_policy import enforce_development
from .strategies import STRATEGIES


def walk_forward_backtest(data, config, base_config, train_years, test_months, step_months, progress=print):
    """Run walk-forward backtest: train on N years, test on M months, step by K months."""
    from .backtest import simulate
    from .strategies import STRATEGIES
    
    all_dates = sorted(set().union(*[set(d.keys()) for d in data.values()]))
    if not all_dates:
        return None
    
    start_date = date.fromisoformat(config.start)
    end_date = date.fromisoformat(config.end)
    
    results = []
    current_train_end = start_date + timedelta(days=train_years * 365)
    
    while current_train_end + timedelta(days=test_months * 30) <= end_date:
        test_start = current_train_end
        test_end = min(current_train_end + timedelta(days=test_months * 30), end_date)
        
        if test_start >= test_end:
            break
            
        progress(f"Walk-forward: train until {current_train_end}, test {test_start} to {test_end}")
        
        # Create config for this window
        test_config = Config(
            start=config.start,
            end=test_end.isoformat(),
            capital=config.capital,
            budget=config.budget,
            fee_bps=config.fee_bps,
            slippage_bps=config.slippage_bps,
            profile=config.profile,
            execution=config.execution,
            execution_strategy=config.execution_strategy,
            feed=config.feed,
            latency_ms=config.latency_ms,
            participation_pct=config.participation_pct,
            spread_bps=config.spread_bps,
            quote_sample_ms=config.quote_sample_ms,
            max_position_pct=config.max_position_pct,
        )
        
        # Filter data to test period
        # Filter data to test period
        test_data = {}
        for symbol, bars in data.items():
            test_bars = [b for b in bars if test_start <= datetime.fromisoformat(b["t"]).date() <= test_end]
            if test_bars:
                test_data[symbol] = test_bars
        
        if not test_data:
            break
            
        # Run backtest on test period
        try:
            report = simulate(test_data, test_config, config.execution_strategy)
            report["walk_forward_window"] = {
                "train_end": current_train_end.isoformat(),
                "test_start": test_start.isoformat(),
                "test_end": test_end.isoformat(),
            }
            results.append(report)
        except Exception as e:
            progress(f"Window failed: {e}")
        
        current_train_end += timedelta(days=step_months * 30)
    
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Backtest de seis estrategias de bolsa, sin órdenes."
    )
    parser.add_argument("--symbols", default="AAPL,MSFT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--capital", type=float, default=10000)
    parser.add_argument("--budget", type=float, default=500)
    parser.add_argument("--profile", choices=("budget", "swing"), default="budget")
    parser.add_argument("--fee-bps", type=float, default=5)
    parser.add_argument("--slippage-bps", type=float, default=5)
    parser.add_argument("--execution", choices=("daily", "intraday"), default="daily")
    parser.add_argument("--feed", choices=("iex", "sip"), default="iex")
    parser.add_argument("--execution-strategy", choices=STRATEGIES, default="sma")
    parser.add_argument("--latency-ms", type=float, default=1000)
    parser.add_argument("--participation-pct", type=float, default=1)
    parser.add_argument("--spread-bps", type=float, default=10)
    parser.add_argument("--quote-sample-ms", type=float, default=100)
    parser.add_argument("--max-position-pct", type=float, default=100)
    parser.add_argument("--regime-filter", action="store_true", help="Enable regime filter (SPY > MA200)")
    parser.add_argument("--vol-sizing", action="store_true", help="Enable volatility-based position sizing")
    parser.add_argument("--correlation-check", action="store_true", help="Enable correlation check for entries")
    parser.add_argument("--trailing-stop", type=float, default=0, help="Trailing stop percentage (0=disabled)")
    parser.add_argument("--max-daily-loss", type=float, default=0, help="Max daily loss %% kill switch (0=disabled)")
    parser.add_argument("--walk-forward", action="store_true", help="Enable walk-forward backtest")
    parser.add_argument("--wf-train-years", type=int, default=2, help="Walk-forward training years")
    parser.add_argument("--wf-test-months", type=int, default=6, help="Walk-forward test months")
    parser.add_argument("--wf-step-months", type=int, default=3, help="Walk-forward step months")
    parser.add_argument("--bootstrap-samples", type=int, default=1000, help="Bootstrap samples for confidence intervals")
    parser.add_argument("--bootstrap-confidence", type=float, default=0.95, help="Bootstrap confidence level (0-1)")
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
        enforce_development(config)
        data = load_histories(historical_client(), symbols, config, print)
        
        # Handle walk-forward backtest
        if args.walk_forward:
            print(f"Walk-forward backtest: train={args.wf_train_years}y, test={args.wf_test_months}m, step={args.wf_step_months}m")
            wf_results = walk_forward_backtest(
                data, config, config, 
                args.wf_train_years, args.wf_test_months, args.wf_step_months,
                progress=print
            )
            if wf_results:
                # Aggregate results
                avg_return = sum(r["metrics"]["return_pct"] for r in wf_results) / len(wf_results)
                avg_dd = sum(r["metrics"]["max_drawdown_pct"] for r in wf_results) / len(wf_results)
                print(f"\nWalk-forward aggregate ({len(wf_results)} windows):")
                print(f"  Avg Return: {avg_return:.2f}%")
                print(f"  Avg Max DD: {avg_dd:.2f}%")
                report = {
                    "walk_forward": True,
                    "windows": len(wf_results),
                    "aggregate": {
                        "avg_return_pct": avg_return,
                        "avg_max_drawdown_pct": avg_dd,
                    },
                    "windows_detail": wf_results,
                }
            else:
                report = {"walk_forward": True, "windows": 0, "error": "No valid windows"}
        else:
            if config.execution == "intraday":
                from .execution_replay import ExecutionData
                execution_data = ExecutionData(historical_client(), data, config, print)
                report = compare(data, config, execution_data)
            else:
                report = compare(data, config)
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        if not args.walk_forward:
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