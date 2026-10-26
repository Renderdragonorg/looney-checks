# v0.4.2 — Subscription libraries are not free

Minor release. Corrects over-permissive verdicts; no breaking changes.

- Previous version: `0.4.1`
- Compare: <https://github.com/Renderdragonorg/looney-checks/compare/v0.4.1...v0.4.2>

---

## 1. Why

A Spotify check of an **Epidemic Sound** track returned `free_to_use`, but
Epidemic Sound is a paid subscription library: its catalog is marketed as
"royalty-free", yet that clearance exists only for creators with an active
paid subscription. The AI researcher reads the library's own marketing copy
(the "royalty-free" label, Spotify listing) and reports the track as free,
which is exactly backwards for someone who does not subscribe.

## 2. The fix

Two layers:

1. **Prompt rule** (`prompts.py`, `RESEARCH_PROMPT_VERSION` `6 → 7`): the
   research instructions now explicitly state that tracks whose rights
   holder, label, or publisher is a subscription production-music library
   (Epidemic Sound, Artlist, Musicbed, Soundstripe, Uppbeat, PremiumBeat, …)
   are **not** `free_to_use` — use `clearance_required` instead, unless the
   track is genuinely public domain or the library verifiably grants a free
   tier (with a cited source). The version bump re-keys the research cache,
   so cached results from the old prompt re-run research on their next check.

2. **Deterministic guard** (`pipeline.py::_guard_subscription_library_result`):
   after every check (fresh **or** served from the research cache), if the
   track's `label` or any research match's label/publisher/rights-holder/
   licence-type names a known subscription library, any `free_to_use` verdict
   is downgraded to `clearance_required` and a caveat is added, e.g.
   *"Epidemic Sound is a paid subscription library: its catalog is only
   cleared for creators with an active subscription, so this track is not
   free to use."* This corrects even cached AI output that was produced
   under the old prompt, without waiting for the cache to expire.

## 3. Impact

- Subscription-catalog tracks can no longer come back `free_to_use`.
- Verdicts like `clearance_required` / `permitted_with_conditions` are left
  untouched; so are tracks whose label belongs to an ordinary label or a
  genuinely free-source channel.

## 4. Also in this release

- `tests/test_subscription_guard.py`: coverage for the Epidemic Sound label
  downgrade, match-based detection, the full library list, non-interference
  with non-free verdicts and ordinary labels, single-caveat behaviour, and
  the prompt-version bump.

## 5. Upgrade notes

- Version bumped `0.4.1 → 0.4.2` (`pyproject.toml`, `__version__`).
- No configuration changes. Subscribing users get the same result text — the
  caveat simply explains that the library requires a subscription.
- This remains research assistance, not legal advice.
