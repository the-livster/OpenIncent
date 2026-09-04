# Contributing to OpenIncent

## Development setup

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/the-livster/OpenIncent.git
cd OpenIncent/icm-engine

uv sync                 # core engine + CLI
uv sync --extra all     # + Excel, Parquet, AI extras
```

Run the gates before committing:

```bash
uv run pytest                        # tests
uv run ruff check src/ tests/        # lint
uv run mypy --platform linux src/    # typecheck (CI's platform)
uv run mypy --platform win32 src/    # typecheck (the desktop app's)
```

Or just `make check`, which runs all of them.

All must pass. There are currently 598 tests; they run in about 12 seconds.
Both `--platform` passes matter: the code has Windows-only branches (the
updater's `subprocess` flags, for one), and CI type-checks on Linux. Checking
only your own platform will let the other one's errors through.

## Pull requests

- Keep PRs small and focused. One logical change per PR.
- All three gates (tests, lint, typecheck) must be green. CI enforces this.
- New behavior needs tests. If you fix a bug, include a regression test that would have caught it.
- Match the existing code style — the project uses `ruff` with zero custom rules disabled, so if `ruff check` passes you are in the right ballpark. Use `Decimal` for all money values, not `float`.
- Commit messages: use [conventional commit](https://www.conventionalcommits.org/) style (`feat:`, `fix:`, `chore:`, `docs:`) in lowercase. See `git log` for examples.
- Prefer focused, descriptive summaries over generic ones ("add ramp period support" not "update engine").

## Contributor License Agreement (CLA)

OpenIncent is dual-licensed: the engine defaults to **AGPL-3.0-only**, and the maintainer also offers a **commercial license** for proprietary use. To keep the project dual-licensable, we need a lightweight Contributor License Agreement.

**On your first PR**, you will be asked to sign the CLA (typically via [CLA Assistant](https://cla-assistant.io/) on GitHub). By signing, you grant the project maintainer the right to:

- Include your contribution under the AGPL-3.0-only license.
- Also offer your contribution under a separate commercial license.

You retain full ownership of your contribution. The CLA does not assign copyright — it grants the maintainer the additional permission needed to keep the dual-license option open for everyone.

We recommend wiring this through [CLA Assistant](https://cla-assistant.io/) and configuring it to prompt on the first PR. Until the CLA is signed, the PR cannot be merged.

---

*The CLA terms described above are a summary. The full legal text will be provided when you sign. If you are contributing on behalf of an employer, ensure you have the authority to do so.*
