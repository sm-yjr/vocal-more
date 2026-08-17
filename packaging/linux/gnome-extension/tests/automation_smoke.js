import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';

// GNOME Shell automation entry point used by test_shell_extension.sh.
// Returning from run() tells gnome-shell-test-tool to shut down cleanly.
export function run() {
    const seat = Clutter.get_default_backend().get_default_seat();
    const keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    let time = GLib.get_monotonic_time();
    keyboard.notify_keyval(time++, Clutter.KEY_Control_L, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(time++, Clutter.KEY_V, Clutter.KeyState.PRESSED);
    keyboard.notify_keyval(time++, Clutter.KEY_V, Clutter.KeyState.RELEASED);
    keyboard.notify_keyval(time, Clutter.KEY_Control_L, Clutter.KeyState.RELEASED);
    if (typeof keyboard.destroy === 'function')
        keyboard.destroy();
    else if (typeof keyboard.dispose === 'function')
        keyboard.dispose();
    return true;
}
