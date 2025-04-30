import math
import re
import shutil
import telnetlib
from decimal import Decimal
from logging import getLogger

from dls_pmacanalyse.errors import PmacReadError
from dls_pmacanalyse.globalconfig import GlobalConfig
from dls_pmacanalyse.pmac import Pmac
from dls_pmaclib.dls_pmacremote import (
    PmacEthernetInterface,
    PmacTelnetInterface,
    RemotePmacInterface,
)

from dls_backup_bl.defaults import Defaults

log = getLogger(__name__)


# noinspection PyBroadException
class Brick:
    def __init__(
        self, controller: str, server: str, port: int, t_serv: bool, defaults: Defaults
    ):
        self.controller = controller
        self.server = server
        self.port = port
        self.t_serv = t_serv
        self.defaults = defaults
        self.pti: RemotePmacInterface = None
        self.analyse: Pmac = None
        self.desc = f"pmac {controller} at {server}:{port}"
        n = f"{self.controller}{defaults.positions_suffix}"
        self.positions_file = self.defaults.motion_folder / n
        f_name = f"{self.controller}{defaults.positions_suffix}"
        self.f_name = f"{self.controller}.pmc"
        self.file = self.defaults.motion_folder / f_name

    # this destructor saves having loads of disconnect logic in the exception
    # handlers. Without it the test brick quickly ran out of connections
    def __del__(self):
        if self.pti:
            self.pti.disconnect()

    def _check_connection(self):
        for _attempt_num in range(self.defaults.retries):
            try:
                t = telnetlib.Telnet()
                t.open(self.server, self.port, timeout=2)
                t.close()
            except BaseException:
                msg = f"connection attempt failed for {self.desc}"
                log.debug(msg, exc_info=True)
            else:
                break
        else:
            msg = f"ERROR: {self.desc} is offline"
            log.critical(msg)
            return False
        return True

    def _connect_analyse(self):
        try:
            analyse_config = GlobalConfig()
            self.analyse = analyse_config.createOrGetPmac(self.controller)
            self.analyse.setProtocol(self.server, self.port, self.t_serv)
            # None means that readHardware will decide for itself
            self.analyse.setGeobrick(None)
        except BaseException:
            log.error(f"pmac_analyse connection failed for {self.desc}")

    def _connect_direct(self):
        # make sure the pmac config we have backed up is also saved
        # on the brick itself
        try:
            if self.t_serv:
                self.pti = PmacTelnetInterface(verbose=False)
            else:
                self.pti = PmacEthernetInterface(verbose=False)
            self.pti.setConnectionParams(self.server, self.port)
            self.pti.connect()
        except BaseException:
            log.error(f"connection failed for {self.desc}")

    def backup_positions(self):
        log.info(f"Getting motor positions for {self.desc}.")
        if not self._check_connection():
            return

        for attempt_num in range(self.defaults.retries):
            try:
                self._connect_direct()
                axes = self.pti.getNumberOfAxes()

                pmc = []
                for axis in range(axes):
                    # store position and homed state
                    for cmd in [f"M{axis + 1}62", f"M{axis + 1}45"]:
                        (return_str, status) = self.pti.sendCommand(cmd)
                        if not status:
                            raise PmacReadError(return_str)
                        pmc.append(f"{cmd} = {return_str[:-2]}\n")

                for plc in range(1, 33):
                    cmd = f"M{plc + 5000}"
                    (return_str, status) = self.pti.sendCommand(cmd)
                    if not status:
                        raise PmacReadError(return_str)
                    pmc.append(f"{cmd} = {return_str[:-2]}\n")
                self.pti.disconnect()
                self.pti = None

                with self.positions_file.open("w") as f:
                    f.writelines(pmc)

                log.critical(f"SUCCESS: positions retrieved for {self.desc}")
            except Exception:
                num = attempt_num + 1
                msg = (
                    f"ERROR: position retrieval for {self.desc} failed on "
                    f"attempt {num} of {self.defaults.retries}"
                )
                log.debug(msg, exc_info=True)
                log.error(msg)
                continue
            break
        else:
            msg = (
                f"ERROR: {self.desc} all {self.defaults.retries} "
                f"attempts to save positions failed"
            )
            log.critical(msg)

    # only send the positions (filter out running PLCs and homed state)
    restore_commands = re.compile("M[0-9]{1,2}62 = -?[0-9]+")

    def restore_positions(self):
        log.info(f"Sending motor positions for {self.desc}.")

        positionSFList = self.getPositionSF(self.controller, self.defaults)

        for attempt_num in range(self.defaults.retries):
            try:
                self._connect_direct()
                with self.positions_file.open("r") as f:
                    # munge into format for sendSeries and only send commands
                    # that match restore_commands
                    lines = f.readlines()

                    # In some cases Mx62 cannot be written directly to the controller as the maximum
                    # acceptable value appears to be 2^35. Instead the value of Mx62 is calculated as
                    # a factor of 1/(ix08*32) and written to the pmac as an expression
                    for i, line in enumerate(lines):
                        newL = line.split("=")
                        newL = [a.strip() for a in newL]
                        if "62" in newL[0]:
                            # Determine axis number M variable is related to
                            if newL[0] == "M" or "m":
                                axisNo = int(int(newL[0][1:]) / 100)
                                scaling_factor = f"{1 / positionSFList[axisNo]}"
                                # The controller can't parse values in scientific notation (eg 3.69e-05)
                                # These need replacing with their decimal form equivalent
                                scaling_factor = Decimal(scaling_factor)
                                newL[1] = int(newL[1]) * (1 / positionSFList[axisNo])
                                newL[1] = f"{int(newL[1])}/{scaling_factor}"
                                lines[i] = f"{newL[0]} = {newL[1]}\n"

                    pmc = [
                        (n + 1, line[:-1])
                        for n, line in enumerate(lines)
                        if re.search(self.restore_commands, line)
                    ]
                # send ctrl K to kill all axes (otherwise the servo loop
                # will fight the change of position)
                self.pti.sendCommand("\u000b")
                for success, _line, cmd, response in self.pti.sendSeries(pmc):
                    if not success:
                        log.critical(
                            f"ERROR: command '{cmd}' failed for {self.desc} ("
                            f"{response})"
                        )

                log.critical(f"SUCCESS: positions restored for {self.desc}")
            except BaseException:
                msg = (
                    f"ERROR: {self.desc} position restore failed on "
                    f"attempt {attempt_num + 1} of {self.defaults.retries}"
                )
                log.debug(msg, exc_info=True)
                log.error(msg)
                continue
            break
        else:
            msg = (
                f"ERROR: {self.desc} all {self.defaults.retries} backup attempts failed"
            )
            log.critical(msg)

    def backup_controller(self):
        if not self._check_connection():
            return

        # Call dls-pmacanalyse backup
        # If backup fails retry specified number of times before giving up
        log.info(f"Backing up {self.desc}.")
        for attempt_num in range(self.defaults.retries):
            try:
                self._connect_analyse()
                self.analyse.readHardware(
                    self.defaults.temp_dir, False, False, False, False
                )

                new_file = self.defaults.temp_dir / self.f_name
                old_file = self.defaults.motion_folder / self.f_name
                shutil.copyfile(str(new_file), str(old_file))

                self._connect_direct()
                self.pti.sendCommand("save")
                self.pti.disconnect()
                self.pti = None

                log.critical(f"SUCCESS: {self.desc} backed up")
            except Exception:
                num = attempt_num + 1
                msg = (
                    f"ERROR: {self.desc} backup failed on attempt {num} "
                    f"of {self.defaults.retries}"
                )
                log.debug(msg, exc_info=True)
                log.error(msg)
                continue
            break
        else:
            msg = (
                f"ERROR: {self.desc} all {self.defaults.retries} backup attempts failed"
            )
            log.critical(msg)

    plc_running = re.compile(r"-M50([0-3][0-9]) = (-?[0-9]+)")
    old_m_var = re.compile(r"-M([0-9]{1,2})62 = (-?[0-9]+)")
    new_m_var = re.compile(r"\+M([0-9]{1,2})62 = (-?[0-9]+)")
    get_i08 = {i: re.compile(rf"i{i:d}08 *= *(-?[0-9]+)") for i in range(1, 33)}

    @classmethod
    def getPositionSF(cls, brick, defaults: Defaults):
        scaleFactors = [0] * 33
        pmc_file = defaults.motion_folder / (brick + ".pmc")
        try:
            with pmc_file.open("r") as f:
                pmc = f.read()
        except (FileNotFoundError, LookupError):
            log.error(f"could not read i08 for {brick} assuming i08 == 32 for all axes")
            pmc = ""

        for axis in range(1, 33):
            r = re.search(cls.get_i08[axis], pmc)
            if r is not None:
                scaleFactors[axis] = int(r[1]) * 32
            else:
                scaleFactors[axis] = 1024
        return scaleFactors

    @classmethod
    def diff_to_counts(cls, brick: str, diff_output: str, defaults: Defaults):
        """
        converts a diff output for Mxx62 file to a readable list of changes
        of counts per axis
        :param brick: name of the brick
        :param diff_output: output from a diff of the _positions file.
        :param defaults: a Defaults structure with names of folders etc.
        :return: str: human readable list of count differences per axis
        """
        scaleFactors = cls.getPositionSF(brick, defaults)
        output = ""

        old_plcs = {
            int(m): int(val) for m, val in re.findall(cls.plc_running, diff_output)
        }
        for plc, state in old_plcs.items():
            if state == 0:
                output += f"PLC {plc} was running but now is stopped\n"
            else:
                output += f"PLC {plc} was stopped but now is running\n"

        old_values = {
            int(m): int(val) for m, val in re.findall(cls.old_m_var, diff_output)
        }
        new_values = {
            int(m): int(val)
            for m, val in re.findall(cls.new_m_var, diff_output)
            if int(m) in old_values.keys()
        }

        for m, val in new_values.items():
            counts = int(val / scaleFactors[m])
            diff = int((val - old_values[m]) / scaleFactors[m])
            if math.fabs(diff) > 0:
                output += f"Axis {m} changed by {diff} counts to {counts}\n"

        return output
