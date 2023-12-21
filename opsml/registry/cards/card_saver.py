# Copyright (c) Shipt, Inc.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
from functools import cached_property
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union, cast

import pyarrow as pa
from numpy.typing import NDArray

from opsml.registry.cards.audit import AuditCard
from opsml.registry.cards.base import ArtifactCard
from opsml.registry.cards.data import DataCard
from opsml.registry.cards.model import ModelCard
from opsml.registry.cards.pipeline import PipelineCard
from opsml.registry.cards.project import ProjectCard
from opsml.registry.cards.run import RunCard
from opsml.registry.data.formatter import DataFormatter
from opsml.registry.image.dataset import ImageDataset
from opsml.registry.model.interfaces import SUPPORTED_MODELS, HuggingFaceModel
from opsml.registry.model.metadata_creator import _TrainedModelMetadataCreator
from opsml.registry.model.model_converters import _OnnxModelConverter
from opsml.registry.storage.artifact import save_artifact_to_storage
from opsml.registry.storage.client import StorageClientType
from opsml.registry.types import (
    AllowedDataType,
    ArrowTable,
    ArtifactStorageSpecs,
    ArtifactStorageType,
    CardType,
    HuggingFaceStorageArtifact,
    ModelMetadata,
    OnnxAttr,
    SaveName,
    StoragePath,
    UriNames,
    ValidSavedSample,
)


class CardArtifactSaver:
    def __init__(self, card: ArtifactCard, storage_client: StorageClientType):
        """
        Parent class for saving artifacts belonging to cards

        Args:
            card:
                ArtifactCard with artifacts to save
            card_storage_info:
                Extra info to use with artifact storage
        """

        self._card = card
        self.storage_client = storage_client
        self.uris: Dict[str, str] = {}  # holder for card uris

    @cached_property
    def card(self) -> ArtifactCard:
        return self.card

    def save_artifacts(self) -> Tuple[Any, Any]:
        raise NotImplementedError

    def _get_storage_spec(self, filename: str, uri: Optional[str] = None) -> ArtifactStorageSpecs:
        """
        Gets storage spec for saving

        Args:
            uri:
                Base URI to write the file to
            filename:
                Name of file

        """
        if uri is None:
            return ArtifactStorageSpecs(save_path=str(self.card.uri), filename=filename)

        return ArtifactStorageSpecs(save_path=self._resolve_dir(uri), filename=filename)

    def _resolve_dir(self, uri: str) -> str:
        """
        Resolve a file dir uri for card updates

        Args:
            uri:
                path to file
        Returns
            Resolved uri *directory* relative to the card.
        """
        base_path = Path(self.storage_client.base_path_prefix)
        uri_path = Path(uri).parent
        return str(uri_path.relative_to(base_path))

    @staticmethod
    def validate(card_type: str) -> bool:
        raise NotImplementedError


class DataCardArtifactSaver(CardArtifactSaver):
    @cached_property
    def card(self) -> DataCard:
        return cast(DataCard, self._card)

    def _save_datacard(self) -> None:
        """Saves a datacard to file system"""

        exclude_attr = {"data_profile", "storage_client"}

        # ImageDataSets use pydantic models for data
        if AllowedDataType.IMAGE not in self.card.metadata.data_type:
            exclude_attr.add("data")

        spec = self._get_storage_spec(
            filename=SaveName.DATACARD.value,
            uri=self.uris.get(UriNames.DATACARD_URI.value),
        )
        storage_path = save_artifact_to_storage(
            artifact=self.card.model_dump(exclude=exclude_attr),
            storage_client=self.storage_client,
            storage_spec=spec,
        )

        self.uris[UriNames.DATACARD_URI.value] = storage_path.uri

    def _convert_data_to_arrow(self) -> ArrowTable:
        """Converts data to arrow table

        Returns:
            arrow table model
        """
        arrow_table: ArrowTable = DataFormatter.convert_data_to_arrow(
            data=self.card.data,
            data_type=self.card.metadata.data_type,
        )
        arrow_table.feature_map = DataFormatter.create_table_schema(data=self.card.data)
        return arrow_table

    def _save_data_to_storage(self, data: Union[pa.Table, NDArray[Any], ImageDataset]) -> StoragePath:
        """Saves pyarrow table to file system

        Args:
            data:
                either numpy array , pyarrow table or image dataset

        Returns:
            StoragePath
        """

        storage_path = save_artifact_to_storage(
            artifact=data,
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=self.card.name,
                uri=self.uris.get(UriNames.DATA_URI.value),
            ),
            artifact_type=self.card.metadata.data_type,
        )

        return storage_path

    # TODO: steven - should be able to save tensorflow and torch datasets
    def _save_data(self) -> None:
        """Saves DataCard data to file system"""
        if self.card.data is None:
            return

        if isinstance(self.card.data, ImageDataset):
            self.card.data.convert_metadata()
            storage_path = self._save_data_to_storage(data=self.card.data)
            self.uris[UriNames.DATA_URI.value] = storage_path.uri

        else:
            arrow_table: ArrowTable = self._convert_data_to_arrow()
            storage_path = self._save_data_to_storage(data=arrow_table.table)
            self.uris[UriNames.DATA_URI.value] = storage_path.uri
            self.card.metadata.feature_map = arrow_table.feature_map

    def _save_profile(self) -> None:
        """Saves a datacard data profile"""
        if self.card.data_profile is None:
            return

        # profile report needs to be dumped to bytes and saved in joblib/pickle format
        # This is a requirement for loading with ydata-profiling
        profile_bytes = self.card.data_profile.dumps()

        storage_path = save_artifact_to_storage(
            artifact=profile_bytes,
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=SaveName.DATA_PROFILE.value,
                uri=self.uris.get(UriNames.PROFILE_URI.value),
            ),
        )
        self.uris[UriNames.PROFILE_URI.value] = storage_path.uri

    def _save_profile_html(self) -> None:
        """Saves a profile report to file system"""
        if self.card.data_profile is None:
            return

        profile_html = self.card.data_profile.to_html()

        storage_path = save_artifact_to_storage(
            artifact=profile_html,
            artifact_type=ArtifactStorageType.HTML.value,
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=SaveName.DATA_PROFILE.value,
                uri=self.uris.get(UriNames.PROFILE_HTML_URI.value),
            ),
        )
        self.uris[UriNames.PROFILE_HTML_URI.value] = storage_path.uri

    def save_artifacts(self) -> DataCard:
        """Saves artifacts from a DataCard"""

        self._save_data()
        self._save_profile()
        self._save_profile_html()

        self._save_datacard()

        return self.card, self.uris

    @staticmethod
    def validate(card_type: str) -> bool:
        return CardType.DATACARD.value in card_type


class ModelCardArtifactSaver(CardArtifactSaver):
    @cached_property
    def card(self) -> ModelCard:
        return cast(ModelCard, self._card)

    def _get_model_metadata(self, onnx_attr: OnnxAttr) -> ModelMetadata:
        """Create Onnx Model from trained model"""

        return ModelMetadata(
            model_name=self.card.name,
            model_type=self.card.interface.model_type,
            onnx_uri=onnx_attr.onnx_path,
            onnx_version=onnx_attr.onnx_version,
            model_uri=self.uris[UriNames.TRAINED_MODEL_URI.value],
            model_version=self.card.version,
            model_team=self.card.team,
            sample_data=self.card._get_sample_data_for_api(),  # pylint: disable=protected-access
            data_schema=self.card.metadata.data_schema,
        )

    def _create_metadata(self) -> OnnxAttr:
        if not self.card.to_onnx:
            model_metadata = _TrainedModelMetadataCreator(self.card.interface).get_model_metadata()
            self.card.metadata.data_schema = model_metadata.data_schema
            return OnnxAttr()

        if isinstance(self.card.interface, HuggingFaceModel):
            return OnnxAttr()

        model_metadata = _OnnxModelConverter(self.card.interface).convert_model()
        assert model_metadata.onnx_model is not None
        self.card.metadata.data_schema = model_metadata.data_schema

        storage_path = save_artifact_to_storage(
            artifact=model_metadata.onnx_model.sess._model_bytes,
            artifact_type=ArtifactStorageType.ONNX.value,
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=SaveName.ONNX_MODEL.value,
                uri=self.uris.get(UriNames.ONNX_MODEL_URI.value),
            ),
            extra_path="onnx",
        )

        self.uris[UriNames.ONNX_MODEL_URI.value] = storage_path.uri

        # add onnx model to card interface
        assert hasattr(self.card.interface, "onnx_model")
        self.card.interface.onnx_model = model_metadata.onnx_model

        return OnnxAttr(
            onnx_path=storage_path.uri,
            onnx_version=model_metadata.onnx_model.onnx_version,
        )

    def _save_model_metadata(self) -> None:
        self._save_trained_model()
        self._save_sample_data()
        onnx_attr = self._create_metadata()

        model_metadata = self._get_model_metadata(onnx_attr=onnx_attr)
        metadata_path = save_artifact_to_storage(
            artifact=model_metadata.model_dump_json(),
            artifact_type=ArtifactStorageType.JSON.value,
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=SaveName.MODEL_METADATA.value,
                uri=self.uris.get(UriNames.MODEL_METADATA_URI.value),
            ),
        )
        self.uris[UriNames.MODEL_METADATA_URI.value] = metadata_path.uri

    def _save_modelcard(self) -> None:
        """Saves a modelcard to file system"""

        model_dump = self.card.model_dump(
            exclude={
                {"model": {"model", "sample_data", "preprocessor"}},
                "storage_client",
            }
        )
        model_dump["metadata"].pop("onnx_model")

        storage_path = save_artifact_to_storage(
            artifact=model_dump,
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=SaveName.MODELCARD.value,
                uri=self.uris.get(UriNames.MODELCARD_URI.value),
            ),
        )

        self.uris[UriNames.MODELCARD_URI.value] = storage_path.uri

    def _get_model_artifact_to_save(self) -> Union[HuggingFaceStorageArtifact, SUPPORTED_MODELS]:
        """Saves a huggingface model to file system

        Huggingface models are converted to onnx during model saving since they are built
        from the model save dir (don't want to do this twice).
        Thus, we need to add the onnx arguments and metadata to save arguments
        """

        if isinstance(self.card.model, HuggingFaceModel):
            return HuggingFaceStorageArtifact(
                model_interface=self.card.model,
                metadata=self.card.metadata,
                to_onnx=self.card.to_onnx,
            )

        return self.card.model

    def _save_trained_model(self) -> None:
        """Saves trained model associated with ModelCard to filesystem"""

        storage_path = save_artifact_to_storage(
            artifact=self._get_model_artifact_to_save(),
            artifact_type=self.card.model.model_type,
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=SaveName.TRAINED_MODEL.value,
                uri=self.uris.get(UriNames.TRAINED_MODEL_URI.value),
            ),
            extra_path="model",
        )

        if not isinstance(self.card.model, HuggingFaceModel):
            self.uris[UriNames.TRAINED_MODEL_URI.value] = storage_path.uri

    def _save_preprocessor(self) -> None:
        """Saves preprocessor artifact associated with model"""

        if not isinstance(self.card.model, HuggingFaceModel):
            return

        storage_path = save_artifact_to_storage(
            artifact=self.card.interface.preprocessor,
            artifact_type=self.card.model.model_type,
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=SaveName.PREPROCESSOR.value,
                uri=self.uris.get(UriNames.PREPROCESSOR_URI.value),
            ),
            extra_path="model",
        )

        self.uris[UriNames.PREPROCESSOR_URI.value] = storage_path.uri

    # TODO: steven - should be able to save tensorflow and torch datasets
    def _get_artifact_and_type(self) -> Tuple[ValidSavedSample, str]:
        """Get artifact and artifact type to save"""

        if self.card.interface.data_type == AllowedDataType.DICT:
            return self.card.interface.sample_data, AllowedDataType.DICT

        if self.card.interface.data_type in [AllowedDataType.PYARROW.value, AllowedDataType.PANDAS.value]:
            arrow_table: ArrowTable = DataFormatter.convert_data_to_arrow(
                data=self.card.interface.sample_data,
                data_type=self.card.interface.data_type,
            )
            return arrow_table.table, AllowedDataType.PYARROW.value

        return self.card.interface.sample_data, AllowedDataType.NUMPY.value

    def _save_sample_data(self) -> None:
        """Saves sample data associated with ModelCard to filesystem"""

        storage_spec = self._get_storage_spec(
            filename=SaveName.SAMPLE_MODEL_DATA.value,
            uri=self.uris.get(UriNames.SAMPLE_DATA_URI.value),
        )
        artifact, artifact_type = self._get_artifact_and_type()

        storage_path = save_artifact_to_storage(
            artifact=artifact,
            storage_client=self.storage_client,
            storage_spec=storage_spec,
            artifact_type=artifact_type,
        )

        self.uris[UriNames.SAMPLE_DATA_URI.value] = storage_path.uri

    def save_artifacts(self) -> ModelCard:
        """Save model artifacts associated with ModelCard"""

        if self.card.metadata.uris.model_metadata_uri is None:
            self._save_model_metadata()

        self._save_modelcard()

        return self.card, self.uris

    @staticmethod
    def validate(card_type: str) -> bool:
        return CardType.MODELCARD.value in card_type


class AuditCardArtifactSaver(CardArtifactSaver):
    @cached_property
    def card(self) -> AuditCard:
        return cast(AuditCard, self._card)

    def _save_audit(self) -> None:
        storage_path = save_artifact_to_storage(
            artifact=self.card.model_dump(),
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=SaveName.AUDIT,
                uri=self.uris.get(UriNames.AUDIT_URI.value),
            ),
        )

        self.uris[UriNames.AUDIT_URI.value] = storage_path.uri

    def save_artifacts(self) -> AuditCard:
        self._save_audit()

        return self.card, self.uris

    @staticmethod
    def validate(card_type: str) -> bool:
        return CardType.AUDITCARD.value in card_type


class RunCardArtifactSaver(CardArtifactSaver):
    @cached_property
    def card(self) -> RunCard:
        return cast(RunCard, self._card)

    def _save_runcard(self) -> None:
        """Saves a runcard"""
        storage_path = save_artifact_to_storage(
            artifact=self.card.model_dump(exclude={"artifacts", "storage_client"}),
            storage_client=self.storage_client,
            storage_spec=self._get_storage_spec(
                filename=SaveName.RUNCARD.value,
                uri=self.uris.get(UriNames.RUNCARD_URI.value),
            ),
        )
        self.uris[UriNames.RUNCARD_URI.value] = storage_path.uri

    def _save_run_artifacts(self) -> None:
        """Saves all artifacts associated with RunCard to filesystem"""
        artifact_uris: Dict[str, str] = {}
        self.uris[UriNames.ARTIFACT_URIS.value]: Dict[str, str] = {}
        if self.card.artifact_uris is not None:
            # some cards have already been saved and thus have URIs already.
            # include them
            artifact_uris = self.card.artifact_uris

        if self.card.artifacts is not None:
            for name, artifact in self.card.artifacts.items():
                if name in artifact_uris:
                    continue

                storage_path = save_artifact_to_storage(
                    artifact=artifact,
                    storage_client=self.storage_client,
                    storage_spec=ArtifactStorageSpecs(save_path=str(self.card.artifact_uri), filename=name),
                )

                artifact_uris[name] = storage_path.uri
                self.uris[UriNames.ARTIFACT_URIS.value][name] = storage_path.uri

        self.card.artifact_uris = artifact_uris

    def save_artifacts(self) -> RunCard:
        self._save_run_artifacts()
        self._save_runcard()

        return self.card, self.uris

    @staticmethod
    def validate(card_type: str) -> bool:
        return CardType.RUNCARD.value in card_type


class PipelineCardArtifactSaver(CardArtifactSaver):
    @cached_property
    def card(self) -> PipelineCard:
        return cast(PipelineCard, self._card)

    def save_artifacts(self) -> PipelineCard:
        return self.card, self.uris

    @staticmethod
    def validate(card_type: str) -> bool:
        return CardType.PIPELINECARD.value in card_type


class ProjectCardArtifactSaver(CardArtifactSaver):
    @cached_property
    def card(self) -> ProjectCard:
        return cast(ProjectCard, self._card)

    def save_artifacts(self) -> ProjectCard:
        return self.card

    @staticmethod
    def validate(card_type: str) -> bool:
        return CardType.PROJECTCARD.value in card_type


def save_card_artifacts(card: ArtifactCard, storage_client: StorageClientType) -> ArtifactCard:
    """Saves a given ArtifactCard's artifacts to a filesystem

    Args:
        card:
            ArtifactCard to save
        storage_client:
            StorageClient to use to save artifacts

    Returns:
        ArtifactCard with updated artifact uris

    """
    card_saver = next(
        card_saver
        for card_saver in CardArtifactSaver.__subclasses__()
        if card_saver.validate(card_type=card.__class__.__name__.lower())
    )

    saver = card_saver(card=card, storage_client=storage_client)

    return saver.save_artifacts()  # type: ignore
