"""Offline paper replay, one process with a hard4GiB address-space budget."""
import argparse
import json
import os
from pathlib import Path
import resource
import sys

for name in ('OMP_NUM_THREADS','OPENBLAS_NUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):
    os.environ[name] = '1'
resource.setrlimit(resource.RLIMIT_AS, (4*1024**3, 4*1024**3))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.quant.n2_replay import replay
from backend.quant.n2_maintenance import verify_replay

parser = argparse.ArgumentParser(description=__doc__)
for name in ('base','overlay','start','end','owner-key','output'):
    parser.add_argument('--'+name, required=True)
args = parser.parse_args()
Path(args.output).parent.mkdir(parents=True, exist_ok=True)
result = replay(args.base, args.overlay, args.start, args.end, args.owner_key, output=args.output)
verify_replay(result)
path = Path(args.output)
path.parent.mkdir(parents=True, exist_ok=True)
temporary = path.with_suffix(path.suffix+'.tmp')
temporary.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',',':'))+'\n')
temporary.replace(path)
path.with_suffix('.progress.json').write_text(json.dumps(dict(status='complete', days=len(result['daily_nav']),
    trades=len(result['trades']), equity_fen=result['daily_nav'][-1]['equity_fen'], sha256=result['sha256']))+'\n')
print(json.dumps(dict(status='complete', output=str(path), trades=len(result['trades']), sha256=result['sha256'])),flush=True)
