//! Task statuses, and how run events move them. Pure: no I/O.
//!
//! A task's status is a projection of its run's events. Events can arrive late or twice, so
//! each carries the run's ledger sequence and only a newer one may change the task; a finished
//! task never changes again.

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Queued,
    Running,
    WaitingHuman,
    Completed,
    Failed,
    Cancelled,
}

impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Queued => "queued",
            Self::Running => "running",
            Self::WaitingHuman => "waiting_human",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "queued" => Self::Queued,
            "running" => Self::Running,
            "waiting_human" => Self::WaitingHuman,
            "completed" => Self::Completed,
            "failed" => Self::Failed,
            "cancelled" => Self::Cancelled,
            _ => return None,
        })
    }

    pub fn is_finished(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }
}

/// What one run event says about the task.
#[derive(Debug, Clone, PartialEq)]
pub struct RunUpdate {
    /// The run ledger sequence; run.started (which has none) counts as -1.
    pub seq: i64,
    pub status: Status,
    pub stop_reason: String,
}

/// Read a run event (run.started, run.step_completed, run.completed, run.failed).
pub fn run_update(event_type: &str, data: &Value) -> Option<RunUpdate> {
    let seq = data.get("seq").and_then(Value::as_i64);
    let stop_reason = data
        .get("stop_reason")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let kind = data.get("kind").and_then(Value::as_str).unwrap_or_default();
    match event_type {
        "run.started" => Some(RunUpdate {
            seq: -1,
            status: Status::Running,
            stop_reason: String::new(),
        }),
        "run.step_completed" => {
            let status = match data.get("status").and_then(Value::as_str)? {
                "failed" if kind == "cancelled" => Status::Cancelled,
                other => Status::parse(other)?,
            };
            Some(RunUpdate {
                seq: seq?,
                status,
                stop_reason,
            })
        }
        "run.completed" => Some(RunUpdate {
            seq: seq?,
            status: Status::Completed,
            stop_reason,
        }),
        "run.failed" => Some(RunUpdate {
            seq: seq?,
            status: if stop_reason == "cancelled" {
                Status::Cancelled
            } else {
                Status::Failed
            },
            stop_reason,
        }),
        _ => None,
    }
}

/// Should `update` be applied to a task that is `current` at sequence `current_seq`?
pub fn applies(current: Status, current_seq: i64, update: &RunUpdate) -> bool {
    // run.completed / run.failed share the seq of the last step: equal is fine for them,
    // which is why a finished status may follow at the same seq.
    let newer =
        update.seq > current_seq || (update.seq == current_seq && update.status.is_finished());
    !current.is_finished() && newer
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn reads_every_run_event() {
        let started = run_update("run.started", &json!({"run_id": "r"})).unwrap();
        assert_eq!((started.seq, started.status), (-1, Status::Running));
        let waiting = run_update(
            "run.step_completed",
            &json!({"seq": 1, "status": "waiting_human", "kind": "ask_human"}),
        )
        .unwrap();
        assert_eq!((waiting.seq, waiting.status), (1, Status::WaitingHuman));
        let cancelled = run_update(
            "run.step_completed",
            &json!({"seq": 4, "status": "failed", "kind": "cancelled"}),
        )
        .unwrap();
        assert_eq!(cancelled.status, Status::Cancelled);
        let done = run_update("run.completed", &json!({"seq": 5, "stop_reason": "final"})).unwrap();
        assert_eq!(
            (done.status, done.stop_reason.as_str()),
            (Status::Completed, "final")
        );
        let failed = run_update("run.failed", &json!({"seq": 5, "stop_reason": "error"})).unwrap();
        assert_eq!(failed.status, Status::Failed);
        assert!(run_update("run.unknown", &json!({})).is_none());
        assert!(run_update("run.step_completed", &json!({"status": "running"})).is_none());
    }

    #[test]
    fn only_newer_events_move_a_task_and_finished_tasks_never_move() {
        let at = |seq, status| RunUpdate {
            seq,
            status,
            stop_reason: String::new(),
        };
        assert!(applies(Status::Queued, -2, &at(-1, Status::Running)));
        assert!(applies(Status::Running, 1, &at(2, Status::WaitingHuman)));
        assert!(
            !applies(Status::WaitingHuman, 3, &at(2, Status::Running)),
            "late event"
        );
        assert!(
            !applies(Status::Running, 2, &at(2, Status::Running)),
            "duplicate"
        );
        assert!(
            applies(Status::Running, 4, &at(4, Status::Completed)),
            "final at same seq"
        );
        assert!(
            !applies(Status::Completed, 4, &at(9, Status::Running)),
            "finished stays"
        );
        assert!(!applies(Status::Cancelled, -2, &at(-1, Status::Running)));
    }

    #[test]
    fn status_names_round_trip() {
        for s in [
            Status::Queued,
            Status::Running,
            Status::WaitingHuman,
            Status::Completed,
            Status::Failed,
            Status::Cancelled,
        ] {
            assert_eq!(Status::parse(s.as_str()), Some(s));
        }
    }
}
