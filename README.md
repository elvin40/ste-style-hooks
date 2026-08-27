# ste-style-hooks

![tests](https://github.com/elvin40/ste-style-hooks/actions/workflows/test.yml/badge.svg)

Claude Code hooks that keep chat responses in the spirit of
[ASD-STE100](https://www.asd-ste100.org/) Simplified Technical English.
A per-prompt reminder steers each reply, and the same hook feeds style
drift from recent replies back as context the model acts on.

This is not a conformance checker. It has no STE dictionary (the official
dictionary is copyrighted) and covers only the mechanical rules that make
chat easier to read: short active sentences, plain words, no idioms.

## What it checks

- Banned words and idioms — edit `BANNED` at the top of the script.
- Arrow chains in prose: two or more `→` or spaced `->` in one reply. One
  arrow passes, so a UI path like Settings → Emails stays legal; two
  separate UI paths in one reply do trip it.
- Sentence length: 2 or more sentences over 30 words, or any over 38.

The default mode never blocks or regenerates. At each user prompt, the hook lints
the replies written since its last run and appends the findings to the
reminder, so the model corrects course in its next reply and the user sees
the same line. A per-session byte-offset file under `~/.claude/ste-style-hooks-state/`
(override with `STE_STATE_DIR`) keeps a reply from being linted twice;
replies from before the hook first saw a session are skipped by design. Earlier versions blocked by default; field data showed the duplicated
messages cost more than the drift, so v0.5.0 made feedback the default and
v0.7.0 added the opt-in block mode below.

## How it protects technical content

The hook never rewrites anything, so machinery cannot drop a fact. Style
corrections happen only in future replies, written with full context. The
checks target form, never volume: there is no total-length rule and no
reward for deletion.

## What it skips

Code fences, inline code, tables, blockquotes, link URLs, quoted spans
(a mention is not a use), subagent messages, and replies under 280
characters (the 3-sentence floor applies to the length check only). Any
internal error fails open: the response goes through.

## Install

Python 3.9+ standard library only.

As a plugin (recommended, two commands):

```
/plugin marketplace add elvin40/ste-style-hooks
/plugin install ste-style-hooks@ste-style-hooks
```

Manual: clone, then add to `~/.claude/settings.json` with the absolute
path of your clone:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/ste-style-hooks/ste_style_check.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

The hook loads at the next session start, or open `/hooks` once to reload.

## How it works

One script, one event. On `UserPromptSubmit` it prints the style reminder,
which Claude Code injects into context next to the prompt — prevention that
keeps the instruction in the recency window for about 40 input tokens per
turn. The same run lints any replies finished since the previous prompt and
appends the findings, which closes the feedback loop without a regeneration.
Feedback arrives one turn late by design. Tune the reminder text via the
`REMINDER` constant.

## Optional: regeneration mode

The default never regenerates a reply. To make violations force a rewrite,
turn on block mode:

```
echo block > ~/.claude/ste-style-hooks-mode    # on
rm ~/.claude/ste-style-hooks-mode              # off
```

A non-empty `STE_MODE` environment variable overrides the file. In block mode a
`Stop` hook re-checks each finished reply against the session's offset
state, so it never judges a reply it has already seen. Banned words and
arrow chains block once, and Claude rewrites the message; sentence length
never blocks and stays prompt-time feedback. The costs return with the
mode: a blocked reply is paid for twice, and both versions stay on screen.
If the reply has not reached the transcript within about 1.6 s, the check
skips silently and the next prompt's feedback covers it. Block mode needs
the `Stop` hook registered — the plugin registers it; manual installs add
a second entry with the same command under `"Stop"`. With the mode off,
that registration is a silent no-op.

## Test

```
python3 test_ste_style_check.py   # full suite, stdlib only
```

CI runs the suite on every push against Python 3.9 and 3.13; the badge
above shows the current result.

## Limits

- Form only; it cannot detect semantic loss.
- No passive-voice check: regex passive detection false-positives too much.
  Add a spaCy pass if active voice drifts.
- An unclosed code fence is linted as prose.
- Feedback reaches the model at the next prompt, one turn after the drift.

MIT license for this project's own code.

ASD-STE100 Simplified Technical English is a copyright and registered
trademark of ASD, Brussels (EU Trade Mark No. 017966390). This project is
independent and unofficial, is not affiliated with or endorsed by ASD or
the STEMG, and reproduces no part of the specification or its dictionary.
