"""hl skills — skill discovery and registry."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

skills_app = typer.Typer(no_args_is_help=True)


@skills_app.command("list")
def skills_list(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List all available skills by reading skills/*/SKILL.md frontmatter."""
    import json

    project_root = Path(__file__).resolve().parent.parent.parent
    skills_dir = project_root / "skills"

    if not skills_dir.exists():
        typer.echo("No skills directory found.")
        raise typer.Exit(1)

    skills = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        skill_name = skill_md.parent.name
        meta = _parse_frontmatter(skill_md)
        skills.append({
            "name": meta.get("name", skill_name),
            "version": meta.get("version", ""),
            "description": meta.get("description", ""),
            "path": str(skill_md.parent.relative_to(project_root)),
        })

    if not skills:
        typer.echo("No skills found.")
        raise typer.Exit(0)

    if json_output:
        typer.echo(json.dumps(skills, indent=2))
    else:
        typer.echo(f"{'Name':<25} {'Version':<10} {'Description'}")
        typer.echo("-" * 80)
        for s in skills:
            typer.echo(f"{s['name']:<25} {s['version']:<10} {s['description'][:45]}")
        typer.echo(f"\n{len(skills)} skill(s) found.")


def _parse_frontmatter(path: Path) -> dict:
    """Parse YAML frontmatter from a SKILL.md file."""
    text = path.read_text()
    if not text.startswith("---"):
        return {}

    end = text.find("---", 3)
    if end == -1:
        return {}

    frontmatter = text[3:end].strip()
    result = {}
    current_key = None
    current_value_lines = []

    for line in frontmatter.splitlines():
        stripped = line.strip()
        if stripped.startswith("-") or stripped.startswith("["):
            continue  # skip list items
        if ":" in stripped and not stripped.startswith(" "):
            # Save previous key
            if current_key and current_value_lines:
                result[current_key] = " ".join(current_value_lines)
            key, _, value = stripped.partition(":")
            current_key = key.strip()
            value = value.strip().strip('"').strip("'")
            # Skip YAML multiline indicators
            if value in (">-", "|", ">", "|-"):
                current_value_lines = []
            elif value:
                current_value_lines = [value]
            else:
                current_value_lines = []
        elif current_key and stripped:
            # Continuation line for multiline value
            current_value_lines.append(stripped)

    # Save last key
    if current_key and current_value_lines:
        result[current_key] = " ".join(current_value_lines)

    return result
