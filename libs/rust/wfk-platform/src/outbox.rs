//! CloudEvents envelope and the transactional outbox.
//!
//! ```ignore
//! let mut tx = db.tenant_tx(tenant).await?;
//! sqlx::query("UPDATE tasks SET status = 'done' WHERE id = $1").bind(id).execute(&mut *tx).await?;
//! enqueue(&mut tx, &CloudEvent::new("task.completed", SOURCE, tenant, json!({"task_id": id}))?).await?;
//! tx.commit().await?;
//! ```
//!
//! If the transaction rolls back, the event was never written, so it is never published.

use chrono::{DateTime, SubsecRound, Utc};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sqlx::PgConnection;
use uuid::Uuid;

/// CloudEvents 1.0 envelope (`contracts/events/cloudevent-envelope.schema.json`).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CloudEvent {
    pub specversion: String,
    pub id: Uuid,
    pub source: String,
    #[serde(rename = "type")]
    pub event_type: String,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub subject: Option<String>,
    pub time: DateTime<Utc>,
    pub datacontenttype: String,
    pub tenantid: Uuid,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub traceparent: Option<String>,
    pub data: Value,
}

#[derive(Debug, thiserror::Error)]
pub enum EventError {
    #[error("event type must look like a NATS subject (run.completed): {0:?}")]
    BadType(String),
    #[error("event data must be a JSON object")]
    DataNotObject,
    #[error("malformed event: {0}")]
    Malformed(#[from] serde_json::Error),
}

/// `segment(.segment)+` where each segment is `[a-z][a-z0-9_]*`.
pub fn is_valid_type(t: &str) -> bool {
    let parts: Vec<&str> = t.split('.').collect();
    parts.len() >= 2
        && parts.iter().all(|p| {
            let mut chars = p.chars();
            matches!(chars.next(), Some('a'..='z'))
                && chars.all(|c| matches!(c, 'a'..='z' | '0'..='9' | '_'))
        })
}

impl CloudEvent {
    pub fn new(
        event_type: &str,
        source: &str,
        tenant_id: Uuid,
        data: Value,
    ) -> Result<Self, EventError> {
        let e = Self {
            specversion: "1.0".into(),
            id: Uuid::now_v7(),
            source: source.into(),
            event_type: event_type.into(),
            subject: None,
            // Microseconds, as PostgreSQL and Python store them, so the event read back
            // from the outbox is identical to the one written.
            time: Utc::now().trunc_subsecs(6),
            datacontenttype: "application/json".into(),
            tenantid: tenant_id,
            traceparent: None,
            data,
        };
        e.validate()
    }

    pub fn with_subject(mut self, subject: impl Into<String>) -> Self {
        self.subject = Some(subject.into());
        self
    }

    fn validate(self) -> Result<Self, EventError> {
        if !is_valid_type(&self.event_type) {
            return Err(EventError::BadType(self.event_type));
        }
        if !self.data.is_object() {
            return Err(EventError::DataNotObject);
        }
        Ok(self)
    }

    pub fn to_json(&self) -> Vec<u8> {
        serde_json::to_vec(self).expect("a CloudEvent always serialises")
    }

    pub fn from_json(raw: &[u8]) -> Result<Self, EventError> {
        let e: Self = serde_json::from_slice(raw)?;
        e.validate()
    }
}

/// Write the event to the outbox inside the caller's open transaction.
pub async fn enqueue(conn: &mut PgConnection, event: &CloudEvent) -> sqlx::Result<()> {
    sqlx::query(
        "INSERT INTO outbox (id, tenant_id, type, source, subject, time, traceparent, data)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
    )
    .bind(event.id)
    .bind(event.tenantid)
    .bind(&event.event_type)
    .bind(&event.source)
    .bind(&event.subject)
    .bind(event.time)
    .bind(&event.traceparent)
    .bind(&event.data)
    .execute(conn)
    .await
    .map(|_| ())
}
