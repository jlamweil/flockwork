"""Contract for the /flockwork skill (C13) — the repo-local habitual-chat
entry. The skill is the human-facing surface that routes a "fix X; verify: ..."
chat message into the substrate's protocol. It must reference the SAME
invariants the canonical protocol carries (seed integrity, settle-before-stop)
+ the bootstrap + the debuggability/recovery path, so every surface teaches the
same honest loop. (The live "routes a chat message to the protocol" behavior is
C16.)
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[1]
SKILL = REPO / ".opencode" / "skills" / "flockwork" / "SKILL.md"


def test_skill_exists_with_valid_frontmatter():
    assert SKILL.is_file(), f"missing {SKILL}"
    text = SKILL.read_text()
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "the skill must open with YAML frontmatter"
    fm = m.group(1)
    assert re.search(r"^name:\s*flockwork\s*$", fm, re.MULTILINE)
    assert re.search(r"^description:\s*\S", fm, re.MULTILINE)


def test_skill_references_the_bootstrap_and_protocol():
    text = SKILL.read_text()
    assert "flockwork harness init" in text, "the bootstrap step must be taught"
    assert "flockwork harness protocol" in text, (
        "the skill must reference the canonical protocol source of truth")


def test_skill_carries_the_invariants_and_toolset():
    text = SKILL.read_text()
    for t in ("fw_brief", "fw_claim", "fw_judge", "fw_land", "fw_verdict",
              "fw_work_done"):
        assert t in text, f"the skill must name the {t} tool"
    assert "extract" in text.lower(), "seed integrity (extract the verify)"
    assert "never invent" in text.lower().replace("never\n", "never "), (
        "the skill must say never invent the verify")
    assert "before you stop" in text.lower(), "the settle-before-stop rule"


def test_skill_teaches_debuggability_and_honest_recovery():
    text = SKILL.read_text()
    assert "flockwork debug" in text, "the debug/timeline path must be taught"
    assert "restart" in text.lower(), "mid-task recovery must be mentioned"
    # recovery is human-driven, not automatic — the skill says so
    assert "do not imply" in text.lower() or "automatic" in text.lower()
