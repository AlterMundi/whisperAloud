// Pure status logic for the WhisperAloud top-bar indicator.
//
// No gi:// or resource:// imports on purpose: this module is unit-tested
// with plain Node (tests/test_shell_extension_js.py) and must stay free of
// anything only GNOME Shell provides.
//
// `status` is the daemon's GetStatus() dict, unpacked to plain JS values
// (see docs/dbus-api.md), later patched in place by the StatusChanged and
// ModelStateChanged signals. Every key is read defensively: older daemons
// lack model_state/hotkey_trigger, and hotkey_bound may not exist yet.

export const IndicatorState = Object.freeze({
    NOT_RUNNING: 'not-running',
    STARTING: 'starting',
    LOADING: 'loading',
    READY: 'ready',
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
    'recording': 'media-record-symbolic',
    'transcribing': 'system-run-symbolic',
    'error': 'dialog-error-symbolic',
    'model-missing': 'dialog-warning-symbolic',
});

function str(value) {
    return typeof value === 'string' ? value : '';
}

/**
 * Overall indicator state.
 *
 * @param {object} args
 * @param {boolean} args.running - the daemon's bus name has an owner
 * @param {boolean} [args.starting] - we asked D-Bus to activate the daemon
 * @param {object|null} args.status - GetStatus() dict, null until it answers
 * @returns {string} one of IndicatorState
 */
export function deriveState({running, starting = false, status}) {
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
        return IndicatorState.READY;
    }
}

/**
 * Global-hotkey summary from GetStatus.
 *
 * bound: true / false / null (unknown). Prefers the explicit `hotkey_bound`
 * key when the daemon provides it; otherwise only backend 'none' counts as
 * "definitely not bound" (a portal session may still be pending).
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
 * Whether the extension should register its own fallback keybinding.
 *
 * Only when the daemon is running and reports that it has no global hotkey
 * of its own: grabbing the same accelerator as the daemon's portal shortcut
 * could fire both and toggle twice.
 */
export function needsFallbackKeybinding({running, status}) {
    return Boolean(running && status) && hotkeyInfo(status).bound === false;
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
        return hotkeyInfo(status).bound === false
            ? 'Ready (no global hotkey)' : 'Ready';
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

/** "Hotkey: …" menu line, or '' when unknown. */
export function hotkeyLabel(status) {
    if (!status)
        return '';
    const {bound, trigger} = hotkeyInfo(status);
    if (trigger)
        return `Hotkey: ${trigger}`;
    if (bound === false)
        return 'Hotkey: not registered';
    if (bound === true)
        return 'Hotkey: registered';
    return '';
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
