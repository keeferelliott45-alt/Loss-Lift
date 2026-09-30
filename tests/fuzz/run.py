"""Command-line fuzz runner for the silent-CLEAN hunt.

    python -m tests.fuzz.run --family hostile --seed-start 0 --count 60
    python -m tests.fuzz.run --family clean --seed-start 0 --count 60
    python -m tests.fuzz.run --family hostile --budget 20m --seed-start 60
    python -m tests.fuzz.run --case 12:wrong_total,duplicate_page

Every case is reproducible from ``(seed, operator list)``: the generator, the
operator draw and the operator order are all derived from the seed.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import pymupdf

from core.extract_vision import VisionExtraction, parse_vision_response
from core.pipeline import run_pipeline

from tests.fuzz import mutations
from tests.fuzz.generator import Case, clean_case, render, scanned_payloads
from tests.fuzz.oracle import LOUD, SAFE, SILENT, UNNAMED, classify

FAMILIES = ("clean", "hostile")


def choose_operators(seed: int, max_ops: int = 3) -> tuple[str, ...]:
    rng = random.Random(seed * 7919 + 13)
    names = list(mutations.ALL_OPERATOR_NAMES)
    count = rng.randint(1, max_ops)
    return tuple(sorted(rng.sample(names, min(count, len(names)))))


def apply_operators(case: Case, names: tuple[str, ...], seed: int) -> Case:
    rng = random.Random(seed * 104729 + 7)
    for name in names:
        op = mutations.OPERATORS[name]
        if op.applies(case):
            op.fn(case, rng)
    return case


def build_case(seed: int, names: tuple[str, ...]) -> Case:
    return apply_operators(clean_case(seed), names, seed)


def run_case(case: Case, workdir: Path, tag: str):
    path = render(case, workdir / f"{tag}.pdf")
    return run_pipeline(path, use_vision=False, profiles_dir=workdir / f"{tag}_profiles")


def rasterise(source: Path, target: Path, dpi: int = 72) -> Path:
    """Turn a rendered page into a synthetic scan: an image on a blank page."""
    source_document = pymupdf.open(source)
    out = pymupdf.open()
    for page in source_document:
        pixmap = page.get_pixmap(dpi=dpi)
        new = out.new_page(width=page.rect.width, height=page.rect.height)
        new.insert_image(new.rect, pixmap=pixmap)
    out.save(target)
    out.close()
    source_document.close()
    return target


def run_case_scanned(case: Case, workdir: Path, tag: str, *, dpi: int = 72):
    """Run a case through the replayed-vision path, never a live model."""
    digital = render(case, workdir / f"{tag}_digital.pdf")
    scanned = rasterise(digital, workdir / f"{tag}_scanned.pdf", dpi=dpi)
    payloads = scanned_payloads(case)

    def extractor(path, pages, **kwargs):
        return VisionExtraction(
            tables=[parse_vision_response(payloads[page], page) for page in pages],
            failures={},
        )

    return run_pipeline(scanned, use_vision=True, vision_extractor=extractor,
                        profiles_dir=workdir / f"{tag}_vision")


def _parse_budget(text: str) -> float:
    text = text.strip().lower()
    unit = text[-1]
    value = float(text[:-1]) if unit.isalpha() else float(text)
    return value * {"s": 1, "m": 60, "h": 3600}.get(unit, 1)


def _seed_cases(family: str, seed: int, max_ops: int):
    if family == "clean":
        return ()
    return choose_operators(seed, max_ops)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=FAMILIES, default="hostile")
    parser.add_argument("--seed-start", type=int, default=0)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--budget", type=str, default="")
    parser.add_argument("--max-ops", type=int, default=3)
    parser.add_argument("--ops", type=str, default="",
                        help="force one operator set (comma separated) on every seed")
    parser.add_argument("--vision", action="store_true",
                        help="read every case through the synthetic replay path")
    parser.add_argument("--dpi", type=int, default=72)
    parser.add_argument("--case", type=str, default="")
    parser.add_argument("--json", type=str, default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    if args.case:
        seed_text, _, ops_text = args.case.partition(":")
        seed = int(seed_text)
        names = tuple(sorted(n for n in ops_text.split(",") if n))
        names = tuple(n for n in names if n in mutations.OPERATORS)
        with tempfile.TemporaryDirectory(prefix="fuzz_case_") as tmp:
            case = build_case(seed, names)
            if args.vision:
                result = run_case_scanned(case, Path(tmp), "case", dpi=args.dpi)
            else:
                result = run_case(case, Path(tmp), "case")
            outcome = classify(case, result)
            print(json.dumps({
                "seed": seed,
                "operators": list(names),
                "outcome": asdict(outcome),
            }, indent=2, default=str))
        return 0

    deadline = time.monotonic() + _parse_budget(args.budget) if args.budget else None
    forced_ops = tuple(sorted(n for n in args.ops.split(",") if n)) if args.ops else ()
    tally: dict[str, int] = {SAFE: 0, SILENT: 0, LOUD: 0, UNNAMED: 0}
    silent_by_ops: dict[tuple[str, ...], int] = {}
    loud_by_ops: dict[tuple[str, ...], int] = {}
    silent_cases = []
    unnamed_cases = []
    errors: list[dict] = []

    seed = args.seed_start
    done = 0
    with tempfile.TemporaryDirectory(prefix="fuzz_run_") as tmp:
        workdir = Path(tmp)
        while True:
            if args.count and done >= args.count:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            names = forced_ops or _seed_cases(args.family, seed, args.max_ops)
            try:
                case = build_case(seed, names)
                if args.vision:
                    result = run_case_scanned(case, workdir, f"{args.family}_{seed}", dpi=args.dpi)
                else:
                    result = run_case(case, workdir, f"{args.family}_{seed}")
                outcome = classify(case, result)
            except Exception as error:  # a malformed mutation, not a silent CLEAN
                tally["ERROR"] = tally.get("ERROR", 0) + 1
                print(f"seed {seed} ERROR ops={names} {type(error).__name__}: {error}")
                if args.json:
                    errors.append({"seed": seed, "operators": list(names),
                                   "error": f"{type(error).__name__}: {error}"})
                done += 1
                seed += 1
                continue
            tally[outcome.category] = tally.get(outcome.category, 0) + 1
            if outcome.category == SILENT:
                silent_by_ops[names] = silent_by_ops.get(names, 0) + 1
                silent_cases.append({
                    "seed": seed,
                    "operators": list(names),
                    "defects": [asdict(d) for d in outcome.defects],
                    "rules": outcome.rules,
                    "read": outcome.read,
                    "expected": outcome.expected,
                })
            elif outcome.category == LOUD:
                loud_by_ops[names] = loud_by_ops.get(names, 0) + 1
            elif outcome.category == UNNAMED:
                unnamed_cases.append({
                    "seed": seed,
                    "operators": list(names),
                    "defects": [asdict(d) for d in outcome.defects],
                    "rules": outcome.rules,
                })
            if args.verbose:
                print(f"seed {seed} {outcome.category} ops={names} status={outcome.status}")
            done += 1
            seed += 1

    print(f"family={args.family} cases={done} seeds={args.seed_start}..{seed - 1}")
    for category in (SAFE, SILENT, LOUD, UNNAMED, "ERROR"):
        print(f"  {category}: {tally.get(category, 0)}")
    if silent_by_ops:
        print("SILENT by operator combination:")
        for names, count in sorted(silent_by_ops.items(), key=lambda item: -item[1]):
            print(f"  {count:3d}  {','.join(names) or '(none)'}")
    if args.verbose and loud_by_ops:
        print("LOUD by operator combination:")
        for names, count in sorted(loud_by_ops.items(), key=lambda item: -item[1]):
            print(f"  {count:3d}  {','.join(names) or '(none)'}")

    if args.json:
        Path(args.json).write_text(json.dumps({
            "family": args.family,
            "cases": done,
            "tally": tally,
            "silent": silent_cases,
            "unnamed": unnamed_cases,
            "errors": errors,
        }, indent=2, default=str), encoding="utf-8")

    return 1 if silent_cases else 0


if __name__ == "__main__":
    sys.exit(main())
