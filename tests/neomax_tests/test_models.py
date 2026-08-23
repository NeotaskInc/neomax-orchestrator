"""Default-model policy and explicit local-model overrides."""

from .support import *


def test_default_model_switch():
    """Fable 5 is the Claude default; Opus 5 and every other CLI model are explicit opt-ins."""
    print("test_default_model_switch")
    check(
        m.CLAUDE_DEFAULT_MODEL == "claude-fable-5"
        and m.CLAUDE_DEFAULT_MODEL_1M == "claude-fable-5[1m]",
        "Claude Fable 5 is the explicit default",
    )
    check(
        m.CLAUDE_OPUS_MODEL_1M == "claude-opus-5[1m]"
        and m.CLAUDE_OPUS_MODEL != m.CLAUDE_DEFAULT_MODEL,
        "Claude Opus 5 is a separate explicit opt-in",
    )
    check(
        m.model_pricing("claude-fable-5")["out"] == 50.0
        and m.model_pricing("claude-opus-5")["out"] == 25.0
        and (m.model_pricing("claude-opus-4-8")["out"] == 25.0),
        "Claude model history remains priceable",
    )
    a = m.claude_args({"engine": "claude", "_prompt_to_send": "x", "session": "s"})
    check(
        a[a.index("--model") + 1] == m.CLAUDE_DEFAULT_MODEL_1M,
        "Claude workers explicitly pin Fable 5 by default",
    )
    for model in ("claude-opus-5[1m]", "claude-sonnet-4-6", "custom-local-claude"):
        a2 = m.claude_args(
            {"engine": "claude", "model": model, "_prompt_to_send": "x", "session": "s"}
        )
        check(
            a2[a2.index("--model") + 1] == model
            and m.resolve_claude_model(model) == model,
            "Claude passes explicit local CLI model %r unchanged" % model,
        )
