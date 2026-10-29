// SPDX-License-Identifier: GPL-3.0-only
import St from 'gi://St';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {decode} from './protocol.js';

const NAME = 'org.gnome.Shell.Extensions.VocalMore';
const PATH = '/org/gnome/Shell/Extensions/VocalMore';
const XML = `<node><interface name="${NAME}">
  <method name="Update"><arg type="s" direction="in" name="state"/></method>
  <method name="ClipboardVersion"><arg type="u" direction="out"/></method>
  <method name="ClipboardSet"><arg type="s" direction="in"/><arg type="ay" direction="in"/><arg type="u" direction="out"/></method>
  <method name="ClipboardOwned"><arg type="u" direction="in"/><arg type="b" direction="out"/></method>
  <method name="ClipboardTypes"><arg type="as" direction="out"/></method>
  <method name="ClipboardRead"><arg type="s" direction="in"/><arg type="ay" direction="out"/></method>
  <signal name="Action"><arg type="s" name="command"/></signal>
</interface></node>`;

export default class VocalMoreCapsule extends Extension {
    enable() {
        this._controller = null;
        this._monitor = null;
        this._wasLocked = false;
        this._watch = 0;
        this._level = 0;
        this._phase = 0;
        this._last = 0;
        this._clipboardSerial = 0;
        this._clipboardSource = null;
        this._model = {active: false, state: 'hidden', text: '', level: 0};
        this._actor = new St.BoxLayout({style_class: 'vocal-more-capsule', reactive: true, can_focus: false});
        this._wave = new St.BoxLayout({style_class: 'vocal-more-wave', y_align: Clutter.ActorAlign.CENTER});
        this._bars = Array.from({length: 21}, () => new St.Widget({style_class: 'vocal-more-bar', height: 3, y_align: Clutter.ActorAlign.CENTER}));
        this._bars.forEach(bar => this._wave.add_child(bar));
        this._label = new St.Label({style_class: 'vocal-more-text', y_align: Clutter.ActorAlign.CENTER});
        this._label.clutter_text.set_ellipsize(3);
        this._label.clutter_text.set_single_line_mode(true);
        this._cancel = new St.Button({style_class: 'vocal-more-cancel', label: '×', can_focus: false});
        this._finish = new St.Button({style_class: 'vocal-more-finish', label: '✓', can_focus: false});
        this._cancel.connect('clicked', () => this._action('cancel'));
        this._finish.connect('clicked', () => this._action('stop'));
        [this._wave, this._label, this._cancel, this._finish].forEach(child => this._actor.add_child(child));
        Main.layoutManager.addTopChrome(this._actor, {trackFullscreen: false});
        this._actor.connect('destroy', () => {
            if (this._tick) GLib.source_remove(this._tick);
            this._tick = 0;
            this._actor = null;
        });
        this._actor.hide();
        this._exported = Gio.DBusExportedObject.wrapJSObject(XML, this);
        this._exported.export(Gio.DBus.session, PATH);
        this._name = Gio.bus_own_name_on_connection(Gio.DBus.session, NAME, Gio.BusNameOwnerFlags.NONE, null, null);
        this._monitors = Main.layoutManager.connect('monitors-changed', () => this._position());
        this._session = Main.sessionMode.connect('updated', () => this._sessionChanged());
        this._tick = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 16, () => {
            const locked = this._sessionChanged();
            const alive = this._model.active && GLib.get_monotonic_time() - this._last < 3500000;
            this._actor.visible = alive && !locked;
            if (this._actor.visible) {
                const target = this._model.level;
                this._level += (target - this._level) * (target > this._level ? 0.65 : 0.13);
                const reduced = this._model.reduced_motion || !St.Settings.get().enable_animations;
                this._phase += reduced ? 0 : 0.13;
                this._bars.forEach((bar, i) => {
                    const envelope = Math.exp(-Math.pow((i - 10) / 6, 2) / 2);
                    const motion = reduced ? 0.8 : 0.65 + 0.35 * Math.abs(Math.sin(this._phase + i * 0.8));
                    bar.height = 3 + 27 * this._level * envelope * motion;
                });
            }
            return GLib.SOURCE_CONTINUE;
        });
    }
    ClipboardVersion() { return 1; }
    ClipboardTypes() {
        if (Main.sessionMode.isLocked || Main.screenShield.locked) throw new Error('Desktop is locked');
        return global.display.get_selection().get_mimetypes(Meta.SelectionType.SELECTION_CLIPBOARD) ?? [];
    }
    ClipboardReadAsync([mime], invocation) {
        if (Main.sessionMode.isLocked || Main.screenShield.locked) {
            invocation.return_dbus_error(`${NAME}.ClipboardError`, 'Desktop is locked');
            return;
        }
        const stream = Gio.MemoryOutputStream.new_resizable();
        const cancel = new Gio.Cancellable();
        let timeout = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 2500, () => { timeout = 0; cancel.cancel(); return GLib.SOURCE_REMOVE; });
        global.display.get_selection().transfer_async(Meta.SelectionType.SELECTION_CLIPBOARD, mime, 64 * 1024 * 1024 + 1, stream, cancel, (selection, result) => {
            if (timeout) GLib.source_remove(timeout);
            try {
                selection.transfer_finish(result);
                stream.close(null);
                const bytes = stream.steal_as_bytes();
                if (bytes.get_size() > 64 * 1024 * 1024) throw new Error('Clipboard is too large');
                invocation.return_value(new GLib.Variant('(ay)', [bytes.get_data()]));
            } catch (error) {
                invocation.return_dbus_error(`${NAME}.ClipboardError`, error.message);
            }
        });
    }
    ClipboardSet(mime, bytes) {
        if (Main.sessionMode.isLocked || Main.screenShield.locked)
            throw new Error('Desktop is locked');
        if (!mime || mime.length > 256 || /[\r\n\0]/.test(mime) || bytes.length > 64 * 1024 * 1024)
            throw new Error('Invalid clipboard payload');
        const source = Meta.SelectionSourceMemory.new(mime, new GLib.Bytes(bytes));
        global.display.get_selection().set_owner(Meta.SelectionType.SELECTION_CLIPBOARD, source);
        this._clipboardSource = source;
        this._clipboardSerial = (this._clipboardSerial + 1) >>> 0;
        return this._clipboardSerial;
    }
    ClipboardOwned(serial) {
        return serial === this._clipboardSerial && !!this._clipboardSource?.is_active();
    }
    UpdateAsync([text], invocation) {
        try {
            const sender = invocation.get_sender();
            if (this._controller && this._controller !== sender)
                throw new Error('Another desktop owns this capsule');
            const model = decode(text);
            if (!this._controller) {
                this._controller = sender;
                this._watch = Gio.bus_watch_name_on_connection(Gio.DBus.session, sender, Gio.BusNameWatcherFlags.NONE, null, () => {
                    this._controller = null;
                    this._model.active = false;
                    this._last = 0;
                    this._actor?.hide();
                    if (this._watch) Gio.bus_unwatch_name(this._watch);
                    this._watch = 0;
                });
            }
            if (model.active && !this._model.active) {
                const [x, y] = global.get_pointer();
                this._monitor = Main.layoutManager.monitors.find(m =>
                    x >= m.x && x < m.x + m.width && y >= m.y && y < m.y + m.height)
                    ?? Main.layoutManager.primaryMonitor;
            }
            if (Main.sessionMode.isLocked || Main.screenShield.locked) model.active = false;
            this._model = model;
            this._last = GLib.get_monotonic_time();
            const titles = model.english ? {starting: 'Preparing microphone', recording: 'Listening', processing: 'Processing', cancelling: 'Processing', error: 'Try again'} :
                {starting: '正在连接麦克风', recording: '正在聆听', processing: '正在处理', cancelling: '正在处理', error: '请重试'};
            this._label.text = model.text || titles[model.state] || 'Vocal More';
            this._position();
            invocation.return_value(null);
        } catch (error) {
            invocation.return_dbus_error(`${NAME}.InvalidState`, error.message);
        }
    }
    _sessionChanged() {
        const locked = Main.sessionMode.isLocked || Main.screenShield.locked;
        if (locked) {
            if (!this._wasLocked && this._model.active) this._action('cancel');
            this._model.active = false;
            this._actor?.hide();
        }
        this._wasLocked = locked;
        return locked;
    }
    _position() {
        const monitor = Main.layoutManager.monitors.find(m =>
            this._monitor && m.x === this._monitor.x && m.y === this._monitor.y)
            ?? Main.layoutManager.primaryMonitor;
        if (monitor) this._actor.set_position(Math.round(monitor.x + (monitor.width - 420) / 2), Math.round(monitor.y + monitor.height - 130));
    }
    _action(command) {
        if (this._controller)
            Gio.DBus.session.emit_signal(this._controller, PATH, NAME, 'Action', new GLib.Variant('(s)', [command]));
    }
    disable() {
        if (this._tick) GLib.source_remove(this._tick);
        this._tick = 0;
        if (this._watch) Gio.bus_unwatch_name(this._watch);
        if (this._monitors) Main.layoutManager.disconnect(this._monitors);
        if (this._session) Main.sessionMode.disconnect(this._session);
        if (this._name) Gio.bus_unown_name(this._name);
        this._exported?.unexport();
        this._actor?.destroy();
        this._tick = this._watch = this._monitors = this._name = this._session = 0;
        this._monitor = null;
        this._controller = null;
        this._exported = this._actor = null;
    }
}
