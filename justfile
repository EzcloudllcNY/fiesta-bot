# Shortcuts for everyday work. `just` on its own lists them.

# Pass recipe arguments through as "$@", so quoted text survives intact.
set positional-arguments

# List every recipe
default:
    @just --list --unsorted

# Start the bot 
run: 
    uv run fiesta run

# Bring the database up to the latest schema
migrate:
    uv run fiesta migrate

# Any fiesta command, e.g. `just fiesta settings list`
fiesta *args:
    uv run fiesta "$@"

# Run the tests; extra args go to pytest, e.g. `just test -k outbox`
test *args:
    uv run pytest "$@"

# Run the tests with a coverage report
cov:
    uv run pytest --cov=fiesta --cov-report=term-missing

# Type check
typecheck:
    uv run basedpyright

# Lint
lint:
    uv run ruff check .

# Format the code and apply the safe lint fixes
fmt:
    uv run ruff format .
    uv run ruff check --fix .

# Every check, changing nothing: format, lint, types, tests
check:
    uv run ruff format --check .
    uv run ruff check .
    uv run basedpyright
    uv run pytest
