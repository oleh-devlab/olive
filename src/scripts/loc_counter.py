"""Count Python lines of code in the project.

"Code lines" means non-blank lines that don't start with '#'. Docstrings count
as code — they're content someone wrote and maintains.

    python src/scripts/loc_counter.py            # from the repo root
    python src/scripts/loc_counter.py --top 20
    python src/scripts/loc_counter.py --by-dir
"""

import argparse
import os
import sys
from fnmatch import fnmatch
from pathlib import Path

# Directories skipped entirely, by relative path or by bare name at any depth.
# Bare names match wherever they turn up, so entries stay valid on branches and
# machines where a directory sits somewhere else — or isn't checked out at all.
EXCLUDED_DIRS = (
    ".git",
    ".venv",
    "__pycache__",
    "probes",  # gitignored API probes, not project code
    "archive_modules",  # retired code kept for reference
    "automatic_timetable_py",  # submodule with its own repo
    "inflation_calculator",  # submodule with its own repo
)

# Files skipped, matched against the relative path, the bare name, or as a glob.
EXCLUDED_FILES = (
    "__init__.py",
    "settings.py",  # gitignored, generated from settings.py.example
    "core/cache.py",  # module of globals, not logic
    "loc_counter.py",  # this script
)


def is_excluded_dir(rel_dir: str) -> bool:
    name = rel_dir.rsplit("/", 1)[-1]
    return any(rel_dir == pattern or ("/" not in pattern and name == pattern) for pattern in EXCLUDED_DIRS)


def is_excluded_file(rel_file: str) -> bool:
    name = rel_file.rsplit("/", 1)[-1]
    return any(rel_file == pattern or name == pattern or fnmatch(rel_file, pattern) for pattern in EXCLUDED_FILES)


def count_lines(path: Path) -> tuple[int, int]:
    """Return (code lines, physical lines). Raises OSError/UnicodeDecodeError on unreadable files."""
    code = 0
    physical = 0
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            physical += 1
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                code += 1
    return code, physical


def collect(root: Path) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """Walk root, returning per-file code counts, physical counts, and unreadable files."""
    code_counts: dict[str, int] = {}
    physical_counts: dict[str, int] = {}
    unreadable: list[str] = []

    for dirpath_str, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath_str).relative_to(root).as_posix()
        if rel_dir == ".":
            rel_dir = ""

        # Prune in place so os.walk never descends into excluded trees.
        dirnames[:] = [d for d in dirnames if not is_excluded_dir(f"{rel_dir}/{d}" if rel_dir else d)]

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            rel_file = f"{rel_dir}/{filename}" if rel_dir else filename
            if is_excluded_file(rel_file):
                continue

            try:
                code, physical = count_lines(Path(dirpath_str) / filename)
            except (OSError, UnicodeDecodeError) as e:
                unreadable.append(f"{rel_file}: {e.__class__.__name__}: {e}")
                continue

            code_counts[rel_file] = code
            physical_counts[rel_file] = physical

    return code_counts, physical_counts, unreadable


def print_by_dir(code_counts: dict[str, int]) -> None:
    totals: dict[str, int] = {}
    for rel_file, count in code_counts.items():
        directory = rel_file.rsplit("/", 1)[0] if "/" in rel_file else "."
        totals[directory] = totals.get(directory, 0) + count

    width = max(len(d) for d in totals) if totals else 0
    for directory, count in sorted(totals.items(), key=lambda item: -item[1]):
        print(f"{directory:<{width}}  {count}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("root", nargs="?", default=".", type=Path, help="directory to scan (default: cwd)")
    parser.add_argument("--top", type=int, metavar="N", help="show only the N largest files")
    parser.add_argument("--by-dir", action="store_true", help="roll totals up per directory instead of per file")
    parser.add_argument("--sort-by-path", action="store_true", help="sort by path instead of by size")
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 2

    code_counts, physical_counts, unreadable = collect(root)

    if not code_counts:
        print(f"No Python files found under {root}")
        return 0

    if args.by_dir:
        print_by_dir(code_counts)
    else:
        rows = sorted(code_counts.items()) if args.sort_by_path else sorted(code_counts.items(), key=lambda i: -i[1])
        if args.top:
            rows = rows[: args.top]

        width = max(len(path) for path, _ in rows)
        for path, count in rows:
            print(f"{path:<{width}}  {count}")

    print("-" * 40)
    print(f"Files:          {len(code_counts)}")
    print(f"Code lines:     {sum(code_counts.values())}")
    print(f"Physical lines: {sum(physical_counts.values())}")

    if unreadable:
        print(f"\n{len(unreadable)} file(s) could not be read:", file=sys.stderr)
        for entry in unreadable:
            print(f"  {entry}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
