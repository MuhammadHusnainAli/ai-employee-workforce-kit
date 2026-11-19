#!/usr/bin/env bash
# End to end through the containers (just up-all): a task's run is remembered by brain ingest,
# and the next run of the same employee recalls it.
#
#   1. task A (a unique fact) runs to completion through the task module (core-rust) and the agent runtime (core-python)
#   2. brain ingest turns run.completed into an episode in brain_db
#   3. task B asks about the same fact; its run calls Recall
#   4. B's run ledger cites a recall whose items include A's episode
#   5. the Brain Inspector lists that memory, with its trust and source
#
# Run from the repo root:  just e2e
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep /protos as a container path

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deployments/docker-compose.yml")
TENANT="0193a7c4-0000-7000-8000-00000000d3e0"
EMPLOYEE="summariser"
T="\"tenant\":{\"tenant_id\":\"$TENANT\",\"actor\":{\"id\":\"e2e\"}}"
TAG="zephyr-$RANDOM$RANDOM"

host_path() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }
TASK_PROTOS="$(host_path "$ROOT/microservices/core-rust/contracts/proto")"
BRAIN_PROTOS="$(host_path "$ROOT/microservices/core-python/contracts/proto")"

grpc() {  # grpc <proto dir> <proto file> <address> <method> <json>
  docker run --rm --network wfk_default -v "$1:/protos" fullstorydev/grpcurl:v1.9.3 \
    -plaintext -import-path /protos -proto "$2" -d "$5" "$3" "$4"
}
task() { grpc "$TASK_PROTOS" wfk/task_service/v1/tasks.proto core-rust:7002 \
  "wfk.task_service.v1.TaskService/$1" "$2"; }
sql() { "${COMPOSE[@]}" exec -T postgres psql -U wfk -d "$1" -Atc "$2"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

run_task() {  # run_task <input> -> prints the task id once it completed
  local out id status=""
  out=$(task CreateTask "{$T,\"idempotency_key\":{\"value\":\"e2e-$RANDOM$RANDOM\"},\"employee_id\":\"$EMPLOYEE\",\"input\":\"$1\"}")
  id=$(echo "$out" | grep -o '"taskId": "[^"]*"' | cut -d'"' -f4)
  [ -n "$id" ] || fail "CreateTask returned no task id: $out"
  for _ in $(seq 1 60); do
    status=$(task GetTask "{$T,\"task_id\":\"$id\"}" | grep -o '"status": "[^"]*"' | cut -d'"' -f4)
    case "$status" in
      TASK_STATUS_COMPLETED) echo "$id"; return 0 ;;
      TASK_STATUS_FAILED|TASK_STATUS_CANCELLED) fail "task $id ended $status" ;;
    esac
    sleep 1
  done
  fail "task $id did not finish (last status: $status)"
}

echo "1. task A: a fact to remember"
A=$(run_task "Project $TAG ships on Friday; Ana owns the launch checklist.")
echo "   run $A completed"

echo "2. brain ingest remembers it"
MEMORY=""
for _ in $(seq 1 60); do
  MEMORY=$(sql brain_db "select memory_id from episodes where run_id = '$A'")
  [ -n "$MEMORY" ] && break
  sleep 1
done
[ -n "$MEMORY" ] || fail "no episode for run $A in brain_db"
echo "   episode memory $MEMORY: $(sql brain_db "select trust || ', ' || status from memories where id = '$MEMORY'")"

echo "3. task B: the same employee needs it"
B=$(run_task "When does project $TAG ship, and who owns the checklist?")
echo "   run $B completed"

echo "4. B's run recalled A's memory"
RECALLS=$(sql run_db "select string_agg(recall_id::text, ',') from run_steps where run_id = '$B' and recall_id is not null")
[ -n "$RECALLS" ] || fail "run $B cited no recall (is WFK_BRAIN_ADDRESS set on the worker?)"
FOUND=$(sql brain_db "select count(*) from recall_log where id = any(string_to_array('$RECALLS', ',')::uuid[]) and '$MEMORY' = any(item_ids)")
[ "$FOUND" -ge 1 ] || fail "recalls $RECALLS of run $B do not include memory $MEMORY"
echo "   recall_log cites memory $MEMORY"

echo "5. the Brain Inspector shows it"
LISTED=$(grpc "$BRAIN_PROTOS" wfk/brain_service/v1/brain.proto core-python:7001 \
  wfk.brain_service.v1.BrainService/GetMemory "{$T,\"memory_id\":\"$MEMORY\"}")
echo "$LISTED" | grep -q "\"runId\": \"$A\"" || fail "GetMemory does not show run $A: $LISTED"
echo "   GetMemory: episode of run $A, trust $(echo "$LISTED" | grep -o '"trust": "[^"]*"' | head -1 | cut -d'"' -f4)"

echo "PASS: remembered by brain ingest, recalled by the next run, visible in the Inspector"
