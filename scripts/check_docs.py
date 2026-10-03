"""Validate the documentation foundation, not a working commerce pipeline."""

import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = (
    "README.md", "LICENSE", "NOTICE", "AGENTS.md", "CONTEXT.md", "SECURITY.md",
    "CONTRIBUTING.md", "docs/VISION.md", "docs/SOURCES.md", "docs/ARCHITECTURE.md",
    "docs/CONFIGURATION.md", "docs/COST_MODEL.md", "docs/EVALUATIONS.md",
    "docs/ROADMAP.md", "config/radar.example.json",
)


def config_errors(config):
    errors = []
    if not isinstance(config, dict):
        return ["Configuration must be an object"]
    if config.get("status") != "design_only_not_executable":
        errors.append("Example must identify its documentary-only status")
    if config.get("mode") != "read_only":
        errors.append("Example must be read-only")
    sources = config.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append("Sources must be a nonempty list")
    else:
        ids = []
        for source in sources:
            if not isinstance(source, dict) or not isinstance(source.get("id"), str) or not source["id"]:
                errors.append("Each source needs a nonempty ID")
                continue
            ids.append(source["id"])
            if source.get("enabled") is not False or type(source.get("required")) is not bool:
                errors.append(f"Unverified example source must be disabled: {source['id']}")
        if len(ids) != len(set(ids)):
            errors.append("Source IDs must be unique")
    ai = config.get("ai", {})
    security = config.get("security", {})
    economics = config.get("economics", {})
    for name, section in (("ai", ai), ("security", security), ("economics", economics)):
        if not isinstance(section, dict):
            errors.append(f"{name} must be an object")
    if errors:
        return errors
    if ai.get("enabled") is not False or ai.get("paid_fallback_allowed") is not False:
        errors.append("Example must not enable AI or paid fallback implicitly")
    for field in ("outbound_actions_enabled", "raw_content_in_telemetry",
                  "private_network_fetch_allowed", "credential_values_in_config_allowed"):
        if security.get(field) is not False:
            errors.append(f"Unsafe or missing example security flag: {field}")
    if economics.get("listing_price_is_realized_sale") is not False:
        errors.append("Published price must not mean realized sale")
    if economics.get("missing_cost_policy") != "show_incomplete_without_full_profit":
        errors.append("Missing costs must stay unknown")
    for section in ("discovery", "concurrency"):
        values = config.get(section)
        if not isinstance(values, dict):
            errors.append(f"{section} must be an object")
            continue
        for key, value in values.items():
            if key == "continuous":
                if value is not False:
                    errors.append("Unbounded example execution is not allowed")
            elif type(value) is not int or value <= 0:
                errors.append(f"Positive integer budget required: {section}.{key}")
    return errors


def link_errors(root):
    errors = []
    for document in root.rglob("*.md"):
        if ".git" in document.parts:
            continue
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"!?\[[^\]]*\]\(([^\s)]+)\)", text):
            parsed = urlsplit(target)
            if parsed.scheme or target.startswith("#"):
                continue
            destination = (document.parent / unquote(parsed.path)).resolve()
            if not destination.is_relative_to(root.resolve()) or not destination.exists():
                errors.append(f"Broken/outside local link in {document.relative_to(root)}: {target}")
    return errors


def main():
    errors = [f"Missing file: {name}" for name in REQUIRED if not (ROOT / name).is_file()]
    errors.extend(link_errors(ROOT))
    try:
        errors.extend(config_errors(json.loads((ROOT / "config/radar.example.json").read_text(encoding="utf-8"))))
    except (OSError, ValueError) as exc:
        errors.append(f"Invalid configuration: {exc}")
    listing = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                             cwd=ROOT, capture_output=True, text=True, timeout=10)
    if listing.returncode:
        errors.append("Cannot inspect repository files with Git")
    for entry in listing.stdout.splitlines():
        path = Path(entry)
        if (any(part in (".local", ".agent-reach", ".codegraph", "node_modules") for part in path.parts)
                or path.name.startswith(".env") or path.suffix in (".key", ".pem", ".db", ".sqlite")
                or path.name in ("pairing.json", "credentials.json", "token.json")):
            errors.append(f"Private/generated path must not be versioned: {entry}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    if errors:
        return 1
    print("Documentation/configuration checks passed. This does not validate commerce connectors or profits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
