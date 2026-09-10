# Third-party notices

Talkback Local bundles no third-party code. At install time it downloads:

| Component | Licence | Source |
|---|---|---|
| Kokoro-82M (model weights) | Apache-2.0 | https://huggingface.co/hexgrad/Kokoro-82M |
| `kokoro` (Python package, macOS default engine) | Apache-2.0 | https://pypi.org/project/kokoro/ |
| `kokoro-onnx` (Python package, `TALKBACK_ENGINE=onnx`) | MIT | https://github.com/thewh1teagle/kokoro-onnx |
| Kokoro onnx model files (`kokoro-v1.0.onnx`, `voices-v1.0.bin`) | Apache-2.0 | https://github.com/thewh1teagle/kokoro-onnx/releases |
| `sounddevice` (Python package) | MIT | https://python-sounddevice.readthedocs.io/ |
| PortAudio (bundled with the `sounddevice` wheel) | MIT-style | http://www.portaudio.com/ |
| `espeak-ng` (macOS, PyTorch engine) | GPL-3.0 | invoked as a separate binary, not linked |

`espeak-ng` is called as an external process, so its copyleft terms do not extend
to this project's MIT-licensed code. On the onnx route `kokoro-onnx` brings its
own espeak through `espeakng-loader`; the same separation applies.

The voice in `engine/icg_voice.pt` is a blend of two Kokoro stock voices and
carries Kokoro's Apache-2.0 terms.
