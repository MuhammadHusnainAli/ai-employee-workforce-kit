//! From channel to task and back.
//!
//! - **Forward:** each stored message becomes a task in task-service (`CreateTask`, idempotent
//!   on the message id, so a retry never makes two tasks).
//! - **Answer:** agent-runtime's run events (`RUNS` stream) become deliveries in the thread the
//!   task came from: the output when the run completes, a note when it needs a person or fails.
//! - **Send:** each delivery is posted to the provider, retried with backoff, and its receipt
//!   (`channel.message_delivered`) recorded.
//!
//! The loops find work through id-only queues (`forward_queue`, `delivery_queue`) and do it in
//! a tenant transaction, so row-level security holds for every read and write.

use std::time::Duration;

use futures::future::BoxFuture;
use serde_json::{Value, json};
use sqlx::{PgConnection, Row};
use tokio::sync::watch;
use uuid::Uuid;

use crate::channel::adapters::{BindingRow, ChannelError, Channels};
use crate::channel::domain;
use crate::contracts::wfk::common::v1::{Actor, IdempotencyKey, TenantContext};
use crate::contracts::wfk::task_service::v1::CreateTaskRequest;
use crate::contracts::wfk::task_service::v1::task_service_client::TaskServiceClient;
use crate::platform::CloudEvent;
use crate::platform::messaging::EventHandler;

/// After this many failed attempts a message or delivery is marked failed and leaves its queue.
pub const MAX_ATTEMPTS: i32 = 8;
const IDLE: Duration = Duration::from_millis(250);
const BATCH: i64 = 20;

/// Wait before attempt `n` (1-based): 1 s, 2 s, 4 s ... capped at 5 minutes.
pub fn backoff(attempts: i32) -> Duration {
    let secs = 1u64 << attempts.clamp(0, 16).saturating_sub(1);
    Duration::from_secs(secs.min(300))
}

const DUE_FORWARDS: &str = "SELECT inbound_id AS id, tenant_id FROM forward_queue
    WHERE next_attempt_at <= now() ORDER BY next_attempt_at LIMIT $1";
const DUE_DELIVERIES: &str = "SELECT delivery_id AS id, tenant_id FROM delivery_queue
    WHERE next_attempt_at <= now() ORDER BY next_attempt_at LIMIT $1";

/// Due queue entries (id, tenant), oldest first.
async fn due(
    db: &crate::platform::Database,
    sql: &'static str,
) -> Result<Vec<(Uuid, Uuid)>, sqlx::Error> {
    let rows = sqlx::query(sql).bind(BATCH).fetch_all(&db.pool).await?;
    rows.iter()
        .map(|r| Ok((r.try_get("id")?, r.try_get("tenant_id")?)))
        .collect()
}

// ---- forward: message -> task ------------------------------------------------------------

#[derive(Clone)]
pub struct Forwarder {
    pub channels: Channels,
    pub tasks: TaskServiceClient<tonic::transport::Channel>,
}

impl Forwarder {
    /// Forward every due message once. Returns how many became tasks.
    pub async fn forward_due(&mut self) -> anyhow::Result<usize> {
        let mut done = 0;
        for (inbound, tenant) in due(&self.channels.db, DUE_FORWARDS).await? {
            done += usize::from(self.forward(tenant, inbound).await?);
        }
        Ok(done)
    }

    async fn forward(&mut self, tenant: Uuid, inbound: Uuid) -> anyhow::Result<bool> {
        let row = {
            let mut tx = self.channels.db.tenant_tx(tenant).await?;
            sqlx::query(
                "SELECT m.text, m.sender, m.attempts, b.employee_id, b.kind
                 FROM inbound_messages m JOIN bindings b ON b.id = m.binding_id
                 WHERE m.id = $1",
            )
            .bind(inbound)
            .fetch_optional(&mut *tx)
            .await?
        };
        let Some(row) = row else {
            sqlx::query("DELETE FROM forward_queue WHERE inbound_id = $1")
                .bind(inbound)
                .execute(&self.channels.db.pool)
                .await?;
            return Ok(false);
        };
        let text: String = row.try_get("text")?;
        let sender: String = row.try_get("sender")?;
        let kind: String = row.try_get("kind")?;
        let title: String = text
            .lines()
            .next()
            .unwrap_or_default()
            .chars()
            .take(80)
            .collect();
        let req = CreateTaskRequest {
            tenant: Some(TenantContext {
                tenant_id: tenant.to_string(),
                actor: Some(Actor {
                    kind: 3, // ACTOR_KIND_SERVICE: the channel speaks for the sender
                    id: format!("{kind}:{sender}"),
                }),
                ..Default::default()
            }),
            idempotency_key: Some(IdempotencyKey {
                value: format!("channel:{inbound}"),
            }),
            employee_id: row.try_get("employee_id")?,
            title,
            input: text,
            priority: 0,
        };
        let mut call = tonic::Request::new(req);
        call.set_timeout(Duration::from_secs(10));
        let result = self.tasks.create_task(call).await;
        let mut tx = self.channels.db.tenant_tx(tenant).await?;
        match result {
            Ok(resp) => {
                let task_id = resp
                    .into_inner()
                    .task
                    .and_then(|t| Uuid::parse_str(&t.task_id).ok())
                    .ok_or_else(|| anyhow::anyhow!("task-service returned no task id"))?;
                sqlx::query(
                    "UPDATE inbound_messages SET status = 'forwarded', task_id = $2,
                        attempts = attempts + 1, last_error = NULL WHERE id = $1",
                )
                .bind(inbound)
                .bind(task_id)
                .execute(&mut *tx)
                .await?;
                sqlx::query("DELETE FROM forward_queue WHERE inbound_id = $1")
                    .bind(inbound)
                    .execute(&mut *tx)
                    .await?;
                tx.commit().await?;
                Ok(true)
            }
            Err(status) => {
                let attempts: i32 = row.try_get::<i32, _>("attempts")? + 1;
                // A request task-service says is wrong will not get better by retrying.
                let permanent = matches!(
                    status.code(),
                    tonic::Code::InvalidArgument
                        | tonic::Code::AlreadyExists
                        | tonic::Code::PermissionDenied
                );
                let give_up = permanent || attempts >= MAX_ATTEMPTS;
                sqlx::query(
                    "UPDATE inbound_messages SET attempts = $2, last_error = $3,
                        status = CASE WHEN $4 THEN 'failed' ELSE status END WHERE id = $1",
                )
                .bind(inbound)
                .bind(attempts)
                .bind(status.message())
                .bind(give_up)
                .execute(&mut *tx)
                .await?;
                if give_up {
                    sqlx::query("DELETE FROM forward_queue WHERE inbound_id = $1")
                        .bind(inbound)
                        .execute(&mut *tx)
                        .await?;
                } else {
                    sqlx::query(
                        "UPDATE forward_queue SET next_attempt_at = now() + $2 WHERE inbound_id = $1",
                    )
                    .bind(inbound)
                    .bind(backoff(attempts))
                    .execute(&mut *tx)
                    .await?;
                }
                tx.commit().await?;
                tracing::warn!(%inbound, attempts, give_up, error = %status, "forward failed");
                Ok(false)
            }
        }
    }
}

// ---- answer: run events -> deliveries ------------------------------------------------------

/// What a run event says back to the person, if anything.
pub fn answer_for(event_type: &str, data: &Value) -> Option<(&'static str, String)> {
    let stop = data
        .get("stop_reason")
        .and_then(Value::as_str)
        .unwrap_or_default();
    match event_type {
        "run.completed" => {
            let out = data
                .get("output")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .trim();
            let text = if out.is_empty() { "Done." } else { out };
            Some(("answer", text.to_string()))
        }
        "run.failed" if stop == "cancelled" => None,
        "run.failed" => Some((
            "failure",
            format!(
                "I could not finish this ({}). A person will take a look.",
                if stop.is_empty() { "error" } else { stop }
            ),
        )),
        "run.step_completed"
            if data.get("status").and_then(Value::as_str) == Some("waiting_human") =>
        {
            let seq = data.get("seq").and_then(Value::as_i64).unwrap_or_default();
            Some((
                "approval_request",
                format!(
                    "I need a person before I go on (step {seq}). Approve, decline or reply in the web app."
                ),
            ))
        }
        _ => None,
    }
}

/// Inbox handler for the RUNS stream.
pub struct RunAnswers(pub Channels);

impl RunAnswers {
    pub async fn apply(
        &self,
        conn: &mut PgConnection,
        event: &CloudEvent,
    ) -> Result<(), ChannelError> {
        let Some((purpose, text)) = answer_for(&event.event_type, &event.data) else {
            return Ok(());
        };
        let Some(task_id) = event
            .data
            .get("run_id")
            .and_then(Value::as_str)
            .and_then(|s| Uuid::parse_str(s).ok())
        else {
            return Ok(());
        };
        let inbound =
            sqlx::query("SELECT id, binding_id, thread FROM inbound_messages WHERE task_id = $1")
                .bind(task_id)
                .fetch_optional(&mut *conn)
                .await?;
        let Some(m) = inbound else {
            return Ok(()); // the task did not come from a channel
        };
        let inbound_id: Uuid = m.try_get("id")?;
        let delivery = Uuid::new_v4();
        let inserted = sqlx::query(
            "INSERT INTO deliveries (id, tenant_id, binding_id, inbound_id, task_id, thread,
                purpose, text)
             VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
             ON CONFLICT (inbound_id, purpose, md5(text)) WHERE inbound_id IS NOT NULL DO NOTHING",
        )
        .bind(delivery)
        .bind(event.tenantid)
        .bind(m.try_get::<Uuid, _>("binding_id")?)
        .bind(inbound_id)
        .bind(task_id)
        .bind(m.try_get::<String, _>("thread")?)
        .bind(purpose)
        .bind(&text)
        .execute(&mut *conn)
        .await?
        .rows_affected()
            == 1;
        if inserted {
            sqlx::query("INSERT INTO delivery_queue (delivery_id, tenant_id) VALUES ($1, $2)")
                .bind(delivery)
                .bind(event.tenantid)
                .execute(&mut *conn)
                .await?;
        }
        Ok(())
    }
}

impl EventHandler for RunAnswers {
    fn handle<'a>(
        &'a self,
        conn: &'a mut PgConnection,
        event: &'a CloudEvent,
    ) -> BoxFuture<'a, anyhow::Result<()>> {
        Box::pin(async move {
            self.apply(conn, event)
                .await
                .map_err(|e| anyhow::anyhow!(e))
        })
    }
}

// ---- send: deliveries -> provider ----------------------------------------------------------

#[derive(Clone)]
pub struct Sender {
    pub channels: Channels,
    pub http: reqwest::Client,
}

impl Sender {
    pub fn new(channels: Channels) -> Self {
        Self {
            channels,
            http: reqwest::Client::builder()
                .timeout(Duration::from_secs(10))
                .build()
                .unwrap_or_default(),
        }
    }

    /// Send every due delivery once. Returns how many were sent.
    pub async fn send_due(&self) -> anyhow::Result<usize> {
        let mut sent = 0;
        for (delivery, tenant) in due(&self.channels.db, DUE_DELIVERIES).await? {
            sent += usize::from(self.send(tenant, delivery).await?);
        }
        Ok(sent)
    }

    async fn send(&self, tenant: Uuid, delivery: Uuid) -> anyhow::Result<bool> {
        let (binding, thread, text, attempts) = {
            let mut tx = self.channels.db.tenant_tx(tenant).await?;
            let d = sqlx::query(
                "SELECT binding_id, thread, text, attempts FROM deliveries WHERE id = $1",
            )
            .bind(delivery)
            .fetch_one(&mut *tx)
            .await?;
            let binding_id: Uuid = d.try_get("binding_id")?;
            let b = sqlx::query("SELECT * FROM bindings WHERE id = $1")
                .bind(binding_id)
                .fetch_one(&mut *tx)
                .await?;
            (
                BindingRow::from_row(&b)?,
                d.try_get::<String, _>("thread")?,
                d.try_get::<String, _>("text")?,
                d.try_get::<i32, _>("attempts")?,
            )
        };
        let outcome = if binding.outbound.url.is_empty() {
            Err("the binding has no outbound url".to_string())
        } else {
            let req = domain::reply(
                binding.kind,
                &binding.outbound,
                &binding.secret,
                &thread,
                &text,
            );
            self.post(&req).await
        };
        let attempts = attempts + 1;
        let mut tx = self.channels.db.tenant_tx(tenant).await?;
        match outcome {
            Ok(provider_ref) => {
                sqlx::query(
                    "UPDATE deliveries SET status = 'sent', attempts = $2, provider_ref = $3,
                        last_error = NULL, sent_at = now() WHERE id = $1",
                )
                .bind(delivery)
                .bind(attempts)
                .bind(&provider_ref)
                .execute(&mut *tx)
                .await?;
                sqlx::query("DELETE FROM delivery_queue WHERE delivery_id = $1")
                    .bind(delivery)
                    .execute(&mut *tx)
                    .await?;
                let data = json!({"delivery_id": delivery, "binding_id": binding.id,
                                  "thread": thread, "provider_ref": provider_ref});
                self.channels
                    .emit(
                        &mut tx,
                        tenant,
                        "channel.message_delivered",
                        delivery.to_string(),
                        data,
                    )
                    .await?;
                tx.commit().await?;
                Ok(true)
            }
            Err(error) => {
                let give_up = attempts >= MAX_ATTEMPTS || binding.outbound.url.is_empty();
                sqlx::query(
                    "UPDATE deliveries SET attempts = $2, last_error = $3,
                        status = CASE WHEN $4 THEN 'failed' ELSE status END WHERE id = $1",
                )
                .bind(delivery)
                .bind(attempts)
                .bind(&error)
                .bind(give_up)
                .execute(&mut *tx)
                .await?;
                if give_up {
                    sqlx::query("DELETE FROM delivery_queue WHERE delivery_id = $1")
                        .bind(delivery)
                        .execute(&mut *tx)
                        .await?;
                    let data = json!({"delivery_id": delivery, "error": error});
                    self.channels
                        .emit(
                            &mut tx,
                            tenant,
                            "channel.delivery_failed",
                            delivery.to_string(),
                            data,
                        )
                        .await?;
                } else {
                    sqlx::query(
                        "UPDATE delivery_queue SET next_attempt_at = now() + $2 WHERE delivery_id = $1",
                    )
                    .bind(delivery)
                    .bind(backoff(attempts))
                    .execute(&mut *tx)
                    .await?;
                }
                tx.commit().await?;
                tracing::warn!(%delivery, attempts, give_up, %error, "delivery failed");
                Ok(false)
            }
        }
    }

    /// POST the reply. Ok(provider reference) on success. Slack reports errors inside a 200
    /// (`{"ok": false}`), so its body is checked too.
    async fn post(&self, req: &domain::Request) -> Result<String, String> {
        let mut r = self.http.post(&req.url).json(&req.body);
        for (k, v) in &req.headers {
            if k != "content-type" {
                r = r.header(k, v);
            }
        }
        let resp = r.send().await.map_err(|e| e.to_string())?;
        let status = resp.status();
        let body: Value = resp.json().await.unwrap_or(Value::Null);
        if !status.is_success() {
            return Err(format!("provider answered {status}"));
        }
        if body.get("ok") == Some(&Value::Bool(false)) {
            let why = body
                .get("error")
                .and_then(Value::as_str)
                .unwrap_or("not ok");
            return Err(format!("provider refused: {why}"));
        }
        Ok(body
            .get("ts")
            .or_else(|| body.get("name"))
            .or_else(|| body.get("id"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string())
    }
}

// ---- the loops -----------------------------------------------------------------------------

async fn pause(stop: &mut watch::Receiver<bool>, d: Duration) -> bool {
    if *stop.borrow() {
        return true;
    }
    tokio::select! {
        _ = stop.changed() => {}
        _ = tokio::time::sleep(d) => {}
    }
    *stop.borrow()
}

/// Forward messages to task-service until `stop`. Connects lazily, so task-service may start
/// later; messages wait in the queue meanwhile.
pub async fn run_forwarder(
    channels: Channels,
    task_service: String,
    mut stop: watch::Receiver<bool>,
) {
    let endpoint = match tonic::transport::Endpoint::from_shared(task_service) {
        Ok(e) => e.connect_timeout(Duration::from_secs(3)),
        Err(e) => {
            tracing::error!(error = %e, "bad task-service address; forwarding is off");
            return;
        }
    };
    let mut f = Forwarder {
        channels,
        tasks: TaskServiceClient::new(endpoint.connect_lazy()),
    };
    loop {
        let mut halt = stop.clone();
        tokio::select! {
            r = f.forward_due() => {
                if let Err(e) = r {
                    tracing::warn!(error = %e, "forwarder paused");
                }
            }
            // Stop at once, even mid-pass: an unfinished forward stays queued for next time.
            _ = halt.wait_for(|s| *s) => return,
        }
        if pause(&mut stop, IDLE).await {
            return;
        }
    }
}

/// Send deliveries until `stop`.
pub async fn run_sender(channels: Channels, mut stop: watch::Receiver<bool>) {
    let s = Sender::new(channels);
    loop {
        let mut halt = stop.clone();
        tokio::select! {
            r = s.send_due() => {
                if let Err(e) = r {
                    tracing::warn!(error = %e, "sender paused");
                }
            }
            // Stop at once, even mid-pass: an unfinished delivery stays queued for next time.
            _ = halt.wait_for(|s| *s) => return,
        }
        if pause(&mut stop, IDLE).await {
            return;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_grows_and_is_capped() {
        assert_eq!(backoff(1), Duration::from_secs(1));
        assert_eq!(backoff(4), Duration::from_secs(8));
        assert_eq!(backoff(20), Duration::from_secs(300));
    }

    #[test]
    fn what_each_run_event_says_back() {
        let done = answer_for("run.completed", &json!({"output": " Summary: fine. "}));
        assert_eq!(done, Some(("answer", "Summary: fine.".into())));
        assert_eq!(
            answer_for("run.completed", &json!({})),
            Some(("answer", "Done.".into()))
        );
        let failed = answer_for("run.failed", &json!({"stop_reason": "budget"})).unwrap();
        assert_eq!(failed.0, "failure");
        assert!(failed.1.contains("budget"));
        assert_eq!(
            answer_for("run.failed", &json!({"stop_reason": "cancelled"})),
            None
        );
        let waiting = answer_for(
            "run.step_completed",
            &json!({"status": "waiting_human", "seq": 3}),
        );
        assert_eq!(waiting.map(|w| w.0), Some("approval_request"));
        assert_eq!(
            answer_for("run.step_completed", &json!({"status": "running"})),
            None
        );
        assert_eq!(answer_for("run.started", &json!({})), None);
    }
}
