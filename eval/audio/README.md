# Benchmark audio

`eval/manifest.yaml` points at locally generated calibration audio under
`eval/generated/`. Generate it with:

```bash
scripts/generate_benchmark_audio.sh
```

The generated files are 16 kHz, mono, 16-bit PCM WAV and are intentionally
gitignored. They exercise the benchmark pipeline and required category
coverage, but they are not a substitute for human-recorded normal voice,
actual whispering, or office noise.

For a claim-grade corpus, copy private or licensed human recordings into a
local directory, create a separate manifest with manually verified truth, and
do not commit those recordings. The report fingerprint binds results to the
exact audio bytes and truth text.
