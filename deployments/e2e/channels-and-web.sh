#!/usr/bin/env bash
# End to end through the containers (just up-all): a signed webhook becomes a task, the
# employee's answer goes back to the sender, the web app shows it all, and the sandbox runs code
# with no network.
#
#   1. a receiver container stands in for the customer's system (it logs what it is sent)
#   2. a webhook binding for the summariser, replying to that receiver
#   3. a signed message to channel-gateway; a forged one is refused
#   4. the message became a task (public API) and the task completed
#   5. the answer reached the receiver, signed by channel-gateway
#   6. the web app serves the task's page and the live API
#   7. the sandbox runs Python, with no network
#
# Run from the repo root:  just e2e
set -euo pipefail
export MSYS_NO_PATHCONV=1  # Git Bash on Windows: keep container paths as they are

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TENANT="0193a7c4-0000-7000-8000-00000000d3e0"
WEB="http://127.0.0.1:7020"
HOOKS="http://127.0.0.1:7013"
SECRET="e2e-signing-secret-$RANDOM$RANDOM"
RECEIVER="wfk-e2e-receiver"
T="\"tenant\":{\"tenant_id\":\"$TENANT\",\"actor\":{\"kind\":\"ACTOR_KIND_HUMAN\",\"id\":\"e2e\"}}"

host_path() { (cd "$1" && (pwd -W 2>/dev/null || pwd)); }
CHANNEL_PROTOS="$(host_path "$ROOT/microservices/channel-gateway/contracts/proto")"
SANDBOX_PROTOS="$(host_path "$ROOT/microservices/sandbox-runner/contracts/proto")"

grpc() {  # grpc <proto dir> <proto file> <address> <method> <json>
  docker run --rm --network wfk_default -v "$1:/protos" fullstorydev/grpcurl:v1.9.3 \
    -plaintext -import-path /protos -proto "$2" -d "$5" "$3" "$4"
}
field() { grep -o "\"$1\": *\"[^\"]*\"" | head -1 | sed 's/.*: *"\(.*\)"/\1/'; }
fail() { echo "FAIL: $*" >&2; exit 1; }
# The HTTP status of a request (output discarded in the shell: Windows' curl cannot write to
# /dev/null once path conversion is off).
status() { curl -s -w '
%{http_code}' "$@" | tail -n 1; }
cleanup() { docker rm -f "$RECEIVER" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "1. a receiver for replies"
cleanup
docker run -d --name "$RECEIVER" --network wfk_default python:3.13-alpine python -u -c '
import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["content-length"]))
        print(json.dumps({"sig": self.headers.get("x-wfk-signature"), "body": body.decode()}), flush=True)
        self.send_response(200); self.send_header("content-type", "application/json"); self.end_headers()
        self.wfile.write(b"{\"id\": \"received\"}")
    def log_message(self, *a): pass
http.server.HTTPServer(("0.0.0.0", 8080), H).serve_forever()
' >/dev/null
echo "   $RECEIVER listening"

echo "2. a webhook binding for the summariser"
B=$(grpc "$CHANNEL_PROTOS" wfk/channel_gateway/v1/channels.proto channel-gateway:7013 \
  wfk.channel_gateway.v1.ChannelService/CreateBinding \
  "{$T,\"idempotency_key\":{\"value\":\"e2e-$SECRET\"},\"kind\":\"CHANNEL_KIND_WEBHOOK\",\"employee_id\":\"summariser\",\"name\":\"e2e\",\"secret\":\"$SECRET\",\"outbound\":{\"url\":\"http://$RECEIVER:8080/reply\"}}")
HOOK=$(echo "$B" | field webhookPath)
[ -n "$HOOK" ] || fail "no binding: $B"
echo "   POST $HOOKS$HOOK"

echo "3. a signed message (and a forged one)"
MSG_ID="e2e-$RANDOM$RANDOM"
BODY="{\"id\":\"$MSG_ID\",\"from\":\"crm\",\"text\":\"Summarise the new lead from Acme (ref $MSG_ID): they want a demo next week.\"}"
SIG="sha256=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | sed 's/^.* //')"
CODE=$(status -X POST "$HOOKS$HOOK" -H "content-type: application/json" -H "x-wfk-signature: sha256=00" -d "$BODY")
[ "$CODE" = "401" ] || fail "a forged signature got HTTP $CODE"
for _ in 1 2; do  # delivered twice: one message, one task
  CODE=$(status -X POST "$HOOKS$HOOK" -H "content-type: application/json" -H "x-wfk-signature: $SIG" -d "$BODY")
  [ "$CODE" = "200" ] || fail "the signed message got HTTP $CODE"
done
echo "   forged: 401, signed: 200 (sent twice)"

echo "4. it became a task, and the task completed (public API)"
TASK=""
for _ in $(seq 1 60); do
  TASK=$(curl -s "$WEB/api/v1/tasks?employee_id=summariser&limit=50" | python -c "
import json, sys
tasks = [t for t in json.load(sys.stdin)['tasks'] if t['created_by'] == 'webhook:crm' and '$MSG_ID' in t['input']]
done = [t for t in tasks if t['status'] == 'completed']
print((done or tasks or [{}])[0].get('id', ''), (done or tasks or [{}])[0].get('status', ''), len(tasks))")
  case "$TASK" in *" completed "*) break ;; esac
  sleep 1
done
read -r TASK_ID STATUS COUNT <<<"$TASK"
[ "$STATUS" = "completed" ] || fail "no completed task from the webhook (got: $TASK)"
echo "   task $TASK_ID completed"

echo "5. the answer reached the sender, signed"
REPLY=""
for _ in $(seq 1 30); do
  REPLY=$(docker logs "$RECEIVER" 2>/dev/null | grep '"thread' | grep "$MSG_ID" | head -1 || true)
  [ -n "$REPLY" ] && break
  sleep 1
done
[ -n "$REPLY" ] || fail "no reply reached the receiver"
echo "$REPLY" | python -c "
import hashlib, hmac, json, sys
r = json.loads(sys.stdin.read())
want = 'sha256=' + hmac.new(b'$SECRET', r['body'].encode(), hashlib.sha256).hexdigest()
assert r['sig'] == want, 'bad signature on the reply'
print('   reply:', json.loads(r['body'])['text'][:60], '(signature ok)')"
REPLIES=$(docker logs "$RECEIVER" 2>/dev/null | grep -c "$MSG_ID" || true)
[ "$REPLIES" = "1" ] || fail "expected one reply, got $REPLIES"

echo "6. the web app"
PAGE=$(status "$WEB/tasks/$TASK_ID")
READY=$(curl -s "$WEB/readyz" | field status)
[ "$PAGE" = "200" ] && [ "$READY" = "ok" ] || fail "web app: page $PAGE, readiness $READY"
curl -s "$WEB/api/v1/tasks/$TASK_ID" | python -c "
import json, sys
d = json.load(sys.stdin)
assert d['run'] and d['run']['status'] == 'completed', d
print('   page 200, ready, run', d['run']['status'])"

echo "7. the sandbox"
OUT=$(grpc "$SANDBOX_PROTOS" wfk/sandbox_runner/v1/sandbox.proto sandbox-runner:7015 \
  wfk.sandbox_runner.v1.SandboxService/RunSandbox \
  "{\"tenant\":{\"tenant_id\":\"$TENANT\"},\"idempotency_key\":{\"value\":\"e2e-$RANDOM$RANDOM\"},\"image\":\"python-3.13\",\"code\":\"import socket\\ntry:\\n    socket.create_connection(('1.1.1.1', 53), timeout=3)\\n    print('ONLINE')\\nexcept OSError:\\n    print('offline', 6 * 7)\"}")
echo "$OUT" | grep -q 'offline 42' || fail "sandbox: $OUT"
echo "   python ran: offline 42"

echo "PASS: webhook -> task -> answer back to the sender (signed, once); web app; sandbox offline"
