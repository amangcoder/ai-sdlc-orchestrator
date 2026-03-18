---
name: text-to-figma
description: Generate Figma-ready Flutter screen designs from text descriptions or orchestrator UX specs
---

Generate visual screen designs for the Flutter mobile app using the text-to-figma pipeline.
Each screen goes through: **wireframe → HTML generation → Playwright render → LLM visual review**.
Output is `final.html` per screen (import into Figma via the `html.to.design` plugin).

## Steps

1. **Determine the prompt:**
   - If an argument was provided, use it as the screen description.
   - Otherwise ask: *"Describe the Flutter screen(s) to design (e.g. 'login screen with email/password, dashboard with run list, settings screen')"*

2. **Check for an existing UX spec:**
   - Look for `workspace/artifacts/ux_spec.json` in the current working directory.
   - If it exists, use `--from-prd workspace/artifacts/ux_spec.json` instead of `--prompt`.

3. **Build the command** using these Flutter-specific defaults:
   - Viewport: `mobile` (375×812 — Flutter default device)
   - Style hint: `"Flutter Material Design 3, seed color #1565C0, rounded cards 12px, system dark/light mode, clean sans-serif typography, no external images"`
   - Max rounds: `3` (good quality/speed balance; increase to 5 for polish)

4. **Run text-to-figma** from its project directory:

   **With a text prompt:**
   ```bash
   cd /Users/amangupta/Projects/text-to-figma && node src/cli.js generate \
     --prompt "<user description>" \
     --viewport mobile \
     --style-hint "Flutter Material Design 3, seed color #1565C0, rounded cards 12px, system dark/light mode, clean sans-serif typography, no external images" \
     --max-rounds 3
   ```

   **With UX spec artifact (if found):**
   ```bash
   cd /Users/amangupta/Projects/text-to-figma && node src/cli.js generate \
     --from-prd "<absolute path to ux_spec.json>" \
     --viewport mobile \
     --style-hint "Flutter Material Design 3, seed color #1565C0, rounded cards 12px, system dark/light mode, clean sans-serif typography, no external images" \
     --max-rounds 3
   ```

5. **Report results:**
   - Show the output directory path (`output/<timestamp>-<slug>/`)
   - List each generated screen and its `final.html` path
   - Print the clarity scores and pass/fail status from the judge
   - Remind the user: **Open Figma → Plugins → `html.to.design` → import each `final.html`**

## Common options to expose to the user

| Flag | Purpose |
|------|---------|
| `--dry-run` | Wireframes + design tokens only — fast preview, no rendering |
| `--screens <names>` | Target specific screens, comma-separated (e.g. `login,dashboard`) |
| `--max-rounds <n>` | Refinement iterations per screen (1–10, default 3) |
| `--style-hint <text>` | Append extra design guidance (appended to the Flutter defaults above) |
| `--refine <html-path>` | Refine an existing screen's HTML instead of generating from scratch |

## Example invocations

```bash
# Basic — describe screens inline
/text-to-figma "login screen, home dashboard with run cards, settings screen"

# Dry run — just wireframes, skip rendering
/text-to-figma --dry-run "onboarding flow with 3 steps"

# Target specific screens from an existing run
/text-to-figma --screens "dashboard,settings" "orchestrator mobile app"

# Refine a specific screen
/text-to-figma --refine output/20250318-orchestrator/dashboard/round-2.html --screens dashboard
```

## Notes

- The text-to-figma project lives at `/Users/amangupta/Projects/text-to-figma`
- Run `node src/cli.js setup` once if Chromium isn't installed yet
- The `feedback-store.json` inside text-to-figma persists lessons across runs — designs improve over time
- The Flutter app theme (`app.dart`): seed `#1565C0`, Material 3, card elevation 2, 12px radius — match these in `--style-hint` overrides
