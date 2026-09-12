"""Quick, runnable tests for the search module.

Run with:
    python -m tests.test_search              # runs everything
    python -m tests.test_search text         # text search only
    python -m tests.test_search image        # image search only
    python -m tests.test_search instant      # instant-answer API only
    python -m tests.test_search html         # HTML-scrape results only

These don't touch Discord — they call the underlying helpers directly
and pretty-print the raw data so you can eyeball what the bot would see.
"""

import asyncio
import json
import sys

from search import (
    _ddg_instant_answer,
    _ddg_html_results,
    _ddg_image_results,
    _text_results_embed,
    _image_embed,
)


QUERY = "python asyncio"


def _hr(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


async def test_instant() -> None:
    _hr(f"Instant answer API → {QUERY!r}")
    data = await _ddg_instant_answer(QUERY)
    # Print just the fields we actually use in the embed builder.
    interesting = {
        k: data.get(k)
        for k in (
            "AbstractText",
            "AbstractSource",
            "Answer",
            "AnswerType",
            "Heading",
            "Definition",
        )
    }
    print(json.dumps(interesting, indent=2, ensure_ascii=False))


async def test_html() -> None:
    _hr(f"HTML results scrape → {QUERY!r}")
    results = await _ddg_html_results(QUERY, limit=5)
    if not results:
        print("(no results — DuckDuckGo may be rate-limiting or blocking)")
        return
    for i, r in enumerate(results, 1):
        print(f"\n[{i}] {r['title']}")
        print(f"    {r['url']}")
        if r["snippet"]:
            print(f"    {r['snippet'][:160]}")


async def test_images() -> None:
    _hr(f"Image results → {QUERY!r}")
    results = await _ddg_image_results(QUERY, limit=4)
    if not results:
        print("(no results — check that `ddgs` is installed and network is up)")
        return
    for i, r in enumerate(results, 1):
        print(f"\n[{i}] {r['title']}")
        print(f"    image     : {r['image']}")
        print(f"    thumbnail : {r['thumbnail']}")
        print(f"    page      : {r['url']}")


async def test_text_embed() -> None:
    """Build the full text embed and print its rendered fields."""
    _hr(f"Full text embed → {QUERY!r}")
    instant, results = await asyncio.gather(
        _ddg_instant_answer(QUERY),
        _ddg_html_results(QUERY),
    )
    embed = _text_results_embed(QUERY, instant, results)
    _print_embed(embed)


async def test_image_embed() -> None:
    _hr(f"Full image embed → {QUERY!r}")
    results = await _ddg_image_results(QUERY)
    embed = _image_embed(QUERY, results)
    _print_embed(embed)


def _print_embed(embed) -> None:
    print(f"title      : {embed.title}")
    print(f"description: {embed.description}")
    print(f"image      : {embed.image.url if embed.image else None}")
    print(f"footer     : {embed.footer.text if embed.footer else None}")
    for f in embed.fields:
        print(f"\n-- field: {f.name}")
        print(f.value)


async def main() -> None:
    which = sys.argv[1] if len(sys.argv) > 1 else "all"

    runners = {
        "instant": test_instant,
        "html": test_html,
        "image": test_images,
        "text": test_text_embed,
        "text-embed": test_text_embed,
        "image-embed": test_image_embed,
    }

    if which == "all":
        order = [
            # test_instant, # Returns empty data for some reason, so skip it for now.
            test_html,
            test_images,
            test_text_embed,
            # test_image_embed, # DDGSException: RequestError: RequestError('error sending request for url (https://duckduckgo.com/?q=python+asyncio) > user error: malformed headers')
        ]
    elif which in runners:
        order = [runners[which]]
    else:
        print(f"Unknown test {which!r}. Choices: all, {', '.join(runners)}")
        return

    for fn in order:
        try:
            await fn()
        except Exception as e:
            print(f"\n[FAILED] {fn.__name__}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())