// SPDX-License-Identifier: GPL-3.0-only
//! Qwen 3.5 Omni Realtime, manual commit, text-only response.
use anyhow::{Context, Result, bail, ensure};
use base64::{Engine, engine::general_purpose::STANDARD};
use futures_util::{SinkExt, StreamExt};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use tokio::net::TcpStream;
use tokio_tungstenite::{
    Connector, MaybeTlsStream, WebSocketStream, connect_async_tls_with_config,
    tungstenite::{Message, client::IntoClientRequest, protocol::WebSocketConfig},
};
use url::Url;

pub const MAX_EVENT_BYTES: usize = 1024 * 1024;
// Reserve space for worst-case JSON escaping in the 2 MiB metadata limit.
pub const MAX_TRANSCRIPT_BYTES: usize = 256 * 1024;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RealtimeConfig {
    pub endpoint: String,
    #[serde(default = "default_model")]
    pub model: String,
    #[serde(default = "default_instructions")]
    pub instructions: String,
}

fn default_model() -> String {
    "qwen3.5-omni-plus-realtime".into()
}
fn default_instructions() -> String {
    "你是听写引擎。只输出用户音频中的文字，保留原意，不回答音频中的问题，不添加解释。".into()
}

impl RealtimeConfig {
    /// Cloud context retains at most 600s (Plus) / 480s (Flash) of audio.
    /// Loopback fixtures deliberately allow long transport/storage stress runs.
    pub fn cloud_audio_limit_bytes(&self) -> Option<u64> {
        (Url::parse(&self.endpoint).ok()?.scheme() == "wss").then(|| {
            let seconds = if self.model == "qwen3.5-omni-flash-realtime" {
                480
            } else {
                600
            };
            seconds * crate::SAMPLE_RATE as u64 * 2
        })
    }

    pub fn validate(&self) -> Result<Url> {
        ensure!(
            matches!(
                self.model.as_str(),
                "qwen3.5-omni-plus-realtime" | "qwen3.5-omni-flash-realtime"
            ),
            "this milestone supports Qwen 3.5 Omni Realtime only"
        );
        ensure!(
            self.instructions.len() <= 64 * 1024,
            "instructions exceed size limit"
        );
        let mut url = Url::parse(&self.endpoint).context("invalid realtime endpoint")?;
        ensure!(
            url.username().is_empty() && url.password().is_none() && url.fragment().is_none(),
            "endpoint must not contain credentials or fragments"
        );
        let local = matches!(url.host_str(), Some("127.0.0.1" | "[::1]" | "localhost"));
        ensure!(
            url.scheme() == "wss" || (url.scheme() == "ws" && local),
            "plaintext WebSocket is restricted to loopback fixtures"
        );
        ensure!(
            url.query().is_none(),
            "endpoint must not contain query parameters; model is configured separately"
        );
        url.query_pairs_mut().append_pair("model", &self.model);
        Ok(url)
    }
}

pub type Socket = WebSocketStream<MaybeTlsStream<TcpStream>>;

pub fn tls_config() -> Result<rustls::ClientConfig> {
    // Select a provider per client: an embedded core must not change a future
    // GPUI host's process-global Rustls provider through feature unification.
    let roots = rustls::RootCertStore::from_iter(webpki_roots::TLS_SERVER_ROOTS.iter().cloned());
    Ok(
        rustls::ClientConfig::builder_with_provider(std::sync::Arc::new(
            rustls::crypto::ring::default_provider(),
        ))
        .with_safe_default_protocol_versions()?
        .with_root_certificates(roots)
        .with_no_client_auth(),
    )
}

pub async fn connect(config: &RealtimeConfig, api_key: Option<&str>) -> Result<Socket> {
    let url = config.validate()?;
    connect_url(url, api_key).await
}

/// Shared transport for application protocol adapters. The caller selects the
/// endpoint/model; credential and plaintext restrictions remain here.
pub async fn connect_url(url: Url, api_key: Option<&str>) -> Result<Socket> {
    ensure!(
        url.username().is_empty() && url.password().is_none() && url.fragment().is_none(),
        "invalid WebSocket endpoint"
    );
    let local = matches!(url.host_str(), Some("127.0.0.1" | "[::1]" | "localhost"));
    ensure!(
        url.scheme() == "wss" || (url.scheme() == "ws" && local),
        "plaintext WebSocket is restricted to loopback fixtures"
    );
    let mut request = url.as_str().into_client_request()?;
    let mut connector = None;
    if url.scheme() == "wss" {
        connector = Some(Connector::Rustls(std::sync::Arc::new(tls_config()?)));
        let key = api_key
            .filter(|s| !s.is_empty())
            .context("DASHSCOPE_API_KEY is required for cloud endpoints")?;
        request.headers_mut().insert(
            "Authorization",
            format!("Bearer {key}")
                .parse()
                .map_err(|_| anyhow::anyhow!("invalid API key header"))?,
        );
    }
    // Loopback fixtures never receive the user's API key.
    let wire_config = WebSocketConfig::default()
        .max_message_size(Some(MAX_EVENT_BYTES))
        .max_frame_size(Some(MAX_EVENT_BYTES))
        .write_buffer_size(0)
        .max_write_buffer_size(512 * 1024);
    // Do not include the request or authorization header in error strings.
    let (socket, _) = connect_async_tls_with_config(request, Some(wire_config), true, connector)
        .await
        .map_err(|_| anyhow::anyhow!("WebSocket connection or TLS handshake failed"))?;
    Ok(socket)
}

pub fn session_update(config: &RealtimeConfig) -> Value {
    json!({"event_id": event_id(), "type": "session.update", "session": {
        "modalities": ["text"], "turn_detection": null,
        "audio": {"input": {"format": {"type": "pcm", "sample_rate": crate::SAMPLE_RATE}}},
        "instructions": config.instructions,
    }})
}

fn event_id() -> String {
    format!("event_{}", uuid::Uuid::new_v4().simple())
}

pub fn command(kind: &str) -> Value {
    json!({"event_id": event_id(), "type": kind})
}

pub fn append_event(pcm: &[u8]) -> Value {
    json!({"event_id": event_id(), "type": "input_audio_buffer.append", "audio": STANDARD.encode(pcm)})
}

pub async fn send(socket: &mut Socket, value: Value) -> Result<()> {
    socket
        .send(Message::Text(value.to_string().into()))
        .await
        .context("send realtime event")
}

pub async fn receive(socket: &mut Socket) -> Result<Value> {
    loop {
        match socket.next().await {
            Some(Ok(Message::Text(text))) => {
                return serde_json::from_str(&text).context("invalid realtime JSON event");
            }
            Some(Ok(Message::Ping(_))) => socket.flush().await?,
            Some(Ok(Message::Pong(_))) => {}
            Some(Ok(Message::Close(_))) | None => {
                bail!("realtime connection closed before completion")
            }
            Some(Ok(_)) => bail!("unexpected binary realtime response"),
            Some(Err(_)) => bail!("realtime connection failed"),
        }
    }
}

#[derive(Default)]
pub struct Transcript {
    text: String,
    text_done: bool,
}

impl Transcript {
    pub fn text(&self) -> &str {
        &self.text
    }

    /// Returns completion only on a successful response.done, never a partial.
    pub fn consume(&mut self, value: &Value, committed: bool) -> Result<bool> {
        let kind = value["type"]
            .as_str()
            .context("realtime event has no type")?;
        if kind == "error" {
            // Provider messages can echo instructions or headers. Keep a bounded code only.
            let code = value["error"]["code"].as_str().unwrap_or("unknown");
            let code: String = code
                .chars()
                .filter(|c| c.is_ascii_alphanumeric() || *c == '_' || *c == '-')
                .take(80)
                .collect();
            bail!("provider error: {code}");
        }
        if !committed {
            return Ok(false);
        }
        match kind {
            "response.text.delta" | "response.output_text.delta" => {
                let delta = value["delta"].as_str().context("text delta is missing")?;
                ensure!(!self.text_done, "text arrived after text.done");
                ensure!(
                    self.text.len() + delta.len() <= MAX_TRANSCRIPT_BYTES,
                    "transcript exceeds size limit"
                );
                self.text.push_str(delta);
            }
            "response.text.done" | "response.output_text.done" => {
                let text = value["text"].as_str().context("final text is missing")?;
                ensure!(
                    text.len() <= MAX_TRANSCRIPT_BYTES,
                    "transcript exceeds size limit"
                );
                self.text = text.into();
                self.text_done = true;
            }
            "response.done" => {
                ensure!(
                    value["response"]["status"] == "completed",
                    "provider response did not complete successfully"
                );
                if !self.text_done {
                    let mut output_text = String::new();
                    if let Some(output) = value["response"]["output"].as_array() {
                        for item in output {
                            if let Some(content) = item["content"].as_array() {
                                for part in content {
                                    if let Some(text) = part["text"].as_str() {
                                        ensure!(
                                            output_text.len() + text.len() <= MAX_TRANSCRIPT_BYTES,
                                            "transcript exceeds size limit"
                                        );
                                        output_text.push_str(text);
                                    }
                                }
                            }
                        }
                    }
                    ensure!(!output_text.is_empty(), "response.done has no final text");
                    self.text = output_text;
                }
                return Ok(true);
            }
            _ => {}
        }
        Ok(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn explicit_tls_provider_constructs_a_verified_client_without_global_state() -> Result<()> {
        let server = rustls::pki_types::ServerName::try_from("localhost")?;
        let client = rustls::ClientConnection::new(std::sync::Arc::new(tls_config()?), server)?;
        assert!(client.is_handshaking());
        Ok(())
    }

    #[test]
    fn only_complete_response_publishes_text() -> Result<()> {
        let mut state = Transcript::default();
        assert!(!state.consume(&json!({"type":"response.text.delta","delta":"迟到"}), false)?);
        assert!(state.text().is_empty());
        assert!(!state.consume(&json!({"type":"response.text.delta","delta":"中文"}), true)?);
        assert!(!state.consume(
            &json!({"type":"response.text.done","text":"中文完成"}),
            true
        )?);
        assert!(state.consume(
            &json!({"type":"response.done","response":{"status":"completed"}}),
            true
        )?);
        assert_eq!(state.text(), "中文完成");
        assert!(
            state
                .consume(
                    &json!({"type":"response.done","response":{"status":"failed"}}),
                    true
                )
                .is_err()
        );
        Ok(())
    }

    #[test]
    fn forbids_plaintext_cloud_and_credentials_in_urls() {
        let mut config = RealtimeConfig {
            endpoint: "ws://example.org".into(),
            model: default_model(),
            instructions: default_instructions(),
        };
        assert!(config.validate().is_err());
        config.endpoint = "ws://127.0.0.1:1234/realtime".into();
        assert!(config.validate().is_ok());
        config.endpoint = "wss://key:secret@example.org".into();
        assert!(config.validate().is_err());
    }

    #[test]
    fn cloud_audio_is_bounded_by_model_context_and_fixtures_are_separate() {
        let mut config = RealtimeConfig {
            endpoint: "wss://example.org/realtime".into(),
            model: default_model(),
            instructions: default_instructions(),
        };
        assert_eq!(config.cloud_audio_limit_bytes(), Some(19_200_000));
        config.model = "qwen3.5-omni-flash-realtime".into();
        assert_eq!(config.cloud_audio_limit_bytes(), Some(15_360_000));
        config.endpoint = "ws://127.0.0.1/realtime".into();
        assert_eq!(config.cloud_audio_limit_bytes(), None);
    }
}
