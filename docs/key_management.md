# Key Management

## Overview

agent-cli supports four private key storage backends with a unified resolution system.
When a private key is needed, backends are tried in priority order until one succeeds.

## Resolution Order

1. **macOS Keychain** — most secure, recommended for local development on macOS
2. **Encrypted Keystore** — geth-compatible Web3 Secret Storage (scrypt KDF)
3. **Railway Environment** — reads `HL_PRIVATE_KEY` from Railway-injected env vars
4. **Flat File** — plaintext `~/.hl-agent/keys/{address}.txt` (development only)
5. **Direct Env Var** — `HL_PRIVATE_KEY` environment variable (final fallback)

## Backends

### macOS Keychain (`keychain`)

Uses the system Keychain via the `security` CLI. Keys are stored encrypted at rest
and protected by macOS login credentials.

- **Availability**: macOS only (`sys.platform == "darwin"`)
- **Service name**: `agent-cli`
- **Security**: Strong — encrypted at rest, OS-level access control
- **Store**: `hl keys import --backend keychain`
- **Retrieve**: Automatic via resolver

### Encrypted Keystore (`keystore`)

Existing geth-compatible keystore at `~/.hl-agent/keystore/<address>.json`.
Uses eth_account with scrypt KDF for encryption.

- **Availability**: Always (eth_account is a required dependency)
- **Requires**: Password via `HL_KEYSTORE_PASSWORD` env var or `~/.hl-agent/env`
- **Security**: Strong — scrypt-encrypted, industry standard
- **Store**: `hl wallet import` or `hl keys import --backend keystore`
- **Retrieve**: Automatic via resolver (requires password)

### Railway Environment (`railway`)

Reads keys from environment variables injected by Railway deployment platform.
Cannot store keys — they must be set via the Railway dashboard.

- **Availability**: Only when `RAILWAY_ENVIRONMENT` env var is set
- **Reads**: `HL_PRIVATE_KEY` and `{VENUE}_PRIVATE_KEY` patterns
- **Security**: Managed by Railway — encrypted at rest in their platform
- **Store**: Via Railway dashboard only

### Flat File (`file`)

Plaintext key files at `~/.hl-agent/keys/{address}.txt`. Files are created with
`0600` permissions (owner read/write only).

- **Availability**: Always
- **Security**: Weak — plaintext on disk. Use only for development.
- **Store**: `hl keys import --backend file`
- **Warning**: Logs a warning on every key retrieval

## CLI Commands

### Import a key

```bash
# Import into macOS Keychain (recommended on macOS)
hl keys import --backend keychain

# Import into encrypted keystore
hl keys import --backend keystore

# Import into flat file (dev only)
hl keys import --backend file
```

### List all keys

```bash
hl keys list
```

Shows all addresses across all available backends (never displays private keys).

### Migrate between backends

```bash
# Move from flat file to keychain
hl keys migrate --from file --to keychain

# Move specific address from keystore to keychain
hl keys migrate --from keystore --to keychain --address 0x1234...
```

## Migration Guide

### From `HL_PRIVATE_KEY` env var to Keychain

1. Run `hl keys import --backend keychain` and paste your key
2. Remove `HL_PRIVATE_KEY` from your shell profile
3. Verify: `hl keys list` should show the address under `keychain`

### From encrypted keystore to Keychain

```bash
hl keys migrate --from keystore --to keychain
```

### From flat file to encrypted keystore

```bash
hl keys migrate --from file --to keystore
```

## Programmatic Usage

```python
from common.credentials import resolve_private_key

# Auto-resolves using priority chain
key = resolve_private_key(venue="hl")

# For a specific address
key = resolve_private_key(venue="hl", address="0x1234...")

# Use a specific backend directly
from common.credentials import get_backend
keychain = get_backend("keychain")
if keychain and keychain.available():
    key = keychain.get_key("0x1234...")
```
