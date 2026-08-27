#!/usr/bin/env python3
"""Claude Code UserPromptSubmit hook: prose style reminder plus drift feedback.
On each user prompt it prints a short style reminder, lints the main-thread
assistant replies written since its last run (state = one byte-offset file per
session), and appends the findings, which reach both the user and the model.
The default mode never blocks; the opt-in block mode (see mode()) rewrites
replies with banned words or arrow chains. Fail-open: errors never break a
session."""
import json
import os
import re
import sys
import time

REMINDER = ("Prose style reminder: short active sentences (~20 words, one idea "
            "each), name the actor, no idioms or figures of speech, same word "
            "for same thing. Simplify the language, never the content.")

BANNED = [
    "genuinely", "honestly", "utilize", "utilizes", "utilizing",
    "leverage", "leverages", "leveraging", "delve", "delves", "delving",
    "under the hood", "low-hanging fruit", "at the end of the day",
    "rabbit hole", "silver bullet", "game-changer", "kicks in",
    "a breeze", "shines", "the beauty of",
]
LONG_WORDS = 30       # per-sentence flag threshold; the style target is ~20
VERY_LONG_WORDS = 38  # field data 2026-08-27: 45 left most real run-ons silent
MIN_PROSE_CHARS = 280  # skip short replies entirely
MIN_SENTENCES = 3
# One state file per session: concurrent sessions never share a write target.
STATE_DIR = os.environ.get("STE_STATE_DIR") or os.path.expanduser(
    "~/.claude/ste-style-hooks-state")
MODE_PATH = os.path.expanduser("~/.claude/ste-style-hooks-mode")


def mode():
    m = (os.environ.get("STE_MODE") or "").strip()
    if not m:
        try:
            with open(MODE_PATH) as f:
                m = f.read().strip()
        except Exception:
            m = ""
    return "block" if m == "block" else "feedback"


def state_file(sid):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid)[:80] or "unknown"
    return os.path.join(STATE_DIR, safe + ".json")


def read_offset(path):
    try:
        with open(path) as f:
            v = int(f.read().strip())
        return v if v >= 0 else None  # a negative seek would wedge the session
    except Exception:
        return None  # missing or corrupt: treat the session as new


def write_offset(path, value):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"  # per-writer tmp: no shared target
        with open(tmp, "w") as f:
            f.write(str(value))
        os.replace(tmp, path)  # atomic: a reader never sees a torn file
    except Exception:
        pass
    try:
        now = time.time()
        files = []
        for n in os.listdir(STATE_DIR):
            p = os.path.join(STATE_DIR, n)
            if n.endswith(".tmp") and now - os.path.getmtime(p) > 60:
                os.remove(p)  # a tmp older than a minute is a dead writer's litter
            elif n.endswith(".json"):
                files.append(p)
        if len(files) > 200:  # active sessions have fresh mtimes; oldest go
            files.sort(key=os.path.getmtime)
            for f in files[:len(files) - 100]:
                os.remove(f)
    except Exception:
        pass


def prose_only(text):
    text = re.sub(r"[​‌‍﻿]", "", text)  # zero-width chars
    text = re.sub(r"```.*?```", " ", text, flags=re.S)   # fenced code
    text = re.sub(r"`[^`\n]*`", " ", text)               # inline code
    text = text.replace("*", "")                         # emphasis markers
    # quoted span = mention, not use; word-boundary anchors keep apostrophes
    # ("it's") and inch marks (24") from opening or closing a span
    text = re.sub(r"(?<!\w)[\"“'‘]([^\"”'’\n]{1,200})[\"”'’](?!\w)", " ", text)
    kept = [l for l in text.splitlines()
            if not l.lstrip().startswith(("|", ">"))]    # tables, quotes
    text = "\n".join(kept)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> label
    text = re.sub(r"^#+\s*", "", text, flags=re.M)        # heading markers
    return text


def sentences(text):
    # no split on ":" — colons hid run-on sentences from the length check
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [p.strip() for p in parts if len(p.split()) >= 3]


def lint(text):
    p = prose_only(text)
    if len(p) < MIN_PROSE_CHARS:
        return []
    v = []
    low = p.lower()
    for term in BANNED:
        # leading-hyphen lookbehind spares compounds like "highest-leverage"
        if re.search(r"(?<!-)\b" + re.escape(term) + r"\b", low):
            v.append(f'banned word or idiom: "{term}"')
    # A chain needs 2+ arrows total, any glyph mix; one arrow is UI navigation.
    if len(re.findall(r"→|\s->\s", p)) >= 2:
        v.append("arrow chain in prose: write the relation in words")
    # The sentence floor gates only the length check; banned words and
    # arrows apply to fragment-heavy messages too.
    sents = sentences(p)
    if len(sents) >= MIN_SENTENCES:
        long_s = [s for s in sents if len(s.split()) > LONG_WORDS]
        if len(long_s) >= 2 or any(len(s.split()) > VERY_LONG_WORDS for s in long_s):
            n = len(long_s)
            v.append(f'{n} sentence{"s" if n != 1 else ""} over {LONG_WORDS} '
                     f'words (target ~20), e.g. "{long_s[0][:90]}..."')
    # ponytail: no passive-voice check; regex passives false-positive too much.
    # Add a spaCy pass here if active voice keeps drifting.
    return v


def new_texts(path, offset):
    """Main-thread assistant texts in the complete lines from byte offset on."""
    with open(path, "rb") as f:
        f.seek(offset)
        chunk = f.read()
    end = chunk.rfind(b"\n")  # a trailing partial line is left for next run
    if end < 0:
        return [], offset
    texts = []
    for raw in chunk[:end + 1].splitlines():
        try:
            obj = json.loads(raw.decode("utf-8", "replace"))
        except ValueError:
            continue
        if not isinstance(obj, dict):  # a null/list line must not wedge the loop
            continue
        if obj.get("type") != "assistant" or obj.get("isSidechain"):
            continue
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        t = "\n".join((c.get("text") if isinstance(c.get("text"), str) else "")
                      for c in (content or [])
                      if isinstance(c, dict) and c.get("type") == "text")
        if t.strip():
            texts.append(t)
    return texts, offset + end + 1


def handle_stop(data):
    """Block mode only: re-check the reply that just finished and make Claude
    rewrite it once when a blocking class (banned words, arrows) fires."""
    tp = data.get("transcript_path")
    sid = str(data.get("session_id") or "unknown")
    if not tp or not os.path.exists(tp):
        return
    sf = state_file(sid)
    offset = read_offset(sf)
    if offset is None or offset > os.path.getsize(tp):
        return  # no baseline yet; the prompt-time pass will set one
    texts = []
    for i in range(4):  # the finished reply flushes ~simultaneously; poll briefly
        texts, new_offset = new_texts(tp, offset)
        if texts:
            break
        if i < 3:
            time.sleep(0.4)
    if not texts:
        return  # reply never flushed in time; prompt-time feedback covers it
    all_v, seen = [], set()
    for t in texts:
        for v in lint(t):
            if v not in seen:
                seen.add(v)
                all_v.append(v)
    blocking = [v for v in all_v if "sentences over" not in v]
    if not blocking:
        return  # clean or length-only: leave state for the prompt-time pass
    if read_offset(sf) != offset:
        return  # a prompt-time run advanced state mid-poll; it owns this reply
    write_offset(sf, new_offset)
    print(json.dumps({"decision": "block", "reason":
        "Prose style check failed on your last message. Rewrite the full "
        "message: short active sentences of about 12-25 words (split long "
        "ones at clause boundaries; never chop into telegraph fragments), "
        "no idioms or figures of speech, and keep every technical fact, "
        "number, and caveat. Violations: " + "; ".join(blocking[:6])}))


def main():
    data = json.load(sys.stdin)
    event = data.get("hook_event_name")
    if event == "Stop":
        if mode() == "block" and not data.get("stop_hook_active"):
            handle_stop(data)
        return  # in feedback mode a Stop registration is a silent no-op
    if event != "UserPromptSubmit":
        return
    print(REMINDER)
    tp = data.get("transcript_path")
    sid = str(data.get("session_id") or "unknown")
    if not tp or not os.path.exists(tp):
        return
    sf = state_file(sid)
    offset = read_offset(sf)
    first = offset is None
    if first:
        offset = 0
    if offset > os.path.getsize(tp):  # transcript replaced or truncated
        first, offset = True, 0
    texts, new_offset = new_texts(tp, offset)
    if first:
        texts = texts[-1:]  # no catch-up flood on a session's first run
    seen, found = set(), []
    for t in texts:
        for v in lint(t):
            if v not in seen:
                seen.add(v)
                found.append(v)
    if found:
        print("Style feedback on your recent replies. Fix in your next reply: "
              + "; ".join(found[:6]))
    write_offset(sf, new_offset)


def demo():
    filler = "The linter checks each sentence for length and bad words. " * 8
    assert lint("Too short.") == []
    assert lint(filler) == []
    hit = lint(filler + "Honestly, the cache kicks in under the hood.")
    assert len(hit) == 3, hit
    assert lint(filler + "word " * 46 + "end.")
    assert lint(filler + "The flow goes A -> B -> C.")
    code = "```\nhonestly = 1  # " + "word " * 50 + "\n```\n" + filler
    assert lint(code) == []
    mention = filler + 'The list bans "honestly" and "under the hood" for chat.'
    assert lint(mention) == [], lint(mention)
    print("ok")


if __name__ == "__main__":
    if "--test" in sys.argv:
        demo()
    else:
        try:
            main()
        except Exception:
            pass  # fail-open: a style helper must never break the session
