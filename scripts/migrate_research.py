"""Add research storage on the configured local DB or the established SSH MySQL host.

MySQL runtime credentials remain CRUD-only. DDL is executed by sudo mysql over SSH.
"""
import argparse
import re
import subprocess
from pathlib import Path

from backend.database import create_auth_store
from backend.research_store import ResearchStore

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ssh-host', default='airhust@airhust.cn')
    parser.add_argument('--ssh-port', type=int, default=11622)
    args = parser.parse_args()
    store = create_auth_store(ROOT)
    if store.dialect == 'mysql':
        name = store.config.get('name', 'finpulse_dev')
        if not re.fullmatch(r'[A-Za-z0-9_]+', name):
            raise ValueError('Invalid database name')
        schema = (ROOT / 'database/mysql_schema.sql').read_text(encoding='utf-8')
        sql = f'USE `{name}`;\n' + schema[schema.index('CREATE TABLE IF NOT EXISTS research_records'):]
        subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-p', str(args.ssh_port), args.ssh_host, 'sudo -n mysql'], input=sql, text=True, encoding='utf-8', check=True)
    else:
        store.initialize()
    ResearchStore(store).initialize()
    print('Research schema is ready; existing accounts and data were preserved.')


if __name__ == '__main__':
    main()
