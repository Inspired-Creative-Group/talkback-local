# Third-party notices

Talkback Local bundles no third-party code. At install time it downloads:

| Component | Licence | Source |
|---|---|---|
| Kokoro-82M (model weights) | Apache-2.0 | https://huggingface.co/hexgrad/Kokoro-82M |
| `kokoro` (Python package) | Apache-2.0 | https://pypi.org/project/kokoro/ |
| `espeak-ng` | GPL-3.0 | invoked as a separate binary, not linked |
| `ffmpeg` / `ffplay` | LGPL-2.1+ | invoked as a separate binary, not linked |

`espeak-ng` and `ffmpeg` are called as external processes, so their copyleft terms
do not extend to this project's MIT-licensed code.

The voice in `engine/icg_voice.pt` is a blend of two Kokoro stock voices and
carries Kokoro's Apache-2.0 terms.
