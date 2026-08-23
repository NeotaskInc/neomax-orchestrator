"""Rotation command parsing and dispatch."""


def _parse_account_selectors(tokens):
    """Loose CLI tokens → clean account selectors for /rotate's target list. Skips noise words
    ('account', 'to', 'and'), maps number-words ('one'->'1'), and unwraps 'acct2'/'account2'
    forms. Passes numbers, 'orch', and paths through untouched (paths keep their case)."""
    from . import config as _config
    from . import rotation as _rotation

    out = []
    for t in tokens:
        s = str(t).strip().rstrip(",")
        low = s.lower()
        if not low or low in ("account", "accounts", "acct", "accts", "to", "and"):
            continue
        if low in _rotation._NUMWORDS:
            out.append(_rotation._NUMWORDS[low])
            continue
        mwrap = _config.re.match("^(?:account|acct)[-_]?(\\d+)$", low)
        if mwrap:
            out.append(mwrap.group(1))
            continue
        out.append(s)
    return out


def cmd_session_rotate(argv):
    """/rotate engine: rotate THIS session's account NOW (optionally to a SPECIFIC account), and
    optionally ARM hands-off auto-rotation driven by the per-turn Stop hook (no background poller).
       neomax session-rotate [<acct>...] [--threshold N] [--arm] [--dry-run]
    With no <acct> it moves to the most-available account now. With one or more <acct> (e.g. `2` or
    `acct3 1`), it rotates NOW to the first usable named account — use this to deliberately land on a high-weekly
    account and burn its remaining weekly allowance before it resets."""
    from . import rotation as _rotation
    from . import rotation_state as _rotation_state
    from . import solo as _solo

    threshold = _solo.SOLO_5H_ROTATE
    positionals = []
    skip = False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a == "--threshold" and i + 1 < len(argv):
            try:
                threshold = float(argv[i + 1])
            except ValueError:
                pass
            skip = True
        elif a == "--to" and i + 1 < len(argv):
            positionals.append(argv[i + 1])
            skip = True
        elif a == "--arm":
            continue
        elif a.startswith("-"):
            continue
        else:
            positionals.append(a)
    prefer = _parse_account_selectors(positionals)
    profile = _rotation_state._current_session_profile()
    msg = _rotation.session_rotate(
        profile=profile,
        threshold=threshold,
        prefer=prefer or None,
        force=True,
        dry_run="--dry-run" in argv,
    )
    print("rotate: " + msg)
    if "--arm" in argv:
        _rotation_state.set_armed_rotate(profile, threshold, prefer)
        tgt = " (preferring %s)" % ", ".join(prefer) if prefer else ""
        print(
            "rotate: ARMED — this session auto-rotates at %.0f%% 5h%s on each turn-end (hands-off, no /login, no background polling). Walk away; work continues."
            % (threshold, tgt)
        )


def cmd_rotate(argv):
    """Provider-neutral `/rotate` entrypoint.

    Claude can exchange credentials underneath the current process, so it keeps the existing
    in-place swap/arm behavior. Codex, OpenCode, Kimi, and Grok keep credentials isolated; for
    them rotation is a clean, same-provider orchestrator handoff to another authenticated profile.
    The active launcher exports NEOMAX_ROLE, while provider-native workflows also pass --engine so
    the command remains unambiguous in default-profile sessions.
    """
    from . import config as _config
    from . import handoff as _handoff
    from . import models as _models

    engine = _handoff.orchestrator_engine()
    cleaned = []
    skip = False
    for i, value in enumerate(argv):
        if skip:
            skip = False
            continue
        if value == "--engine":
            if i + 1 >= len(argv) or argv[i + 1] not in _models.ENGINES:
                _models.err(
                    "neomax rotate: --engine must be claude, codex, opencode, kimi, or grok"
                )
                _config.sys.exit(2)
            engine = argv[i + 1]
            skip = True
            continue
        cleaned.append(value)
    if engine == "claude":
        cmd_session_rotate(cleaned)
        return
    positionals = []
    skip = False
    for i, value in enumerate(cleaned):
        if skip:
            skip = False
            continue
        if value in ("--threshold", "--prompt", "--reason") and i + 1 < len(cleaned):
            skip = True
        elif value == "--to" and i + 1 < len(cleaned):
            positionals.append(cleaned[i + 1])
            skip = True
        elif not value.startswith("-"):
            positionals.append(value)
    handoff_args = ["--engine", engine, "--reason", "manual /rotate"]
    for selector in _parse_account_selectors(positionals):
        handoff_args += ["--to", selector]
    for flag in ("--check", "--json", "--dry-run"):
        if flag in cleaned:
            handoff_args.append(flag)
    _handoff.cmd_handoff(handoff_args)


def _profile_recently_active(profile, within_s=900):
    """Any Claude transcript under this profile written within `within_s` (the session is still
    alive/working). Used so an armed /rotate monitor exits once the session goes idle/gone."""
    from . import config as _config

    now = _config.time.time()
    for f in _config.globmod.iglob(
        _config.os.path.join(profile, "projects", "*", "*.jsonl")
    ):
        try:
            if now - _config.os.path.getmtime(f) <= within_s:
                return True
        except OSError:
            continue
    return False
