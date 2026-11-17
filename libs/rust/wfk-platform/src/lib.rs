//! Infrastructure shared by Rust services: settings, telemetry, health, tenancy, outbox and inbox.
//!
//! Holds no domain logic. Mirrors the Python `wfk-platform` library so both languages behave
//! the same way at service boundaries.

pub mod health;
pub mod settings;
pub mod telemetry;

pub use health::{HealthRegistry, health_router};
pub use settings::{Environment, ServiceSettings};
pub use telemetry::{TelemetryGuard, init_telemetry};
