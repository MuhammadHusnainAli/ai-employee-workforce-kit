//! Transport: the provider webhooks (`POST /hooks/{kind}/{binding_id}`) and ChannelService
//! over gRPC (contracts/proto/wfk/channel_gateway/v1/channels.proto).

use axum::Router;
use axum::body::Bytes;
use axum::extract::{Path, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Json, Response};
use axum::routing::post;
use serde_json::json;
use tonic::{Request, Response as RpcResponse, Status};
use uuid::Uuid;

use crate::channel::adapters::{BindingRow, ChannelError, Channels, GoogleTokens, NewBinding};
use crate::channel::domain::{self, Kind, Outbound, Parsed};
use crate::contracts::wfk::channel_gateway::v1 as pb;
use crate::contracts::wfk::channel_gateway::v1::channel_service_server::ChannelService;
use crate::contracts::wfk::common::v1::TenantContext;

// ---- webhooks ------------------------------------------------------------------------------

#[derive(Clone)]
pub struct HookState {
    pub channels: Channels,
    pub google: GoogleTokens,
}

pub fn hooks(state: HookState) -> Router {
    Router::new()
        .route("/hooks/{kind}/{binding_id}", post(receive))
        .with_state(state)
}

fn header<'a>(h: &'a HeaderMap, name: &str) -> Option<&'a str> {
    h.get(name).and_then(|v| v.to_str().ok())
}

fn status(code: StatusCode, msg: &str) -> Response {
    (code, Json(json!({"error": msg}))).into_response()
}

/// Verify the request the provider's way, then store the message once.
///
/// 404 for an unknown, disabled or mismatched binding (nothing about it leaks), 401 for a bad
/// signature, 400 for a body that is not the provider's format, 503 when the store is down
/// (providers retry; the message id makes the retry safe).
async fn receive(
    State(s): State<HookState>,
    Path((kind, binding_id)): Path<(String, String)>,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let (Some(kind), Ok(id)) = (Kind::parse(&kind), Uuid::parse_str(&binding_id)) else {
        return status(StatusCode::NOT_FOUND, "no such hook");
    };
    let binding = match s.channels.binding_for_hook(kind, id).await {
        Ok(Some(b)) => b,
        Ok(None) | Err(ChannelError::NotFound(_)) => {
            return status(StatusCode::NOT_FOUND, "no such hook");
        }
        Err(e) => {
            tracing::error!(error = %e, "binding lookup failed");
            return status(StatusCode::SERVICE_UNAVAILABLE, "try again");
        }
    };
    let verified = match kind {
        Kind::Slack => domain::verify_slack(
            &binding.secret,
            header(&headers, "x-slack-request-timestamp"),
            header(&headers, "x-slack-signature"),
            &body,
            chrono::Utc::now().timestamp(),
        )
        .map_err(|e| e.to_string()),
        Kind::Teams => {
            domain::verify_teams(&binding.secret, header(&headers, "authorization"), &body)
                .map_err(|e| e.to_string())
        }
        Kind::GoogleChat => s
            .google
            .verify(header(&headers, "authorization"), &binding.secret)
            .await
            .map_err(|e| e.to_string()),
        Kind::Email | Kind::Webhook => {
            domain::verify_wfk(&binding.secret, header(&headers, "x-wfk-signature"), &body)
                .map_err(|e| e.to_string())
        }
    };
    if let Err(why) = verified {
        tracing::warn!(binding = %id, kind = kind.as_str(), reason = %why, "webhook rejected");
        return status(StatusCode::UNAUTHORIZED, "signature check failed");
    }
    match domain::parse(kind, &body) {
        Err(e) => status(StatusCode::BAD_REQUEST, &e.to_string()),
        Ok(Parsed::Challenge(c)) => Json(json!({"challenge": c})).into_response(),
        Ok(Parsed::Ignore) => Json(json!({})).into_response(),
        Ok(Parsed::Message(m)) => match s.channels.record_inbound(&binding, &m).await {
            Ok(rec) => {
                tracing::info!(binding = %id, inbound = %rec.inbound_id, new = rec.new, "message");
                ack(kind)
            }
            Err(e) => {
                tracing::error!(error = %e, "storing the message failed");
                status(StatusCode::SERVICE_UNAVAILABLE, "try again")
            }
        },
    }
}

/// What the provider shows right away. Teams needs a message back within 5 seconds; the
/// others just need a 200.
fn ack(kind: Kind) -> Response {
    match kind {
        Kind::Teams => Json(json!({"type": "message", "text": "On it."})).into_response(),
        _ => Json(json!({})).into_response(),
    }
}

// ---- gRPC ------------------------------------------------------------------------------------

fn kind_pb(k: Kind) -> pb::ChannelKind {
    match k {
        Kind::Slack => pb::ChannelKind::Slack,
        Kind::Teams => pb::ChannelKind::Teams,
        Kind::GoogleChat => pb::ChannelKind::GoogleChat,
        Kind::Email => pb::ChannelKind::Email,
        Kind::Webhook => pb::ChannelKind::Webhook,
    }
}

fn kind_from_pb(v: i32) -> Option<Kind> {
    Some(match pb::ChannelKind::try_from(v).ok()? {
        pb::ChannelKind::Unspecified => return None,
        pb::ChannelKind::Slack => Kind::Slack,
        pb::ChannelKind::Teams => Kind::Teams,
        pb::ChannelKind::GoogleChat => Kind::GoogleChat,
        pb::ChannelKind::Email => Kind::Email,
        pb::ChannelKind::Webhook => Kind::Webhook,
    })
}

fn binding_pb(b: BindingRow) -> pb::Binding {
    pb::Binding {
        binding_id: b.id.to_string(),
        kind: kind_pb(b.kind) as i32,
        employee_id: b.employee_id.clone(),
        name: b.name.clone(),
        outbound: Some(pb::Outbound {
            url: b.outbound.url.clone(),
            token: String::new(), // never returned
            from: b.outbound.from.clone(),
        }),
        active: b.active,
        webhook_path: b.webhook_path(),
        created_at: Some(prost_types::Timestamp {
            seconds: b.created_at.timestamp(),
            nanos: i32::try_from(b.created_at.timestamp_subsec_nanos()).unwrap_or(0),
        }),
    }
}

fn bad(msg: &str) -> Status {
    Status::invalid_argument(msg)
}

fn tenant(t: Option<&TenantContext>) -> Result<(Uuid, String), Status> {
    let t = t.ok_or_else(|| bad("tenant is required"))?;
    let id = Uuid::parse_str(&t.tenant_id).map_err(|_| bad("tenant.tenant_id must be a UUID"))?;
    let actor = t
        .actor
        .as_ref()
        .map(|a| a.id.clone())
        .filter(|a| !a.is_empty())
        .unwrap_or_else(|| "unknown".into());
    Ok((id, actor))
}

impl From<ChannelError> for Status {
    fn from(e: ChannelError) -> Self {
        match e {
            ChannelError::NotFound(m) => Status::not_found(m),
            ChannelError::Conflict(m) => Status::already_exists(m),
            ChannelError::Db(e) => {
                tracing::error!(error = %e, "database error");
                Status::unavailable("database unavailable")
            }
            ChannelError::Other(e) => {
                tracing::error!(error = %e, "internal error");
                Status::internal("internal error")
            }
        }
    }
}

pub struct ChannelApi(pub Channels);

#[tonic::async_trait]
impl ChannelService for ChannelApi {
    async fn create_binding(
        &self,
        req: Request<pb::CreateBindingRequest>,
    ) -> Result<RpcResponse<pb::CreateBindingResponse>, Status> {
        let r = req.into_inner();
        let (tenant, actor) = tenant(r.tenant.as_ref())?;
        let key = r
            .idempotency_key
            .map(|k| k.value.trim().to_string())
            .filter(|k| !k.is_empty() && k.len() <= 200)
            .ok_or_else(|| bad("idempotency_key is required (at most 200 characters)"))?;
        let kind = kind_from_pb(r.kind).ok_or_else(|| bad("kind is required"))?;
        if r.employee_id.trim().is_empty() {
            return Err(bad("employee_id is required"));
        }
        if r.secret.len() < 8 {
            return Err(bad("secret is required (at least 8 characters)"));
        }
        let out = r.outbound.unwrap_or_default();
        if !out.url.is_empty()
            && !(out.url.starts_with("https://") || out.url.starts_with("http://"))
        {
            return Err(bad("outbound.url must be an http(s) URL"));
        }
        let name = if r.name.trim().is_empty() {
            format!("{} for {}", kind.as_str(), r.employee_id)
        } else {
            r.name
        };
        let b = self
            .0
            .create_binding(
                tenant,
                NewBinding {
                    idempotency_key: key,
                    kind,
                    employee_id: r.employee_id,
                    name,
                    secret: r.secret,
                    outbound: Outbound {
                        url: out.url,
                        token: out.token,
                        from: out.from,
                    },
                    created_by: actor,
                },
            )
            .await?;
        Ok(RpcResponse::new(pb::CreateBindingResponse {
            binding: Some(binding_pb(b)),
        }))
    }

    async fn list_bindings(
        &self,
        req: Request<pb::ListBindingsRequest>,
    ) -> Result<RpcResponse<pb::ListBindingsResponse>, Status> {
        let r = req.into_inner();
        let (tenant, _) = tenant(r.tenant.as_ref())?;
        let emp = Some(r.employee_id.as_str()).filter(|e| !e.is_empty());
        let rows = self.0.list_bindings(tenant, emp).await?;
        Ok(RpcResponse::new(pb::ListBindingsResponse {
            bindings: rows.into_iter().map(binding_pb).collect(),
        }))
    }

    async fn disable_binding(
        &self,
        req: Request<pb::DisableBindingRequest>,
    ) -> Result<RpcResponse<pb::DisableBindingResponse>, Status> {
        let r = req.into_inner();
        let (tenant, _) = tenant(r.tenant.as_ref())?;
        let id = Uuid::parse_str(&r.binding_id).map_err(|_| bad("binding_id must be a UUID"))?;
        let b = self.0.disable_binding(tenant, id).await?;
        Ok(RpcResponse::new(pb::DisableBindingResponse {
            binding: Some(binding_pb(b)),
        }))
    }

    async fn deliver_message(
        &self,
        req: Request<pb::DeliverMessageRequest>,
    ) -> Result<RpcResponse<pb::DeliverMessageResponse>, Status> {
        let r = req.into_inner();
        let (tenant, _) = tenant(r.tenant.as_ref())?;
        let key = r
            .idempotency_key
            .map(|k| k.value.trim().to_string())
            .filter(|k| !k.is_empty() && k.len() <= 200)
            .ok_or_else(|| bad("idempotency_key is required (at most 200 characters)"))?;
        let binding =
            Uuid::parse_str(&r.binding_id).map_err(|_| bad("binding_id must be a UUID"))?;
        if r.thread.trim().is_empty() {
            return Err(bad("thread is required"));
        }
        if r.text.trim().is_empty() || r.text.len() > 40_000 {
            return Err(bad("text is required (at most 40,000 characters)"));
        }
        let id = self
            .0
            .deliver(tenant, binding, &key, &r.thread, &r.text)
            .await?;
        Ok(RpcResponse::new(pb::DeliverMessageResponse {
            delivery_id: id.to_string(),
        }))
    }
}
