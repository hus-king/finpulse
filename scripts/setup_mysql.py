"""Provision the shared development database via SSH. Never prints passwords."""
import json
import secrets
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

from argon2 import PasswordHasher

ROOT = Path(__file__).resolve().parent.parent
ADMINS = ("hsq", "szy", "zhr", "lzk")
PRIVATE = ROOT / "data" / "mysql-admin-credentials.local.json"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-p", "11622", "airhust@airhust.cn"]


def mysql(sql):
    result = subprocess.run([*SSH, "sudo -n mysql -u root --batch --default-character-set=utf8mb4"], input=sql, text=True, encoding="utf-8", capture_output=True, timeout=60)
    if result.returncode:
        # MySQL errors can contain query text; do not echo password-bearing SQL.
        raise RuntimeError("MySQL setup failed; inspect server state before retrying (SQL/passwords suppressed).")
    return result.stdout


def quote(value):
    # Hex string literals avoid SQL quoting pitfalls for migrated names/hashes.
    return "CONVERT(0x" + str(value).encode("utf-8").hex() + " USING utf8mb4)" if str(value) else "''"


def main():
    ROOT.joinpath("data").mkdir(exist_ok=True)
    if PRIVATE.exists():
        raise RuntimeError("Credentials already exist: this initialization is not a password-reset command.")
    existing = mysql("SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME='finpulse_dev';")
    if "finpulse_dev" in existing:
        raise RuntimeError("finpulse_dev already exists; refusing to overwrite an existing project database.")
    local_users = []
    path = ROOT / "data" / "finpulse.db"
    if path.exists():
        with sqlite3.connect(path) as conn:
            conn.row_factory = sqlite3.Row
            local_users = [dict(row) for row in conn.execute("SELECT * FROM users")]
    existing_admins = {row["username"].lower() for row in local_users if row["username"].lower() in ADMINS}
    credentials = {
        "server": "airhust.cn", "ssh_port": 11622, "database": "finpulse_dev",
        "mysql_admins": {name: secrets.token_hex(20) for name in ADMINS},
        "website_admins": {name: None if name in existing_admins else secrets.token_urlsafe(20) for name in ADMINS},
        "application": {"username": "finpulse_app", "password": secrets.token_hex(24)},
    }
    # Save before provisioning so credentials are recoverable if a later step fails.
    with PRIVATE.open("x", encoding="utf-8") as handle:
        json.dump(credentials, handle, ensure_ascii=False, indent=2)
    sql = (ROOT / "database" / "mysql_schema.sql").read_text(encoding="utf-8")
    for name, password in credentials["mysql_admins"].items():
        sql += f"\nCREATE USER '{name}'@'127.0.0.1' IDENTIFIED BY '{password}';\n"
        sql += f"GRANT ALL PRIVILEGES ON finpulse_dev.* TO '{name}'@'127.0.0.1';\n"
    app_password = credentials["application"]["password"]
    sql += f"CREATE USER 'finpulse_app'@'127.0.0.1' IDENTIFIED BY '{app_password}';\n"
    sql += "GRANT SELECT,INSERT,UPDATE,DELETE ON finpulse_dev.* TO 'finpulse_app'@'127.0.0.1';\nSTART TRANSACTION;\n"
    hasher = PasswordHasher()
    for row in local_users:
        values = [quote(row[k]) for k in ("id", "username", "nickname", "password_hash")]
        role = "admin" if row["username"].lower() in ADMINS else "user"
        values += ["1" if role == "admin" else str(int(row["is_active"])), str(int(row["created_at"])), str(int(row["last_login_at"])) if row["last_login_at"] else "NULL", quote(role)]
        sql += "INSERT INTO users(id,username,nickname,password_hash,is_active,created_at,last_login_at,role) VALUES(" + ",".join(values) + ");\n"
    now = int(time.time())
    for name, password in credentials["website_admins"].items():
        if name in existing_admins:
            continue
        values = [quote(uuid.uuid4().hex), quote(name), quote(name.upper()), quote(hasher.hash(password)), "1", str(now), "NULL", quote("admin")]
        sql += "INSERT INTO users(id,username,nickname,password_hash,is_active,created_at,last_login_at,role) VALUES(" + ",".join(values) + ");\n"
    sql += "COMMIT;\nSELECT username,role,is_active FROM users ORDER BY username;\n"
    print(mysql(sql))
    config_path = ROOT / "config.local.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    config["database"] = {"engine": "mysql", "host": "127.0.0.1", "port": 13306, "name": "finpulse_dev", **credentials["application"]}
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = ["# FinPulse 本地账号密码（不要提交或公开分享）", "", "服务器：airhust.cn，SSH 端口：11622。数据库：finpulse_dev。", "", "网站密码与 MySQL 密码分别设置，两者互不通用。", "", "| 账号 | 网站管理员初始密码 | MySQL 项目管理员密码 |", "| --- | --- | --- |"]
    for name in ADMINS:
        website_password = credentials['website_admins'][name] or '沿用原有网站密码（本次未重置）'
        lines.append(f"| {name} | `{website_password}` | `{credentials['mysql_admins'][name]}` |")
    lines += ["", "登录网站后，可在账号菜单中选择‘修改网站密码’。MySQL 密码修改使用 ALTER USER，两者分别修改。", "", "FastAPI 运行账号为 finpulse_app，密码已写入 config.local.json；不需要在前端填写。", "", "该文件及 JSON 凭据都在 data/ 中，被 .gitignore 忽略。只向对应成员私下提供其自己的账号密码。"]
    (ROOT / "data" / "管理员账号密码.local.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(f"Migrated {len(local_users)} local users; created four website admins and four database admins. Credentials saved locally (not printed).")


if __name__ == "__main__":
    main()
