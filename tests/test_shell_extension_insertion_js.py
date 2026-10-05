"""Pure-logic tests for the extension's text insertion (WHISP-119).

insertion.js holds every decision the extension's TextInput.InsertText makes
(gating, commit vs paste, UTF-8 chunking, paste chord) with no gi:// or
resource:// imports, so it runs under plain Node. Never loads the extension
into a shell. Skipped when Node is not installed.
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


def test_insertion_js_has_no_shell_imports():
    imports = [
        line for line in (EXT_DIR / "insertion.js").read_text().splitlines()
        if line.lstrip().startswith("import ")
    ]
    assert imports == []


def test_insertion_js_never_logs():
    """The extension must never log the dictated text; the pure module logs
    nothing at all."""
    src = (EXT_DIR / "insertion.js").read_text()
    assert "console." not in src and "log(" not in src


def test_extension_js_has_no_gtk_gdk_imports():
    """EGO rule: no Gdk/Gtk/Adw in the shell process."""
    for name in ("extension.js", "insertion.js", "status.js"):
        src = (EXT_DIR / name).read_text()
        for lib in ("gi://Gtk", "gi://Gdk", "gi://Adw"):
            assert lib not in src, (name, lib)


_JS_CASES = r"""
import * as m from './insertion.mjs';
const R = m.Reason;
const results = [];
function check(name, got, want) {
    results.push({name, ok: JSON.stringify(got) === JSON.stringify(want), got, want});
}
const enc = new TextEncoder();
const bytes = (s) => enc.encode(s).length;

// ── UTF-8 length ───────────────────────────────────────────────────────────
for (const s of ['', 'abc', 'ñandú', '日本語', '😀x', 'á', '\u{10FFFF}'])
    check(`utf8Length ${JSON.stringify(s)}`, m.utf8Length(s), bytes(s));

// ── chunking (Wayland 4096-byte message cap) ──────────────────────────────
check('max commit bytes is under the wayland cap', m.MAX_COMMIT_BYTES <= 4000, true);
check('empty text has no chunks', m.chunkUtf8(''), []);
check('short text is one chunk', m.chunkUtf8('hola mundo'), ['hola mundo']);
{
    const ascii = 'a'.repeat(9000);
    const c = m.chunkUtf8(ascii, 4000);
    check('ascii chunk sizes', c.map(bytes), [4000, 4000, 1000]);
    check('ascii chunks rejoin', c.join(''), ascii);
}
{
    // 3-byte chars: a chunk must never split a code point.
    const cjk = '語'.repeat(3000);  // 9000 bytes
    const c = m.chunkUtf8(cjk, 4000);
    check('cjk chunks within cap', c.every(x => bytes(x) <= 4000), true);
    check('cjk chunks are whole code points', c.map(bytes), [3999, 3999, 1002]);
    check('cjk chunks rejoin', c.join(''), cjk);
}
{
    // Astral (surrogate pair) chars never split into lone surrogates.
    const emoji = '😀'.repeat(2500);  // 4 bytes each, 10000 bytes
    const c = m.chunkUtf8(emoji, 4000);
    check('emoji chunk sizes', c.map(bytes), [4000, 4000, 2000]);
    check('emoji chunks rejoin', c.join(''), emoji);
    check('no lone surrogates',
          c.every(x => !/[\uD800-\uDBFF]$/.test(x) && !/^[\uDC00-\uDFFF]/.test(x)), true);
}
check('default chunk size is MAX_COMMIT_BYTES',
      m.chunkUtf8('b'.repeat(m.MAX_COMMIT_BYTES + 1)).map(bytes), [m.MAX_COMMIT_BYTES, 1]);

// ── paste chord (layout independent: Insert, never a letter keysym) ───────
check('chord auto normal', m.pasteChord('normal', 'auto'), ['Shift_L', 'Insert']);
check('chord auto terminal', m.pasteChord('terminal', 'auto'), ['Control_L', 'Shift_L', 'Insert']);
check('chord default arg', m.pasteChord('normal'), ['Shift_L', 'Insert']);
check('chord explicit shift+insert', m.pasteChord('terminal', 'shift+insert'), ['Shift_L', 'Insert']);
check('chord explicit ctrl+shift+insert', m.pasteChord('normal', 'ctrl+shift+insert'),
      ['Control_L', 'Shift_L', 'Insert']);
check('chord explicit ctrl+v', m.pasteChord('normal', 'ctrl+v'), ['Control_L', 'v']);
check('chord explicit ctrl+shift+v', m.pasteChord('normal', 'ctrl+shift+v'),
      ['Control_L', 'Shift_L', 'v']);
check('chord unknown', m.pasteChord('normal', 'alt+f4'), null);
check('chord events press then reverse release',
      m.chordEvents(['Control_L', 'Shift_L', 'Insert']),
      [['Control_L', true], ['Shift_L', true], ['Insert', true],
       ['Insert', false], ['Shift_L', false], ['Control_L', false]]);

// ── window id comparison (GetFocusedWindow reports a uint32) ───────────────
check('same id', m.sameWindowId(42, 42), true);
check('different id', m.sameWindowId(42, 43), false);
check('uint32 truncation matches', m.sameWindowId(2 ** 32 + 7, 7), true);
check('null focus never matches', m.sameWindowId(null, 7), false);

// ── terminal-unsafe text ──────────────────────────────────────────────────
check('plain text is commit-safe', m.unsafeForTerminalCommit('ls -la'), false);
check('tab is commit-safe', m.unsafeForTerminalCommit('a\tb'), false);
check('newline is unsafe', m.unsafeForTerminalCommit('rm -rf x\n'), true);
check('carriage return is unsafe', m.unsafeForTerminalCommit('a\rb'), true);
check('escape is unsafe', m.unsafeForTerminalCommit('a\x1b[201~b'), true);
check('DEL is unsafe', m.unsafeForTerminalCommit('a\x7f'), true);

// ── decideInsertion: gating and method choice ─────────────────────────────
const base = (o = {}) => Object.assign({
    text: 'hello world', locked: false, modalCount: 0,
    focusWindowId: 10, expectedWindowId: undefined,
    imFocus: true, hasPreedit: false, purpose: 'normal',
    mode: 'auto', pasteShortcut: 'auto',
}, o);
const d = (o) => m.decideInsertion(base(o));

check('commit when IM focus', d({}), {method: 'commit', reason: '', chunks: ['hello world']});
check('empty text refused', d({text: ''}), {method: 'none', reason: R.EMPTY});
check('locked refused', d({locked: true}), {method: 'none', reason: R.LOCKED});
check('locked wins over everything', d({locked: true, modalCount: 2, text: 'x'}).reason, R.LOCKED);
check('modal refused', d({modalCount: 1}), {method: 'none', reason: R.MODAL});
check('no focus window refused', d({focusWindowId: null}), {method: 'none', reason: R.NO_FOCUS});
check('window mismatch refused', d({expectedWindowId: 11}),
      {method: 'none', reason: R.WINDOW_MISMATCH});
check('window match commits', d({expectedWindowId: 10}).method, 'commit');
check('password refused', d({purpose: 'password'}), {method: 'none', reason: R.PASSWORD});
check('password refused even in paste mode', d({purpose: 'password', mode: 'paste'}).reason,
      R.PASSWORD);
check('invalid mode refused', d({mode: 'type'}), {method: 'none', reason: R.INVALID_OPTION});
check('no IM focus pastes with shift+insert', d({imFocus: false}),
      {method: 'paste', reason: '', chord: ['Shift_L', 'Insert']});
check('preedit pastes', d({hasPreedit: true}).method, 'paste');
check('paste mode pastes', d({mode: 'paste'}).method, 'paste');
check('commit mode without IM focus refused', d({mode: 'commit', imFocus: false}),
      {method: 'none', reason: R.NO_IM_FOCUS});
check('commit mode with preedit refused', d({mode: 'commit', hasPreedit: true}),
      {method: 'none', reason: R.PREEDIT});
check('invalid paste shortcut refused on the paste path',
      d({imFocus: false, pasteShortcut: 'alt+f4'}), {method: 'none', reason: R.INVALID_OPTION});
check('long text is chunked',
      d({text: 'z'.repeat(8001)}).chunks.map(bytes), [4000, 4000, 1]);

// Terminals: never commit a newline (it would run the command line).
check('terminal plain text commits', d({purpose: 'terminal', text: 'ls -la'}).method, 'commit');
check('terminal newline uses paste with ctrl+shift+insert',
      d({purpose: 'terminal', text: 'ls\nrm -rf /'}),
      {method: 'paste', reason: '', chord: ['Control_L', 'Shift_L', 'Insert']});
check('terminal newline in commit mode refused',
      d({purpose: 'terminal', text: 'ls\n', mode: 'commit'}),
      {method: 'none', reason: R.TERMINAL_NEWLINE});
check('terminal without IM focus pastes ctrl+shift+insert',
      d({purpose: 'terminal', imFocus: false}).chord, ['Control_L', 'Shift_L', 'Insert']);
check('non-terminal newline commits', d({text: 'a\nb'}).method, 'commit');

// ── capabilities ──────────────────────────────────────────────────────────
{
    const caps = m.capabilities(5);
    check('caps insert_text', caps.insert_text, true);
    check('caps methods', caps.methods, ['commit', 'paste']);
    check('caps max commit bytes', caps.max_commit_bytes, m.MAX_COMMIT_BYTES);
    check('caps version', caps.version, 5);
    check('caps reasons cover every Reason', caps.reasons.slice().sort(),
          Object.values(R).slice().sort());
}

console.log(JSON.stringify(results));
"""


@needs_node
def test_insertion_js_syntax(tmp_path):
    dst = tmp_path / "insertion.mjs"
    shutil.copy(EXT_DIR / "insertion.js", dst)
    proc = subprocess.run([NODE, "--check", str(dst)], capture_output=True, text=True,
                          timeout=30)
    assert proc.returncode == 0, proc.stderr


@needs_node
def test_insertion_js_logic(tmp_path):
    shutil.copy(EXT_DIR / "insertion.js", tmp_path / "insertion.mjs")
    runner = tmp_path / "run.mjs"
    runner.write_text(_JS_CASES)
    proc = subprocess.run([NODE, str(runner)], capture_output=True, text=True, timeout=30,
                          cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    results = json.loads(proc.stdout.strip().splitlines()[-1])
    failed = [r for r in results if not r["ok"]]
    assert not failed, failed
    assert len(results) >= 50
