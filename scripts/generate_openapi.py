#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure project root is on sys.path when running from scripts/
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import yaml  # type: ignore
except Exception as e:  # pragma: no cover
    yaml = None

# Import the FastAPI app
from src.api.routes import app


def generate_schema_dict() -> dict:
    """Return the OpenAPI schema dict from the FastAPI app."""
    # Ensure schema is built
    return app.openapi()


def write_output(schema: dict, out_path: Path, fmt: str) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "yaml":
        if yaml is None:
            raise RuntimeError(
                "PyYAML is required to output YAML. Install with: pip install pyyaml"
            )
        text = yaml.safe_dump(schema, sort_keys=False, allow_unicode=True)
        out_path.write_text(text, encoding="utf-8")
    else:
        text = json.dumps(schema, indent=2, ensure_ascii=False)
        out_path.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate OpenAPI schema from FastAPI app.")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("openapi.yaml"),
        help="Output file path (default: openapi.yaml)",
    )
    parser.add_argument(
        "--format",
        choices=["yaml", "json"],
        default="yaml",
        help="Output format (default: yaml)",
    )
    args = parser.parse_args()

    schema = generate_schema_dict()
    write_output(schema, args.out, args.format)
    print(f"OpenAPI schema written to {args.out} in {args.format.upper()} format")


if __name__ == "__main__":
    main()
