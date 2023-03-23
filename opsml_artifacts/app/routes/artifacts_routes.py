from typing import Union

from fastapi import APIRouter, BackgroundTasks, Body, Request
from fastapi.responses import StreamingResponse

from opsml_artifacts import CardRegistry
from opsml_artifacts.app.core.config import config
from opsml_artifacts.app.routes.models import (
    AddRecordRequest,
    AddRecordResponse,
    DownloadModelRequest,
    ListRequest,
    ListResponse,
    StorageSettingsResponse,
    UidExistsRequest,
    UidExistsResponse,
    UpdateRecordRequest,
    UpdateRecordResponse,
    VersionRequest,
    VersionResponse,
)
from opsml_artifacts.app.routes.utils import (
    MODEL_FILE,
    ModelDownloader,
    delete_dir,
    iterfile,
)
from opsml_artifacts.helpers.logging import ArtifactLogger

logger = ArtifactLogger.get_logger(__name__)

router = APIRouter()
CHUNK_SIZE = 31457280  # 30 chunks


@router.get("/settings", response_model=StorageSettingsResponse, name="settings")
def get_storage_settings() -> StorageSettingsResponse:
    """Returns backend storage path and type"""

    if bool(config.STORAGE_URI):

        # TODO (steven) - Think of a different way to do this in the future
        # do we need to return anything if using proxy for both registration and storage?

        if not config.is_proxy:

            if "gs://" in config.STORAGE_URI:
                return StorageSettingsResponse(
                    storage_type="gcs",
                    storage_uri=config.STORAGE_URI,
                )

    return StorageSettingsResponse(
        storage_type="local",
        storage_uri=config.STORAGE_URI,
        proxy=config.is_proxy,
    )


@router.post("/check_uid", response_model=UidExistsResponse, name="check_uid")
def check_uid(
    request: Request,
    payload: UidExistsRequest = Body(...),
) -> UidExistsResponse:

    """Checks if a uid already exists in the database"""
    table_for_registry = payload.table_name.split("_")[1].lower()
    registry: CardRegistry = getattr(request.app.state.registries, table_for_registry)

    if registry.registry.check_uid(
        uid=payload.uid,
        table_to_check=payload.table_name,
    ):
        return UidExistsResponse(uid_exists=True)
    return UidExistsResponse(uid_exists=False)


@router.post("/version", response_model=Union[VersionResponse, UidExistsResponse], name="version")
def set_version(
    request: Request,
    payload: VersionRequest = Body(...),
) -> Union[VersionResponse, UidExistsResponse]:

    """Sets the version for an artifact card"""
    table_for_registry = payload.table_name.split("_")[1].lower()
    registry: CardRegistry = getattr(request.app.state.registries, table_for_registry)

    version = registry.registry.set_version(
        name=payload.name,
        team=payload.team,
        version_type=payload.version_type,
    )

    return VersionResponse(version=version)


@router.post("/list", response_model=ListResponse, name="list")
def list_cards(
    request: Request,
    payload: ListRequest = Body(...),
) -> ListResponse:

    """Sets the version for an artifact card. It also checks if a uid already exists"""
    table_for_registry = payload.table_name.split("_")[1].lower()
    registry: CardRegistry = getattr(request.app.state.registries, table_for_registry)

    dataframe = registry.list_cards(
        uid=payload.uid,
        name=payload.name,
        team=payload.team,
        version=payload.version,
    )

    records = dataframe.to_dict("records")

    return ListResponse(records=records)


@router.post("/create", response_model=AddRecordResponse, name="create")
def add_record(
    request: Request,
    payload: AddRecordRequest = Body(...),
) -> AddRecordResponse:

    """Sets the version for an artifact card. It also checks if a uid already exists"""
    table_for_registry = payload.table_name.split("_")[1].lower()
    registry: CardRegistry = getattr(request.app.state.registries, table_for_registry)

    registry.registry.add_and_commit(record=payload.record)

    return AddRecordResponse(registered=True)


@router.post("/update", response_model=UpdateRecordResponse, name="update")
def update_record(
    request: Request,
    payload: UpdateRecordRequest = Body(...),
) -> UpdateRecordResponse:

    """Updates a specific artifact card"""
    table_for_registry = payload.table_name.split("_")[1].lower()
    registry: CardRegistry = getattr(request.app.state.registries, table_for_registry)

    registry.registry.update_record(record=payload.record)

    return UpdateRecordResponse(updated=True)


@router.post("/download", name="download")
def download_model(
    request: Request,
    background_tasks: BackgroundTasks,
    payload: DownloadModelRequest,
) -> StreamingResponse:

    """Downloads a Model API definition

    Args: (Query Params)
        name (str): Optional name of model
        version (str): Optional semVar version of model
        team (str): Optional team name
        uid (str): Optional uid of ModelCard

    Returns:
        FileResponse object containing model definition json
    """

    registry: CardRegistry = getattr(request.app.state.registries, "model")

    loader = ModelDownloader(
        registry=registry,
        model_info=payload,
        config=config,
    )
    loader.download_model()
    background_tasks.add_task(delete_dir, dir_path=loader.base_path)

    headers = {"Content-Disposition": f'attachment; filename="{MODEL_FILE}"'}
    return StreamingResponse(
        iterfile(
            file_path=loader.file_path,
            chunk_size=CHUNK_SIZE,
        ),
        media_type="application/octet-stream",
        headers=headers,
    )