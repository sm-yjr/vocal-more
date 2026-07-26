# Lossless recording-history compression

Vocal More keeps recording history useful for playback and retry transcription
without retaining every entry as an uncompressed WAV file.

## Storage policy

- The newest three recordings remain WAV for zero-conversion access.
- Older entries are eligible only after they reach `success` or `failed`.
- Eligible WAV files are converted to FLAC with macOS
  `/usr/bin/afconvert`.
- Compression runs on one owned background worker and never on the dictation
  finish path.
- Settings > History shows current storage use and provides a manual sweep.

The recording index stores `storage_format`, `original_bytes`, and
`stored_bytes`. Existing index entries without these fields are normalized
when loaded; this does not read or rewrite their audio content.

## Verification and commit order

For every candidate, the store:

1. hashes the WAV PCM frames and records channel count, sample width, sample
   rate, and frame count;
2. writes FLAC into a temporary directory inside the recording store;
3. decodes that FLAC back to a temporary WAV;
4. requires an exact match of the PCM SHA-256 and all four audio parameters;
5. skips the candidate if FLAC is not smaller than WAV;
6. atomically moves FLAC into place and atomically writes the updated index;
7. deletes the original WAV only after the index commit succeeds.

A conversion, decode, verification, or index-write failure keeps the original
WAV referenced by the index. A failed cleanup can leave an unreferenced WAV,
but cannot make the recording unreadable.

## Compatibility

`RecordingStore` presents a format-independent interface:

- playback receives base64-encoded WAV bytes;
- retry transcription receives raw 16 kHz mono PCM;
- support bundles include the indexed WAV or FLAC file;
- deletion removes the indexed file;
- the 30-recording retention limit applies to both formats.

The app therefore needs no FLAC logic in the settings frontend, ASR engines,
or meeting workflow.

## Operations and privacy

Automatic migration begins only when an older terminal WAV exists. Empty or
small histories do not start a worker. The worker is joined during app and RPC
shutdown.

Compression is entirely local. It does not send audio, transcripts, file
names, hashes, or storage statistics to a model or telemetry service.

To verify the native codec and all safety paths:

```bash
uv run python -m pytest -q tests/test_recording_store.py
```

The current measured compression, verification time, and foreground scheduling
impact are recorded in
[benchmarks/2026-07-27-recording-compression.md](benchmarks/2026-07-27-recording-compression.md).
