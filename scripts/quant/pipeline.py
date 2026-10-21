import argparse
from pathlib import Path
from scripts.quant.collect import collect
from scripts.quant.train import train

parser=argparse.ArgumentParser();parser.add_argument('--root',required=True);parser.add_argument('--end',required=True)
args=parser.parse_args();root=Path(args.root)
collect(root/'data',end=args.end)
train(root/'data',root/'output')
