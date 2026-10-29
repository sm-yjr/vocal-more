// SPDX-License-Identifier: GPL-3.0-only
// Disposable helper loaded only in the runner's private GNOME session.
import Gio from 'gi://Gio';
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
export default class ScreenTest extends Extension {
    enable() {
        this._pointer = global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
        this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), 640, 820);
        this._inputTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => {
            const path = GLib.getenv('VOCAL_MORE_SCREEN_TEST_OUTPUT') + '/clicks.json';
            if (GLib.file_test(path, GLib.FileTest.EXISTS)) {
                const [, bytes] = GLib.file_get_contents(path);
                this._steps = JSON.parse(new TextDecoder().decode(bytes));
                GLib.unlink(path);
                this._next = 0;
            }
            if (this._steps?.length && GLib.get_monotonic_time() >= this._next) {
                const [x, y] = this._steps.shift();
                this._pointer.notify_absolute_motion(GLib.get_monotonic_time(), x, y);
                this._press = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 100, () => {
                    this._pointer.notify_button(GLib.get_monotonic_time(), 1, Clutter.ButtonState.PRESSED);
                    this._press = 0;
                    this._release = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 50, () => {
                        this._pointer.notify_button(GLib.get_monotonic_time(), 1, Clutter.ButtonState.RELEASED);
                        this._release = 0;
                        return GLib.SOURCE_REMOVE;
                    });
                    return GLib.SOURCE_REMOVE;
                });
                this._next = GLib.get_monotonic_time() + 1000000;
                GLib.timeout_add(GLib.PRIORITY_DEFAULT, 700, () => {
                    const file = Gio.File.new_for_path(GLib.getenv('VOCAL_MORE_SCREEN_TEST_OUTPUT') + `/after-${x}-${y}.png`);
                    const stream = file.replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
                    new Shell.Screenshot().screenshot_area(0, 0, 1280, 960, stream).then(() => stream.close(null)).catch(e => console.error(e));
                    return GLib.SOURCE_REMOVE;
                });
            }
            return GLib.SOURCE_CONTINUE;
        });
        this._timer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 3000, () => {
            Main.overview.hide();
            GLib.file_set_contents(GLib.getenv('VOCAL_MORE_SCREEN_TEST_OUTPUT') + '/shell-ready', 'ready');
            this._windowTimer = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1000, () => {
                GLib.file_set_contents(GLib.getenv('VOCAL_MORE_SCREEN_TEST_OUTPUT') + '/shell-windows.json', JSON.stringify(global.get_window_actors().map(a => ({title: a.meta_window.title, visible: a.visible}))));
                if (!this._shot && global.get_window_actors().some(a => a.meta_window.title === 'Share Screen')) {
                    this._shot = true;
                    const file = Gio.File.new_for_path(GLib.getenv('VOCAL_MORE_SCREEN_TEST_OUTPUT') + '/chooser.png');
                    const stream = file.replace(null, false, Gio.FileCreateFlags.REPLACE_DESTINATION, null);
                    new Shell.Screenshot().screenshot_area(0, 0, 1280, 960, stream).then(() => stream.close(null)).catch(e => console.error(e));
                }
                return GLib.SOURCE_CONTINUE;
            });
            this._timer = 0;
            return GLib.SOURCE_REMOVE;
        });
    }
    disable() {if (this._inputTimer) GLib.source_remove(this._inputTimer);if (this._press) GLib.source_remove(this._press);if (this._release) GLib.source_remove(this._release);this._pointer = null;if (this._windowTimer) GLib.source_remove(this._windowTimer);if (this._timer) GLib.source_remove(this._timer);this._timer = 0;}
}
