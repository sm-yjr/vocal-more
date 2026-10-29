// SPDX-License-Identifier: GPL-3.0-only
//! Protocol acceptance uses a private D-Bus session and never captures a screen.
use super::screen::{Key, ScreenWorker};
use std::{
    collections::HashMap,
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    time::{Duration, Instant},
};
use zbus::{
    Connection,
    zvariant::{OwnedObjectPath, OwnedValue, Value},
};

struct Session {
    closed: Arc<AtomicUsize>,
}
#[zbus::interface(name = "org.freedesktop.portal.Session")]
impl Session {
    fn close(&self) {
        self.closed.fetch_add(1, Ordering::AcqRel);
    }
}
struct Portal {
    started: Arc<AtomicUsize>,
    closed: Arc<AtomicUsize>,
}
fn request_path(
    header: &zbus::message::Header<'_>,
    options: &HashMap<String, OwnedValue>,
) -> OwnedObjectPath {
    let sender = header
        .sender()
        .unwrap()
        .as_str()
        .trim_start_matches(':')
        .replace('.', "_");
    let token = <&str>::try_from(options.get("handle_token").unwrap()).unwrap();
    OwnedObjectPath::try_from(format!(
        "/org/freedesktop/portal/desktop/request/{sender}/{token}"
    ))
    .unwrap()
}
fn reply(connection: Connection, path: OwnedObjectPath, result: HashMap<&'static str, OwnedValue>) {
    std::thread::spawn(move || {
        futures_lite::future::block_on(async move {
            std::thread::sleep(Duration::from_millis(20));
            connection
                .emit_signal(
                    None::<&str>,
                    path,
                    "org.freedesktop.portal.Request",
                    "Response",
                    &(0u32, result),
                )
                .await
                .unwrap();
        });
    });
}
#[zbus::interface(name = "org.freedesktop.portal.ScreenCast")]
impl Portal {
    #[zbus(property, name = "version")]
    fn version(&self) -> u32 {
        5
    }
    async fn create_session(
        &self,
        options: HashMap<String, OwnedValue>,
        #[zbus(header)] header: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
    ) -> zbus::fdo::Result<OwnedObjectPath> {
        let sender = header
            .sender()
            .unwrap()
            .as_str()
            .trim_start_matches(':')
            .replace('.', "_");
        let token = <&str>::try_from(options.get("session_handle_token").unwrap()).unwrap();
        let session = OwnedObjectPath::try_from(format!(
            "/org/freedesktop/portal/desktop/session/{sender}/{token}"
        ))
        .unwrap();
        connection
            .object_server()
            .at(
                session.clone(),
                Session {
                    closed: self.closed.clone(),
                },
            )
            .await
            .map_err(|e| zbus::fdo::Error::Failed(e.to_string()))?;
        let path = request_path(&header, &options);
        reply(
            connection.clone(),
            path.clone(),
            HashMap::from([(
                "session_handle",
                OwnedValue::try_from(Value::from(session.as_str())).unwrap(),
            )]),
        );
        Ok(path)
    }
    fn select_sources(
        &self,
        _session: OwnedObjectPath,
        options: HashMap<String, OwnedValue>,
        #[zbus(header)] header: zbus::message::Header<'_>,
        #[zbus(connection)] connection: &Connection,
    ) -> OwnedObjectPath {
        assert_eq!(u32::try_from(options.get("types").unwrap()).unwrap(), 3);
        assert_eq!(
            u32::try_from(options.get("persist_mode").unwrap()).unwrap(),
            1
        );
        let path = request_path(&header, &options);
        reply(connection.clone(), path.clone(), HashMap::new());
        path
    }
    fn start(
        &self,
        _session: OwnedObjectPath,
        _parent: &str,
        options: HashMap<String, OwnedValue>,
        #[zbus(header)] header: zbus::message::Header<'_>,
    ) -> OwnedObjectPath {
        self.started.fetch_add(1, Ordering::AcqRel);
        // Simulate an open permission chooser. Cancellation must close Session.
        request_path(&header, &options)
    }
}
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "run with dbus-run-session, never the real desktop session"]
async fn linux_portal_pending_permission_is_closed_on_cancel_and_shutdown() {
    assert_eq!(
        std::env::var("VOCAL_MORE_PRIVATE_PORTAL_TEST").as_deref(),
        Ok("1")
    );
    let started = Arc::new(AtomicUsize::new(0));
    let closed = Arc::new(AtomicUsize::new(0));
    let _server = zbus::connection::Builder::session()
        .unwrap()
        .name("org.freedesktop.portal.Desktop")
        .unwrap()
        .serve_at(
            "/org/freedesktop/portal/desktop",
            Portal {
                started: started.clone(),
                closed: closed.clone(),
            },
        )
        .unwrap()
        .build()
        .await
        .unwrap();
    let worker = ScreenWorker::new().unwrap();
    for n in 1..=2 {
        worker
            .start(Key {
                request: n as u64,
                epoch: n as u64,
            })
            .unwrap();
        let deadline = Instant::now() + Duration::from_secs(4);
        while started.load(Ordering::Acquire) < n && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(
            started.load(Ordering::Acquire),
            n,
            "permission request did not open"
        );
        if n == 1 {
            worker.stop();
        } else {
            worker.close();
        }
        let deadline = Instant::now() + Duration::from_secs(2);
        while closed.load(Ordering::Acquire) < n && Instant::now() < deadline {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert_eq!(
            closed.load(Ordering::Acquire),
            n,
            "permission session leaked"
        );
        assert!(
            worker.poll().is_none(),
            "cancelled chooser produced a frame/error for a new session"
        );
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    while !worker.finished() && Instant::now() < deadline {
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert!(worker.finished());
}
