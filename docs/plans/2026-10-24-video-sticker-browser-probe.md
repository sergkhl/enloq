# Reliable video-sticker browser previews

Last meaningful update: 2026-10-24

The owner asked to investigate and fix the remaining VPS video-sticker browser
journey failure. Origin was pulled before development. ADR 46 Q10 and ADR 32
own the existing behavior: animate only after transparency is proved, preserve
still fallbacks and reduced motion, and keep private media behind the gate.

## Work and acceptance

1. Reproduce the failure with bounded browser diagnostics against the authored
   disposable fixture. Distinguish timing, visibility, decoding and cancellation.
2. Fix the demonstrated cause and add a deterministic behavior regression.
   Keep the five-second bound, source validation, cancellation and media limits.
3. Run focused repeated VPS journeys and the required full source gate. Review
   the change, commit and push main, fetch and verify GitHub equality, then
   deploy the VPS service and verify its health and served web build.
4. Record evidence before closing this plan in a later commit. Remove the
   watched-limit entry only after its cause is fixed and verified.

## Validation log

- Baseline isolated VPS journey passed once in 5.0 seconds. The previous full
  run and isolated rerun failed. Diagnostic repeats reproduced the failure.
- The first-frame regression failed before the fix. The fix keeps blank samples
  pending within the original deadline and cancels their animation frame on finish.
- Focused checks passed the original media journey, blank-frame recovery and
  deadline cleanup. The cancellation journey initially used the wrong Back
  label; independent review found it and it was corrected to the actual
  Back to conversations control. Review found no runtime defect in the fix.
- Changed TypeScript and journey formatting passed. The new journey file also
  passed a strict standalone TypeScript check.

- Five repeated VPS runs of the original video-sticker journey and each new
  regression all passed: 20/20 in 1.3 minutes, with no retries. The full
  ./scripts/check gate then passed in full on the VPS: 3,876 backend tests,
  all 53 phone-size browser journeys (including the original failure and three
  regressions), and 297 mobile tests in 38 suites. Backend lint/format, web and
  mobile TypeScript and mobile/shared formatting passed. The backend retained
  its existing single warning; the web build retained its chunk-size advisory.
- The watched-limit entry is removed: the reproduced cause is now covered by
  executable regressions. Delivery remains tracked by this active plan.

- No mobile/shared/API-schema files changed, so this fix does not require a
  native build. No live Telegram sends or provider calls were used for testing.
- Main 84b76c356fcc53d99420a6a81533aedef83b415c was committed and pushed;
  origin/main was freshly fetched and matched the clean main checkout before
  ./scripts/deploy. The service started enloq:84b76c356fcc and became healthy.
- Public verification at 2026-10-24 03:09 UTC returned ready from /health and
  matched the public HTML, JavaScript, CSS and 718-byte capability canary to
  their SHA-256 hashes inside the deployed image. The image revision label
  matches the committed fix. This is deployment proof; the authored browser
  journeys provide behavior proof, with native-device acceptance unchanged.
- The disposable journey server stopped after its checks. Review and all
  required checks are complete. This validation is committed before the plan
  is removed; the helper, regressions and media runbook retain the behavior.

## Findings

The failing trace showed loadeddata with readyState 4, then a completely
zero canvas at 242 ms. The helper immediately resolved unavailable; its deadline
was not reached. Passing runs read the expected opaque green marker and
transparent corners. The deterministic blank-frame regression failed against
the unchanged helper with the same missing video element.
