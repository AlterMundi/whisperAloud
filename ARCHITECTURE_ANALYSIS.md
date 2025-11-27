# ANÁLISIS DE ARQUITECTURA - WhisperAloud
## Diagnóstico de Integración y Plan de Unificación

---

## 1. ESTADO ACTUAL: 3 CAPAS DESCONECTADAS

### Ejecutables disponibles (pyproject.toml)

```toml
[project.scripts]
whisper-aloud = "whisper_aloud.__main__:main"
whisper-aloud-transcribe = "whisper_aloud.__main__:main"  # MISMO que whisper-aloud
whisper-aloud-gui = "whisper_aloud.ui:main"
```

**Nota**: `whisper-aloud` y `whisper-aloud-transcribe` son **aliases** del mismo código.

```
┌─────────────────────────────────────────────────────────────────────┐
│                    EJECUTABLES INDEPENDIENTES                        │
└─────────────────────────────────────────────────────────────────────┘

╔═══════════════════╗     ╔═══════════════════╗     ╔═══════════════════╗
║  whisper-aloud    ║     ║ whisper-aloud-gui ║     ║ GNOME Extension   ║
║  whisper-aloud-   ║     ║                   ║     ║  (JavaScript)     ║
║    transcribe     ║     ║                   ║     ║                   ║
║  (mismo código)   ║     ║                   ║     ║                   ║
║  CLI + Daemon     ║     ║                   ║     ║                   ║
╚═══════════════════╝     ╚═══════════════════╝     ╚═══════════════════╝
        │                          │                          │
        │                          │                          │
        ▼                          ▼                          ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│ Daemon Mode   │         │ Standalone    │         │ D-Bus Client  │
│               │         │ GTK4 App      │         │               │
│ • D-Bus       │         │               │         │ • Panel       │
│ • Servicio    │         │ • Recording   │         │ • Shortcuts   │
│   background  │         │ • History UI  │         │ • Menu        │
│ • CLI client  │         │ • Settings    │         │               │
└───────────────┘         └───────────────┘         └───────────────┘
        │                          │                          │
        │                          │                          │
        ▼                          ▼                          ▼
┌───────────────┐         ┌───────────────┐         ┌───────────────┐
│ ❌ NO HISTORY │         │ ✅ SQLite DB  │         │ ❌ Sin datos  │
│ ❌ Solo notify│         │ ✅ Audio FLAC │         │ ❌ Solo señal │
│ ✅ D-Bus API  │         │ ✅ Full hist. │         │ ✅ D-Bus OK   │
└───────────────┘         └───────────────┘         └───────────────┘
```

---

## 2. DESCONEXIONES CRÍTICAS

### 🔴 Problema #1: Daemon sin persistencia
```
Usuario usa GNOME Extension → Graba → Transcribe
                                          │
                                          ▼
                                   Solo notificación
                                   NO se guarda en BD
                                   NO aparece en historial
```

**Ubicación**: `src/whisper_aloud/service/daemon.py:48-75`
**Impacto**: Transcripciones por daemon se pierden (solo se ven en notificación)

---

### 🔴 Problema #2: GUI aislada del daemon
```
GUI corriendo                    Daemon corriendo
    │                                 │
    ├─ Graba audio                    ├─ Graba audio
    ├─ Transcribe                     ├─ Transcribe
    ├─ Guarda en BD                   ├─ Muestra notificación
    └─ Muestra en panel               └─ ❌ NO guarda
         │                                 │
         ▼                                 ▼
    BD privada GUI              Memoria volátil (se pierde)

    ❌ NO SE COMUNICAN ENTRE SÍ
```

**Ubicación**: `src/whisper_aloud/ui/main_window.py` - sin imports de D-Bus
**Impacto**: Dos "universos" separados - usuario ve cosas diferentes según cómo use la app

---

### 🔴 Problema #3: GNOME Extension flotante
```
GNOME Shell Extension
         │
         ├─ Lee D-Bus del daemon ✅
         ├─ Puede iniciar/parar  ✅
         └─ ✅ INSTALADA (pero daemon sin historial)

gnome-extension/ código integrado pero daemon no persiste
```

**Ubicación**: `gnome-extension/extension.js` + `scripts/install_gnome_integration.sh`
**Impacto**: Extension funciona pero transcripciones no se guardan

---

### 🔴 Problema #4: Configuración sin propagación
```
Usuario cambia settings en GUI
         │
         ▼
~/.config/whisper_aloud/config.json actualizado
         │
         ├─ GUI reload ✅ (reinicia modelo)
         │
         └─ Daemon ❌ NO se entera
                      (sigue usando config vieja)
```

**Ubicación**: `src/whisper_aloud/ui/settings_dialog.py:_on_save()` vs `daemon.py`
**Impacto**: Cambios de configuración no afectan daemon hasta reinicio manual

---

## 3. COMPONENTES COMPARTIDOS (pero no coordinados)

### ✅ Infraestructura común (que SÍ existe)

```
┌──────────────────────────────────────────────────────────────┐
│                    CAPA DE CONFIGURACIÓN                      │
│   ~/.config/whisper_aloud/config.json                        │
│   • Modelo, audio, transcripción, clipboard, persistence     │
└──────────────────────────────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
      Daemon            GUI          Extension
    (lee 1 vez)    (lee + escribe)  (no accede)


┌──────────────────────────────────────────────────────────────┐
│                 CAPA DE ALMACENAMIENTO                        │
│   ~/.local/share/whisper_aloud/                              │
│   ├─ history.db           ← Solo GUI escribe                 │
│   └─ audio/YYYY/MM/*.flac ← Solo GUI escribe                 │
└──────────────────────────────────────────────────────────────┘


┌──────────────────────────────────────────────────────────────┐
│                      CORE ENGINE                              │
│   • Transcriber (faster-whisper)                             │
│   • AudioRecorder (sounddevice)                              │
│   • ClipboardManager (wl-copy/xclip)                         │
│   • Usado por TODOS pero instancias separadas                │
└──────────────────────────────────────────────────────────────┘
```

---

## 4. ARQUITECTURA DESEADA (Integrada)

```
┌─────────────────────────────────────────────────────────────────┐
│                  CAPA DE PRESENTACIÓN (UI)                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐     │
│  │ GNOME        │    │   GTK4 GUI   │    │  CLI Client  │     │
│  │ Extension    │    │              │    │              │     │
│  │              │    │ • Settings   │    │ • Commands   │     │
│  │ • Indicator  │    │ • History UI │    │ • Status     │     │
│  │ • Shortcuts  │    │ • Visualizer │    │              │     │
│  └──────────────┘    └──────────────┘    └──────────────┘     │
│         │                    │                    │             │
└─────────┼────────────────────┼────────────────────┼─────────────┘
          │                    │                    │
          │      ┌─────────────┴────────────┐      │
          │      │                          │      │
          ▼      ▼                          ▼      ▼
┌─────────────────────────────────────────────────────────────────┐
│                      D-BUS SERVICE LAYER                         │
│                  org.fede.whisperAloud                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  WhisperAloudService (daemon)                                   │
│  │                                                               │
│  ├─ StartRecording() → StopRecording()                          │
│  ├─ GetHistory() → GetStatus()                                  │
│  ├─ ConfigChanged signal                                        │
│  └─ TranscriptionCompleted signal                               │
│                                                                  │
│  Signals:                                                        │
│  • StatusChanged(state)                                          │
│  • TranscriptionCompleted(text, entry_id)                       │
│  • HistoryUpdated(entry_id)                                     │
│  • ConfigReloaded()                                             │
└─────────────────────────────────────────────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                      BUSINESS LOGIC LAYER                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ Transcriber │  │AudioRecorder │  │ClipboardMgr  │          │
│  └─────────────┘  └──────────────┘  └──────────────┘          │
│                                                                  │
│  ┌──────────────────────────────────────────────────┐          │
│  │         HistoryManager (SHARED)                  │          │
│  │  • Usado por daemon Y GUI                        │          │
│  │  • Coordinación de sesiones                      │          │
│  │  • Deduplicación por hash                        │          │
│  └──────────────────────────────────────────────────┘          │
└─────────────────────────────────────────────────────────────────┘
          │                    │                    │
          ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                   PERSISTENCE LAYER                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────────────────────┐  ┌──────────────────────┐          │
│  │ TranscriptionDatabase  │  │   AudioArchive       │          │
│  │ (SQLite + FTS5)        │  │   (FLAC files)       │          │
│  └────────────────────────┘  └──────────────────────┘          │
│                                                                  │
│  ~/.local/share/whisper_aloud/                                  │
│  ├─ history.db          ← Compartida por todos                  │
│  └─ audio/YYYY/MM/      ← Compartida por todos                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 5. FLUJOS DE DATOS INTEGRADOS

### Flujo A: Usuario usa GNOME Extension

```
1. Usuario: Super+Shift+A (shortcut global)
          ↓
2. Extension → D-Bus.StartRecording()
          ↓
3. Daemon → AudioRecorder.start()
          ↓
4. Usuario: Super+Shift+A (para parar)
          ↓
5. Daemon → AudioRecorder.stop() → audio[]
          ↓
6. Daemon → Transcriber.transcribe_numpy(audio)
          ↓
7. Daemon → HistoryManager.add_transcription() ✅ NUEVA
          ↓                                              ↓
8. D-Bus signal                                    SQLite INSERT
   TranscriptionCompleted(text, entry_id)              ↓
          ↓                                        Audio FLAC
9. Extension muestra notificación                   guardado
          ↓
10. GUI (si está abierta) → escucha signal → refresh history ✅ NUEVA
```

### Flujo B: Usuario usa GUI

```
1. Usuario: Click "Record" en GUI
          ↓
2. Opción A: GUI modo standalone
   └─ GUI → AudioRecorder.start() [igual que ahora]

   Opción B: GUI modo daemon-client ✅ NUEVA
   └─ GUI → D-Bus.StartRecording()
          ↓
      Daemon procesa (igual que Flujo A)
          ↓
      GUI escucha TranscriptionCompleted signal
          ↓
      GUI actualiza su panel
```

### Flujo C: Usuario cambia configuración

```
1. GUI SettingsDialog → config.json.save()
          ↓
2. ✅ NUEVO: Watcher de archivos o D-Bus signal
          ↓
3. Daemon recibe ConfigChanged
          ↓
4. Daemon → reload_config()
          ↓
5. Daemon → Transcriber.reload_model()
          ↓
6. D-Bus signal → Extension actualiza estado
```

---

## 6. PLAN DE INTEGRACIÓN

### FASE 1: Daemon + Persistencia (CRÍTICO) 🔥
**Objetivo**: Que daemon guarde historial como lo hace GUI

**Archivo**: `src/whisper_aloud/service/daemon.py`

**Cambios necesarios**:
```python
# LÍNEA ~15 - AÑADIR imports:
from whisper_aloud.persistence import HistoryManager
import uuid

# LÍNEA ~48 - En WhisperAloudService.__init__:
class WhisperAloudService(dbus.service.Object):
    def __init__(self, config):
        # ... código existente ...

        # ✅ NUEVO - Añadir después de línea 75
        self.history_manager = HistoryManager(config.persistence)
        self.session_id = str(uuid.uuid4())
        self.logger.info(f"Daemon session ID: {self.session_id}")

# LÍNEA ~150 - En _transcribe_and_emit, después de transcripción:
    def _transcribe_and_emit(self, audio, sample_rate):
        try:
            # ... transcripción existente ...
            result = self.transcriber.transcribe_numpy(audio, sample_rate)

            # ✅ NUEVO - Guardar en BD
            try:
                entry = self.history_manager.add_transcription(
                    result=result,
                    audio=audio if self.config.persistence.save_audio else None,
                    sample_rate=sample_rate,
                    session_id=self.session_id
                )
                self.logger.info(f"Transcription saved to database: ID {entry.id}")

                # ✅ NUEVO - Emitir señal con ID de entrada
                self.TranscriptionCompleted(result.text, entry.id)

            except Exception as e:
                self.logger.error(f"Failed to save history: {e}")
                # Continuar aunque falle guardado
                self.TranscriptionCompleted(result.text, -1)

        except Exception as e:
            # ... error handling existente ...
```

**Señal D-Bus actualizada**:
```python
# Cambiar signature de 's' a 'si' (string + integer)
@dbus.service.signal(dbus_interface='org.fede.whisperAloud', signature='si')
def TranscriptionCompleted(self, text, entry_id):
    """Emitido cuando transcripción completa.

    Args:
        text: Texto transcrito
        entry_id: ID de entrada en BD (-1 si no se guardó)
    """
    pass
```

**Resultado**:
- ✅ Daemon guarda en `~/.local/share/whisper_aloud/history.db`
- ✅ Daemon guarda audio en FLAC si `config.persistence.save_audio = true`
- ✅ Transcripciones accesibles desde GUI
- ✅ Deduplicación automática por hash de audio

**Esfuerzo**: ~50 líneas, 15-20 minutos

---

### FASE 2: Señales de Historial (COORDINACIÓN) 🔗
**Objetivo**: Que GUI se entere cuando daemon añade transcripción

**Archivo 1**: `src/whisper_aloud/service/daemon.py`

```python
# AÑADIR nueva señal D-Bus (después de TranscriptionCompleted)
@dbus.service.signal(dbus_interface='org.fede.whisperAloud', signature='i')
def HistoryUpdated(self, entry_id):
    """Emitido cuando se añade nueva entrada a historial.

    Args:
        entry_id: ID de entrada en BD
    """
    pass

# En _transcribe_and_emit, después de add_transcription:
self.HistoryUpdated(entry.id)
```

**Archivo 2**: `src/whisper_aloud/ui/main_window.py`

```python
# AÑADIR al final de __init__ (opcional, para escuchar daemon):
def _setup_dbus_listener(self):
    """Escucha señales del daemon para sincronización."""
    try:
        from pydbus import SessionBus
        bus = SessionBus()
        daemon = bus.get('org.fede.whisperAloud')

        # Callback para HistoryUpdated
        def on_history_updated(entry_id):
            self.logger.debug(f"Daemon added transcription {entry_id}")
            GLib.idle_add(self.history_panel.refresh_recent)

        daemon.onHistoryUpdated = on_history_updated
        self.logger.info("Listening to daemon signals")

    except Exception as e:
        self.logger.debug(f"No daemon available for sync: {e}")

# Llamar en __init__:
self._setup_dbus_listener()
```

**Resultado**:
- ✅ GUI actualiza historial en tiempo real cuando daemon transcribe
- ✅ Sincronización automática entre componentes
- ✅ Usuario ve consistencia entre extension y GUI

**Esfuerzo**: ~30 líneas, 10 minutos

---

### FASE 3: Configuración Hot-Reload (LIVE UPDATES) ⚡
**Objetivo**: Daemon detecta cambios en config.json sin reinicio

**Opción A - Signal D-Bus** (recomendada):

**Archivo 1**: `src/whisper_aloud/service/daemon.py`

```python
# AÑADIR método D-Bus:
@dbus.service.method(dbus_interface='org.fede.whisperAloud')
def ReloadConfig(self):
    """Recarga configuración desde archivo."""
    try:
        self.logger.info("Reloading configuration...")
        new_config = WhisperAloudConfig.load()

        # Recargar modelo si cambió
        if (new_config.model.name != self.config.model.name or
            new_config.model.device != self.config.model.device):
            self.logger.info("Model config changed, reloading...")
            self.transcriber = Transcriber(new_config)

        # Recargar audio si cambió
        if new_config.audio != self.config.audio:
            self.logger.info("Audio config changed, recreating recorder...")
            self.audio_recorder = AudioRecorder(new_config.audio)

        self.config = new_config
        self.ConfigReloaded()
        self.logger.info("Configuration reloaded successfully")
        return "OK"

    except Exception as e:
        self.logger.error(f"Failed to reload config: {e}")
        return f"ERROR: {e}"

# AÑADIR señal:
@dbus.service.signal(dbus_interface='org.fede.whisperAloud')
def ConfigReloaded(self):
    """Emitido cuando configuración se recarga."""
    pass
```

**Archivo 2**: `src/whisper_aloud/ui/settings_dialog.py`

```python
# MODIFICAR método _on_save (línea ~250):
def _on_save(self, button):
    # ... código existente de guardado ...
    save_config_to_file(self.config)

    # ✅ NUEVO - Notificar a daemon
    try:
        from pydbus import SessionBus
        bus = SessionBus()
        daemon = bus.get('org.fede.whisperAloud')
        result = daemon.ReloadConfig()
        self.logger.info(f"Daemon config reload: {result}")
    except Exception as e:
        self.logger.debug(f"No daemon to notify: {e}")

    self.destroy()
```

**Archivo 3**: `src/whisper_aloud/__main__.py` (añadir comando)

```python
# AÑADIR en handle_client_command (línea ~120):
elif command == "reload":
    result = service.ReloadConfig()
    print(f"Config reload: {result}")
```

**Resultado**:
- ✅ Cambios de settings afectan daemon inmediatamente
- ✅ No requiere reinicio manual
- ✅ Usuario puede cambiar modelo/audio en caliente

**Esfuerzo**: ~60 líneas, 20 minutos

---

### FASE 4: GNOME Extension - Mejoras (UX) 🎨
**Objetivo**: Mostrar contador de transcripciones e integrar con historial

**Archivo**: `gnome-extension/extension.js`

```javascript
// AÑADIR en _init después de conectar signals existentes:

// Escuchar HistoryUpdated
this._historyUpdatedId = this._proxy.connectSignal(
    'HistoryUpdated',
    (proxy, sender, [entryId]) => {
        log(`WhisperAloud: New transcription ${entryId}`);
        this._updateTranscriptionCount();
        this._showQuickNotification(`Transcription #${entryId} saved`);
    }
);

// AÑADIR método para mostrar contador:
_updateTranscriptionCount() {
    // Leer contador desde D-Bus (requiere añadir método GetTranscriptionCount al daemon)
    // O simplemente incrementar contador local
    this._transcriptionCount++;
    if (this._transcriptionCount > 0) {
        this._indicator.text = `${this._transcriptionCount}`;
    }
}

// AÑADIR en disable():
if (this._historyUpdatedId) {
    this._proxy.disconnectSignal(this._historyUpdatedId);
    this._historyUpdatedId = null;
}
```

**Archivo daemon.py** (añadir método para contador):

```python
@dbus.service.method(dbus_interface='org.fede.whisperAloud', signature='', signature_out='i')
def GetTranscriptionCount(self):
    """Devuelve número total de transcripciones en BD."""
    try:
        return self.history_manager.get_total_count()
    except:
        return 0
```

**Resultado**:
- ✅ Extension muestra contador en panel
- ✅ Notificaciones más informativas
- ✅ Mejor feedback visual

**Esfuerzo**: ~40 líneas JS + 10 líneas Python, 15 minutos

---

### FASE 5: GUI Modo Híbrido (OPCIONAL) 🔀
**Objetivo**: GUI puede conectarse a daemon existente O correr standalone

**Archivo**: `src/whisper_aloud/ui/app.py`

```python
class WhisperAloudApp(Gtk.Application):
    def __init__(self, force_standalone=False):
        super().__init__(
            application_id='org.fede.whisperAloud.GUI',
            flags=Gio.ApplicationFlags.FLAGS_NONE
        )
        self.force_standalone = force_standalone
        self.daemon_mode = self._check_daemon() if not force_standalone else False

    def _check_daemon(self):
        """Verifica si daemon está corriendo."""
        try:
            from pydbus import SessionBus
            bus = SessionBus()
            daemon = bus.get('org.fede.whisperAloud')
            status = daemon.GetStatus()
            logger.info(f"Found running daemon: {status}")
            return True
        except Exception as e:
            logger.debug(f"No daemon found: {e}")
            return False

    def do_activate(self):
        if not self.props.active_window:
            if self.daemon_mode:
                # Crear GUI que controla daemon via D-Bus
                from whisper_aloud.ui.daemon_client_window import DaemonClientWindow
                win = DaemonClientWindow(application=self)
            else:
                # GUI standalone (actual)
                from whisper_aloud.ui.main_window import MainWindow
                win = MainWindow(application=self)

            win.present()
```

**Archivo nuevo**: `src/whisper_aloud/ui/daemon_client_window.py`

```python
"""GUI window que actúa como cliente del daemon."""

from gi.repository import Gtk, GLib
from pydbus import SessionBus
import logging

logger = logging.getLogger(__name__)

class DaemonClientWindow(Gtk.ApplicationWindow):
    """Ventana GUI que controla daemon via D-Bus."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.set_title("WhisperAloud (Daemon Mode)")
        self.set_default_size(400, 300)

        # Conectar a daemon
        bus = SessionBus()
        self.daemon = bus.get('org.fede.whisperAloud')

        # UI simple con botón toggle
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.set_margin_start(12)
        box.set_margin_end(12)

        self.status_label = Gtk.Label(label="Status: Unknown")
        box.append(self.status_label)

        self.toggle_button = Gtk.Button(label="Start Recording")
        self.toggle_button.connect("clicked", self._on_toggle_clicked)
        box.append(self.toggle_button)

        self.result_view = Gtk.TextView()
        self.result_view.set_editable(False)
        self.result_view.set_wrap_mode(Gtk.WrapMode.WORD)
        scroll = Gtk.ScrolledWindow()
        scroll.set_child(self.result_view)
        scroll.set_vexpand(True)
        box.append(scroll)

        self.set_child(box)

        # Escuchar señales
        self.daemon.onStatusChanged = lambda s: GLib.idle_add(self._on_status_changed, s)
        self.daemon.onTranscriptionCompleted = lambda t, i: GLib.idle_add(
            self._on_transcription_completed, t, i
        )

        # Update inicial
        self._update_status()

    def _on_toggle_clicked(self, button):
        try:
            self.daemon.ToggleRecording()
        except Exception as e:
            logger.error(f"Toggle failed: {e}")

    def _on_status_changed(self, status):
        self.status_label.set_text(f"Status: {status}")
        if status == "RECORDING":
            self.toggle_button.set_label("Stop Recording")
        else:
            self.toggle_button.set_label("Start Recording")

    def _on_transcription_completed(self, text, entry_id):
        buf = self.result_view.get_buffer()
        buf.insert(buf.get_end_iter(), f"\n[#{entry_id}] {text}\n", -1)

    def _update_status(self):
        try:
            status = self.daemon.GetStatus()
            self._on_status_changed(status)
        except Exception as e:
            self.status_label.set_text(f"Error: {e}")
```

**Resultado**:
- ✅ GUI ligera cuando daemon está corriendo
- ✅ Ahorro de recursos (un solo Transcriber, AudioRecorder)
- ✅ Coherencia total entre extension y GUI

**Esfuerzo**: ~150 líneas, 30-40 minutos

---

## 7. PRIORIZACIÓN DE CAMBIOS

### 🔥 CRÍTICO (Hacer primero)
1. **Daemon + HistoryManager** - FASE 1
   - Sin esto, daemon es "amnésico"
   - ~50 líneas de código
   - Riesgo bajo, alto impacto
   - **Tiempo**: 15-20 minutos

2. **Signal HistoryUpdated** - FASE 2
   - Sincronización básica GUI ↔ Daemon
   - ~30 líneas de código
   - Necesario para coherencia
   - **Tiempo**: 10 minutos

### ⚡ IMPORTANTE (Hacer segundo)
3. **Config Hot-Reload** - FASE 3
   - Mejora UX significativa
   - ~60 líneas
   - Evita confusión de usuario
   - **Tiempo**: 20 minutos

4. **Extension Mejoras** - FASE 4
   - Contador y mejor feedback
   - ~50 líneas JS + Python
   - Aprovecha trabajo de FASE 1-2
   - **Tiempo**: 15 minutos

### 🎯 DESEABLE (Hacer tercero)
5. **GUI Modo Híbrido** - FASE 5
   - Optimización, no necesidad urgente
   - Refactor más grande (~150 líneas)
   - Puede esperar a futuro
   - **Tiempo**: 30-40 minutos

---

## 8. MÉTRICAS DE INTEGRACIÓN

### Antes de integración
```
Componentes aislados:        3
Bases de datos:              1 (solo GUI)
Ejecuciones que persisten:   1/3 (33%)
Sincronización:              0%
Configuración compartida:    Solo lectura
```

### Después de FASE 1-2
```
Componentes integrados:      3/3
Bases de datos:              1 (compartida)
Ejecuciones que persisten:   3/3 (100%)
Sincronización:              100% (GUI ↔ Daemon)
Configuración compartida:    Lectura (mejora en FASE 3)
```

### Después de FASE 3-4
```
Componentes integrados:      3/3
Sincronización:              100%
Configuración compartida:    Lectura + Hot-reload
Extension features:          +Contador +Notifications
```

### Después de FASE 5 (completo)
```
Arquitectura unificada:      ✅
Modo cliente/servidor:       ✅
Optimización de recursos:    ✅
Experiencia coherente:       ✅
```

---

## 9. RESUMEN EJECUTIVO

### Estado Actual

```
3 capas funcionales pero aisladas (2 ejecutables únicos + extension):

├─ whisper-aloud / whisper-aloud-transcribe (MISMO código)
│  ├─ CLI Mode: Transcripción directa de archivos
│  └─ Daemon Mode: Servicio D-Bus background
│     • Graba + transcribe + notifica
│     • ❌ NO guarda historial en BD
│
├─ whisper-aloud-gui (ejecutable separado)
│  • Graba + transcribe + historial + settings
│  • ✅ Guarda en SQLite + FLAC
│  • ❌ NO se comunica con daemon
│
└─ GNOME Extension (JavaScript)
   • ✅ Instalada y funcional
   • ✅ Controla daemon via D-Bus
   • ❌ Daemon no persiste → sin historial
```

### Problema Principal
**Falta de coordinación de estado compartido**
- Daemon no usa `HistoryManager` → transcripciones volátiles
- GUI no escucha daemon → historial desincronizado
- Config no se propaga → inconsistencia de comportamiento

### Solución Mínima Viable (FASE 1)
**Añadir capa de persistencia al daemon**
```python
# 3 líneas clave:
self.history_manager = HistoryManager(config.persistence)
entry = self.history_manager.add_transcription(result, audio, rate, session_id)
self.TranscriptionCompleted(result.text, entry.id)
```

### Impacto Total
```
ANTES:  3 universos paralelos sin cohesión
DESPUÉS: Sistema unificado con historial centralizado

Código a cambiar:  ~250 líneas totales
Tiempo estimado:   90-120 minutos (todas las fases)
Archivos afectados: 5-6 archivos Python + 1 JS
Riesgo:            Bajo (cambios aditivos, no destructivos)
```

---

## 10. SIGUIENTE PASO RECOMENDADO

**Implementar FASE 1 AHORA** (15-20 minutos):

### Checklist
- [ ] Editar `src/whisper_aloud/service/daemon.py`
  - [ ] Añadir imports (HistoryManager, uuid)
  - [ ] Crear instancia en `__init__`
  - [ ] Llamar `add_transcription()` en `_transcribe_and_emit()`
  - [ ] Actualizar signal TranscriptionCompleted
- [ ] Probar con CLI
  ```bash
  # Terminal 1
  whisper-aloud --daemon

  # Terminal 2
  whisper-aloud start
  # ... hablar ...
  whisper-aloud stop
  ```
- [ ] Verificar BD
  ```bash
  sqlite3 ~/.local/share/whisper_aloud/history.db "SELECT COUNT(*) FROM transcriptions;"
  ```
- [ ] Abrir GUI y verificar historial muestra transcripción del daemon

### ¿Proceder con implementación?
Confirma si quieres que implemente FASE 1 ahora mismo.
