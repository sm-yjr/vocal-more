import Clutter from 'gi://Clutter';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const _KEYS = {
    F8: Clutter.KEY_F8,
    F9: Clutter.KEY_F9,
    F10: Clutter.KEY_F10,
    F11: Clutter.KEY_F11,
    F12: Clutter.KEY_F12,
};

function _readTrigger(settings) {
    let value = 'F8';
    try {
        const values = settings.get_strv('linux-accelerator');
        value = (values[0] || 'F8').toUpperCase();
    } catch (_error) {
        value = 'F8';
    }
    return Object.prototype.hasOwnProperty.call(_KEYS, value) ? value : 'F8';
}

export class HotkeyGesture {
    constructor(settings, client) {
        this._settings = settings;
        this._client = client;
        this._pressed = false;
        this._stagePressId = 0;
        this._stageReleaseId = 0;
        this._stageReleaseFallbackId = 0;
        this._bindingInstalled = false;
        this._settingsChangedId = 0;
        // add_keybinding() reads the accelerator from this GSettings key.
        this._bindingName = 'linux-accelerator';
        this._enabled = false;
        this.trigger = _readTrigger(settings);
    }

    enable() {
        if (this._enabled)
            return;
        this._enabled = true;
        this._installBinding();
        // captured-event sees key releases before the focused application and
        // lets a press/release gesture work without taking keyboard focus.
        this._stagePressId = global.stage.connect('captured-event', (_stage, event) => {
            if (event.type() === Clutter.EventType.KEY_PRESS && this._isCancelEvent(event)) {
                this._client.cancel();
                return Clutter.EVENT_STOP;
            }
            if (event.type() === Clutter.EventType.KEY_PRESS && this._matchesEvent(event)) {
                this._press();
                return Clutter.EVENT_STOP;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._stageReleaseId = global.stage.connect('captured-event', (_stage, event) => {
            if (event.type() === Clutter.EventType.KEY_RELEASE && this._matchesEvent(event)) {
                this._release();
                // Let the normal release signal run as well. Some Shell 50
                // builds expose releases there even when capture is active.
                return Clutter.EVENT_PROPAGATE;
            }
            return Clutter.EVENT_PROPAGATE;
        });
        this._stageReleaseFallbackId = global.stage.connect('key-release-event', (_stage, event) => {
            if (this._matchesEvent(event))
                this._release();
            return Clutter.EVENT_PROPAGATE;
        });
        try {
            this._settingsChangedId = this._settings.connect('changed::linux-accelerator', () => {
                this._reloadBinding();
            });
        } catch (_error) {
            this._settingsChangedId = 0;
        }
    }

    disable() {
        if (!this._enabled)
            return;
        this._enabled = false;
        if (this._stagePressId)
            global.stage.disconnect(this._stagePressId);
        if (this._stageReleaseId)
            global.stage.disconnect(this._stageReleaseId);
        if (this._stageReleaseFallbackId)
            global.stage.disconnect(this._stageReleaseFallbackId);
        this._stagePressId = 0;
        this._stageReleaseId = 0;
        this._stageReleaseFallbackId = 0;
        if (this._settingsChangedId) {
            this._settings.disconnect(this._settingsChangedId);
            this._settingsChangedId = 0;
        }
        this._removeBinding();
        if (this._pressed) {
            this._pressed = false;
            this._client.triggerReleased();
        }
    }

    lock() {
        // Lock-screen transitions must not leave a press held in the backend.
        if (this._pressed)
            this._release();
    }

    _reloadBinding() {
        const oldPressed = this._pressed;
        this._pressed = false;
        if (oldPressed)
            this._client.triggerReleased();
        this.trigger = _readTrigger(this._settings);
        if (this._enabled) {
            this._removeBinding();
            this._installBinding();
        }
    }

    _installBinding() {
        try {
            // GNOME Shell delivers the accelerator press to the extension.
            // Release is observed by the stage capture handler above.
            Main.wm.addKeybinding(
                this._bindingName,
                this._settings,
                Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.NORMAL,
                () => this._press());
            this._bindingInstalled = true;
        } catch (_error) {
            // The stage capture path remains available when another extension
            // owns the binding name or a shell policy rejects the binding.
            this._bindingInstalled = false;
        }
    }

    _removeBinding() {
        if (!this._bindingInstalled)
            return;
        try {
            Main.wm.removeKeybinding(this._bindingName);
        } catch (_error) {
            // Shell teardown can remove the binding before extension disable.
        }
        this._bindingInstalled = false;
    }

    _matchesEvent(event) {
        let keySymbol;
        try {
            keySymbol = event.get_key_symbol();
        } catch (_error) {
            return false;
        }
        return keySymbol === _KEYS[this.trigger];
    }

    _isCancelEvent(event) {
        try {
            return event.get_key_symbol() === Clutter.KEY_Escape &&
                this._client.snapshot?.can_cancel === true;
        } catch (_error) {
            return false;
        }
    }

    _press() {
        if (!this._enabled || this._pressed)
            return;
        this._pressed = true;
        this._client.triggerPressed();
    }

    _release() {
        if (!this._enabled || !this._pressed)
            return;
        this._pressed = false;
        this._client.triggerReleased();
    }
};
