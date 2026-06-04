# STT Benchmark Matrix


> Generated from `bench/results/ledger.jsonl` by `python -m bench.report`. FLEURS is read-speech: absolute WER is optimistic vs real mic dictation, but relative ordering is reliable. GPU numbers are machine-specific (see `hw.id`).


| lang | engine | model | compute | device | hw.id | WER % | RTF | RAM MB | VRAM MB | size MB |
|---|---|---|---|---|---|---|---|---|---|---|
| en | faster-whisper | medium | int8 | cpu | i7-1360P | 5.3 | 0.505 | 2223.9 | — | — |
| en | faster-whisper | small | int8 | cpu | i7-1360P | 7.2 | 0.197 | 899.8 | — | — |
| en | parakeet | sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 | int8 | cpu | i7-1360P | 10.5 | 0.163 | 1223.0 | — | 671.2 |
| es | parakeet | sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8 | int8 | cpu | i7-1360P | 3.2 | 0.171 | 1415.8 | — | 671.2 |
| es | faster-whisper | small | int8 | cpu | i7-1360P | 5.1 | 0.169 | 943.1 | — | — |
| es | faster-whisper | medium | int8 | cpu | i7-1360P | 5.3 | 0.454 | 2214.6 | — | — |
