---
name: asof
description: Keep numbers in documentation true. Use when an `asof` check fails in CI or locally (drift, split, stale, orphan, error), when setting up or adopting `asof` in a repository, when asked whether a README's numbers are still accurate or which documented numbers are unverified, or before changing a constant in code that a document may also state (a port, rate limit, default, timeout, supported version, size or count).
---

# asof

`asof` checks that numbers written in documents are still true. A number carries
a trailing comment naming a claim — `<!-- asof:dashboard-port -->` in Markdown or
HTML, `# asof:dashboard-port` in config and most code, `// asof:…` in C-family
languages — and `asof.ini` records how each claim is settled: by a command, or
by a person on a schedule.

The marker binds **the last value before it on that line**.

## A check is failing

Run `asof check` and read the `->` line under each failure; it names the next
step. `asof why NAME` explains any single claim — what it says, who settles it,
when it was last true and everywhere it is written — without running anything.

`asof check --json` returns `{tool, checked, blocked, counts, items[]}`. Each
item leads with `id`, `status`, `where` and `why`; failing ones carry `remedy`,
which is the same next step as the `->` line. `blocked` says whether this run
should stop a build. `list`, `suggest`, `why`, `update` and `touch` return the
same envelope, so there is one shape to learn — and `update --json` adds a
`changed` flag and an `edits` list saying which bytes it moved.

| Status | What happened | What to do |
|---|---|---|
| `DRIFT` | the document and its command disagree | `asof update NAME` writes the command's answer in the document's own style. If the *document* is right and the command is wrong, fix `run` in `asof.ini` instead. |
| `SPLIT` | one claim, different numbers in different files | Decide which is correct and edit the others. Never resolve it by deleting a marker. |
| `STALE` | nobody has confirmed a manual claim in too long | Verify it however it is verified, then `asof touch NAME`. Correcting the number in the document also counts. |
| `ORPHAN` | every marker for the claim is gone | Put one back. Only delete `[NAME]` from `asof.ini` if the number is genuinely gone from the docs. |
| `DROPPED` | one file that carried the claim no longer does | Put that marker back. If the file really should stop stating it, `asof touch NAME` accepts the removal. |
| `ERROR` | the claim's command would not run | Fix `run` under `[NAME]`. If it cannot run in this environment, `asof check --no-run` judges by age alone. |

**Never delete an `asof:` comment to make a check pass.** It is the fastest way
to a green build and the exact failure the tool exists to prevent. asof records
which files carried each claim, so taking a marker off one of several files is
`DROPPED` and still fails.

## Before changing a constant

If you are about to change a port, a rate limit, a default, a timeout or a
supported version in code or config, check whether a document states it too:

```bash
asof check          # a SPLIT means a document names the same value
```

A README and a config file that both state one value are **one claim**. Changing
one without the other fails the build. This is the most common way a change
breaks `asof`, and the cheapest one to avoid.

## Adopting it in a repository

```bash
asof suggest --why          # unclaimed numbers, most likely to rot first
```

It ranks by repetition — a number written in a document *and* in a config file
is a constant stated twice by two people who will not both remember to change
it. Work down that list:

1. **Paste the marker** it prints, immediately after the value it names. On a
   crowded line the marker takes the *last* value before it, so at the end of
   `<input min="50" max="99" value="70">` it would claim the `70`.
2. **`asof init`** writes a starter `asof.ini` from the markers found.
3. **Give each claim a `run` or an `every`.** A command if a machine can answer
   it; a shelf life (`every = 90d`) if only a person can. Write the `why` line —
   it is what the next reader gets.
4. **Add CI**, two steps, deliberately separate:

```yaml
- run: asof check                            # a wrong number is a build failure
- run: asof check --no-run --fail-on stale   # an unverified one is a reminder
```

5. **`asof agents >> AGENTS.md`** so the next agent in this repo knows the rules.

Commit `asof.lock`. It records when each claim was last known to be true.

## Things that surprise people

- **In Markdown, code is not speech.** Markers inside fenced blocks and inline
  `` `code spans` `` are ignored, so a document can explain `asof` without
  claiming its own examples. `<style>` and `<script>` are skipped when
  *suggesting*, though a marker deliberately placed there still works.
- **Formatting is not drift.** `1,247`, `1247`, `1.247k` and `18_000` vs
  `18,000` are the same claim; `2.5 kHz` matches `2500`; `99%` matches
  `max="99"`. Units are not interchangeable: `250ms` never matches `250s`.
- **Versions compare as versions.** `v3.10.0` = `"3.10.0"` = `3.10`; a
  pre-release tag must match. A two-part version like `3.10` is also the
  decimal 3.1, so a claim tracking one needs `type = version` in `asof.ini` —
  add it whenever the claim is a Python, Node, API or SDK version.
- **`asof update` only fixes drift.** Anything else needs a person to decide
  which file is wrong, and it says so rather than exiting 0.
- **`asof:off`** in a file excludes it entirely.

## If it is not installed

```bash
pip install git+https://github.com/Blueangelman36/asof
```

Or run the vendored single file if the repository has one — `python asof.pyz
check` is the whole tool, no install, no network. Build one with
`python tools/build_pyz.py`.
