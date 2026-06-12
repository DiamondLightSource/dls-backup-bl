import hashlib
import os
import re
from logging import getLogger
from pathlib import Path

import pexpect
import requests

from .defaults import Defaults

log = getLogger(__name__)

try:
    requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS += "HIGH:!DH:!aNULL"  # type: ignore
except AttributeError:
    # urllib3 >= 2.0 removed DEFAULT_CIPHERS and uses sensible defaults
    pass
try:
    requests.packages.urllib3.contrib.pyopenssl.DEFAULT_SSL_CIPHER_LIST += (  # type: ignore
        "HIGH:!DH:!aNULL"
    )
except AttributeError:
    # no pyopenssl support used / needed / available
    pass

# moxa NPort firmware 2.x serves https with a self-signed certificate
requests.packages.urllib3.disable_warnings()  # type: ignore


# todo make ts_type an enum
# todo use Path instead of system
class TsConfig:
    def __init__(
        self,
        ts: str,
        backup_directory: Path,
        username: str | None = None,
        password: str | None = None,
        ts_type: str = "",
    ):
        self.ts = ts
        self.path: Path = backup_directory
        self.desc = f"Terminal server {ts} type {ts_type}"

        log.info(f"backing up {self.desc}")

        self.success = False
        if ts_type.lower() == "moxa":
            self.success = self.get_moxa_config(
                username or "admin", password or "tslinux"
            )
        elif ts_type.lower() == "acs":
            self.success = self.get_acs_config(
                username or "root", password or "tslinux", "/mnt/flash/config.tgz"
            )
        elif ts_type.lower() == "acsold":
            self.success = self.get_acs_config(
                username or "root", password or "tslinux", "/proc/flash/script"
            )
        else:
            log.error(f"unknown type for {self.desc}")

    @staticmethod
    def make_moxa_login(page: str, username: str, password: str):
        match = re.search("(?:fake_challenge|FakeChallenge) value=([^>]*)>", page)
        if match is None:
            raise ValueError("This web page that doesn't look like a moxa login screen")
        fake_challenge = match.groups()[0]

        if "EncUser" in page:
            # firmware 2.x - do what SetPass() javascript does on the login
            # screen: XOR each credential into the SHA256 of the challenge
            key = hashlib.sha256(fake_challenge.encode("utf8")).digest()

            def encode(secret):
                result = bytearray(key)
                for i, c in enumerate(secret):
                    result[i] = key[i] ^ ord(c)
                return result.hex()

            login_data = {
                "Username": "",
                "Password": "",
                "EncUser": encode(username),
                "EncPasswd": encode(password),
                "FakeChallenge": fake_challenge,
            }
        else:
            # firmware 1.x - do what SetPass() javascript does on the login
            # screen: XOR the password's hex digits into the MD5 of the
            # challenge
            md = hashlib.md5(fake_challenge.encode("utf8")).hexdigest()
            p = ""
            for c in password:
                p += f"{ord(c):x}"
            md5_pass = ""
            for i in range(len(p)):
                m = int(p[i], 16)
                n = int(md[i], 16)
                md5_pass += "%x" % (m ^ n)

            login_data = {
                "Username": username,
                "MD5Password": md5_pass,
                "Password": "",
                "FakeChallenge": fake_challenge,
            }
        return login_data

    def get_moxa_config(self, username, password):
        url = f"http://{self.ts}"
        # use requests session to get authentication cookie
        session = requests.session()
        # firmware 2.x redirects to https with a self-signed certificate
        response = session.post(url, verify=False)
        response.raise_for_status()
        url = response.url.rstrip("/")

        login = self.make_moxa_login(response.text, username, password)
        # send the encoded credentials - populates session cookie 'ChallID'
        response = session.post(f"{url}/", data=login, verify=False)

        # firmware 2.x shows a login history page first - press 'Continue'
        if "WebLogClr" in response.text:
            session.get(f"{url}/WebLogClr?op=0", verify=False)

        # the export form moved from ConfExp.htm to BackupRestore.htm in
        # firmware 2.x, which has several forms each with its own csrf_token -
        # take the one from the form that posts to Config.txt
        response = session.get(f"{url}/ConfExp.htm", verify=False)
        if response.status_code == 404:
            response = session.get(f"{url}/BackupRestore.htm", verify=False)
            m = re.search(
                r"action=Config\.txt>.*?csrf_token value=([^>]*)>",
                response.text,
                re.DOTALL,
            )
        else:
            m = re.search(r"csrf_token value=([^>]*)>", response.text)
        data = {"csrf_token": m[1]} if m else {}

        response = session.post(f"{url}/Config.txt", data=data, verify=False)
        response.raise_for_status()
        if b"FakeChallenge" in response.content:
            # we got the login page back instead of the configuration
            raise ValueError(f"moxa {self.ts} login failed - check credentials")

        cfg_path = self.path / (self.ts + "_config.dec")
        with cfg_path.open("wb") as f:
            f.write(response.content)
        return True

    def get_acs_config(self, username, password, remote_path):
        tar = self.path / (self.ts + "_config.tar.gz")
        child = pexpect.spawn(f"scp {username}@{self.ts}:{remote_path} {str(tar)}")
        i = child.expect(
            ["Are you sure you want to continue connecting (yes/no)?", "Password:"],
            timeout=120,
        )
        if i == 0:
            child.sendline("yes")
            child.expect("Password:", timeout=120)
        child.sendline(password)
        i = child.expect([pexpect.EOF, "scp: [^ ]* No such file or directory"])
        try:
            os.chmod(str(tar), 0o664)
        except Exception:
            msg = "Warning: Permissions for ACS Terminal server backup file could not be changed."
            log.critical(msg)
            pass
        if i == 1:
            log.error(f"Remote path {remote_path} doesn't exist on this ACS")
            return False
        else:
            return True


def backup_terminal_server(server: str, ts_type: str, defaults: Defaults):
    desc = f"terminal server {server} type {ts_type}"

    # If backup fails retry specified number of times before giving up
    for attempt_num in range(defaults.retries):
        # noinspection PyBroadException
        try:
            t = TsConfig(server, defaults.ts_folder, None, None, ts_type)
            if t.success:
                log.critical(f"SUCCESS backed up {desc}")
            else:
                log.critical(f"ERROR failed to back up {desc}")
        except Exception:
            msg = f"ERROR: {desc} backup failed on attempt {attempt_num + 1} of {defaults.retries}"
            log.debug(msg, exc_info=True)
            log.error(msg)
            continue
        break
    else:
        msg = f"ERROR: {desc} all {defaults.retries} attempts failed"
        log.critical(msg)
