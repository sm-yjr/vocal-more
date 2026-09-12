#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""仅供测量：将现有 Python RPC 服务的数据工厂指向隔离目录。

不改产品源码，不重定义 HOME，不接触用户配置，不启动录音或云端请求。
"""
import os
from pathlib import Path
import runpy
import sys

base = Path(sys.argv[1]).resolve()
base.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
os.environ.pop("DASHSCOPE_API_KEY", None)

from vocal_more import paths
paths.default_data_dir = lambda: base
from vocal_more.config import Config
Config.get_config_dir = classmethod(lambda cls: base)
from vocal_more.core import recording_store

OriginalRecordingStore = recording_store.RecordingStore

class IsolatedRecordingStore(OriginalRecordingStore):
    def __init__(self, recordings_dir=None, **kwargs):
        super().__init__(recordings_dir=str(base / "recordings"), **kwargs)

recording_store.RecordingStore = IsolatedRecordingStore
runpy.run_module("vocal_more.serve", run_name="__main__")
