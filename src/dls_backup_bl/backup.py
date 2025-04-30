import argparse
import logging
import signal
import smtplib
from enum import Enum
from logging import getLogger
from multiprocessing.pool import ThreadPool
from pathlib import Path

from . import __version__
from .brick import Brick
from .config import BackupsConfig
from .defaults import Defaults
from .importjson import import_json
from .repository import commit_changes, compare_changes, restore_positions
from .tserver import backup_terminal_server
from .zebra import backup_zebra

log = getLogger(__name__)

empty_message = """

BACKUP ABORTED

The configuration file contains no devices for backup.
Please import the dls-pmac-analyse cfg file with --import-cfg and / or
use dls-edit-backup.py to complete the device configuration.
"""
setup_message = """

BACKUP NOT SET UP

There is no backup area set up for this beamline.

Please import the dls-pmac-analyse cfg file with --import-cfg and / or
use dls-backup-gui.py to complete the device configuration.
"""


class Positions(Enum):
    save = "save"
    restore = "restore"
    compare = "compare"


# noinspection PyBroadException
class BackupBeamline:
    def __init__(self):
        self.args = None

        self.json_data: object = None
        self.thread_pool: ThreadPool = None
        self.defaults: Defaults = None
        self.config: BackupsConfig = None

        self.motor_controllers: list = None
        self.terminal_servers: list = None
        self.zebras: list = None
        self.email: str = None

    def setup_logging(self, level: str):
        """
        set up 3 logging handlers:
            A file logger to record debug information
            A file logger to record Critical messages
            A console logger
        The critical logger will be stored in the repo for a record of
        success/failure of each backup
        The debug logger will be in .gitignore so can be used to diagnose
        the most recent backup only
        The console logger level is configurable at the command line
        """

        # basic config sets up the debugging log file
        msg_f = "%(asctime)s %(levelname)-8s %(message)s        (%(name)s)"
        date_f = "%y-%m-%d %H:%M:%S"
        logging.basicConfig(
            level=logging.DEBUG,
            format=msg_f,
            datefmt=date_f,
            filename=str(self.defaults.log_file),
            filemode="w",
        )

        # critical log file for emails and record of activity
        critical = logging.FileHandler(
            filename=str(self.defaults.critical_log_file), mode="w"
        )
        critical.setLevel(logging.CRITICAL)

        # console log file for immediate feedback
        numeric_level = getattr(logging, level.upper(), 0)

        # suppress verbose logging in dependent libraries
        if numeric_level > logging.DEBUG:
            logging.getLogger("dls_pmacanalyse").setLevel(logging.ERROR)
            logging.getLogger("dls_pmaclib").setLevel(logging.ERROR)

        # control logging for all modules in this package to the console
        console = logging.StreamHandler()
        # set a format which is simpler for console use
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-10s %(message)s      (%(name)s)",
            datefmt="%y-%m-%d %H:%M:%S",
        )
        # tell the handler to use this format
        console.setFormatter(formatter)
        console.setLevel(numeric_level)

        # add the extra handlers to the root logger
        root_logger = logging.getLogger()
        root_logger.addHandler(console)
        root_logger.addHandler(critical)

    def parse_args(self):
        # Setup an argument Parser
        parser = argparse.ArgumentParser(
            description="Backup PMAC & GeoBrick motor controllers, terminal "
            "servers, and Zebra boxes. "
            "RECOMMENDATION: run this program from a "
            "workstation on the beamline to be backed up and "
            "provide NO arguments except --email (see below for "
            "defaults).",
            usage="%(prog)s [options]",
        )
        parser.add_argument(
            "-v",
            "--version",
            action="version",
            version=__version__,
        )
        parser.add_argument(
            "-i",
            "--import-cfg",
            action="store",
            help="import brick configuration from a "
            "dls-pmac-analyse configuration file.",
        )
        parser.add_argument(
            "-b",
            "--beamline",
            action="store",
            help="Name of the beamline to backup. "
            "The format is 'i16' or 'b07'. Defaults to "
            "the current beamline",
        )
        parser.add_argument(
            "--domain",
            action="store",
            help="When BLXXY is not appropriate, use domain for"
            " the backup folder name. e.g. --domain ME01D",
        )
        parser.add_argument(
            "--dir",
            action="store",
            help="Directory to save backups to. Defaults to"
            "/dls_sw/work/motion/Backups/BLXXY",
        )
        parser.add_argument(
            "-j",
            action="store",
            dest="json_file",
            help="JSON file of devices to be backed up. "
            "Defaults to DIR/BLXXY-backup.json",
        )
        parser.add_argument(
            "-r",
            "--retries",
            action="store",
            type=int,
            default=4,
            help="Number of times to attempt backup. Defaults to 4",
        )
        parser.add_argument(
            "-t",
            "--threads",
            action="store",
            type=int,
            default=Defaults.threads,
            help="Number of processor threads to use (Number "
            "of simultaneous backups). Defaults to"
            "10",
        )
        parser.add_argument(
            "-e",
            "--email",
            action="store",
            help="Email address to send backup reports to.",
        )
        parser.add_argument(
            "-l",
            "--log-level",
            action="store",
            default="info",
            help="Set logging to error, warning, info, debug",
        )
        parser.add_argument(
            "-d",
            "--devices",
            action="store",
            nargs="+",
            help="only backup the listed named device",
        )
        parser.add_argument(
            "-p",
            "--positions",
            action="store",
            # todo make this neat, using Positions Enum
            type=str,
            choices=["save", "restore", "compare"],
            help="save and restore motor positions",
        )
        parser.add_argument(
            "--folder",
            action="store_true",
            help="report the motion backup folder that the tool will use.",
        )

        # Parse the command line arguments
        self.args = parser.parse_args()

    def do_geobricks(self, pmacs: list[str] | None = None):
        count = 0
        # Go through every motor controller listed in JSON file
        for motor_controller in self.config.motion_controllers:
            # Pull out the controller details
            controller = motor_controller.controller
            server = motor_controller.server
            port = motor_controller.port

            # Check whether a terminal server is used or not
            uses_ts = int(port) != 1025

            # Add a backup job to the pool
            args = (controller, server, port, uses_ts, self.defaults)

            if not pmacs or any((i in controller) for i in pmacs):
                count += 1
                b = Brick(*args)
                if self.args.positions == "save" or self.args.positions == "compare":
                    func = b.backup_positions
                elif self.args.positions == "restore":
                    func = b.restore_positions
                else:
                    func = b.backup_controller

                self.thread_pool.apply_async(func)
        return count

    def do_t_servers(self, servers: list[str] | None = None):
        count = 0
        # Go through every terminal server listed in JSON file
        for terminal_server in self.config.terminal_servers:
            # Pull out the server details
            server = terminal_server.server
            args = (server, terminal_server.ts_type, self.defaults)
            # allows substring match of any devices entry against this server
            if not servers or any((i in server) for i in servers):
                count += 1
                # Add a backup job to the pool
                self.thread_pool.apply_async(backup_terminal_server, args)
        return count

    def do_zebras(self, zebras: str | None = None):
        count = 0
        # Go through every zebra listed in JSON file
        for z in self.config.zebras:
            # Pull out the PV name detail
            name = z.Name
            # Add a backup job to the pool
            args = (name, self.defaults)

            # allows substring match of any devices entry against this server
            if not zebras or any((i in name) for i in zebras):
                count += 1
                # call zebra backup in main thread since it uses cothread
                backup_zebra(*args)
        return count

    def sort_log(self):
        # Order the results alphabetically to make them easier to read
        with self.defaults.critical_log_file.open("r") as f:
            sorted_text = sorted(f.readlines())

        with self.defaults.critical_log_file.open("w") as f:
            f.writelines(sorted_text)

    def send_email(self):
        with self.defaults.critical_log_file.open("r") as f:
            e_text = f.read()

        if self.email is None:
            log.info("Email address not supplied")
            return

        try:
            e_from = f"From: {self.defaults.diamond_sender}\r\n"
            e_to = f"To: {self.email}\r\n"
            e_subject = f"Subject: {self.defaults.beamline} Backup Report\r\n\r\n"
            msg = e_from + e_to + e_subject + e_text
            mail_server = smtplib.SMTP(self.defaults.diamond_smtp)
            mail_server.sendmail(self.defaults.diamond_sender, self.email, msg)
            mail_server.quit()
            log.critical("Sent Email report")
        except BaseException:
            msg = "Sending Email FAILED"
            log.critical(msg)
            log.debug(msg, exc_info=True)

    def check_restore(self):
        print(
            "\nAre you sure? This will restore the most recent commit "
            "and\noverwrite the motor positions on all specified pmacs (Y/N)"
        )
        reply = input()
        if reply[0].lower() != "y":
            exit(0)
        restore_positions(self.defaults)

    def do_backups(self):
        if self.args.positions == "restore":
            self.check_restore()
        else:
            log.info(
                "START OF BACKUP for beamline %s to %s",
                self.defaults.beamline,
                self.defaults.backup_folder,
            )

        # Initiate a thread pool with the desired number of threads
        self.thread_pool = ThreadPool(self.args.threads)

        # queue threads for each type of backup
        total = self.do_geobricks(pmacs=self.args.devices)
        if self.args.positions is None:
            total += self.do_t_servers(servers=self.args.devices)
            total += self.do_zebras(zebras=self.args.devices)

        # Wait for completion of all backup threads
        self.thread_pool.close()
        self.thread_pool.join()

        # finish up
        self.sort_log()
        if total == 0:
            log.critical("Nothing was backed up (incorrect --devices argument?)")

        if not self.args.positions:
            commit_changes(self.defaults, do_positions=False)
        elif self.args.positions == "save":
            commit_changes(self.defaults, do_positions=True)
        elif self.args.positions == "compare":
            compare_changes(self.defaults, pmacs=self.args.devices)

        print("\n--------- Summary ----------")
        with self.defaults.critical_log_file.open() as f:
            print(f.read())

        if self.args.positions:
            print("The following command reviews the position files")
            print(
                f"more {self.defaults.motion_folder}/*{self.defaults.positions_suffix}"
            )

    def cancel(self, sig, frame):
        log.critical("Cancelled by the user")
        self.send_email()
        exit(1)

    def main(self):
        self.parse_args()
        self.email = self.args.email

        # catch CTRL-C
        signal.signal(signal.SIGINT, self.cancel)

        self.defaults = Defaults(
            self.args.beamline,
            self.args.dir,
            self.args.json_file,
            self.args.retries,
            domain=self.args.domain,
        )

        if self.args.folder:
            print(self.defaults.backup_folder)
        elif self.args.import_cfg:
            self.defaults.check_folders()
            self.setup_logging(self.args.log_level)
            import_file = Path(self.args.import_cfg)
            import_json(import_file, self.defaults.config_file)
        elif self.defaults.config_file.exists():
            self.defaults.check_folders()
            self.setup_logging(self.args.log_level)
            self.config = BackupsConfig.from_json(self.defaults.config_file)
            if self.config.count_devices() == 0:
                print("\n\n" + empty_message)
            else:
                self.do_backups()
        else:
            print("\n\n" + setup_message)


def main():
    BackupBeamline().main()
