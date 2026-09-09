#!/usr/bin/env python3
"""Provision the Oracle AI Database for the workshop — run once in the Codespace / dev container.

This is a faithful, headless port of **Part 1** of ``total_recall_complete.ipynb``: it creates the
least-privilege ``AGENT`` schema with the grants the harness needs, then loads the 384-dim
in-database ONNX embedder (``ALL_MINILM_L12_V2``) **into the AGENT schema** so the appbook can warm
on startup. That is the *only* thing the app cannot build itself — everything else (tables, HNSW
indexes, the seeded commerce schema, the semantic catalog, the tool / skill registries) the appbook
creates idempotently the first time it connects, and the notebook builds as you work through it.

It makes **no** model/API calls, so it never needs an LLM key. Idempotent and retrying — safe
to run again any time (the user is created-if-absent; the model is loaded-if-absent).

Env (set by ``.devcontainer/docker-compose.yml``; sensible localhost defaults otherwise):
  ORA_DSN, ORA_ADMIN_USER, ORA_ADMIN_PWD, ORA_AGENT_USER, ORA_AGENT_PWD, EMBED_MODEL,
  EMBED_ONNX_URL, EMBED_ONNX_PATH
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import oracledb

# CLOB -> str, BLOB -> bytes (simpler, and avoids cursor-lifetime LOB issues).
oracledb.defaults.fetch_lobs = False

DSN = os.environ.get("ORA_DSN", "localhost:1521/FREEPDB1")
ADMIN_USER = os.environ.get("ORA_ADMIN_USER", "SYS")
ADMIN_PWD = os.environ.get("ORA_ADMIN_PWD", "OraclePwd_2025")
AGENT = os.environ.get("ORA_AGENT_USER", "AGENT")
AGENT_PWD = os.environ.get("ORA_AGENT_PWD", "AgentPw_2026")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "ALL_MINILM_L12_V2")
EMBED_ONNX_URL = os.environ.get(
    "EMBED_ONNX_URL",
    "https://objectstorage.us-ashburn-1.oraclecloud.com/n/adwc4pm/b/OML-Resources/o/all_MiniLM_L12_v2.onnx",
)
EMBED_ONNX_PATH = os.environ.get(
    "EMBED_ONNX_PATH", os.path.expanduser("~/oracle-models/all_MiniLM_L12_v2.onnx")
)

# The grants the harness needs (mirrors Part 1 of the notebook exactly).
GRANTS = [
    "GRANT CONNECT, RESOURCE TO {a}",
    "GRANT CREATE SESSION, CREATE TABLE, CREATE SEQUENCE, CREATE VIEW TO {a}",
    "GRANT CREATE PROCEDURE, CREATE MATERIALIZED VIEW TO {a}",
    "GRANT CREATE DOMAIN TO {a}",  # use-case domains (semantic layer)
    "GRANT CREATE MINING MODEL TO {a}",  # load ONNX models
    "GRANT CREATE JOB TO {a}",  # scheduled jobs / automations
    "GRANT EXECUTE ON DBMS_SCHEDULER TO {a}",
    "GRANT EXECUTE ON DBMS_VECTOR TO {a}",  # in-DB vectorisation
    "GRANT EXECUTE ON DBMS_VECTOR_CHAIN TO {a}",
    "GRANT SELECT_CATALOG_ROLE TO {a}",  # read the data dictionary
    "GRANT SELECT ON SYS.V_$SQL TO {a}",  # read the SQL workload
    "GRANT UNLIMITED TABLESPACE TO {a}",
]

# 'already exists' / 'does not exist' family — safe to swallow so the script is idempotent.
_IGNORE = ("ORA-00955", "ORA-01920", "ORA-00942", "ORA-01430", "ORA-02260", "ORA-00001")


def connect_with_retry(user, password, dsn, mode=None, attempts=30, base_delay=5.0):
    """Connect, retrying while the database warms up (it can take a minute on first boot)."""
    last = None
    for i in range(1, attempts + 1):
        try:
            kw = {"mode": mode} if mode is not None else {}
            return oracledb.connect(user=user, password=password, dsn=dsn, **kw)
        except Exception as e:  # noqa: BLE001
            last = e
            print(f"  …waiting for Oracle ({i}/{attempts}): {str(e).splitlines()[0]}")
            time.sleep(base_delay)
    raise SystemExit(f"Could not connect to Oracle at {dsn} as {user}: {last}")


def ddl_idempotent(conn, sql):
    """Run CREATE/DROP/GRANT DDL, swallowing the 'already exists' family of errors."""
    cur = conn.cursor()
    try:
        cur.execute(sql)
        conn.commit()
        return True
    except Exception as e:  # noqa: BLE001
        if any(code in str(e) for code in _IGNORE):
            return False
        raise
    finally:
        cur.close()


def is_model_loaded(conn, model_name):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM user_mining_models WHERE model_name = :m", {"m": model_name.upper()}
        )
        return cur.fetchone() is not None
    finally:
        cur.close()


def ensure_model_file(local_path, url):
    """Prefer a local cache; download from the bucket only if missing or truncated."""
    p = pathlib.Path(local_path)
    if p.exists() and p.stat().st_size > 1_000_000:
        return str(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    print(f"  downloading embedder ONNX from {url}")
    import requests  # local import so the script imports even without requests installed yet

    r = requests.get(url, stream=True, timeout=600)
    r.raise_for_status()
    with open(p, "wb") as f:
        for chunk in r.iter_content(1 << 20):
            f.write(chunk)
    return str(p)


def load_onnx_model(conn, model_name, onnx_path, metadata):
    if is_model_loaded(conn, model_name):
        print(f"  ONNX model already loaded: {model_name}")
        return
    with open(onnx_path, "rb") as f:
        blob = f.read()
    cur = conn.cursor()
    try:
        lob = conn.createlob(oracledb.DB_TYPE_BLOB)
        lob.write(blob)
        cur.execute(
            "BEGIN DBMS_VECTOR.LOAD_ONNX_MODEL(:name, :data, JSON(:meta)); END;",
            {"name": model_name, "data": lob, "meta": json.dumps(metadata)},
        )
        conn.commit()
    finally:
        cur.close()
    print(f"  loaded ONNX model: {model_name} ({len(blob) // (1024 * 1024)} MB)")


def main():
    print(f"▸ Provisioning Oracle at {DSN} (AGENT user + in-DB ONNX embedder)…")

    # 1) Create the AGENT user and apply the grants (as SYSDBA, in FREEPDB1).
    admin = connect_with_retry(ADMIN_USER, ADMIN_PWD, DSN, mode=oracledb.AUTH_MODE_SYSDBA)
    print(f"  admin connected — DB version {admin.version}")
    ddl_idempotent(admin, f'CREATE USER {AGENT} IDENTIFIED BY "{AGENT_PWD}"')
    for g in GRANTS:
        ddl_idempotent(admin, g.format(a=AGENT))
    print(f"  user {AGENT} ready ({len(GRANTS)} grants applied idempotently)")
    admin.close()

    # 2) As AGENT, load the embedder so the model is owned by the AGENT schema (the appbook's
    #    VECTOR_EMBEDDING(ALL_MINILM_L12_V2 ...) calls resolve to a model in its own schema).
    agent = connect_with_retry(AGENT, AGENT_PWD, DSN)
    print(f"  {AGENT} connected — loading the embedder")
    onnx_path = ensure_model_file(EMBED_ONNX_PATH, EMBED_ONNX_URL)
    load_onnx_model(
        agent,
        EMBED_MODEL,
        onnx_path,
        {"function": "embedding", "embeddingOutput": "embedding", "input": {"input": ["DATA"]}},
    )

    # 3) Prove it works end to end.
    cur = agent.cursor()
    cur.execute(f"SELECT VECTOR_EMBEDDING({EMBED_MODEL} USING 'hello world' AS DATA) FROM dual")
    dim = len(cur.fetchone()[0])
    cur.close()
    agent.close()
    print(f"\n✓ Database provisioned — '{EMBED_MODEL}' embeds to {dim} dims, entirely in-database.")
    print("  The appbook can now warm; the notebook will build the rest of the harness.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        print(f"\n✗ provisioning failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
