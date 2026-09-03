import shutil
import tempfile
from enum import Enum
from os import environ
from pathlib import Path

from .nport import DEFAULT_PSK


class TsConfigFormat(Enum):
    """What to write for each Moxa terminal server configuration backup."""

    #: only the encrypted export, as the tool has always done
    encrypted = "encrypted"
    #: the encrypted export and a decrypted, readable copy beside it
    both = "both"
    #: only the decrypted copy
    decrypted = "decrypted"


class Defaults:
    """
    manage default values for paths and other settings
    """

    # public fixed defaults
    diamond_smtp: str = "outbox.rl.ac.uk"
    diamond_sender: str = "backup_bl@diamond.ac.uk"
    root_folder = Path("/dls_sw/work/motion/Backups/")
    positions_suffix = "_positions.pmc"
    positions_file = "positions_comparison.txt"
    threads: int = 10

    motion_subfolder = Path("MotionControllers/")
    _zebra_folder = Path("Zebras/")
    _ts_folder = Path("TerminalServers/")
    _config_file_suffix = Path("backup.json")
    _log_file = Path("backup_detail.log")
    _critical_log_file = Path("backup.log")
    _retries: int = 4

    def __init__(
        self,
        beamline: str | None = None,
        backup_folder: Path | None = None,
        config_file: Path | None = None,
        retries: int = 0,
        config_file_only: bool = False,
        domain: str | None = None,
        ts_config_format: TsConfigFormat | None = None,
        ts_psk: str = DEFAULT_PSK,
    ):
        """
         Create an object to hold important file paths.
         Pass in command line parameters which override defaults:

         :param beamline: the name of the beamline in the form 'i16'
         :param backup_folder: override the default location for backups
         :param config_file: where to read config if not from default
         :param retries: number of backup retries on failure
         :param config_file_only: if this is true do not require a valid
                beamline setting when config_file is supplied. this is for
                use by the GUI
        :param domain: override the beamline name to give no BLXXY folder name
        :param ts_config_format: run level override for how Moxa terminal
               server backups are saved. None leaves it to each device's own
               'decrypt' setting in the configuration file
        :param ts_psk: configuration pre-shared key used to decrypt them
        """
        self._retries = retries if int(retries) > 0 else Defaults._retries
        self.ts_config_format = ts_config_format
        self.ts_psk = ts_psk
        self.temp_dir: Path = Path(tempfile.mkdtemp())

        if config_file_only and config_file is not None:
            self._beamline = ""
        elif domain:
            self._beamline = domain
        else:
            self.get_beamline(beamline)

        if backup_folder:
            self._backup_folder = Path(backup_folder)
        else:
            self._backup_folder = Defaults.root_folder / self._beamline

        if config_file:
            self._config_file = Path(config_file)
        else:
            name = Path(f"{self._beamline}-{Defaults._config_file_suffix}")
            self._config_file = self._backup_folder / name

    def __del__(self):
        shutil.rmtree(str(self.temp_dir), ignore_errors=True)

    def get_beamline(self, short_form):
        """
        converts the short form of beamline name found environment
        variable ${BEAMLINE} e.g. i09-1 to a PV prefix e.g. BL09J
        which is used in the Backup folder name.

        This is (reasonably) deterministic but there are at least
        one exceptions to the rules.
        """
        try:
            if short_form is None:
                short_form = environ.get("BEAMLINE")
                assert short_form is not None

            # special cases
            if short_form == "i02-2":
                self._beamline = "BL02I"
            else:
                bl_letter = short_form[0].upper()
                bl_nums = short_form[1:].split("-")
                if len(bl_nums) == 2:
                    bl_letter = chr(ord(bl_letter) + 1)
                bl_no = int(bl_nums[0])
                self._beamline = f"BL{bl_no:02d}{bl_letter}"
        except (IndexError, AssertionError, ValueError, TypeError):
            print(
                "\n\nBeamline must be of the form i16 or i09-1."
                "\nCheck environment variable ${BEAMLINE} or use argument "
                "--beamline (-b)"
            )
            exit(1)

    def check_folders(self):
        self.motion_folder.mkdir(parents=True, exist_ok=True)
        self.zebra_folder.mkdir(parents=True, exist_ok=True)
        self.ts_folder.mkdir(parents=True, exist_ok=True)
        if not self.config_file.exists():
            if str(self.config_file).startswith(str(self.root_folder)):
                # create an empty json config file
                with self.config_file.open("w") as f:
                    f.write(Defaults.json)

    @property
    def beamline(self) -> str:
        return self._beamline

    @property
    def backup_folder(self) -> Path:
        return self._backup_folder

    @property
    def config_file(self) -> Path:
        return self._config_file

    @property
    def motion_folder(self) -> Path:
        return self._backup_folder / Defaults.motion_subfolder

    @property
    def zebra_folder(self) -> Path:
        return self._backup_folder / Defaults._zebra_folder

    @property
    def ts_folder(self) -> Path:
        return self._backup_folder / Defaults._ts_folder

    @property
    def retries(self) -> int:
        return self._retries

    @property
    def log_file(self) -> Path:
        return self._backup_folder / Defaults._log_file

    @property
    def critical_log_file(self) -> Path:
        return self._backup_folder / Defaults._critical_log_file

    json = """{
    "motion_controllers": [ ],
    "terminal_servers": [ ],
    "zebras": [ ]
    }"""
