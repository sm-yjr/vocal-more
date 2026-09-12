#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""真实 AppKit/WKWebView 集成验收；使用隔离数据且不启动快捷键/麦克风。

运行：uv run python rust/tools/check_macos_frontend.py --output .build/rust-ui-acceptance
在已有 macOS 桌面会话中短暂打开原设置窗口，验证页面到 Rust 的往返、
两种外观、关闭前表单保存，并分别测量薄 UI 和完整 Rust 子进程。
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

from AppKit import NSApplication, NSAppearance, NSBitmapImageRep, NSPNGFileType
from Foundation import NSDate, NSRunLoop
from vocal_more.rust_ui import RustVocalMoreApp


def pump(seconds):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(.02))


def evaluate(webview, expression):
    results=[]
    webview.evaluateJavaScript_completionHandler_(expression,lambda value,error:results.append((value,error)))
    deadline=time.monotonic()+5
    while not results and time.monotonic()<deadline:
        pump(.02)
    assert results and results[0][1] is None, results
    return results[0][0]


def snapshot_image(webview, path):
    done=[]
    def complete(image,error):
        if error:
            done.append(str(error))
            return
        bitmap=NSBitmapImageRep.imageRepWithData_(image.TIFFRepresentation())
        data=bitmap.representationUsingType_properties_(NSPNGFileType,{})
        done.append(None if data.writeToFile_atomically_(str(path),True) else 'PNG write failed')
    webview.takeSnapshotWithConfiguration_completionHandler_(None,complete)
    deadline=time.monotonic()+5
    while not done and time.monotonic()<deadline:
        pump(.02)
    assert done == [None], done


def measure(pid, output, label):
    result=subprocess.run(['/usr/bin/vmmap','-summary',str(pid)],capture_output=True,text=True,timeout=15,check=True)
    (output/(label+'-vmmap.txt')).write_text(result.stdout)
    match=re.search(r'Physical footprint:\s*([\d.]+)([KMGT]?)',result.stdout)
    assert match, result.stdout[:150]
    return {'pid':pid,'physical_footprint_mib':float(match[1])*{'':1/1048576,'K':1/1024,'M':1,'G':1024,'T':1048576}[match[2]]}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--binary',type=Path,default=Path('.build/rust-host/vocal-more-backend'))
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    NSApplication.sharedApplication()
    results={'test_sources':'isolated config and dictionary; no microphone or cloud requests','measurements':{}}
    with tempfile.TemporaryDirectory(prefix='vocal-rust-ui-') as directory:
        start=time.monotonic()
        app=RustVocalMoreApp(args.binary,Path(directory),args.binary.parent/'libvocal_more_audio.dylib',no_hotkeys=True)
        results['initialize_ms']=(time.monotonic()-start)*1000
        try:
            pump(1)
            results['measurements']['idle_ui']=measure(os.getpid(),args.output,'idle-ui')
            results['measurements']['idle_backend']=measure(app.client.pid,args.output,'idle-backend')
            app.client.call('set_config',{'key':'ui.onboarding_completed','value':True})
            app.client.call('set_config',{'key':'ui.advanced_settings','value':True})
            app.show_settings()
            pump(2)
            webview=app._settings._webview
            assert evaluate(webview,'document.querySelectorAll("[role=tab]").length')==7
            # Send the real webkit settings envelope through the installed JS bridge.
            evaluate(webview,'window.webkit.messageHandlers.settings.postMessage({action:"addDictEntry",term:"Rust",aliases:["rust"]}); null')
            evaluate(webview,'window.webkit.messageHandlers.settings.postMessage({action:"setConfig",key:"api_key",value:"synthetic-ui-key"}); null')
            pump(.3)
            assert app.client.call('get_dictionary')==[{'term':'Rust','aliases':['rust']}]
            assert app.client.call('snapshot')['api_key_set']
            assert evaluate(webview,'collectFormState().api_key')==''
            evaluate(webview,'document.querySelectorAll("[role=tab]")[0].click(); null')
            for appearance in ('Aqua','DarkAqua'):
                chosen=NSAppearance.appearanceNamed_(appearance)
                NSApplication.sharedApplication().setAppearance_(chosen)
                app._settings._window.setAppearance_(chosen)
                webview.setAppearance_(chosen)
                pump(.4)
                actual=evaluate(webview,'JSON.stringify({dark:matchMedia("(prefers-color-scheme: dark)").matches,classes:document.documentElement.className,theme:localStorage.getItem("vocal-more-settings-theme")})')
                results.setdefault('appearances',{})[appearance]=json.loads(actual)
                # Local AppKit appearance overrides do not reliably change
                # WebKit matchMedia on every macOS release. Render the existing
                # theme CSS explicitly, without changing OS/user preferences.
                theme='dark' if appearance=='DarkAqua' else 'light'
                evaluate(webview,'document.documentElement.classList.remove("light","dark"); document.documentElement.classList.add('+json.dumps(theme)+'); null')
                pump(.1)
                results['appearances'][appearance]['rendered_css_theme']=theme
                snapshot_image(webview,args.output/(appearance+'.png'))
            results['measurements']['settings_ui']=measure(os.getpid(),args.output,'settings-ui')
            results['measurements']['settings_backend']=measure(app.client.pid,args.output,'settings-backend')
            # A local form edit must survive close even without an immediate message.
            evaluate(webview,'window.updateConfig("llm.tone","gentle"); null')
            app._settings._on_window_close_requested()
            pump(.3)
            assert app.client.call('get_config')['llm']['tone']=='gentle'
            assert app._settings._webview is None
            pump(1)
            results['measurements']['after_close_ui']=measure(os.getpid(),args.output,'after-close-ui')
            results['measurements']['after_close_backend']=measure(app.client.pid,args.output,'after-close-backend')
            # Reopen onboarding with the masked, configured key and confirm it
            # remains eligible to proceed without revealing the saved secret.
            app.client.call('set_config',{'key':'ui.onboarding_completed','value':False})
            app.show_settings()
            pump(1)
            assert app.client.call('snapshot')['api_key_set']
            assert evaluate(app._settings._webview,'collectFormState().api_key')==''
            results['forbidden_imports']=[name for name in ('numpy','sounddevice','dashscope','vocal_more.app','vocal_more.core.asr_engine','vocal_more.core.audio_recorder') if name in sys.modules]
            assert not results['forbidden_imports']
            results['checks']=['seven existing tabs','WKWebView action -> Rust dictionary persistence','masked configured API key','light and dark CSS rendering (local override)','close flushes unsent form state','reopen onboarding','no Python provider/audio imports']
        finally:
            start=time.monotonic()
            app.close()
            results['shutdown_ms']=(time.monotonic()-start)*1000
            results['backend_exit_code']=app.client._process.returncode
            assert results['backend_exit_code']==0
        results['webkit_helpers']='The table reports the two owned main processes separately; shared WebKit helper processes are not attributed or included.'
        (args.output/'result.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
        print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
