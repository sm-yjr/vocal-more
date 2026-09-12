#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""在 macOS 上测量完整 release 服务；本机 HTTP 夹具，无录音/云请求。"""
import argparse
import importlib.util
import json
from pathlib import Path
import queue
import re
import subprocess
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--binary',type=Path,default=ROOT/'.build/rust-host/vocal-more-backend')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--rounds',type=int,default=30)
    args=parser.parse_args()
    assert 1<=args.rounds<=100
    args.output.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('fixture',ROOT/'tests/test_rust_backend_integration.py')
    fixture=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    fixture.BINARY=args.binary.resolve()
    result={'scope':'full Rust application service; synthetic PCM and loopback HTTP; controller excluded','rounds':args.rounds,'samples':[]}
    def sample(client,label):
        output=subprocess.run(['/usr/bin/vmmap','-summary',str(client.pid)],capture_output=True,text=True,timeout=15,check=True).stdout
        (args.output/(label+'-vmmap.txt')).write_text(output)
        match=re.search(r'Physical footprint:\s*([\d.]+)([KMGT]?)',output)
        assert match
        result['samples'].append({'label':label,'physical_footprint_mib':float(match[1])*{'':1/1048576,'K':1/1024,'M':1,'G':1024,'T':1048576}[match[2]]})
    with tempfile.TemporaryDirectory(prefix='vocal-full-bench-') as temp, fixture.fixture_http() as (port,requests,*_):
        events=queue.Queue()
        with fixture.client(Path(temp),port,events.put) as client:
            client.call('initialize')
            client.call('set_asr_model',{'model':'qwen3-asr-flash'})
            client.call('set_config',{'key':'enable_polish','value':False})
            sample(client,'idle')
            for i in range(args.rounds):
                fixture.start_audio(client)
                fixture.wait_event(events,'final_result')
                paste=fixture.wait_event(events,'paste_requested')
                client.call('claim_paste',{'token':paste['token']})
                client.call('prepare_paste_observation',{'token':paste['token'],'snapshot':None})
                if i==0:sample(client,'after-first-session')
            time.sleep(.2)
            sample(client,'after-repeated-sessions')
            result['recordings']=len(client.call('list_recordings'))
            result['http_requests']=len(requests)
            result['state']=client.call('status')['state']
            assert result['state']=='idle' and len(requests)==args.rounds
        result['exit_code']=client._process.returncode
    (args.output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
