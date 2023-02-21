from click.testing import CliRunner
from opsml_artifacts.scripts.load_model_card import load_model_card_to_file
from unittest.mock import patch, MagicMock


def test_load_model_card_version(mock_model_cli_loader, test_model_card):

    with patch.multiple(
        "opsml_artifacts.registry.sql.registry.CardRegistry",
        load_card=MagicMock(return_value=test_model_card),
    ):
        args1 = ["--name", "driven_drop_off_predictor", "--team", "SPMS", "--version", "2"]
        args2 = ["--name", "driven_drop_off_predictor", "--team", "SPMS", "--version", "2", "--version", "3"]
        args3 = ["--name", "driven_drop_off_predictor", "--uid", "blah"]

        for arg_list in [args1, args2, args3]:
            runner = CliRunner()
            result = runner.invoke(load_model_card_to_file, arg_list)
            assert result.exit_code == 0