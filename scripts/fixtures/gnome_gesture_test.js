// SPDX-License-Identifier: GPL-3.0-only
// Virtual input belongs exclusively to the private headless acceptance shell.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Clutter from 'gi://Clutter';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const pause = ms => new Promise(resolve => GLib.timeout_add(GLib.PRIORITY_DEFAULT, ms, () => {resolve();return GLib.SOURCE_REMOVE;}));
export default class Test extends Extension {
    enable(){this._timer=GLib.timeout_add(GLib.PRIORITY_DEFAULT,2000,()=>{this._timer=0;this.run().catch(e=>this.finish({ok:false,error:String(e)}));return GLib.SOURCE_REMOVE;});}
    async run(){
        const output=GLib.getenv('VOCAL_MORE_GESTURE_TEST_OUTPUT');
        if(!output || !GLib.getenv('XDG_RUNTIME_DIR').startsWith(GLib.getenv('HOME')))throw new Error('Private session required');
        const exists=name=>GLib.file_test(`${output}/${name}`,GLib.FileTest.EXISTS);
        const phase=()=>exists('phase')?new TextDecoder().decode(GLib.file_get_contents(`${output}/phase`)[1]):'';
        const wait=async condition=>{const end=GLib.get_monotonic_time()+90000000;while(!condition()){if(GLib.get_monotonic_time()>end)throw new Error(`Phase timed out: ${phase()}`);await pause(50);}};
        Main.overview.hide();
        await wait(()=>global.get_window_actors().some(a=>a.meta_window.title==='Vocal More AT-SPI Fixture'));
        const editor=global.get_window_actors().find(a=>a.meta_window.title==='Vocal More AT-SPI Fixture').meta_window;
        editor.activate(global.get_current_time());await pause(200);
        this._keyboard=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        await wait(()=>phase()==='waiting');
        await pause(1700);
        if(phase()==='waiting'){
            const stream=Gio.File.new_for_path(`${output}/permission.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);
            await new Shell.Screenshot().screenshot_area(0,0,1280,960,stream);stream.close(null);
        }
        if(phase()==='waiting') {
            const windows=global.get_window_actors().map(a=>a.meta_window);
            const dialog=windows.find(w=>w.title==='Add Keyboard Shortcuts' || w.get_wm_class()==='org.gnome.Settings.GlobalShortcutsProvider');
            if(!dialog) {
                GLib.file_set_contents(`${output}/windows.json`,JSON.stringify(windows.map(w=>({title:w.title,wm:w.get_wm_class()}))));
                throw new Error('Owned shortcuts consent dialog missing');
            }
            dialog.activate(global.get_current_time()); await pause(500);
            const bounds=dialog.get_frame_rect();
            if(bounds.width>650 || bounds.height>450)throw new Error('Unexpected consent dialog geometry');
            GLib.file_set_contents(`${output}/dialog-bounds.json`,JSON.stringify({x:bounds.x,y:bounds.y,width:bounds.width,height:bounds.height,title:dialog.title}));
            // Activate the native Add button's documented mnemonic.
            this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Alt_L,Clutter.KeyState.PRESSED);
            this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_a,Clutter.KeyState.PRESSED);
            await pause(80);
            this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_a,Clutter.KeyState.RELEASED);
            this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Alt_L,Clutter.KeyState.RELEASED);
            await pause(500);
            GLib.file_set_contents(`${output}/permission-clicked`,'native Add mnemonic');
            if(phase()==='waiting') {
                const stream=Gio.File.new_for_path(`${output}/after-click.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);
                await new Shell.Screenshot().screenshot_area(0,0,1280,960,stream);stream.close(null);
            }
        }
        await wait(()=>phase()==='bound');
        editor.activate(global.get_current_time());await pause(200);
        this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_F8,Clutter.KeyState.PRESSED);
        await pause(850);
        this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_F8,Clutter.KeyState.RELEASED);
        await wait(()=>phase()==='tap');
        this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_F8,Clutter.KeyState.PRESSED);
        await pause(80);
        this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_F8,Clutter.KeyState.RELEASED);
        await wait(()=>phase()==='close-held');
        this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_F8,Clutter.KeyState.PRESSED);
        await wait(()=>exists('rust-gestures.json'));
        this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_F8,Clutter.KeyState.RELEASED);
        editor.activate(global.get_current_time());await pause(200);
        GLib.file_set_contents(`${output}/input-ready`,'yes');
        await wait(()=>exists('input-done'));
        this.finish({ok:true,checks:['portal-consent','real-hold-release','real-short-tap','held-key-owner-close','native-wayland-edit'],cloud_tested:false});
    }
    finish(report){GLib.file_set_contents(`${GLib.getenv('VOCAL_MORE_GESTURE_TEST_OUTPUT')}/session.json`,JSON.stringify(report));}
    disable(){if(this._timer)GLib.source_remove(this._timer);this._timer=0;this._keyboard=null;}
}
