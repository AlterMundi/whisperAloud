#!/usr/bin/env python3
"""
Demo of the persistence layer (history) functionality.
"""

import sys
from pathlib import Path

# Add src to path if running from repo root
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.persistence import HistoryManager
from whisper_aloud.transcriber import TranscriptionResult

def main():
    print("📚 WhisperAloud History Demo")
    print("============================")

    # Load configuration
    try:
        config = WhisperAloudConfig.load()
    except Exception as e:
        print(f"Error loading config: {e}")
        return

    # Use a temporary database for demo
    db_path = Path("/tmp/whisper_aloud_demo_history.db")
    if db_path.exists():
        db_path.unlink()
        
    # Override persistence config for demo
    config.persistence.db_path = db_path
    config.persistence.save_audio = False  # Disable audio saving for this demo
    
    print(f"Using temporary database: {db_path}")
    
    # Initialize manager
    manager = HistoryManager(config.persistence)
    
    # 1. Add some sample transcriptions
    print("\n1. Adding sample transcriptions...")
    
    samples = [
        ("Hello world, this is a test transcription.", "en", 0.95),
        ("Esta es una prueba en español.", "es", 0.92),
        ("Python is a great programming language.", "en", 0.98),
        ("Whisper models are very powerful.", "en", 0.85),
        ("Me gusta programar en Linux.", "es", 0.90),
    ]
    
    for text, lang, conf in samples:
        result = TranscriptionResult(
            text=text,
            language=lang,
            confidence=conf,
            duration=2.5,
            processing_time=0.5,
            segments=[]
        )
        
        entry_id = manager.add_transcription(result)
        print(f"   Added entry {entry_id}: {text[:30]}...")
        
    # 2. Search
    print("\n2. Searching for 'program'...")
    results = manager.search("program")
    for entry in results:
        print(f"   Found: [{entry.language}] {entry.text}")
        
    # 3. Favorites
    print("\n3. Managing favorites...")
    recent = manager.get_recent(limit=1)
    if recent:
        entry_id = recent[0].id
        print(f"   Marking entry {entry_id} as favorite")
        manager.toggle_favorite(entry_id)
        
        favs = manager.get_favorites()
        print(f"   Favorites count: {len(favs)}")
        
    # 4. Export
    print("\n4. Exporting data...")
    export_path = Path("/tmp/whisper_history_export.json")
    manager.export_json(manager.get_recent(100), export_path)
    print(f"   Exported to {export_path}")
    
    # 5. Stats
    print("\n5. Database Stats:")
    stats = manager.get_stats()
    for k, v in stats.items():
        print(f"   {k}: {v}")
        
    # Cleanup
    if db_path.exists():
        db_path.unlink()
        print(f"\nCleaned up temporary database.")
        
    print("\nDemo complete! ✨")

if __name__ == "__main__":
    main()