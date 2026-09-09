#!/usr/bin/env bash
# Ensure Oracle's Vector Memory Pool is ALLOCATED so in-memory HNSW indexes can build.
#
# On the Free image VECTOR_MEMORY_SIZE defaults to 0, so the first HNSW build fails with
# ORA-51962 ("vector memory area is out of space"). Setting the parameter is NOT enough:
# going from 0 to non-zero only takes effect after a RESTART — the pool is sized at instance
# startup. (ALTER SYSTEM ... SCOPE=BOTH silently fails to allocate on the Free image, which is
# why the previous .sql hook left the pool at 0 and the very first index build blew up.)
#
# gvenzl/oracle-free runs files in /container-entrypoint-startdb.d/ as the oracle user (with
# ORACLE_SID/ORACLE_HOME set) on EVERY start, so this self-heals existing data volumes too. It is
# conditional and idempotent: it only sets the parameter + bounces the instance when the pool is not
# yet allocated, so steady-state starts are a no-op (no restart loop). Mounted executable, so gvenzl
# runs it as a subprocess rather than sourcing it.
#
# Idempotency check uses V$VECTOR_MEMORY_POOL.ALLOC_BYTES, NOT the parameter: on the Free image
# SHOW PARAMETER / v$parameter reports VECTOR_MEMORY_SIZE as 0 even after the pool is allocated
# ("phantom" memory), so keying off the parameter would restart on every single boot.
#
# 256M is ample for this workshop's HNSW data and stays well within the Free edition's SGA budget —
# oversizing (e.g. 1024M) starves the shared pool and triggers ORA-04031, making every query crawl.

POOL_MB=256

allocated=$(sqlplus -s -L / as sysdba <<'SQL' 2>/dev/null
set heading off feedback off pagesize 0 verify off echo off termout on
SELECT NVL(SUM(alloc_bytes), 0) FROM v$vector_memory_pool;
exit
SQL
)
allocated=$(printf '%s' "$allocated" | tr -dc '0-9')

if [ -z "$allocated" ] || [ "$allocated" = "0" ]; then
  echo "[vector-memory] Pool not allocated. Setting ${POOL_MB}M and restarting once…"
  sqlplus -s -L / as sysdba <<SQL
whenever sqlerror continue
alter system set vector_memory_size = ${POOL_MB}M scope=spfile;
shutdown immediate;
startup;
alter pluggable database all open;
exit
SQL
  echo "[vector-memory] Done — vector pool allocated (${POOL_MB}M). HNSW indexes can now build."
else
  echo "[vector-memory] Pool already allocated (${allocated} bytes). No restart needed."
fi
