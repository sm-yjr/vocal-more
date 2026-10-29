// SPDX-License-Identifier: GPL-3.0-only
// Loaded only by the isolated headless GNOME acceptance runner.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import Clutter from 'gi://Clutter';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const NAME = 'org.gnome.Shell.Extensions.VocalMore';
const PATH = '/org/gnome/Shell/Extensions/VocalMore';
const pause = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {resolve(); return GLib.SOURCE_REMOVE;}));
function check(value, message) {if (!value) throw new Error(message);}
function connect() {
    return Gio.DBusConnection.new_for_address_sync(GLib.getenv('DBUS_SESSION_BUS_ADDRESS'),
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, null, null);
}
function update(bus, model) {
    return new Promise((resolve, reject) => bus.call(NAME, PATH, NAME, 'Update',
        new GLib.Variant('(s)', [JSON.stringify(model)]), null, Gio.DBusCallFlags.NONE, 1000, null,
        (connection, result) => {try {connection.call_finish(result); resolve();} catch (error) {reject(error);}}));
}
export default class CapsuleTest extends Extension {
    enable() {
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 4500, () => {
            this._timer = 0;
            this.run().catch(error => this.finish({ok: false, error: String(error)}));
            return GLib.SOURCE_REMOVE;
        });
    }
    async run() {
        const seat = global.stage.context.get_backend().get_default_seat();
        this._pointer = seat.create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), 640, 500);
        const click = async actor => {
            const [x, y] = actor.get_transformed_position();
            this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), x + actor.width / 2, y + actor.height / 2);
            await pause(100);
            this._pointer.notify_button(GLib.get_monotonic_time(), 1, Clutter.ButtonState.PRESSED);
            await pause(50);
            this._pointer.notify_button(GLib.get_monotonic_time(), 1, Clutter.ButtonState.RELEASED);
            await pause(200);
        };
        const output = GLib.getenv('VOCAL_MORE_GNOME_TEST_OUTPUT');
        const write = (name, value='yes') => GLib.file_set_contents(`${output}/${name}`, value);
        const exists = name => GLib.file_test(`${output}/${name}`, GLib.FileTest.EXISTS);
        const wait = async condition => {
            const deadline = GLib.get_monotonic_time() + 45000000;
            while (!condition()) {check(GLib.get_monotonic_time() < deadline, 'Transport phase timed out'); await pause(50);}
        };
        const phase = wanted => {
            if (!exists('transport-phase')) return false;
            const [, bytes] = GLib.file_get_contents(`${output}/transport-phase`);
            return new TextDecoder().decode(bytes) === wanted;
        };
        let capsule = Main.extensionManager.lookup('vocal-more@sm-yjr').stateObj;
        check(capsule?._actor, 'Production capsule did not load');
        Main.overview.hide();
        await pause(500);
        await wait(() => global.get_window_actors().some(a => a.meta_window.title === 'Vocal More AT-SPI Fixture'));
        const editor = global.get_window_actors().find(a => a.meta_window.title === 'Vocal More AT-SPI Fixture').meta_window;
        editor.activate(global.get_current_time());
        await pause(300);
        check(global.display.focus_window === editor, 'Fixture editor did not receive focus');
        const focus = global.stage.key_focus;
        const controller = connect();
        const model = {active: true, state: 'recording', text: '低声输入 · Linux 悬浮胶囊', level: 0.72};
        await update(controller, model);
        await pause(400);
        check(capsule._actor.visible, 'Capsule not visible');
        check(!capsule._actor.can_focus && global.stage.key_focus === focus, 'Capsule stole focus');
        check(capsule._actor.width >= 400 && capsule._actor.width <= 440, `Invalid width ${capsule._actor.width}`);
        check(capsule._label.text === model.text, 'Text not delivered');
        check(Main.layoutManager.monitors.length >= 2, 'Second virtual monitor missing');
        await update(controller, {...model, active:false});
        const second = Main.layoutManager.monitors[1];
        this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), second.x + 100, second.y + 100);
        await pause(100);
        await update(controller, model);
        await pause(100);
        check(capsule._actor.x >= second.x && capsule._actor.x < second.x + second.width, 'Capsule did not follow recording monitor');
        const shot = async name => {
            const stream=Gio.File.new_for_path(`${output}/${name}`).replace(null,false,Gio.FileCreateFlags.REPLACE_DESTINATION,null);
            await new Shell.Screenshot().screenshot_area(second.x,second.y,second.width,second.height,stream);
            stream.close(null);
        };
        editor.move_to_monitor(1);
        editor.make_fullscreen();
        await pause(200);
        check(capsule._actor.visible, 'Fullscreen hid capsule');
        await shot('capsule-second-monitor-fullscreen.png');
        const originalWorkspace = editor.get_workspace();
        const nextWorkspace = global.workspace_manager.get_workspace_by_index(1);
        editor.change_workspace(nextWorkspace);
        nextWorkspace.activate(global.get_current_time());
        await pause(200);
        check(capsule._actor.visible, 'Workspace change hid capsule');
        await shot('capsule-second-monitor-workspace.png');
        editor.change_workspace(originalWorkspace);
        originalWorkspace.activate(global.get_current_time());
        editor.unmake_fullscreen();
        editor.move_to_monitor(0);
        await update(controller, {...model, active:false});
        this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), 640, 500);
        await pause(100);
        await update(controller, model);
        editor.activate(global.get_current_time());
        await pause(200);
        Main.screenShield.lock(false);
        await wait(() => Main.sessionMode.currentMode === 'unlock-dialog');
        check(!capsule._actor || !capsule._actor.visible, 'Lock retained active capsule');
        Main.screenShield.deactivate(false);
        await wait(() => Main.sessionMode.currentMode === 'user');
        capsule = Main.extensionManager.lookup('vocal-more@sm-yjr').stateObj;
        await wait(() => capsule._actor !== null);
        check(!capsule._actor.visible && !capsule._model.active, 'Unlock resurrected old recording');
        await update(controller, model);
        await pause(200);
        editor.activate(global.get_current_time());

        let received = '';
        const watch = controller.signal_subscribe(NAME, NAME, 'Action', PATH, null, Gio.DBusSignalFlags.NONE,
            (_bus, _sender, _path, _interface, _signal, parameters) => {received = parameters.deep_unpack()[0];});
        await click(capsule._cancel);
        await pause(100);
        check(received === 'cancel', 'Cancel action did not reach the controller');
        await click(capsule._finish);
        await pause(100);
        check(received === 'stop', 'Finish action did not reach the controller');
        check(global.display.focus_window === editor && global.stage.key_focus === focus, 'Button click stole editor focus');
        let rejected = false;
        const other = connect();
        try {await update(other, model);} catch {rejected = true;}
        check(rejected, 'Second controller was accepted');
        other.close_sync(null);
        const file = Gio.File.new_for_path(GLib.getenv('VOCAL_MORE_GNOME_TEST_OUTPUT') + '/gnome-capsule.png');
        const stream = file.replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
        await new Shell.Screenshot().screenshot_area(0, 0, 1280, 960, stream);
        stream.close(null);
        const idleAt = GLib.get_monotonic_time();
        await update(controller, {...model, active: false, state: 'hidden'});
        await wait(() => !capsule._actor.visible);
        this._idleHideMs = (GLib.get_monotonic_time() - idleAt) / 1000;
        check(this._idleHideMs < 250, `Idle capsule hid after ${this._idleHideMs} ms`);
        await update(controller, model);
        await pause(100);
        controller.signal_unsubscribe(watch);
        controller.close_sync(null);
        await pause(200);
        check(!capsule._actor.visible, 'Disconnected controller left an overlay');
        const stale = connect();
        await update(stale, model);
        await pause(3700);
        check(!capsule._actor.visible, 'Stale controller left an overlay');
        stale.close_sync(null);
        await pause(100);
        // Disabling must remove timers, actors and the D-Bus export cleanly.
        capsule.disable();
        check(capsule._tick === 0 && capsule._actor === null, 'Capsule cleanup failed');
        capsule.enable();
        write('transport-ready');
        await wait(() => phase('recording'));
        await wait(() => capsule._actor.visible);
        check(capsule._label.text === '实际点击与焦点验证', 'Rust model did not reach Shell');
        await click(capsule._cancel);
        await wait(() => phase('processing'));
        await wait(() => capsule._label.text === '处理中');
        await click(capsule._finish);
        await wait(() => phase('restart'));
        check(global.display.focus_window === editor && global.stage.key_focus === focus, 'Rust capsule stole focus');
        capsule.disable();
        write('transport-disabled');
        await wait(() => exists('transport-unavailable'));
        capsule.enable();
        write('transport-enabled');
        await wait(() => phase('recovered'));
        await wait(() => capsule._actor.visible);
        check(capsule._label.text === '处理中', 'Rust transport failed to recover after re-enable');
        write('transport-recovery-checked');
        await wait(() => phase('hide'));
        await wait(() => !Main.extensionManager.lookup('vocal-more@sm-yjr').stateObj._actor.visible);
        write('transport-hide-checked');
        await wait(() => phase('closed'));
        await pause(300);
        check(!capsule._actor.visible, 'Rust shutdown left capsule visible');
        // Synthetic keyboard belongs to this private compositor only. Exercise
        // native GTK Wayland clipboard and AT-SPI without X11 injection.
        this._keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        this._keysTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 25, () => {
            const path = `${output}/keys.json`;
            if (GLib.file_test(path, GLib.FileTest.EXISTS) && !this._typing) {
                this._typing = true;
                const [, bytes] = GLib.file_get_contents(path);
                const args = JSON.parse(new TextDecoder().decode(bytes));
                const key = async value => {
                    const parts = value.split('+');
                    const modifiers = parts.slice(0, -1).map(m => m === 'ctrl' ? Clutter.KEY_Control_L : Clutter.KEY_Shift_L);
                    const name = parts.at(-1);
                    const code = Clutter[`KEY_${name}`] ?? name.codePointAt(0);
                    for (const mod of modifiers) this._keyboard.notify_keyval(GLib.get_monotonic_time(), mod, Clutter.KeyState.PRESSED);
                    this._keyboard.notify_keyval(GLib.get_monotonic_time(), code, Clutter.KeyState.PRESSED);
                    await pause(20);
                    this._keyboard.notify_keyval(GLib.get_monotonic_time(), code, Clutter.KeyState.RELEASED);
                    for (const mod of modifiers.reverse()) this._keyboard.notify_keyval(GLib.get_monotonic_time(), mod, Clutter.KeyState.RELEASED);
                    await pause(20);
                };
                (async () => {
                    const keys = args[0] === 'type' ? [...args[2]] : args.slice(2);
                    for (const value of keys) await key(value);
                    GLib.unlink(path);
                    this._typing = false;
                })().catch(error => this.finish({ok:false,error:String(error)}));
            }
            return GLib.SOURCE_CONTINUE;
        });
        write('transport-atspi-ready');
        await wait(() => exists('transport-atspi-done'));
        GLib.source_remove(this._keysTimer);
        this._keysTimer = 0;

        this.finish({ok: true, cloud_tested: false, idle_hide_ms: this._idleHideMs, checks: ['recording-monitor', 'fullscreen', 'workspace', 'screen-lock-no-resurrection', 'render', 'real-pointer-buttons', 'editor-focus', 'single-controller', 'idle', 'disconnect', 'stale-timeout', 'disable', 'rust-model-transport', 'rust-cancel-revocation', 'rust-stop', 'extension-reenable', 'worker-shutdown', 'wayland-real-paste-correction-learning']});
    }
    finish(report) {
        GLib.file_set_contents(GLib.getenv('VOCAL_MORE_GNOME_TEST_OUTPUT') + '/gnome-capsule.json', JSON.stringify(report));
    }
    disable() {if (this._timer) GLib.source_remove(this._timer); this._timer = 0;this._pointer = null;this._keyboard = null;if(this._keysTimer) GLib.source_remove(this._keysTimer);this._keysTimer=0;}
}
