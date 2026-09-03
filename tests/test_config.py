"""The terminal server 'decrypt' flag, from configuration file to backup."""

import json
from pathlib import Path

import pytest

from dls_backup_bl.config import BackupsConfig, TerminalServer, to_bool
from dls_backup_bl.defaults import Defaults, TsConfigFormat

OLD_STYLE = {
    "motion_controllers": [
        {"controller": "BL16I-MO-BRICK-01", "port": 1025, "server": "172.23.240.97"}
    ],
    "terminal_servers": [{"server": "ts-01", "ts_type": "moxa"}],
    "zebras": [{"Name": "BL16I-EA-ZEBRA-01"}],
}


def test_the_editor_can_tell_that_decrypt_is_a_boolean():
    """The GUI reads these to pick a checkbox rather than a text box.

    It cannot be tested through Qt here - PyQt5 needs libGL - so pin the
    introspection the editor actually branches on.
    """
    assert TerminalServer.types()["decrypt"] is bool
    assert list(TerminalServer.keys()) == ["server", "ts_type", "decrypt"]
    # the editor pairs my_types() with the tab order taken from keys()
    assert BackupsConfig.my_types()[1] is TerminalServer
    assert list(BackupsConfig.keys())[1] == "terminal_servers"


def write(tmp_path: Path, data: dict) -> Path:
    f = tmp_path / "backup.json"
    f.write_text(json.dumps(data))
    return f


def test_a_config_written_before_the_flag_existed_still_loads(tmp_path: Path):
    """Every deployed configuration file predates this field."""
    config = BackupsConfig.from_json(write(tmp_path, OLD_STYLE))

    assert config.terminal_servers[0].server == "ts-01"
    assert config.terminal_servers[0].decrypt is False


def test_the_flag_survives_a_save_and_reload(tmp_path: Path):
    """save() sorts the keys, so this only works if reads are by keyword."""
    config = BackupsConfig.from_json(write(tmp_path, OLD_STYLE))
    config.terminal_servers[0].decrypt = True
    config.save(tmp_path / "backup.json")

    written = json.loads((tmp_path / "backup.json").read_text())
    assert list(written["terminal_servers"][0]) == ["decrypt", "server", "ts_type"]

    reloaded = BackupsConfig.from_json(tmp_path / "backup.json")
    assert reloaded.terminal_servers[0].decrypt is True
    assert reloaded.terminal_servers[0].server == "ts-01"
    assert reloaded.motion_controllers[0].controller == "BL16I-MO-BRICK-01"


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("True", True),
        ("yes", True),
        ("1", True),
        ("false", False),
        ("no", False),
        ("", False),
    ],
)
def test_a_hand_edited_flag_is_read_sensibly(
    tmp_path: Path, written: object, expected: bool
):
    """bool("false") is True, which is exactly the trap to avoid."""
    assert to_bool(written) is expected

    # and through the real path, so the attrs converter is proven wired up
    data = dict(OLD_STYLE)
    data["terminal_servers"] = [
        {"server": "ts-01", "ts_type": "moxa", "decrypt": written}
    ]
    config = BackupsConfig.from_json(write(tmp_path, data))
    assert config.terminal_servers[0].decrypt is expected


@pytest.mark.parametrize(
    ("decrypt_flag", "decrypt_only_flag", "per_device", "expected"),
    [
        # no command line override: the device's own setting decides
        (False, False, False, TsConfigFormat.encrypted),
        (False, False, True, TsConfigFormat.decrypted),
        # --decrypt / --decrypt-only apply to the whole run and win
        (True, False, False, TsConfigFormat.both),
        (False, True, False, TsConfigFormat.decrypted),
        (True, False, True, TsConfigFormat.both),
    ],
)
def test_the_command_line_overrides_the_per_device_flag(
    decrypt_flag: bool,
    decrypt_only_flag: bool,
    per_device: bool,
    expected: TsConfigFormat,
):
    override = None
    if decrypt_only_flag:
        override = TsConfigFormat.decrypted
    elif decrypt_flag:
        override = TsConfigFormat.both

    defaults = Defaults(domain="TEST", ts_config_format=override)
    resolved = defaults.ts_config_format or (
        TsConfigFormat.decrypted if per_device else TsConfigFormat.encrypted
    )
    assert resolved is expected
