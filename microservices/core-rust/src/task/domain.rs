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

/// How deep delegation may go: the goal is depth 0, its sub-tasks 1, and so on.
pub const MAX_DEPTH: i16 = 3;
/// How many sub-tasks one task may have, over its whole life.
pub const MAX_CHILDREN: i64 = 20;
/// How much of a sub-task's output travels up to its parent, in characters.
pub const MAX_CHILD_OUTPUT: usize = 4_000;

/// The depth of a new sub-task, or why the parent cannot take another one.
pub fn check_child(parent: Status, parent_depth: i16, children: i64) -> Result<i16, String> {
    if parent.is_finished() {
        return Err(format!(
            "the parent task is already {}, so it takes no more sub-tasks",
            parent.as_str()
        ));
    }
    if parent_depth >= MAX_DEPTH {
        return Err(format!("sub-tasks go at most {MAX_DEPTH} levels deep"));
    }
    if children >= MAX_CHILDREN {
        return Err(format!("a task has at most {MAX_CHILDREN} sub-tasks"));
    }
    Ok(parent_depth + 1)
}

/// A sub-task's output, cut to what its parent receives.
pub fn child_output(output: &str) -> String {
    if output.chars().count() <= MAX_CHILD_OUTPUT {
        return output.to_string();
    }
    let mut cut: String = output.chars().take(MAX_CHILD_OUTPUT).collect();
    cut.push('…');
    cut
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
    match event_type {
        "run.started" => Some(RunUpdate {
            seq: -1,
            status: Status::Running,
            stop_reason: String::new(),
        }),
        "run.step_completed" => {
            let status = Status::parse(data.get("status").and_then(Value::as_str)?)?;
            if status.is_finished() {
                // The run.completed / run.failed that follows finishes the task, with the
                // stop reason the step event does not carry.
                return None;
            }
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
    fn a_child_sits_one_level_below_an_open_parent() {
        assert_eq!(check_child(Status::Running, 0, 0), Ok(1));
        assert_eq!(check_child(Status::Queued, 2, 19), Ok(3));
    }

    #[test]
    fn children_are_capped_in_depth_and_number() {
        assert!(
            check_child(Status::Running, MAX_DEPTH, 0).is_err(),
            "too deep"
        );
        assert!(
            check_child(Status::Running, 0, MAX_CHILDREN).is_err(),
            "too many"
        );
        assert!(check_child(Status::Completed, 0, 0).is_err(), "parent done");
        assert!(check_child(Status::Cancelled, 0, 0).is_err(), "parent done");
    }

    #[test]
    fn child_output_is_cut_on_a_character_boundary() {
        let long = "é".repeat(MAX_CHILD_OUTPUT + 10);
        let cut = child_output(&long);
        assert_eq!(cut.chars().count(), MAX_CHILD_OUTPUT + 1);
        assert!(cut.ends_with('…'));
        assert_eq!(child_output("short"), "short");
    }

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
        let last_step = json!({"seq": 4, "status": "completed", "kind": "final"});
        assert!(
            run_update("run.step_completed", &last_step).is_none(),
            "only run.completed / run.failed finish a task"
        );
        let cancelled = run_update(
            "run.failed",
            &json!({"seq": 4, "status": "failed", "stop_reason": "cancelled"}),
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
