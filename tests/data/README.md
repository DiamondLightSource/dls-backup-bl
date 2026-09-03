# Test data

## `nport6650_config.dec` / `nport6650_config.ini`

A real configuration export from a Moxa NPort, and the decryption of that exact
file produced by Moxa's own `mcc_tool`.

`test_matches_the_vendor_tool_on_a_real_device_export` asserts that
`nport.decrypt()` reproduces the `.ini` byte for byte. The expected bytes
therefore come from the vendor's application, not from this implementation, so
the test cannot be satisfied by a decryptor that is self-consistently wrong.

| | |
| --- | --- |
| Device | NP6650-16, serial 417 |
| Firmware | 2.3 Build 24101817 |
| Pre-shared key | the factory default, `moxa` |
| Export format | v2 (16 byte header, magic `6000`) |
| Size | 76336 bytes, 2385 blocks, 19 bytes of NUL padding |

### Why these can be committed

The device is a bench unit that carries no production configuration. Its
passwords and SNMP community strings are deliberate placeholders - `password`,
`read`, `write`, `public` - set for this capture. Before the files were added,
every password-, community- and secret-shaped field was also compared against
all 42 production terminal server backups: none of the values appears on any
production device, so nothing shared is published here. The addresses in the file are internal RFC1918 addresses of the same kind
already present elsewhere in this repository.

Anyone adding another capture must repeat that check. An NPort export has
around 790 fields and does carry `Console Password`, `User Pass #n`, the SNMP
community strings and, where configured, RADIUS and TACACS+ secrets.

### Editing

Do not. Both files must stay byte identical to what the device and `mcc_tool`
produced, or the comparison is meaningless. `.gitattributes` marks this
directory `-text` so no checkout rewrites the `.ini`'s CRLF line endings, and
the `end-of-file-fixer` pre-commit hook is configured to skip it — that hook
would otherwise append a newline and break the test.

To replace the pair, capture a fresh `/Config.txt` from the device's web UI and
decrypt that same file with `mcc_tool`, then update the table above.
