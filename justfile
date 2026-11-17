# Repo-level commands only: the local stack and creating services.
# Everything else (lint, test, migrate, image) is in each service's own justfile:
#   cd microservices/<service> && just --list
# Install just with: uv tool install rust-just

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

compose := "docker compose -f deployments/docker-compose.yml"

default:
    @just --list

# Start the local stack and wait until healthy, then create S3 buckets
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

# Create a self-contained service from a template: just new-service rust task-service
new-service lang name:
    python microservices/templates/new_service.py {{lang}} {{name}}
