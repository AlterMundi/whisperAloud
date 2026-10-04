// Pure status logic for the WhisperAloud top-bar indicator.
//
// No gi:// or resource:// imports on purpose: this module is unit-tested
// with plain Node (tests/test_shell_extension_js.py) and must stay free of
// anything only GNOME Shell provides.
//
// `status` is the daemon's GetStatus() dict, unpacked to plain JS values
// (see docs/dbus-api.md), later patched in place by the StatusChanged and
// ModelStateChanged signals. Every key is read defensively: older daemons
// lack model_state (< 1.6.0) and hotkey_bound/autostart (< 1.7.0).
//
// "All OK" (API 1.7.0): model_state == "ready" and hotkey_bound.

export const IndicatorState = Object.freeze({
    NOT_RUNNING: 'not-running',
    STARTING: 'starting',
    LOADING: 'loading',
    READY: 'ready',
    // Model ready, but no working global hotkey (yet).
    READY_NO_HOTKEY: 'ready-no-hotkey',
    RECORDING: 'recording',
    TRANSCRIBING: 'transcribing',
    ERROR: 'error',
    MODEL_MISSING: 'model-missing',
});

export const ICONS = Object.freeze({
    'not-running': 'microphone-disabled-symbolic',
    'starting': 'content-loading-symbolic',
    'loading': 'content-loading-symbolic',
    'ready': 'audio-input-microphone-symbolic',
    'ready-no-hotkey': 'audio-input-microphone-symbolic',
    'recording': 'media-record-symbolic',
    'transcribing': 'system-run-symbolic',
    'error': 'dialog-error-symbolic',
    'model-missing': 'dialog-warning-symbolic',
});

// Extra style class per state (stylesheet.css), '' for none.
export const STYLE_CLASSES = Object.freeze({
    'recording': 'whisperaloud-recording',
    'ready-no-hotkey': 'whisperaloud-degraded',
});

// hotkey_bound has no change signal (docs/dbus-api.md): while the daemon
// says "not bound yet" (e.g. GNOME's shortcut consent dialog is still open)
// the extension re-reads GetStatus at these delays, then gives up until the
// next ConfigChanged or daemon restart (a return to idle still re-reads
// GetStatus once). Bounded on purpose: this is not a presence poll.
export const HOTKEY_RECHECK_DELAYS_S = Object.freeze([2, 5, 10, 20, 40, 60]);

function str(value) {
    return typeof value === 'string' ? value : '';
}

/**
 * Global-hotkey summary from GetStatus.
 *
 * bound: true / false / null (unknown). Uses the explicit `hotkey_bound`
 * key (1.7.0) when present; otherwise only backend 'none' counts as
 * "definitely not bound" and a bound trigger as "bound".
 */
export function hotkeyInfo(status) {
    if (!status)
        return {bound: null, trigger: '', backend: ''};
    const backend = str(status.hotkey_backend);
    const trigger = str(status.hotkey_trigger);
    let bound = null;
    if (typeof status.hotkey_bound === 'boolean')
        bound = status.hotkey_bound;
    else if (backend === 'none')
        bound = false;
    else if (trigger)
        bound = true;
    return {bound, trigger, backend};
}

/**
 * Overall indicator state.
 *
 * @param {object} args
 * @param {boolean} args.running - the daemon's bus name has an owner
 * @param {boolean} [args.starting] - we asked D-Bus to activate the daemon
 * @param {object|null} args.status - GetStatus() dict, null until it answers
 * @param {boolean} [args.fallbackHotkey] - the extension's own fallback
 *   keybinding is grabbed (so dictation by hotkey works anyway)
 * @returns {string} one of IndicatorState
 */
export function deriveState({running, starting = false, status, fallbackHotkey = false}) {
    if (!running)
        return starting ? IndicatorState.STARTING : IndicatorState.NOT_RUNNING;
    if (!status)
        return IndicatorState.STARTING;

    const state = str(status.state);
    if (state === 'shutdown')
        return IndicatorState.NOT_RUNNING;
    // Recording/transcribing win over model readiness: a batch engine may
    // record while the model is still loading.
    if (state === 'recording')
        return IndicatorState.RECORDING;
    if (state === 'transcribing')
        return IndicatorState.TRANSCRIBING;
    if (state === 'error')
        return IndicatorState.ERROR;

    switch (str(status.model_state)) {
    case 'missing':
        return IndicatorState.MODEL_MISSING;
    case 'error':
        return IndicatorState.ERROR;
    case 'loading':
    case 'downloading':
    case 'unloaded':
        return IndicatorState.LOADING;
    default:
        // 'ready', or a pre-1.6.0 daemon without model_state.
        if (hotkeyInfo(status).bound === false && !fallbackHotkey)
            return IndicatorState.READY_NO_HOTKEY;
        return IndicatorState.READY;
    }
}

/**
 * Whether the extension should grab its own fallback keybinding.
 *
 * Only when the running daemon has no hotkey backend at all. NOT when the
 * portal binding is merely pending or was declined (hotkey_bound false with
 * a backend): the portal may still bind the same accelerator, and both
 * firing would toggle twice.
 */
export function needsFallbackKeybinding({running, status}) {
    return Boolean(running && status) && hotkeyInfo(status).backend === 'none';
}

/** Whether hotkey_bound may still flip to true without any signal. */
export function shouldRecheckHotkey({running, status}) {
    if (!running || !status)
        return false;
    const {bound, backend} = hotkeyInfo(status);
    return bound === false && backend !== 'none' && backend !== '';
}

/** One-line status for the panel menu header. */
export function statusLabel(state, status) {
    switch (state) {
    case IndicatorState.NOT_RUNNING:
        return 'WhisperAloud is not running';
    case IndicatorState.STARTING:
        return 'Starting…';
    case IndicatorState.LOADING:
        return status && status.model_state === 'downloading'
            ? 'Downloading model…' : 'Loading model…';
    case IndicatorState.READY:
        return 'Ready';
    case IndicatorState.READY_NO_HOTKEY:
        return 'Ready, but no global hotkey';
    case IndicatorState.RECORDING:
        return 'Recording…';
    case IndicatorState.TRANSCRIBING:
        return 'Transcribing…';
    case IndicatorState.MODEL_MISSING:
        return 'Model not downloaded';
    case IndicatorState.ERROR: {
        const detail = status ? str(status.model_error) : '';
        return detail ? `Error: ${detail}` : 'Error';
    }
    default:
        return 'WhisperAloud';
    }
}

/** "Model: …" menu line, or '' when unknown. */
export function modelLabel(status) {
    const model = status ? str(status.model) : '';
    return model ? `Model: ${model}` : '';
}

/**
 * "Hotkey: …" menu line, or '' when unknown.
 *
 * @param {object|null} status
 * @param {string} [fallbackAccel] - accelerator of the extension's fallback
 *   keybinding while it is grabbed
 */
export function hotkeyLabel(status, fallbackAccel = '') {
    if (!status)
        return '';
    if (fallbackAccel)
        return `Hotkey: ${fallbackAccel} (via top-bar extension)`;
    const {bound, trigger} = hotkeyInfo(status);
    if (bound === false)
        return 'Hotkey: not registered';
    if (trigger)
        return `Hotkey: ${trigger}`;
    if (bound === true)
        return 'Hotkey: registered';
    return '';
}

/** "Starts at login" menu line (1.7.0 `autostart`), '' when unknown. */
export function autostartLabel(status) {
    if (!status || typeof status.autostart !== 'boolean')
        return '';
    return status.autostart ? 'Starts at login' : 'Does not start at login';
}

/**
 * Apply a ModelStateChanged(state, detail) signal to a status dict in place.
 * Mirrors GetStatus: model_error carries the detail only for missing/error.
 */
export function applyModelState(status, state, detail) {
    if (!status)
        return;
    status.model_state = state;
    status.model_error = (state === 'missing' || state === 'error') ? str(detail) : '';
}
