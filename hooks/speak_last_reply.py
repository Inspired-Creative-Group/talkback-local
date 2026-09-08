"""Compatibility shim (one release): the code lives in talkback.rewrite."""
import sys

from talkback.rewrite import (  # noqa: F401
    ONES,
    SAYABLE,
    TENS,
    last_assistant_text,
    main,
    n2w,
    rewrite,
    say_inline,
    say_time,
)

if __name__ == "__main__":
    sys.exit(main(sys.argv))
