import argparse
import asyncio
import json
import os
import uuid
from pathlib import Path

from jevcraft.agents import (
    JevProvider,
    LocalModelProvider,
    OpenAIProvider,
    RandomProvider,
    RuleBasedProvider,
)
from jevcraft.bwapi.server import BridgeApplication, make_server
from jevcraft.bwapi.synthetic import SyntheticGame
from jevcraft.loop import AgentLoop


def provider_for(args):
    if args.provider == "rule":
        return RuleBasedProvider()
    if args.provider == "random":
        return RandomProvider(args.seed)
    if args.provider == "jev":
        return JevProvider(os.environ.get("TYPESAFE_API_KEY", ""), args.model or "jev-latest")
    if not args.model:
        raise ValueError("--model is required for OpenAI and local providers")
    if args.provider == "openai":
        return OpenAIProvider(os.environ.get("OPENAI_API_KEY", ""), args.model)
    return LocalModelProvider(args.model, args.base_url)


def main():
    parser = argparse.ArgumentParser(description="JevCraft decision harness")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("demo", "serve"):
        command = sub.add_parser(name)
        command.add_argument(
            "--provider", choices=["rule", "random", "jev", "openai", "local"], default="rule"
        )
        command.add_argument("--model")
        command.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
        command.add_argument("--seed", type=int, default=0)
        command.add_argument("--output", type=Path, default=Path("runs"))
        command.add_argument("--deadline-ms", type=int, default=200)
        command.add_argument("--ttl-frames", type=int, default=24)
        command.add_argument("--candidate-limit", type=int, default=50)
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
        from jevcraft.models import Observation

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(Observation.model_json_schema(), indent=2) + "\n")
        return
    if (args.input_price is None) != (args.output_price is None):
        parser.error("Set both --input-price and --output-price, or neither")
    pricing = None if args.input_price is None else (args.input_price, args.output_price)

    def new_loop():
        return AgentLoop(
            provider_for(args),
            args.output,
            deadline_ms=args.deadline_ms,
            ttl_frames=args.ttl_frames,
            limit=args.candidate_limit,
            seed=args.seed,
            mode="synthetic" if args.command == "demo" else "live",
            pricing=pricing,
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
                f"JevCraft listening at http://127.0.0.1:{server.server_port}; provider={args.provider}; map={args.map}",
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
