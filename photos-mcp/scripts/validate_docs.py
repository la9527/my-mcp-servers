#!/usr/bin/env python3
"""Validate the current documentation tree without treating archived files as current."""

from __future__ import annotations

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
DOC_INDEX = DOCS / "README.md"
ARCHIVE = DOCS / "99-archive"
ARCHIVED_LEGACY = ARCHIVE / "99-legacy-2026-08-09"

REQUIRED_DOCUMENTS = (
    ROOT / "README.md",
    DOC_INDEX,
    DOCS / "01-getting-started" / "02-installation.md",
    DOCS / "02-user-guide" / "README.md",
    DOCS / "03-integration" / "README.md",
    DOCS / "03-integration" / "02-tool-reference.md",
    DOCS / "04-architecture" / "README.md",
    DOCS / "05-operations" / "04-troubleshooting.md",
    DOCS / "06-development" / "02-testing.md",
    DOCS / "07-design-system" / "README.md",
    DOCS / "09-roadmap" / "README.md",
    ARCHIVE / "README.md",
)

LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
NUMBERED_NAME_PATTERN = re.compile(r"\d{2}-.+")


def current_markdown_files() -> list[Path]:
    files = [ROOT / "README.md"]
    files.extend(
        path
        for path in DOCS.rglob("*.md")
        if ARCHIVE not in path.parents and path != ARCHIVE / "README.md"
    )
    files.append(ARCHIVE / "README.md")
    return sorted(set(files))


def local_link_target(source: Path, raw_target: str) -> Path | None:
    target = raw_target.strip().strip("<>")
    if not target or target.startswith(("#", "http://", "https://", "mailto:")):
        return None
    path_text = target.split("#", 1)[0].split("?", 1)[0]
    if not path_text:
        return None
    return (source.parent / path_text).resolve()


def validate() -> list[str]:
    errors: list[str] = []

    for path in REQUIRED_DOCUMENTS:
        if not path.is_file():
            errors.append(f"필수 문서 없음: {path.relative_to(ROOT)}")

    legacy_design_system = ROOT / "design-system"
    if legacy_design_system.exists():
        errors.append("현행 디자인 시스템은 docs/07-design-system 아래에만 있어야 합니다")

    for path in DOCS.rglob("*"):
        if path == ARCHIVED_LEGACY or ARCHIVED_LEGACY in path.parents:
            continue
        if path.is_dir() or (path.suffix.lower() == ".md" and path.name != "README.md"):
            if not NUMBERED_NAME_PATTERN.fullmatch(path.name):
                errors.append(f"숫자 접두사 누락: {path.relative_to(ROOT)}")

    current_files = {path.resolve() for path in current_markdown_files()}
    reachable = {DOC_INDEX.resolve()}
    pending = list(reachable)
    while pending:
        source = pending.pop()
        content = source.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(content):
            target = local_link_target(source, raw_target)
            if target is not None and target.is_dir():
                target = target / "README.md"
            if target in current_files and target not in reachable:
                reachable.add(target)
                pending.append(target)

    for path in current_markdown_files():
        if path == ROOT / "README.md":
            continue
        if path.resolve() not in reachable:
            errors.append(f"문서 인덱스에서 도달할 수 없음: {path.relative_to(ROOT)}")

    for source in current_markdown_files():
        if not source.is_file():
            continue
        content = source.read_text(encoding="utf-8")
        for line_number, line in enumerate(content.splitlines(), start=1):
            if line.endswith((" ", "\t")):
                errors.append(
                    f"줄 끝 공백: {source.relative_to(ROOT)}:{line_number}"
                )
        for raw_target in LINK_PATTERN.findall(content):
            target = local_link_target(source, raw_target)
            if target is not None and not target.exists():
                errors.append(
                    f"깨진 링크: {source.relative_to(ROOT)} -> {raw_target}"
                )

        if source not in {DOC_INDEX, ARCHIVE / "README.md"}:
            for raw_target in LINK_PATTERN.findall(content):
                target = local_link_target(source, raw_target)
                if target is not None and (target == ARCHIVE or ARCHIVE in target.parents):
                    errors.append(
                        f"현행 문서가 archive를 참조함: {source.relative_to(ROOT)}"
                    )

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("문서 검증 실패", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"문서 검증 통과: 검증 대상 Markdown {len(current_markdown_files())}개")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
