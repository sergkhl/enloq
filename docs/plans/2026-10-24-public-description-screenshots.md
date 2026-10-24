# Public description and screenshots

Last meaningful update: 2026-10-24

The owner accepted the product-first description, three screenshot story and
high-resolution phone-width web captures. They requested pulling origin before
work and completing the name origin. Both checkouts and the VPS were pulled
before development. [ADR 57](../adr/0057-public-description-and-screenshots.md)
owns the presentation decisions.

## Work and acceptance

1. Capture the real web interface at 430 × 860 CSS pixels and 3× scale, using
   the disposable fixture and authored conversations and prepared outputs.
   Show calming, an editable suggested reply and a source-linked briefing.
2. Replace the README screenshot with all three, add short captions and the
   complete name origin. Preserve the distinction between UI demonstration,
   provider behavior, native acceptance and deployment.
3. Update the portfolio card and case study, include all three images and
   source links, and check desktop and mobile presentation.
4. Run the repository source gate and portfolio checks. Commit and push the
   GitHub changes. Deploy the portfolio only from clean main after fetching
   and verifying it equals GitHub main. Verify public content and image bytes.
5. Record outcomes here before deleting this delivered plan in a later commit.
   Documentation and screenshots do not change Enloq runtime behavior or call
   for a new native build.

## Validation log

- Captured all three real web views at 1290 × 2580 with authored synthetic
  messages and prepared outputs. Browser actions proved the original/AI toggle,
  suggestion insertion and subsequent draft editing, retained draft after
  reload, and briefing citation/return navigation. No provider calls, live
  Telegram messages or native-device checks were used.
- Portfolio: production build, all 13 tests and lint passed (three img advisory
  warnings). Browser review at 1440 px and 390 px found no horizontal overflow
  or console errors. The homepage portrait frame was enlarged so its controls
  and text remain visible.
- Portfolio main 4b241b3 was pushed to GitHub, fetched again and matched before
  deployment. Release 20261024T022045Z-4b241b3 was activated. HTTPS verification
  confirmed the new description, full name origin, three gallery images and
  source links. Each public PNG exactly matches its local SHA-256. The other
  case routes and CV returned 200; a missing path returned 404.
- Enloq source checks: backend lint/format and 3,876 tests passed; web typecheck
  and 49 of 50 phone-size browser journeys passed. The existing video-sticker
  VP9 probe issue reproduced in the suite and on one isolated rerun. Source
  files are unchanged. The unchanged media journey passed on the Mac in 6.3 s.
  A separate VPS canary returned VP9-alpha support in 9 ms, so categorical
  decoder incompatibility is ruled out; the failed journey's lifecycle cause
  is unresolved. The full VPS source command is not reported as green; its
  existing TODO issue remains open.
- Mobile TypeScript and formatting passed; all 297 Jest tests in 38 suites
  passed. This is source evidence, not native-device acceptance.
- Pending: publish the README/images to GitHub and verify their remote content.

## Open findings

The existing video-sticker journey issue remains in TODO under Watched limits.
It is outside this description/screenshots scope; no runtime source was changed.
