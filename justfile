# Common commands. Install just with: uv tool install rust-just
# Recipes are plain commands so they run the same in bash and PowerShell.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]
set dotenv-load := false

compose := "docker compose -f deployments/docker-compose.yml"

default:
    @just --list

# Install Python and Rust dependencies
sync:
    uv sync --all-packages
    cargo fetch

# Lint and type-check everything
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run pyright
    cargo fmt --all --check
    cargo clippy --workspace --all-targets -- -D warnings

# Format everything
fmt:
    uv run ruff format .
    uv run ruff check --fix .
    cargo fmt --all

# Unit tests (no docker needed)
test:
    uv run pytest -m "not integration"
    cargo test --workspace

# Integration tests (needs: just up)
test-integration:
    uv run pytest -m integration
    cargo test --workspace -- --ignored

# Start the local stack
up:
    {{compose}} up -d --wait --remove-orphans
    {{compose}} --profile init run --rm seaweedfs-init

# Stop the local stack (keeps data)
down:
    {{compose}} down

# Stop the local stack and delete its data
nuke:
    {{compose}} down -v

# Show the local stack
ps:
    {{compose}} ps

buf := "npx -y @bufbuild/buf@1.47.2"

# Lint protobuf contracts
contracts-lint:
    cd contracts; {{buf}} lint

# Check contracts for breaking changes against main
contracts-breaking:
    cd contracts; {{buf}} breaking --against "../.git#branch=main,subdir=contracts"

# Generate code from contracts (Rust generates in build.rs; this regenerates Python)
gen:
    uv run --package wfk-contracts --group dev python libs/python/wfk-contracts/tools/gen_python.py
    cargo build -p wfk-contracts

# Fail if committed generated code is out of date
gen-check: gen
    git diff --exit-code -- libs/python/wfk-contracts/src

pg := "postgresql://wfk:wfk@127.0.0.1:5432"

# Apply pending migrations from a folder to a local database (created if missing)
migrate dir database:
    uv run wfk-migrate up --dsn {{pg}}/{{database}} --dir {{dir}} --create-db

# Revert the latest migration
migrate-down dir database:
    uv run wfk-migrate down --dsn {{pg}}/{{database}} --dir {{dir}}

# Show applied and pending migrations
migrate-status dir database:
    uv run wfk-migrate status --dsn {{pg}}/{{database}} --dir {{dir}}

# Create a service from a template: just new-service rust task-service
new-service lang name:
    uv run python tools/new_service.py {{lang}} {{name}}
