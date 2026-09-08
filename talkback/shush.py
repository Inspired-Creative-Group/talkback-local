"""``talkback shush [quiet]``: what ``bin/shush`` did, scoped to Talkback's own
player through ``.player.pid`` instead of a machine-wide pkill. Owner: player."""


def shush(home=None, quiet: bool = False) -> bool:
    """raise_stop; kill the player named by the pid file if alive; clear the
    pid file; print ``shushed`` / ``nothing was speaking`` unless quiet.
    Returns whether a player was killed."""
    raise NotImplementedError("talkback.shush.shush — player implementer")
