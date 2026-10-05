// Pure text-insertion logic for the WhisperAloud extension (WHISP-119).
//
// No gi:// or resource:// imports on purpose: this module is unit-tested
// with plain Node (tests/test_shell_extension_insertion_js.py). extension.js
// gathers the Shell state (lock, modal dialogs, focus window, IM focus,
// content purpose), calls decideInsertion(), and only then touches
// Main.inputMethod / St.Clipboard / the virtual keyboard.
//
// Never log the text: nothing in this module logs at all.
//
// Background (docs/research/2026-10-05-gnome-text-insertion, appendix C):
// - Main.inputMethod.commit() reaches the focused Wayland client through
//   text-input-v3 commit_string. libwayland caps a message at 4096 bytes and
//   disconnects the client on overflow, so commits are split into chunks of
//   at most MAX_COMMIT_BYTES UTF-8 bytes, on code-point boundaries.
// - Without IM focus (XWayland, apps without text-input) or with a user
//   preedit in progress, the text goes to the clipboard and a paste chord is
//   sent through a Clutter virtual keyboard. The chord uses Insert, never a
//   letter keysym: notify_keyval only searches the current layout group, so
//   Ctrl+V fails on Cyrillic/Greek layouts while Shift/Control/Insert exist
//   in every layout.
// - Terminals (VTE sets input purpose TERMINAL): a committed newline is a
//   typed Enter and runs the command line, so text with control characters
//   is never committed there; it is pasted with Ctrl+Shift+Insert (VTE's
//   CLIPBOARD paste; Shift+Insert would paste PRIMARY).

export const MAX_COMMIT_BYTES = 4000;

export const Reason = Object.freeze({
    EMPTY: 'empty',
    LOCKED: 'locked',
    MODAL: 'modal',
    NO_FOCUS: 'no-focus',
    WINDOW_MISMATCH: 'window-mismatch',
    PASSWORD: 'password-field',
    INVALID_OPTION: 'invalid-option',
    NO_IM_FOCUS: 'no-im-focus',
    PREEDIT: 'preedit',
    TERMINAL_NEWLINE: 'terminal-newline',
    // Set by extension.js, not decideInsertion(): the paste path was chosen
    // but the virtual keyboard is gone (extension being disabled).
    NO_VIRTUAL_KEYBOARD: 'no-virtual-keyboard',
});

// Content purposes as decideInsertion() sees them; extension.js maps
// Clutter.InputContentPurpose (and the hidden-text hint) onto these.
export const Purpose = Object.freeze({
    NORMAL: 'normal',
    PASSWORD: 'password',
    TERMINAL: 'terminal',
});

const MODES = new Set(['auto', 'commit', 'paste']);

const CHORDS = Object.freeze({
    'shift+insert': ['Shift_L', 'Insert'],
    'ctrl+shift+insert': ['Control_L', 'Shift_L', 'Insert'],
    // Accepted for completeness; layout-dependent (see header).
    'ctrl+v': ['Control_L', 'v'],
    'ctrl+shift+v': ['Control_L', 'Shift_L', 'v'],
});

function codePointBytes(cp) {
    if (cp < 0x80)
        return 1;
    if (cp < 0x800)
        return 2;
    if (cp < 0x10000)
        return 3;
    return 4;
}

export function utf8Length(text) {
    let n = 0;
    for (const ch of text)
        n += codePointBytes(ch.codePointAt(0));
    return n;
}

// Split `text` into pieces of at most `maxBytes` UTF-8 bytes each, never
// inside a code point (so never into lone surrogates). Joining the pieces
// gives back `text`.
export function chunkUtf8(text, maxBytes = MAX_COMMIT_BYTES) {
    const chunks = [];
    let current = '';
    let size = 0;
    for (const ch of text) {
        const n = codePointBytes(ch.codePointAt(0));
        if (size + n > maxBytes && current) {
            chunks.push(current);
            current = '';
            size = 0;
        }
        current += ch;
        size += n;
    }
    if (current)
        chunks.push(current);
    return chunks;
}

// Key names (Clutter.KEY_<name>) to press, in order, or null when the
// requested shortcut is unknown.
export function pasteChord(purpose, shortcut = 'auto') {
    if (shortcut === 'auto') {
        shortcut = purpose === Purpose.TERMINAL ? 'ctrl+shift+insert' : 'shift+insert';
    }
    const keys = CHORDS[shortcut];
    return keys ? [...keys] : null;
}

// [keyName, pressed] events: press every key in order, release in reverse.
export function chordEvents(keys) {
    return [
        ...keys.map(k => [k, true]),
        ...[...keys].reverse().map(k => [k, false]),
    ];
}

// GetFocusedWindow reports the window id as a uint32, so the daemon's
// window_id may be the low 32 bits of Meta.Window.get_id().
export function sameWindowId(actual, expected) {
    if (actual === null || actual === undefined)
        return false;
    const a = Number(actual);
    const e = Number(expected);
    return a === e || a % 2 ** 32 === e;
}

// Control characters other than tab are typed input in a terminal (a
// newline runs the command line, ESC starts a sequence): never commit them.
export function unsafeForTerminalCommit(text) {
    // eslint-disable-next-line no-control-regex
    return /[\x00-\x08\x0a-\x1f\x7f]/.test(text);
}

// Decide what InsertText does. `ctx`:
//   text, locked (Main.sessionMode.isLocked), modalCount (Main.modalCount),
//   focusWindowId (focus window id or null), expectedWindowId (the daemon's
//   window_id option, or undefined), imFocus (Main.inputMethod.currentFocus
//   is set), hasPreedit, purpose (a Purpose value; NORMAL when there is no
//   IM focus), mode ('auto'|'commit'|'paste'), pasteShortcut.
// Returns {method: 'commit', reason: '', chunks}, {method: 'paste',
// reason: '', chord} or {method: 'none', reason}.
export function decideInsertion(ctx) {
    const none = reason => ({method: 'none', reason});
    const text = ctx.text ?? '';
    if (!text)
        return none(Reason.EMPTY);
    // The lock screen and Shell modals (polkit, run dialog, overview search)
    // can hold IM focus themselves: never type into them.
    if (ctx.locked)
        return none(Reason.LOCKED);
    if ((ctx.modalCount ?? 0) > 0)
        return none(Reason.MODAL);
    const mode = ctx.mode ?? 'auto';
    if (!MODES.has(mode))
        return none(Reason.INVALID_OPTION);
    if (ctx.focusWindowId === null || ctx.focusWindowId === undefined)
        return none(Reason.NO_FOCUS);
    // WHISP-88, atomically: the window the daemon confirmed is still the one
    // with focus at the moment of insertion.
    if (ctx.expectedWindowId !== undefined && ctx.expectedWindowId !== null &&
        !sameWindowId(ctx.focusWindowId, ctx.expectedWindowId))
        return none(Reason.WINDOW_MISMATCH);
    if (ctx.purpose === Purpose.PASSWORD)
        return none(Reason.PASSWORD);

    const terminal = ctx.purpose === Purpose.TERMINAL;
    const terminalUnsafe = terminal && unsafeForTerminalCommit(text);
    if (mode !== 'paste' && ctx.imFocus && !ctx.hasPreedit && !terminalUnsafe)
        return {method: 'commit', reason: '', chunks: chunkUtf8(text)};
    if (mode === 'commit') {
        if (terminalUnsafe)
            return none(Reason.TERMINAL_NEWLINE);
        if (ctx.hasPreedit)
            return none(Reason.PREEDIT);
        return none(Reason.NO_IM_FOCUS);
    }
    const chord = pasteChord(ctx.purpose, ctx.pasteShortcut ?? 'auto');
    if (!chord)
        return none(Reason.INVALID_OPTION);
    return {method: 'paste', reason: '', chord};
}

// GetCapabilities() payload as plain values (extension.js wraps them in
// GLib.Variants). The daemon gates on insert_text, never on the version.
export function capabilities(version) {
    return {
        insert_text: true,
        methods: ['commit', 'paste'],
        max_commit_bytes: MAX_COMMIT_BYTES,
        reasons: Object.values(Reason),
        version,
    };
}
