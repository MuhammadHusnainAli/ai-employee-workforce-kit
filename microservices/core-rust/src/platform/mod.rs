//! Platform code: settings, telemetry, health, tenancy, outbox and inbox.
//!
//! Every service carries its own copy, taken from the template when the service was created.
//! Holds no domain logic. Mirrors the Python `wfk_platform` package so both languages behave
//! the same way at service boundaries.

pub mod db;
pub mod health;
pub mod messaging;
pub mod migrate;
pub mod outbox;
pub mod settings;
pub mod telemetry;

pub use db::Database;
pub use health::{HealthRegistry, health_router};
pub use outbox::{CloudEvent, enqueue};
pub use settings::{Environment, ServiceSettings};
pub use telemetry::{TelemetryGuard, init_telemetry};
