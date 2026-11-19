//! What a sandbox run is allowed to be, without I/O: the image allowlist, the limits, how the
//! code gets into the container, and the Kubernetes pod a run becomes in production.
//!
//! The code never touches the host. It is passed base64-encoded in an environment variable,
//! written to a small `/tmp` (the only writable place), and run there by the image's
//! interpreter, with no network, a read-only root, every capability dropped and a non-root user.

use base64::Engine;
use base64::engine::general_purpose::STANDARD as B64;
use serde_json::{Value, json};

/// Code and stdin travel in environment variables, which Linux caps at 128 KiB each.
pub const MAX_CODE_BYTES: usize = 64 * 1024;
pub const MAX_STDIN_BYTES: usize = 64 * 1024;
/// What is kept of stdout and stderr each; the rest is cut and the result says so.
pub const MAX_OUTPUT_BYTES: usize = 256 * 1024;
/// The unprivileged user every run executes as (`nobody`).
pub const SANDBOX_USER: &str = "65534:65534";
pub const TMP_SIZE_MB: u32 = 64;

/// One image a run may use. Runs never name raw images: only these, pinned by tag.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Image {
    pub name: &'static str,
    pub reference: &'static str,
    /// The script file under /tmp and the interpreter that runs it.
    pub file: &'static str,
    pub interpreter: &'static str,
}

pub const IMAGES: &[Image] = &[
    Image {
        name: "python-3.13",
        reference: "python:3.13-alpine",
        file: "main.py",
        interpreter: "python -I",
    },
    Image {
        name: "node-22",
        reference: "node:22-alpine",
        file: "main.js",
        interpreter: "node",
    },
];

pub fn image(name: &str) -> Option<&'static Image> {
    IMAGES.iter().find(|i| i.name == name)
}

/// Hard limits for one run.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    pub timeout_ms: u64,
    pub memory_mb: u64,
    /// Thousandths of a CPU.
    pub cpu_milli: u64,
    pub pids: i64,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            timeout_ms: 10_000,
            memory_mb: 256,
            cpu_milli: 500,
            pids: 64,
        }
    }
}

impl Limits {
    pub const MAX: Self = Self {
        timeout_ms: 120_000,
        memory_mb: 2048,
        cpu_milli: 2000,
        pids: 256,
    };

    /// Zero means the default; anything above the maximum is refused.
    pub fn resolve(
        timeout_ms: u64,
        memory_mb: u64,
        cpu_milli: u64,
        pids: i64,
    ) -> Result<Self, String> {
        let d = Self::default();
        let pick = |v: u64, def: u64| if v == 0 { def } else { v };
        let l = Self {
            timeout_ms: pick(timeout_ms, d.timeout_ms),
            memory_mb: pick(memory_mb, d.memory_mb),
            cpu_milli: pick(cpu_milli, d.cpu_milli),
            pids: if pids == 0 { d.pids } else { pids },
        };
        let m = Self::MAX;
        if l.timeout_ms > m.timeout_ms
            || l.memory_mb > m.memory_mb
            || l.cpu_milli > m.cpu_milli
            || l.pids < 1
            || l.pids > m.pids
            || l.memory_mb < 16
            || l.cpu_milli < 50
        {
            return Err(format!(
                "limits must be within timeout {} ms, memory 16 to {} MB, cpu 50 to {} milli, \
                 pids 1 to {}",
                m.timeout_ms, m.memory_mb, m.cpu_milli, m.pids
            ));
        }
        Ok(l)
    }
}

/// A validated run.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Spec {
    pub image: &'static Image,
    pub code: String,
    pub stdin: String,
    pub limits: Limits,
}

impl Spec {
    pub fn new(
        image_name: &str,
        code: String,
        stdin: String,
        limits: Limits,
    ) -> Result<Self, String> {
        let image = image(image_name).ok_or_else(|| {
            let names: Vec<_> = IMAGES.iter().map(|i| i.name).collect();
            format!(
                "unknown image {image_name:?}; allowed: {}",
                names.join(", ")
            )
        })?;
        if code.trim().is_empty() {
            return Err("code is required".into());
        }
        if code.len() > MAX_CODE_BYTES {
            return Err(format!("code is at most {MAX_CODE_BYTES} bytes"));
        }
        if stdin.len() > MAX_STDIN_BYTES {
            return Err(format!("stdin is at most {MAX_STDIN_BYTES} bytes"));
        }
        Ok(Self {
            image,
            code,
            stdin,
            limits,
        })
    }

    /// The environment the launcher reads: code and stdin, base64-encoded.
    pub fn env(&self) -> Vec<(String, String)> {
        vec![
            ("WFK_CODE".into(), B64.encode(&self.code)),
            ("WFK_STDIN".into(), B64.encode(&self.stdin)),
        ]
    }

    /// The command: decode the script into /tmp, then run it with stdin from WFK_STDIN.
    pub fn command(&self) -> Vec<String> {
        let file = format!("/tmp/{}", self.image.file);
        vec![
            "/bin/sh".into(),
            "-c".into(),
            format!(
                "printf %s \"$WFK_CODE\" | base64 -d > {file} && \
                 printf %s \"$WFK_STDIN\" | base64 -d | exec {} {file}",
                self.image.interpreter
            ),
        ]
    }

    /// The pod this run is in production: gVisor (`runtimeClassName: gvisor`), the same limits,
    /// no service account token, no privilege, a read-only root and a memory-backed /tmp. The
    /// sandbox node pool's NetworkPolicy denies all traffic for pods labelled
    /// `wfk.io/sandbox: "true"`.
    pub fn pod(&self, name: &str, namespace: &str) -> Value {
        let l = self.limits;
        let env: Vec<Value> = self
            .env()
            .into_iter()
            .map(|(k, v)| json!({"name": k, "value": v}))
            .collect();
        json!({
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "labels": {"app.kubernetes.io/name": "sandbox", "wfk.io/sandbox": "true"},
            },
            "spec": {
                "runtimeClassName": "gvisor",
                "restartPolicy": "Never",
                "automountServiceAccountToken": false,
                "enableServiceLinks": false,
                "activeDeadlineSeconds": l.timeout_ms.div_ceil(1000),
                "nodeSelector": {"wfk.io/pool": "sandbox"},
                "tolerations": [{"key": "wfk.io/sandbox", "operator": "Exists", "effect": "NoSchedule"}],
                "securityContext": {
                    "runAsNonRoot": true,
                    "runAsUser": 65534,
                    "runAsGroup": 65534,
                    "seccompProfile": {"type": "RuntimeDefault"},
                },
                "containers": [{
                    "name": "run",
                    "image": self.image.reference,
                    "command": self.command(),
                    "env": env,
                    "resources": {
                        "limits": {"cpu": format!("{}m", l.cpu_milli),
                                   "memory": format!("{}Mi", l.memory_mb),
                                   "ephemeral-storage": format!("{TMP_SIZE_MB}Mi")},
                        "requests": {"cpu": format!("{}m", l.cpu_milli),
                                     "memory": format!("{}Mi", l.memory_mb)},
                    },
                    "securityContext": {
                        "allowPrivilegeEscalation": false,
                        "readOnlyRootFilesystem": true,
                        "privileged": false,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                }],
                "volumes": [{"name": "tmp",
                             "emptyDir": {"medium": "Memory", "sizeLimit": format!("{TMP_SIZE_MB}Mi")}}],
            },
        })
    }
}

/// Keep at most `max` bytes, on a character boundary. Returns the text and whether it was cut.
pub fn clip(bytes: &[u8], max: usize) -> (String, bool) {
    let text = String::from_utf8_lossy(bytes);
    if text.len() <= max {
        return (text.into_owned(), false);
    }
    let mut end = max;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    (text[..end].to_string(), true)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn spec() -> Spec {
        Spec::new(
            "python-3.13",
            "print(1)".into(),
            String::new(),
            Limits::default(),
        )
        .unwrap()
    }

    #[test]
    fn only_allowlisted_images() {
        assert!(Spec::new("python-3.13", "x".into(), String::new(), Limits::default()).is_ok());
        let err = Spec::new(
            "ubuntu:latest",
            "x".into(),
            String::new(),
            Limits::default(),
        )
        .unwrap_err();
        assert!(err.contains("python-3.13"), "{err}");
    }

    #[test]
    fn code_and_stdin_sizes() {
        let big = "x".repeat(MAX_CODE_BYTES + 1);
        assert!(Spec::new("python-3.13", big, String::new(), Limits::default()).is_err());
        assert!(Spec::new("python-3.13", " ".into(), String::new(), Limits::default()).is_err());
        let stdin = "x".repeat(MAX_STDIN_BYTES + 1);
        assert!(Spec::new("python-3.13", "x".into(), stdin, Limits::default()).is_err());
    }

    #[test]
    fn limits_default_and_cap() {
        assert_eq!(Limits::resolve(0, 0, 0, 0), Ok(Limits::default()));
        assert!(Limits::resolve(120_001, 0, 0, 0).is_err());
        assert!(Limits::resolve(0, 4096, 0, 0).is_err());
        assert!(
            Limits::resolve(0, 8, 0, 0).is_err(),
            "too little memory to start"
        );
        assert!(Limits::resolve(0, 0, 0, -1).is_err());
        assert_eq!(Limits::resolve(5_000, 128, 250, 16).unwrap().memory_mb, 128);
    }

    #[test]
    fn the_code_travels_encoded_and_runs_from_tmp() {
        let s = Spec::new(
            "python-3.13",
            "print('a\"b $HOME')".into(),
            "in".into(),
            Limits::default(),
        )
        .unwrap();
        let env: std::collections::HashMap<_, _> = s.env().into_iter().collect();
        assert_eq!(
            B64.decode(&env["WFK_CODE"]).unwrap(),
            b"print('a\"b $HOME')"
        );
        let cmd = s.command();
        assert_eq!(&cmd[..2], ["/bin/sh", "-c"]);
        assert!(cmd[2].contains("exec python -I /tmp/main.py"));
        assert!(
            !cmd[2].contains("print("),
            "the code is never in the command line"
        );
    }

    #[test]
    fn the_pod_is_gvisor_and_locked_down() {
        let p = spec().pod("run-1", "sandbox");
        let s = &p["spec"];
        assert_eq!(s["runtimeClassName"], "gvisor");
        assert_eq!(s["automountServiceAccountToken"], false);
        assert_eq!(s["activeDeadlineSeconds"], 10);
        assert_eq!(s["securityContext"]["runAsNonRoot"], true);
        let c = &s["containers"][0];
        assert_eq!(c["securityContext"]["readOnlyRootFilesystem"], true);
        assert_eq!(c["securityContext"]["allowPrivilegeEscalation"], false);
        assert_eq!(c["securityContext"]["capabilities"]["drop"], json!(["ALL"]));
        assert_eq!(c["resources"]["limits"]["memory"], "256Mi");
        assert_eq!(c["image"], "python:3.13-alpine");
        assert_eq!(p["metadata"]["labels"]["wfk.io/sandbox"], "true");
    }

    #[test]
    fn output_is_clipped_on_a_char_boundary() {
        assert_eq!(clip(b"hello", 10), ("hello".into(), false));
        let (t, cut) = clip("héllo".as_bytes(), 2);
        assert_eq!((t.as_str(), cut), ("h", true));
    }
}
