"""Command line entry point.

Examples:
  instapost make reel --pillar gita --topic "गुस्सा" --vision-qa
  instapost make carousel --pillar kabir
  instapost make story --offline            # free test run with sample text and drawings
  instapost make reel --draft my_draft.json  # render a hand-written draft
  instapost budget
  instapost verses
"""

import argparse
import sys
from pathlib import Path

from instapost import budget
from instapost.config import get_settings
from instapost.models import Draft, Format, Pillar
from instapost.scriptures import load_verses

FORMATS = {"reel": Format.REEL_BOOK_POV, "carousel": Format.CAROUSEL_SHLOK,
           "story": Format.STORY_LINE}


def _make(args) -> int:
    from instapost import pipeline

    draft = None
    if args.draft:
        draft = Draft.model_validate_json(Path(args.draft).read_text(encoding="utf-8"))
    result = pipeline.make(
        FORMATS[args.format], Pillar(args.pillar), args.topic, offline=args.offline,
        draft=draft, image_provider=args.image_provider, vision_qa=args.vision_qa,
    )
    print(f"{result.status.upper()}  {result.item_id}")
    print(f"  folder:     {result.folder}")
    print(f"  fact check: {result.fact.verdict.value}")
    for issue in result.fact.issues:
        print(f"    - {issue}")
    for check in result.qa.checks:
        mark = "ok  " if check.passed else "FAIL"
        print(f"  [{mark}] {check.name}: {check.detail}")
    return 0 if result.status != "blocked" else 2


def _budget(_args) -> int:
    s = get_settings()
    print(f"today:      ${budget.spent('day'):.3f} of ${s.daily_budget_usd:.2f}")
    print(f"this month: ${budget.spent('month'):.3f} of ${s.monthly_budget_usd:.2f}")
    return 0


def _verses(_args) -> int:
    for v in load_verses().values():
        print(f"{v.review_status:9} {v.id:28} {v.label}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="instapost")
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("make", help="create one reel, carousel or story")
    make.add_argument("format", choices=FORMATS)
    make.add_argument("--pillar", choices=[p.value for p in Pillar], default="jeevan_sutra")
    make.add_argument("--topic", help="optional topic hint, in Hindi or English")
    make.add_argument("--offline", action="store_true",
                      help="no API calls: sample text and placeholder images")
    make.add_argument("--draft", help="path to a Draft JSON to render instead of writing one")
    make.add_argument("--image-provider", choices=["gemini", "openai", "placeholder"])
    make.add_argument("--vision-qa", action="store_true",
                      help="also check the final image with Claude (about $0.002)")
    make.set_defaults(func=_make)

    sub.add_parser("budget", help="show spend against the caps").set_defaults(func=_budget)
    sub.add_parser("verses", help="list the verified-verse database").set_defaults(func=_verses)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
