"""Durable run registry, event log, and permanent history."""

from . import config as _config


def ensure_dirs():
    from . import config as _config

    _config.os.makedirs(_config.RUNS_DIR, exist_ok=True)
    _config.os.makedirs(_config.LOGS_DIR, exist_ok=True)


def log_event(rec, event, **extra):
    """Append one line to the durable, append-only audit ledger. Every meaningful
    transition is checkpointed here so the full history of a run is recoverable
    even if the run JSON is later cleaned. Best-effort; never raises."""
    from . import config as _config

    try:
        _config.os.makedirs(_config.EVENTS_DIR, exist_ok=True)
        rid = rec["id"] if isinstance(rec, dict) else rec
        entry = {
            "ts": int(_config.time.time()),
            "run": rid,
            "event": event,
            "engine": (rec.get("engine") if isinstance(rec, dict) else None)
            or "claude",
            "account": _config.os.path.basename(rec.get("profile", ""))
            if isinstance(rec, dict)
            else None,
            "status": rec.get("status") if isinstance(rec, dict) else None,
            "attempt": rec.get("attempt") if isinstance(rec, dict) else None,
        }
        entry.update(extra)
        day = _config.time.strftime("%Y-%m-%d", _config.time.localtime())
        with open(_config.os.path.join(_config.EVENTS_DIR, day + ".jsonl"), "a") as f:
            f.write(_config.json.dumps(entry) + "\n")
    except (OSError, ValueError, KeyError):
        pass


def read_events(runid=None, limit=0):
    """All audit events (optionally for one run), oldest first."""
    from . import config as _config

    out = []
    if not _config.os.path.isdir(_config.EVENTS_DIR):
        return out
    for name in sorted(_config.os.listdir(_config.EVENTS_DIR)):
        if not name.endswith(".jsonl"):
            continue
        try:
            with open(_config.os.path.join(_config.EVENTS_DIR, name)) as f:
                for line in f:
                    try:
                        e = _config.json.loads(line)
                    except ValueError:
                        continue
                    if runid is None or e.get("run") == runid:
                        out.append(e)
        except OSError:
            continue
    return out[-limit:] if limit else out


def run_path(runid):
    from . import config as _config

    return _config.os.path.join(_config.RUNS_DIR, runid + ".json")


def save_run(rec):
    from . import config as _config
    from . import models as _models

    try:
        ensure_dirs()
        tmp = run_path(rec["id"]) + ".tmp"
        with open(tmp, "w") as f:
            _config.json.dump(rec, f, indent=1)
        _config.os.rename(tmp, run_path(rec["id"]))
    except OSError as e:
        _models.err(
            "neomax: WARNING could not persist run %s (%s) — recovery state may be incomplete"
            % (rec.get("id", "?"), e)
        )


def load_run(runid):
    from . import config as _config

    try:
        with open(run_path(runid)) as f:
            return _config.json.load(f)
    except (OSError, ValueError):
        return None


def all_runs():
    from . import config as _config

    ensure_dirs()
    out = []
    for name in sorted(_config.os.listdir(_config.RUNS_DIR)):
        if name.endswith(".json"):
            rec = load_run(name[:-5])
            if rec:
                out.append(rec)
    return out


def history_conn():
    """Open the history DB (WAL + busy timeout for concurrent finishers), creating
    the schema on first use. Returns None if sqlite is unavailable for any reason —
    history is best-effort and must NEVER break a run."""
    from . import config as _config

    try:
        _config.os.makedirs(_config.STATE_DIR, exist_ok=True)
        c = _config.sqlite3.connect(_config.HISTORY_DB, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=5000")
        c.execute(
            "CREATE TABLE IF NOT EXISTS runs(\n            id TEXT PRIMARY KEY, engine TEXT, account TEXT, acct_no INTEGER,\n            status TEXT, prompt TEXT, repo TEXT, branch TEXT, tag TEXT, goal TEXT,\n            effort TEXT, ultra INTEGER, opus INTEGER, model TEXT,\n            children INTEGER, attempt INTEGER, pr_url TEXT,\n            started INTEGER, ended INTEGER, archived_at INTEGER,\n            log_path TEXT, record TEXT)"
        )
        c.execute("CREATE INDEX IF NOT EXISTS runs_started ON runs(started DESC)")
        return c
    except Exception:
        return None


HISTORY_PENDING = _config.os.path.join(_config.STATE_DIR, "history-pending")


def archive_run(rec):
    """Permanently record a run in the history DB (idempotent upsert) and preserve its
    worker log under HISTORY_LOGS. Called at terminal finish AND before any deletion,
    so EVERY run we ever do is browsable forever — even after `clean` removes the live
    JSON. Best-effort: never raises."""
    from . import config as _config
    from . import models as _models
    from . import status_helpers as _status_helpers

    c = history_conn()
    if c is None:
        return
    try:
        log_path = None
        live_log = rec.get("log")
        if live_log:
            base = _config.os.path.basename(live_log).rsplit(".attempt", 1)[0]
            try:
                _config.os.makedirs(_config.HISTORY_LOGS, exist_ok=True)
                if _config.os.path.isdir(_config.LOGS_DIR):
                    for name in _config.os.listdir(_config.LOGS_DIR):
                        if name.startswith(base):
                            dst = _config.os.path.join(_config.HISTORY_LOGS, name)
                            if not _config.os.path.exists(dst):
                                _config.shutil.copy2(
                                    _config.os.path.join(_config.LOGS_DIR, name), dst
                                )
                            if name.endswith(".jsonl"):
                                log_path = dst
            except OSError:
                pass
        plist = _models.engine_profiles(rec.get("engine", "claude"))
        acct_no = (
            plist.index(rec["profile"]) + 1
            if rec.get("profile") in plist
            else _status_helpers.profile_acct_no(
                rec.get("profile", ""), rec.get("engine", "claude")
            )
        )
        c.execute(
            "INSERT INTO runs(id,engine,account,acct_no,status,prompt,repo,branch,\n            tag,goal,effort,ultra,opus,model,children,attempt,pr_url,started,ended,\n            archived_at,log_path,record) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)\n            ON CONFLICT(id) DO UPDATE SET status=excluded.status,children=excluded.children,\n            attempt=excluded.attempt,pr_url=excluded.pr_url,ended=excluded.ended,\n            archived_at=excluded.archived_at,log_path=COALESCE(excluded.log_path,runs.log_path),\n            record=excluded.record",
            (
                rec["id"],
                rec.get("engine", "claude"),
                _config.os.path.basename(rec.get("profile", "")),
                acct_no,
                rec.get("status"),
                (rec.get("prompt") or "")[:2000],
                _config.os.path.basename(rec.get("repo") or "") or None,
                rec.get("branch"),
                rec.get("tag"),
                rec.get("goal"),
                rec.get("effort"),
                1 if rec.get("ultra") else 0,
                1 if rec.get("opus") else 0,
                rec.get("model"),
                len(rec.get("children") or []),
                rec.get("attempt", 1),
                rec.get("pr_url"),
                rec.get("started", 0),
                rec.get("ended"),
                int(_config.time.time()),
                log_path,
                _config.json.dumps(rec),
            ),
        )
        c.commit()
    except _config.sqlite3.OperationalError:
        ok = False
        for delay in (0.2, 0.5, 1.0):
            _config.time.sleep(delay)
            try:
                c.execute("SELECT 1")
                ok = True
                break
            except Exception:
                pass
        try:
            c.close()
        except Exception:
            pass
        if ok:
            return archive_run(rec)
        try:
            _config.os.makedirs(HISTORY_PENDING, exist_ok=True)
            with open(
                _config.os.path.join(HISTORY_PENDING, rec["id"] + ".json"), "w"
            ) as f:
                _config.json.dump(rec, f)
            _models.err(
                "neomax: history DB locked — run %s spilled to history-pending (reconcile --heal will archive it)"
                % rec["id"]
            )
        except OSError:
            pass
        return
    except Exception:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass


def history_runs(limit=100, engine=None):
    """Most-recent-first list of archived runs (lightweight columns for the dashboard)."""
    from . import projects as _projects

    c = history_conn()
    if c is None:
        return []
    try:
        q = "SELECT id,engine,account,acct_no,status,prompt,branch,tag,goal,ultra,opus,effort,children,attempt,pr_url,started,ended,repo FROM runs"
        args = []
        if engine:
            q += " WHERE engine=?"
            args.append(engine)
        q += " ORDER BY COALESCE(ended,started) DESC LIMIT ?"
        args.append(limit)
        cols = [
            "id",
            "engine",
            "account",
            "acct_no",
            "status",
            "prompt",
            "branch",
            "tag",
            "goal",
            "ultra",
            "opus",
            "effort",
            "children",
            "attempt",
            "pr_url",
            "started",
            "ended",
            "repo",
        ]
        out = []
        for row in c.execute(q, args).fetchall():
            d = dict(zip(cols, row))
            d["project"] = _projects.project_of(d.get("repo"))
            d["goal"] = (d.get("goal") or "")[:300] or None
            d["tag"] = (d.get("tag") or "")[:120] or None
            d["prompt"] = (d.get("prompt") or "")[:160] or None
            out.append(d)
        return out
    except Exception:
        return []
    finally:
        try:
            c.close()
        except Exception:
            pass


def history_get(runid):
    """Full archived record + resolved log path for one run, or None."""
    from . import config as _config

    c = history_conn()
    if c is None:
        return None
    try:
        row = c.execute(
            "SELECT record,log_path,status FROM runs WHERE id=?", (runid,)
        ).fetchone()
        if not row:
            return None
        rec = _config.json.loads(row[0]) if row[0] else {}
        rec["_log_path"] = row[1]
        rec["_archived_status"] = row[2]
        return rec
    except Exception:
        return None
    finally:
        try:
            c.close()
        except Exception:
            pass


def pid_alive(pid):
    from . import config as _config

    if not pid:
        return False
    try:
        _config.os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False
