//! Channel protocols, without I/O: how each provider signs its webhooks, what its payloads
//! mean, and what a reply to it looks like.
//!
//! | kind          | inbound check                                                   |
//! |---------------|-----------------------------------------------------------------|
//! | `slack`       | `X-Slack-Signature: v0=hex(HMAC-SHA256(secret, "v0:{ts}:{body}"))`, ts within 5 min |
//! | `teams`       | `Authorization: HMAC base64(HMAC-SHA256(base64-decoded secret, body))` |
//! | `google_chat` | `Authorization: Bearer <JWT>` signed by Google (checked in `adapters`) |
//! | `email`       | `X-WFK-Signature: sha256=hex(HMAC-SHA256(secret, body))` from the mail relay |
//! | `webhook`     | the same `X-WFK-Signature`                                       |
//!
//! Every inbound message carries the provider's own message id. It is unique per binding, so a
//! webhook delivered twice becomes one task.

use base64::Engine;
use base64::engine::general_purpose::STANDARD as B64;
use hmac::{Hmac, Mac};
use serde_json::{Value, json};
use sha2::Sha256;

type HmacSha256 = Hmac<Sha256>;

/// Slack rejects requests older than this to stop replays; so do we.
pub const MAX_SKEW_SECS: i64 = 300;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Slack,
    Teams,
    GoogleChat,
    Email,
    Webhook,
}

impl Kind {
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "slack" => Self::Slack,
            "teams" => Self::Teams,
            "google_chat" => Self::GoogleChat,
            "email" => Self::Email,
            "webhook" => Self::Webhook,
            _ => return None,
        })
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Self::Slack => "slack",
            Self::Teams => "teams",
            Self::GoogleChat => "google_chat",
            Self::Email => "email",
            Self::Webhook => "webhook",
        }
    }
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum VerifyError {
    #[error("missing signature")]
    Missing,
    #[error("bad signature")]
    Bad,
    #[error("request too old or from the future")]
    Stale,
}

fn hmac(key: &[u8], parts: &[&[u8]]) -> HmacSha256 {
    let mut mac = HmacSha256::new_from_slice(key).expect("HMAC takes any key length");
    for p in parts {
        mac.update(p);
    }
    mac
}

/// `sha256=<hex>` over the body, the signature this platform's relays and webhooks send.
pub fn sign_wfk(secret: &str, body: &[u8]) -> String {
    let tag = hmac(secret.as_bytes(), &[body]).finalize().into_bytes();
    format!("sha256={}", hex::encode(tag))
}

pub fn verify_wfk(secret: &str, signature: Option<&str>, body: &[u8]) -> Result<(), VerifyError> {
    let sig = signature.ok_or(VerifyError::Missing)?;
    let hex_sig = sig.strip_prefix("sha256=").ok_or(VerifyError::Bad)?;
    let raw = hex::decode(hex_sig).map_err(|_| VerifyError::Bad)?;
    hmac(secret.as_bytes(), &[body])
        .verify_slice(&raw)
        .map_err(|_| VerifyError::Bad)
}

pub fn sign_slack(secret: &str, ts: i64, body: &[u8]) -> String {
    let base = format!("v0:{ts}:");
    let tag = hmac(secret.as_bytes(), &[base.as_bytes(), body])
        .finalize()
        .into_bytes();
    format!("v0={}", hex::encode(tag))
}

pub fn verify_slack(
    secret: &str,
    timestamp: Option<&str>,
    signature: Option<&str>,
    body: &[u8],
    now: i64,
) -> Result<(), VerifyError> {
    let ts: i64 = timestamp
        .ok_or(VerifyError::Missing)?
        .parse()
        .map_err(|_| VerifyError::Bad)?;
    if (now - ts).abs() > MAX_SKEW_SECS {
        return Err(VerifyError::Stale);
    }
    let sig = signature.ok_or(VerifyError::Missing)?;
    let raw = hex::decode(sig.strip_prefix("v0=").ok_or(VerifyError::Bad)?)
        .map_err(|_| VerifyError::Bad)?;
    let base = format!("v0:{ts}:");
    hmac(secret.as_bytes(), &[base.as_bytes(), body])
        .verify_slice(&raw)
        .map_err(|_| VerifyError::Bad)
}

/// Teams outgoing webhooks: the security token Teams shows is base64; the HMAC key is its bytes.
pub fn sign_teams(secret_b64: &str, body: &[u8]) -> Option<String> {
    let key = B64.decode(secret_b64).ok()?;
    Some(format!(
        "HMAC {}",
        B64.encode(hmac(&key, &[body]).finalize().into_bytes())
    ))
}

pub fn verify_teams(
    secret_b64: &str,
    authorization: Option<&str>,
    body: &[u8],
) -> Result<(), VerifyError> {
    let auth = authorization.ok_or(VerifyError::Missing)?;
    let given = auth.strip_prefix("HMAC ").ok_or(VerifyError::Bad)?;
    let raw = B64.decode(given.trim()).map_err(|_| VerifyError::Bad)?;
    let key = B64.decode(secret_b64).map_err(|_| VerifyError::Bad)?;
    hmac(&key, &[body])
        .verify_slice(&raw)
        .map_err(|_| VerifyError::Bad)
}

/// One message from a person, in any channel.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Inbound {
    /// The provider's id for this message (or event): the dedupe key.
    pub provider_message_id: String,
    /// Where a reply goes: Slack channel and thread, a Chat thread, an email's message id...
    pub thread: String,
    pub sender: String,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Parsed {
    Message(Inbound),
    /// Slack's URL check when a webhook is set up: answer with the challenge.
    Challenge(String),
    /// Not a message from a person (bot echoes, edits, other events): accept and drop.
    Ignore,
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
#[error("unreadable payload: {0}")]
pub struct ParseError(pub String);

fn s(v: &Value, ptr: &str) -> String {
    v.pointer(ptr)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

/// Strip Teams' `<at>Bot</at>` mention markup and surrounding space.
fn strip_mentions(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut rest = text;
    while let Some(start) = rest.find("<at>") {
        out.push_str(&rest[..start]);
        match rest[start..].find("</at>") {
            Some(end) => rest = &rest[start + end + 5..],
            None => {
                rest = "";
                break;
            }
        }
    }
    out.push_str(rest);
    out.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub fn parse(kind: Kind, body: &[u8]) -> Result<Parsed, ParseError> {
    let v: Value = serde_json::from_slice(body).map_err(|e| ParseError(e.to_string()))?;
    let message = |id: String, thread: String, sender: String, text: String| {
        if id.is_empty() || text.trim().is_empty() {
            Ok(Parsed::Ignore)
        } else {
            Ok(Parsed::Message(Inbound {
                provider_message_id: id,
                thread,
                sender,
                text: text.trim().to_string(),
            }))
        }
    };
    match kind {
        Kind::Slack => match v.get("type").and_then(Value::as_str) {
            Some("url_verification") => Ok(Parsed::Challenge(s(&v, "/challenge"))),
            Some("event_callback") => {
                let ev = v.get("event").cloned().unwrap_or_default();
                let human = matches!(
                    ev.get("type").and_then(Value::as_str),
                    Some("message" | "app_mention")
                ) && ev.get("bot_id").is_none()
                    && ev.get("subtype").is_none();
                if !human {
                    return Ok(Parsed::Ignore);
                }
                let ts = s(&ev, "/thread_ts");
                let ts = if ts.is_empty() { s(&ev, "/ts") } else { ts };
                message(
                    s(&v, "/event_id"),
                    format!("{}:{}", s(&ev, "/channel"), ts),
                    s(&ev, "/user"),
                    s(&ev, "/text"),
                )
            }
            _ => Ok(Parsed::Ignore),
        },
        Kind::Teams => {
            if v.get("type").and_then(Value::as_str) != Some("message") {
                return Ok(Parsed::Ignore);
            }
            message(
                s(&v, "/id"),
                s(&v, "/conversation/id"),
                s(&v, "/from/name"),
                strip_mentions(&s(&v, "/text")),
            )
        }
        Kind::GoogleChat => {
            if v.get("type").and_then(Value::as_str) != Some("MESSAGE") {
                return Ok(Parsed::Ignore);
            }
            if s(&v, "/message/sender/type") == "BOT" {
                return Ok(Parsed::Ignore);
            }
            let text = s(&v, "/message/argumentText");
            let text = if text.trim().is_empty() {
                s(&v, "/message/text")
            } else {
                text
            };
            message(
                s(&v, "/message/name"),
                s(&v, "/message/thread/name"),
                s(&v, "/message/sender/email"),
                text,
            )
        }
        Kind::Email => {
            let id = s(&v, "/message_id");
            let thread = s(&v, "/in_reply_to");
            let subject = s(&v, "/subject");
            let body = s(&v, "/text");
            let text = if subject.is_empty() {
                body
            } else {
                format!("{subject}\n\n{body}")
            };
            let thread = if thread.is_empty() {
                id.clone()
            } else {
                thread
            };
            message(id, thread, s(&v, "/from"), text)
        }
        Kind::Webhook => {
            let thread = s(&v, "/thread");
            let id = s(&v, "/id");
            let thread = if thread.is_empty() {
                id.clone()
            } else {
                thread
            };
            message(id, thread, s(&v, "/from"), s(&v, "/text"))
        }
    }
}

/// Where and how replies to a binding are sent (its `outbound` settings).
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Outbound {
    /// The URL to post to: Slack's `chat.postMessage`, a Teams or Chat incoming webhook, the mail
    /// relay, or the customer's webhook.
    pub url: String,
    /// Slack bot token (`xoxb-...`). Other kinds leave it empty.
    #[serde(default)]
    pub token: String,
    /// Mail relay: the address replies come from.
    #[serde(default)]
    pub from: String,
}

/// One HTTP request that delivers a reply.
#[derive(Debug, Clone, PartialEq)]
pub struct Request {
    pub url: String,
    pub headers: Vec<(String, String)>,
    pub body: Value,
}

/// The reply to `thread` in this channel. `secret` signs requests to generic webhooks and the
/// mail relay, so the receiver can check they came from us.
pub fn reply(kind: Kind, out: &Outbound, secret: &str, thread: &str, text: &str) -> Request {
    let (body, mut headers) = match kind {
        Kind::Slack => {
            let (channel, ts) = thread.split_once(':').unwrap_or((thread, ""));
            (
                json!({"channel": channel, "thread_ts": ts, "text": text}),
                vec![("authorization".into(), format!("Bearer {}", out.token))],
            )
        }
        Kind::Teams => (json!({"text": text}), vec![]),
        Kind::GoogleChat => (json!({"text": text, "thread": {"name": thread}}), vec![]),
        Kind::Email => (
            json!({"from": out.from, "in_reply_to": thread, "text": text}),
            vec![],
        ),
        Kind::Webhook => (json!({"thread": thread, "text": text}), vec![]),
    };
    if matches!(kind, Kind::Email | Kind::Webhook) {
        let raw = serde_json::to_vec(&body).unwrap_or_default();
        headers.push(("x-wfk-signature".into(), sign_wfk(secret, &raw)));
    }
    headers.push(("content-type".into(), "application/json".into()));
    Request {
        url: out.url.clone(),
        headers,
        body,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn wfk_signatures_verify_and_reject_tampering() {
        let body = br#"{"id":"1","text":"hi"}"#;
        let sig = sign_wfk("s3cret", body);
        assert_eq!(verify_wfk("s3cret", Some(&sig), body), Ok(()));
        assert_eq!(
            verify_wfk("s3cret", Some(&sig), br#"{"id":"1","text":"HI"}"#),
            Err(VerifyError::Bad)
        );
        assert_eq!(verify_wfk("other", Some(&sig), body), Err(VerifyError::Bad));
        assert_eq!(verify_wfk("s3cret", None, body), Err(VerifyError::Missing));
        assert_eq!(
            verify_wfk("s3cret", Some("md5=abc"), body),
            Err(VerifyError::Bad)
        );
    }

    #[test]
    fn slack_signatures_check_time_too() {
        let body = b"{}";
        let now = 1_800_000_000;
        let sig = sign_slack("sec", now, body);
        let ts = now.to_string();
        assert_eq!(
            verify_slack("sec", Some(&ts), Some(&sig), body, now),
            Ok(())
        );
        assert_eq!(
            verify_slack("sec", Some(&ts), Some(&sig), body, now + 301),
            Err(VerifyError::Stale),
            "a replayed request is refused"
        );
        let old = sign_slack("sec", now - 400, body);
        let old_ts = (now - 400).to_string();
        assert_eq!(
            verify_slack("sec", Some(&old_ts), Some(&old), body, now),
            Err(VerifyError::Stale)
        );
        assert_eq!(
            verify_slack("sec", Some(&ts), Some(&sig), b"{ }", now),
            Err(VerifyError::Bad)
        );
    }

    #[test]
    fn teams_signatures_use_the_decoded_token() {
        let secret = B64.encode(b"teams-shared-secret");
        let body = br#"{"type":"message"}"#;
        let auth = sign_teams(&secret, body).unwrap();
        assert_eq!(verify_teams(&secret, Some(&auth), body), Ok(()));
        assert_eq!(
            verify_teams(&secret, Some("HMAC AAAA"), body),
            Err(VerifyError::Bad)
        );
        assert_eq!(
            verify_teams(&secret, Some("Bearer x"), body),
            Err(VerifyError::Bad)
        );
    }

    #[test]
    fn slack_messages_challenges_and_noise() {
        let challenge = br#"{"type":"url_verification","challenge":"abc"}"#;
        assert_eq!(
            parse(Kind::Slack, challenge),
            Ok(Parsed::Challenge("abc".into()))
        );
        let msg = br#"{"type":"event_callback","event_id":"Ev1","event":{"type":"app_mention",
            "user":"U1","text":" Summarise the Q3 report ","channel":"C9","ts":"171.2"}}"#;
        assert_eq!(
            parse(Kind::Slack, msg),
            Ok(Parsed::Message(Inbound {
                provider_message_id: "Ev1".into(),
                thread: "C9:171.2".into(),
                sender: "U1".into(),
                text: "Summarise the Q3 report".into(),
            }))
        );
        let in_thread = br#"{"type":"event_callback","event_id":"Ev2","event":{"type":"message",
            "user":"U1","text":"and Q4","channel":"C9","ts":"172.0","thread_ts":"171.2"}}"#;
        let Ok(Parsed::Message(m)) = parse(Kind::Slack, in_thread) else {
            panic!("a message")
        };
        assert_eq!(m.thread, "C9:171.2", "replies stay in the thread");
        let bot = br#"{"type":"event_callback","event_id":"Ev3","event":{"type":"message",
            "bot_id":"B1","text":"my own reply","channel":"C9","ts":"1"}}"#;
        assert_eq!(
            parse(Kind::Slack, bot),
            Ok(Parsed::Ignore),
            "never answer ourselves"
        );
    }

    #[test]
    fn teams_google_chat_email_and_webhook_messages() {
        let teams = br#"{"type":"message","id":"t1","text":"<at>Sam</at> book the room",
            "from":{"name":"Ana"},"conversation":{"id":"conv1"}}"#;
        let Ok(Parsed::Message(m)) = parse(Kind::Teams, teams) else {
            panic!()
        };
        assert_eq!(
            (m.text.as_str(), m.thread.as_str()),
            ("book the room", "conv1")
        );

        let chat = br#"{"type":"MESSAGE","message":{"name":"spaces/A/messages/B",
            "text":"@Sam draft it","argumentText":" draft it","sender":{"email":"ana@x.com","type":"HUMAN"},
            "thread":{"name":"spaces/A/threads/T"}}}"#;
        let Ok(Parsed::Message(m)) = parse(Kind::GoogleChat, chat) else {
            panic!()
        };
        assert_eq!(
            (m.text.as_str(), m.thread.as_str()),
            ("draft it", "spaces/A/threads/T")
        );
        let added = br#"{"type":"ADDED_TO_SPACE"}"#;
        assert_eq!(parse(Kind::GoogleChat, added), Ok(Parsed::Ignore));

        let email = br#"{"message_id":"<m1@x>","from":"ana@x.com","subject":"Invoice",
            "text":"Please send it.","in_reply_to":""}"#;
        let Ok(Parsed::Message(m)) = parse(Kind::Email, email) else {
            panic!()
        };
        assert_eq!(m.text, "Invoice\n\nPlease send it.");
        assert_eq!(m.thread, "<m1@x>", "a new email starts its own thread");

        let hook = br#"{"id":"h1","from":"crm","text":"New lead: Acme"}"#;
        let Ok(Parsed::Message(m)) = parse(Kind::Webhook, hook) else {
            panic!()
        };
        assert_eq!((m.thread.as_str(), m.sender.as_str()), ("h1", "crm"));
        assert_eq!(
            parse(Kind::Webhook, br#"{"id":"h2","text":"  "}"#),
            Ok(Parsed::Ignore)
        );
        assert!(parse(Kind::Webhook, b"not json").is_err());
    }

    #[test]
    fn replies_go_back_to_the_thread() {
        let out = Outbound {
            url: "https://slack.com/api/chat.postMessage".into(),
            token: "xoxb-1".into(),
            from: String::new(),
        };
        let r = reply(Kind::Slack, &out, "s", "C9:171.2", "Done.");
        assert_eq!(
            r.body,
            json!({"channel": "C9", "thread_ts": "171.2", "text": "Done."})
        );
        assert!(
            r.headers
                .contains(&("authorization".into(), "Bearer xoxb-1".into()))
        );

        let hook = Outbound {
            url: "https://crm.example/hook".into(),
            ..Outbound::default()
        };
        let r = reply(Kind::Webhook, &hook, "s3cret", "h1", "Done.");
        let raw = serde_json::to_vec(&r.body).unwrap();
        let sig = r
            .headers
            .iter()
            .find(|(k, _)| k == "x-wfk-signature")
            .map(|(_, v)| v.clone());
        assert_eq!(
            verify_wfk("s3cret", sig.as_deref(), &raw),
            Ok(()),
            "the receiver can check it came from us"
        );
    }
}
