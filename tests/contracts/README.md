# D-Bus interface contracts

This directory holds a versioned snapshot of WhisperAloud's D-Bus
introspection XML for `org.fede.whisperaloud.Control`, the interface
published by the daemon (`src/whisper_aloud/service/daemon.py`). The XML
itself lives as the `WhisperAloudService` class docstring — pydbus reads it
directly as introspection data — and `org.fede.whisperaloud.Control.xml`
here is a canonicalized copy of that docstring, checked by
`tests/test_dbus_contract.py`.

## Policy

**Changing the interface requires updating the snapshot in the same
commit**, and noting the change in the commit message. Concretely:

1. Edit the introspection XML in the `WhisperAloudService` docstring
   (add/remove/rename a method or signal arg, change a type code, etc.).
2. Regenerate the snapshot:

   ```
   python -m tests.contracts.regen
   ```

   or run the contract test with the override env var, which writes the
   snapshot in place:

   ```
   UPDATE_DBUS_SNAPSHOT=1 python -m pytest tests/test_dbus_contract.py
   ```

3. Review the resulting diff to `org.fede.whisperaloud.Control.xml` — it
   should match exactly the interface change you intended, nothing more.
4. Commit the docstring change and the regenerated snapshot together, and
   say what changed in the D-Bus interface in the commit message (e.g.
   `feat(daemon): add GetStatus.queue_depth field`).
5. If the change also affects `service/client.py` or `service/history_client.py`
   (a new method call or signal subscription), update the maintained
   `CLIENT_METHOD_CALLS` / `CLIENT_SIGNAL_SUBSCRIPTIONS` lists at the top of
   `tests/test_dbus_contract.py` in the same commit — the test fails loudly
   if they drift out of sync with the client source.

A proper changelog for the public D-Bus API (tracking this across releases,
not just across commits) is tracked separately as WHISP-62 and is not yet
implemented — this snapshot only catches drift at test time, it does not
record history.

## Why a snapshot instead of hand-written assertions

The interface has grown method-by-method over time; asserting its exact
shape field-by-field in a test would just be a second copy of the XML that
silently goes stale. A snapshot makes *any* change — intended or not —
visible as an explicit diff that must be acknowledged (by regenerating and
committing it), while `tests/test_dbus_contract.py` still asserts the
semantic properties that matter (client/daemon agreement) independently of
the snapshot.

## What's in here

- `org.fede.whisperaloud.Control.xml` — the committed, canonical snapshot.
- `regen.py` — extracts the introspection XML straight from the daemon's
  source (via `ast`, without importing the daemon module or starting a
  session bus) and canonicalizes it deterministically (declaration order is
  preserved; only whitespace/indentation is normalized). Run as
  `python -m tests.contracts.regen` to rewrite the snapshot.
