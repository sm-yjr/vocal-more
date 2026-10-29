// SPDX-License-Identifier: GPL-3.0-only
//! Opt-in AT-SPI observation. Text reads fail closed for secure/unknown targets.
use anyhow::{Context, Result, ensure};
use atspi_proxies::{
    accessible::AccessibleProxy,
    common::{ObjectRefOwned, Role, State, events::object::StateChangedEvent},
    text::TextProxy,
};
use futures_lite::StreamExt;
use serde_json::{Value, json};
use std::{
    collections::{HashMap, VecDeque},
    sync::{
        Arc, RwLock,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

pub(super) const OUTPUT_TEXT_LIMIT: usize = 256 * 1024;
const OBSERVATION_TEXT_LIMIT: usize = 8000;

pub const CAPTURE_BUDGET: Duration = Duration::from_millis(150);
enum Job {
    Capture { ticket: u64, expires: Instant },
    Retain { id: String, target: String },
    Poll(String),
    End(String),
    Configure(bool),
}
pub enum Event {
    Captured {
        ticket: u64,
        value: Value,
    },
    Polled {
        id: String,
        focused: Value,
        retained: Value,
    },
}
pub(super) struct Shared {
    text_limit: usize,
    allow_terminal: bool,
    excluded: RwLock<Vec<String>>,
    ready: AtomicBool,
    enabled: AtomicBool,
    closed: AtomicBool,
    finished: AtomicBool,
}
impl Shared {
    pub(super) fn output() -> Self {
        Self {
            text_limit: OUTPUT_TEXT_LIMIT,
            allow_terminal: true,
            excluded: RwLock::new(Vec::new()),
            ready: AtomicBool::new(false),
            enabled: AtomicBool::new(true),
            closed: AtomicBool::new(false),
            finished: AtomicBool::new(false),
        }
    }
    pub(super) fn exclude(&self, excluded: Vec<String>) {
        *self.excluded.write().unwrap() = excluded;
    }
    pub(super) fn stop(&self) {
        self.closed.store(true, Ordering::Release);
    }
}
pub struct AccessibilityWorker {
    sender: async_channel::Sender<Job>,
    events: async_channel::Receiver<Event>,
    state: Arc<Shared>,
}
impl AccessibilityWorker {
    pub fn new() -> Result<Self> {
        let (sender, receiver) = async_channel::bounded(8);
        let (output, events) = async_channel::bounded(8);
        let state = Arc::new(Shared {
            text_limit: OBSERVATION_TEXT_LIMIT,
            allow_terminal: false,
            excluded: RwLock::new(Vec::new()),
            ready: AtomicBool::new(false),
            enabled: AtomicBool::new(false),
            closed: AtomicBool::new(false),
            finished: AtomicBool::new(false),
        });
        let shared = state.clone();
        std::thread::Builder::new()
            .name("vocal-more-linux-accessibility".into())
            .spawn(move || {
                if let Ok(runtime) = tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                {
                    runtime.block_on(async {
                        while let Ok(job) = receiver.recv().await {
                            if shared.closed.load(Ordering::Acquire) {
                                break;
                            }
                            if matches!(job, Job::Configure(true)) {
                                let result = serve(&receiver, &output, &shared).await;
                                #[cfg(test)]
                                if let Err(error) = &result {
                                    eprintln!("AT-SPI fixture connection: {error}");
                                }
                                let _ = result;
                                shared.ready.store(false, Ordering::Release);
                            } else if let Job::Capture { ticket, .. } = job {
                                let _ = output.try_send(Event::Captured {
                                    ticket,
                                    value: Value::Null,
                                });
                            }
                        }
                    });
                }
                shared.finished.store(true, Ordering::Release);
            })?;
        Ok(Self {
            sender,
            events,
            state,
        })
    }
    pub fn configure(&self, enabled: bool, excluded: Vec<String>) {
        *self.state.excluded.write().unwrap() = excluded;
        let old = self.state.enabled.swap(enabled, Ordering::AcqRel);
        if old != enabled || (enabled && !self.state.ready.load(Ordering::Acquire)) {
            self.state.ready.store(enabled, Ordering::Release);
            if self.sender.try_send(Job::Configure(enabled)).is_err() {
                self.state.ready.store(false, Ordering::Release);
            }
        }
    }

    pub fn capture(&self, ticket: u64) -> Result<()> {
        if self.state.enabled.load(Ordering::Acquire) && !self.state.ready.load(Ordering::Acquire) {
            let excluded = self.state.excluded.read().unwrap().clone();
            self.configure(true, excluded);
        }
        self.sender
            .try_send(Job::Capture {
                ticket,
                expires: Instant::now() + CAPTURE_BUDGET,
            })
            .context("accessibility queue is busy")
    }
    pub fn retain(&self, id: &str, snapshot: &Value) {
        let _ = self.sender.try_send(Job::Retain {
            id: id.into(),
            target: snapshot["target_id"].as_str().unwrap_or("").into(),
        });
    }
    pub fn observe(&self, id: &str) {
        let _ = self.sender.try_send(Job::Poll(id.into()));
    }
    pub fn end(&self, id: &str) {
        let _ = self.sender.try_send(Job::End(id.into()));
    }
    pub fn poll(&self) -> Option<Event> {
        self.events.try_recv().ok()
    }
    pub fn close(&self) {
        self.state.enabled.store(false, Ordering::Release);
        self.state.closed.store(true, Ordering::Release);
        self.sender.close();
    }
    pub fn finished(&self) -> bool {
        self.state.finished.load(Ordering::Acquire)
    }
}
impl Drop for AccessibilityWorker {
    fn drop(&mut self) {
        self.close();
    }
}

async fn serve(
    jobs: &async_channel::Receiver<Job>,
    output: &async_channel::Sender<Event>,
    state: &Shared,
) -> Result<()> {
    let session =
        tokio::time::timeout(Duration::from_secs(1), zbus::Connection::session()).await??;
    let address = tokio::time::timeout(Duration::from_secs(1), async {
        atspi_proxies::bus::BusProxy::new(&session)
            .await?
            .get_address()
            .await
    })
    .await??;
    let connection = tokio::time::timeout(
        Duration::from_secs(1),
        zbus::connection::Builder::address(address.as_str())?.build(),
    )
    .await??;
    let registry = atspi_proxies::registry::RegistryProxy::new(&connection).await?;
    let rule = zbus::MatchRule::builder()
        .msg_type(zbus::message::Type::Signal)
        .interface("org.a11y.atspi.Event.Object")?
        .member("StateChanged")?
        .arg(0, "focused")?
        .build();
    let mut signals = zbus::MessageStream::for_match_rule(rule, &connection, Some(64)).await?;
    tokio::time::timeout(
        Duration::from_secs(1),
        registry.register_event("object:state-changed:focused"),
    )
    .await??;
    let mut focused: Option<ObjectRefOwned> = None;
    let mut captures: HashMap<String, ObjectRefOwned> = HashMap::new();
    let mut retained: Option<(String, ObjectRefOwned)> = None;
    loop {
        if state.closed.load(Ordering::Acquire) || !state.enabled.load(Ordering::Acquire) {
            break;
        }
        tokio::select! {
            biased;
            job = jobs.recv() => {
                let Ok(job) = job else {break;};
                match job {
                    Job::Configure(false) => break,
                    Job::Configure(true) => {},
                    Job::Capture {ticket,expires} => {
                        let budget = expires.saturating_duration_since(Instant::now());
                        let result = tokio::time::timeout(budget,async {
                            let target = match focused.clone() {
                                Some(target) => target,
                                None => find_focused(&connection).await?,
                            };
                            let value = read(&connection,&target,true,state).await?;
                            Ok::<_,anyhow::Error>((target,value))
                        }).await;
                        let value = match result {
                            Ok(Ok((target,value))) if state.enabled.load(Ordering::Acquire) && Instant::now() < expires => {
                                if captures.len() >= 8 {captures.clear();}
                                focused = Some(target.clone());
                                captures.insert(value["target_id"].as_str().unwrap_or("").into(),target);
                                value
                            },
                            failure => {
                                #[cfg(test)] match failure {Ok(Err(error))=>eprintln!("AT-SPI fixture capture: {error}"),Err(_)=>eprintln!("AT-SPI fixture capture timed out"),_=>{}}
                                #[cfg(not(test))] let _=failure;
                                Value::Null
                            },
                        };
                        let _ = output.try_send(Event::Captured {ticket,value});
                    },
                    Job::Retain {id,target} => {
                        let object=captures.remove(&target).or_else(|| {
                            let (owner,path)=target.split_once(":/")?;
                            let owner=zbus::names::OwnedUniqueName::try_from(owner.to_owned()).ok()?;
                            let path=zbus::zvariant::OwnedObjectPath::try_from(format!("/{path}")).ok()?;
                            Some(atspi_proxies::common::ObjectRef::new_owned(owner,path))
                        });
                        retained=object.map(|object|(id,object));
                    },
                    Job::Poll(id) => {
                        let result = tokio::time::timeout(CAPTURE_BUDGET,async {
                            // Poll only the retained paste target. Reading the newly
                            // focused field would collect unrelated text unnecessarily.
                            let Some((owner,target)) = &retained else {return (Value::Null,Value::Null);};
                            if owner != &id {return (Value::Null,Value::Null);}
                            if let Ok(current) = read(&connection,target,true,state).await {
                                (current,Value::Null)
                            } else {
                                (Value::Null,read(&connection,target,false,state).await.unwrap_or(Value::Null))
                            }
                        }).await.unwrap_or((Value::Null,Value::Null));
                        let _ = output.try_send(Event::Polled {id,focused:result.0,retained:result.1});
                    },
                    Job::End(id) => {if retained.as_ref().is_some_and(|(owner,_)|owner==&id) {retained=None;}},
                }
            },
            message = signals.next() => {
                let Some(Ok(message)) = message else {break;};
                if let Ok(event) = StateChangedEvent::try_from(&message) && event.state == State::Focused {
                    if event.enabled { focused = Some(event.item); }
                    else if focused.as_ref() == Some(&event.item) { focused=None; }
                }
            },
        }
    }
    let _ = tokio::time::timeout(
        Duration::from_millis(100),
        registry.deregister_event("object:state-changed:focused"),
    )
    .await;
    Ok(())
}
pub(super) async fn accessible<'a>(
    bus: &'a zbus::Connection,
    target: &'a ObjectRefOwned,
) -> Result<AccessibleProxy<'a>> {
    Ok(AccessibleProxy::builder(bus)
        .destination(
            target
                .name_as_str()
                .context("missing accessibility owner")?,
        )?
        .path(target.path())?
        .cache_properties(zbus::proxy::CacheProperties::No)
        .build()
        .await?)
}
pub(super) async fn find_focused(bus: &zbus::Connection) -> Result<ObjectRefOwned> {
    find_focused_impl(bus, false).await
}
pub(super) async fn find_focused_output(bus: &zbus::Connection) -> Result<ObjectRefOwned> {
    find_focused_impl(bus, true).await
}
async fn find_focused_impl(bus: &zbus::Connection, output: bool) -> Result<ObjectRefOwned> {
    let root = AccessibleProxy::builder(bus)
        .destination("org.a11y.atspi.Registry")?
        .path("/org/a11y/atspi/accessible/root")?
        .cache_properties(zbus::proxy::CacheProperties::No)
        .build()
        .await?;
    // Prefer the active top-level window. A broad BFS through inactive browser
    // trees can spend the whole admission budget before reaching a small editor.
    // Broken/stale providers must not hide healthy applications later in the list.
    let mut queue = VecDeque::new();
    for app in root.get_children().await?.into_iter().take(64) {
        let windows = tokio::time::timeout(Duration::from_millis(20), async {
            accessible(bus, &app)
                .await?
                .get_children()
                .await
                .map_err(anyhow::Error::from)
        })
        .await;
        if let Ok(Ok(windows)) = windows {
            for window in windows.into_iter().take(32) {
                if let Ok(Ok(states)) = tokio::time::timeout(Duration::from_millis(20), async {
                    accessible(bus, &window)
                        .await?
                        .get_state()
                        .await
                        .map_err(anyhow::Error::from)
                })
                .await
                    && states.contains(State::Active)
                {
                    queue.push_back(window);
                }
            }
        }
    }
    // Query the toolkit's collection in process; a browser conversation may
    // expose thousands of history nodes before its deeply nested composer.
    for window in &queue {
        let matches = tokio::time::timeout(Duration::from_millis(100), async {
            let collection = atspi_proxies::collection::CollectionProxy::builder(bus)
                .destination(window.name_as_str().context("missing owner")?)?
                .path(window.path())?
                .build()
                .await?;
            let rule = atspi_proxies::common::ObjectMatchRule::builder()
                .states(
                    if output {
                        vec![State::Focused]
                    } else {
                        vec![State::Focused, State::Editable]
                    },
                    atspi_proxies::common::MatchType::All,
                )
                .attributes(HashMap::new(), atspi_proxies::common::MatchType::All)
                .roles(&[], atspi_proxies::common::MatchType::All)
                .interfaces(
                    std::iter::empty::<atspi_proxies::common::Interface>(),
                    atspi_proxies::common::MatchType::All,
                )
                .build();
            collection
                .get_matches(rule, atspi_proxies::common::SortOrder::Canonical, 1, true)
                .await
                .map_err(anyhow::Error::from)
        })
        .await;
        if let Ok(Ok(matches)) = matches {
            for target in matches {
                if let Ok(proxy) = accessible(bus, &target).await
                    && let Ok(states) = proxy.get_state().await
                    && states.contains(State::Focused)
                    && (output || states.contains(State::Editable))
                {
                    return Ok(target);
                }
            }
        }
    }
    for _ in 0..512 {
        let Some(target) = queue.pop_front() else {
            break;
        };
        let Ok(proxy) = accessible(bus, &target).await else {
            continue;
        };
        let Ok(states) = proxy.get_state().await else {
            continue;
        };
        if states.contains(State::Focused) && (output || states.contains(State::Editable)) {
            return Ok(target);
        }
        if queue.len() < 512
            && let Ok(children) = proxy.get_children().await
        {
            queue.extend(children.into_iter().take(512 - queue.len()));
        }
    }
    anyhow::bail!("focused accessible field is unavailable")
}
fn editable(
    role: Role,
    states: atspi_proxies::common::StateSet,
    attributes: &HashMap<String, String>,
    focused: bool,
) -> bool {
    matches!(
        role,
        Role::Entry | Role::Text | Role::Paragraph | Role::DocumentText | Role::ComboBox
    ) && states.contains(State::Editable)
        && (states.contains(State::Enabled) || states.contains(State::Sensitive))
        && states.contains(State::Showing)
        && (!focused || states.contains(State::Focused))
        && !attributes
            .iter()
            .any(|(key, value)| format!("{key}={value}").to_lowercase().contains("password"))
}
async fn public_ancestors(bus: &zbus::Connection, target: &ObjectRefOwned) -> Result<()> {
    let mut item = target.clone();
    for _ in 0..32 {
        let proxy = accessible(bus, &item).await?;
        let role = proxy.get_role().await?;
        let attrs = proxy.get_attributes().await?;
        ensure!(
            role != Role::PasswordText
                && !attrs.iter().any(|(key, value)| format!("{key}={value}")
                    .to_lowercase()
                    .contains("password")),
            "secure ancestor"
        );
        if role == Role::Application {
            return Ok(());
        }
        let parent = proxy.parent().await?;
        ensure!(
            parent.path_as_str() != "/org/a11y/atspi/null" && parent != item,
            "unverified field ancestry"
        );
        item = parent;
    }
    anyhow::bail!("field ancestry exceeds privacy limit")
}
pub(super) async fn read(
    bus: &zbus::Connection,
    target: &ObjectRefOwned,
    focused: bool,
    state: &Shared,
) -> Result<Value> {
    let proxy = accessible(bus, target).await?;
    let role = proxy.get_role().await?;
    let states = proxy.get_state().await?;
    let attrs = proxy.get_attributes().await?;
    #[cfg(test)]
    if !editable(role, states, &attrs, focused) {
        eprintln!("AT-SPI fixture metadata: role={role:?} states={states:?} focused={focused}");
    }
    if focused
        && !states.contains(State::Focused)
        && std::env::var_os("VOCAL_MORE_TRACE_UI").is_some()
    {
        eprintln!("input lost-focus role={role:?} states={states:?}");
    }
    ensure!(
        !focused || states.contains(State::Focused),
        "原输入位置已失去键盘焦点，已停止自动插入"
    );
    let terminal_candidate = state.allow_terminal
        && role == Role::Panel
        && states.contains(State::Focusable)
        && states.contains(State::Focused)
        && states.contains(State::Showing)
        && states.contains(State::Sensitive);
    ensure!(
        editable(role, states, &attrs, focused) || terminal_candidate,
        "field is secure or not editable"
    );
    let owner = target.name_as_str().unwrap();
    let dbus = zbus::fdo::DBusProxy::new(bus).await?;
    let pid = dbus
        .get_connection_unix_process_id(owner.try_into()?)
        .await?;
    let app = attrs
        .get("desktop-entry")
        .or_else(|| attrs.get("app-id"))
        .cloned()
        .unwrap_or_else(|| {
            std::fs::read_link(format!("/proc/{pid}/exe"))
                .ok()
                .and_then(|p| p.file_name().map(|s| s.to_string_lossy().into_owned()))
                .unwrap_or_default()
        });
    ensure!(
        pid != std::process::id(),
        "do not learn from application settings"
    );
    ensure!(
        state.enabled.load(Ordering::Acquire) && !state.closed.load(Ordering::Acquire),
        "learning is disabled"
    );
    ensure!(
        !app.is_empty() && !state.excluded.read().unwrap().contains(&app),
        "excluded application"
    );
    public_ancestors(bus, target).await?;
    if terminal_candidate {
        let executable = std::fs::read_link(format!("/proc/{pid}/exe"))?;
        ensure!(
            executable.file_name().is_some_and(|name| name == "ghostty"),
            "unsupported terminal surface"
        );
        ensure!(
            !proxy
                .get_interfaces()
                .await?
                .contains(atspi_proxies::common::Interface::Text),
            "terminal surface has text interface"
        );
        return Ok(
            json!({"pid":pid,"target_id":format!("{owner}:{}",target.path_as_str()),
            "role":"Terminal","terminal":true,"is_secure":false,"value":null,
            "app_bundle_id":app,"app_name":app}),
        );
    }
    let text = TextProxy::builder(bus)
        .destination(target.name_as_str().unwrap())?
        .path(target.path())?
        .cache_properties(zbus::proxy::CacheProperties::No)
        .build()
        .await?;
    let count = text.character_count().await?;
    ensure!(
        count >= 0 && count as usize <= state.text_limit,
        "field exceeds observation limit"
    );
    ensure!(
        state.enabled.load(Ordering::Acquire)
            && !state.closed.load(Ordering::Acquire)
            && !state.excluded.read().unwrap().contains(&app),
        "learning disabled or excluded"
    );
    let value = text.get_text(0, count).await?;
    // Some GTK versions expose visibility=false GtkEntry as generic Text, with
    // no secure metadata. GTK returns display glyphs, never the secret. Do not
    // admit that masked display value into the observation/evidence pipeline.
    ensure!(
        !value
            .chars()
            .any(|c| matches!(c, '•' | '●' | '▪' | '◦' | '∙'))
            && (value.is_empty() || !value.chars().all(|c| c == '*')),
        "masked field"
    );
    ensure!(
        value.chars().count() == count as usize && text.character_count().await? == count,
        "field changed during capture"
    );
    ensure!(
        editable(
            proxy.get_role().await?,
            proxy.get_state().await?,
            &proxy.get_attributes().await?,
            focused
        ),
        "field state changed"
    );
    public_ancestors(bus, target).await?;
    let (start, end) = if text
        .inner()
        .call::<_, _, i32>("GetNSelections", &())
        .await?
        > 0
    {
        text.get_selection(0).await?
    } else {
        let caret = text.caret_offset().await?;
        (caret, caret)
    };
    ensure!(
        start >= 0 && end >= start && end <= count,
        "invalid text selection"
    );
    Ok(
        json!({"pid":pid,"target_id":format!("{owner}:{}",target.path_as_str()),"role":format!("{role:?}"),
        "is_secure":false,"value":value,"selection_start":start,"selection_length":end-start,"app_bundle_id":app,"app_name":app}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn password_and_unknown_fields_are_rejected_before_any_text_read() {
        let states = atspi_proxies::common::StateSet::new(
            State::Editable | State::Enabled | State::Showing | State::Focused,
        );
        let mut attrs = HashMap::new();
        assert!(editable(Role::Entry, states, &attrs, true));
        assert!(!editable(Role::PasswordText, states, &attrs, true));
        assert!(!editable(Role::Unknown, states, &attrs, true));
        attrs.insert("text-input-type".into(), "password".into());
        assert!(!editable(Role::Entry, states, &attrs, true));
    }
    /// Run only with scripts/smoke_linux_accessibility.py on a private desktop.
    #[test]
    #[ignore = "requires the isolated GTK/AT-SPI acceptance fixture"]
    fn linux_atspi_real_field_observation() {
        let path = std::path::PathBuf::from(
            std::env::var_os("VOCAL_MORE_ATSPI_TEST_CONTROL").expect("use the isolated runner"),
        );
        fn capture(worker: &AccessibilityWorker, ticket: u64) -> Value {
            worker.capture(ticket).unwrap();
            let deadline = Instant::now() + Duration::from_secs(1);
            while Instant::now() < deadline {
                if let Some(Event::Captured { ticket: id, value }) = worker.poll()
                    && id == ticket
                {
                    return value;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            panic!("AT-SPI capture did not complete");
        }
        let mut acceptance_report = Value::Null;
        let worker = AccessibilityWorker::new().unwrap();
        worker.configure(true, vec![]);
        let mut original = Value::Null;
        for ticket in 1..30 {
            std::thread::sleep(Duration::from_millis(100));
            original = capture(&worker, ticket);
            if !original.is_null() {
                break;
            }
        }
        use vocal_more_backend::{
            dictionary::Dictionary,
            learning::{self, Decision},
            learning_store::LearningStore,
            observation::{Observation, Progress},
        };
        fn keyboard(arguments: &[&str]) {
            if let Some(path) = std::env::var_os("VOCAL_MORE_ATSPI_TEST_KEYS") {
                let path = std::path::PathBuf::from(path);
                std::fs::write(&path, serde_json::to_vec(arguments).unwrap()).unwrap();
                let deadline = Instant::now() + Duration::from_secs(3);
                while path.exists() {
                    assert!(
                        Instant::now() < deadline,
                        "Wayland keyboard fixture timed out"
                    );
                    std::thread::sleep(Duration::from_millis(20));
                }
                std::thread::sleep(Duration::from_millis(150));
                return;
            }
            assert!(
                std::process::Command::new("xdotool")
                    .args(arguments)
                    .status()
                    .unwrap()
                    .success()
            );
            std::thread::sleep(Duration::from_millis(150));
        }
        fn observe(worker: &AccessibilityWorker) -> (Value, Value) {
            worker.observe("fixture");
            let deadline = Instant::now() + Duration::from_secs(1);
            while Instant::now() < deadline {
                if let Some(Event::Polled {
                    focused, retained, ..
                }) = worker.poll()
                {
                    return (focused, retained);
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            panic!("observation did not complete");
        }
        assert_eq!(
            original["value"], "前缀：旧值后缀",
            "GTK field was not captured"
        );
        assert_eq!(original["selection_start"], 3);
        assert_eq!(original["selection_length"], 2);
        use super::super::platform::{DeliveryJob, DesktopWorker, Session};
        use crate::bridge::BackendDriver;
        let profile = tempfile::tempdir().unwrap();
        let (driver, sink, _events) = BackendDriver::start(
            vocal_more_backend::application::Options::new(profile.path().into()),
        )
        .unwrap();
        let session = Session::current();
        let mut output = DesktopWorker::new(sink.clone(), session).unwrap();
        // Exercise both X11 automatic and Wayland-style manual observation with
        // real clipboard/keyboard input. No ASR or classifier request is made.
        for manual in [false, true] {
            if manual {
                std::fs::write(&path, "reset").unwrap();
                std::thread::sleep(Duration::from_millis(250));
                original = capture(&worker, 50);
                assert_eq!(original["selection_length"], 2);
            }
            worker.retain("fixture", &original);
            let paste =
                json!({"text":"github", "raw_text":"github", "recording_id":"local-fixture"});
            let mut observation = if manual {
                Observation::prepare_manual("fixture".into(), original.clone(), &paste, 0.)
            } else {
                Observation::prepare("fixture".into(), original.clone(), &paste, 0.)
            }
            .unwrap();
            let (focused, retained) = observe(&worker);
            assert!(matches!(
                observation.poll(&focused, &retained, 0.1, false),
                Progress::Pending
            ));
            let automatic = !manual && session == Session::X11;
            output
                .deliver(DeliveryJob {
                    text: "github".into(),
                    epoch: sink.paste_epoch(),
                    generation: sink.generation(),
                    auto_paste: automatic,
                    restore: false,
                })
                .unwrap();
            let end = Instant::now() + Duration::from_secs(5);
            loop {
                if let Some(result) = output.completion() {
                    assert_eq!(result["ok"], true, "clipboard delivery: {result}");
                    break;
                }
                assert!(Instant::now() < end, "clipboard delivery timed out");
                std::thread::sleep(Duration::from_millis(20));
            }
            if !automatic {
                keyboard(&["key", "--clearmodifiers", "ctrl+v"]);
            }
            std::thread::sleep(Duration::from_millis(150));
            let (focused, retained) = observe(&worker);
            assert_eq!(focused["value"], "前缀：github后缀");
            assert!(matches!(
                observation.poll(&focused, &retained, 0.5, false),
                Progress::Pending
            ));
            keyboard(&[
                "key",
                "--clearmodifiers",
                "Home",
                "Right",
                "Right",
                "Right",
                "shift+Right",
                "shift+Right",
                "shift+Right",
                "shift+Right",
                "shift+Right",
                "shift+Right",
            ]);
            keyboard(&["type", "--clearmodifiers", "GitHub"]);
            let (focused, retained) = observe(&worker);
            assert_eq!(focused["value"], "前缀：GitHub后缀");
            assert!(matches!(
                observation.poll(&focused, &retained, 1., false),
                Progress::Pending
            ));
            std::fs::write(&path, "other").unwrap();
            std::thread::sleep(Duration::from_millis(250));
            let (focused, retained) = observe(&worker);
            assert!(focused.is_null(), "poll read unrelated focused field");
            assert_eq!(retained["value"], "前缀：GitHub后缀");
            let Progress::Finished(Some(evidence)) =
                observation.poll(&focused, &retained, 1.5, false)
            else {
                panic!("real correction did not produce evidence");
            };
            assert_eq!(evidence["baseline_text"], "github");
            assert_eq!(evidence["edited_text"], "GitHub");
            acceptance_report = json!({"ok":true,"cloud_tested":false,"classifier":"deterministic_fixture","session":format!("{session:?}"),"manual":manual,"checks":["production_clipboard_delivery","unicode_selection","real_keyboard_edit","retained_target_only","dictionary_apply","durable_reopen","undo","excluded_app","password","disabled"],"evidence":evidence});
            // Classifier is deliberately a deterministic fixture; verify the
            // real local queue, policy, durable dictionary and undo boundaries.
            let temporary = tempfile::tempdir().unwrap();
            let dict_path = temporary.path().join("dictionary.yaml");
            let db_path = temporary.path().join("learning.sqlite3");
            let mut dictionary = Dictionary::open(&dict_path).unwrap();
            let mut store = LearningStore::open(&db_path, 0.).unwrap();
            store
                .enqueue(&learning::split(&evidence, "fixture"), 2.)
                .unwrap();
            let job = store.claim(2.).unwrap().unwrap();
            let decision = Decision::parse(json!({"decision":"add", "term":"GitHub", "aliases":["github"], "term_type":"proper_name", "confidence":1.})).unwrap();
            let validated = learning::validate(&decision, &job.evidence, &dictionary.entries);
            let authorized = store
                .authorize(&job, validated, 2., |a, b| {
                    learning::identity(a) == learning::identity(b)
                })
                .unwrap();
            assert_eq!(authorized.action, "add");
            store
                .apply(&job, &authorized, &mut dictionary, "automatic", 2.)
                .unwrap();
            assert_eq!(
                Dictionary::open(&dict_path).unwrap().entries[0].term,
                "GitHub"
            );
            drop(store);
            let mut store = LearningStore::open(&db_path, 3.).unwrap();
            assert_eq!(store.get(&job.id).unwrap().unwrap().status, "applied");
            assert!(store.undo(&job.id, &mut dictionary, 3.).unwrap());
            assert!(Dictionary::open(&dict_path).unwrap().entries.is_empty());
            worker.end("fixture");
        }
        std::fs::write(&path, "reset").unwrap();
        std::thread::sleep(Duration::from_millis(250));
        original = capture(&worker, 99);
        assert!(!original.is_null());
        worker.configure(
            true,
            vec![original["app_bundle_id"].as_str().unwrap().into()],
        );
        assert!(capture(&worker, 100).is_null(), "excluded app was read");
        worker.configure(true, vec![]);
        std::fs::write(&path, "secure").unwrap();
        std::thread::sleep(Duration::from_millis(200));
        assert!(capture(&worker, 101).is_null(), "password field was read");
        worker.configure(false, vec![]);
        assert!(capture(&worker, 102).is_null(), "disabled worker read text");
        output.close();
        driver.close();
        let deadline = Instant::now() + Duration::from_secs(3);
        while (!output.finished() || !driver.finished()) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(output.finished() && driver.finished());
        worker.close();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !worker.finished() && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(10));
        }
        assert!(worker.finished(), "AT-SPI worker did not shut down");
        if let Some(report) = std::env::var_os("VOCAL_MORE_ATSPI_TEST_REPORT") {
            std::fs::write(
                report,
                serde_json::to_vec_pretty(&acceptance_report).unwrap(),
            )
            .unwrap();
        }
    }
}
