//! Service settings read from the environment (prefix `WFK_`), matching the Python
//! `ServiceSettings`. Defaults point at the local stack in `deployments/docker-compose.yml`.

use serde::Deserialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Default)]
#[serde(rename_all = "lowercase")]
pub enum Environment {
    #[default]
    Local,
    Test,
    Staging,
    Production,
}

impl Environment {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Local => "local",
            Self::Test => "test",
            Self::Staging => "staging",
            Self::Production => "production",
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ServiceSettings {
    pub service_name: String,
    #[serde(default)]
    pub environment: Environment,
    #[serde(default = "d::cell_id")]
    pub cell_id: String,
    #[serde(default = "d::log_level")]
    pub log_level: String,
    #[serde(default = "d::yes")]
    pub log_json: bool,

    /// One port per service (deployments/README.md, port map): HTTP health and gRPC share it.
    #[serde(default = "d::host")]
    pub host: String,
    #[serde(default = "d::port")]
    pub port: u16,

    #[serde(default = "d::database_url")]
    pub database_url: String,
    #[serde(default = "d::nats_url")]
    pub nats_url: String,

    /// Empty disables export (spans are still created, just not sent).
    #[serde(default)]
    pub otlp_endpoint: String,

    /// Deadline for each readiness check, in milliseconds.
    #[serde(default = "d::readiness_timeout_ms")]
    pub readiness_timeout_ms: u64,
}

#[derive(Debug, thiserror::Error)]
pub enum SettingsError {
    #[error("invalid settings: {0}")]
    Invalid(String),
}

impl ServiceSettings {
    /// Read from the process environment.
    pub fn from_env() -> Result<Self, SettingsError> {
        Self::from_vars(std::env::vars())
    }

    /// Read from an explicit list of variables. Tests use this instead of mutating the
    /// process environment.
    pub fn from_vars<I>(vars: I) -> Result<Self, SettingsError>
    where
        I: IntoIterator<Item = (String, String)>,
    {
        let s: Self = envy::prefixed("WFK_")
            .from_iter(vars)
            .map_err(|e| SettingsError::Invalid(e.to_string()))?;
        s.validate()
    }

    fn validate(self) -> Result<Self, SettingsError> {
        if self.service_name.trim().is_empty() {
            return Err(SettingsError::Invalid("WFK_SERVICE_NAME is empty".into()));
        }
        if self.port == 0 {
            return Err(SettingsError::Invalid("port must be 1..=65535".into()));
        }
        let level = self.log_level.to_ascii_uppercase();
        if !["DEBUG", "INFO", "WARNING", "WARN", "ERROR"].contains(&level.as_str()) {
            return Err(SettingsError::Invalid(format!(
                "unknown log level {}",
                self.log_level
            )));
        }
        Ok(self)
    }
}

mod d {
    pub fn cell_id() -> String {
        "cell-local".into()
    }
    pub fn log_level() -> String {
        "INFO".into()
    }
    pub fn yes() -> bool {
        true
    }
    pub fn host() -> String {
        "0.0.0.0".into()
    }
    // One local port, from the 7000-7020 range (deployments/README.md). Give a new service its
    // own free port: two services on the same port cannot run side by side.
    pub fn port() -> u16 {
        7020
    }
    pub fn database_url() -> String {
        "postgresql://wfk:wfk@127.0.0.1:7000/wfk".into()
    }
    pub fn nats_url() -> String {
        "nats://127.0.0.1:7003".into()
    }
    pub fn readiness_timeout_ms() -> u64 {
        2000
    }
}
