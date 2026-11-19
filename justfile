# Repo-level commands only: the local stack.
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

# Start the stack and every microservice in containers (builds images), wait until healthy
up-all: up
    {{compose}} --profile services up -d --build --wait

# Rebuild and restart one microservice container: just restart core-rust
restart service:
    {{compose}} --profile services up -d --build --wait {{service}}

# Follow a microservice's logs: just logs core-python
logs service:
    {{compose}} --profile services logs -f {{service}}

# End-to-end checks against the running containers (needs: just up-all)
e2e:
    bash deployments/e2e/remember-then-recall.sh
    bash deployments/e2e/feedback-to-rule.sh
    bash deployments/e2e/channels-and-web.sh

# Stop the local stack and the microservices (keeps data)
down:
    {{compose}} --profile services down

# Stop everything and delete its data
nuke:
    {{compose}} --profile services down -v

# Show the local stack and the microservices
ps:
    {{compose}} --profile services ps
