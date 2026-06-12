"""Base configuration and rule-loading for all services."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Rule:
    tool_name: str
    description: str
    content: str


def _parse_md(text: str) -> Rule | None:
    """Parse YAML frontmatter + body from a markdown file."""
    if not text.startswith("---"):
        return None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        meta = yaml.safe_load(parts[1]) or {}
        return Rule(
            tool_name=meta["tool_name"],
            description=meta["description"],
            content=parts[2].strip(),
        )
    except KeyError:
        return None


def _parse_yaml(text: str) -> Rule | None:
    """Parse _meta header + remaining keys from a YAML file."""
    try:
        data = yaml.safe_load(text) or {}
        meta = data.pop("_meta", None)
        if not meta:
            return None
        content = yaml.dump(data, allow_unicode=True, default_flow_style=False).strip()
        return Rule(
            tool_name=meta["tool_name"],
            description=meta["description"],
            content=content,
        )
    except (KeyError, TypeError):
        return None


def load_rules(rules_dir: Path) -> list[Rule]:
    """Load all rule files from a directory, sorted by filename."""
    if not rules_dir or not rules_dir.exists():
        return []
    rules = []
    for f in sorted(rules_dir.iterdir()):
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8")
        rule = _parse_md(text) if f.suffix == ".md" else (
            _parse_yaml(text) if f.suffix in (".yaml", ".yml") else None
        )
        if rule:
            rules.append(rule)
    return rules


@dataclass(frozen=True)
class ServiceConfig:
    display_name: str
    enabled: bool = True
    rules: list = field(default_factory=list)
