[![CI](https://github.com/DiamondLightSource/dls-backup-bl/actions/workflows/ci.yml/badge.svg)](https://github.com/DiamondLightSource/dls-backup-bl/actions/workflows/ci.yml)
[![Coverage](https://codecov.io/gh/DiamondLightSource/dls-backup-bl/branch/main/graph/badge.svg)](https://codecov.io/gh/DiamondLightSource/dls-backup-bl)
[![PyPI](https://img.shields.io/pypi/v/dls-backup-bl.svg)](https://pypi.org/project/dls-backup-bl)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://www.apache.org/licenses/LICENSE-2.0)

# dls_backup_bl

Back up the configuration of the devices on a Diamond beamline — PMAC and
GeoBrick motor controllers, Moxa and ACS terminal servers, and Zebra boxes —
into a git repository.

Each run fetches every device listed for the beamline, writes the results into
the beamline's backup area and commits anything that changed. Because the area
is a git repository you get two things a pile of files cannot give you: a device
can be restored after a failure, and `git log` tells you when a working
configuration last changed and how.

What            | Where
:---:           | :---:
Source          | <https://github.com/DiamondLightSource/dls-backup-bl>
PyPI            | `pip install dls-backup-bl`
Docker          | `docker run ghcr.io/diamondlightsource/dls-backup-bl:latest`
Releases        | <https://github.com/DiamondLightSource/dls-backup-bl/releases>

## Quick start

Run it from a workstation on the beamline you are backing up, and pass nothing
but your email address:

```bash
dls-backup-bl --email you@diamond.ac.uk
```

Everything else is derived from the `$BEAMLINE` environment variable, so on a
beamline workstation there is nothing else to configure. A report is emailed
when the run finishes, and printed as a summary at the end either way.

To check where it is about to write before you run it:

```bash
dls-backup-bl --folder
```

## What gets backed up

| Device | How it is fetched | What lands in the backup area |
| --- | --- | --- |
| PMAC / GeoBrick | telnet, directly or through a terminal server port | `MotionControllers/<controller>.pmc` |
| Moxa NPort terminal server | HTTP(S) to the device's web UI | `TerminalServers/<address>_config.dec` (and/or `.ini`, see below) |
| ACS terminal server | `scp` of the device's flash config | `TerminalServers/<address>_config.tar.gz` |
| Zebra | EPICS Channel Access — the IOC writes its own file | `Zebras/<name>` |

Devices are backed up in parallel (`--threads`, default 10) and each one is
retried on failure (`--retries`, default 4).

## Where the backups go

The default backup area is `/dls_sw/work/motion/Backups/<BLXXY>`, where `BLXXY`
comes from `$BEAMLINE`: `i16` becomes `BL16I`, and a branch line such as `i09-1`
becomes `BL09J`. Override the beamline with `--beamline i16`, the whole
directory with `--dir`, or — where a `BLXXY` name makes no sense — the folder
name alone with `--domain ME01D`.

```
/dls_sw/work/motion/Backups/BL16I/
├── BL16I-backup.json      the list of devices to back up
├── MotionControllers/     <controller>.pmc, plus <controller>_positions.pmc
├── TerminalServers/
├── Zebras/
├── backup.log             the summary that is committed and emailed
└── backup_detail.log      full debug log of the most recent run only
```

`backup.log` is the record of what succeeded and failed; it is committed with
the backup and is the text that `--email` sends. `backup_detail.log` is never
committed and is overwritten every run, so it only ever describes the run that
just happened — check it first when diagnosing a failure.

## Choosing which devices are backed up

The device list lives in `<backup area>/<BLXXY>-backup.json`. Edit it with the
GUI:

```bash
dls-backup-gui
```

or by hand — it is plain JSON:

```json
{
    "motion_controllers": [
        {
            "controller": "BL16I-MO-BRICK-01",
            "port": 1025,
            "server": "172.23.240.97"
        }
    ],
    "terminal_servers": [
        {
            "server": "bl16i-nt-tserv-01",
            "ts_type": "moxa",
            "decrypt": true
        }
    ],
    "zebras": [
        { "Name": "BL16I-EA-ZEBRA-01" }
    ]
}
```

`ts_type` is one of `moxa`, `acs` or `acsold`. `decrypt` asks for that Moxa's
backup to be saved as a readable `.ini` instead of an encrypted `.dec`; it
defaults to `false`, is ignored for ACS servers, and is overridden for the whole
run by `--decrypt` / `--decrypt-only`. A motion controller with
`"port": 1025` is contacted directly; any other port means it is reached through
a terminal server. An existing `dls-pmac-analyse` configuration can be imported
instead of typing the controllers in:

```bash
dls-backup-bl --import-cfg /path/to/pmac-analyse.cfg
```

To back up only some of the listed devices, name them:

```bash
dls-backup-bl --devices BL16I-MO-BRICK-01 BL16I-MO-BRICK-02
```

## Motor positions

Motor positions change constantly, so they are kept out of the ordinary backup
commit and handled explicitly with `--positions`:

```bash
dls-backup-bl --positions save      # record current positions and commit them
dls-backup-bl --positions compare   # report how positions have moved since
dls-backup-bl --positions restore   # write the last committed positions back
```

`compare` writes its report to `positions_comparison.txt` in the backup area and
commits it, so there is a record of what was checked and when.

## Reading a Moxa terminal server backup

An NPort exports its configuration encrypted, so a `.dec` file cannot be read or
diffed without putting it back on a device. This tool can decrypt them locally,
with no device and no vendor software.

Set `decrypt` on a terminal server in the configuration file to have its backup
saved as an `.ini` from then on, or ask for it a run at a time:

```bash
dls-backup-bl --decrypt        # write both .dec and a readable .ini
dls-backup-bl --decrypt-only   # write only the .ini
```

The encrypted export is only dropped once a readable copy has been written, so a
wrong pre-shared key can never cost you the backup.

Or convert a backup you already have, without running a backup at all:

```bash
dls-backup-bl --decrypt-file BL16I/TerminalServers/172.23.243.10_config.dec
# writes 172.23.243.10_config.ini

dls-backup-bl --decrypt-file 172.23.243.10_config.dec --out -   # to stdout
```

which makes the configurations greppable and diffable:

```bash
dls-backup-bl --decrypt-file a_config.dec --out - | grep -i '^Server Name'

diff <(dls-backup-bl --decrypt-file a_config.dec --out -) \
     <(dls-backup-bl --decrypt-file b_config.dec --out -)
```

The `.ini` is the device's own format: it can be edited and uploaded straight
back to an NPort through its web UI. Output keeps the device's CRLF line
endings, so add `| tr -d '\r'` if that gets in the way of a text tool.

Decryption uses the configuration pre-shared key, which devices leave at the
factory default `moxa`. If a device has been given its own key, pass it with
`--psk`. A wrong key is caught by the file's checksum rather than silently
producing rubbish.

## Common options

| Option | Purpose |
| --- | --- |
| `-e, --email ADDRESS` | email the backup report |
| `-b, --beamline i16` | back up a beamline other than `$BEAMLINE` |
| `--dir DIR` | use a different backup area |
| `-d, --devices NAME ...` | back up only the named devices |
| `-p, --positions save\|restore\|compare` | handle motor positions |
| `--folder` | print the backup folder and exit |
| `-l, --log-level debug` | more detail on the console |

`dls-backup-bl --help` lists every option.

## Development

```bash
git clone https://github.com/DiamondLightSource/dls-backup-bl.git
cd dls-backup-bl
tox -p            # pre-commit, mypy and the tests
```

The tests are self-contained and need no beamline, device or network. The
exception is `tests/test_nport_estate.py`, which checks that every terminal
server backup we hold still decrypts. Those are live configurations and cannot
be committed here, so it skips unless you point it at them — see its docstring.
