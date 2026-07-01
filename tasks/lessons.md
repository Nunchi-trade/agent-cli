# Lessons Learned

## 2026-03-12: Never fabricate URLs, endpoints, or external service details
**Mistake:** Hardcoded a fake telemetry URL (`api-temp.nunchi.trade/api/v1/telemetry`) that doesn't exist, presenting it as if it were real.
**Rule:** Never make up URLs, API endpoints, service names, or any external dependency. If something doesn't exist yet, use an env var or config placeholder and ask the user what the actual value should be. Always ask before inventing external interfaces.
