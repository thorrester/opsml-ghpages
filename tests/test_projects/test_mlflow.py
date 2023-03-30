import os
from typing import Any
import pandas as pd
from numpy.typing import NDArray
import pytest
from sklearn import pipeline
import lightgbm as lgb
import matplotlib.pyplot as plt
from opsml_artifacts import DataCard, ModelCard
from opsml_artifacts.registry.cards import cards
from opsml_artifacts.projects.mlflow import MlFlowProject, MlFlowProjectInfo
from opsml_artifacts.helpers.logging import ArtifactLogger
import shutil
from tests import conftest

logger = ArtifactLogger.get_logger(__name__)


def test_read_only(mlflow_project: MlFlowProject, sklearn_pipeline: tuple[pipeline.Pipeline, pd.DataFrame]) -> None:
    """ify that we can read artifacts / metrics / cards without making a run
    active."""

    info = MlFlowProjectInfo(name="test", team="test", user_email="user@test.com")
    with mlflow_project.run() as run:
        # Create metrics / params / cards
        run.log_metric(key="m1", value=1.1)
        run.log_param(key="m1", value="apple")
        model, data = sklearn_pipeline
        data_card = DataCard(
            data=data,
            name="pipeline_data",
            team="mlops",
            user_email="mlops.com",
        )
        run.register_card(card=data_card)
        model_card = ModelCard(
            trained_model=model,
            sample_input_data=data[0:1],
            name="pipeline_model",
            team="mlops",
            user_email="mlops.com",
            data_card_uid=data_card.uid,
        )
        run.register_card(card=model_card)
        info.run_id = run.run_id

    # Retrieve the run and load projects without making the run active (read only mode)
    proj = conftest.mock_mlflow_project(info)
    assert len(proj.metrics) == 1
    assert proj.metrics["m1"] == 1.1
    assert len(proj.params) == 1
    assert proj.params["m1"] == "apple"

    # Load model card
    loaded_card: ModelCard = proj.load_card(
        card_type="model",
        info=cards.CardInfo(name="pipeline_model", team="mlops", user_email="mlops.com"),
    )
    loaded_card.load_trained_model()
    assert loaded_card.uid is not None
    assert loaded_card.trained_model is not None

    # Load data card by uid
    loaded_data_card: DataCard = proj.load_card(
        card_type="data", info=cards.CardInfo(name="pipeline_data", team="mlops", uid=data_card.uid)
    )
    assert loaded_data_card.uid is not None
    assert loaded_data_card.uid == data_card.uid

    # Attempt to write register cards / log params / log metrics w/o the card being active
    with pytest.raises(ValueError):
        proj.register_card(data_card)
    with pytest.raises(ValueError):
        proj.log_param(key="param1", value="value1")
    with pytest.raises(ValueError):
        proj.log_metric(key="metric1", value=0.0)


def test_metrics(mlflow_project: MlFlowProject) -> None:
    # verify metrics require an ActiveRun

    with pytest.raises(ValueError) as ve:
        mlflow_project.log_metric(key="m1", value=1.1)

    assert ve.match("^ActiveRun")
    info = MlFlowProjectInfo(name="test", team="test", user_email="user@test.com")
    proj = conftest.mock_mlflow_project(info)
    with pytest.raises(ValueError) as ve:
        proj.log_metric(key="m1", value=1.0)
    assert ve.match("^ActiveRun")

    with proj.run() as run:
        run.log_metric(key="m1", value=1.1)

        info.run_id = run.run_id

    # open the project in read only mode (don't activate w/ context)
    proj = conftest.mock_mlflow_project(info)
    assert len(proj.metrics) == 1
    assert proj.metrics["m1"] == 1.1


def test_params(mlflow_project: MlFlowProject) -> None:
    # verify params require an ActiveRun
    with pytest.raises(ValueError) as ve:
        mlflow_project.log_param(key="m1", value=1.1)
    assert ve.match("^ActiveRun")

    info = MlFlowProjectInfo(name="test", team="test", user_email="user@test.com")
    with conftest.mock_mlflow_project(info).run() as run:
        run.log_param(key="m1", value="apple")
        info.run_id = run.run_id

    # open the project in read only mode (don't activate w/ context)
    proj = conftest.mock_mlflow_project(info)
    assert len(proj.params) == 1
    assert proj.params["m1"] == "apple"


def test_log_artifact() -> None:
    filename = "test.png"
    info = MlFlowProjectInfo(name="test", team="test", user_email="user@test.com")
    with conftest.mock_mlflow_project(info).run() as run:
        fig, ax = plt.subplots(nrows=1, ncols=1)  # create figure & 1 axis
        ax.plot([0, 1, 2], [10, 20, 3])
        fig.savefig("test.png")  # save the figure to file
        plt.close(fig)
        run.log_artifact(local_path=filename)
        run.add_tag("test_tag", "1.0.0")
        info.run_id = run.run_id

    proj = conftest.mock_mlflow_project(info)
    proj.download_artifacts()
    os.remove(filename)

    tags = proj.tags
    assert tags["test_tag"] == "1.0.0"


def test_register_load(
    mlflow_project: MlFlowProject,
    sklearn_pipeline: tuple[pipeline.Pipeline, pd.DataFrame],
) -> None:

    info = MlFlowProjectInfo(name="test", team="test", user_email="user@test.com")
    with mlflow_project.run() as run:
        model, data = sklearn_pipeline
        data_card = DataCard(
            data=data,
            name="pipeline_data",
            team="mlops",
            user_email="mlops.com",
        )
        run.register_card(card=data_card)

        model_card = ModelCard(
            trained_model=model,
            sample_input_data=data[0:1],
            name="pipeline_model",
            team="mlops",
            user_email="mlops.com",
            data_card_uid=data_card.uid,
        )
        run.register_card(card=model_card)

        ## Load model card
        loaded_model_card: ModelCard = run.load_card(
            card_type="model",
            info=cards.CardInfo(name="pipeline_model", team="mlops", user_email="mlops.com"),
        )
        loaded_model_card.load_trained_model()
        assert loaded_model_card.uid is not None
        assert loaded_model_card.trained_model is not None

        # Load data card by uid
        loaded_data_card: DataCard = run.load_card(
            card_type="data", info=cards.CardInfo(name="pipeline_data", team="mlops", uid=data_card.uid)
        )
        assert loaded_data_card.uid is not None
        assert loaded_data_card.uid == data_card.uid
        info.run_id = run.run_id
        model_uid = loaded_model_card.uid

    proj = conftest.mock_mlflow_project(info)
    loaded_card: ModelCard = proj.load_card(
        card_type="model",
        info=cards.CardInfo(uid=model_uid),
    )
    loaded_card.load_trained_model()


def test_lgb_model(
    mlflow_project: MlFlowProject,
    lgb_booster_dataframe: tuple[lgb.Booster, pd.DataFrame],
) -> None:

    info = MlFlowProjectInfo(name="test", team="test", user_email="user@test.com")
    with mlflow_project.run() as run:
        model, data = lgb_booster_dataframe
        data_card = DataCard(
            data=data,
            name="lgb_data",
            team="mlops",
            user_email="mlops.com",
        )
        run.register_card(card=data_card)

        model_card = ModelCard(
            trained_model=model,
            sample_input_data=data[0:1],
            name="lgb_model",
            team="mlops",
            user_email="mlops.com",
            data_card_uid=data_card.uid,
        )
        run.register_card(card=model_card)
        info.run_id = run.run_id

    proj = conftest.mock_mlflow_project(info)
    loaded_card: ModelCard = proj.load_card(
        card_type="model",
        info=cards.CardInfo(uid=model_card.uid),
    )
    loaded_card.load_trained_model()


def test_pytorch_model(
    mlflow_project: MlFlowProject,
    load_pytorch_resnet: tuple[Any, NDArray],
):
    # another run (pytorch)
    info = MlFlowProjectInfo(name="test", team="test", user_email="user@test.com")
    with mlflow_project.run() as run:
        model, data = load_pytorch_resnet
        data_card = DataCard(
            data=data,
            name="resnet_data",
            team="mlops",
            user_email="mlops.com",
        )
        run.register_card(card=data_card)

        model_card = ModelCard(
            trained_model=model,
            sample_input_data=data[0:1],
            name="resnet_model",
            team="mlops",
            user_email="mlops.com",
            data_card_uid=data_card.uid,
        )
        run.register_card(card=model_card)
        info.run_id = run.run_id

    proj = conftest.mock_mlflow_project(info)
    loaded_card: ModelCard = proj.load_card(
        card_type="model",
        info=cards.CardInfo(uid=model_card.uid),
    )
    loaded_card.load_trained_model()


def test_tf_model(
    mlflow_project: MlFlowProject,
    load_transformer_example: tuple[Any, NDArray],
):
    # another run (pytorch)
    info = MlFlowProjectInfo(name="test", team="test", user_email="user@test.com")
    with mlflow_project.run() as run:
        model, data = load_transformer_example
        data_card = DataCard(
            data=data,
            name="transformer_data",
            team="mlops",
            user_email="mlops.com",
        )
        run.register_card(card=data_card)

        model_card = ModelCard(
            trained_model=model,
            sample_input_data=data[0:1],
            name="transformer_model",
            team="mlops",
            user_email="mlops.com",
            data_card_uid=data_card.uid,
        )
        run.register_card(card=model_card)
        info.run_id = run.run_id

    proj = conftest.mock_mlflow_project(info)
    loaded_card: ModelCard = proj.load_card(
        card_type="model",
        info=cards.CardInfo(uid=model_card.uid),
    )
    loaded_card.load_trained_model()
