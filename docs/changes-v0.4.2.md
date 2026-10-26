# v0.4.2 — Epidemic Sound (and other subscription-library) tracks are no longer reported free to use

Minor release. Corrects over-permissive verdicts for paid-subscription music
libraries; no breaking changes.

- Release: <https://github.com/Renderdragonorg/looney-checks/releases/tag/v0.4.2>
- Previous version: `0.4.1`
- Compare: <https://github.com/Renderdragonorg/looney-checks/compare/v0.4.1...v0.4.2>

---

## 1. Why

A Spotify check of an **Epidemic Sound** track returned `free_to_use`, but
Epidemic Sound is a paid subscription library. Its catalog is marketed as
"royalty-free", yet that clearance exists only for creators with an active
paid subscription — so for anyone who does not subscribe, using the track is
exactly what requires payment.

The AI researcher reads the library's own marketing copy (the "royalty-free"
label on Spotify, the Epidemic Sound site) and reports the track as free.
"Royalty-free" in production-music-library marketing means *no per-use
royalties for paying subscribers*, not *free to use*.

## 2. What changed

Two layers of correction:

1. **Research prompt** — `prompts.py`, `RESEARCH_PROMPT_VERSION` `6 → 7`:
   the instructions now explicitly state that a track whose rights holder,
   label, or publisher is a subscription production-music library (Epidemic
   Sound, Artlist, Musicbed, Soundstripe, Uppbeat, PremiumBeat, …) is **not**
   `free_to_use` — use `clearance_required` instead, unless the track is
   genuinely public domain or the library verifiably grants a free tier (with
   a cited source). The version bump re-keys the research cache, so results
   cached under the old prompt re-run research on their next check.

2. **Deterministic guard** — `pipeline.py::_guard_subscription_library_result`:
   after every check (fresh **or** served from the research cache), if the
   track's `label` or any research match's label / publisher / rights-holder /
   licence-type names a known subscription library, any `free_to_use` verdict
   is downgraded to `clearance_required` and a caveat is added:

   > *Epidemic Sound is a paid subscription library: its catalog is only
   > cleared for creators with an active subscription, so this track is not
   > free to use.*

   Because the guard runs on cached results too, stale AI output is corrected
   immediately without waiting for the research cache to expire.

## 3. Impact

- Subscription-catalog tracks can no longer come back `free_to_use`.
- Other verdicts (`clearance_required`, `permitted_with_conditions`, …) are
  left untouched, as are tracks on ordinary labels or genuinely free-source
  channels.
- Cached results keyed under prompt version `6` are invalidated by the
  version bump and re-researched once.
- Request and response shapes are unchanged; adding the caveat is the only
  visible change.

## 4. Verifying

```bash
python -m pytest tests/test_subscription_guard.py
```

A subscription-library label must never yield a free verdict, e.g.
`TrackMetadata(label="Epidemic Sound")` + `free_to_use` → downgraded to
`clearance_required` with the explanatory caveat, while
`label="Chill🍓 Records"` + `free_to_use` stays untouched.

## 5. Upgrade notes

- Version bumped `0.4.1 → 0.4.2` (`pyproject.toml`, `__version__`).
- No configuration changes. Subscribers see the same verdicts as before; the
  caveat simply explains that the library requires a paid subscription.
- This remains research assistance, not legal advice.
