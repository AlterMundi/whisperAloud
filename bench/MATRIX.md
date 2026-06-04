# STT Benchmark Matrix


> Generated from `bench/results/ledger.jsonl` by `python -m bench.report`. FLEURS is read-speech: absolute WER is optimistic vs real mic dictation, but relative ordering is reliable. GPU numbers are machine-specific (see `hw.id`).


| lang | engine | model | compute | device | hw.id | WER % | RTF | RAM MB | VRAM MB | size MB |
|---|---|---|---|---|---|---|---|---|---|---|
| en | faster-whisper | medium | int8 | cpu | i7-1360P | 5.3 | 0.518 | 2651.6 | — | — |
| en | faster-whisper | medium | int8 | cuda | inference-public | 5.4 | 0.023 | 2170.1 | 1104.0 | — |
| en | faster-whisper | small | int8 | cpu | i7-1360P | 7.2 | 0.201 | 1080.1 | — | — |
| en | faster-whisper | small | int8 | cuda | inference-public | 7.4 | 0.013 | 1345.8 | 560.0 | — |
| en | parakeet | sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 | int8 | cpu | i7-1360P | 10.5 | 0.184 | 1221.1 | — | 671.2 |
| es | parakeet | sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 | int8 | cpu | i7-1360P | 3.2 | 0.151 | 1399.8 | — | 671.2 |
| es | faster-whisper | small | int8 | cuda | inference-public | 4.6 | 0.013 | 1345.5 | 560.0 | — |
| es | faster-whisper | small | int8 | cpu | i7-1360P | 5.1 | 0.161 | 952.1 | — | — |
| es | faster-whisper | medium | int8 | cpu | i7-1360P | 5.3 | 0.488 | 2392.5 | — | — |
| es | faster-whisper | medium | int8 | cuda | inference-public | 5.6 | 0.023 | 2170.0 | 1104.0 | — |
