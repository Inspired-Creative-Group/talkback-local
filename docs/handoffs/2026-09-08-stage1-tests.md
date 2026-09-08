# Stage 1 handoff — tests and CI

**Date:** 2026-09-08 · **Branch:** `stage1-tests` → PR into `main` · **Plan:** `ICG-Brain/projects/infrastructure/claudia-tts-voice/talkback-hardening-plan.md`

## Status

**Complete on the branch, not pushed, not merged — blocked on a GitHub credential, not on the work.** 145 tests, green on Python 3.11, 3.12 and 3.13, with `ruff`, `shellcheck` and `bash -n` clean; four real bugs found by the tests and fixed test-first; CI workflow in place. `git push -u origin stage1-tests` was refused by GitHub: *"refusing to allow an OAuth App to create or update workflow `.github/workflows/ci.yml` without `workflow` scope"*. The `gh` token on the M4 has `repo, gist, read:org, admin:public_key` only, and the M4's SSH key is not registered on GitHub (`Permission denied (publickey)`). Every way past that — `gh auth refresh -h github.com -s workflow` (browser login), registering the key, or a PAT with `workflow` — changes Juan's GitHub account permissions and was left to him. Rewriting the branch to drop the workflow commit was not an option (no rebase, and the workflow is the deliverable). Once the credential is fixed the remaining steps are: push, open the PR "Stage 1: tests and CI" against `main`, watch CI on 3.11/3.12/3.13, merge with a merge commit, watch `main`'s run.

## What is covered

Every subprocess in the suite runs with `HOME` pointed at a throwaway sandbox, fake `ffplay` / `osascript` / `shush` / `kokoro-server` shims first on `PATH`, `KOKORO_PORT` at 8930+ (never 8910), and every `TTS_*` / `ELEVENLABS_*` variable removed. The engine is an in-process fake HTTP server that returns fixed PCM; the player is a shim that logs its calls. The live install is never read or written.

| Plan item | Status | Tests |
|---|---|---|
| speak_last_reply.py: transcript parsing (last assistant text; tool-only turns skipped; multi-block content) | covered | test_last_assistant_text_picks_the_last_turn_with_text, test_tool_only_turns_are_skipped_so_the_previous_text_wins, test_multi_block_content_is_joined_and_tool_blocks_are_dropped, test_non_assistant_and_malformed_lines_are_ignored, test_a_non_object_json_line_does_not_abort_the_hook, test_no_assistant_text_gives_an_empty_string |
| speak_last_reply.py: say_time | covered | test_clock_times_are_spoken_as_words, test_impossible_clock_times_are_left_alone |
| speak_last_reply.py: say_inline | covered | test_short_inline_code_is_spoken_as_written, test_unlistenable_inline_code_becomes_a_command, test_empty_backticks_vanish_without_breaking_the_sentence |
| speak_last_reply.py: SAYABLE | covered | test_sayable_pattern_accepts_short_plain_tokens_only |
| speak_last_reply.py: fenced code → "shown on screen" | covered | test_fenced_code_becomes_shown_on_screen |
| speak_last_reply.py: inline code spoken when sayable | covered | test_short_inline_code_is_spoken_as_written, test_unlistenable_inline_code_becomes_a_command |
| speak_last_reply.py: URLs/emails/hash-ids rewritten | covered | test_urls_and_domains_become_a_link, test_emails_become_an_email_address, test_hash_like_tokens_become_an_id |
| speak_last_reply.py: empty reply → exit non-zero | covered | test_cli_exits_1_with_empty_text_when_only_tool_turns, test_cli_exits_1_with_empty_text_when_no_assistant_turn, test_cli_treats_a_symbol_only_reply_as_empty |
| speak_last_reply.py: local engine needs no ElevenLabs key | covered | test_speak_last_reply_key.py (added by the harness phase; not in the plan's list) |
| chunk_text.py: first chunk ≤120 chars | covered | test_first_chunk_fits_120_chars_and_later_chunks_fit_400, test_chunk_limits_hold_when_two_sentences_sum_exactly_to_the_limit[first-at-120, first-over-by-the-space] |
| chunk_text.py: later chunks ≤400 | covered | test_first_chunk_fits_120_chars_and_later_chunks_fit_400, test_chunk_limits_hold_when_two_sentences_sum_exactly_to_the_limit[later-at-400, later-over-by-the-space] |
| chunk_text.py: sentence boundaries respected | covered | test_chunks_end_on_sentence_boundaries_and_keep_every_sentence_whole, test_single_sentence_over_120_chars_is_kept_whole |
| chunk_text.py: one-sentence reply → one chunk | covered | test_one_sentence_reply_is_exactly_one_chunk |
| chunk_text.py: no empty chunks | covered | test_no_chunk_file_is_empty_or_padded, test_empty_input_prints_zero_and_writes_nothing, test_printed_count_matches_the_chunk_files_written |
| tts_toggle.sh: exact phrases exit 2 and write/remove the session flag | covered | test_tts_on_arms_the_session, test_tts_on_is_idempotent, test_tts_off_disarms_and_shushes, test_tts_off_only_disarms_its_own_session, test_stop_words_shush_but_keep_the_session_armed[shush, stop, quiet, be quiet], test_replay_words_replay_this_session[6 phrases], test_replay_with_nothing_recorded_says_so, test_missing_session_id_uses_unknown |
| tts_toggle.sh: anything else exits 0 untouched | covered | test_ordinary_prompts_pass_through_untouched[3 prompts], test_payload_without_prompt_is_ignored |
| tts_toggle.sh: case/whitespace tolerance as currently implemented | partial | test_tts_on_tolerates_case_whitespace_and_punctuation[TTS ON, ' tts on ', 'tts on.', 'TTS on!'], test_tts_off_only_disarms_its_own_session ('TTS off.') — internal double spaces and case tolerance for shush/again phrases are not asserted |
| speak_last_reply.sh: unarmed session → no playback | covered | test_unarmed_session_plays_nothing |
| speak_last_reply.sh: armed → text saved to lastreply/<sid>.txt before playback | covered | test_armed_session_saves_text_before_playback, test_saved_pcm_is_the_engine_audio_in_order |
| speak_last_reply.sh: duplicate-within-120s guard | covered | test_duplicate_reply_within_seconds_is_suppressed, test_changed_reply_plays_again, test_same_reply_after_two_minutes_plays_again |
| verify_audio.sh: rejects JSON/HTML bodies | covered | test_json_error_body_is_rejected_and_announced_via_notification, test_html_error_body_is_rejected_with_its_text_in_the_log, test_local_engine_failure_is_not_spoken_through_itself, test_cloud_failure_is_spoken_through_the_local_engine, test_cloud_failure_is_not_spoken_when_local_engine_is_down |
| verify_audio.sh: rejects files < 2000 bytes | covered | test_file_under_2000_bytes_is_rejected_as_too_short, test_missing_file_is_rejected_as_nothing_downloaded |
| verify_audio.sh: accepts real PCM | covered | test_2000_bytes_of_pcm_is_accepted_silently, test_50kb_of_pcm_is_accepted |
| play_reply.sh: plays exactly once per chunk | covered | test_three_chunks_are_fetched_and_played_once_each_in_order, test_one_sentence_is_one_fetch_one_play_and_saved, test_tts_tempo_is_applied_to_every_chunk, test_no_tempo_filter_when_tts_tempo_is_unset |
| play_reply.sh: pipelines fetch of chunk n+1 during chunk n | covered | test_next_chunk_is_fetched_while_the_current_one_plays |
| play_reply.sh: stops on the stop-flag | covered | test_stop_flag_ends_the_reply_after_the_current_chunk_and_drops_it, test_stale_stop_flag_is_cleared_so_the_reply_plays_in_full |
| play_reply.sh: publishes .pcm only for a complete reply | covered | test_three_chunks_are_fetched_and_played_once_each_in_order, test_stop_flag_ends_the_reply_after_the_current_chunk_and_drops_it, test_engine_error_body_is_never_published_and_the_failure_is_loud |
| play_reply.sh: never leaves .part | covered | the four tests above assert no `.part` on the success, stop and error paths |
| bin/replay: prefers <sid>.txt + engine | covered | test_saved_text_is_respoken_through_the_engine, test_text_beats_a_stale_recording_when_the_engine_is_up, test_no_argument_picks_the_most_recently_saved_text |
| bin/replay: falls back to .pcm | covered | test_recording_alone_plays_straight_from_disk[engine-up, engine-down], test_text_without_an_engine_falls_back_to_the_recording, test_fallback_clears_the_stop_flag_before_playing |
| bin/replay: exit 1 when nothing exists | covered | test_nothing_saved_exits_1[with-sid, no-sid], test_text_without_an_engine_or_recording_reports_nothing |
| engine/server.py: POST text → PCM bytes (mock KPipeline) | covered | test_post_text_returns_l16_pcm_of_every_chunk, test_voice_is_the_tensor_loaded_from_icg_voice_pt_beside_the_server, test_kokoro_speed_env_is_passed_to_every_synthesis, test_get_root_answers_ok, test_requests_are_logged_under_home_and_nowhere_else, test_missing_log_dir_does_not_break_synthesis |
| engine/server.py: empty body → 400 | covered | test_blank_body_is_rejected_with_400_and_never_reaches_the_model[empty, whitespace] |
| engine/server.py: device env override honoured | covered | test_kokoro_device_env_picks_the_pipeline_device[unset, empty, mps], test_unusable_device_falls_back_to_cpu_and_still_serves |
| engine/server.py: startup warm-up runs once | covered | test_warm_up_runs_once_before_any_request_and_never_again |
| install.sh: end-to-end in a sandbox HOME, model download stubbed | covered | test_fresh_install_exits_zero_and_reports_installed, test_python_env_is_built_with_uv_inside_the_engine_dir, test_missing_spacy_model_is_installed_from_the_wheel_url, test_missing_prerequisite_aborts_before_changing_anything |
| install.sh: every file lands | covered | test_engine_files_and_state_dirs_land_under_automation, test_every_hook_lands_and_shell_scripts_are_executable, test_commands_land_executable_in_home_bin, test_settings_get_exactly_one_stop_and_one_prompt_hook, test_existing_settings_are_kept_and_talkback_hooks_appended, test_settings_with_an_emptied_hook_list_still_get_registered |
| install.sh: plist rendered with the sandbox HOME | covered | test_plist_is_rendered_with_the_sandbox_home, test_launch_agent_is_unloaded_then_loaded |
| install.sh: second run is idempotent | covered | test_second_run_is_idempotent |
| install.sh: __pycache__ in hooks/ does not abort it | covered | test_pycache_in_hooks_neither_aborts_nor_gets_copied |
| install.sh: missing ~/Library/LaunchAgents is created | covered | test_missing_launch_agents_dir_is_created |
| CI: macos-latest, Python 3.11–3.13, pytest, shellcheck, ruff, bash -n | covered | `.github/workflows/ci.yml` — see "How CI runs" |
| Harness self-checks | extra | tests/test_harness.py — shims resolve first on PATH, `python3` is the test interpreter, fake engine answers |

## What is not covered, and why

- **`bin/shush` and `bin/recmode off`.** Both use a machine-wide `pkill` on `ffplay`/`play_reply.sh`. Running the real ones from a test would kill the owner's live playback, so the sandbox swaps `shush` for a logging fake that only touches the stop flag. Consequently "the next reply kills the previous player" is not asserted; play_reply.sh's stop-flag path is (that is the path shush actually relies on). Stage 2's Python `shush` should be scoped to the sandbox's own processes so this becomes testable.
- **`bin/recmode` in general.** Only exercised indirectly: `tts on` runs the real `recmode` prune inside the sandbox. Its own contract (list, prune of dead sessions, `off`) has no tests.
- **The real Kokoro model and real audio output.** `engine/server.py` is tested with `KPipeline` mocked; the torch-tensor audio branch (`audio.detach().cpu().numpy()`) is not run because the fake yields numpy arrays. Nothing plays through a sound device; `ffplay`'s format flags (`-f s16le -ar 24000 -ch_layout mono`) are not asserted — Stage 2's stereo-stream contract test is the natural owner.
- **The `__main__` bind of the server on `KOKORO_PORT`**, the `LOCK` serialisation under concurrent POSTs, and the swallowed `BrokenPipe` on client disconnect.
- **launchd.** `launchctl` is a shim; whether launchd accepts the rendered plist is not exercised (a `plutil -lint` would be a cheap extra).
- **The model download and real `uv venv` / `uv pip install`.** Stubbed by design per the plan: the fake `uv` logs its argv and creates an empty venv.
- **install.sh's `uname != Darwin` guard and the brew remedy strings.** Only reachable by faking `uname`, which would test the fake.
- **The ElevenLabs branch** of `speak_last_reply.sh`, `play_reply.sh` and `bin/replay`'s `.mp3` fallback — would need `api.elevenlabs.io` stubbed; Kokoro is the product.
- **`lastreply/` housekeeping** (`find -mtime +2 -delete`), a present-but-empty `<sid>.txt` falling through to the recording, no-argument replay choosing among `.pcm` files only, engine-unreachable (`curl` fails) on the kokoro path in play_reply.sh — all small, none in the plan's list.
- **tts_toggle.sh whitespace tolerance** beyond outer whitespace/case/punctuation for `tts on`/`tts off` (see table).
- **Windows, Intel, older macOS** — Stage 2 and the README's honest list.

## Bugs found and fixed

All four were found by a failing test committed before the fix; none of the tests was adjusted to pass.

| Symptom a user would see | Test that pins it | Fix commit |
|---|---|---|
| With the default local engine, no `ELEVENLABS_API_KEY` anywhere, an armed session said nothing: `speak_last_reply.py` exited 1 with "no api key" and the hook silently gave up — the README's "no API key" promise was false. | `tests/test_speak_last_reply_key.py` (failing test in 807249d) | 8467802 |
| A transcript line that is valid JSON but not an object (`null`, `[]`, a bare string), or an assistant line with `"message": null`, crashed the Stop hook with `AttributeError`, so an armed session went silent on that reply. | `test_speak_last_reply_py.py::test_a_non_object_json_line_does_not_abort_the_hook` (message:null case pinned in cac049b) | c583a6c |
| Two sentences whose lengths summed to exactly the limit produced a 121-char first chunk or a 401-char later chunk, against the documented 120/400 — the fit check forgot the joining space. | `test_chunk_text.py::test_chunk_limits_hold_when_two_sentences_sum_exactly_to_the_limit[first-over-by-the-space, later-over-by-the-space]` | 5a02a69 |
| `install.sh` died with a Python traceback and "INSTALL FAILED at line 62" when `~/.claude/settings.json` already held an empty list for `Stop` or `UserPromptSubmit`; the hooks were never registered. | `test_install.py::test_settings_with_an_emptied_hook_list_still_get_registered` | ae1ab14 |

## Behaviour changes

`tests_adjusted` is empty — no test was changed to make code pass. Three fixes change what a user sees, each with its reason:

- **Local engine no longer needs an ElevenLabs key** (8467802). Reason: the key is only used by the `elevenlabs` engine; requiring it for Kokoro contradicted the README and silenced the default install. The key file is still written (empty), so `play_reply.sh` is unchanged.
- **Exact-sum chunk boundary** (5a02a69). Reason: the documented limits are 120/400 and the joining space counts; in the exact-sum case the second sentence now starts the next chunk — same words, same order, one more engine request, nothing audible.
- **Installer completes on an emptied hook list** (ae1ab14). Reason: an empty list is a valid settings.json state and the installer's job is to register the hooks; the non-empty path is byte-for-byte unchanged.

Refactors with no behaviour change, proven by byte-identical old-vs-new runs: `speak_last_reply.py` made importable (`main()` + functions, 0ec65c6; `TTS_VOICE_ID` is now read inside `main()`), ruff/shellcheck cleanups (8abf28b, 565a603; the inert shebang on `engine/server.py` was removed).

## How CI runs

`.github/workflows/ci.yml`: on push to `main` and on every pull request, one job `test` on `macos-latest`, matrix Python 3.11 / 3.12 / 3.13, `fail-fast: false`. Steps: checkout, setup-python, `pip install -r requirements-dev.txt` (pytest, numpy, ruff, shellcheck-py — `shellcheck` comes from the wheel, no brew), then `bash -n install.sh hooks/*.sh bin/*`, `shellcheck install.sh hooks/*.sh bin/*`, `ruff check .`, `pytest -q`. Locally the same thing is `uv run --isolated --no-project --python 3.12 --with-requirements requirements-dev.txt -- pytest -q` or `pip install -r requirements-dev.txt && pytest -q`; runtime is about 70 s, dominated by the fake player's 0.15 s per chunk.

## Decisions left to Juan

1. **Required status check on `main`.** The plan says "green required to merge", but that is a branch-protection setting, not something the workflow can express. Not enabled here because it would also block your direct pushes to `main` (README edits and the like). If you want it: repository settings → branches → require the `test` check.
2. **Pin `ruff` (and `shellcheck-py`) to an exact version.** ruff's *default* rule set is version-dependent (413 rules at 0.16.6); `requirements-dev.txt` only sets floors, so a future ruff release could turn CI red with no code change. A pin trades that for a periodic bump.
3. **Apply the fixes to the live install** by re-running `install.sh` after filming — the plan puts that in your hands, and nothing here touched `~/.claude/automation`.
4. **`shush`'s machine-wide `pkill`.** Fine for one user on one Mac; it is what makes it untestable and it would also kill any other `ffplay` on the machine. Worth scoping to Talkback's own processes in Stage 2 (a pid file or `pkill -f` on the automation path).
5. **The `.spoken-<sid>` duplicate marker is written before the engine-up check and before playback**, so a reply lost to a downed engine is still suppressed as a duplicate if identical text comes back within 120 s. Deliberate per the script comment; your call whether that is the behaviour you want.

## Parked

Deduplicated from every phase's list; none of it is in the plan.

**Rewriter (speak_last_reply.py)**
- `Details:` followed by a fenced block speaks as "Details:. shown on screen." (colon then period).
- `\S*/\S*` deletes any slash token outright: "and/or", "input/output" vanish from the spoken sentence.
- Dated slugs like `2026-09-08-stage1` become "an I D" under the 12+ char rule — consistent with the rule, odd to the ear.
- Consecutive text blocks in one assistant turn are joined with `""`, so "Hello" + "world" speaks as "Helloworld"; rare in real transcripts.
- Long-sentence splitting on commas would be a feature, not a fix; a single sentence over 120/400 is sent whole (pinned as current behaviour).

**chunk_text.py**
- Crashes with a traceback (no count printed) when the output dir does not exist; play_reply.sh always creates it, so untested and undefined.
- The splitter only recognises `. ! ?` followed by whitespace; a long bulleted reply with no full stops lands in one chunk and loses the fast first-chunk start.
- Runs at import (reads `sys.argv` at module level), so only testable via subprocess; a `main()` refactor like speak_last_reply.py's would allow in-process tests.

**tts_toggle.sh**
- `| xargs` in the prompt-normalising pipeline has no `2>/dev/null`: a pass-through prompt containing an apostrophe ("don't stop") exits 0 correctly but leaks `xargs: unterminated quote` to stderr; and xargs strips shell quotes, so `"stop"` typed with double quotes matches the bare word. One quote-agnostic trim (`sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//; s/[[:space:]]+/ /g'`) fixes both.
- The internal double-space case branch is dead (xargs collapses whitespace).

**play_reply.sh**
- Latent race on the stop path: `kill "$FPID"` targets the backgrounded `fetch` subshell, not the curl inside it; wins today because the kill follows the fork within microseconds (40/40 measured). Hardening: `fetch() { exec curl ...; }` and call the foreground one as `( fetch 0 ) || DL=1`.
- `cat "$TMP"/a*.pcm` relies on glob order, which would misorder at 10+ chunks; unreachable because the text is capped at 2500 chars.
- The background announcement pipeline inherits stdout, so a caller that captures stdout waits for ffplay to exit; harmless in production.

**verify_audio.sh**
- Keys the JSON/HTML check on the literal first byte; an error body with leading whitespace is reported as "only N bytes" instead of its text.

**engine/server.py**
- A blank POST is logged to `requests.log` (chars=0) before the 400 is sent; moving the check first is a one-line tidy.
- Non-ASCII / invalid UTF-8 bodies not pinned.

**bin/**
- `bin/kokoro-server`, `bin/replay`, `bin/recmode` use `ls` for counting/newest-file selection (SC2012 disabled with reasons); a `stat -f %m` loop would be a rewrite, only worth it if a filename problem shows up.
- The disk-fallback path of `bin/replay` writes nothing to `speak.log`; a log line would be a behaviour change needing a plan entry.

**install.sh**
- `ensure()` always appends into `blocks[0]`; a pre-existing `Stop` block with a non-empty matcher would receive the talkback hook under that matcher.
- "INSTALL FAILED at line 62" points at the heredoc's closing line for any failure inside the settings.json Python, not the failing line.
- Parametrising the missing-prerequisite test over espeak-ng / uv / ffplay; `plutil -lint` on the rendered plist.

**Test harness**
- (Fixed in this PR, noted for the record.) On a machine whose only `jq` is Homebrew's, conftest symlinks it into the shim dir and test_install used to write its jq shim *through* that symlink, clobbering the real binary. Never fired here (`/usr/bin/jq` exists); `_write_exec` in test_install.py now unlinks a symlink before writing.
- Add `tts  on`, `SHUSH`, `Say That Again` to the toggle tolerance tests.
- After install.sh runs in a sandbox, `home/bin/shush` is the REAL shush; nothing calls it, and nothing must.
- The fake shush cannot end a running play_reply.sh; a sandbox-scoped kill was deliberately not added under the no-pkill rule.
- `TMPDIR` in the sandbox env is ignored by macOS `mktemp -d`; hooks' scratch dirs land in the Darwin per-user temp dir as in production and play_reply.sh cleans its own.

**Docs**
- The `engine/server.py` per-file E402 ignore could document why the stdlib import sits below the warnings filter.
- A process note from the audit: one read-only `ls`/`stat` was run against `~/Library/LaunchAgents/com.icg.talkback.plist` and `~/.claude/settings.json` to confirm the live install was untouched; nothing was written or executed there. Reported rather than hidden.

## Gate to Stage 2

Stage 2 (Python runtime, `sounddevice`, no `jq`, Windows logic in CI) must keep all 145 of these passing **unchanged** — any behaviour change needs a test change in the same PR with a one-line reason. Specifically it must keep: the 120/400 chunk contract including the exact-sum boundary; "shown on screen" for fences and the sayable/unlistenable inline-code split; the URL/email/id/time rewrites; the transcript rules (tool-only turns skipped, malformed and non-object lines ignored, message:null tolerated); the exact toggle phrases and exit codes; text saved before playback and the 120 s duplicate guard; one play per chunk, pipelined fetch, stop-flag honoured, `.pcm` only on completion, no `.part`; replay preferring text over recording and exit 1 on nothing; the server contract (L16 PCM, 400 on blank, device override, one warm-up); and the installer's file layout, plist rendering, idempotence and settings.json merge. Shell-specific tests get replaced by equivalent Python tests in the same PR. Add the stereo-stream test the plan requires. Stage 2 also inherits the `shush` scoping decision above — a Python `shush` that only kills Talkback's own player would make the one untestable piece testable.
