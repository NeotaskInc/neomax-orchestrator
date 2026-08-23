"""Exact Claude and Codex patch extraction."""


def _tooluse_file_stats(line, stats):
    """Accumulate per-file line-add/del stats from a transcript line's Edit/Write/
    MultiEdit/NotebookEdit tool_use blocks (approximate: counts payload lines)."""
    from . import config as _config

    if '"tool_use"' not in line:
        return
    try:
        e = _config.json.loads(line)
    except ValueError:
        return
    content = (e.get("message") or {}).get("content")
    if not isinstance(content, list):
        return

    def lines_of(t):
        return str(t).count("\n") + 1 if t else 0

    for b in content:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        name = b.get("name")
        inp = b.get("input") or {}
        fp = inp.get("file_path") or inp.get("notebook_path")
        if not fp or name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            continue
        st = stats.setdefault(fp, {"adds": 0, "dels": 0, "ops": 0})
        st["ops"] += 1
        if name == "Write":
            st["adds"] += lines_of(inp.get("content"))
        elif name == "MultiEdit":
            for ed in inp.get("edits") or []:
                st["adds"] += lines_of((ed or {}).get("new_string"))
                st["dels"] += lines_of((ed or {}).get("old_string"))
        else:
            st["adds"] += lines_of(inp.get("new_string") or inp.get("new_source"))
            st["dels"] += lines_of(inp.get("old_string"))


def _input_exact_diff(files, line):
    """EXACT diffs from Edit/MultiEdit/Write tool_use INPUTS (verbatim old/new strings →
    difflib unified diff). Used where results carry no structuredPatch (sub-agent
    transcripts) — the inputs ARE the exact content applied."""
    from . import config as _config

    if '"tool_use"' not in line:
        return 0
    try:
        e = _config.json.loads(line)
    except ValueError:
        return 0
    content = (e.get("message") or {}).get("content")
    if not isinstance(content, list):
        return 0
    import difflib

    n = 0
    for b in content:
        if not isinstance(b, dict) or b.get("type") != "tool_use":
            continue
        (name, inp) = (b.get("name"), b.get("input") or {})
        fp = inp.get("file_path") or inp.get("notebook_path")
        if not fp or name not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
            continue
        ent = files.setdefault(fp, {"adds": 0, "dels": 0, "hunks": [], "src": "input"})
        pairs = (
            [
                (ed.get("old_string") or "", ed.get("new_string") or "")
                for ed in inp.get("edits") or []
            ]
            if name == "MultiEdit"
            else [("", inp.get("content") or "")]
            if name == "Write"
            else [
                (
                    inp.get("old_string") or "",
                    inp.get("new_string") or inp.get("new_source") or "",
                )
            ]
        )
        for old_s, new_s in pairs:
            n += 1
            ud = list(
                difflib.unified_diff(
                    old_s.split("\n"), new_s.split("\n"), lineterm="", n=2
                )
            )
            body = "\n".join(ud[2:]) if len(ud) > 2 else ""
            if body:
                ent["hunks"].append(body)
                ent["adds"] += sum((1 for line in ud[2:] if line.startswith("+")))
                ent["dels"] += sum((1 for line in ud[2:] if line.startswith("-")))
    return n


def extract_claude_exact_patches(transcript_path, max_bytes=8 * 1024 * 1024):
    """EXACT per-file diffs from a Claude Code transcript: every Edit/MultiEdit result
    carries toolUseResult.structuredPatch (true unified-diff hunks: oldStart/oldLines/
    newStart/newLines/lines) and every Write carries the created content. Returns
    {files: {path: {adds, dels, hunks:[...unified text...]}}, n_edits}. Identical to
    what the CLI applied — no approximation."""
    from . import config as _config

    files = {}
    input_files = {}
    n = 0
    n_in = 0
    read = 0
    try:
        with open(transcript_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                read += len(line)
                if read > max_bytes:
                    break
                n_in += _input_exact_diff(input_files, line)
                if '"toolUseResult"' not in line:
                    continue
                if '"structuredPatch"' not in line and '"create"' not in line:
                    continue
                try:
                    e = _config.json.loads(line)
                except ValueError:
                    continue
                tr = e.get("toolUseResult")
                if not isinstance(tr, dict):
                    continue
                fp = tr.get("filePath")
                if not fp:
                    continue
                ent = files.setdefault(fp, {"adds": 0, "dels": 0, "hunks": []})
                sp = tr.get("structuredPatch")
                if sp:
                    n += 1
                    for h in sp:
                        lines = h.get("lines") or []
                        ent["adds"] += sum(
                            (1 for line in lines if line.startswith("+"))
                        )
                        ent["dels"] += sum(
                            (1 for line in lines if line.startswith("-"))
                        )
                        ent["hunks"].append(
                            "@@ -%s,%s +%s,%s @@\n%s"
                            % (
                                h.get("oldStart"),
                                h.get("oldLines"),
                                h.get("newStart"),
                                h.get("newLines"),
                                "\n".join(lines),
                            )
                        )
                elif tr.get("type") == "create" and tr.get("content") is not None:
                    n += 1
                    content = str(tr["content"])
                    clines = content.split("\n")
                    ent["adds"] += len(clines)
                    ent["hunks"].append(
                        "@@ -0,0 +1,%d @@ (file created)\n%s"
                        % (
                            len(clines),
                            "\n".join(("+" + line for line in clines[:2000])),
                        )
                    )
    except OSError:
        pass
    for fp, ent in input_files.items():
        if fp not in files:
            files[fp] = ent
    return {"files": files, "n_edits": n or n_in}


def extract_codex_exact_patches(rollout_path, max_bytes=16 * 1024 * 1024):
    """EXACT patches from a Codex rollout: every apply_patch custom_tool_call carries the
    complete '*** Begin Patch' envelope VERBATIM (what the CLI applied); patch_apply_end
    confirms success per call_id. Returns {files:{path:{adds,dels,hunks}}, n_edits}."""
    from . import config as _config

    files = {}
    n = 0
    applied = set()
    pending = {}
    read = 0
    try:
        with open(rollout_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                read += len(line)
                if read > max_bytes:
                    break
                if "apply_patch" not in line and '"patch_apply_end"' not in line:
                    continue
                try:
                    e = _config.json.loads(line)
                except ValueError:
                    continue
                pl = e.get("payload") or {}
                t = pl.get("type")
                if t in ("custom_tool_call", "function_call") and (
                    pl.get("name") == "apply_patch"
                    or "apply_patch" in str(pl.get("name"))
                ):
                    pending[pl.get("call_id")] = str(
                        pl.get("input") or pl.get("arguments") or ""
                    )
                elif t == "patch_apply_end" and pl.get("success"):
                    applied.add(pl.get("call_id"))
        for cid in applied:
            envelope = pending.get(cid)
            if not envelope:
                continue
            n += 1
            cur = None
            for ln in envelope.split("\n"):
                m = _config.re.match("\\*\\*\\* (Add|Update|Delete) File: (.+)$", ln)
                if m:
                    cur = files.setdefault(
                        m.group(2).strip(), {"adds": 0, "dels": 0, "hunks": []}
                    )
                    cur["hunks"].append("*** %s File ***" % m.group(1))
                    continue
                if cur is None:
                    continue
                if ln.startswith("+"):
                    cur["adds"] += 1
                elif ln.startswith("-"):
                    cur["dels"] += 1
                if cur["hunks"]:
                    cur["hunks"][-1] += "\n" + ln
    except OSError:
        pass
    return {"files": files, "n_edits": n}
