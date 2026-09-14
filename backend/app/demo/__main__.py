"""``python -m app.demo`` — write or seed the demo estate.

Two modes, and the difference matters:

* ``--out DIR`` writes the transcripts as JSON and touches nothing. Use it to inspect what
  the estate contains, to diff two profiles, or to hand a collector author a payload.
* ``--api-url URL`` posts them through the ingestion API. Use it to fill a development
  database.

Credentials are never read from a file or a prompt. Pass ``--collector-key`` (the normal
credential for ingestion), ``--token`` (an administrator's bearer token), or
``--dev-login ACCOUNT`` against a deployment running in development mode, which obtains one
from ``/auth/dev/login``. A deployment in OIDC mode has no such route, so the third option
simply fails there rather than offering a way in.

Seeding is idempotent: the run ids are derived from the estate, so running this twice
leaves one estate behind. ``--fresh-run-ids`` is the opt-out, for building a run history.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from typing import Any

from app.demo.estate import FEATURES, PROFILES, build_estate
from app.demo.seed import SeedingFailed, seed_transcripts
from app.demo.transcripts import build_transcripts, restamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.demo",
        description="Generate or seed the ADG demo estate (AD + SMB + NTFS).",
    )
    parser.add_argument(
        "--profile",
        default="standard",
        choices=sorted(PROFILES),
        help="How large the estate is. The feature set is the same in all of them.",
    )
    parser.add_argument(
        "--out",
        type=pathlib.Path,
        help="Write the transcripts as JSON into this directory instead of posting them.",
    )
    parser.add_argument(
        "--api-url",
        help="Post the transcripts to this ADG API, for example http://localhost:8000.",
    )
    parser.add_argument("--token", help="Bearer token for an account with collectors:ingest.")
    parser.add_argument("--collector-key", help="A value from ADG_COLLECTOR_API_KEYS.")
    parser.add_argument(
        "--dev-login",
        metavar="ACCOUNT",
        help="Obtain a token from /auth/dev/login. Development deployments only.",
    )
    parser.add_argument(
        "--fresh-run-ids",
        action="store_true",
        help="Post under new run ids, adding to the run history instead of replaying.",
    )
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="Per-request timeout in seconds."
    )
    parser.add_argument(
        "--features",
        action="store_true",
        help="List what the estate deliberately contains, and exit.",
    )
    return parser


def _write(directory: pathlib.Path, transcripts: Any) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    for index, transcript in enumerate(transcripts, start=1):
        path = directory / f"{index:02d}-{transcript.name}.json"
        path.write_text(
            json.dumps(transcript.as_document(), indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
        print(f"{path}  ({transcript.observation_count} observations, {transcript.status})")
    return 0


async def _seed(args: argparse.Namespace, transcripts: Any) -> int:
    try:
        import httpx
    except ModuleNotFoundError:  # pragma: no cover - httpx is a dev dependency
        print(
            "Posting requires httpx. Install the dev extra: pip install -e 'backend[dev]'.",
            file=sys.stderr,
        )
        return 2

    headers: dict[str, str] = {}
    async with httpx.AsyncClient(base_url=args.api_url.rstrip("/"), timeout=args.timeout) as client:
        if args.dev_login:
            response = await client.post("/auth/dev/login", json={"username": args.dev_login})
            if response.status_code != 200:
                print(
                    f"Development login as {args.dev_login!r} failed with HTTP "
                    f"{response.status_code}: {response.text[:300]}",
                    file=sys.stderr,
                )
                return 1
            headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        elif args.token:
            headers["Authorization"] = f"Bearer {args.token}"
        elif args.collector_key:
            headers["X-ADG-Collector-Key"] = args.collector_key
        else:
            print(
                "Posting needs a credential: --collector-key, --token, or --dev-login.",
                file=sys.stderr,
            )
            return 2
        client.headers.update(headers)

        try:
            report = await seed_transcripts(client, transcripts)
        except SeedingFailed as failure:
            print(str(failure), file=sys.stderr)
            return 1
        except httpx.HTTPError as failure:
            print(f"The ADG API at {args.api_url} could not be reached: {failure}", file=sys.stderr)
            return 1

    print(report.summary())
    if report.replayed:
        print(
            "\nNothing was applied: this estate is already seeded. Use --fresh-run-ids to "
            "post it again as a new set of runs."
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.features:
        for feature in FEATURES:
            print(f"{feature.name}\n    where: {feature.where}\n    why:   {feature.why}")
        return 0

    if bool(args.out) == bool(args.api_url):
        print("Choose exactly one of --out (write files) or --api-url (post).", file=sys.stderr)
        return 2

    estate = build_estate(args.profile)
    transcripts = build_transcripts(estate)
    if args.fresh_run_ids:
        transcripts = tuple(restamp(transcript) for transcript in transcripts)

    print(
        f"Demo estate '{args.profile}': {len(estate.principals)} principals, "
        f"{len(estate.edges)} memberships, {len(estate.servers)} servers, "
        f"{len(estate.shares)} shares, {len(estate.directories)} directories, "
        f"{estate.object_count} objects in {len(transcripts)} scan runs.",
        file=sys.stderr,
    )

    if args.out:
        return _write(args.out, transcripts)
    return asyncio.run(_seed(args, transcripts))


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
