from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from dotenv import load_dotenv

from trial_optimizer.clients.bright_data import BrightDataClient
from trial_optimizer.clients.clinical_trials_gov import ClinicalTrialsGovClient
from trial_optimizer.importers import (
    load_convoke_programs,
    load_convoke_rows,
    read_structured_records,
)
from trial_optimizer.normalization import parse_ctgov_study
from trial_optimizer.repository import Repository, init_database

app = typer.Typer(no_args_is_help=True, help="Trial Optimizer data ingestion commands")
DEFAULT_DATABASE_URL = "postgresql://trialopt:trialopt@localhost:5432/trialopt"
load_dotenv()


def _database_url(value: str | None) -> str:
    return value or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL


@app.command("init-db")
def init_db(
    database_url: Annotated[
        str | None, typer.Option(envvar="DATABASE_URL", help="PostgreSQL URL")
    ] = None,
) -> None:
    """Create or update the database schema."""
    init_database(_database_url(database_url))
    typer.echo("Database schema is ready.")


@app.command("ingest-ctgov")
def ingest_ctgov(
    nct_id: Annotated[
        list[str] | None, typer.Option("--nct-id", help="NCT ID; may be repeated")
    ] = None,
    query: Annotated[
        str | None, typer.Option(help="ClinicalTrials.gov query.term expression")
    ] = None,
    limit: Annotated[int, typer.Option(min=1, max=100_000)] = 100,
    database_url: Annotated[
        str | None, typer.Option(envvar="DATABASE_URL", help="PostgreSQL URL")
    ] = None,
) -> None:
    """Fetch current ClinicalTrials.gov API v2 records and preserve versioned snapshots."""
    if not nct_id and not query:
        raise typer.BadParameter("Provide at least one --nct-id or a --query")

    seen = 0
    inserted = 0
    with Repository.connect(_database_url(database_url)) as repository:
        run_id = repository.start_run(
            "clinicaltrials.gov", {"nct_ids": nct_id or [], "query": query, "limit": limit}
        )
        with ClinicalTrialsGovClient() as client:
            for identifier in nct_id or []:
                record = parse_ctgov_study(client.get_study(identifier))
                seen += 1
                inserted += int(repository.ingest_ctgov(record, ingestion_run_id=run_id))
                typer.echo(f"Ingested {record.nct_id}")
            if query:
                for study in client.search(query, limit=limit):
                    record = parse_ctgov_study(study)
                    seen += 1
                    inserted += int(repository.ingest_ctgov(record, ingestion_run_id=run_id))
                    typer.echo(f"Ingested {record.nct_id}")
        repository.finish_run(run_id, seen=seen, inserted=inserted)
    typer.echo(f"Complete: {seen} records observed, {inserted} new versions stored.")


@app.command("ingest-convoke")
def ingest_convoke(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    database_url: Annotated[
        str | None, typer.Option(envvar="DATABASE_URL", help="PostgreSQL URL")
    ] = None,
) -> None:
    """Import analog assertions through the Convoke CSV interchange contract."""
    rows = load_convoke_rows(path)
    inserted = 0
    with Repository.connect(_database_url(database_url)) as repository:
        run_id = repository.start_run(
            "convoke", {"file": path.name, "interchange_contract": "convoke-analog-csv-v1"}
        )
        for index, row in enumerate(rows, start=1):
            locator = f"{path.name}:{index}"
            inserted += int(
                repository.ingest_convoke_row(row, locator=locator, ingestion_run_id=run_id)
            )
        repository.finish_run(run_id, seen=len(rows), inserted=inserted)
    typer.echo(f"Complete: {len(rows)} analog links observed, {inserted} stored.")


@app.command("ingest-convoke-programs")
def ingest_convoke_programs(
    path: Annotated[
        Path | None,
        typer.Argument(exists=True, dir_okay=False, readable=True),
    ] = None,
    database_url: Annotated[
        str | None, typer.Option(envvar="DATABASE_URL", help="PostgreSQL URL")
    ] = None,
) -> None:
    """Import a Convoke MCP Program Tracker response as a timestamped landscape snapshot."""
    if path:
        paths = [path]
    else:
        snapshot_dir = Path(os.getenv("CONVOKE_SNAPSHOT_DIR", "data/convoke"))
        paths = sorted(snapshot_dir.glob("*.json"))
        if not paths:
            raise typer.BadParameter(
                f"No JSON snapshots found in {snapshot_dir}. Provide a path or update "
                "CONVOKE_SNAPSHOT_DIR."
            )

    snapshots = [(snapshot, *load_convoke_programs(snapshot)) for snapshot in paths]
    observed = sum(len(rows) for _, rows, _ in snapshots)
    inserted = 0
    with Repository.connect(_database_url(database_url)) as repository:
        run_id = repository.start_run(
            "convoke",
            {
                "files": [snapshot.name for snapshot in paths],
                "adapter": "convoke-program-tracker-v1",
            },
        )
        for snapshot, rows, entity_resolution in snapshots:
            for index, row in enumerate(rows, start=1):
                locator = (
                    f"program:{row.get('drug_id', row['drug_name'])}:"
                    f"{row.get('indication_id', row['indication_name'])}:"
                    f"{snapshot.name}:{index}"
                )
                inserted += int(
                    repository.ingest_convoke_program(
                        row,
                        entity_resolution=entity_resolution,
                        locator=locator,
                        ingestion_run_id=run_id,
                    )
                )
        repository.finish_run(run_id, seen=observed, inserted=inserted)
    typer.echo(f"Complete: {observed} programs observed, {inserted} snapshots stored.")


@app.command("ingest-web-snapshot")
def ingest_web_snapshot(
    path: Annotated[Path, typer.Argument(exists=True, dir_okay=False, readable=True)],
    source_system: Annotated[str, typer.Option(help="Authority/site represented by the snapshot")],
    snapshot_id: Annotated[
        str | None, typer.Option(help="Bright Data snapshot ID, if applicable")
    ] = None,
    database_url: Annotated[
        str | None, typer.Option(envvar="DATABASE_URL", help="PostgreSQL URL")
    ] = None,
) -> None:
    """Land a public-web JSON/JSONL/NDJSON/CSV snapshot without making claims from it."""
    seen = 0
    inserted = 0
    with Repository.connect(_database_url(database_url)) as repository:
        run_id = repository.start_run(
            source_system, {"file": path.name, "bright_data_snapshot_id": snapshot_id}
        )
        for index, row in enumerate(read_structured_records(path), start=1):
            locator = str(
                row.get("url") or row.get("source_url") or row.get("id") or f"{path.name}:{index}"
            )
            inserted += int(
                repository.ingest_web_record(
                    row,
                    source_system=source_system,
                    locator=locator,
                    ingestion_run_id=run_id,
                    metadata={"bright_data_snapshot_id": snapshot_id} if snapshot_id else {},
                )
            )
            seen += 1
        repository.finish_run(run_id, seen=seen, inserted=inserted)
    typer.echo(f"Complete: {seen} public-web records observed, {inserted} new documents stored.")


@app.command("download-brightdata")
def download_brightdata(
    snapshot_id: Annotated[str, typer.Argument()],
    output: Annotated[Path, typer.Argument(dir_okay=False)],
    output_format: Annotated[
        str, typer.Option("--format", help="json, jsonl, ndjson, or csv")
    ] = "jsonl",
) -> None:
    """Download a completed Bright Data snapshot without exposing the API token."""
    output.parent.mkdir(parents=True, exist_ok=True)
    with BrightDataClient() as client:
        content = client.download_snapshot(snapshot_id, output_format=output_format)
    output.write_bytes(content)
    typer.echo(f"Downloaded snapshot {snapshot_id} to {output}")


@app.command("web")
def web(
    host: Annotated[str, typer.Option(envvar="TRIALOPT_HOST", help="Host address")] = "127.0.0.1",
    port: Annotated[
        int, typer.Option(envvar="TRIALOPT_PORT", min=1, max=65_535, help="Port number")
    ] = 8000,
    reload: Annotated[bool, typer.Option(help="Reload when source files change")] = False,
) -> None:
    """Start the Trial Optimizer dashboard."""
    uvicorn.run("trial_optimizer.web:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
