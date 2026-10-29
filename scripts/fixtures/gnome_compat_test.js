// SPDX-License-Identifier: GPL-3.0-only
// The actual permission dialog and virtual input belong only to an owned compositor.
import Gio from 'gi://Gio';import GLib from 'gi://GLib';import Clutter from 'gi://Clutter';import St from 'gi://St';import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';import * as Main from 'resource:///org/gnome/shell/ui/main.js';
const pause=ms=>new Promise(resolve=>GLib.timeout_add(GLib.PRIORITY_DEFAULT,ms,()=>{resolve();return GLib.SOURCE_REMOVE;}));
export default class Test extends Extension {
 enable(){const output=GLib.getenv('VOCAL_MORE_COMPAT_OUTPUT');if(output){GLib.file_set_contents(`${output}/display`,GLib.getenv('DISPLAY')??'');GLib.file_set_contents(`${output}/authority`,GLib.getenv('XAUTHORITY')??'');}this._timer=GLib.timeout_add(GLib.PRIORITY_DEFAULT,1500,()=>{this._timer=0;this.run().catch(e=>this.finish({ok:false,error:String(e)}));return GLib.SOURCE_REMOVE;});}
 async run(){
  const output=GLib.getenv('VOCAL_MORE_COMPAT_OUTPUT');if(!output||!GLib.getenv('XDG_RUNTIME_DIR').startsWith(GLib.getenv('HOME')))throw new Error('Private session required');
  const read=name=>GLib.file_test(`${output}/${name}`,GLib.FileTest.EXISTS)?new TextDecoder().decode(GLib.file_get_contents(`${output}/${name}`)[1]):'';
  const wait=async fn=>{const end=GLib.get_monotonic_time()+90000000;while(!fn()){if(GLib.get_monotonic_time()>end)throw new Error(`Compatibility phase timed out: ${read('phase')}`);await pause(25);}};
  const editorTitle=GLib.getenv('VOCAL_MORE_COMPAT_SCENARIO')==='terminal'?'Vocal More Terminal Fixture':'Vocal More Compatible Input Fixture';
  Main.overview.hide();await wait(()=>global.get_window_actors().some(a=>a.meta_window.title===editorTitle));
  const editor=global.get_window_actors().find(a=>a.meta_window.title===editorTitle).meta_window;
  editor.activate(global.get_current_time());await wait(()=>read('phase')==='authorizing');await pause(1500);
  const stream=Gio.File.new_for_path(`${output}/permission.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);await new Shell.Screenshot().screenshot_area(0,0,1280,960,stream);stream.close(null);
  const all=[];const walk=actor=>{all.push(actor);for(const child of actor.get_children?.()??[])walk(child);};walk(Main.uiGroup);
  const buttons=all.filter(a=>a instanceof St.Button&&a.mapped).map(a=>({actor:a,label:a.get_label?.()??a.label??''}));
  GLib.file_set_contents(`${output}/buttons.json`,JSON.stringify(buttons.map(b=>b.label)));
  const button=buttons.find(b=>['Share','Allow','允许','共享'].includes(b.label));
  if(!button) {
   const dialog=global.get_window_actors().map(a=>a.meta_window).find(w=>w.title==='Remote Desktop');
   if(!dialog)throw new Error('Owned native keyboard consent dialog unavailable');
   dialog.activate(global.get_current_time());await pause(300);
   this._keyboard=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
   const key=async value=>{this._keyboard.notify_keyval(GLib.get_monotonic_time(),value,Clutter.KeyState.PRESSED);await pause(40);this._keyboard.notify_keyval(GLib.get_monotonic_time(),value,Clutter.KeyState.RELEASED);await pause(100);};
   await key(Clutter.KEY_Tab);await key(Clutter.KEY_Tab);await key(Clutter.KEY_space);
   const toggled=Gio.File.new_for_path(`${output}/after-toggle.png`).replace(null,false,Gio.FileCreateFlags.NONE,null);await new Shell.Screenshot().screenshot_area(0,0,1280,960,toggled);toggled.close(null);
   this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Alt_L,Clutter.KeyState.PRESSED);await key(Clutter.KEY_s);this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Alt_L,Clutter.KeyState.RELEASED);
  }
  if(button) {
  this._pointer=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.POINTER_DEVICE);
  const [x,y]=button.actor.get_transformed_position();this._pointer.notify_absolute_motion(GLib.get_monotonic_time(),x+button.actor.width/2,y+button.actor.height/2);await pause(100);
  this._pointer.notify_button(GLib.get_monotonic_time(),1,Clutter.ButtonState.PRESSED);await pause(60);this._pointer.notify_button(GLib.get_monotonic_time(),1,Clutter.ButtonState.RELEASED);
  }
  await wait(()=>read('phase')==='authorized');editor.activate(global.get_current_time());await pause(300);
  if(GLib.getenv('VOCAL_MORE_COMPAT_SCENARIO')==='mixed'){
   this._keyboard=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
   this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Control_L,Clutter.KeyState.PRESSED);
   this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_c,Clutter.KeyState.PRESSED);
   this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_c,Clutter.KeyState.RELEASED);
   this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Control_L,Clutter.KeyState.RELEASED);
   await pause(300);
  }
  const focusEvents=[];const windowEvents=[];
  const focusSignal=global.display.connect('notify::focus-window',()=>{if(read('phase')==='inserting')focusEvents.push(global.display.focus_window?.title??null);});
  const windowSignal=global.display.connect('window-created',(_display,window)=>{if(read('phase')==='inserting')windowEvents.push(window.title);});
  GLib.file_set_contents(`${output}/focused`,'yes');
  await wait(()=>read('phase')==='paste-completed');
  global.display.disconnect(focusSignal);global.display.disconnect(windowSignal);
  GLib.file_set_contents(`${output}/paste-focus.json`,JSON.stringify({focusEvents,windowEvents}));
  if(focusEvents.length||windowEvents.length)throw new Error('Paste created a window or changed focus');
  this._keyboard=global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
  if(GLib.getenv('VOCAL_MORE_COMPAT_SCENARIO')==='terminal'){
   const other=global.get_window_actors().find(a=>a.meta_window.title==='Vocal More AT-SPI Fixture');
   if(!other)throw new Error('Owned alternate input missing');other.meta_window.activate(global.get_current_time());
  }else{
  this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Tab,Clutter.KeyState.PRESSED);this._keyboard.notify_keyval(GLib.get_monotonic_time(),Clutter.KEY_Tab,Clutter.KeyState.RELEASED);}
  await pause(200);GLib.file_set_contents(`${output}/focus-changed`,'yes');
  await wait(()=>read('phase')==='complete');this.finish({ok:true,keyboard_only:true,chromium_native_wayland:true});
 }
 finish(value){GLib.file_set_contents(`${GLib.getenv('VOCAL_MORE_COMPAT_OUTPUT')}/session.json`,JSON.stringify(value));}
 disable(){if(this._timer)GLib.source_remove(this._timer);this._timer=0;this._pointer=this._keyboard=null;}
}
