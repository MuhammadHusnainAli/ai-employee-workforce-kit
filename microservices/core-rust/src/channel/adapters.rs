//! channel_db (bindings, inbound messages) and Google's token check for Chat webhooks.

use std::sync::Arc;
use std::time::{Duration, Instant};

use chrono::{DateTime, Utc};
use serde_json::json;
use sqlx::{PgConnection, Row};
use tokio::sync::RwLock;
use uuid::Uuid;

use crate::channel::domain::{Inbound, Kind, Outbound};
use crate::platform::{CloudEvent, Database, enqueue};

pub const SOURCE_SERVICE: &str = "channel-gateway";

#[derive(Debug, thiserror::Error)]
pub enum ChannelError {
    #[error("{0}")]
    NotFound(String),
    #[error("{0}")]
    Conflict(String),
    #[error(transparent)]
    Db(#[from] sqlx::Error),
    #[error(transparent)]
    Other(#[from] anyhow::Error),
}

#[derive(Debug, Clone, PartialEq)]
pub struct BindingRow {
    pub id: Uuid,
    pub tenant_id: Uuid,
    pub kind: Kind,
    pub employee_id: String,
    pub name: String,
    pub secret: String,
    pub outbound: Outbound,
    pub active: bool,
    pub created_at: DateTime<Utc>,
}

impl BindingRow {
    pub fn from_row(r: &sqlx::postgres::PgRow) -> Result<Self, ChannelError> {
        let kind: String = r.try_get("kind")?;
        let outbound: serde_json::Value = r.try_get("outbound")?;
        Ok(Self {
            id: r.try_get("id")?,
            tenant_id: r.try_get("tenant_id")?,
            kind: Kind::parse(&kind).ok_or_else(|| anyhow::anyhow!("unknown kind {kind}"))?,
            employee_id: r.try_get("employee_id")?,
            name: r.try_get("name")?,
            secret: r.try_get("secret")?,
            outbound: serde_json::from_value(outbound).unwrap_or_default(),
            active: r.try_get("active")?,
            created_at: r.try_get("created_at")?,
        })
    }

    pub fn webhook_path(&self) -> String {
        format!("/hooks/{}/{}", self.kind.as_str(), self.id)
    }
}

pub struct NewBinding {
    pub idempotency_key: String,
    pub kind: Kind,
    pub employee_id: String,
    pub name: String,
    pub secret: String,
    pub outbound: Outbound,
    pub created_by: String,
}

/// What recording an inbound message did.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Recorded {
    pub inbound_id: Uuid,
    /// False when the provider delivered the same message again: nothing new was stored.
    pub new: bool,
}

#[derive(Clone)]
pub struct Channels {
    pub db: Database,
    pub source: String,
}

impl Channels {
    pub fn new(db: Database, cell_id: &str) -> Self {
        Self {
            db,
            source: format!("//{cell_id}/{SOURCE_SERVICE}"),
        }
    }

    pub async fn emit(
        &self,
        conn: &mut PgConnection,
        tenant: Uuid,
        event_type: &str,
        subject: String,
        data: serde_json::Value,
    ) -> Result<(), ChannelError> {
        let event = CloudEvent::new(event_type, &self.source, tenant, data)
            .map_err(|e| anyhow::anyhow!(e))?
            .with_subject(subject);
        enqueue(conn, &event).await?;
        Ok(())
    }

    /// Idempotent on the key: the same key returns the same binding.
    pub async fn create_binding(
        &self,
        tenant: Uuid,
        b: NewBinding,
    ) -> Result<BindingRow, ChannelError> {
        let id = Uuid::new_v5(&tenant, format!("binding/{}", b.idempotency_key).as_bytes());
        let mut tx = self.db.tenant_tx(tenant).await?;
        let inserted = sqlx::query(
            "INSERT INTO bindings (id, tenant_id, kind, employee_id, name, secret, outbound,
                created_by)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8) ON CONFLICT (id) DO NOTHING",
        )
        .bind(id)
        .bind(tenant)
        .bind(b.kind.as_str())
        .bind(&b.employee_id)
        .bind(&b.name)
        .bind(&b.secret)
        .bind(serde_json::to_value(&b.outbound).map_err(|e| anyhow::anyhow!(e))?)
        .bind(&b.created_by)
        .execute(&mut *tx)
        .await?
        .rows_affected()
            == 1;
        let row = fetch(&mut tx, id).await?;
        if inserted {
            sqlx::query("INSERT INTO hook_routes (binding_id, tenant_id) VALUES ($1, $2)")
                .bind(id)
                .bind(tenant)
                .execute(&mut *tx)
                .await?;
            let data = json!({"binding_id": id, "kind": b.kind.as_str(),
                              "employee_id": b.employee_id});
            self.emit(
                &mut tx,
                tenant,
                "channel.binding_created",
                id.to_string(),
                data,
            )
            .await?;
        } else if row.kind != b.kind || row.employee_id != b.employee_id {
            return Err(ChannelError::Conflict(
                "idempotency key already used for a different binding".into(),
            ));
        }
        tx.commit().await?;
        Ok(row)
    }

    pub async fn list_bindings(
        &self,
        tenant: Uuid,
        employee_id: Option<&str>,
    ) -> Result<Vec<BindingRow>, ChannelError> {
        let mut tx = self.db.tenant_tx(tenant).await?;
        let rows = sqlx::query(
            "SELECT * FROM bindings WHERE ($1::text IS NULL OR employee_id = $1)
             ORDER BY created_at DESC LIMIT 500",
        )
        .bind(employee_id)
        .fetch_all(&mut *tx)
        .await?;
        rows.iter().map(BindingRow::from_row).collect()
    }

    pub async fn disable_binding(
        &self,
        tenant: Uuid,
        id: Uuid,
    ) -> Result<BindingRow, ChannelError> {
        let mut tx = self.db.tenant_tx(tenant).await?;
        let updated = sqlx::query("UPDATE bindings SET active = false WHERE id = $1")
            .bind(id)
            .execute(&mut *tx)
            .await?
            .rows_affected();
        if updated == 0 {
            return Err(ChannelError::NotFound(format!("no binding {id}")));
        }
        let row = fetch(&mut tx, id).await?;
        tx.commit().await?;
        Ok(row)
    }

    /// Queue a message for a thread. Idempotent on `key`: the delivery id comes from it.
    pub async fn deliver(
        &self,
        tenant: Uuid,
        binding: Uuid,
        key: &str,
        thread: &str,
        text: &str,
    ) -> Result<Uuid, ChannelError> {
        let id = Uuid::new_v5(&binding, format!("deliver/{key}").as_bytes());
        let mut tx = self.db.tenant_tx(tenant).await?;
        let b = fetch(&mut tx, binding).await?;
        if !b.active {
            return Err(ChannelError::NotFound(format!(
                "binding {binding} is disabled"
            )));
        }
        let new = sqlx::query(
            "INSERT INTO deliveries (id, tenant_id, binding_id, thread, purpose, text)
             VALUES ($1, $2, $3, $4, 'message', $5) ON CONFLICT (id) DO NOTHING",
        )
        .bind(id)
        .bind(tenant)
        .bind(binding)
        .bind(thread)
        .bind(text)
        .execute(&mut *tx)
        .await?
        .rows_affected()
            == 1;
        if new {
            sqlx::query("INSERT INTO delivery_queue (delivery_id, tenant_id) VALUES ($1, $2)")
                .bind(id)
                .bind(tenant)
                .execute(&mut *tx)
                .await?;
        }
        tx.commit().await?;
        Ok(id)
    }

    /// The binding a webhook URL names, if it exists, is active and is of that kind.
    pub async fn binding_for_hook(
        &self,
        kind: Kind,
        id: Uuid,
    ) -> Result<Option<BindingRow>, ChannelError> {
        let tenant: Option<Uuid> =
            sqlx::query_scalar("SELECT tenant_id FROM hook_routes WHERE binding_id = $1")
                .bind(id)
                .fetch_optional(&self.db.pool)
                .await?;
        let Some(tenant) = tenant else {
            return Ok(None);
        };
        let mut tx = self.db.tenant_tx(tenant).await?;
        let row = fetch(&mut tx, id).await?;
        Ok(Some(row).filter(|b| b.active && b.kind == kind))
    }

    /// Store a message once (per binding and provider message id), with its event, and queue it
    /// to become a task.
    pub async fn record_inbound(
        &self,
        b: &BindingRow,
        m: &Inbound,
    ) -> Result<Recorded, ChannelError> {
        let id = Uuid::new_v5(&b.id, m.provider_message_id.as_bytes());
        let mut tx = self.db.tenant_tx(b.tenant_id).await?;
        let new = sqlx::query(
            "INSERT INTO inbound_messages (id, tenant_id, binding_id, provider_message_id, thread,
                sender, text)
             VALUES ($1, $2, $3, $4, $5, $6, $7)
             ON CONFLICT (binding_id, provider_message_id) DO NOTHING",
        )
        .bind(id)
        .bind(b.tenant_id)
        .bind(b.id)
        .bind(&m.provider_message_id)
        .bind(&m.thread)
        .bind(&m.sender)
        .bind(&m.text)
        .execute(&mut *tx)
        .await?
        .rows_affected()
            == 1;
        if new {
            sqlx::query("INSERT INTO forward_queue (inbound_id, tenant_id) VALUES ($1, $2)")
                .bind(id)
                .bind(b.tenant_id)
                .execute(&mut *tx)
                .await?;
            let data = json!({"inbound_id": id, "binding_id": b.id, "kind": b.kind.as_str(),
                              "employee_id": b.employee_id, "thread": m.thread,
                              "sender": m.sender});
            self.emit(
                &mut tx,
                b.tenant_id,
                "channel.message_received",
                id.to_string(),
                data,
            )
            .await?;
        }
        tx.commit().await?;
        Ok(Recorded {
            inbound_id: id,
            new,
        })
    }
}

async fn fetch(conn: &mut PgConnection, id: Uuid) -> Result<BindingRow, ChannelError> {
    let row = sqlx::query("SELECT * FROM bindings WHERE id = $1")
        .bind(id)
        .fetch_optional(&mut *conn)
        .await?;
    match row {
        Some(r) => BindingRow::from_row(&r),
        None => Err(ChannelError::NotFound(format!("no binding {id}"))),
    }
}

// ---- Google Chat: verify the bearer token Google signs each request with -----------------

pub const GOOGLE_CHAT_ISSUER: &str = "chat@system.gserviceaccount.com";
pub const GOOGLE_CHAT_JWKS: &str =
    "https://www.googleapis.com/service_accounts/v1/jwk/chat@system.gserviceaccount.com";
const JWKS_TTL: Duration = Duration::from_secs(3600);

/// Checks Google Chat's `Authorization: Bearer <JWT>`: RS256, issued by Chat's service account,
/// for this app's project number (the binding's secret), not expired. Google's public keys are
/// fetched from `jwks_url` and cached for an hour; an unknown key id refreshes them once.
#[derive(Clone)]
pub struct GoogleTokens {
    jwks_url: String,
    http: reqwest::Client,
    cache: Arc<RwLock<Option<(Instant, jsonwebtoken::jwk::JwkSet)>>>,
}

impl GoogleTokens {
    pub fn new(jwks_url: impl Into<String>) -> Self {
        Self {
            jwks_url: jwks_url.into(),
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(5))
                .build()
                .unwrap_or_default(),
            cache: Arc::new(RwLock::new(None)),
        }
    }

    async fn keys(&self, refresh: bool) -> anyhow::Result<jsonwebtoken::jwk::JwkSet> {
        if !refresh
            && let Some((at, set)) = self.cache.read().await.as_ref()
            && at.elapsed() < JWKS_TTL
        {
            return Ok(set.clone());
        }
        let set: jsonwebtoken::jwk::JwkSet = self
            .http
            .get(&self.jwks_url)
            .send()
            .await?
            .error_for_status()?
            .json()
            .await?;
        *self.cache.write().await = Some((Instant::now(), set.clone()));
        Ok(set)
    }

    pub async fn verify(&self, authorization: Option<&str>, audience: &str) -> anyhow::Result<()> {
        use jsonwebtoken::{Algorithm, DecodingKey, Validation, decode, decode_header};
        let token = authorization
            .and_then(|a| a.strip_prefix("Bearer "))
            .ok_or_else(|| anyhow::anyhow!("missing bearer token"))?;
        let kid = decode_header(token)?
            .kid
            .ok_or_else(|| anyhow::anyhow!("token has no key id"))?;
        let mut set = self.keys(false).await?;
        if set.find(&kid).is_none() {
            set = self.keys(true).await?; // Google rotated its keys
        }
        let jwk = set
            .find(&kid)
            .ok_or_else(|| anyhow::anyhow!("unknown key id"))?;
        let mut validation = Validation::new(Algorithm::RS256);
        validation.set_audience(&[audience]);
        validation.set_issuer(&[GOOGLE_CHAT_ISSUER]);
        decode::<serde_json::Value>(token, &DecodingKey::from_jwk(jwk)?, &validation)?;
        Ok(())
    }
}
