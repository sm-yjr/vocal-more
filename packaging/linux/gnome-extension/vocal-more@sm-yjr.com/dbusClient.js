import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const DBUS_NAME = 'com.sm_yjr.VocalMore';
export const DBUS_PATH = '/com/sm_yjr/VocalMore/Desktop';
export const DBUS_INTERFACE = 'com.sm_yjr.VocalMore.Desktop1';
export const DBUS_CONTEXT_INTERFACE = 'com.sm_yjr.VocalMore.DesktopContext1';

// Keep this contract local to the extension so a broken host cannot make the
// UI guess at arbitrary values. The payload deliberately contains no user
// dictated text; it is a state-only snapshot.
const _INTERFACE_XML = `
<node>
  <interface name="${DBUS_INTERFACE}">
    <method name="GetSnapshot">
      <arg direction="out" type="s" name="snapshot_json"/>
    </method>
    <method name="TriggerPressed"/>
    <method name="TriggerReleased"/>
    <method name="Cancel"/>
    <method name="SetMode"><arg direction="in" type="s" name="mode"/></method>
    <method name="SetAutoPaste"><arg direction="in" type="b" name="enabled"/></method>
    <method name="ShowSettings"/>
    <method name="Quit"/>
    <method name="CompletePaste">
      <arg direction="in" type="t" name="request_id"/>
      <arg direction="in" type="b" name="success"/>
      <arg direction="in" type="s" name="error"/>
    </method>
    <signal name="SnapshotChanged">
      <arg type="s" name="snapshot_json"/>
    </signal>
    <signal name="PasteRequested">
      <arg type="t" name="request_id"/>
    </signal>
  </interface>
</node>`;

const _INTERFACE_INFO = Gio.DBusNodeInfo.new_for_xml(_INTERFACE_XML)
    .interfaces[0];

const _MODES = new Set(['walkie_talkie', 'realtime_long', 'meeting']);
const _STATES = new Set(['idle', 'recording', 'processing', 'success', 'error', 'failed', 'cancelled']);
const _STAGES = new Set([
    'idle', 'recording', 'stopping', 'uploading', 'transcribing', 'polishing',
    'saving', 'complete', 'error', 'cancelled',
]);

function _clamp(value, lower, upper) {
    return Math.max(lower, Math.min(upper, value));
}

function _safeString(value, fallback, maximum = 64) {
    if (typeof value !== 'string' || value.length === 0 || value.length > maximum)
        return fallback;
    return value;
}

function _safeSnapshot(value, fallbackTrigger = 'F8') {
    let parsed;
    try {
        parsed = JSON.parse(value);
    } catch (_error) {
        return null;
    }
    if (!parsed || typeof parsed !== 'object' || parsed.schema_version !== 1)
        return null;

    const mode = _MODES.has(parsed.mode) ? parsed.mode : 'walkie_talkie';
    const state = _STATES.has(parsed.state) ? parsed.state : 'idle';
    const stage = _STAGES.has(parsed.stage) ? parsed.stage : state;
    const level = Number(parsed.audio_level);
    return {
        schema_version: 1,
        state,
        mode,
        language: _safeString(parsed.language, 'en', 16),
        stage,
        audio_level: Number.isFinite(level) ? _clamp(level, 0, 1) : 0,
        trigger_label: /^F(?:8|9|10|11|12)$/.test(parsed.trigger_label)
            ? parsed.trigger_label
            : fallbackTrigger,
        can_cancel: parsed.can_cancel === true,
        auto_paste: parsed.auto_paste !== false,
        backend_ready: parsed.backend_ready !== false,
    };
}

export class DesktopClient {
    constructor() {
        this._proxy = null;
        this._proxySignalId = 0;
        this._ownerSignalId = 0;
        this._reconnectSource = 0;
        this._retrySeconds = 1;
        this._destroyed = false;
        this._snapshot = null;
        this._handlers = {
            snapshot: [],
            paste: [],
            availability: [],
        };
    }

    connect() {
        this._destroyed = false;
        this._connectNow();
    }

    destroy() {
        this._destroyed = true;
        if (this._reconnectSource) {
            GLib.Source.remove(this._reconnectSource);
            this._reconnectSource = 0;
        }
        this._disconnectProxy();
        this._handlers.snapshot = [];
        this._handlers.paste = [];
        this._handlers.availability = [];
    }

    onSnapshotChanged(callback) {
        this._handlers.snapshot.push(callback);
        return () => this._removeHandler(this._handlers.snapshot, callback);
    }

    onPasteRequested(callback) {
        this._handlers.paste.push(callback);
        return () => this._removeHandler(this._handlers.paste, callback);
    }

    onAvailabilityChanged(callback) {
        this._handlers.availability.push(callback);
        return () => this._removeHandler(this._handlers.availability, callback);
    }

    get snapshot() {
        return this._snapshot;
    }

    triggerPressed() { this._call('TriggerPressed', '()', []); }
    triggerReleased() { this._call('TriggerReleased', '()', []); }
    cancel() { this._call('Cancel', '()', []); }
    showSettings() { this._call('ShowSettings', '()', []); }
    quit() { this._call('Quit', '()', []); }

    setFocusedApp(desktopAppId) {
        if (typeof desktopAppId !== 'string' || desktopAppId.length > 256)
            return;
        if (!this._proxy || !this._proxy.g_name_owner)
            return;
        const connection = this._proxy.get_connection();
        connection.call(
            DBUS_NAME,
            DBUS_PATH,
            DBUS_CONTEXT_INTERFACE,
            'SetFocusedApp',
            GLib.Variant.new('(s)', [desktopAppId]),
            null,
            Gio.DBusCallFlags.NONE,
            5000,
            null,
            (source, result) => {
                try { source.call_finish(result); } catch (_error) { /* best effort */ }
            });
    }

    setMode(mode) {
        if (_MODES.has(mode))
            this._call('SetMode', '(s)', [mode]);
    }

    setAutoPaste(enabled) {
        this._call('SetAutoPaste', '(b)', [enabled === true]);
    }

    completePaste(requestId, success, error = '') {
        // The uint64 request ID plus success/error tuple is the Desktop1
        // contract and keeps the acknowledgement free of clipboard contents.
        const numericRequestId = Number(requestId);
        if (!Number.isSafeInteger(numericRequestId) || numericRequestId < 0)
            return;
        this._call('CompletePaste', '(tbs)', [numericRequestId, success === true, String(error)]);
    }

    _removeHandler(list, callback) {
        const index = list.indexOf(callback);
        if (index >= 0)
            list.splice(index, 1);
    }

    _connectNow() {
        if (this._destroyed || this._proxy)
            return;
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            _INTERFACE_INFO,
            DBUS_NAME,
            DBUS_PATH,
            DBUS_INTERFACE,
            null,
            (_source, result) => {
                if (this._destroyed)
                    return;
                try {
                    this._proxy = Gio.DBusProxy.new_for_bus_finish(result);
                    this._proxySignalId = this._proxy.connect('g-signal', (_proxy, _sender, signal, parameters) => {
                        this._onSignal(signal, parameters);
                    });
                    this._ownerSignalId = this._proxy.connect('notify::g-name-owner', () => {
                        if (!this._proxy.g_name_owner)
                            this._onUnavailable();
                        else
                            this._emitAvailability(true);
                    });
                    if (this._proxy.g_name_owner) {
                        this._retrySeconds = 1;
                        this._emitAvailability(true);
                        this._requestSnapshot();
                    } else {
                        this._onUnavailable();
                    }
                } catch (_error) {
                    this._onUnavailable();
                }
            });
    }

    _disconnectProxy() {
        if (!this._proxy)
            return;
        if (this._proxySignalId)
            this._proxy.disconnect(this._proxySignalId);
        if (this._ownerSignalId)
            this._proxy.disconnect(this._ownerSignalId);
        this._proxySignalId = 0;
        this._ownerSignalId = 0;
        this._proxy = null;
    }

    _requestSnapshot() {
        this._call('GetSnapshot', '()', [], (result) => {
            let snapshot;
            try {
                snapshot = _safeSnapshot(result.deepUnpack()[0]);
            } catch (_error) {
                snapshot = null;
            }
            if (snapshot)
                this._publishSnapshot(snapshot);
        });
    }

    _onSignal(signal, parameters) {
        if (signal === 'SnapshotChanged') {
            let snapshot;
            try {
                snapshot = _safeSnapshot(parameters.deepUnpack()[0]);
            } catch (_error) {
                snapshot = null;
            }
            if (snapshot)
                this._publishSnapshot(snapshot);
        } else if (signal === 'PasteRequested') {
            let requestId;
            try {
                requestId = parameters.deepUnpack()[0];
            } catch (_error) {
                return;
            }
            if (!Number.isSafeInteger(Number(requestId)) || Number(requestId) < 0)
                return;
            for (const callback of [...this._handlers.paste])
                callback(Number(requestId));
        }
    }

    _publishSnapshot(snapshot) {
        this._snapshot = snapshot;
        for (const callback of [...this._handlers.snapshot])
            callback(snapshot);
    }

    _call(method, signature, args, onSuccess = null, onError = null) {
        if (!this._proxy || !this._proxy.g_name_owner) {
            if (onError)
                onError();
            return;
        }
        let parameters;
        try {
            parameters = GLib.Variant.new(signature, args);
        } catch (_error) {
            if (onError)
                onError();
            return;
        }
        this._proxy.call(method, parameters, Gio.DBusCallFlags.NONE, 5000, null, (proxy, result) => {
            try {
                const reply = proxy.call_finish(result);
                if (onSuccess)
                    onSuccess(reply);
            } catch (_error) {
                if (onError)
                    onError();
            }
        });
    }

    _onUnavailable() {
        this._emitAvailability(false);
        this._disconnectProxy();
        this._scheduleReconnect();
    }

    _emitAvailability(available) {
        for (const callback of [...this._handlers.availability])
            callback(available === true);
    }

    _scheduleReconnect() {
        if (this._destroyed || this._reconnectSource)
            return;
        const delay = this._retrySeconds;
        this._retrySeconds = Math.min(30, this._retrySeconds * 2);
        this._reconnectSource = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, delay, () => {
            this._reconnectSource = 0;
            this._connectNow();
            return GLib.SOURCE_REMOVE;
        });
    }
};
