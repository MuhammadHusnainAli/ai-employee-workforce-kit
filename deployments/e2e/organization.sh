#!/usr/bin/env bash
# End to end through the containers (just up-all): the AI organisation.
#
#   1. a department, and a co-CEO (hired once per workspace, reused after)
#   2. the person hires a head of sales under the co-CEO
#   3. the co-CEO asks for a hire; nothing is hired until the person approves it
#   4. the person gives the co-CEO a goal: it becomes the co-CEO's task, which runs
#   5. the goal is the top of its own task tree in the public API
#   6. the head of sales calls the co-CEO with a question and gets an answer
#   7. the person messages the head of sales; the conversation shows up for both
#   8. a standup (no discussion) collects everyone's update and sums up
#
# The model is local-mock, so employees answer but do not choose tools; the delegation loop
# itself is covered by core-python's workflow tests.
#
# Run from the repo root:  just e2e
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep container paths as they are

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TENANT="0193a7c4-0000-7000-8000-00000000d3e0"
WEB="http://127.0.0.1:7000/api/v1"
RUN="$RANDOM$RANDOM"

host_path() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }
PY_PROTOS="$(host_path "$ROOT/microservices/core-python/contracts/proto")"

grpc() {  # grpc <proto dir> <proto file> <address> <method> <json>
  docker run --rm --network wfk_default -v "$1:/protos" fullstorydev/grpcurl:v1.9.3 \
    -plaintext -import-path /protos -proto "$2" -d "$5" "$3" "$4"
}
as_employee() {
  echo "\"tenant\":{\"tenant_id\":\"$TENANT\",\"employee_id\":\"$1\",\"actor\":{\"kind\":\"ACTOR_KIND_EMPLOYEE\",\"id\":\"$1\"}}"
}
api() {  # api <method> <path> [json]: the public API as the person
  local args=(-s -X "$1" "$WEB$2" -H "x-wfk-actor: e2e" -H "content-type: application/json")
  [ "$1" = POST ] && args+=(-H "idempotency-key: e2e-$RUN-$RANDOM")
  [ $# -ge 3 ] && args+=(-d "$3")
  curl "${args[@]}"
}
field() { grep -o "\"$1\": *\"[^\"]*\"" | head -1 | sed 's/.*: *"\(.*\)"/\1/'; }
fail() { echo "FAIL: $*" >&2; exit 1; }
wait_for() {  # wait_for <what> <command...>: retry for up to 90 s
  local what="$1"; shift
  for _ in $(seq 1 90); do "$@" >/dev/null 2>&1 && return 0; sleep 1; done
  fail "$what"
}

echo "1. a department and a co-CEO"
api POST /org/departments '{"id":"sales","name":"Sales"}' | grep -q '"id":"sales"' \
  || fail "no sales department"
CEO=$(api GET /org | grep -o '"id":"[^"]*","name":"[^"]*","title":"[^"]*","department_id":[^,]*,"manager_id":[^,]*,"is_co_ceo":true' \
  | field id || true)
if [ -z "$CEO" ]; then
  CEO="co-ceo"
  api POST /org/hires "{\"id\":\"$CEO\",\"name\":\"Cora\",\"title\":\"Co-CEO\",\"is_co_ceo\":true,\"role\":\"Runs the company for the CEO.\",\"model\":\"local-mock\"}" \
    | grep -q '"is_co_ceo":true' || fail "could not hire the co-CEO"
fi
echo "   co-CEO: $CEO"

echo "2. the person hires a head of sales"
HEAD="sales-head-$RUN"
api POST /org/hires "{\"id\":\"$HEAD\",\"name\":\"Sam\",\"title\":\"Head of Sales\",\"department_id\":\"sales\",\"manager_id\":\"$CEO\",\"role\":\"Leads sales.\",\"model\":\"local-mock\"}" \
  | grep -q "\"manager_id\":\"$CEO\"" || fail "head of sales not placed under the co-CEO"
echo "   $HEAD reports to $CEO"

echo "3. the co-CEO asks for a hire; only the person can approve it"
INTERN="sales-intern-$RUN"
REQ=$(grpc "$PY_PROTOS" wfk/workforce_service/v1/workforce.proto core-python:7001 \
  wfk.workforce_service.v1.WorkforceService/RequestHire \
  "{$(as_employee "$CEO"),\"idempotency_key\":{\"value\":\"e2e-hire-$RUN\"},\"reason\":\"Too many leads\",\"hire\":{\"employee_id\":\"$INTERN\",\"name\":\"Ivy\",\"title\":\"Sales Intern\",\"department_id\":\"sales\",\"manager_id\":\"$HEAD\",\"role\":\"Qualifies leads.\",\"model\":\"local-mock\"}}" \
  | field requestId)
[ -n "$REQ" ] || fail "no hire request"
api GET /org | grep -q "\"id\":\"$INTERN\"" && fail "hired before the person approved"
api POST "/org/hire-requests/$REQ/decide" '{"approved":true,"note":"Go"}' \
  | grep -q '"status":"approved"' || fail "approval failed"
api GET /org | grep -q "\"id\":\"$INTERN\"" || fail "approved hire not in the chart"
echo "   $INTERN hired under $HEAD after approval"

echo "4. a goal for the co-CEO"
GOAL=$(api POST /org/goals '{"goal":"Book three demos this week."}' | field id)
[ -n "$GOAL" ] || fail "no goal task"
done_task() { api GET "/tasks/$1" | grep -q '"status":"completed"'; }
wait_for "the goal never completed" done_task "$GOAL"
echo "   goal $GOAL completed by $CEO"

echo "5. the goal is the top of its own tree in the public API"
api GET "/tasks/$GOAL" | grep -q "\"root_task_id\":\"$GOAL\"" || fail "goal is not its own root"
api GET "/tasks/$GOAL" | grep -q '"depth":0' || fail "goal is not at depth 0"
api GET "/tasks?root=$GOAL" | grep -q "\"id\":\"$GOAL\"" || fail "root filter"
echo "   goal $GOAL at depth 0 (sub-task rules: core-rust tests/tasks.rs)"

echo "6. the head of sales calls the co-CEO"
CALL=$(grpc "$PY_PROTOS" wfk/collaboration_service/v1/collaboration.proto core-python:7001 \
  wfk.collaboration_service.v1.CollaborationService/AskColleague \
  "{$(as_employee "$HEAD"),\"idempotency_key\":{\"value\":\"e2e-call-$RUN\"},\"colleague\":\"$CEO\",\"question\":\"Which accounts first?\"}")
echo "$CALL" | grep -q '"answer"' || fail "no answer on the call"
CALL_ID=$(echo "$CALL" | field conversationId)
api GET "/conversations/$CALL_ID" | grep -q '"kind":"answer"' || fail "the call is not readable"
echo "   call $CALL_ID answered"

echo "7. the person messages the head of sales"
api POST /conversations "{\"to\":\"$HEAD\",\"text\":\"Keep me posted.\"}" | grep -q '"author_kind":"person"' \
  || fail "message not sent"
api GET "/conversations?participant=$HEAD" | grep -q '"kind":"direct"' || fail "message not listed"
echo "   direct conversation listed for $HEAD"

echo "8. a standup run by the co-CEO"
SID=$(api POST /standups '{"department_id":"sales","discussion_s":0}' | field id)
[ -n "$SID" ] || fail "no standup"
standup_done() { api GET "/standups/$SID" | grep -q '"status":"done"'; }
wait_for "the standup never finished" standup_done
S=$(api GET "/standups/$SID")
echo "$S" | grep -q "\"led_by\":\"$CEO\"" || fail "not led by the co-CEO"
echo "$S" | grep -q "\"employee_id\":\"$HEAD\"" || fail "no update from the head of sales"
echo "$S" | grep -q "\"employee_id\":\"$INTERN\"" || fail "no update from the intern"
api GET "/conversations/$SID" | grep -q '"kind":"summary"' || fail "no summary in the standup"
echo "   standup $SID: updates from sales, summed up by $CEO"

echo "PASS: organisation"
