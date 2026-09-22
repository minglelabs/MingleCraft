import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from minglecraft.agents import (
    JevProvider,
    LocalModelProvider,
    OpenAIProvider,
    OpenRouterJevProvider,
    RandomProvider,
    RuleBasedProvider,
)
from minglecraft.bwapi.server import BridgeApplication, make_server
from minglecraft.bwapi.synthetic import SyntheticGame
from minglecraft.loop import AgentLoop
from minglecraft.strategy.policy import SHARED_POLICY


def provider_for(args):
    if args.provider == "rule":
        return RuleBasedProvider()
    if args.provider == "random":
        return RandomProvider(args.seed)
    if args.provider == "jev":
        return JevProvider(os.environ.get("TYPESAFE_API_KEY", ""), args.model or "jev-latest")
    if args.provider == "openrouter-jev":
        return OpenRouterJevProvider(
            os.environ.get("OPENROUTER_API_KEY", ""), args.model or "~typesafe/jev-latest"
        )
    if not args.model:
        raise ValueError("--model is required for OpenAI and local providers")
    if args.provider == "openai":
        return OpenAIProvider(os.environ.get("OPENAI_API_KEY", ""), args.model)
    return LocalModelProvider(args.model, args.base_url)


def main():
    parser = argparse.ArgumentParser(description="MingleCraft decision harness")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "serve"):
        command = sub.add_parser(name)
        command.add_argument(
            "--provider",
            choices=["rule", "random", "jev", "openrouter-jev", "openai", "local"],
            default="rule" if name == "demo" else "jev",
        )
        command.add_argument("--model")
        command.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
        command.add_argument("--seed", type=int, default=0)
        command.add_argument("--output", type=Path, default=Path("runs"))
        command.add_argument("--deadline-ms", type=int, default=10_000)
        command.add_argument("--ttl-frames", type=int, default=480)
        command.add_argument("--candidate-limit", type=int, default=50)
        command.add_argument(
            "--single-stage",
            action="store_true",
            help="Skip value estimate stage and call policy directly for fast response",
        )
        command.add_argument("--strategy-file", type=Path)
        command.add_argument(
            "--request-size-limit",
            type=int,
            default=1_500_000,
            help="Maximum wire-payload bytes per provider request (positive integer)",
        )
        command.add_argument(
            "--byte-budget",
            type=int,
            default=96_000,
            help="Conservative byte budget per request (positive integer, not a guaranteed provider limit)",
        )
        command.add_argument(
            "--token-budget",
            type=int,
            default=24_000,
            help="Conservative estimated token budget per request (positive integer, not an exact tokenizer count)",
        )
        command.add_argument(
            "--spatial-precision-px",
            type=int,
            default=8,
            help="Final uniform ground-coordinate cell size in pixels",
        )
        command.add_argument("--input-price", type=float, help="USD per million input tokens")
        command.add_argument("--output-price", type=float, help="USD per million output tokens")
        if name == "demo":
            command.add_argument("--steps", type=int, default=240)
        else:
            command.add_argument("--port", type=int, default=8765)
            command.add_argument("--map", default="(2)Destination.scx")
    report = sub.add_parser("report")
    report.add_argument("directory", type=Path)
    schema = sub.add_parser("schema")
    schema.add_argument("--output", type=Path, default=Path("docs/observation.schema.json"))
    args = parser.parse_args()
    if args.command == "report":
        records = [json.loads(p.read_text()) for p in sorted(args.directory.glob("*/summary.json"))]
        print(json.dumps(records, indent=2))
        return
    if args.command == "schema":
        from minglecraft.models import Observation

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(Observation.model_json_schema(), indent=2) + "\n")
        return
    if (args.input_price is None) != (args.output_price is None):
        parser.error("Set both --input-price and --output-price, or neither")
    pricing = None if args.input_price is None else (args.input_price, args.output_price)
    if args.strategy_file is None:
        strategy = SHARED_POLICY
    else:
        try:
            strategy = args.strategy_file.read_text(encoding="utf-8")
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError) as exc:
            parser.error(f"Cannot read strategy file: {exc}")
    if args.request_size_limit <= 0:
        parser.error("--request-size-limit must be a positive integer")

    def new_loop():
        return AgentLoop(
            provider_for(args),
            args.output,
            deadline_ms=args.deadline_ms,
            ttl_frames=args.ttl_frames,
            limit=args.candidate_limit,
            single_stage=getattr(args, "single_stage", False),
            seed=args.seed,
            mode="synthetic" if args.command == "demo" else "live",
            pricing=pricing,
            strategy=strategy,
            request_size_limit=args.request_size_limit,
            byte_budget=args.byte_budget,
            token_budget=args.token_budget,
            spatial_precision_px=args.spatial_precision_px,
        )

    try:
        # Validate settings and credentials before opening a listening socket.
        initial_loop = new_loop()
        if args.command == "demo":
            if args.steps < 1:
                parser.error("--steps must be positive")
            game = SyntheticGame(f"demo_{uuid.uuid4().hex[:12]}")

            async def run():
                for _ in range(args.steps):
                    decision = await initial_loop.step(game.observe())
                    game.execute(decision)
                    game.advance()
                return initial_loop.finish(game.observe(ended=True))

            print(json.dumps(asyncio.run(run()), indent=2))
        else:
            app = BridgeApplication(new_loop, args.map)
            server = make_server(app, args.port)
            print(
                f"MingleCraft listening at http://127.0.0.1:{server.server_port}; provider={args.provider}; map={args.map}",
                flush=True,
            )
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
                app.close()
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
