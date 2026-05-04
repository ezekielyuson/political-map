"""Typer CLI entrypoint.

The single ``pge`` command exposes subcommands grouped by concern. New
subcommands (e.g. ``ingest``) get attached as sub-Typer apps from their own
modules so this file stays small.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from pge.graph.db import DEFAULT_DB_PATH, GraphDB, init_db
from pge.resolution import individuals as resolve_individuals_mod
from pge.sources.congress import fetch as congress_fetch
from pge.sources.congress import ingest as congress_ingest
from pge.sources.fec import fetch as fec_fetch
from pge.sources.fec import ingest as fec_ingest
from pge.sources.lda import fetch as lda_fetch
from pge.sources.lda import ingest as lda_ingest

load_dotenv()  # picks up .env at CWD

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Political Graph Engine.")
db_app = typer.Typer(no_args_is_help=True, help="Database lifecycle and inspection.")
ingest_app = typer.Typer(no_args_is_help=True, help="Pull data from a source into the graph.")
app.add_typer(db_app, name="db")
app.add_typer(ingest_app, name="ingest")

PathOpt = Annotated[
    Path,
    typer.Option("--path", "-p", help="Path to the SQLite database file."),
]


@db_app.command("init")
def db_init(path: PathOpt = DEFAULT_DB_PATH) -> None:
    """Create the database file and apply the schema. Idempotent."""
    resolved = init_db(path)
    typer.echo(f"initialized {resolved}")


@db_app.command("stats")
def db_stats(path: PathOpt = DEFAULT_DB_PATH) -> None:
    """Print row counts grouped by node kind, edge kind, and evidence type."""
    if not path.exists():
        typer.echo(f"no database at {path}; run `pge db init` first", err=True)
        raise typer.Exit(code=1)
    with GraphDB.open(path) as db:
        stats = db.stats()
    typer.echo(json.dumps(stats, indent=2, sort_keys=True))


def _parse_since(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise typer.BadParameter("must be YYYY-MM-DD") from exc


@ingest_app.command("fec")
def ingest_fec(
    entity: Annotated[
        str,
        typer.Option(
            "--entity",
            "-e",
            help="Which FEC entity to pull: committees | candidates | contributions",
        ),
    ],
    since: Annotated[
        str | None,
        typer.Option("--since", help="ISO date (YYYY-MM-DD). Falls back to saved cursor."),
    ] = None,
    cycle: Annotated[
        int | None, typer.Option("--cycle", help="Limit to a single election cycle (e.g. 2024).")
    ] = None,
    committee_id: Annotated[
        str | None,
        typer.Option("--committee-id", help="(contributions only) limit to one recipient."),
    ] = None,
    max_pages: Annotated[
        int | None,
        typer.Option("--max-pages", help="Cap pages fetched (useful for sanity runs)."),
    ] = None,
    no_archive: Annotated[
        bool, typer.Option("--no-archive", help="Skip writing raw responses to raw/fec/.")
    ] = False,
    raw_root: Annotated[
        Path,
        typer.Option("--raw-root", help="Where to write raw FEC responses."),
    ] = fec_fetch.DEFAULT_RAW_ROOT,
    path: PathOpt = DEFAULT_DB_PATH,
) -> None:
    """Pull a slice of FEC data into the graph.

    Examples:
        pge ingest fec --entity committees --since 2024-01-01
        pge ingest fec --entity contributions --committee-id C00123456
    """
    if entity not in {"committees", "candidates", "contributions"}:
        raise typer.BadParameter("--entity must be committees | candidates | contributions")
    if not path.exists():
        typer.echo(f"no database at {path}; run `pge db init` first", err=True)
        raise typer.Exit(code=1)

    since_date = _parse_since(since)
    with GraphDB.open(path) as db:
        result = fec_ingest.ingest(
            db,
            entity=entity,  # type: ignore[arg-type]
            since=since_date,
            cycle=cycle,
            committee_id=committee_id,
            raw_root=raw_root,
            archive=not no_archive,
            max_pages=max_pages,
        )
    typer.echo(json.dumps(result, sort_keys=True))


@ingest_app.command("congress")
def ingest_congress(
    entity: Annotated[
        str,
        typer.Option(
            "--entity",
            "-e",
            help="Which Congress entity: members | committees | resolve | bootstrap",
        ),
    ],
    legislators_cache_dir: Annotated[
        Path,
        typer.Option(
            "--legislators-cache",
            help="Directory for the cached congress-legislators YAML.",
        ),
    ] = Path("data/ref"),
    include_historical: Annotated[
        bool,
        typer.Option("--historical", help="Also include legislators-historical.yaml."),
    ] = False,
    current_only: Annotated[
        bool,
        typer.Option(
            "--current-only/--all-members",
            help="(members only) restrict to currently-serving members.",
        ),
    ] = True,
    max_pages: Annotated[
        int | None,
        typer.Option("--max-pages", help="Cap pages fetched (sanity runs)."),
    ] = None,
    no_archive: Annotated[
        bool, typer.Option("--no-archive", help="Skip writing raw responses to raw/congress/.")
    ] = False,
    raw_root: Annotated[
        Path, typer.Option("--raw-root", help="Where to write raw Congress responses."),
    ] = congress_fetch.DEFAULT_RAW_ROOT,
    path: PathOpt = DEFAULT_DB_PATH,
) -> None:
    """Pull members / committees from Congress.gov, or run a resolve pass.

    Entities:

    * ``members``    -- API ingest of all current members + detail. Needs
                        ``CONGRESS_API_KEY``.
    * ``committees`` -- API ingest of all committees + per-committee
                        member rosters. Needs ``CONGRESS_API_KEY``.
    * ``resolve``    -- merge any FEC-keyed politicians into their
                        bioguide-keyed canonical nodes (no fetching).
    * ``bootstrap``  -- seed Politician nodes from the public
                        congress-legislators YAML. No API key required.
                        Useful for demos and at Docker build time.
    """
    if entity not in {"members", "committees", "resolve", "bootstrap"}:
        raise typer.BadParameter(
            "--entity must be members | committees | resolve | bootstrap"
        )
    if not path.exists():
        typer.echo(f"no database at {path}; run `pge db init` first", err=True)
        raise typer.Exit(code=1)

    with GraphDB.open(path) as db:
        result = congress_ingest.ingest(
            db,
            entity=entity,  # type: ignore[arg-type]
            legislators_cache_dir=legislators_cache_dir,
            include_historical=include_historical,
            raw_root=raw_root,
            archive=not no_archive,
            max_pages=max_pages,
            current_only=current_only,
        )
    typer.echo(json.dumps(result, sort_keys=True))


@ingest_app.command("lda")
def ingest_lda(
    year: Annotated[
        int | None,
        typer.Option("--year", "-y", help="Filing year (e.g. 2024)."),
    ] = None,
    period: Annotated[
        str | None,
        typer.Option(
            "--period",
            "-q",
            help="Filing period: q1 | q2 | q3 | q4 | h1 | h2.",
        ),
    ] = None,
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="Only filings posted on/after this ISO date. Falls back to saved cursor.",
        ),
    ] = None,
    max_pages: Annotated[
        int | None,
        typer.Option("--max-pages", help="Cap pages fetched (sanity runs)."),
    ] = None,
    no_archive: Annotated[
        bool, typer.Option("--no-archive", help="Skip writing raw responses to raw/lda/.")
    ] = False,
    raw_root: Annotated[
        Path, typer.Option("--raw-root", help="Where to write raw LDA responses."),
    ] = lda_fetch.DEFAULT_RAW_ROOT,
    path: PathOpt = DEFAULT_DB_PATH,
) -> None:
    """Pull a slice of LDA quarterly filings into the graph.

    Examples:
        pge ingest lda --year 2024 --period q2
        pge ingest lda --since 2024-07-01
    """
    if not path.exists():
        typer.echo(f"no database at {path}; run `pge db init` first", err=True)
        raise typer.Exit(code=1)

    with GraphDB.open(path) as db:
        result = lda_ingest.ingest(
            db,
            filing_year=year,
            filing_period=period,
            dt_posted_after=since,
            raw_root=raw_root,
            archive=not no_archive,
            max_pages=max_pages,
        )
    typer.echo(json.dumps(result, sort_keys=True))


resolve_app = typer.Typer(no_args_is_help=True, help="Entity resolution.")
ui_app = typer.Typer(no_args_is_help=True, help="Local human-in-the-loop UIs.")
app.add_typer(resolve_app, name="resolve")
app.add_typer(ui_app, name="ui")


@resolve_app.command("individuals")
def resolve_individuals_cmd(
    auto_merge_threshold: Annotated[
        float,
        typer.Option(
            "--auto-merge", help="Score >= this -> merge automatically. (0..1)"
        ),
    ] = 0.95,
    review_threshold: Annotated[
        float,
        typer.Option(
            "--review", help="Score >= this -> queue for human review. (0..1)"
        ),
    ] = 0.80,
    skip_review_apply: Annotated[
        bool,
        typer.Option(
            "--skip-review-apply",
            help="Don't process previously-accepted review_queue rows on this run.",
        ),
    ] = False,
    path: PathOpt = DEFAULT_DB_PATH,
) -> None:
    """Cluster ``Individual`` nodes; auto-merge confident matches, queue the rest."""
    if not path.exists():
        typer.echo(f"no database at {path}; run `pge db init` first", err=True)
        raise typer.Exit(code=1)
    with GraphDB.open(path) as db:
        summary = resolve_individuals_mod.resolve_individuals(
            db,
            auto_merge_threshold=auto_merge_threshold,
            review_threshold=review_threshold,
            apply_review_queue=not skip_review_apply,
        )
    typer.echo(
        json.dumps(
            {
                "auto_merged": summary.auto_merged,
                "queued_for_review": summary.queued_for_review,
                "skipped_already_decided": summary.skipped_already_decided,
            },
            sort_keys=True,
        )
    )


@ui_app.command("review")
def ui_review(
    port: Annotated[int, typer.Option("--port", help="Streamlit server port.")] = 8501,
    path: PathOpt = DEFAULT_DB_PATH,
) -> None:
    """Launch the Streamlit review UI for queued individual-resolution pairs."""
    import os
    import shutil
    import subprocess
    import sys

    if not path.exists():
        typer.echo(f"no database at {path}; run `pge db init` first", err=True)
        raise typer.Exit(code=1)

    streamlit = shutil.which("streamlit")
    cmd = (
        [sys.executable, "-m", "streamlit", "run"]
        if streamlit is None
        else [streamlit, "run"]
    )

    script = Path(__file__).parent / "ui" / "review.py"
    env = os.environ.copy()
    env["PGE_DB_PATH"] = str(path)
    subprocess.run(
        [*cmd, str(script), "--server.port", str(port)],
        env=env,
        check=False,
    )


@app.command("serve")
def serve(
    host: Annotated[str, typer.Option("--host", help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", "-P", help="Port to listen on.")] = 8000,
    reload: Annotated[bool, typer.Option("--reload", help="Auto-reload on code changes.")] = False,
    path: PathOpt = DEFAULT_DB_PATH,
) -> None:
    """Run the local-dev FastAPI server."""
    import os

    import uvicorn

    # The module-level ``app`` factory reads PGE_DB_PATH; set it for the child.
    os.environ["PGE_DB_PATH"] = str(path)
    uvicorn.run("pge.api:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
