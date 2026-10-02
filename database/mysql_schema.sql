CREATE DATABASE IF NOT EXISTS finpulse_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE finpulse_dev;

CREATE TABLE IF NOT EXISTS users (
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    username VARCHAR(32) CHARACTER SET ascii COLLATE ascii_general_ci NOT NULL UNIQUE,
    nickname VARCHAR(40) NOT NULL,
    password_hash VARCHAR(255) CHARACTER SET ascii NOT NULL,
    is_active TINYINT NOT NULL DEFAULT 1,
    created_at BIGINT NOT NULL,
    last_login_at BIGINT NULL,
    role VARCHAR(10) CHARACTER SET ascii NOT NULL DEFAULT 'user',
    CHECK (is_active IN (0,1)), CHECK (role IN ('user','admin'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS sessions (
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    user_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    last_seen_at BIGINT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX sessions_user_idx(user_id), INDEX sessions_expiry_idx(expires_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS auth_rate_limits (
    bucket CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    window_start BIGINT NOT NULL,
    attempts INT NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS admin_audit (
    id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    actor_id CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    actor_name VARCHAR(32) NOT NULL,
    target_name VARCHAR(32) NOT NULL,
    action VARCHAR(40) NOT NULL,
    created_at BIGINT NOT NULL,
    INDEX admin_audit_time_idx(created_at)
) ENGINE=InnoDB;
