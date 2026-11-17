//! NATS JetStream: the outbox relay (publisher side) and the inbox consumer (subscriber side).
//!
//! Delivery is at least once end to end. Duplicates are removed twice:
//! - JetStream drops a publish whose `Nats-Msg-Id` it has seen inside the stream's duplicate
//!   window, so a relay that crashes after publishing but before marking the row does not
//!   produce a second message.
//! - The inbox table makes each handler run once per (consumer, event id).

use std::time::Duration;

use async_nats::jetstream::consumer::{PullConsumer, pull};
use async_nats::jetstream::message::{AckKind, Message, PublishMessage};
use async_nats::jetstream::{self, stream};
use futures::StreamExt;
use sqlx::{PgConnection, Row};

use super::db::Database;
use super::outbox::CloudEvent;

pub const DUPLICATE_WINDOW: Duration = Duration::from_secs(600);

/// Create the stream if it does not exist. Idempotent.
pub async fn ensure_stream(
    js: &jetstream::Context,
    name: &str,
    subjects: Vec<String>,
) -> anyhow::Result<stream::Stream> {
    Ok(js
        .get_or_create_stream(stream::Config {
            name: name.to_string(),
            subjects,
            duplicate_window: DUPLICATE_WINDOW,
            ..Default::default()
        })
        .await?)
}

/// Publishes committed outbox rows to JetStream, oldest first.
///
/// Several replicas can run at once: rows are claimed with `FOR UPDATE SKIP LOCKED`.
pub struct OutboxRelay {
    db: Database,
    js: jetstream::Context,
    batch_size: i64,
}

impl OutboxRelay {
    pub fn new(db: Database, js: jetstream::Context, batch_size: i64) -> Self {
        Self { db, js, batch_size }
    }

    /// Publish one batch. Returns how many rows were published.
    pub async fn publish_pending(&self) -> anyhow::Result<usize> {
        let mut tx = self.db.tx().await?;
        let rows = sqlx::query(
            "SELECT seq, id, tenant_id, type, source, subject, time, traceparent, data
             FROM outbox WHERE published_at IS NULL
             ORDER BY seq LIMIT $1 FOR UPDATE SKIP LOCKED",
        )
        .bind(self.batch_size)
        .fetch_all(&mut *tx)
        .await?;

        let mut published: Vec<i64> = Vec::with_capacity(rows.len());
        let mut failure = None;
        for row in &rows {
            let event = CloudEvent {
                specversion: "1.0".into(),
                id: row.try_get("id")?,
                source: row.try_get("source")?,
                event_type: row.try_get("type")?,
                subject: row.try_get("subject")?,
                time: row.try_get("time")?,
                datacontenttype: "application/json".into(),
                tenantid: row.try_get("tenant_id")?,
                traceparent: row.try_get("traceparent")?,
                data: row.try_get("data")?,
            };
            let publish = PublishMessage::build()
                .payload(event.to_json().into())
                .message_id(event.id.to_string());
            let sent = async {
                self.js
                    .send_publish(event.event_type.clone(), publish)
                    .await?
                    .await
            }
            .await;
            match sent {
                Ok(_) => published.push(row.try_get("seq")?),
                Err(e) => {
                    failure = Some(e);
                    break;
                }
            }
        }
        // Mark what made it out, even if a later publish failed. The rest stay pending.
        if !published.is_empty() {
            sqlx::query("UPDATE outbox SET published_at = now() WHERE seq = ANY($1)")
                .bind(&published)
                .execute(&mut *tx)
                .await?;
        }
        tx.commit().await?;
        match failure {
            Some(e) => Err(e.into()),
            None => Ok(published.len()),
        }
    }
}

/// Pulls events from a durable JetStream consumer and runs the handler once per event.
///
/// The handler runs inside a tenant-scoped transaction together with the inbox insert, so
/// "processed" and the handler's own writes commit or roll back together.
pub struct InboxConsumer {
    db: Database,
    name: String,
    consumer: PullConsumer,
}

impl InboxConsumer {
    pub async fn new(
        db: Database,
        stream: &stream::Stream,
        name: &str,
        subject: &str,
    ) -> anyhow::Result<Self> {
        let consumer = stream
            .get_or_create_consumer(
                name,
                pull::Config {
                    durable_name: Some(name.to_string()),
                    filter_subject: subject.to_string(),
                    ..Default::default()
                },
            )
            .await?;
        Ok(Self {
            db,
            name: name.to_string(),
            consumer,
        })
    }

    /// Fetch and handle up to `batch` messages, waiting up to `wait` for them.
    /// Returns how many were handled (redelivered duplicates are not counted).
    pub async fn process_batch<H>(
        &self,
        batch: usize,
        wait: Duration,
        handler: &H,
    ) -> anyhow::Result<usize>
    where
        H: AsyncFn(&mut PgConnection, &CloudEvent) -> anyhow::Result<()>,
    {
        let mut messages = self
            .consumer
            .fetch()
            .max_messages(batch)
            .expires(wait)
            .messages()
            .await?;
        let mut handled = 0;
        while let Some(msg) = messages.next().await {
            let msg = msg.map_err(|e| anyhow::anyhow!(e))?;
            handled += self.handle(msg, handler).await?;
        }
        Ok(handled)
    }

    async fn handle<H>(&self, msg: Message, handler: &H) -> anyhow::Result<usize>
    where
        H: AsyncFn(&mut PgConnection, &CloudEvent) -> anyhow::Result<()>,
    {
        let event = match CloudEvent::from_json(&msg.payload) {
            Ok(e) => e,
            Err(e) => {
                tracing::error!(subject = %msg.subject, error = %e, "dropping malformed event");
                msg.ack_with(AckKind::Term)
                    .await
                    .map_err(|e| anyhow::anyhow!(e))?;
                return Ok(0);
            }
        };
        let outcome: anyhow::Result<bool> = async {
            let mut tx = self.db.tenant_tx(event.tenantid).await?;
            let first_time = sqlx::query(
                "INSERT INTO inbox (consumer, event_id, tenant_id) VALUES ($1, $2, $3)
                 ON CONFLICT DO NOTHING",
            )
            .bind(&self.name)
            .bind(event.id)
            .bind(event.tenantid)
            .execute(&mut *tx)
            .await?
            .rows_affected()
                == 1;
            if first_time {
                handler(&mut tx, &event).await?;
            }
            tx.commit().await?;
            Ok(first_time)
        }
        .await;
        match outcome {
            Ok(first_time) => {
                msg.ack().await.map_err(|e| anyhow::anyhow!(e))?;
                Ok(usize::from(first_time))
            }
            Err(e) => {
                tracing::error!(event_id = %event.id, error = %e, "handler failed, will be redelivered");
                msg.ack_with(AckKind::Nak(None))
                    .await
                    .map_err(|e| anyhow::anyhow!(e))?;
                Ok(0)
            }
        }
    }
}
