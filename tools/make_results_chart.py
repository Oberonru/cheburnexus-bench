#!/usr/bin/env python3
"""Regenerate assets/results-{light,dark}.svg from results/published/.

Grouped horizontal bar chart of PRECISION, primary cell, for the C# repos plus
class-validator (the one TypeScript repo with a clean grep/repowise/cheburnexus-ts
triple published so far — zod's cheburnexus-ts is excluded from results/published/
pending investigation, so it is left out of this chart too).

No third-party dependencies on purpose — this repo's own quickstart promises
"Python 3, standard library only", and a chart script is not an exception. Output
is hand-built SVG: two files, one per color-scheme, both transparent-background so
they drop into a GitHub README <picture> switch.

Usage:
    python3 tools/make_results_chart.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLISHED = ROOT / "results" / "published"
ASSETS = ROOT / "assets"

# (display label, repo dir name, cell, arm dir name for the "cheburnexus" column)
ROWS = [
    ("serilog/serilog", "serilog", "without-tests", "cheburnexus"),
    ("App-vNext/Polly", "Polly", "without-tests", "cheburnexus"),
    ("FluentValidation", "FluentValidation", "without-tests", "cheburnexus"),
    ("jbogard/MediatR", "MediatR", "without-tests", "cheburnexus"),
    ("AutoMapper", "AutoMapper", "without-tests", "cheburnexus"),
    ("Humanizr/Humanizer", "Humanizer", "without-tests", "cheburnexus"),
    ("typestack/class-validator", "class-validator", "with-tests", "cheburnexus-ts"),
]

# arm key in ROWS's last column varies per repo (cheburnexus vs cheburnexus-ts); grep/repowise
# are the same dir name for every repo.
SERIES = ["grep", "repowise", "cheburnexus"]  # display order, top to bottom within a group

# Fixed categorical order — never cycled, per dataviz skill. Gray reads as "no signal / baseline"
# on purpose; both modes pass the CVD-separation and normal-vision-floor checks against the other
# two (validated with the skill's validate_palette.js). Direct value labels on every bar are the
# required secondary encoding for that intentional low-chroma slot.
COLORS_LIGHT = {"grep": "#6b6b68", "repowise": "#eda100", "cheburnexus": "#2a78d6"}
COLORS_DARK = {"grep": "#7d7d77", "repowise": "#c98500", "cheburnexus": "#3987e5"}

LABELS = {"grep": "grep", "repowise": "repowise", "cheburnexus": "cheburnexus"}


def load_precision(repo: str, cell: str, arm: str) -> float | None:
    p = PUBLISHED / arm / repo / cell / "result.json"
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    primary = data["cells"][0]
    return primary.get("precision")


def gather() -> list[tuple[str, dict[str, float | None]]]:
    out = []
    for label, repo, cell, cheb_arm in ROWS:
        values: dict[str, float | None] = {}
        for series in SERIES:
            arm_dir = cheb_arm if series == "cheburnexus" else series
            values[series] = load_precision(repo, cell, arm_dir)
        out.append((label, values))
    return out


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def render(data: list[tuple[str, dict[str, float | None]]], colors: dict[str, str],
           text_color: str, muted_color: str, grid_color: str) -> str:
    width = 1000
    left_margin = 190
    right_margin = 70
    top_margin = 70
    bottom_margin = 30
    bar_h = 20
    bar_gap = 4
    group_gap = 22
    group_h = len(SERIES) * bar_h + (len(SERIES) - 1) * bar_gap
    row_h = group_h + group_gap
    plot_w = width - left_margin - right_margin
    height = top_margin + len(data) * row_h + bottom_margin

    font = 'font-family="-apple-system, \'Segoe UI\', Roboto, Helvetica, Arial, sans-serif"'

    parts = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Precision by repository and arm, primary cell">'
    )
    parts.append(f'<title>Precision by repository and arm (primary cell)</title>')

    # title
    parts.append(
        f'<text x="{left_margin}" y="22" {font} font-size="17" font-weight="600" '
        f'fill="{text_color}">Precision, primary cell</text>'
    )
    parts.append(
        f'<text x="{left_margin}" y="40" {font} font-size="12" '
        f'fill="{muted_color}">matched edges / edges emitted, per repo — higher is better</text>'
    )

    # legend, its own row so it never collides with the title/subtitle above
    ly = 60
    lx = left_margin
    for i, series in enumerate(SERIES):
        gx = lx + i * 160
        parts.append(f'<rect x="{gx}" y="{ly - 10}" width="12" height="12" rx="2" fill="{colors[series]}"/>')
        parts.append(
            f'<text x="{gx + 18}" y="{ly}" {font} font-size="12" fill="{text_color}">{esc(LABELS[series])}</text>'
        )

    plot_top = top_margin
    # gridlines at 0, 0.25, 0.5, 0.75, 1.0
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gx = left_margin + plot_w * frac
        gy_top = plot_top
        gy_bot = plot_top + len(data) * row_h - group_gap + 6
        parts.append(f'<line x1="{gx:.1f}" y1="{gy_top}" x2="{gx:.1f}" y2="{gy_bot}" '
                      f'stroke="{grid_color}" stroke-width="1"/>')
        parts.append(f'<text x="{gx:.1f}" y="{gy_bot + 16}" {font} font-size="11" '
                      f'fill="{muted_color}" text-anchor="middle">{frac:.2f}</text>')

    y = plot_top
    for label, values in data:
        group_top = y
        # repo label, vertically centered on the group
        label_y = group_top + group_h / 2 + 4
        parts.append(
            f'<text x="{left_margin - 12}" y="{label_y:.1f}" {font} font-size="13" '
            f'fill="{text_color}" text-anchor="end">{esc(label)}</text>'
        )
        by = group_top
        for series in SERIES:
            v = values.get(series)
            bx = left_margin
            bw = 0.0 if v is None else max(0.0, min(1.0, v)) * plot_w
            color = colors[series]
            if v is None:
                # no data: thin dashed baseline, "n/a" label
                parts.append(
                    f'<line x1="{bx}" y1="{by + bar_h / 2:.1f}" x2="{bx + 26}" y2="{by + bar_h / 2:.1f}" '
                    f'stroke="{muted_color}" stroke-width="2" stroke-dasharray="3,3"/>'
                )
                parts.append(
                    f'<text x="{bx + 34}" y="{by + bar_h / 2 + 4:.1f}" {font} font-size="11" '
                    f'fill="{muted_color}">n/a</text>'
                )
            else:
                parts.append(
                    f'<rect x="{bx:.1f}" y="{by:.1f}" width="{max(bw, 3):.1f}" height="{bar_h}" '
                    f'rx="3" fill="{color}"/>'
                )
                label_x = bx + bw + 8
                parts.append(
                    f'<text x="{label_x:.1f}" y="{by + bar_h / 2 + 4:.1f}" {font} font-size="11" '
                    f'fill="{text_color}">{v:.3f}</text>'
                )
            by += bar_h + bar_gap
        y += row_h

    parts.append('</svg>')
    return "\n".join(parts)


def main() -> None:
    data = gather()
    ASSETS.mkdir(parents=True, exist_ok=True)

    light_svg = render(
        data, COLORS_LIGHT,
        text_color="#0b0b0b", muted_color="#52514e", grid_color="#e3e2dd",
    )
    dark_svg = render(
        data, COLORS_DARK,
        text_color="#ffffff", muted_color="#c3c2b7", grid_color="#33332f",
    )

    (ASSETS / "results-light.svg").write_text(light_svg, encoding="utf-8")
    (ASSETS / "results-dark.svg").write_text(dark_svg, encoding="utf-8")
    print(f"wrote {ASSETS / 'results-light.svg'}")
    print(f"wrote {ASSETS / 'results-dark.svg'}")


if __name__ == "__main__":
    main()
