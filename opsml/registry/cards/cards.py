# pylint: disable=too-many-lines

from functools import cached_property
from typing import Any, Dict, List, Optional, Union, cast

import numpy as np
import pandas as pd
from pyarrow import Table
from pydantic import BaseModel, root_validator, validator

from opsml.helpers.logging import ArtifactLogger
from opsml.helpers.utils import (
    FindPath,
    TypeChecker,
    clean_string,
    validate_name_team_pattern,
)
from opsml.model.predictor import OnnxModelPredictor
from opsml.model.types import (
    ApiDataSchemas,
    DataDict,
    Feature,
    ModelMetadata,
    ModelReturn,
    OnnxModelDefinition,
    TorchOnnxArgs,
)
from opsml.registry.cards.types import (
    METRICS,
    PARAMS,
    CardInfo,
    CardType,
    Metric,
    ModelCardUris,
    Param,
)
from opsml.registry.data.splitter import DataHolder, DataSplitter
from opsml.registry.sql.records import (
    ARBITRARY_ARTIFACT_TYPE,
    DataRegistryRecord,
    ModelRegistryRecord,
    PipelineRegistryRecord,
    ProjectRegistryRecord,
    RegistryRecord,
    RunRegistryRecord,
)
from opsml.registry.storage.artifact_storage import load_record_artifact_from_storage
from opsml.registry.storage.storage_system import StorageClientType
from opsml.registry.storage.types import ArtifactStorageSpecs, ArtifactStorageType

logger = ArtifactLogger.get_logger(__name__)


class ArtifactCard(BaseModel):
    """Base pydantic class for artifact cards"""

    name: str
    team: str
    user_email: str
    version: Optional[str] = None
    uid: Optional[str] = None
    info: Optional[CardInfo] = None
    storage_client: Optional[StorageClientType]

    class Config:
        arbitrary_types_allowed = True
        validate_assignment = False
        smart_union = True

    @root_validator(pre=True)
    def validate(cls, env_vars):  # pylint: disable=no-self-argument)
        """Validate base args and Lowercase name and team"""

        card_info = env_vars.get("info")

        for key in ["name", "team", "user_email", "version", "uid"]:
            val = env_vars.get(key)

            if card_info is not None:
                val = val or getattr(card_info, key)

            if key in ["name", "team"]:
                val = clean_string(val)

            env_vars[key] = val

        # validate name and team for pattern
        validate_name_team_pattern(
            name=env_vars["name"],
            team=env_vars["team"],
        )

        return env_vars

    def create_registry_record(self) -> RegistryRecord:
        """Creates a registry record from self attributes"""
        raise NotImplementedError

    @property
    def card_type(self) -> str:
        raise NotImplementedError


class DataCard(ArtifactCard):
    """Create a DataCard from your data.

    Args:
        data:
            Data to use for data card.
        name:
            What to name the data
        team:
            Team that this data is associated with
        user_email:
            Email to associate with data card
        dependent_vars:
            Optional list of dependent variables in data
        feature_descriptions:
            Optional dictionary of feature names and their descriptions
        dependent_vars:
            List of dependent variables. Can be string or index if using numpy
        feature_descriptions:
            Dictionary of features and their descriptions
        additional_info:
            Dictionary of additional info to associate with data
            (i.e. if data is tokenized dataset, metadata could be {"vocab_size": 200})
        data_splits:
            Optional list containing split logic. Defaults to None.
            Logic for data splits can be defined in the following three ways:

            You can specify as many splits as you'd like

            (1) Split based on column value (works for pd.DataFrame)
                splits = [
                    {"label": "train", "column": "DF_COL", "column_value": 0}, -> "val" can also be a string
                    {"label": "test",  "column": "DF_COL", "column_value": 1},
                    {"label": "eval",  "column": "DF_COL", "column_value": 2},
                    ]

            (2) Index slicing by start and stop (works for np.ndarray, pyarrow.Table, and pd.DataFrame)
                splits = [
                    {"label": "train", "start": 0, "stop": 10},
                    {"label": "test", "start": 11, "stop": 15},
                    ]

            (3) Index slicing by list (works for np.ndarray, pyarrow.Table, and pd.DataFrame)
                splits = [
                    {"label": "train", "indices": [1,2,3,4]},
                    {"label": "test", "indices": [5,6,7,8]},
                    ]

        runcard_uid:
            Id of RunCard that created the DataCard

        pipelinecard_uid:
            Associated PipelineCard

        sql_logic:
            Dictionary of strings containing sql logic or sql files used to create the data

        The following are non-required args and are set after registering a DataCard

        data_uri:
            Location where converted pyarrow table is stored
        version:
            DataCard version
        feature_map:
            Map of features in data (inferred when converting to pyrarrow table)
        data_type:
            Data type inferred from supplied data
        uid:
            Unique id assigned to the DataCard

    Returns:
        DataCard

    """

    data: Optional[Union[np.ndarray, pd.DataFrame, Table]]
    data_splits: Optional[List[Dict[str, Any]]]
    feature_map: Optional[Dict[str, Union[str, None]]]
    data_type: Optional[str]
    dependent_vars: Optional[List[Union[int, str]]]
    feature_descriptions: Optional[Dict[str, str]]
    additional_info: Optional[Dict[str, Union[float, int, str]]]
    sql_logic: Dict[Optional[str], Optional[str]] = {}
    runcard_uid: Optional[str] = None
    pipelinecard_uid: Optional[str] = None
    data_uri: Optional[str]
    datacard_uri: Optional[str] = None

    @validator("data_uri", pre=True, always=True)
    def check_data(cls, data_uri, values):  # pylint: disable=no-self-argument
        if data_uri is None:
            if values["data"] is None and not bool(values["sql_logic"]):
                raise ValueError("Data or sql logic must be supplied when no data_uri is present")

        return data_uri

    @validator("data_splits", pre=True, always=True)
    def check_splits(cls, splits):  # pylint: disable=no-self-argument
        if splits is None:
            return []

        for split in splits:
            indices = split.get("indices")
            if indices is not None and isinstance(indices, np.ndarray):
                split["indices"] = indices.tolist()

        return splits

    @validator("feature_descriptions", pre=True, always=True)
    def lower_descriptions(cls, feature_descriptions):  # pylint: disable=no-self-argument
        if feature_descriptions is None:
            return feature_descriptions

        feat_dict = {}
        for feature, description in feature_descriptions.items():
            feat_dict[feature.lower()] = description.lower()

        return feat_dict

    @validator("additional_info", pre=True, always=True)
    def check_info(cls, value):  # pylint: disable=no-self-argument
        return value or {}

    @validator("sql_logic", pre=True, always=True)
    def load_sql(cls, sql_logic, values):  # pylint: disable=no-self-argument
        if not bool(sql_logic):
            return sql_logic

        for name, query in sql_logic.items():
            if ".sql" in query:
                try:
                    sql_path = FindPath.find_filepath(name=query)
                    with open(sql_path, "r", encoding="utf-8") as file_:
                        query_ = file_.read()
                    sql_logic[name] = query_

                except Exception as error:
                    raise ValueError(f"Could not load sql file {query}. {error}") from error

        return sql_logic

    def split_data(self) -> Optional[DataHolder]:
        """
        Loops through data splits and splits data either by indexing or
        column values

        Example:

            ```python
            card_info = CardInfo(name="linnerrud", team="tutorial", user_email="user@email.com")
            data_card = DataCard(
                info=card_info,
                data=data,
                dependent_vars=["Pulse"],
                # define splits
                data_splits=[
                    {"label": "train", "indices": train_idx},
                    {"label": "test", "indices": test_idx},
                ],

            )

            splits = data_card.split_data()
            print(splits.train.X.head())

               Chins  Situps  Jumps
            0    5.0   162.0   60.0
            1    2.0   110.0   60.0
            2   12.0   101.0  101.0
            3   12.0   105.0   37.0
            4   13.0   155.0   58.0
            ```

        Returns
            Class containing data splits
        """

        if self.data_splits is not None:
            data_holder = DataHolder()
            for split in self.data_splits:
                label, data = DataSplitter(
                    split_attributes=split,
                    dependent_vars=self.dependent_vars,
                ).split(data=self.data)

                setattr(data_holder, label, data)

            return data_holder
        raise ValueError("No data splits provided")

    def load_data(self):
        """Loads data"""

        if not bool(self.data) and self.storage_client is not None:
            storage_spec = ArtifactStorageSpecs(save_path=self.data_uri)

            self.storage_client.storage_spec = storage_spec
            data = load_record_artifact_from_storage(
                storage_client=self.storage_client,
                artifact_type=self.data_type,
            )

            setattr(self, "data", data)
        else:
            logger.info("Data has already been loaded")

    def create_registry_record(self) -> RegistryRecord:
        """
        Creates required metadata for registering the current data card.
        Implemented with a DataRegistry object.

        Returns:
            Regsitry metadata

        """
        exclude_attr = {"data", "storage_client"}
        return DataRegistryRecord(**self.dict(exclude=exclude_attr))

    def add_info(self, info: Dict[str, Union[float, int, str]]):
        """
        Adds metadata to the existing DataCard metadatda dictionary

        Args:
            Metadata:
                Dictionary containing name (str) and value (float, int, str) pairs
                to add to the current metadata set
        """

        curr_info = cast(Dict[str, Union[int, float, str]], self.additional_info)
        self.additional_info = {**info, **curr_info}

    def add_sql(
        self,
        name: str,
        query: Optional[str] = None,
        filename: Optional[str] = None,
    ):
        """
        Adds a query or query from file to the sql_logic dictionary. Either a query or
        a filename pointing to a sql file are required in addition to a name.

        Args:
            name:
                Name for sql query
            query:
                SQL query
            filename:
                Filename of sql query
        """
        if query is not None:
            self.sql_logic[name] = query

        elif filename is not None:
            sql_path = FindPath.find_filepath(name=filename)
            with open(sql_path, "r", encoding="utf-8") as file_:
                query = file_.read()
            self.sql_logic[name] = query

        else:
            raise ValueError("SQL Query or Filename must be provided")

    @property
    def card_type(self) -> str:
        return CardType.DATACARD.value


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
        onnx_model_data:
            Pydantic model containing onnx data schema
        onnx_model_def:
            Pydantic model containing OnnxModel definition
        model_type:
            Type of model
        data_schema:
            Optional dictionary of the data schema used in model training
        additional_onnx_args:
            Optional pydantic model containing Torch args for model conversion to onnx.
        runcard_uid:
            RunCard associated with the ModelCard
        pipelinecard_uid:
            Associated PipelineCard
        uris:
            modelcard_uri:
                URI of modelcard
            trained_model_uri:
                URI where model is stored
            sample_data_uri:
                URI of trained model sample data
            model_metadata_uri:
                URI where model metadata is stored
    """

    trained_model: Optional[Any]
    sample_input_data: Optional[Union[pd.DataFrame, np.ndarray, Dict[str, np.ndarray]]]
    datacard_uid: Optional[str]
    onnx_model_data: Optional[DataDict]
    onnx_model_def: Optional[OnnxModelDefinition]
    sample_data_type: Optional[str]
    model_type: Optional[str]
    additional_onnx_args: Optional[TorchOnnxArgs]
    data_schema: Optional[ApiDataSchemas]
    runcard_uid: Optional[str] = None
    pipelinecard_uid: Optional[str] = None
    to_onnx: bool = True
    uris: ModelCardUris = ModelCardUris()

    class Config:
        arbitrary_types_allowed = True
        keep_untouched = (cached_property,)

    @root_validator(pre=True)
    def check_args(cls, values: Dict[str, Any]):  # pylint: disable=no-self-argument
        """Converts trained model to modelcard"""

        if all([values.get("uid"), values.get("version")]):
            return values

        if not cls._required_args_present(values=values):
            raise ValueError(
                """trained_model and sample_input_data are required for instantiating a ModelCard""",
            )

        return values

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
        if self.data_schema is not None:
            return self.data_schema.model_data_schema
        raise ValueError("Model data schema has not been set")

    @property
    def input_data_schema(self) -> Dict[str, Feature]:
        if self.data_schema is not None and self.data_schema.input_data_schema is not None:
            return self.data_schema.input_data_schema
        raise ValueError("Model input data schema has not been set or is not needed for this model")

    def load_sample_data(self):
        """Loads sample data associated with original non-onnx model"""

        storage_spec = ArtifactStorageSpecs(save_path=self.uris.sample_data_uri)

        self.storage_client.storage_spec = storage_spec
        sample_data = load_record_artifact_from_storage(
            storage_client=self.storage_client,
            artifact_type=self.sample_data_type,
        )

        setattr(self, "sample_input_data", sample_data)

    def load_trained_model(self):
        """Loads original trained model"""

        if not all([bool(self.uris.trained_model_uri), bool(self.uris.sample_data_uri)]):
            raise ValueError(
                """Trained model uri and sample data uri must both be set to load a trained model""",
            )

        if self.storage_client is not None:
            self.load_sample_data()
            storage_spec = ArtifactStorageSpecs(save_path=self.uris.trained_model_uri)
            self.storage_client.storage_spec = storage_spec
            trained_model = load_record_artifact_from_storage(
                storage_client=self.storage_client,
                artifact_type=self.model_type,
            )

            setattr(self, "trained_model", trained_model)

    def _load_metadata(self, storage_client: StorageClientType) -> ModelMetadata:
        """Loads onnx metadata"""

        # get metadata
        storage_spec = ArtifactStorageSpecs(save_path=self.uris.model_metadata_uri)
        storage_client.storage_spec = storage_spec
        model_metadata = load_record_artifact_from_storage(
            storage_client=storage_client,
            artifact_type=ArtifactStorageType.JSON.value,
        )

        return ModelMetadata.parse_obj(model_metadata)

    def _load_onnx_model(self, metadata: ModelMetadata, storage_client: StorageClientType) -> Any:
        """Loads the actual onnx file"""
        # get onnx model

        if metadata.onnx_uri is not None:
            storage_client.storage_spec.save_path = metadata.onnx_uri
            onnx_model = load_record_artifact_from_storage(
                storage_client=storage_client,
                artifact_type=ArtifactStorageType.ONNX.value,
            )

            return onnx_model

        raise ValueError("Onnx uri is not specified")

    def load_onnx_model_definition(self):
        """Loads the onnx model definition"""

        if self.uris.model_metadata_uri is None:
            raise ValueError("No model metadata exists. Please check the registry or register a new model")

        if self.storage_client is not None:
            metadata = self._load_metadata(storage_client=self.storage_client)

            onnx_model = self._load_onnx_model(
                metadata=metadata,
                storage_client=self.storage_client,
            )

            model_def = OnnxModelDefinition(
                onnx_version=metadata.onnx_version,
                model_bytes=onnx_model.SerializeToString(),
            )

            setattr(self, "onnx_model_def", model_def)

    def create_registry_record(self) -> RegistryRecord:
        """
        Creates a registry record from the current ModelCard

        Args:
            registry_name:
                ModelCard Registry table making request
            uid:
                Unique id of ModelCard

        """

        exclude_vars = {
            "trained_model",
            "sample_input_data",
            "onnx_model_def",
            "storage_client",
        }

        if not bool(self.onnx_model_def):
            self._create_and_set_model_attr(to_onnx=self.to_onnx)

        return ModelRegistryRecord(**self.dict(exclude=exclude_vars))

    def _set_version_for_predictor(self) -> str:
        if self.version is None:
            logger.warning(
                """ModelCard has no version (not registered).
                Defaulting to 1 (for testing only)
            """
            )
            version = "1.0.0"
        else:
            version = self.version

        return version

    def _set_model_attributes(self, model_return: ModelReturn) -> None:
        setattr(self, "onnx_model_def", model_return.model_definition)
        setattr(self, "data_schema", model_return.api_data_schema)
        setattr(self, "model_type", model_return.model_type)

    def _create_and_set_model_attr(self, to_onnx: bool) -> None:
        """
        Creates Onnx model from trained model and sample input data
        and sets Card attributes

        Args:
            to_onnx:
                Whether to convert to onnx or not
        """
        from opsml.model.creator import (  # pylint: disable=import-outside-toplevel
            create_model,
        )

        model_return = create_model(
            model=self.trained_model,
            input_data=self.sample_input_data,
            additional_onnx_args=self.additional_onnx_args,
            to_onnx=self.to_onnx,
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
            return {input_name: sample_data[0, :].tolist()}

        if isinstance(sample_data, pd.DataFrame):
            record = list(sample_data[0:1].T.to_dict().values())[0]
            return record

        record = {}
        for feat, val in sample_data.items():
            record[feat] = np.ravel(val).tolist()
        return record

    def onnx_model(
        self,
        start_onnx_runtime: bool = True,
    ) -> OnnxModelPredictor:
        """
        Loads an onnx model from string or creates an onnx model from trained model

        Args:
            start_onnx_runtime:
                Whether to start the onnx runtime session or not

        Returns
            `OnnxModelPredictor`

        """
        # todo: clean this up
        if not bool(self.onnx_model_def):
            self._create_and_set_model_attr(to_onnx=False)

        version = self._set_version_for_predictor()

        # recast to make mypy happy
        # todo: refactor
        model_def = cast(OnnxModelDefinition, self.onnx_model_def)
        model_type = str(self.model_type)
        data_schema = cast(ApiDataSchemas, self.data_schema)

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


class PipelineCard(ArtifactCard):
    """Create a PipelineCard from specified arguments

    Args:
        name:
            Pipeline name
        team:
            Team that this card is associated with
        user_email:
            Email to associate with card
        uid:
            Unique id (assigned if card has been registered)
        version:
            Current version (assigned if card has been registered)
        pipeline_code_uri:
            Storage uri of pipeline code
        datacard_uids:
            Optional list of DataCard uids to associate with pipeline
        modelcard_uids:
            Optional list of ModelCard uids to associate with pipeline
        runcard_uids:
            Optional list of RunCard uids to associate with pipeline

    """

    pipeline_code_uri: Optional[str] = None
    datacard_uids: List[Optional[str]] = []
    modelcard_uids: List[Optional[str]] = []
    runcard_uids: List[Optional[str]] = []

    def add_card_uid(self, uid: str, card_type: str):
        """
        Adds Card uid to appropriate card type attribute

        Args:
            uid:
                Card uid
            card_type:
                Card type. Accepted values are "data", "model", "run"
        """
        card_type = card_type.lower()
        if card_type.lower() not in [CardType.DATACARD.value, CardType.RUNCARD.value, CardType.MODELCARD.value]:
            raise ValueError("""Only 'model', 'run' and 'data' are allowed values for card_type""")

        current_ids = getattr(self, f"{card_type}card_uids")
        new_ids = [*current_ids, *[uid]]
        setattr(self, f"{card_type}card_uids", new_ids)

    def load_pipeline_code(self):
        raise NotImplementedError

    def create_registry_record(self) -> RegistryRecord:
        """Creates a registry record from the current PipelineCard"""
        return PipelineRegistryRecord(**self.dict())

    @property
    def card_type(self) -> str:
        return CardType.PIPELINECARD.value


class RunCard(ArtifactCard):

    """
    Create a RunCard from specified arguments.
    Apart from required args, an Experiment card must be associated with one of datacard_uid,
    modelcard_uids or pipelinecard_uid

    Args:
        name:
            Run name
        team:
            Team that this card is associated with
        user_email:
            Email to associate with card
        datacard_uid:
            Optional DataCard uid associated with pipeline
        modelcard_uids:
            Optional List of ModelCard uids to associate with this run
        pipelinecard_uid:
            Optional PipelineCard uid to associate with this experiment
        metrics:
            Optional dictionary of key (str), value (int, float) metric paris.
            Metrics can also be added via class methods.
        parameters:
            Parameters associated with a RunCard
        artifacts:
            Optional dictionary of artifacts (i.e. plots, reports) to associate with
            the current run.
        artifact_uris:
            Optional dictionary of artifact uris associated with artifacts.
        uid:
            Unique id (assigned if card has been registered)
        version:
            Current version (assigned if card has been registered)

    """

    datacard_uids: List[str] = []
    modelcard_uids: List[str] = []
    pipelinecard_uid: Optional[str]
    metrics: METRICS = {}
    parameters: PARAMS = {}
    artifacts: Dict[str, Any] = {}
    artifact_uris: Dict[str, str] = {}
    tags: Dict[str, str] = {}
    project_id: Optional[str]
    runcard_uri: Optional[str]

    def add_tag(self, key: str, value: str):
        """
        Logs params to current RunCard

        Args:
            key:
                Key for tag
            value:
                value for tag
        """
        self.tags = {**{key: value}, **self.tags}

    def add_tags(self, tags: Dict[str, str]):
        """
        Logs params to current RunCard

        Args:
            tags:
                Dictionary of tags
        """
        self.tags = {**tags, **self.tags}

    def log_parameters(self, params: Dict[str, Union[float, int, str]]):
        """
        Logs params to current RunCard

        Args:
            params:
                Dictionary of parameters
        """

        for key, value in params.items():
            # check key
            self.log_parameter(key, value)

    def log_parameter(self, key: str, value: Union[int, float, str]):
        """
        Logs params to current RunCard

        Args:
            key:
                Param name
            value:
                Param value
        """

        TypeChecker.check_param_type(param=value)
        param = Param(name=key, value=value)

        if self.parameters.get(key) is not None:
            self.parameters[key].append(param)

        else:
            self.parameters[key] = [param]

    def log_metric(
        self,
        key: str,
        value: Union[int, float],
        timestamp: Optional[int] = None,
        step: Optional[int] = None,
    ) -> None:
        """
        Logs metric to the existing RunCard metric dictionary

        Args:
            key:
                Metric name
            value:
                Metric value
            timestamp:
                Optional timestamp
            ste:
                Optional step associated with name and value
        """

        TypeChecker.check_metric_type(metric=value)
        metric = Metric(name=key, value=value, timestamp=timestamp, step=step)

        if self.metrics.get(key) is not None:
            self.metrics[key].append(metric)
        else:
            self.metrics[key] = [metric]

    def log_metrics(self, metrics: Dict[str, Union[float, int]]) -> None:
        """
        Log metrics to the existing RunCard metric dictionary

        Args:
            metrics:
                Dictionary containing key (str) and value (float or int) pairs
                to add to the current metric set
        """

        for key, value in metrics.items():
            self.log_metric(key, value)

    def log_artifact(self, name: str, artifact: Any) -> None:
        """
        Append any artifact associated with your run to
        the RunCard. The aritfact will be saved and the uri
        will be appended to the RunCard. Artifact must be pickleable
        (saved with joblib)

        Args:
            name:
                Artifact name
            artifact:
                Artifact
        """

        curr_artifacts = cast(Dict[str, Any], self.artifacts)
        new_artifact = {name: artifact}
        self.artifacts = {**new_artifact, **curr_artifacts}
        setattr(self, "artifacts", {**new_artifact, **self.artifacts})

    def create_registry_record(self) -> RegistryRecord:
        """Creates a registry record from the current RunCard"""

        exclude_attr = {"artifacts", "storage_client", "params", "metrics"}

        return RunRegistryRecord(**self.dict(exclude=exclude_attr))

    def add_artifact_uri(self, name: str, uri: str):
        """
        Adds an artifact_uri to the runcard

        Args:
            name:
                Name to associate with artifact
            uri:
                Uri where artifact is stored
        """

        self.artifact_uris[name] = uri

    def add_card_uid(self, card_type: str, uid: str) -> None:
        """
        Adds a card uid to the appropriact card uid list for tracking

        Args:
            card_type:
                ArtifactCard class name
            uid:
                Uid of registered ArtifactCard
        """

        if card_type == CardType.DATACARD:
            self.datacard_uids = [uid, *self.datacard_uids]
        elif card_type == CardType.MODELCARD:
            self.modelcard_uids = [uid, *self.modelcard_uids]

    def get_metric(self, name: str) -> Union[List[Metric], Metric]:
        """
        Gets a metric by name

        Args:
            name:
                Name of metric

        Returns:
            List of dictionaries or dictionary containing value

        """
        metric = self.metrics.get(name)
        if metric is not None:
            if len(metric) > 1:
                return metric
            if len(metric) == 1:
                return metric[0]
            return metric

        raise ValueError(f"Metric {metric} is not defined")

    def get_parameter(self, name: str) -> Union[List[Param], Param]:
        """
        Gets a metric by name

        Args:
            name:
                Name of param

        Returns:
            List of dictionaries or dictionary containing value

        """
        param = self.parameters.get(name)
        if param is not None:
            if len(param) > 1:
                return param
            if len(param) == 1:
                return param[0]
            return param

        raise ValueError(f"Param {param} is not defined")

    def load_artifacts(self) -> None:
        if bool(self.artifact_uris) and self.storage_client is not None:
            for name, uri in self.artifact_uris.items():
                storage_spec = ArtifactStorageSpecs(save_path=uri)
                self.storage_client.storage_spec = storage_spec
                self.artifacts[name] = load_record_artifact_from_storage(
                    storage_client=self.storage_client,
                    artifact_type=ARBITRARY_ARTIFACT_TYPE,
                )
            return None

        logger.info("No artifact uris associated with RunCard")
        return None

    @property
    def card_type(self) -> str:
        return CardType.RUNCARD.value


class ProjectCard(ArtifactCard):
    """
    Card containg project information
    """

    project_id: Optional[str] = None

    @validator("project_id", pre=True, always=True)
    def create_project_id(cls, value, values, **kwargs):  # pylint: disable=no-self-argument
        return f'{values["name"]}:{values["team"]}'

    def create_registry_record(self) -> RegistryRecord:
        """Creates a registry record for a project"""

        return ProjectRegistryRecord(**self.dict())

    @property
    def card_type(self) -> str:
        return CardType.PROJECTCARD.value