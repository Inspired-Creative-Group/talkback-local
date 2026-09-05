# The voice

`engine/icg_voice.pt` is the only file here that cannot be regenerated from code.

It is a blend of two Kokoro stock voices — `af_heart` at 65% and `af_sarah` at
35% — chosen by ear on 2026-09-05. Blending produces a voice nobody else running
Kokoro has.

To make a different one:

```python
import torch
from huggingface_hub import hf_hub_download
load = lambda v: torch.load(hf_hub_download("hexgrad/Kokoro-82M", f"voices/{v}.pt"), weights_only=True)
torch.save(load("af_heart") * 0.65 + load("af_sarah") * 0.35, "engine/icg_voice.pt")
```

There are 11 American female voices, 9 male, 4 British of each, plus other
languages. `list_repo_files("hexgrad/Kokoro-82M")` enumerates them.
