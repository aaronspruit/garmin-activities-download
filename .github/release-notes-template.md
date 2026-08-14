## Highlights

{{RELEASE HIGHLIGHTS}}

<!--
Replace the placeholder above before publishing the draft, then delete this
comment. Write for someone upgrading a running container, not for someone
reading the diff.

Breaking changes deserve a callout block rather than a bullet. Two kinds
recur here, so check for both:

  - A renamed or removed env var, which stops an existing deployment at
    startup.
  - Anything that moves a file under OUTPUT_DIR (a changed folder layout, a
    change to how downloaded files are named). Dedup is path-based, so
    existing downloads stop being recognised and the whole DAYS_BACK window
    is fetched again on the next run.

Say plainly what breaks and what the operator has to do about it:

> [!WARNING]
> **Breaking:** `OLD_VAR` is replaced by `NEW_VAR`, and setting both now
> fails at startup. See the migration notes in the README.

Otherwise a short paragraph per notable feature is enough. Everything routine
is already covered by the generated sections below.
-->

---
