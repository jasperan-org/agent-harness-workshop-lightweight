> **Advanced reference:** This is an advanced reference for the original self-contained workshop build. The current lightweight AppBook uses an `agent_scratch` SecureFile LOB table for scratch content; it does not require Oracle DBFS or add another canonical TODO.

# Part 4: DBFS Scratchpad

[Oracle DBFS (Database File System)](https://docs.oracle.com/en/database/oracle/oracle-database/26/adlob/database-filesystem-DBFS-intro.html) is a POSIX-like filesystem layered on SecureFile LOBs in a table. The agent sees files and directories; the database sees rows. Same backups, same audit, same security model as everything else in the harness — but with `open()`/`read()`/`write()` ergonomics.

## What you need to provision

DBFS is **not** part of the default Codespace bootstrap — `scripts/seed_oracle.py` provisions the schema, vector pool, ONNX models and memory tables only. To work through this Part, run this once as an admin connection (every statement is skippable if the object already exists):

```sql
-- 1. tablespace. `DATAFILE SIZE …` (no path) needs DB_CREATE_FILE_DEST/OMF, which the Free image
--    usually leaves unset — so name the file. <datafile_dir> comes from
--    SELECT name FROM v$datafile WHERE rownum = 1  (typically .../oradata/FREEPDB1/).
CREATE TABLESPACE AGENT_DBFS_TS
  DATAFILE '<datafile_dir>/agent_dbfs01.dbf' SIZE 100M AUTOEXTEND ON NEXT 50M MAXSIZE 2G;
ALTER USER AGENT QUOTA UNLIMITED ON AGENT_DBFS_TS;

-- 2. store + mount
BEGIN
  DBMS_DBFS_SFS.CREATEFILESYSTEM(store_name => 'AGENT_SCRATCH', tbl_name => 'AGENT_SCRATCH_T',
                                 tbl_tbs => 'AGENT_DBFS_TS', use_bf => FALSE);
  DBMS_DBFS_CONTENT.REGISTERSTORE(store_name => 'AGENT_SCRATCH', provider_name => 'sample1',
                                  provider_package => 'DBMS_DBFS_SFS');
  DBMS_DBFS_CONTENT.MOUNTSTORE(store_name => 'AGENT_SCRATCH', store_mount => 'scratch');
END;
/

-- 3. grants
GRANT EXECUTE ON DBMS_DBFS_CONTENT TO AGENT;
GRANT EXECUTE ON DBMS_DBFS_SFS TO AGENT;
GRANT DBFS_ROLE TO AGENT;
```

The three PL/SQL calls are re-runnable: `ORA-00955`, `ORA-64007`, `ORA-64008` and the unique-constraint error on `DBFS$_STORES`/`DBFS$_MOUNTS` all just mean "already there".

Once the store is mounted, the notebook wraps the PL/SQL `PUTPATH` / `GETPATH` calls behind a Python class so the rest of the harness can use `read` / `write` / `append` semantics.

## Why a filesystem at all?

Three reasons:

1. **Mid-task scratch.** "Write a SQL draft, read it back, edit, run it" is a filesystem workload, not OLTP.
2. **Path-addressable handles.** Tools can pass `/scratch/draft.sql` between calls without serializing a row id.
3. **Cheap rewrite.** Overwriting a CLOB row works but isn't idiomatic; DBFS gives you file semantics directly.

We use DBFS only as the agent's scratchpad — the long-term, search-heavy data stays in Part 2's OAMP tables.

## The `DBFS` Python wrapper

Minimal file-like wrapper. Only the methods the agent uses:

| Method | What it does | Use when |
|---|---|---|
| `write(path, content)` | Create-or-overwrite the file at `path`. | SQL drafts, plan revisions — *"latest is the truth"* |
| `append(path, content)` | Append to `path`, create if missing. | Running findings logs, transcripts |
| `read(path)` | Read the bytes back as a string. | Reading scratch before passing to `run_sql` |
| `list(path)` | Enumerate files under `path`. | Inspecting state |

The agent uses these via three pre-registered tools: `scratch_write`, `scratch_append`, `scratch_read`.

## Why not just use the kernel's `/tmp`?

Because we want the scratchpad to live inside the database:

- It survives container rebuilds (as long as the datafile survives).
- It's inside the same transactional boundary as OAMP's memory tables.
- In an enterprise deployment it's covered by the same backups, replication, and audit as the rest of the database.

No separate filesystem to secure.

## Key Takeaways — Part 4

- **The scratchpad is a real filesystem.** DBFS persists across tool calls AND across turns on the same thread.
- **`scratch_write` for drafts, `scratch_append` for logs.** Write replaces (SQL drafts, plan revisions); append grows (findings logs, transcripts).
- **BATCH your appends.** One `scratch_append` per row of data is wasteful and burns the iteration budget. Combine many rows into one call.
- **Multi-step reasoning without context bloat.** The agent reasons over a long task by *writing* intermediate state to the scratchpad, not by inflating the prompt.

## Troubleshooting

**`ORA-64001: path not found`** — File doesn't exist. Either `scratch.write` it first or catch `FileNotFoundError`.

**`ORA-22288: file or LOB operation FILEOPEN failed`** — The DBFS store isn't mounted. Mount the store — step 2 of *What you need to provision* above.

**`PLS-00306: wrong number or types of arguments in call to PUTPATH`** — Wrong Oracle DBFS version. Ensure you're on Oracle 23ai / 26ai.
