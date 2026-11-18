//! Real runs in hardened containers (needs Docker and the local stack: `just up`).
//! Each test checks one promise from inside the sandbox.

use std::sync::Arc;
use std::time::Instant;

use sqlx::{Connection, Executor, PgConnection};
use tokio::sync::{OnceCell, Semaphore};
use tonic::Request;
use uuid::Uuid;

// This service's own crate, kept in its own block so its position does not depend on the name.
use sandbox_runner::adapters::{DockerRunner, Executions, Outcome};
use sandbox_runner::api::SandboxApi;
use sandbox_runner::contracts::wfk::common::v1::{IdempotencyKey, TenantContext};
use sandbox_runner::contracts::wfk::sandbox_runner::v1 as pb;
use sandbox_runner::contracts::wfk::sandbox_runner::v1::sandbox_service_server::SandboxService;
use sandbox_runner::domain::{Limits, Spec};
use sandbox_runner::platform::{Database, migrate};

const T1: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_0000000000e1);
const TEST_DB: &str = "sandbox_runner_rs_test";

fn runner() -> DockerRunner {
    DockerRunner::connect(std::env::var("WFK_SANDBOX_RUNTIME").ok()).expect("docker")
}

async fn run(image: &str, code: &str, stdin: &str, limits: Limits) -> Outcome {
    let spec = Spec::new(image, code.into(), stdin.into(), limits).unwrap();
    runner()
        .run(&spec, &format!("wfk-sandbox-test-{}", Uuid::new_v4()))
        .await
        .unwrap()
}

async fn py(code: &str) -> Outcome {
    run("python-3.13", code, "", Limits::default()).await
}

#[tokio::test]
#[ignore = "needs Docker: just up"]
async fn runs_code_with_stdin_and_reports_the_exit_code() {
    let o = run(
        "python-3.13",
        "import sys\nprint(sys.stdin.read().upper())\nsys.exit(3)",
        "hello sandbox",
        Limits::default(),
    )
    .await;
    assert_eq!((o.exit_code, o.stdout.trim()), (3, "HELLO SANDBOX"));
    assert!(!o.timed_out && !o.oom_killed);
    let node = run("node-22", "console.log(6 * 7)", "", Limits::default()).await;
    assert_eq!((node.exit_code, node.stdout.trim()), (0, "42"));
}

#[tokio::test]
#[ignore = "needs Docker: just up"]
async fn there_is_no_network() {
    let o = py(concat!(
        "import socket\n",
        "try:\n",
        "    socket.create_connection(('1.1.1.1', 53), timeout=3)\n",
        "    print('CONNECTED')\n",
        "except OSError as e:\n",
        "    print('blocked', type(e).__name__)\n",
        "print([n for _, n in socket.if_nameindex()])\n",
    ))
    .await;
    assert!(o.stdout.contains("blocked"), "{o:?}");
    assert!(!o.stdout.contains("eth0"), "only loopback: {}", o.stdout);
}

#[tokio::test]
#[ignore = "needs Docker: just up"]
async fn the_root_is_read_only_and_the_user_is_nobody() {
    let o = py(concat!(
        "import os\n",
        "print('uid', os.getuid())\n",
        "try:\n",
        "    open('/etc/owned', 'w').write('x')\n",
        "    print('WROTE ROOT')\n",
        "except OSError:\n",
        "    print('root read-only')\n",
        "open('/tmp/ok', 'w').write('x')\n",
        "print('tmp writable')\n",
        "print('caps', open('/proc/self/status').read().split('CapEff:')[1].split()[0])\n",
    ))
    .await;
    assert!(o.stdout.contains("uid 65534"), "{o:?}");
    assert!(o.stdout.contains("root read-only") && o.stdout.contains("tmp writable"));
    assert!(
        o.stdout.contains("caps 0000000000000000"),
        "no capabilities: {}",
        o.stdout
    );
}

#[tokio::test]
#[ignore = "needs Docker: just up"]
async fn runaway_code_is_killed_at_the_deadline() {
    let limits = Limits::resolve(2_000, 0, 0, 0).unwrap();
    let started = Instant::now();
    let o = run("python-3.13", "while True: pass", "", limits).await;
    assert!(o.timed_out, "{o:?}");
    assert!(started.elapsed().as_secs() < 15, "killed promptly");
}

#[tokio::test]
#[ignore = "needs Docker: just up"]
async fn memory_and_processes_are_capped() {
    let small = Limits::resolve(20_000, 64, 0, 0).unwrap();
    let o = run(
        "python-3.13",
        "x = bytearray(512 * 1024 * 1024)\nprint('ALLOCATED')",
        "",
        small,
    )
    .await;
    assert!(!o.stdout.contains("ALLOCATED"), "{o:?}");
    assert!(o.oom_killed || o.exit_code != 0);

    let few = Limits::resolve(20_000, 0, 0, 8).unwrap();
    let o = run(
        "python-3.13",
        concat!(
            "import threading, time\n",
            "n = 0\n",
            "try:\n",
            "    for _ in range(50):\n",
            "        threading.Thread(target=time.sleep, args=(5,), daemon=True).start()\n",
            "        n += 1\n",
            "except RuntimeError:\n",
            "    pass\n",
            "print('threads', n)\n",
        ),
        "",
        few,
    )
    .await;
    let n: u32 = o
        .stdout
        .trim()
        .rsplit(' ')
        .next()
        .unwrap_or("99")
        .parse()
        .unwrap_or(99);
    assert!(n < 50, "the pids limit stops it: {o:?}");
}

// ---- through the API: validation and idempotency ---------------------------------------------

fn admin_url() -> String {
    std::env::var("WFK_TEST_ADMIN_URL")
        .unwrap_or_else(|_| "postgresql://wfk:wfk@127.0.0.1:7000/postgres".into())
}

fn db_url() -> String {
    format!("{}/{TEST_DB}", admin_url().rsplit_once('/').unwrap().0)
}

static CREATED: OnceCell<()> = OnceCell::const_new();

async fn api() -> SandboxApi {
    CREATED
        .get_or_init(|| async {
            let mut admin = PgConnection::connect(&admin_url()).await.expect("just up");
            admin
                .execute("DROP DATABASE IF EXISTS sandbox_runner_rs_test WITH (FORCE)")
                .await
                .unwrap();
            migrate::ensure_database(&db_url()).await.unwrap();
            let pool = sqlx::PgPool::connect(&db_url()).await.unwrap();
            migrate::up(&pool).await.unwrap();
        })
        .await;
    let db = Database::connect(&db_url(), 5)
        .await
        .unwrap()
        .with_tenant_role("wfk_app");
    SandboxApi {
        runner: runner(),
        executions: Executions::new(db, "cell-test"),
        slots: Arc::new(Semaphore::new(2)),
    }
}

fn req(key: &str, image: &str, code: &str) -> pb::RunSandboxRequest {
    pb::RunSandboxRequest {
        tenant: Some(TenantContext {
            tenant_id: T1.to_string(),
            ..Default::default()
        }),
        idempotency_key: Some(IdempotencyKey { value: key.into() }),
        run_id: "run-1".into(),
        image: image.into(),
        code: code.into(),
        stdin: String::new(),
        limits: None,
    }
}

#[tokio::test]
#[ignore = "needs Docker and the local stack: just up"]
async fn a_retry_returns_the_first_outcome_without_running_again() {
    let api = api().await;
    let key = Uuid::new_v4().to_string();
    let code = "import random\nprint(random.random())";
    let first = api
        .run_sandbox(Request::new(req(&key, "python-3.13", code)))
        .await
        .unwrap()
        .into_inner();
    let again = api
        .run_sandbox(Request::new(req(&key, "python-3.13", code)))
        .await
        .unwrap()
        .into_inner();
    assert!(!first.replayed && again.replayed);
    assert_eq!(
        first.stdout, again.stdout,
        "the same random number: it did not run twice"
    );
    let usage: i64 = sqlx::query_scalar(
        "SELECT count(*) FROM outbox WHERE type = 'usage.sandbox' AND subject = $1",
    )
    .bind(&first.execution_id)
    .fetch_one(&api.executions.db.pool)
    .await
    .unwrap();
    assert_eq!(usage, 1);
}

#[tokio::test]
#[ignore = "needs Docker and the local stack: just up"]
async fn bad_requests_never_reach_docker() {
    let api = api().await;
    for r in [
        req("k", "ubuntu:latest", "echo hi"),
        req("k", "python-3.13", "  "),
        req("", "python-3.13", "print(1)"),
        pb::RunSandboxRequest {
            limits: Some(pb::Limits {
                timeout_ms: 999_999,
                ..Default::default()
            }),
            ..req("k", "python-3.13", "print(1)")
        },
    ] {
        let err = api.run_sandbox(Request::new(r)).await.unwrap_err();
        assert_eq!(err.code(), tonic::Code::InvalidArgument, "{err:?}");
    }
}
