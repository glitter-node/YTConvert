# YTConvert workspace

- Keep all file writes inside `/volume1/glitter/ytconvert`.
- Do not run Uvicorn, systemctl, or modify Nginx and system configuration.
- Keep source comments and user-facing strings in English.
- Run tests with the project virtual environment when available.
- Keep the media worker disabled by default. Root media execution has no verified network isolation and requires the documented runtime checks.
- Verify conversion changes with mocks before any real media test. Run an authorized real media test only after the user explicitly approves it.
