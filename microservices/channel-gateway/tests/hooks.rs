//! Webhooks and bindings against the local stack (`just up`). Run with `just test-integration`.

use axum::body::Body;
use axum::http::{Request, StatusCode};
use base64::Engine;
use http_body_util::BodyExt;
use serde_json::{Value, json};
use sqlx::{Connection, Executor, PgConnection};
use tokio::net::TcpListener;
use tokio::sync::OnceCell;
use tonic::Request as RpcRequest;
use tower::ServiceExt;
use uuid::Uuid;

// This service's own crate, kept in its own block so its position does not depend on the name.
use channel_gateway::adapters::{Channels, GoogleTokens};
use channel_gateway::api::{ChannelApi, HookState, hooks};
use channel_gateway::contracts::wfk::channel_gateway::v1 as pb;
use channel_gateway::contracts::wfk::channel_gateway::v1::channel_service_server::ChannelService;
use channel_gateway::contracts::wfk::common::v1::{Actor, IdempotencyKey, TenantContext};
use channel_gateway::domain::{sign_slack, sign_teams, sign_wfk};
use channel_gateway::platform::{Database, migrate};

const T1: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_0000000000c1);
const T2: Uuid = Uuid::from_u128(0x0193a7c4_0000_7000_8000_0000000000c2);
const TEST_DB: &str = "channel_gateway_rs_hooks_test";
const SECRET: &str = "s3cret-signing-key";
const GOOGLE_KEY: &str = include_str!("fixtures/google_chat_test_key.pem");
const GOOGLE_N: &str = "-A-Bj6T3aOk9HWHWLv1HzQkSZFtPBvxpQyaCC-KYrLR1AEQle9K4HdHJXM1ygnL2mNAALKYAb6wuD5E3_Y9nx1twhAj2rncZvSQ5OuBHWvBXHffksAKcjm6eJF4xnxgu7dglzUgQxKCsQRUFx4fgCthufVxRz8h214W72mmWzD711-RwpZMFwjKnH_vgOcRDj0IO7pUk2zjCOpaqefb6gsiXatf_XupBhpgI8WomhFtPog4Mg3TwKF6oheyD9q-DO2zUI5oqEEnOA0teGZL89qDTr8VrbcBwH5a7jaW3PsSwIu-lChyV1oeI5n6KYTZuLSKKUcFDpQNGp5c_iIRl7w";

fn admin_url() -> String {
    std::env::var("WFK_TEST_ADMIN_URL")
        .unwrap_or_else(|_| "postgresql://wfk:wfk@127.0.0.1:7017/postgres".into())
}

fn db_url() -> String {
    format!("{}/{TEST_DB}", admin_url().rsplit_once('/').unwrap().0)
}

static CREATED: OnceCell<()> = OnceCell::const_new();

async fn db() -> Database {
    CREATED
        .get_or_init(|| async {
            let mut admin = PgConnection::connect(&admin_url()).await.expect("just up");
            admin
                .execute("DROP DATABASE IF EXISTS channel_gateway_rs_hooks_test WITH (FORCE)")
                .await
                .unwrap();
            migrate::ensure_database(&db_url()).await.unwrap();
            let pool = sqlx::PgPool::connect(&db_url()).await.unwrap();
            migrate::up(&pool).await.unwrap();
        })
        .await;
    Database::connect(&db_url(), 5)
        .await
        .unwrap()
        .with_tenant_role("wfk_app")
}

/// Serves a JWKS with the test key, as Google does for Chat.
async fn jwks_server() -> String {
    let jwks = json!({"keys": [{"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "test-1",
                                "n": GOOGLE_N, "e": "AQAB"}]});
    let app = axum::Router::new().route(
        "/jwks",
        axum::routing::get(move || {
            let jwks = jwks.clone();
            async move { axum::Json(jwks) }
        }),
    );
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{addr}/jwks")
}

struct World {
    channels: Channels,
    app: axum::Router,
}

async fn world() -> World {
    let channels = Channels::new(db().await, "cell-test");
    let app = hooks(HookState {
        channels: channels.clone(),
        google: GoogleTokens::new(jwks_server().await),
    });
    World { channels, app }
}

fn tenant(t: Uuid) -> Option<TenantContext> {
    Some(TenantContext {
        tenant_id: t.to_string(),
        actor: Some(Actor {
            kind: 1,
            id: "maria".into(),
        }),
        ..Default::default()
    })
}

async fn bind(w: &World, t: Uuid, kind: pb::ChannelKind, secret: &str) -> pb::Binding {
    ChannelApi(w.channels.clone())
        .create_binding(RpcRequest::new(pb::CreateBindingRequest {
            tenant: tenant(t),
            idempotency_key: Some(IdempotencyKey {
                value: Uuid::new_v4().to_string(),
            }),
            kind: kind as i32,
            employee_id: "summariser".into(),
            name: String::new(),
            secret: secret.into(),
            outbound: None,
        }))
        .await
        .unwrap()
        .into_inner()
        .binding
        .unwrap()
}

async fn post(
    w: &World,
    path: &str,
    headers: &[(&str, String)],
    body: &[u8],
) -> (StatusCode, Value) {
    let mut req = Request::post(path).header("content-type", "application/json");
    for (k, v) in headers {
        req = req.header(*k, v);
    }
    let resp = w
        .app
        .clone()
        .oneshot(req.body(Body::from(body.to_vec())).unwrap())
        .await
        .unwrap();
    let status = resp.status();
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    (
        status,
        serde_json::from_slice(&bytes).unwrap_or(Value::Null),
    )
}

async fn stored(w: &World, t: Uuid, binding: &str) -> Vec<(String, String, String)> {
    let mut tx = w.channels.db.tenant_tx(t).await.unwrap();
    sqlx::query_as(
        "SELECT provider_message_id, thread, text FROM inbound_messages
         WHERE binding_id = $1::uuid ORDER BY received_at",
    )
    .bind(binding)
    .fetch_all(&mut *tx)
    .await
    .unwrap()
}

async fn events(w: &World, inbound_subjects_of: &str) -> i64 {
    sqlx::query_scalar(
        "SELECT count(*) FROM outbox WHERE type = 'channel.message_received'
         AND data->>'binding_id' = $1",
    )
    .bind(inbound_subjects_of)
    .fetch_one(&w.channels.db.pool)
    .await
    .unwrap()
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn a_signed_webhook_is_stored_once_with_its_event() {
    let w = world().await;
    let b = bind(&w, T1, pb::ChannelKind::Webhook, SECRET).await;
    assert_eq!(b.webhook_path, format!("/hooks/webhook/{}", b.binding_id));
    let body = br#"{"id":"lead-1","from":"crm","text":"New lead: Acme wants a demo"}"#;
    let sig = sign_wfk(SECRET, body);
    for _ in 0..2 {
        let (status, _) = post(
            &w,
            &b.webhook_path,
            &[("x-wfk-signature", sig.clone())],
            body,
        )
        .await;
        assert_eq!(status, StatusCode::OK);
    }
    assert_eq!(
        stored(&w, T1, &b.binding_id).await,
        vec![(
            "lead-1".into(),
            "lead-1".into(),
            "New lead: Acme wants a demo".into()
        )],
        "delivered twice, stored once"
    );
    assert_eq!(events(&w, &b.binding_id).await, 1);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn bad_or_missing_signatures_store_nothing() {
    let w = world().await;
    let b = bind(&w, T1, pb::ChannelKind::Webhook, SECRET).await;
    let body = br#"{"id":"x","text":"hi"}"#;
    let forged = sign_wfk("not-the-secret", body);
    let (status, _) = post(&w, &b.webhook_path, &[("x-wfk-signature", forged)], body).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    let (status, _) = post(&w, &b.webhook_path, &[], body).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED);
    assert!(stored(&w, T1, &b.binding_id).await.is_empty());
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn unknown_disabled_or_mismatched_hooks_are_not_found() {
    let w = world().await;
    let b = bind(&w, T1, pb::ChannelKind::Webhook, SECRET).await;
    let body = br#"{"id":"x","text":"hi"}"#;
    let sig = sign_wfk(SECRET, body);
    let h = [("x-wfk-signature", sig)];
    let wrong_kind = format!("/hooks/email/{}", b.binding_id);
    for path in [
        format!("/hooks/webhook/{}", Uuid::new_v4()),
        "/hooks/webhook/not-a-uuid".to_string(),
        format!("/hooks/fax/{}", b.binding_id),
        wrong_kind,
    ] {
        assert_eq!(
            post(&w, &path, &h, body).await.0,
            StatusCode::NOT_FOUND,
            "{path}"
        );
    }
    ChannelApi(w.channels.clone())
        .disable_binding(RpcRequest::new(pb::DisableBindingRequest {
            tenant: tenant(T1),
            binding_id: b.binding_id.clone(),
        }))
        .await
        .unwrap();
    assert_eq!(
        post(&w, &b.webhook_path, &h, body).await.0,
        StatusCode::NOT_FOUND
    );
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn slack_challenge_message_and_replay() {
    let w = world().await;
    let b = bind(&w, T1, pb::ChannelKind::Slack, SECRET).await;
    let now = chrono::Utc::now().timestamp();
    let signed = |ts: i64, body: &[u8]| {
        vec![
            ("x-slack-request-timestamp", ts.to_string()),
            ("x-slack-signature", sign_slack(SECRET, ts, body)),
        ]
    };
    let challenge = br#"{"type":"url_verification","challenge":"c-123"}"#;
    let (status, out) = post(&w, &b.webhook_path, &signed(now, challenge), challenge).await;
    assert_eq!(
        (status, out["challenge"].as_str()),
        (StatusCode::OK, Some("c-123"))
    );

    let msg = br#"{"type":"event_callback","event_id":"Ev9","event":{"type":"app_mention",
        "user":"U1","text":"summarise the board deck","channel":"C1","ts":"100.1"}}"#;
    assert_eq!(
        post(&w, &b.webhook_path, &signed(now, msg), msg).await.0,
        StatusCode::OK
    );
    let old = signed(now - 600, msg);
    assert_eq!(
        post(&w, &b.webhook_path, &old, msg).await.0,
        StatusCode::UNAUTHORIZED
    );
    let rows = stored(&w, T1, &b.binding_id).await;
    assert_eq!(rows.len(), 1);
    assert_eq!(rows[0].1, "C1:100.1");
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn teams_gets_an_immediate_answer() {
    let w = world().await;
    let secret = base64::engine::general_purpose::STANDARD.encode(b"teams-token-bytes");
    let b = bind(&w, T1, pb::ChannelKind::Teams, &secret).await;
    let body = br#"{"type":"message","id":"m1","text":"<at>Sam</at> draft the reply",
        "from":{"name":"Ana"},"conversation":{"id":"19:abc"}}"#;
    let auth = sign_teams(&secret, body).unwrap();
    let (status, out) = post(&w, &b.webhook_path, &[("authorization", auth)], body).await;
    assert_eq!(
        (status, out["type"].as_str()),
        (StatusCode::OK, Some("message"))
    );
    assert_eq!(stored(&w, T1, &b.binding_id).await[0].2, "draft the reply");
}

fn google_token(aud: &str, iss: &str, exp_in: i64) -> String {
    use jsonwebtoken::{Algorithm, EncodingKey, Header, encode};
    let mut header = Header::new(Algorithm::RS256);
    header.kid = Some("test-1".into());
    let now = chrono::Utc::now().timestamp();
    let claims = json!({"aud": aud, "iss": iss, "iat": now, "exp": now + exp_in});
    encode(
        &header,
        &claims,
        &EncodingKey::from_rsa_pem(GOOGLE_KEY.as_bytes()).unwrap(),
    )
    .unwrap()
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn google_chat_tokens_are_checked_against_googles_keys() {
    let w = world().await;
    let project = "123456789012";
    let b = bind(&w, T1, pb::ChannelKind::GoogleChat, project).await;
    let body = br#"{"type":"MESSAGE","message":{"name":"spaces/S/messages/M1","text":"hi Sam",
        "sender":{"email":"ana@x.com","type":"HUMAN"},"thread":{"name":"spaces/S/threads/T"}}}"#;
    let bearer = |t: String| vec![("authorization", format!("Bearer {t}"))];
    let iss = "chat@system.gserviceaccount.com";
    for (token, want) in [
        (google_token(project, iss, 300), StatusCode::OK),
        (google_token("999", iss, 300), StatusCode::UNAUTHORIZED),
        (
            google_token(project, "evil@example.com", 300),
            StatusCode::UNAUTHORIZED,
        ),
        (google_token(project, iss, -300), StatusCode::UNAUTHORIZED),
    ] {
        assert_eq!(
            post(&w, &b.webhook_path, &bearer(token), body).await.0,
            want
        );
    }
    assert_eq!(stored(&w, T1, &b.binding_id).await.len(), 1);
}

#[tokio::test]
#[ignore = "needs the local stack: just up"]
async fn bindings_are_idempotent_and_per_tenant() {
    let w = world().await;
    let api = ChannelApi(w.channels.clone());
    let req = |t: Uuid, emp: &str| pb::CreateBindingRequest {
        tenant: tenant(t),
        idempotency_key: Some(IdempotencyKey {
            value: "bind-1".into(),
        }),
        kind: pb::ChannelKind::Email as i32,
        employee_id: emp.into(),
        name: "Support inbox".into(),
        secret: SECRET.into(),
        outbound: Some(pb::Outbound {
            url: "https://relay.example/send".into(),
            token: "do-not-return".into(),
            from: "sam@acme.test".into(),
        }),
    };
    let a = api
        .create_binding(RpcRequest::new(req(T1, "sam")))
        .await
        .unwrap()
        .into_inner();
    let again = api
        .create_binding(RpcRequest::new(req(T1, "sam")))
        .await
        .unwrap()
        .into_inner();
    assert_eq!(a.binding, again.binding);
    let bound = a.binding.unwrap();
    assert_eq!(
        bound.outbound.unwrap().token,
        "",
        "secrets are never returned"
    );
    let conflict = api
        .create_binding(RpcRequest::new(req(T1, "bob")))
        .await
        .unwrap_err();
    assert_eq!(conflict.code(), tonic::Code::AlreadyExists);

    let theirs = api
        .list_bindings(RpcRequest::new(pb::ListBindingsRequest {
            tenant: tenant(T2),
            employee_id: String::new(),
        }))
        .await
        .unwrap()
        .into_inner();
    assert!(
        theirs
            .bindings
            .iter()
            .all(|b| b.binding_id != bound.binding_id)
    );
}
