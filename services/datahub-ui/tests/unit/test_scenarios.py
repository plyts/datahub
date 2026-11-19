import pytest

from datahub_ui.scenarios import FAULTS, fault_run_config, merge_run_config


def test_normal_data_adds_no_config():
    assert fault_run_config("none") == {}


@pytest.mark.parametrize(
    ("fault", "op"),
    [
        ("negative_amounts", "raw__orders"),
        ("volume_spike", "raw__orders"),
        ("source_flaky", "raw__orders"),
        ("null_emails", "raw__customers"),
    ],
)
def test_fault_targets_the_right_ingestion_asset(fault, op):
    assert fault_run_config(fault) == {"ops": {op: {"config": {"fault": fault}}}}


def test_unknown_fault():
    with pytest.raises(ValueError):
        fault_run_config("meteor")


def test_merge_keeps_user_config():
    user = {"ops": {"raw__landing_files": {"config": {"files": ["a.csv"]}}}}
    merged = merge_run_config(user, fault_run_config("negative_amounts"))
    assert merged["ops"]["raw__landing_files"] == {"config": {"files": ["a.csv"]}}
    assert merged["ops"]["raw__orders"] == {"config": {"fault": "negative_amounts"}}
    assert set(FAULTS) >= {"none", "negative_amounts"}
