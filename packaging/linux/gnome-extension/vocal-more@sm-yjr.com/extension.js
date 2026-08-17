import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {DesktopClient} from './dbusClient.js';
import {HotkeyGesture} from './gesture.js';
import {VocalCapsule} from './capsule.js';
import {VocalPanelIndicator} from './panelMenu.js';

export default class VocalMoreExtension extends Extension {
    constructor(metadata) {
        super(metadata);
        this._settings = null;
        this._client = null;
        this._gesture = null;
        this._capsule = null;
        this._panel = null;
        this._unsubscribers = [];
        this._signalIds = [];
        this._locked = false;
        this._destroyed = false;
        this._reducedMotion = false;
        this._virtualKeyboard = null;
    }

    enable() {
        this._destroyed = false;
        this._settings = this.getSettings();
        this._client = new DesktopClient();
        this._capsule = new VocalCapsule(() => this._client.cancel());
        Main.uiGroup.add_child(this._capsule);

        this._panel = new VocalPanelIndicator(this._settings, this._client);
        Main.panel.addToStatusArea('vocal-more', this._panel, 1, 'center');

        this._gesture = new HotkeyGesture(this._settings, this._client);
        this._gesture.enable();

        this._unsubscribers.push(this._client.onSnapshotChanged(snapshot => {
            if (this._locked)
                return;
            this._capsule.update(snapshot);
            this._panel.update(snapshot);
        }));
        this._unsubscribers.push(this._client.onPasteRequested(requestId => {
            this._completePaste(requestId);
        }));
        this._unsubscribers.push(this._client.onAvailabilityChanged(available => {
            this._panel.setAvailable(available);
            this._capsule.setBackendAvailable(available);
            if (available)
                this._reportFocusedApp();
        }));

        this._connectShellSignals();
        this._applyReducedMotion();
        this._updateLockState();
        this._client.connect();
    }

    disable() {
        this._destroyed = true;
        for (const unsubscribe of this._unsubscribers)
            unsubscribe();
        this._unsubscribers = [];
        for (const [object, id] of this._signalIds) {
            try { object.disconnect(id); } catch (_error) { /* Shell may be tearing down */ }
        }
        this._signalIds = [];
        if (this._gesture)
            this._gesture.disable();
        if (this._client)
            this._client.destroy();
        this._disposeVirtualKeyboard();
        if (this._capsule)
            this._capsule.destroy();
        if (this._panel)
            this._panel.destroy();
        this._gesture = null;
        this._client = null;
        this._capsule = null;
        this._panel = null;
        this._settings = null;
    }

    _connectShellSignals() {
        const connect = (object, signal, callback) => {
            if (!object)
                return;
            try {
                this._signalIds.push([object, object.connect(signal, callback)]);
            } catch (_error) {
                // API availability differs slightly between Shell 50 point releases.
            }
        };
        connect(Main.layoutManager, 'monitors-changed', () => {
            if (!this._locked)
                this._capsule.reposition();
        });
        connect(Main.sessionMode, 'updated', () => this._updateLockState());
        connect(Main.screenShield, 'lock-screen-enabled-changed', () => this._updateLockState());
        connect(global.display, 'notify::focus-window', () => this._reportFocusedApp());

        const stSettings = St.Settings.get();
        connect(stSettings, 'notify::enable-animations', () => this._applyReducedMotion());
        this._reportFocusedApp();
    }

    _applyReducedMotion() {
        let enabled = true;
        try {
            enabled = St.Settings.get().enable_animations;
        } catch (_error) {
            enabled = true;
        }
        this._reducedMotion = enabled === false;
        if (this._capsule)
            this._capsule.setReducedMotion(this._reducedMotion);
    }

    _updateLockState() {
        const sessionLocked = Main.sessionMode && Main.sessionMode.isLocked === true;
        const shieldLocked = Main.screenShield && Main.screenShield.locked === true;
        const locked = sessionLocked || shieldLocked;
        if (locked === this._locked)
            return;
        this._locked = locked;
        if (locked) {
            if (this._gesture)
                this._gesture.lock();
            if (this._capsule)
                this._capsule.hide();
            if (this._client)
                this._client.setFocusedApp('');
        } else if (this._capsule && this._client && this._client.snapshot) {
            this._capsule.update(this._client.snapshot);
            this._reportFocusedApp();
        }
    }

    _reportFocusedApp() {
        if (this._locked || !this._client)
            return;
        try {
            const tracker = Shell.WindowTracker.get_default();
            const window = global.display.focus_window;
            const app = window ? tracker.get_window_app(window) : null;
            const appId = app && typeof app.get_id === 'function' ? app.get_id() : '';
            this._client.setFocusedApp(typeof appId === 'string' ? appId : '');
        } catch (_error) {
            this._client.setFocusedApp('');
        }
    }

    _completePaste(requestId) {
        if (this._destroyed || this._locked) {
            this._client.completePaste(requestId, false, 'locked');
            return;
        }
        try {
            // GNOME Shell 50's Clutter virtual keyboard is the Wayland-safe
            // path for the requested Ctrl+V injection. It never carries the
            // clipboard contents and therefore cannot expose dictated text.
            const seat = Clutter.get_default_backend().get_default_seat();
            this._disposeVirtualKeyboard();
            const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
            this._virtualKeyboard = keyboard;
            // VirtualInputDevice timestamps are monotonic microseconds, not
            // Clutter's legacy millisecond event timestamp.
            let time = GLib.get_monotonic_time();
            keyboard.notify_keyval(time++, Clutter.KEY_Control_L, Clutter.KeyState.PRESSED);
            keyboard.notify_keyval(time++, Clutter.KEY_V, Clutter.KeyState.PRESSED);
            keyboard.notify_keyval(time++, Clutter.KEY_V, Clutter.KeyState.RELEASED);
            keyboard.notify_keyval(time, Clutter.KEY_Control_L, Clutter.KeyState.RELEASED);
            this._client.completePaste(requestId, true, '');
        } catch (_error) {
            // Clipboard ownership remains with GTK when injection fails. The
            // host receives a negative acknowledgement and can surface it.
            this._client.completePaste(requestId, false, 'keyboard injection unavailable');
        }
    }

    _disposeVirtualKeyboard() {
        if (!this._virtualKeyboard)
            return;
        try {
            if (typeof this._virtualKeyboard.destroy === 'function')
                this._virtualKeyboard.destroy();
            else if (typeof this._virtualKeyboard.dispose === 'function')
                this._virtualKeyboard.dispose();
        } catch (_error) {
            // Some Shell 50 builds reclaim the device through GObject dispose.
        }
        this._virtualKeyboard = null;
    }
}
