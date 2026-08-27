#!/usr/bin/env python3
"""Test suite for ste_style_check.py. Run: python3 test_ste_style_check.py"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(HERE, "ste_style_check.py")
sys.path.insert(0, HERE)
import ste_style_check as m

# 8 clean sentences, 10 words each; clears the 280-char / 3-sentence floor
FILLER = "The linter checks each sentence for length and bad words. " * 8


def run_hook(stdin_text, state_dir=None, mode=None):
    env = dict(os.environ)
    env["STE_MODE"] = mode or "feedback"  # pin: a machine-level mode file must not leak in
    if state_dir:
        env["STE_STATE_DIR"] = state_dir
    p = subprocess.run([sys.executable, HOOK], input=stdin_text, env=env,
                       capture_output=True, text=True, timeout=20)
    return p.returncode, p.stdout.strip()


def entry(text, side=False):
    row = {"type": "assistant",
           "message": {"role": "assistant",
                       "content": [{"type": "text", "text": text}]}}
    if side:
        row["isSidechain"] = True
    return json.dumps(row)


def transcript(tmp, lines):
    path = os.path.join(tmp, "t.jsonl")
    with open(path, "w") as f:
        f.write("not json at all\n")  # malformed line must be skipped
        for l in lines:
            f.write(l + "\n")
    return path


def ups(tmp, lines, sid="s1"):
    """Build a transcript and the UserPromptSubmit stdin payload for it."""
    return json.dumps({"hook_event_name": "UserPromptSubmit",
                       "transcript_path": transcript(tmp, lines),
                       "session_id": sid})


def statedir(tmp):
    return os.path.join(tmp, "state")


# ---- lint(): floors ----

def test_floor_short_text():
    assert m.lint("Too short.") == []

def test_floor_few_sentences():
    assert m.lint(("word " * 60).strip() + ". " + ("word " * 60).strip() + ".") == []

def test_clean_prose_passes():
    assert m.lint(FILLER) == []

# ---- lint(): banned words ----

def test_banned_bare_terms():
    for term in ["genuinely", "honestly", "leverage", "under the hood"]:
        got = m.lint(FILLER + f"This {term} helps here.")
        assert any(term in x for x in got), (term, got)

def test_banned_inflections():
    assert m.lint(FILLER + "We are leveraging the cache now.")
    assert m.lint(FILLER + "The tool utilizes three cores.")

def test_word_boundary_no_substring_hit():
    assert m.lint(FILLER + "The delvetool binary runs fast.") == []

def test_hyphen_compound_not_flagged():
    assert m.lint(FILLER + "This is the highest-leverage change on the list.") == []
    assert m.lint(FILLER + "We can leverage the cache for this purpose.")

def test_mention_double_quotes_clean():
    assert m.lint(FILLER + 'The rule bans "honestly" and "rabbit hole" here.') == []

def test_mention_backticks_clean():
    assert m.lint(FILLER + "The rule bans `honestly` and `rabbit hole` here.") == []

def test_mention_curly_quotes_clean():
    assert m.lint(FILLER + "The rule bans “honestly” in chat text.") == []

def test_many_banned_all_reported():
    text = FILLER + ("It genuinely and honestly can utilize, leverage, delve, "
                     "kicks in, shines, and is a breeze.")
    assert len(m.lint(text)) == 8, m.lint(text)

# ---- lint(): markdown stripping ----

def test_code_fence_stripped():
    assert m.lint("```\nhonestly = 1  # " + "word " * 50 + "\n```\n" + FILLER) == []

def test_inline_code_stripped():
    assert m.lint(FILLER + "Set `honestly=True` in the config file.") == []

def test_table_rows_skipped():
    assert m.lint(FILLER + "\n| col | honestly " + "word " * 40 + " |\n") == []

def test_blockquote_skipped():
    assert m.lint(FILLER + "\n> honestly this quoted line " + "word " * 40 + "\n") == []

def test_link_url_ignored_label_kept():
    assert m.lint(FILLER + "See [the spec](https://x.com/honestly/delve) for rules.") == []

def test_unclosed_fence_still_linted():
    # Known limit: an unclosed fence is treated as prose. Documents behavior.
    assert m.lint("```\nhonestly broken fence\n" + FILLER)

# ---- lint(): arrows ----

def test_arrow_unicode_flagged():
    assert m.lint(FILLER + "The data goes parser → builder → writer here.")

def test_arrow_ascii_flagged():
    assert m.lint(FILLER + "The flow goes A -> B -> C.")

def test_single_arrow_ui_path_clean():
    assert m.lint(FILLER + "Open Settings → Emails and enable the block toggle.") == []
    assert m.lint(FILLER + "Go to File -> Preferences and set the flag.") == []

def test_mixed_arrow_glyph_chain_flagged():
    assert m.lint(FILLER + "Data flows client → server -> database during the run.")

def test_arrow_in_code_clean():
    assert m.lint(FILLER + "Use `a -> b` lambda syntax there.") == []

def test_arrow_in_quote_clean():
    assert m.lint(FILLER + 'You wrote "A -> B -> fails" in the report.') == []

# ---- lint(): sentence length ----

def sent(n):
    return "word " * (n - 1) + "end. "

def test_one_long_sentence_passes():
    assert m.lint(FILLER + sent(31)) == []

def test_two_long_sentences_flag():
    assert m.lint(FILLER + sent(31) + sent(31))

def test_boundary_38_passes_39_flags():
    assert m.lint(FILLER + sent(38)) == []
    assert m.lint(FILLER + sent(39))

def test_length_message_number_agreement():
    assert any("1 sentence over" in x for x in m.lint(FILLER + sent(39)))
    assert any("2 sentences over" in x for x in m.lint(FILLER + sent(31) + sent(31)))

def test_colon_runon_still_flagged():
    runon = ("The key insight is this one: " + "word " * 44).strip() + "."
    assert m.lint(FILLER + runon), "colon must not hide a run-on sentence"

def test_realistic_technical_prose_clean():
    text = ("The solver reaches residual 3.2e-6 in 41 steps. "
            "Convergence holds for all 128 seeds. "
            "Throughput drops 12% at batch size 256. "
            "The bound tightens from 0.42 to 0.37 with damping λ=0.9. "
            "These numbers come from one machine, so treat them as estimates. "
            "Rerun the sweep before you cite them.")
    assert m.lint(text) == [], m.lint(text)

# ---- red-team regressions (adversarial pass, 2026-08-22) ----

def test_bullets_only_message_still_checks_banned():
    assert m.lint("- Honestly\n" * 40)

def test_single_quoted_mention_clean():
    assert m.lint(FILLER + "The guide bans 'honestly' and 'under the hood' in chat.") == []

def test_mixed_quote_glyphs_clean():
    assert m.lint(FILLER + 'The doc says "a real game-changer for throughput” it seems.') == []

def test_long_quote_up_to_200_chars_clean():
    q = ('The log says "the operation genuinely could not complete due to a '
         'timeout after several automatic retries" verbatim in the output.')
    assert m.lint(FILLER + q) == []

def test_inch_mark_does_not_swallow_prose():
    text = FILLER + 'The panel is 24" and honestly it "just works" for most people.'
    assert any("honestly" in x for x in m.lint(text))

def test_emphasis_inside_phrase_still_flagged():
    assert m.lint(FILLER + "Some call it a **silver** bullet fix for scaling issues.")

def test_zero_width_char_inside_banned_word_flagged():
    assert m.lint(FILLER + "The design is gen​uinely simple for everyone here.")

# ---- hook process: reminder and feedback ----

def test_reminder_only_when_clean(tmp):
    rc, out = run_hook(ups(tmp, [entry(FILLER)]), statedir(tmp))
    assert rc == 0 and out == m.REMINDER

def test_feedback_appended_on_violation(tmp):
    lines = [entry(FILLER), entry(FILLER + "Honestly this delve happened.")]
    rc, out = run_hook(ups(tmp, lines), statedir(tmp))
    assert rc == 0 and out.startswith(m.REMINDER)
    assert "Style feedback" in out and "honestly" in out and "delve" in out

def test_first_run_lints_only_last_message(tmp):
    lines = [entry(FILLER + "Honestly old drift here."), entry(FILLER)]
    rc, out = run_hook(ups(tmp, lines), statedir(tmp))
    assert rc == 0 and out == m.REMINDER  # old history not flooded back

def test_no_repeat_feedback_after_state(tmp):
    sp = statedir(tmp)
    lines = [entry(FILLER), entry(FILLER + "Honestly this happened.")]
    payload = ups(tmp, lines)
    _, first = run_hook(payload, sp)
    assert "Style feedback" in first
    rc, second = run_hook(payload, sp)
    assert rc == 0 and second == m.REMINDER

def test_new_message_after_state_gets_feedback(tmp):
    sp = statedir(tmp)
    payload = ups(tmp, [entry(FILLER)])
    _, out = run_hook(payload, sp)
    assert out == m.REMINDER
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER + "The flow goes A -> B -> C.") + "\n")
    _, out2 = run_hook(payload, sp)
    assert "arrow chain" in out2

def test_sidechain_ignored(tmp):
    sp = statedir(tmp)
    payload = ups(tmp, [entry(FILLER)])
    run_hook(payload, sp)
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER + "Honestly bad subagent text.", side=True) + "\n")
    rc, out = run_hook(payload, sp)
    assert rc == 0 and out == m.REMINDER

def test_trailing_partial_line_deferred(tmp):
    sp = statedir(tmp)
    payload = ups(tmp, [entry(FILLER)])
    run_hook(payload, sp)
    tp = json.loads(payload)["transcript_path"]
    full = entry(FILLER + "Honestly cut mid-write.")
    with open(tp, "a") as f:
        f.write(full[:40])  # partial line, no newline: must be left for later
    rc, out = run_hook(payload, sp)
    assert rc == 0 and out == m.REMINDER
    with open(tp, "a") as f:
        f.write(full[40:] + "\n")
    _, out2 = run_hook(payload, sp)
    assert "honestly" in out2

def test_truncated_transcript_resets_state(tmp):
    sp = statedir(tmp)
    payload = ups(tmp, [entry(FILLER), entry(FILLER)])
    run_hook(payload, sp)
    tp = json.loads(payload)["transcript_path"]
    with open(tp, "w") as f:  # replaced with a shorter file
        f.write(entry(FILLER) + "\n")
    rc, out = run_hook(payload, sp)
    assert rc == 0 and out == m.REMINDER

def test_nonstring_text_value_does_not_wedge(tmp):
    sd = statedir(tmp)
    lines = [entry(FILLER),
             json.dumps({"type": "assistant",
                         "message": {"content": [{"type": "text", "text": 123}]}}),
             entry(FILLER + "Honestly this happened.")]
    payload = ups(tmp, lines)
    _, out = run_hook(payload, sd)
    assert "honestly" in out
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER + "The flow goes A -> B -> C.") + "\n")
    _, out2 = run_hook(payload, sd)
    assert "arrow chain" in out2 and "honestly" not in out2


def test_negative_offset_recovers(tmp):
    sd = statedir(tmp)
    payload = ups(tmp, [entry(FILLER), entry(FILLER + "Honestly again here.")])
    run_hook(payload, sd)
    with open(os.path.join(sd, "s1.json"), "w") as f:
        f.write("-5")
    _, out = run_hook(payload, sd)  # negative = corrupt: first-run semantics
    assert out.startswith(m.REMINDER) and "honestly" in out
    with open(os.path.join(sd, "s1.json")) as f:
        assert int(f.read()) >= 0


def test_prune_removes_tmp_leftovers(tmp):
    sd = statedir(tmp)
    os.makedirs(sd)
    old = 1000000000
    for i in range(205):
        p = os.path.join(sd, f"dead{i}.json")
        open(p, "w").write("1")
        os.utime(p, (old, old))
    for i in range(5):
        p = os.path.join(sd, f"dead{i}.json.999.tmp")
        open(p, "w").write("1")
        os.utime(p, (old, old))
    run_hook(ups(tmp, [entry(FILLER)]), sd)
    names = os.listdir(sd)
    assert not any(n.endswith(".tmp") for n in names), names
    assert len(names) <= 101


# ---- hook process: block mode (regeneration toggle) ----

def stop_payload(payload, active=False):
    d = json.loads(payload)
    d["hook_event_name"] = "Stop"
    d["stop_hook_active"] = active
    return json.dumps(d)


def test_block_mode_blocks_new_dirty_reply(tmp):
    sd = statedir(tmp)
    payload = ups(tmp, [entry(FILLER)])
    run_hook(payload, sd)  # prompt pass sets the baseline offset
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER + "Honestly this happened.") + "\n")
    rc, out = run_hook(stop_payload(payload), sd, mode="block")
    d = json.loads(out)
    assert rc == 0 and d["decision"] == "block" and "honestly" in d["reason"]
    _, out2 = run_hook(stop_payload(payload), sd, mode="block")
    assert out2 == ""  # offset advanced: never a double block


def test_block_mode_clean_reply_silent(tmp):
    sd = statedir(tmp)
    payload = ups(tmp, [entry(FILLER)])
    run_hook(payload, sd)
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER) + "\n")
    rc, out = run_hook(stop_payload(payload), sd, mode="block")
    assert rc == 0 and out == ""


def test_block_mode_length_only_defers_to_feedback(tmp):
    sd = statedir(tmp)
    payload = ups(tmp, [entry(FILLER)])
    run_hook(payload, sd)
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER + sent(31) + sent(31) + sent(31)) + "\n")
    _, out = run_hook(stop_payload(payload), sd, mode="block")
    assert out == ""  # length never blocks
    _, out2 = run_hook(payload, sd, mode="block")
    assert "sentences over" in out2  # it arrives as prompt-time feedback


def test_block_mode_respects_loop_guard(tmp):
    sd = statedir(tmp)
    payload = ups(tmp, [entry(FILLER)])
    run_hook(payload, sd)
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER + "Honestly this happened.") + "\n")
    rc, out = run_hook(stop_payload(payload, active=True), sd, mode="block")
    assert rc == 0 and out == ""


def test_block_mode_env_with_whitespace_still_blocks(tmp):
    sd = statedir(tmp)
    payload = ups(tmp, [entry(FILLER)])
    run_hook(payload, sd)
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER + "Honestly this happened.") + "\n")
    _, out = run_hook(stop_payload(payload), sd, mode="block\n")
    assert json.loads(out)["decision"] == "block"


def test_block_mode_yields_after_prompt_pass(tmp):
    sd = statedir(tmp)
    payload = ups(tmp, [entry(FILLER)])
    run_hook(payload, sd)
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER + "Honestly this happened.") + "\n")
    _, fb = run_hook(payload, sd, mode="block")  # prompt pass reports it first
    assert "honestly" in fb
    rc, out = run_hook(stop_payload(payload), sd, mode="block")
    assert rc == 0 and out == ""  # Stop must not re-block an owned reply


def test_block_mode_without_baseline_silent(tmp):
    sd = statedir(tmp)
    payload = ups(tmp, [entry(FILLER + "Honestly this happened.")])
    rc, out = run_hook(stop_payload(payload), sd, mode="block")
    assert rc == 0 and out == ""  # unlinted history never triggers a block

# ---- hook process: fail-open and event dispatch ----

def test_missing_transcript_still_reminds(tmp):
    payload = json.dumps({"hook_event_name": "UserPromptSubmit",
                          "transcript_path": os.path.join(tmp, "absent.jsonl"),
                          "session_id": "s1"})
    rc, out = run_hook(payload, statedir(tmp))
    assert rc == 0 and out == m.REMINDER

def test_garbage_stdin_fails_open():
    rc, out = run_hook("this is not json")
    assert rc == 0 and out == ""

def test_stale_stop_event_stays_silent(tmp):
    payload = json.dumps({"hook_event_name": "Stop",
                          "transcript_path": transcript(tmp, [entry(FILLER)]),
                          "session_id": "s1"})
    rc, out = run_hook(payload, statedir(tmp))
    assert rc == 0 and out == ""

def test_unwritable_state_still_works(tmp):
    blocker = os.path.join(tmp, "blocker")
    open(blocker, "w").close()  # a regular file where the state dir should be
    lines = [entry(FILLER + "Honestly this happened.")]
    rc, out = run_hook(ups(tmp, lines), blocker)
    assert rc == 0 and out.startswith(m.REMINDER) and "honestly" in out


def test_poison_lines_do_not_wedge(tmp):
    sd = statedir(tmp)
    lines = [entry(FILLER), "null", "42", "[1, 2]",
             json.dumps({"type": "assistant",
                         "message": {"content": [{"type": "text", "text": None}]}}),
             entry(FILLER + "Honestly this happened.")]
    payload = ups(tmp, lines)
    _, out = run_hook(payload, sd)
    assert "honestly" in out
    with open(json.loads(payload)["transcript_path"], "a") as f:
        f.write(entry(FILLER + "The flow goes A -> B -> C.") + "\n")
    _, out2 = run_hook(payload, sd)
    assert "arrow chain" in out2 and "honestly" not in out2


def test_concurrent_sessions_keep_separate_state(tmp):
    sd = statedir(tmp)
    lines = [entry(FILLER + "Honestly this happened.")]
    p1, p2 = ups(tmp, lines, sid="s1"), ups(tmp, lines, sid="s2")
    _, a = run_hook(p1, sd)
    _, b = run_hook(p2, sd)
    assert "honestly" in a and "honestly" in b
    _, a2 = run_hook(p1, sd)
    _, b2 = run_hook(p2, sd)
    assert a2 == m.REMINDER and b2 == m.REMINDER


def test_corrupt_state_recovers(tmp):
    sd = statedir(tmp)
    payload = ups(tmp, [entry(FILLER), entry(FILLER + "Honestly again here.")])
    run_hook(payload, sd)
    with open(os.path.join(sd, "s1.json"), "w") as f:
        f.write("not a number")
    _, out = run_hook(payload, sd)  # corrupt state = first run again: last only
    assert out.startswith(m.REMINDER) and "honestly" in out
    _, out2 = run_hook(payload, sd)
    assert out2 == m.REMINDER


def run_all():
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as tmp:
            try:
                fn(tmp) if fn.__code__.co_argcount else fn()
                print(f"PASS {name}")
            except AssertionError as e:
                failed += 1
                print(f"FAIL {name}: {e}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_all())
