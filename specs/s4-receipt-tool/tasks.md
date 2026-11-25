# S4.2 Receipt tool: tasks

Files you own: `scripts/receipt.py`, `tests/test_receipt_tool.py`. Edit nothing else.

Allowed shell commands, exactly: `uv run ruff check`, `uv run ruff format <your files>`, `uv run ruff format --check`, `uv run mypy`, `uv run pytest -q` (optionally with paths or `-k`), `uv run python scripts/receipt.py --file <path under $CV_SCRATCH/s4-receipt-tool/>`, `uv run python <script under $CV_SCRATCH/s4-receipt-tool/>`, `grep`, `ls`, `cat`, `mkdir`. Nothing else: no cd, no pipes, no redirection, no git, no curl, no env, no psql. Never connect to a real database. Run every command in the foreground and wait for it.

- [x] 1. Read `specs/STEERING.md`, `requirements.md`, `deploy/schema.sql`, `AnswerFactObject` and `ProviderCall` in `contracts.py`, `config.read_secret`, `persistence.py` (connection helper).
- [x] 2. R1, R2, R4.
- [x] 3. R3.
- [x] 4. R5 tests; run the tool with `--file` on a fixture fact you write under scratch.
- [x] 5. All steering checks. Print a short summary.
