# Bootstrap

Run these checks on every startup. Execute silently — only report errors or the final status.

## Startup Sequence

1. **Check environment:**
```bash
hl setup check
```
If this fails, report the missing configuration to the user and stop.

2. **Approve builder fee (idempotent):**
```bash
hl builder approve
```

3. **Check account balance:**
```bash
hl account
```
If balance is 0, tell the user: "Account has no balance. Run `hl setup claim-usdyp` for testnet funds, or deposit USDC for mainnet."

4. **Check existing positions:**
```bash
hl status
```

5. **Check APEX state (if exists):**
```bash
hl apex status
```

6. **Report ready:**
Send to user: "Agent ready. Balance: $X. Active positions: N. Say 'start trading' to begin APEX, or ask me to scan for opportunities."

## On Failure

If any check fails, report the specific error and suggest a fix. Do not start trading with a broken environment.
