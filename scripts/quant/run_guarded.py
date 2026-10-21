"""Launch only our own task tree with CPU affinity and resource watchdog."""
import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path


def snapshot(group):
    memory=0;pids=[]
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit(): continue
        try:
            pid=int(entry.name)
            if os.getpgid(pid)!=group: continue
            status=(entry/'status').read_text()
            rss=next((int(line.split()[1])*1024 for line in status.splitlines() if line.startswith('VmRSS:')),0)
            memory+=rss;pids.append(pid)
        except (OSError,ProcessLookupError): continue
    return memory,pids


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args();root=Path(args.root).resolve()
    if not args.command or shutil.disk_usage(root).free<20*1024**3: raise RuntimeError('磁盘余量不足或命令为空')
    env={**os.environ,**{k:'1' for k in ['OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS']}}
    def setup():
        os.setsid();os.nice(19);os.sched_setaffinity(0,{max(os.sched_getaffinity(0))})
    process=subprocess.Popen(args.command,env=env,preexec_fn=setup)
    subprocess.run(['ionice','-c','3','-p',str(process.pid)],check=True)
    stopped=False
    def stop(*_):
        nonlocal stopped
        stopped=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    started=time.monotonic();peak=0;peak_gpu=0;reason=None;last_io=0;size=0
    try:
        while process.poll() is None:
            memory,pids=snapshot(process.pid);peak=max(peak,memory)
            if time.monotonic()-last_io>5:
                size=sum(p.stat().st_size for p in root.rglob('*') if p.is_file());last_io=time.monotonic()
            gpu=0
            try:
                query=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_memory','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5)
                for line in query.stdout.splitlines():
                    pid,mib=line.split(',')
                    if int(pid) in pids: gpu+=int(mib)*1024**2
            except (ValueError,subprocess.TimeoutExpired): pass
            peak_gpu=max(peak_gpu,gpu)
            load=os.getloadavg()[0]
            if stopped: reason='interrupted'
            elif memory>3*1024**3: reason='RAM limit'
            elif gpu>3*1024**3: reason='GPU limit'
            elif size>2*1024**3: reason='directory limit'
            elif shutil.disk_usage(root).free<20*1024**3: reason='disk reserve'
            elif time.monotonic()-started>3600: reason='runtime limit'
            elif load>os.cpu_count()*.8: reason='host busy'
            if reason: break
            time.sleep(1)
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGTERM)
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired: os.killpg(process.pid,signal.SIGKILL);process.wait()
        (root/'resources.json').write_text(json.dumps({'pid':process.pid,'reason':reason,'exit_code':process.returncode,
            'peak_ram_mib':round(peak/1024**2,2),'peak_gpu_mib':round(peak_gpu/1024**2,2),
            'seconds':round(time.monotonic()-started,2),'cpu_affinity_count':1,'nice':19,'limits':'watchdog checks once per second; 3GiB RAM/GPU, 2GiB disk, 1h'},indent=2))
    return process.returncode if not reason else 1

if __name__=='__main__':sys.exit(main())
