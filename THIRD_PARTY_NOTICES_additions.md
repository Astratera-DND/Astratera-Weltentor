# Additions for THIRD_PARTY_NOTICES.md

Paste this section into your existing THIRD_PARTY_NOTICES.md (the file was not in the copy I worked on).

## Transcription (downloaded by the user inside the app; not bundled in the exe)

| Component | Used for | License | Source |
|---|---|---|---|
| whisper.cpp (ggml-org) | Speech engine (`whisper-cli`) | MIT | https://github.com/ggml-org/whisper.cpp |
| OpenAI Whisper models, ggml conversions | Speech recognition models | MIT | https://github.com/openai/whisper , https://huggingface.co/ggerganov/whisper.cpp |
| Silero VAD (ggml conversion) | Skips silence | MIT | https://github.com/snakers4/silero-vad , https://huggingface.co/ggml-org/whisper-vad |
| sherpa-onnx (k2-fsa) | Runs the speaker model (bundled in the exe, `--collect-all sherpa_onnx`) | Apache-2.0 | https://github.com/k2-fsa/sherpa-onnx |
| NVIDIA NeMo TitaNet-small (`nemo_en_titanet_small.onnx`) | Voice prints for speaker labels | CC-BY-4.0 | https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models |
| NumPy | Voice print maths | BSD-3-Clause | https://numpy.org |

## D&D term list

`dnd_terms.py` contains names (monsters, spells, items, classes and so on, no descriptions or rules text) taken from the
System Reference Document 5.1 by Wizards of the Coast LLC. It is licensed under the Creative Commons Attribution 4.0
International License: https://creativecommons.org/licenses/by/4.0/legalcode

Astratera Weltentor is an unofficial, fan-made tool. It is not affiliated with or endorsed by Wizards of the Coast.
