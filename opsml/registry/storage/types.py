import os
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Any, Generator, List, Optional, Protocol, Tuple, Union

from pydantic import BaseModel

from opsml.helpers.request_helpers import ApiClient

FilePath = Union[List[str], str]


class ArtifactStorageType(str, Enum):
    DATAFRAME = "DataFrame"
    ARROW_TABLE = "Table"
    NDARRAY = "ndarray"
    TF_MODEL = "keras"
    PYTORCH = "pytorch"
    JSON = "json"
    BOOSTER = "booster"
    ONNX = "onnx"


ARTIFACT_TYPES = list(ArtifactStorageType)


class StorageClientSettings(BaseModel):
    storage_type: str = "local"
    storage_uri: str = os.path.expanduser("~")


class GcsStorageClientSettings(StorageClientSettings):
    storage_type: str = "gcs"
    credentials: Optional[Any] = None
    gcp_project: Optional[str] = None


class ApiStorageClientSettings(StorageClientSettings):
    client: Optional[ApiClient] = None

    class Config:
        allow_mutation = True
        arbitrary_types_allowed = True

    @property
    def api_client(self) -> ApiClient:
        if self.client is not None:
            return self.client
        raise ValueError("ApiClient has not been set")


StorageSettings = Union[
    StorageClientSettings,
    GcsStorageClientSettings,
    ApiStorageClientSettings,
]


class ArtifactStorageSpecs(BaseModel):
    save_path: str
    filename: Optional[str] = None

    class Config:
        allow_mutation = True
        extra = "allow"


class StorageClientProto(Protocol):
    backend: str
    client: Any
    base_path_prefix: str
    _storage_spec: Any

    @property
    def storage_spec(self) -> ArtifactStorageSpecs:
        "storage metadata"

    @storage_spec.setter
    def storage_spec(self, artifact_storage_spec):
        "storage metadata"

    def create_save_path(
        self,
        file_suffix: Optional[str] = None,
    ) -> Tuple[str, str]:
        "Creates a save path"

    def create_tmp_path(
        self,
        tmp_dir: str,
        file_suffix: Optional[str] = None,
    ):
        """Temp path"""

    @contextmanager
    def create_temp_save_path(
        self,
        file_suffix: Optional[str],
    ) -> Generator[Tuple[Any, Any], None, None]:
        """Context manager temp save path"""

    def list_files(self, storage_uri: str) -> List[str]:
        """List files"""

    def store(self, storage_uri: Union[List[str], str]) -> Any:
        """store"""

    def upload(self, local_path: str, write_path: str, recursive: bool = False, **kwargs) -> None:
        """Upload"""

    def post_process(self, storage_uri: str) -> str:
        """post process"""

    @staticmethod
    def validate(storage_backend: str) -> bool:
        """Validate"""


class MlFlowClientProto(Protocol):
    def log_artifact(self, run_id: str, local_path: str, artifact_path: str):
        "log artifact"


@dataclass
class MlflowInfo:
    local_path: str
    artifact_path: str
    filename: str
    model: Optional[Any] = None
    model_type: Optional[str] = None