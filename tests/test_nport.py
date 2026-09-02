"""Tests for the pure Python Moxa NPort configuration decryptor.

The vectors below were produced by Moxa's own implementation (``CfgAESDecrypt``
and ``CfgAESEncrypt`` in the mcc_tool plugin ``dsci_mcc.so``), so they are
independent ground truth rather than this module checking itself.

Three further tests run only where the real estate is reachable - the encrypted
backups and the reference C tool in ``tools/ts_decrypt``. They skip elsewhere,
so CI exercises the vectors alone. Override the locations with ``NPORT_CORPUS``,
``TS_DECRYPT`` and ``MCC_SO`` if yours differ.
"""

import os
import subprocess
from pathlib import Path

import pytest

from dls_backup_bl import nport
from dls_backup_bl.defaults import TsConfigFormat
from dls_backup_bl.tserver import TsConfig

# --- vendor block cipher vectors: (key, ciphertext, expected plaintext) ------
BLOCK_VECTORS = [
    (
        "0000000000000000000000000000000000000000000000000000000000000000",
        "0000000000000000000000000000000000000000000000000000000000000000",
        "46a50bd93fa2c9753c7457a9893f885a07e0e9108b6c64d471f9704095573b82",
    ),
    (
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
        "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
        "3d6542ea88c81502474e2f98c0b764e8265f6ce430ade50265b2058fdd3d5415",
    ),
    (
        "8000000000000000000000000000000000000000000000000000000000000000",
        "0000000000000000000000000000000000000000000000000000000000000000",
        "5ad584c7f53c4c76ef516f5b0165303ce0bc001a838dfa655b80f2411659c048",
    ),
    (
        "0000000000000000000000000000000000000000000000000000000000000000",
        "0100000000000000000000000000000000000000000000000000000000000000",
        "5b03924c7d49b742b8f787e7996c4ea9550e531d6c7c2fa815670ab450893bb7",
    ),
]

# A whole synthetic export, encrypted by the vendor library from known text.
FIXTURE_DEC = bytes.fromhex(
    "3630303000000101cd64ba23a00000007cf8212b31eeba91ba2522238247cdd8"
    "4ec4d5469c337362ba13d2654d644a39ef5910b5d14da4c8d4641eeb6f615f1f"
    "8f4365048e32b24582c50db3d749e183c56d0d3566bc24cff77624858f53e8c1"
    "885b706d244922b036e86c5148294a28d6f0526cc5806e4963110425a0097d16"
    "6aa3120be0e419032214a44482adbf9082c00a52fe9d40c9beb94ff70d0c2c4e"
    "199b128e755ec9132e73480b0473c1d4"
)
FIXTURE_PLAIN = (
    b"\r\n[NPort Configuration File]\r\nCheck Code=cfg1\r\n"
    b"Model Name=NPort 6650-16\r\nServer Name=TEST-FIXTURE\r\n"
    b"[Network Setting]\r\nIP Address=192.0.2.10\r\n"
)

CORPUS = Path(os.environ.get("NPORT_CORPUS", "/workspaces/misc/VA/TerminalServers"))
C_TOOL = Path(os.environ.get("TS_DECRYPT", "/workspaces/tools/ts_decrypt/ts_decrypt"))
MCC_SO = Path(
    os.environ.get(
        "MCC_SO",
        "/workspaces/tools/mcc_tool_x64_ver1.6_build_26020217/dsci_mcc.so",
    )
)


def corpus_files() -> list[Path]:
    return sorted(CORPUS.glob("*.dec")) if CORPUS.is_dir() else []


needs_corpus = pytest.mark.skipif(
    not corpus_files(), reason=f"no encrypted backups found in {CORPUS}"
)


@pytest.mark.parametrize(("key", "cipher", "plain"), BLOCK_VECTORS)
def test_block_matches_vendor(key: str, cipher: str, plain: str):
    """Our Rijndael-256 agrees with Moxa's, block for block."""
    round_keys = nport._key_schedule(bytes.fromhex(key))
    assert nport._decrypt_block(bytes.fromhex(cipher), round_keys).hex() == plain


def test_key_is_psk_over_the_vendor_constant():
    key = nport.make_key("moxa")
    assert key[:4] == b"moxa"
    assert key[4:] == nport.CFG_ENCRYPT_KEY[4:]
    assert len(key) == 32


def test_psk_longer_than_the_key_is_rejected():
    with pytest.raises(nport.NPortConfigError):
        nport.make_key("x" * 33)


def test_decrypts_a_whole_export():
    assert nport.decrypt(FIXTURE_DEC) == FIXTURE_PLAIN


def test_wrong_psk_is_caught_by_the_checksum():
    with pytest.raises(nport.ChecksumError):
        nport.decrypt(FIXTURE_DEC, psk="notmoxa")


def test_rejects_something_that_is_not_an_export():
    assert not nport.is_encrypted_config(b"[NPort Configuration File]\r\n")
    with pytest.raises(nport.NPortConfigError):
        nport.decrypt(b"not an NPort configuration at all")


def test_decrypt_file_writes_an_ini_beside_the_dec(tmp_path: Path):
    source = tmp_path / "nport.example_config.dec"
    source.write_bytes(FIXTURE_DEC)
    dest = nport.decrypt_file(source)
    assert dest == tmp_path / "nport.example_config.ini"
    assert dest.read_bytes() == FIXTURE_PLAIN


def make_ts_config(tmp_path: Path, config_format: TsConfigFormat) -> TsConfig:
    """A TsConfig with no device behind it - __init__ would try to back one up."""
    ts = TsConfig.__new__(TsConfig)
    ts.ts = "nport.example"
    ts.path = tmp_path
    ts.config_format = config_format
    ts.psk = nport.DEFAULT_PSK
    return ts


@pytest.mark.parametrize(
    ("config_format", "dec_written", "ini_written"),
    [
        (TsConfigFormat.encrypted, True, False),
        (TsConfigFormat.both, True, True),
        (TsConfigFormat.decrypted, False, True),
    ],
)
def test_backup_writes_the_requested_formats(
    tmp_path: Path,
    config_format: TsConfigFormat,
    dec_written: bool,
    ini_written: bool,
):
    make_ts_config(tmp_path, config_format).save_moxa_config(FIXTURE_DEC)

    dec = tmp_path / "nport.example_config.dec"
    ini = tmp_path / "nport.example_config.ini"
    assert dec.exists() is dec_written
    assert ini.exists() is ini_written
    if dec_written:
        assert dec.read_bytes() == FIXTURE_DEC
    if ini_written:
        assert ini.read_bytes() == FIXTURE_PLAIN


def test_undecryptable_backup_is_still_kept(tmp_path: Path):
    """A wrong pre-shared key must never cost us the backup."""
    ts = make_ts_config(tmp_path, TsConfigFormat.decrypted)
    ts.psk = "notmoxa"
    ts.save_moxa_config(FIXTURE_DEC)

    assert (tmp_path / "nport.example_config.dec").read_bytes() == FIXTURE_DEC
    assert not (tmp_path / "nport.example_config.ini").exists()


@needs_corpus
def test_every_real_backup_passes_its_checksum():
    """The vendor's own integrity check over the whole estate."""
    for dec in corpus_files():
        plain = nport.decrypt(dec.read_bytes())
        assert b"[NPort Configuration File]" in plain[:64], dec.name


@needs_corpus
@pytest.mark.skipif(
    not C_TOOL.is_file() or not MCC_SO.is_file(),
    reason="reference C tool or Moxa dsci_mcc.so not available",
)
def test_matches_the_reference_c_tool(tmp_path: Path):
    """Byte for byte agreement with tools/ts_decrypt, which uses Moxa's cipher."""
    env = dict(os.environ, MCC_SO=str(MCC_SO))
    for dec in corpus_files():
        expected = tmp_path / (dec.stem + ".ini")
        subprocess.run(
            [str(C_TOOL), str(dec), str(expected)],
            check=True,
            capture_output=True,
            env=env,
        )
        assert nport.decrypt(dec.read_bytes()) == expected.read_bytes(), dec.name
