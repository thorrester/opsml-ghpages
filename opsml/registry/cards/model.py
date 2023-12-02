# pylint: disable=too-many-lines
# Copyright (c) Shipt, Inc.
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
from functools import cached_property
from typing import Any, Dict, Optional, Union, cast

import numpy as np
import pandas as pd
import polars as pl
from pydantic import ConfigDict, field_validator, model_validator

from opsml.helpers.logging import ArtifactLogger
from opsml.model.predictor import OnnxModelPredictor
from opsml.model.types import (
    ApiDataSchemas,
    DataDict,
    Feature,
    ModelMetadata,
    ModelReturn,
    OnnxModelDefinition,
    ValidModelInput,
)
from opsml.registry.cards.audit_deco import auditable
from opsml.registry.cards.base import ArtifactCard
from opsml.registry.cards.types import CardType, ModelCardMetadata
from opsml.registry.data.types import AllowedDataType, check_data_type
from opsml.registry.sql.records import ModelRegistryRecord, RegistryRecord
from opsml.registry.storage.artifact_storage import load_record_artifact_from_storage
from opsml.registry.storage.types import ArtifactStorageSpecs, ArtifactStorageType
from opsml.registry.utils.settings import settings

logger = ArtifactLogger.get_logger()
storage_client = settings.storage_client


@auditable
class ModelCard(ArtifactCard):
    """Create a ModelCard from your trained machine learning model.
    This Card is used in conjunction with the ModelCardCreator class.

    Args:
        name:
            Name for the model specific to your current project
        team:
            Team that this model is associated with
        user_email:
            Email to associate with card
        trained_model:
            Trained model. Can be of type sklearn, xgboost, lightgbm or tensorflow
        sample_input_data:
            Sample of data model was trained on
        uid:
            Unique id (assigned if card has been registered)
        version:
            Current version (assigned if card has been registered)
        datacard_uid:
            Uid of the DataCard associated with training the model
        to_onnx:
            Whether to convert the model to onnx or not
        metadata:
            `ModelCardMetadata` associated with the model
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        ignored_types=(cached_property,),
        protected_namespaces=("protect_",),
    )

    trained_model: Optional[Any] = None
    sample_input_data: Optional[ValidModelInput] = None
    datacard_uid: Optional[str] = None
    to_onnx: bool = True
    metadata: ModelCardMetadata

    @model_validator(mode="before")
    def _check_args(cls, values: Dict[str, Any]):
        """Converts trained model to modelcard"""

        if all([values.get("uid"), values.get("version")]):
            return values

        if not cls._required_args_present(values=values):
            raise ValueError(
                """trained_model and sample_input_data are required for instantiating a ModelCard""",
            )

        metadata = values.get("metadata")

        if metadata is None:
            data_type = check_data_type(values["sample_input_data"])
            if data_type in [AllowedDataType.IMAGE]:
                raise ValueError(
                    f"""Invalid model data input type. Accepted types are a pandas dataframe, 
                                 numpy array and dictionary of numpy arrays. Received {data_type}""",
                )
            values["metadata"] = ModelCardMetadata(sample_data_type=data_type)
        return values

    @field_validator("sample_input_data", mode="before")
    def _get_one_sample(cls, input_data: Optional[ValidModelInput]) -> Optional[ValidModelInput]:
        """Parses input data and returns a single record to be used during ONNX conversion and validation"""

        if input_data is None:
            return input_data

        if not isinstance(input_data, dict):
            if isinstance(input_data, pl.DataFrame):
                input_data = input_data.to_pandas()

            return input_data[0:1]

        sample_dict = {}
        if isinstance(input_data, dict):
            for key in input_data.keys():
                sample_dict[key] = input_data[key][0:1]

            return sample_dict

        raise ValueError("Provided sample data is not a valid type")

    @classmethod
    def _required_args_present(cls, values: Dict[str, Any]) -> bool:
        return all(
            values.get(var_) is not None
            for var_ in [
                "trained_model",
                "sample_input_data",
            ]
        )

    @property
    def model_data_schema(self) -> DataDict:
        if self.metadata.data_schema is not None:
            return self.metadata.data_schema.model_data_schema
        raise ValueError("Model data schema has not been set")

    @property
    def input_data_schema(self) -> Dict[str, Feature]:
        if self.metadata.data_schema is not None and self.metadata.data_schema.input_data_schema is not None:
            return self.metadata.data_schema.input_data_schema
        raise ValueError("Model input data schema has not been set or is not needed for this model")

    def load_sample_data(self) -> None:
        """Loads sample data associated with original non-onnx model"""

        if self.metadata.sample_data_type is None:
            raise ValueError("Cannot load sample data - sample_data_type is not set")

        sample_data = load_record_artifact_from_storage(
            artifact_type=self.metadata.sample_data_type,
            storage_client=storage_client,
            storage_spec=ArtifactStorageSpecs(save_path=self.metadata.uris.sample_data_uri),
        )
        self.sample_input_data = sample_data

    def load_trained_model(self) -> None:
        """Loads original trained model"""

        if not all([bool(self.metadata.uris.trained_model_uri), bool(self.metadata.uris.sample_data_uri)]):
            raise ValueError(
                """Trained model uri and sample data uri must both be set to load a trained model""",
            )

        if self.trained_model is None:
            self.load_sample_data()

            if self.metadata.model_type is None:
                raise ValueError("Cannot load trained model - model_type is not set")

            trained_model = load_record_artifact_from_storage(
                artifact_type=self.metadata.model_type,
                storage_client=storage_client,
                storage_spec=ArtifactStorageSpecs(save_path=self.metadata.uris.trained_model_uri),
            )
            self.trained_model = trained_model

    @property
    def model_metadata(self) -> ModelMetadata:
        """Loads `ModelMetadata` class"""
        model_metadata = load_record_artifact_from_storage(
            artifact_type=ArtifactStorageType.JSON.value,
            storage_client=storage_client,
            storage_spec=ArtifactStorageSpecs(save_path=self.metadata.uris.model_metadata_uri),
        )

        return ModelMetadata.model_validate(model_metadata)

    def _load_onnx_model(self, metadata: ModelMetadata) -> Any:
        """Loads the actual onnx file

        Args:
            metadata:
                `ModelMetadata`
        """
        if metadata.onnx_uri is None:
            raise ValueError("Onnx uri is not specified")

        onnx_model = load_record_artifact_from_storage(
            artifact_type=ArtifactStorageType.ONNX.value,
            storage_client=storage_client,
            storage_spec=ArtifactStorageSpecs(save_path=metadata.onnx_uri),
        )

        return onnx_model

    def load_onnx_model_definition(self) -> None:
        """Loads the onnx model definition"""

        if self.metadata.uris.model_metadata_uri is None:
            raise ValueError("No model metadata exists. Please check the registry or register a new model")

        metadata = self.model_metadata
        onnx_model = self._load_onnx_model(metadata=metadata)

        model_def = OnnxModelDefinition(
            onnx_version=metadata.onnx_version,
            model_bytes=onnx_model.SerializeToString(),
        )
        self.metadata.onnx_model_def = model_def

    def create_registry_record(self) -> RegistryRecord:
        """Creates a registry record from the current ModelCard"""

        exclude_vars = {"trained_model", "sample_input_data"}
        dumped_model = self.model_dump(exclude=exclude_vars)
        dumped_model["metadata"].pop("onnx_model_def")

        return ModelRegistryRecord(**dumped_model)

    def _set_version_for_predictor(self) -> str:
        if self.version is None:
            logger.warning("""ModelCard has no version (not registered). Defaulting to 1 (for testing only)""")
            version = "1.0.0"
        else:
            version = self.version

        return version

    def _set_model_attributes(self, model_return: ModelReturn) -> None:
        setattr(self.metadata, "onnx_model_def", model_return.model_definition)
        setattr(self.metadata, "data_schema", model_return.api_data_schema)
        setattr(self.metadata, "model_type", model_return.model_type)

    def _create_and_set_model_attr(self) -> None:
        """
        Creates Onnx model from trained model and sample input data
        and sets Card attributes

        """

        from opsml.model.creator import (  # pylint: disable=import-outside-toplevel
            create_model,
        )

        model_return = create_model(
            model=self.trained_model,
            input_data=cast(ValidModelInput, self.sample_input_data),
            input_data_type=self.metadata.sample_data_type,
            additional_onnx_args=self.metadata.additional_onnx_args,
            to_onnx=self.to_onnx,
            onnx_model_def=self.metadata.onnx_model_def,
        )

        self._set_model_attributes(model_return=model_return)

    def _get_sample_data_for_api(self) -> Dict[str, Any]:
        """
        Converts sample data to dictionary that can be used
        to validate an onnx model
        """

        if self.sample_input_data is None:
            self.load_sample_data()

        sample_data = cast(
            Union[pd.DataFrame, np.ndarray, Dict[str, Any]],
            self.sample_input_data,
        )

        if isinstance(sample_data, np.ndarray):
            model_data = self.model_data_schema
            input_name = next(iter(model_data.input_features.keys()))
            return {input_name: sample_data[0, :].tolist()}  # pylint: disable=unsubscriptable-object

        if isinstance(sample_data, pd.DataFrame):
            record = list(sample_data[0:1].T.to_dict().values())[0]  # pylint: disable=unsubscriptable-object
            return record

        if isinstance(sample_data, pl.DataFrame):
            record = list(sample_data.to_pandas()[0:1].T.to_dict().values())[0]
            return record

        record = {}
        for feat, val in sample_data.items():
            record[feat] = np.ravel(val).tolist()
        return record

    def onnx_model(self, start_onnx_runtime: bool = True) -> OnnxModelPredictor:
        """
        Loads an onnx model from string or creates an onnx model from trained model

        Args:
            start_onnx_runtime:
                Whether to start the onnx runtime session or not

        Returns
            `OnnxModelPredictor`

        """

        # todo: clean this up
        if self.metadata.onnx_model_def is None or self.metadata.data_schema is None:
            self._create_and_set_model_attr()

        version = self._set_version_for_predictor()

        # recast to make mypy happy
        # todo: refactor
        model_def = cast(OnnxModelDefinition, self.metadata.onnx_model_def)
        model_type = str(self.metadata.model_type)
        data_schema = cast(ApiDataSchemas, self.metadata.data_schema)

        sample_api_data = self._get_sample_data_for_api()

        return OnnxModelPredictor(
            model_name=self.name,
            model_type=model_type,
            model_definition=model_def.model_bytes,
            data_schema=data_schema,
            model_version=version,
            onnx_version=model_def.onnx_version,
            sample_api_data=sample_api_data,
            start_sess=start_onnx_runtime,
        )

    @property
    def card_type(self) -> str:
        return CardType.MODELCARD.value
