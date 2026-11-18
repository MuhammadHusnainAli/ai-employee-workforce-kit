#!/usr/bin/env bash
# End to end through the containers (just up-all): a person's correction becomes a rule, a second
# person approves it, and the employee's next run has it pinned; graph recall links the runs.
#
#   1. task A runs (it names a project); brain-ingest stores its episode and the project entity
#   2. a person corrects run A through Brain.Feedback: a rule proposal appears
#   3. evaluation-service runs the suites with and without the rule; it must pass
#   4. the proposal is high risk, so its author cannot approve it; a second person does
#   5. task B runs: brain-service pins the rule in B's recall (and in the Valkey fallback)
#   6. B's recall log shows the graph retriever found A's episode through the project
#
# Run from the repo root:  just e2e
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep /protos as a container path

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deployments/docker-compose.yml")
TENANT="0193a7c4-0000-7000-8000-00000000d3e0"
EMPLOYEE="summariser"
TAG="Orion$RANDOM"

host_path() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }
TASK_PROTOS="$(host_path "$ROOT/microservices/task-service/contracts/proto")"
BRAIN_PROTOS="$(host_path "$ROOT/microservices/brain-service/contracts/proto")"

as() {  # as <person id>: a TenantContext JSON fragment for a human actor
  echo "\"tenant\":{\"tenant_id\":\"$TENANT\",\"actor\":{\"kind\":\"ACTOR_KIND_HUMAN\",\"id\":\"$1\"}}"
}
grpc() {  # grpc <proto dir> <proto file> <address> <method> <json>
  docker run --rm --network wfk_default -v "$1:/protos" fullstorydev/grpcurl:v1.9.3 \
    -plaintext -import-path /protos -proto "$2" -d "$5" "$3" "$4"
}
task() { grpc "$TASK_PROTOS" wfk/task_service/v1/tasks.proto task-service:7004 \
  "wfk.task_service.v1.TaskService/$1" "$2"; }
brain() { grpc "$BRAIN_PROTOS" wfk/brain_service/v1/brain.proto brain-service:7003 \
  "wfk.brain_service.v1.BrainService/$1" "$2"; }
sql() { "${COMPOSE[@]}" exec -T postgres psql -U wfk -d "$1" -Atc "$2"; }
field() { grep -o "\"$1\": \"[^\"]*\"" | head -1 | cut -d'"' -f4; }
fail() { echo "FAIL: $*" >&2; exit 1; }

run_task() {  # run_task <input> -> prints the task id once it completed
  local out id status=""
  out=$(task CreateTask "{$(as e2e),\"idempotency_key\":{\"value\":\"e2e-$RANDOM$RANDOM\"},\"employee_id\":\"$EMPLOYEE\",\"input\":\"$1\"}")
  id=$(echo "$out" | field taskId)
  [ -n "$id" ] || fail "CreateTask returned no task id: $out"
  for _ in $(seq 1 60); do
    status=$(task GetTask "{$(as e2e),\"task_id\":\"$id\"}" | field status)
    case "$status" in
      TASK_STATUS_COMPLETED) echo "$id"; return 0 ;;
      TASK_STATUS_FAILED|TASK_STATUS_CANCELLED) fail "task $id ended $status" ;;
    esac
    sleep 1
  done
  fail "task $id did not finish (last status: $status)"
}

echo "1. task A names Project $TAG"
A=$(run_task "Draft the status note for Project $TAG: the vendor quote arrived.")
for _ in $(seq 1 60); do
  N=$(sql brain_db "select count(*) from memory_entities me join entities e on e.id = me.entity_id
                    join episodes ep on ep.memory_id = me.memory_id
                    where ep.run_id = '$A' and e.norm_name = lower('project $TAG')")
  [ "$N" = "1" ] && break
  sleep 1
done
[ "$N" = "1" ] || fail "run $A's episode is not linked to the entity Project $TAG"
echo "   run $A remembered, linked to the entity Project $TAG"

RULE="Before you pay any vendor for Project $TAG, ask Maria first."
echo "2. Maria corrects run A"
P=$(brain Feedback "{$(as maria),\"idempotency_key\":{\"value\":\"fb-$A\"},\"employee_id\":\"$EMPLOYEE\",\"run_id\":\"$A\",\"kind\":\"FEEDBACK_KIND_CORRECTION\",\"text\":\"$RULE\"}" | field proposalId)
[ -n "$P" ] || fail "the correction proposed no rule"
echo "   proposal $P"

echo "3. evaluation-service evaluates it (golden, regression and safety suites)"
EVAL=""
for _ in $(seq 1 240); do
  EVAL=$(brain ListProposals "{$(as maria),\"employee_id\":\"$EMPLOYEE\"}" | python -c "
import json, sys
ps = [p for p in json.load(sys.stdin).get('proposals', []) if p['proposalId'] == '$P']
print(ps[0].get('evalStatus', 'pending') if ps else 'missing')")
  [ "$EVAL" = "passed" ] || [ "$EVAL" = "failed" ] && break
  sleep 1
done
[ "$EVAL" = "passed" ] || fail "the evaluation did not pass (status: $EVAL)"
echo "   evaluation passed"

echo "4. high risk: Maria cannot approve her own rule, Omar can"
if brain DecideProposal "{$(as maria),\"proposal_id\":\"$P\",\"approve\":true}" >/dev/null 2>&1; then
  fail "the author approved her own high-risk rule"
fi
STATUS=$(brain DecideProposal "{$(as omar),\"proposal_id\":\"$P\",\"approve\":true}" | field status)
[ "$STATUS" = "approved" ] || fail "Omar's approval gave status '$STATUS'"
echo "   approved by omar"

echo "5. task B: the rule is pinned"
B=$(run_task "Pay the vendor for Project $TAG.")
PINNED=$("${COMPOSE[@]}" exec -T valkey valkey-cli GET "wfk:brain:pinned:v1:$TENANT:$EMPLOYEE")
echo "$PINNED" | grep -qF "$RULE" || fail "the pinned context does not carry the rule: $PINNED"
echo "   B's recall pinned: $RULE"

echo "6. graph recall found A's episode for B"
HITS=$(sql brain_db "select max((retrievers->'graph'->>'hits')::int) from recall_log where run_id = '$B'")
EPISODE=$(sql brain_db "select memory_id from episodes where run_id = '$A'")
CITED=$(sql brain_db "select count(*) from recall_log where run_id = '$B' and '$EPISODE' = any(item_ids)")
[ "${HITS:-0}" -ge 1 ] || fail "the graph retriever found nothing for run $B"
[ "$CITED" -ge 1 ] || fail "run $B's recall does not cite A's episode $EPISODE"
echo "   graph hits: $HITS, A's episode cited"

echo "PASS: correction -> proposal -> evaluation -> four-eyes approval -> rule pinned; graph links the runs"
