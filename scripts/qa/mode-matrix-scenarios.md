# bridgic-browser Mode Matrix — Variant Scenarios and Expected N/A

Companion document to `run-mode-matrix.sh` + `run-cli-full-coverage.sh`. Explains
what each variant means, what it requires, which commands are expected to end
up as N/A, and the known limitations.

---

## Variants at a glance

| ID | Link mode | Display | Stealth | Full 90-cmd CLI pass | SDK differential pass |
|---|---|---|---|---|---|
| V1 | Persistent (`launch_persistent_context`) | Headless | on | yes (all 90) | yes |
| V2 | Persistent | Headed (system Chrome) | on | yes (all 90) | no |
| V3 | Ephemeral (`launch + new_context`) | Headless | on | yes (all 90) | yes |
| V4 | Ephemeral | Headed | on | yes (all 90) | no |
| V5 | CDP attach | Headless | on | yes (all 90) | yes |
| V6 | CDP attach | Headed | on | yes (all 90) | no |
| V7 | Persistent | Headless | **off** | no — 8-command smoke only | no |

---

## Environment prerequisites

| Requirement | V1 | V2 | V3 | V4 | V5 | V6 | V7 |
|---|---|---|---|---|---|---|---|
| macOS GUI | | yes | | yes | | yes | |
| System Chrome (`QA_CHROME_BIN`) | | yes | | yes | yes | yes | |
| External Chrome bootstrapped (`setup-chrome.sh`) | | | | | yes | yes | |
| ffmpeg (for `video-start`/`video-stop`) | yes | yes | yes | yes | yes | yes | |

**Skip rule**: if V2/V4/V6 detects that `QA_CHROME_BIN` is empty, the entire
variant is skipped and its TSV records `(variant) | N/A | - | QA_CHROME_BIN unavailable`.

---

## Expected N/A and FAIL per variant

### V1 — Persistent × Headless × Stealth=on (baseline)
Expected: **PASS ≥ 90, FAIL = 0**. This is the configuration validated on
2026-04-21; any command regression here is a real regression.

### V2 — Persistent × Headed × Stealth=on
- In headed mode the stealth JS init script is skipped (see the Stealth section
  of `CLAUDE.md`), so `verify-*` commands that depend on animation/render
  timing can be flaky:
  - `wait_text` / `wait_gone` (depend on the `Show Text Later` animation)
  - `verify-visible` assertions can time out under slow rendering
- `search` depends on the live search engine; headed mode can also surface
  consent banners / popups that affect success rate.

### V3 — Ephemeral × Headless × Stealth=on
- `storage-load`: the `storage-state.json` has just been produced by the
  preceding `storage-save` and should replay successfully. However, in
  ephemeral mode every `launch()` creates a brand-new context, so if
  `storage-load` is called before a `navigate_to` (which is the current script
  order) its effect may be limited.
- `cookies-domain` filter: under ephemeral mode `example.com` cookies may not
  exist; the command should still return an empty list rather than error.

### V4 — Ephemeral × Headed × Stealth=on
Inherits all risks from V2 and V3. Headed + ephemeral is the combination
closest to how an end user actually uses the tool.

### V5 — CDP × Headless × Stealth=on
**Expected N/A commands** (CDP-borrowed-context limitations):
- `video-start` / `video-stop`: in a borrowed CDP context `start_video`
  spins up its own CDP screencast session, which can conflict with the
  DevTools session held by the external Chrome — needs human review.
- `storage-save` / `storage-load`: in borrowed mode these go through the
  DOMStorage CDP protocol; if the origin has no active frame Playwright
  raises `Frame not found`.
- `close`: in CDP mode this only detaches; it does not kill the external
  Chrome. The CLI command itself should still return 0 and be PASS.
- The inline `open --cdp` smoke is auto-marked `N/A (already covered by variant V5)`.

**Expected behavioral differences**:
- Stealth launch args cannot be applied to an already-running Chrome; only
  the JS init script gets injected.
- `navigate_to --wait-for networkidle` may be slower than in persistent mode
  because the external Chrome has unrelated background traffic.

### V6 — CDP × Headed × Stealth=on
Inherits every risk from both V5 and V2. It is the most fragile combination;
its main purpose is to cover the "user already has Chrome open, let the agent
take it over" usage scenario.

### V7 — Persistent × Headless × Stealth=off (smoke)
Runs only 8 core commands: `open → info → snapshot -i → reload → eval →
screenshot → verify-title → close`.
- Purpose: quickly verify that the `{"stealth": false}` code path does not
  crash.
- Expected: **all PASS**. A FAIL here means the stealth-off path has a real
  bug — priority L0.

---

## Known cross-variant systemic risks (from `cr_pr21_cdp_url_findings.md`)

Watch these closely in any CDP-related variant (V5/V6):

1. **Scattered timeout constants**: `navigate_to`, `close`, `start_video`,
   etc. each hardcode their own timeout. If a command FAILs under V5/V6,
   check the log for a "timeout" exception.
2. **Post-CDP-reconnect latency**: prior CRs repeatedly hit "reconnect
   succeeded but the first subsequent command hangs". Review V5/V6 logs for
   any command taking unusually long (>15 s).
3. **Snapshot prefetch pollution**: under V3/V5 if the refs returned by
   `snapshot` do not match the current page, the usual cause is that the
   ephemeral/CDP prefetch logic failed to notice a page reload.

## Known limitations (must be surfaced in the report)

- **Headed variants require a macOS GUI**: on a headless CI they are skipped
  wholesale.
- **CDP variants depend on `QA_CHROME_BIN`**: if the system Chrome cannot be
  found the whole variant is skipped.
- **Network-dependent commands** (`search`, `wait-network`,
  `open_for_network`, …) are affected by network flakiness; a sporadic FAIL
  is not necessarily a regression and needs log-based human judgment.
- **`video-start` / `video-stop`** require ffmpeg; when `which ffmpeg` is
  empty these commands will FAIL.
- **`get_element_by_prompt`** requires `OPENAI_API_KEY + OpenAILlm`; the SDK
  differential pass marks it N/A explicitly.

---

## How to run

```bash
cd /Users/nicecode/Desktop/bitsky-tech/bridgic-browser-3
source scripts/qa/env.sh

# Full 7-variant matrix
bash scripts/qa/run-mode-matrix.sh

# Targeted subsets (recommended for incremental runs)
BRIDGIC_QA_VARIANTS="V1"       bash scripts/qa/run-mode-matrix.sh   # baseline regression
BRIDGIC_QA_VARIANTS="V3 V5 V7" bash scripts/qa/run-mode-matrix.sh   # all headless + smoke
BRIDGIC_QA_VARIANTS="V2 V4 V6" bash scripts/qa/run-mode-matrix.sh   # all headed (needs macOS)

# Skip the SDK differential pass
BRIDGIC_QA_SDK=0 bash scripts/qa/run-mode-matrix.sh

# Run the SDK differential pass standalone (no CLI)
BRIDGIC_QA_VARIANT=V1 uv run python3 scripts/qa/run-sdk-coverage.py --variant V1
```

## Report layout

```
$QA_DIR/
├── cli-full-coverage/
│   ├── V1/coverage-results.tsv     # 90-command CLI result per variant
│   ├── V1/logs/*.log
│   ├── V1/artifacts/…
│   └── …
├── sdk-coverage/
│   ├── V1/results.tsv              # SDK differential result (V1/V3/V5 only)
│   └── …
└── mode-matrix/
    └── mode-matrix-report.md       # aggregated Markdown report
```
