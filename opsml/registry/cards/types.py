from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Union


@dataclass
class ModelCardUris:
    modelcard_uri: Optional[str] = None
    trained_model_uri: Optional[str] = None
    model_metadata_uri: Optional[str] = None
    sample_data_uri: Optional[str] = None


@dataclass
class Metric:
    name: str
    value: Union[float, int]
    step: Optional[int] = None
    timestamp: Optional[int] = None


@dataclass
class Param:
    name: str
    value: Union[float, int, str]


METRICS = Dict[str, List[Metric]]
PARAMS = Dict[str, List[Param]]


@dataclass
class StoragePath:
    uri: str


@dataclass
class CardInfo:

    """
    Class that holds info related to an Artifact Card

    Args:
        name:
            Name of card
        team:
            Team name
        user_email:
            Email
        uid:
            Unique id of card
        version:
            Version of card
    """

    name: Optional[str] = None
    team: Optional[str] = None
    user_email: Optional[str] = None
    uid: Optional[str] = None
    version: Optional[str] = None


class CardType(str, Enum):
    DATACARD = "data"
    RUNCARD = "run"
    MODELCARD = "model"
    PIPELINECARD = "pipeline"
    PROJECTCARD = "project"


class PipelineCardArgs(str, Enum):
    DATA_UIDS = "datacard_uids"
    MODEL_UIDS = "modelcard_uids"
    RUN_UIDS = "runcard_uids"


class RunCardArgs(str, Enum):
    DATA_UID = "datacard_uid"
    MODEL_UIDS = "modelcard_uids"
    PIPELINE_UID = "pipelinecard_uid"


NON_PIPELINE_CARDS = [card.value for card in CardType if card.value not in ["pipeline", "project"]]