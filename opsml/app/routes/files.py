# pylint: disable=protected-access
import os

import streaming_form_data
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from starlette.requests import ClientDisconnect
from streaming_form_data import StreamingFormDataParser
from streaming_form_data.validators import MaxSizeValidator

from opsml.app.routes.pydantic_models import (
    DownloadFileRequest,
    ListFileRequest,
    ListFileResponse,
)
from opsml.app.routes.utils import (
    ExternalFileTarget,
    MaxBodySizeException,
    MaxBodySizeValidator,
)
from opsml.helpers.logging import ArtifactLogger

logger = ArtifactLogger.get_logger(__name__)

router = APIRouter()
CHUNK_SIZE = 31457280


MAX_FILE_SIZE = 1024 * 1024 * 1024 * 50  # = 50GB
MAX_REQUEST_BODY_SIZE = MAX_FILE_SIZE + 1024


# upload uses the request object directly which affects OpenAPI docs
@router.post("/upload", name="upload")
async def upload_file(request: Request):
    """Uploads files in chunks to storage destination"""

    filename = request.headers.get("Filename")
    write_path = request.headers.get("WritePath")
    body_validator = MaxBodySizeValidator(MAX_REQUEST_BODY_SIZE)

    if filename is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Filename header is missing",
        )

    if write_path is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No write path provided",
        )

    try:
        file_ = ExternalFileTarget(
            filename=filename,
            write_path=write_path,
            storage_client=request.app.state.storage_client,
            validator=MaxSizeValidator(MAX_FILE_SIZE),
        )
        parser = StreamingFormDataParser(headers=request.headers)
        parser.register("file", file_)

        async for chunk in request.stream():
            body_validator(chunk)
            parser.data_received(chunk)

    except ClientDisconnect:
        logger.error("Client disconnected")

    except MaxBodySizeException as error:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"""
               Maximum request body size limit ({MAX_REQUEST_BODY_SIZE}.
               Bytes exceeded ({error.body_len} bytes read)""",
        ) from error

    except streaming_form_data.validators.ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Maximum file size limit ({MAX_FILE_SIZE} bytes) exceeded",
        ) from error

    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"There was an error uploading the file. {error}",
        ) from error

    if not file_.multipart_filename:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="File is missing",
        )
    return {
        "storage_uri": os.path.join(write_path, filename),
    }


@router.post("/files/download", name="download_file")
def download_file(
    request: Request,
    payload: DownloadFileRequest,
) -> StreamingResponse:
    """Downloads a file

    Args:
        read_path (str):
            Path to read from

    Returns:
        Streaming file response
    """

    try:
        storage_client = request.app.state.storage_client
        return StreamingResponse(
            storage_client.iterfile(
                file_path=payload.read_path,
                chunk_size=CHUNK_SIZE,
            ),
            media_type="application/octet-stream",
        )

    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"There was an error downloading the file. {error}",
        ) from error


@router.post("/files/list", name="list_files")
def list_files(
    request: Request,
    payload: ListFileRequest,
) -> ListFileResponse:
    """Downloads a file

    Args:
        read_path (str):
            Path to search

    Returns:
        List of files
    """

    try:
        storage_client = request.app.state.storage_client
        files = storage_client.list_files(payload.read_path)
        return ListFileResponse(files=files)

    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"There was an error listing files. {error}",
        ) from error