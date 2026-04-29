"""App for apptainer related operations."""

import asyncio
from pathlib import Path
from typing import Annotated, Optional

import typer

from builder.config import attach_settings
from builder.context import CliContext
from builder.exceptions import handle_abort
from builder.format import terminal_message
from builder.subapps.helpers import (
    build_image,
    check_existing_paths,
    check_sif_exists,
    find_job_scripts,
    load_job_script_metadata,
    publish_image,
    run_tasks_concurrently,
)

app = typer.Typer()


@app.command(name="build")
@handle_abort
def build(
    job_scripts: Annotated[
        Optional[list[Path]],
        typer.Argument(
            ..., help="Paths of the job scripts to build. If None, all job scripts will be built."
        ),
    ] = None,
    dry_run: bool = typer.Option(False, help="Do not build the images, only print the commands."),
):
    """Build an Apptainer .sif file from a Dockerfile for each job script imputed."""
    job_scripts = find_job_scripts(job_scripts)
    check_existing_paths(job_scripts)
    tasks = [build_image(job_script_path, dry_run) for job_script_path in job_scripts]
    asyncio.run(run_tasks_concurrently(tasks))
    terminal_message("Built Apptainer images successfully", "Process Complete")


@app.command(name="publish")
@handle_abort
@attach_settings
def publish(
    ctx: typer.Context,
    job_scripts: Annotated[
        Optional[list[Path]],
        typer.Argument(
            ..., help="Paths of the job scripts to publish. If None, all job scripts will be published."
        ),
    ] = None,
    dry_run: bool = typer.Option(False, help="Do not publish the images, only print the commands."),
):
    """Publish the built Apptainer .sif files for each job script supplied."""
    ctx_obj = ctx.obj
    assert isinstance(ctx_obj, CliContext)
    settings = ctx_obj.settings
    assert settings is not None

    job_scripts = find_job_scripts(job_scripts)

    image_backed_job_scripts = [
        path for path in job_scripts if load_job_script_metadata(path).image_source is not None
    ]
    check_sif_exists(image_backed_job_scripts)
    tasks = [
        publish_image(job_script_path, settings, dry_run, ctx_obj.verbose) for job_script_path in job_scripts
    ]
    asyncio.run(run_tasks_concurrently(tasks))
    terminal_message("Published Apptainer images successfully", "Process Complete")
