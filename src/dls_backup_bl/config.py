import json
import sys
from enum import IntEnum
from logging import getLogger
from pathlib import Path

import attr

log = getLogger(__name__)


# The classes MotorController, TerminalServer, Zebra, TsType
# define the schema of the configuration file and the object graph that
# represents the configuration in memory the class BackupsConfig is the
# root of those representations and provides load and save methods


class JsonAbleDictionaryTuple:
    def __getitem__(self, item):
        return self.__dict__[item]

    @classmethod
    def keys(cls):
        return cls.__annotations__.keys()

    @classmethod
    def types(cls):
        """The declared type of each field, so an editor can choose a widget."""
        return cls.__annotations__

    def items(self):
        return self.__dict__.items()


@attr.s(auto_attribs=True)
class MotorController(JsonAbleDictionaryTuple):
    controller: str
    port: int = attr.ib(converter=int)
    server: str


def to_bool(value: object) -> bool:
    """Accept the several ways a flag can arrive from JSON or the editor.

    The GUI gives a real bool, but a hand edited configuration file may well
    say ``"true"`` or ``"yes"``, and silently reading those as True - which
    ``bool("false")`` also does - would be worse than useless.
    """
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "y", "1", "on")
    return bool(value)


class TsType(IntEnum):
    moxa = 0
    acs = 1
    old_acs = 2


@attr.s(auto_attribs=True)
class TerminalServer(JsonAbleDictionaryTuple):
    server: str
    ts_type: TsType
    #: write this device's backup as a readable .ini instead of an encrypted
    #: .dec. Moxa only, and overridden by --decrypt / --decrypt-only.
    decrypt: bool = attr.ib(default=False, converter=to_bool)


@attr.s(auto_attribs=True)
class Zebra(JsonAbleDictionaryTuple):
    Name: str


@attr.s(auto_attribs=True)
class BackupsConfig(JsonAbleDictionaryTuple):
    motion_controllers: list[MotorController]
    terminal_servers: list[TerminalServer]
    zebras: list[Zebra]

    @classmethod
    def my_types(cls):
        return [MotorController, TerminalServer, Zebra]

    @classmethod
    def empty(cls):
        return cls([], [], [])

    # noinspection PyBroadException
    @staticmethod
    def from_json(json_file: Path):
        try:
            with json_file.open() as f:
                raw_items = json.loads(f.read())
            # by keyword, not position: save() sorts the keys, so field order
            # in the file does not match declaration order, and an omitted
            # optional field must fall back to its default
            m = [MotorController(**i) for i in raw_items["motion_controllers"]]
            t = [TerminalServer(**i) for i in raw_items["terminal_servers"]]
            z = [Zebra(**i) for i in raw_items["zebras"]]
        except Exception:
            msg = "JSON file missing or invalid"
            log.debug(msg, exc_info=True)
            log.error(msg)
            sys.exit()
        return BackupsConfig(m, t, z)

    # noinspection PyBroadException
    def save(self, json_file: Path):
        try:
            with json_file.open("w") as f:
                json.dump(self, f, cls=ComplexEncoder, sort_keys=True, indent=4)
        except Exception:
            msg = "Unable to save configuration file"
            log.debug(msg, exc_info=True)
            log.error(msg)
            raise

    def dumps(self):
        return json.dumps(self, cls=ComplexEncoder, sort_keys=True, indent=4)

    def count_devices(self):
        return (
            len(self.motion_controllers) + len(self.terminal_servers) + len(self.zebras)
        )


class ComplexEncoder(json.JSONEncoder):
    def default(self, obj):
        if hasattr(obj, "__dict__"):
            return obj.__dict__
        else:
            return json.JSONEncoder.default(self, obj)
