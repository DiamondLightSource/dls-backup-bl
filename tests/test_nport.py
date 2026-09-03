"""Tests for the pure Python Moxa NPort configuration decryptor.

Every vector here was produced by Moxa's own implementation (``CfgAESDecrypt``
and ``CfgAESEncrypt`` in the mcc_tool plugin ``dsci_mcc.so``), so they are
independent ground truth rather than this module checking itself, and they are
committed so that no test in this file needs the vendor library, a device, a
network or anything else outside the repository.

Verification against the real estate lives in ``test_nport_estate.py``, which
needs data we cannot ship and so does not run in CI.
"""

from pathlib import Path

import pytest

from dls_backup_bl import nport
from dls_backup_bl.defaults import TsConfigFormat
from dls_backup_bl.tserver import TsConfig, moxa_backup_name

DATA = Path(__file__).parent / "data"

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
    (
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        "8c7a178c44922f0d272b9b9a49819d67a4fb711a7d7b94716fe82c1039a23602",
    ),
    # the key this tool actually uses, against a minimal block
    (
        "6d6f7861a85cc77525a18747f0a7052b4c8b9e70dab30dcb51d0d3125ac43c97",
        "0000000000000000000000000000000000000000000000000000000000000001",
        "8d6cc4b08f468869b4b072f3b14832e273a5978fa489eae790f71743a2c9a57b",
    ),
    (
        "d1990ac88adc43771d313045b34ae927a517c0fe9d0595a400ebcdca1a28d03b",
        "1ba7747b3088f65899e0c43eb3a4c67125316555819b4822442f398156f5a3ba",
        "cc8bfca618379337bb33250480f2e5e58959ec037c69fd199621fd4e314fe50f",
    ),
    (
        "4205f9838bf9b2df4383c01f37b1ebad756d2d3642bfd0a3c3f78df40e422b3a",
        "4cef2aaa646f5769c3f4be04267531c9ef96b7459c689367895f00f3d5e6b1ea",
        "97a228ea777d98c98d8fc5834e8e3432dcd113695e78eefc2e6f753b833e269c",
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

# The same export in the older 12 byte header form: magic, version, checksum,
# and the payload length implied by the file size.  Every real backup here is
# the 16 byte v2 form, so this is synthesised to keep the v1 branch covered.
FIXTURE_DEC_V1 = (
    FIXTURE_DEC[:4] + (1).to_bytes(4, "little") + FIXTURE_DEC[8:12] + FIXTURE_DEC[16:]
)

# An export whose text exactly fills its last block, so there is no NUL padding
# and decrypt() must return the payload whole rather than trimming it.
FIXTURE_DEC_UNPADDED = bytes.fromhex(
    "3630303000000101ad80ee67600000007cf8212b31eeba91ba2522238247cdd8"
    "4ec4d5469c337362ba13d2654d644a39ef5910b5d14da4c8d4641eeb6f615f1f"
    "8f4365048e32b24582c50db3d749e1837fceefb3aa11bec10a1c2891b79f063f"
    "8aee131b80a04bf239f1f1c85e2547db"
)
FIXTURE_PLAIN_UNPADDED = (
    b"\r\n[NPort Configuration File]\r\nCheck Code=cfg1\r\n"
    b"Model Name=NPort 6650-16\r\nServer Name=EXACT-FIT\r\n"
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


def test_matches_the_vendor_tool_on_a_real_device_export():
    """The whole pipeline against a genuine export and the vendor's own answer.

    ``nport6650_config.ini`` is what Moxa's mcc_tool wrote from
    ``nport6650_config.dec``, so the expected bytes here come from the vendor
    application rather than from this implementation.  See data/README.md.
    """
    raw = (DATA / "nport6650_config.dec").read_bytes()
    assert (len(raw) - 16) // nport.BLOCK_SIZE > 2000, "fixture looks truncated"
    assert nport.decrypt(raw) == (DATA / "nport6650_config.ini").read_bytes()


def test_decrypts_an_export_with_no_padding():
    """Nothing to trim: the text is a whole number of blocks."""
    assert len(FIXTURE_PLAIN_UNPADDED) % nport.BLOCK_SIZE == 0
    assert nport.decrypt(FIXTURE_DEC_UNPADDED) == FIXTURE_PLAIN_UNPADDED


def test_decrypts_the_older_12_byte_header_form():
    """The v1 branch takes its payload length from the file size, not a header."""
    assert nport.decrypt(FIXTURE_DEC_V1) == FIXTURE_PLAIN


def test_rejects_an_unknown_header_version():
    bad = FIXTURE_DEC[:4] + (99).to_bytes(4, "little") + FIXTURE_DEC[8:]
    with pytest.raises(nport.NPortConfigError):
        nport.decrypt(bad)


@pytest.mark.parametrize(
    ("claimed_length", "why"),
    [
        (48, "not a whole number of 32 byte blocks"),
        (len(FIXTURE_DEC) * 2, "longer than the file"),
    ],
)
def test_rejects_an_implausible_payload_length(claimed_length: int, why: str):
    bad = FIXTURE_DEC[:12] + claimed_length.to_bytes(4, "little") + FIXTURE_DEC[16:]
    with pytest.raises(nport.NPortConfigError, match="implausible"):
        nport.decrypt(bad)


def test_non_ascii_psk_is_reported_not_raised_raw():
    """The CLI only catches NPortConfigError, so this must not be a UnicodeError."""
    with pytest.raises(nport.NPortConfigError):
        nport.make_key("m\N{DEGREE SIGN}xa")


def test_wrong_psk_is_caught_by_the_checksum():
    with pytest.raises(nport.ChecksumError):
        nport.decrypt(FIXTURE_DEC, psk="notmoxa")


def test_rejects_something_that_is_not_an_export():
    assert not nport.is_encrypted_config(b"[NPort Configuration File]\r\n")
    with pytest.raises(nport.NPortConfigError):
        nport.decrypt(b"not an NPort configuration at all")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("172.23.243.10_config.dec", "172.23.243.10_config.ini"),
        # no suffix at all: with_suffix() would read ".10_config" as the suffix
        ("172.23.243.10_config", "172.23.243.10_config.ini"),
        ("Config.txt", "Config.ini"),
        ("CONFIG.DEC", "CONFIG.ini"),
    ],
)
def test_default_ini_path_does_not_eat_dotted_names(source: str, expected: str):
    assert nport.default_ini_path(Path("/backups") / source).name == expected


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


@pytest.mark.parametrize(
    ("config_format", "psk", "kept", "removed"),
    [
        (TsConfigFormat.encrypted, nport.DEFAULT_PSK, ".dec", ".ini"),
        (TsConfigFormat.decrypted, nport.DEFAULT_PSK, ".ini", ".dec"),
        # a failed decrypt keeps the backup, but must not leave the old .ini
        # standing as if it were this run's configuration
        (TsConfigFormat.decrypted, "notmoxa", ".dec", ".ini"),
    ],
)
def test_the_form_we_did_not_write_is_not_left_stale(
    tmp_path: Path,
    config_format: TsConfigFormat,
    psk: str,
    kept: str,
    removed: str,
):
    """A previous run's copy must not survive beside a freshly fetched one."""
    for suffix in (".dec", ".ini"):
        (tmp_path / f"nport.example_config{suffix}").write_bytes(b"from an old run")

    ts = make_ts_config(tmp_path, config_format)
    ts.psk = psk
    ts.save_moxa_config(FIXTURE_DEC)

    assert not (tmp_path / f"nport.example_config{removed}").exists()
    assert (tmp_path / f"nport.example_config{kept}").read_bytes() != b"from an old run"


@pytest.mark.parametrize(
    "config_format", [TsConfigFormat.both, TsConfigFormat.decrypted]
)
def test_a_plain_text_export_needs_no_decryption(
    tmp_path: Path, config_format: TsConfigFormat
):
    """Older firmware does not encrypt, and that is not a failure.

    The export is already the readable configuration, so it becomes the .ini
    directly instead of being reported as an undecryptable backup.
    """
    ts = make_ts_config(tmp_path, config_format)
    ts.save_moxa_config(FIXTURE_PLAIN)

    assert (tmp_path / "nport.example_config.ini").read_bytes() == FIXTURE_PLAIN


@pytest.mark.parametrize(
    ("server", "expected"),
    [
        ("172.23.243.10", "172.23.243.10"),
        # a colon would make GIO read the name as a URI, so gedit and friends
        # cannot open the .ini
        ("https://nport.example:8026", "nport.example_8026"),
        ("https://nport.example:8026/", "nport.example_8026"),
        ("http://nport.example", "nport.example"),
        ("https://nport.example:8026/sub", "nport.example_8026_sub"),
    ],
)
def test_the_address_cannot_put_awkward_characters_in_the_filename(
    server: str, expected: str
):
    assert moxa_backup_name(server) == expected


def test_the_backup_is_written_under_the_safe_name(tmp_path: Path):
    ts = make_ts_config(tmp_path, TsConfigFormat.encrypted)
    ts.ts = "https://nport.example:8026/"
    ts.save_moxa_config(FIXTURE_DEC)

    assert (tmp_path / "nport.example_8026_config.dec").read_bytes() == FIXTURE_DEC
