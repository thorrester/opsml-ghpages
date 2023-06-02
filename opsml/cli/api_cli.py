import pathlib
from typing import Dict, List, Union

import typer
from rich.console import Console
from rich.table import Table

from opsml.cli.utils import TRACKING_URI, ApiRoutes, CliApiClient, RegistryTableNames
from opsml.helpers.logging import ArtifactLogger

logger = ArtifactLogger.get_logger(__name__)

app = typer.Typer()
api_client = CliApiClient()


@app.command()
def download_model(
    name: str = typer.Option(default=None, help="Name of ModelCard"),
    team: str = typer.Option(default=None, help="Team associated with ModelCard"),
    version: str = typer.Option(default=None, help="Version of ModelCard"),
    uid: str = typer.Option(default=None, help="Uid of ModelCard"),
    onnx: bool = typer.Option(default=True, help="Whether to download onnx model or original model"),
    write_dir: str = typer.Option(default="./models"),
):
    """
    Downloads a model (onnx or original model) associated with a model card

    Args:
        name:
            Card name
        team:
            Team name
        version:
            Version to search
        uid:
            Uid of Card
        onnx:
            Whether to return onnx model or original model (no-onnx)
        write_dir:
            Directory to write to (required)

    Example:

        ```bash
        opsml-cli download-model --name "linear-reg" --team "mlops" --write-dir ".models" --no-onnx # original model
        opsml-cli download-model --name "linear-reg" --team "mlops" --write-dir ".models" --onnx # onnx model
        opsml-cli download-model --name "linear-reg" --team "mlops" --version "1.0.0" --write-dir "./models"
        ```

    """

    path = pathlib.Path(write_dir)
    path.mkdir(parents=True, exist_ok=True)

    metadata = api_client.download_metadata(
        payload={"name": name, "version": version, "team": team, "uid": uid},
        path=path,
    )

    if onnx:
        model_path = str(metadata.get("onnx_uri"))
    else:
        model_path = str(metadata.get("model_uri"))

    api_client.download_model(
        filepath=model_path,
        write_path=path,
    )


@app.command()
def download_model_metadata(
    name: str = typer.Option(default=None),
    team: str = typer.Option(default=None),
    version: str = typer.Option(default=None),
    uid: str = typer.Option(default=None),
    write_dir: str = typer.Option(default="./model"),
):
    """
    Downloads model metadata associated with a model card

    Args:
        name:
            Card name
        team:
            Team name
        version:
            Version to search
        uid:
            Uid of Card
        write_dir:
            Director to write to

    Example:

        ```bash
        opsml-cli download-model-metadata --name "linear-reg" --team "mlops" --write-dir ".models"
        opsml-cli download-model-metadata --name "linear-reg" --team "mlops" --version "1.0.0" --write-dir ".models"
        ```

    """

    path = pathlib.Path(write_dir)
    path.mkdir(parents=True, exist_ok=True)

    api_client.download_metadata(
        payload={"name": name, "version": version, "team": team, "uid": uid},
        path=path,
    )


console = Console()


@app.command()
def list_cards(
    registry: str = typer.Option(
        ..., help="Registry to search. Accepted values are 'model', 'data', 'pipeline', and 'run'"
    ),
    name: str = typer.Option(default=None),
    team: str = typer.Option(default=None),
    version: str = typer.Option(default=None),
    uid: str = typer.Option(default=None),
    tag_key: str = typer.Option(default=None),
    tag_value: str = typer.Option(default=None),
    max_date: str = typer.Option(default=None),
    limit: int = typer.Option(default=None),
):
    """
    Lists cards from a specific registry in table format

    Args:
        registry:
            Name of Card registry to search. Accepted values are 'model', 'data', 'pipeline', and 'run'
        name:
            Card name
        team:
            Team name
        version:
            Version to search
        uid:
            Uid of Card
        tag_key:
            Tag key
        tag_value:
            Tag value
        max_date:
            Max date to search
        limit:
            Max number of records to return

    Example:

        ```bash
        opsml-cli list-cards --name "linear-reg" --team "mlops" --max-date "2023-05-01"
        ```

    """

    registry_name = getattr(RegistryTableNames, registry.upper())

    if registry_name is None:
        raise ValueError(
            f"No registry found. Accepted values are 'model', 'data', 'pipeline', and 'run'. Found {registry}",
            registry,
        )

    if tag_key is not None:
        tags = {tag_key: tag_value}
    else:
        tags = None

    payload: Dict[str, Union[str, int, Dict[str, str]]] = {
        "name": name,
        "version": version,
        "team": team,
        "uid": uid,
        "limit": limit,
        "max_date": max_date,
        "tags": tags,
        "table_name": registry_name,
    }
    cards = api_client.list_cards(payload=payload)

    table = Table(title=f"{registry_name} cards")
    table.add_column("Name", no_wrap=True)
    table.add_column("Team")
    table.add_column("Date")
    table.add_column("User Email")
    table.add_column("Version")
    table.add_column("Tags")
    table.add_column("Uid", justify="right")

    for card in cards:
        table.add_row(
            card.get("name"),
            card.get("team"),
            card.get("date"),
            card.get("user_email"),
            card.get("version"),
            str(card.get("tags")),
            card.get("uid"),
        )
    console.print(table)


@app.command()
def get_model_metrics(
    name: str = typer.Option(default=None, help="Model name"),
    team: str = typer.Option(default=None, help="Team associated with model"),
    version: str = typer.Option(default=None, help="Model Version"),
    uid: str = typer.Option(default=None, help="Model uid"),
):
    """
    Prints metrics associated with a ModelCard

    Args:
        name:
            Card name
        team:
            Team name
        version:
            Version to search
        uid:
            Uid of Card

    Example:

        ```bash
        opsml-cli get-model-metrics --name "linear-reg" --team "mlops" --version "1.0.0"
        ```

    """
    if uid is None and not all(bool(val) for val in [name, team, version]):
        raise ValueError("A combination of (name, team, version) and uid must be supplied")

    payload: Dict[str, Union[str, int]] = {
        "name": name,
        "version": version,
        "team": team,
        "uid": uid,
    }

    metrics = api_client.get_metrics(payload=payload)

    table = Table(title="Model Metrics")
    table.add_column("Metric", no_wrap=True)
    table.add_column("Value")
    table.add_column("Step")
    table.add_column("Timestamp", justify="right")

    for _, metric_list in metrics.items():
        for metric in metric_list:
            table.add_row(
                str(metric.get("name")),
                str(metric.get("value")),
                str(metric.get("step", "None")),
                str(metric.get("timestamp", "None")),
            )
    console.print(table)


@app.command()
def download_data_profile(
    name: str = typer.Option(default=None, help="Data name"),
    team: str = typer.Option(default=None, help="Team associated with data"),
    version: str = typer.Option(default=None, help="Data Version"),
    uid: str = typer.Option(default=None, help="Data uid"),
    write_dir: str = typer.Option(default="./data_profile", help="Directory to write data profile to"),
):
    """
    Downloads a data profile from a DataCard

    Args:
        name:
            Card name
        team:
            Team name
        version:
            Card version
        uid:
            Card uid

    Returns
        HTML file

    Example:

        ```bash
        opsml-cli download-data-profile --name "linear-reg" --team "mlops" --version "1.0.0"
        ```
    """

    if uid is None and not all(bool(val) for val in [name, team, version]):
        raise ValueError("A combination of name, team, version and uid must be supplied")

    payload: Dict[str, Union[str, int, List[str]]] = {
        "name": name,
        "version": version,
        "team": team,
        "uid": uid,
    }

    path = pathlib.Path(write_dir)
    path.mkdir(parents=True, exist_ok=True)

    api_client.stream_data_file(
        path=ApiRoutes.DATA_PROFILE,
        write_path=path,
        payload=payload,
    )


@app.command()
def compare_data_profiles(
    name: str = typer.Option(default=None, help="Data name"),
    team: str = typer.Option(default=None, help="Team associated with data"),
    version: List[str] = typer.Option(default=None, help="List of data versions"),
    uid: List[str] = typer.Option(default=None, help="Data uid"),
    write_dir: str = typer.Option(default="./data_profile", help="Directory to write data profile to"),
):
    """
    Takes a list of version or uids and runs data profile comparisons

    Args:
        name:
            Card name
        team:
            Team name
        version:
            List of versions to compare
        uid:
            List of Uids to compare

    Returns
        HTML file

    Example:

        ```bash
        opsml-cli compare-data-profiles --name "linear-reg" --team "mlops" --version "1.0.0" --version "1.1.0"
        ```

    """
    if uid is None and not all(bool(val) for val in [name, team, version]):
        raise ValueError("A list of versions (with name and team) or uids is required")

    payload: Dict[str, Union[str, int, List[str]]] = {
        "name": name,
        "versions": version,
        "team": team,
        "uids": uid,
    }

    path = pathlib.Path(write_dir)
    path.mkdir(parents=True, exist_ok=True)

    api_client.stream_data_file(
        path=ApiRoutes.COMPARE_DATA,
        write_path=path,
        payload=payload,
    )


@app.command()
def launch_server():
    typer.launch(TRACKING_URI)


if __name__ == "__main__":
    app()