"""``talkback shush [quiet]``: stop Claude mid-reply — what ``bin/shush`` did,
scoped to Talkback's own player through ``.player.pid`` instead of four
machine-wide ``pkill``s (which also killed any other ffplay on the machine).
The stop flag is raised first: it ends the sentence loop between slices even
if the kill were refused. Owner: player."""

from talkback import paths, procs, state


def shush(home=None, quiet: bool = False) -> bool:
    """Raise the stop flag; terminate the player named by the pid file if it
    is alive; clear the pid file. Prints ``shushed`` / ``nothing was speaking``
    unless ``quiet``. Returns whether a player was killed."""
    state.raise_stop(home)
    pidfile = paths.player_pid(home)
    pid = state.read_pid(pidfile)
    killed = bool(pid and procs.alive(pid) and procs.terminate(pid))
    state.clear_pid(pidfile)
    if not quiet:
        print("shushed" if killed else "nothing was speaking")
    return killed
