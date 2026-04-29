"""Core module for defining auxiliary functions of the commands."""

import asyncio
import base64
from pathlib import Path
from typing import Any, AsyncGenerator, Coroutine, Iterable

import boto3
import docker
import yaml
from loguru import logger
from mypy_boto3_ecr_public.client import ECRPublicClient
from mypy_boto3_s3.client import S3Client

from builder.config import Settings
from builder.exceptions import Abort
from builder.format import render_json, terminal_message
from builder.schemas import JobScriptMetadata
from builder.tools import run_command, run_command_logged


def check_existing_paths(paths: list[Path]) -> bool:
    """Check if the given paths exist."""
    with Abort.check_expressions(
        f"Some of the paths in {[path.name for path in paths]} do not exist",
        raise_kwargs={
            "subject": "Path does not exist",
            "log_message": "Some of the paths do not exist",
        },
    ) as checker:
        for path in paths:
            checker(path.exists(), f"{path.name} does not exist")
    return True


def check_sif_exists(paths: list[Path]) -> bool:
    """Check if the given parent paths have a child .sif file in them."""
    return check_existing_paths([path.joinpath("output.sif") for path in paths])


def check_metadata_exists(paths: list[Path]) -> bool:
    """Check if the given parent paths have a child metadata.yaml file in them."""
    return check_existing_paths([path.joinpath("metadata.yaml") for path in paths])


def find_job_scripts(paths: list[Path] | None = None) -> list[Path]:
    """Check if the input is None. If so, return a list containing all job scripts."""
    if paths is None:
        # search of folder in the current execution directory
        # and return the ones with a metadata.yaml file
        paths = list(Path(".").glob("*"))
        paths = [path for path in paths if path.joinpath("metadata.yaml").exists()]
    return paths


def load_job_script_metadata(job_script_path: Path) -> JobScriptMetadata:
    """Load the metadata.yaml file from a job script."""
    with open(job_script_path / "metadata.yaml") as metadata_file:
        metadata_dict = yaml.safe_load(metadata_file)
        metadata = JobScriptMetadata(**metadata_dict)
    return metadata


async def run_tasks_concurrently(tasks: list[Coroutine[Any, Any, Any]]):
    """Run tasks concurrently."""
    await asyncio.gather(*tasks)


async def create_async_generator(iterable: Iterable[Any]) -> AsyncGenerator:
    """Create an async generator from an iterable."""
    for item in iterable:
        yield item


async def build_image(job_script_path: Path, dry_run: bool = False):
    """Build an Apptainer image from a Dockerfile."""
    metadata = load_job_script_metadata(job_script_path)
    if metadata.image_source is None:
        skip_message = f"No image source defined for {job_script_path.name}. Skipping the build process"
        logger.debug(skip_message)
        terminal_message(skip_message, "Build Skipped")
        return

    with Abort.handle_errors(
        "Failed to build Docker image",
        raise_kwargs={
            "subject": "Build error",
            "log_message": f"Failed to build Docker image from {job_script_path}",
        },
    ):
        logger.debug(f"Building local docker image from {job_script_path}")
        docker_client = docker.from_env()
        tag = f"{job_script_path.name}:latest"
        if not dry_run:
            docker_client.images.build(
                path=str(job_script_path),
                tag=tag,
                rm=True,
            )
        docker_image_source = f"docker-daemon://{tag}"
        logger.debug(f"Built local docker image {tag}")

    with Abort.handle_errors(
        "Failed to build Apptainer image",
        raise_kwargs={
            "subject": "Build error",
            "log_message": f"Failed to build Apptainer image from {job_script_path}",
        },
    ):
        logger.debug(f"Building Apptainer image from {docker_image_source}")
        output_path = job_script_path / "output.sif"
        if not dry_run:
            command = f"apptainer build {output_path} {docker_image_source}"
            run_command_logged(command)
    final_message = f"Built Apptainer image {output_path}"
    logger.debug(final_message)
    terminal_message(final_message, "Image Built Successfully")


async def publish_image(
    job_script_path: Path, settings: Settings, dry_run: bool = False, verbose: bool = False
):
    """Publish an Apptainer image to a remote registry."""
    logger.debug(f"Loading metadata.yaml from {job_script_path}")
    metadata = load_job_script_metadata(job_script_path)
    logger.debug("Metadata loaded successfully:")
    if verbose:
        render_json(metadata.model_dump(mode="json"))

    logger.debug("Examining the metadata to identify the image source")
    image_source = metadata.image_source

    logger.debug(f"Using {image_source=} as the image source")
    if image_source is None:
        logger.debug("No image source defined. Skipping the publish process")
        terminal_message("No image source defined. Skipping the publish process", "Publish Skipped")
        return
    elif image_source == "Dockerfile":
        logger.debug("The image source is 'Dockerfile'. Starting the publish process")
        image_name = job_script_path.stem
        logger.debug(f"Using {image_name=} as the image name")

        logger.debug("Getting ECR client for region us-east-1")
        ecr: ECRPublicClient = boto3.client(
            "ecr-public",
            region_name="us-east-1",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_session_token=settings.aws_session_token,
        )

        logger.debug("Fetching authorization token and extracting username and password")
        auth_token_response = ecr.get_authorization_token()
        token = auth_token_response["authorizationData"]["authorizationToken"]
        (username, password) = base64.b64decode(token).decode().split(":")
        logger.debug(f"Got authorization token for user {username}")

        logger.debug("Describing registries on ECR Public")
        registry_response = ecr.describe_registries()
        registry_list = registry_response["registries"]
        Abort.require_condition(
            len(registry_list) == 1,
            "Did not find one and only one registry",
            raise_kwargs=dict(
                subject="Publish failed",
                log_message=f"Found {len(registry_list)} registries instead of one",
            ),
        )
        registry_data = registry_list[0]
        registry_id = registry_data["registryId"]
        registry_uri = registry_data["registryUri"]
        registry_domain = registry_uri.split("/")[0]

        try:
            ecr.get_repository_catalog_data(
                registryId=registry_id,
                repositoryName=image_name,
            )
        except ecr.exceptions.RepositoryNotFoundException:
            logger.warning(f"Repository {image_name} not found. Creating it")
            if not dry_run:
                ecr.create_repository(repositoryName=image_name)

        logger.debug("Logging into the ECR Public registry via Apptainer")
        command = (
            "apptainer registry login "
            f"--username={username} "
            f"--password={password} "
            f"oras://{registry_domain}"
        )
        run_command(command)

        logger.debug("Fetching image tags from the metadata")
        tags = metadata.image_tags
        # if no tag is defined, use "latest"
        # if a tag is present, make sure it has the latest as well
        if tags is None or len(tags) == 0:
            tags = ["latest"]
            logger.debug(f"No tag defined for the image {image_name=}, using {tags=}")
        elif "latest" not in tags:
            tags = list(set(tags + ["latest"]))
            logger.debug("Added the 'latest' tag to the image tags")
        else:
            logger.debug(f"Using tags {tags=} for the image {image_name=}")

        async for tag in create_async_generator(tags):
            logger.debug(f"Publishing Apptainer image from {job_script_path}")
            publish_url = f"oras://{registry_uri}/{image_name}:{tag}"
            output_path = job_script_path / "output.sif"
            if not dry_run:
                command = f"apptainer push {output_path} {publish_url}"
                run_command_logged(command)
            logger.debug(f"Published Apptainer image {output_path} to {registry_uri}/{image_name}:{tag}")
    else:
        logger.debug("The image source is an external registry. Skipping the publish process")
        terminal_message(
            "The image source is an external registry. Skipping the publish process", "Publish Skipped"
        )


async def publish_files(job_script_path: Path, settings: Settings, dry_run: bool = False):
    """Publish the auxiliary files for a job script to a remote S3 bucket."""
    logger.debug(f"Loading metadata.yaml from {job_script_path}")
    metadata = load_job_script_metadata(job_script_path)
    logger.debug("Metadata loaded successfully:")
    render_json(metadata.model_dump(mode="json"))

    logger.debug("Examining the metadata to identify the files to publish")
    files_paths = [metadata.entrypoint]
    if metadata.supporting_files:
        files_paths.extend(metadata.supporting_files)
    logger.debug(f"Using {[path.name for path in files_paths]} as the files to publish")

    if not all(job_script_path.joinpath(path).exists() for path in files_paths):
        raise Abort(
            f"Some of the files in {[path.name for path in files_paths]} do not exist",
            subject="File does not exist",
            log_message="Some of the files do not exist",
        )

    async for file_path in create_async_generator(files_paths):
        logger.debug(f"Publishing {file_path} to the bucket {settings.s3_bucket}")
        s3: S3Client = boto3.client(
            "s3",
            region_name=settings.s3_bucket_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            aws_session_token=settings.aws_session_token,
        )
        if not dry_run:
            s3.upload_file(
                Filename=str(job_script_path / file_path),
                Bucket=settings.s3_bucket,
                Key=f"files/{job_script_path.name}/{file_path}",
            )
        logger.debug(
            f"Published {file_path} to the bucket s3://{settings.s3_bucket}/files/{job_script_path.name}"
        )
