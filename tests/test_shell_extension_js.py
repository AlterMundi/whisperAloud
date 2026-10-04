"""Static checks and pure-logic tests for the GNOME Shell extension's JS.

- `node --check` on every module (syntax only; GNOME Shell's gi:// and
  resource:// imports are not resolved).
- status.js has no Shell-only imports, so it is imported and exercised with
  plain Node.
- metadata.json sanity (shell-version list, URL, schema present).

Skipped when Node is not installed. Never loads the extension into a shell.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXT_DIR = REPO_ROOT / "contrib" / "gnome-shell-extension" / "whisperaloud@fede"
NODE = shutil.which("node") or shutil.which("nodejs")

needs_node = pytest.mark.skipif(NODE is None, reason="node not installed")


@needs_node
@pytest.mark.parametrize("name", ["extension.js", "status.js"])
def test_js_syntax(name, tmp_path):
    # Copy to .mjs so Node parses it as an ES module regardless of version.
    dst = tmp_path / (Path(name).stem + ".mjs")
    shutil.copy(EXT_DIR / name, dst)
    proc = subprocess.run([NODE, "--check", str(dst)], capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0, proc.stderr


def test_status_js_has_no_shell_imports():
    imports = [
        line for line in (EXT_DIR / "status.js").read_text().splitlines()
        if line.lstrip().startswith("import ")
    ]
    assert imports == []


def test_metadata_shell_versions():
    md = json.loads((EXT_DIR / "metadata.json").read_text())
    assert md["shell-version"][:4] == ["45", "46", "47", "48"]
    assert all(v.isdigit() for v in md["shell-version"])
    assert isinstance(md["version"], int) and md["version"] >= 2
    schema = EXT_DIR / "schemas" / f"{md['settings-schema']}.gschema.xml"
    assert schema.exists()


_JS_CASES = r"""
import * as s from './status.mjs';
const S = s.IndicatorState;
const results = [];
function check(name, got, want) {
    results.push({name, ok: JSON.stringify(got) === JSON.stringify(want), got, want});
}
const st = (o) => Object.assign({state: 'idle', model_state: 'ready',
                                 hotkey_backend: 'portal', hotkey_trigger: 'Press Super+Alt+R',
                                 model: 'base'}, o);

check('not running', s.deriveState({running: false, status: null}), S.NOT_RUNNING);
check('starting via activation', s.deriveState({running: false, starting: true, status: null}), S.STARTING);
check('running, status pending', s.deriveState({running: true, status: null}), S.STARTING);
check('ready', s.deriveState({running: true, status: st({})}), S.READY);
check('pre-1.6 daemon (no model_state) is ready',
      s.deriveState({running: true, status: {state: 'idle'}}), S.READY);
check('loading', s.deriveState({running: true, status: st({model_state: 'loading'})}), S.LOADING);
check('downloading', s.deriveState({running: true, status: st({model_state: 'downloading'})}), S.LOADING);
check('missing', s.deriveState({running: true, status: st({model_state: 'missing'})}), S.MODEL_MISSING);
check('model error', s.deriveState({running: true, status: st({model_state: 'error'})}), S.ERROR);
check('daemon error', s.deriveState({running: true, status: st({state: 'error'})}), S.ERROR);
check('recording beats loading',
      s.deriveState({running: true, status: st({state: 'recording', model_state: 'loading'})}), S.RECORDING);
check('transcribing', s.deriveState({running: true, status: st({state: 'transcribing'})}), S.TRANSCRIBING);
check('shutdown', s.deriveState({running: true, status: st({state: 'shutdown'})}), S.NOT_RUNNING);
check('every state has an icon',
      Object.values(S).every(v => typeof s.ICONS[v] === 'string'), true);

check('hotkey bound via trigger', s.hotkeyInfo(st({})).bound, true);
check('hotkey backend none', s.hotkeyInfo(st({hotkey_backend: 'none', hotkey_trigger: ''})).bound, false);
check('portal pending is unknown', s.hotkeyInfo(st({hotkey_trigger: ''})).bound, null);
check('explicit hotkey_bound wins', s.hotkeyInfo(st({hotkey_bound: false})).bound, false);
check('hotkey null status', s.hotkeyInfo(null).bound, null);

check('fallback when daemon has no hotkey',
      s.needsFallbackKeybinding({running: true, status: st({hotkey_backend: 'none', hotkey_trigger: ''})}), true);
check('no fallback while daemon has a hotkey',
      s.needsFallbackKeybinding({running: true, status: st({})}), false);
check('no fallback when unknown',
      s.needsFallbackKeybinding({running: true, status: st({hotkey_trigger: ''})}), false);
check('no fallback while the portal binding is pending/declined (1.7.0)',
      s.needsFallbackKeybinding({running: true, status: st({hotkey_bound: false, hotkey_trigger: ''})}), false);
check('no fallback when not running',
      s.needsFallbackKeybinding({running: false, status: null}), false);

// HotkeyStateChanged (API 1.8.0, WHISP-99): applyHotkeyState patches the
// status dict the same way GetStatus's hotkey_* keys would read.
{
    const hs = st({hotkey_bound: false, hotkey_trigger: '', hotkey_backend: 'portal'});
    s.applyHotkeyState(hs, true, 'Press <Control>space', 'portal');
    check('apply hotkey state sets bound/trigger/backend',
          [hs.hotkey_bound, hs.hotkey_trigger, hs.hotkey_backend],
          [true, 'Press <Control>space', 'portal']);
    s.applyHotkeyState(hs, false, '', 'portal');
    check('apply hotkey state false clears trigger',
          [hs.hotkey_bound, hs.hotkey_trigger], [false, '']);
    s.applyHotkeyState(null, true, 'x', 'portal');  // must not throw
    check('apply hotkey state on null is a no-op', true, true);
}
check('deriveState reflects an applied HotkeyStateChanged(false)',
      (() => {
          const s2 = st({hotkey_bound: true});
          s.applyHotkeyState(s2, false, '', 'portal');
          return s.deriveState({running: true, status: s2});
      })(), S.READY_NO_HOTKEY);

// "All OK" (1.7.0): model ready AND hotkey_bound.
check('all OK', s.deriveState({running: true, status: st({hotkey_bound: true})}), S.READY);
check('ready but hotkey not bound',
      s.deriveState({running: true, status: st({hotkey_bound: false})}), S.READY_NO_HOTKEY);
check('ready, no backend, fallback grabbed',
      s.deriveState({running: true, fallbackHotkey: true,
                     status: st({hotkey_backend: 'none', hotkey_trigger: '', hotkey_bound: false})}), S.READY);
check('loading wins over hotkey',
      s.deriveState({running: true, status: st({model_state: 'loading', hotkey_bound: false})}), S.LOADING);
check('degraded style', s.STYLE_CLASSES[S.READY_NO_HOTKEY], 'whisperaloud-degraded');
check('recording style', s.STYLE_CLASSES[S.RECORDING], 'whisperaloud-recording');

check('label ready', s.statusLabel(S.READY, st({})), 'Ready');
check('label ready no hotkey', s.statusLabel(S.READY_NO_HOTKEY, st({})), 'Ready, but no global hotkey');
check('hotkey label fallback', s.hotkeyLabel(st({hotkey_backend: 'none'}), '<Super><Alt>r'),
      'Hotkey: <Super><Alt>r (via top-bar extension)');
check('hotkey label bound false wins over trigger',
      s.hotkeyLabel(st({hotkey_bound: false})), 'Hotkey: not registered');
check('autostart on', s.autostartLabel(st({autostart: true})), 'Starts at login');
check('autostart off', s.autostartLabel(st({autostart: false})), 'Does not start at login');
check('autostart unknown', s.autostartLabel(st({})), '');
check('label downloading', s.statusLabel(S.LOADING, st({model_state: 'downloading'})), 'Downloading model…');
check('label error detail', s.statusLabel(S.ERROR, st({model_error: 'boom'})), 'Error: boom');
check('label error no status', s.statusLabel(S.ERROR, null), 'Error');
check('every state has a label',
      Object.values(S).every(v => s.statusLabel(v, null).length > 0), true);

check('model label', s.modelLabel(st({})), 'Model: base');
check('model label unknown', s.modelLabel(null), '');
check('hotkey label trigger', s.hotkeyLabel(st({})), 'Hotkey: Press Super+Alt+R');
check('hotkey label none', s.hotkeyLabel(st({hotkey_backend: 'none', hotkey_trigger: ''})), 'Hotkey: not registered');
check('hotkey label unknown', s.hotkeyLabel(st({hotkey_trigger: ''})), '');

const m = st({});
s.applyModelState(m, 'missing', 'base not downloaded');
check('apply missing', [m.model_state, m.model_error], ['missing', 'base not downloaded']);
s.applyModelState(m, 'loading', 'ignored');
check('apply loading clears error', [m.model_state, m.model_error], ['loading', '']);
s.applyModelState(null, 'ready', '');  // must not throw
check('apply on null is a no-op', true, true);

console.log(JSON.stringify(results));
"""


@needs_node
def test_status_js_logic(tmp_path):
    shutil.copy(EXT_DIR / "status.js", tmp_path / "status.mjs")
    runner = tmp_path / "run.mjs"
    runner.write_text(_JS_CASES)
    proc = subprocess.run([NODE, str(runner)], capture_output=True, text=True, timeout=30,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    results = json.loads(proc.stdout.strip().splitlines()[-1])
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed
    assert len(results) >= 30
