"""Listen before talk for transmissions that start something new.

Ping, CQ, beacon, test frame, the start of an ARQ session (raw data or a
message) or P2P connection and the reply to a CQ go through
wait_for_clear_channel before keying up. Replies inside a running session do
not: the other station is waiting for them. An ARQ session's first
ARQ_SESSION_OPEN goes out right after the gate, without the SignalDetector
wait (ARQSession.skip_next_clear_channel_wait); its retries, before the IRS
has answered, go through the gate again (ARQSessionISS.open_retry_clear). The
ARQ session inside a P2P connection uses the CQ reply's holdoff and max wait.

The settings are in the [CHANNEL] section of config.ini (setting(); the
defaults are in DEFAULTS and config.CONFIG.setting_defaults), times in seconds.
enable_listen_before_talk = False turns the gate off: everything is sent at once.

The channel counts as clear when, for listen_time seconds in a row:
  - no FreeDATA (codec2) signal is heard, and none was heard for the last
    freedata_holdoff seconds. The holdoff covers the reply window of a station
    this one cannot hear (a hidden station): the frequency sounds empty while
    the far end of someone else's session answers;
  - the FFT check (audio.calculate_fft) sees no other traffic.

When it is clear, a random delay (up to random_delay s) follows, and the
channel must still be clear after it: two stations that waited for the same
session to end do not key up together.

FreeDATA traffic is never sent over: when it goes on for freedata_max_wait
seconds, the transmission is dropped (returns False). A channel busy only from
other signals (noise, a carrier, another mode) is waited for other_busy_wait
seconds, then the transmission goes out anyway, as the message queue does
(schedule_manager.queue_channel_free): a steady signal must not block sending
for good. Those seconds count from the last FreeDATA signal heard: they used to
count from the start of the wait, so after a long session the channel's fading
busy state was cut short (on the air 2026-11-17, test G2: sent 17 s after
the session ended, "busy from other signals for 30s").
"""

import random
import threading
import time

import structlog

log = structlog.get_logger("channel_gate")

# [CHANNEL] settings in config.ini, used when a setting is missing
DEFAULTS = {
    "enable_listen_before_talk": True,
    "listen_time": 2,  # s the channel must stay clear before keying up
    "freedata_holdoff": 8,  # s after the last FreeDATA signal heard
    "random_delay": 3,  # s, upper bound of the spread after the channel turned clear
    "freedata_max_wait": 60,  # s FreeDATA traffic is waited for, then the transmission is dropped
    "other_busy_wait": 30,  # s a channel busy only from other signals is waited for, then sent anyway
    "beacon_max_wait": 10,  # freedata_max_wait for a beacon (it runs on the scheduler thread)
    "cq_reply_holdoff": 1,  # freedata_holdoff for a CQ reply: the CQ itself was just heard
    "cq_reply_max_wait": 15,  # freedata_max_wait for a CQ reply
    "cq_reply_random_delay": 5,  # random_delay for a CQ reply: several stations may answer
}
POLL = 0.1  # s

CLEAR = "clear"
SEND_ANYWAY = "send_anyway"
DROPPED = "dropped"
STOPPED = "stopped"


def setting(ctx, name):
    """A [CHANNEL] setting from config.ini, or its DEFAULTS value."""
    config = getattr(getattr(ctx, "config_manager", None), "config", None) or {}
    value = config.get("CHANNEL", {}).get(name)
    return DEFAULTS[name] if value is None else value


def _sleep(seconds):
    threading.Event().wait(seconds)


def _now():
    return time.monotonic()


def freedata_heard_recently(state, holdoff, now=None):
    """True while a FreeDATA signal is heard, or was heard in the last holdoff seconds."""
    if state.is_receiving_codec2_signal():
        return True
    last = getattr(state, "last_codec2_traffic", None)
    if last is None:
        return False
    return (_now() if now is None else now) - last < holdoff


def channel_clear_now(state, holdoff):
    """True when neither FreeDATA nor other traffic is on the channel right now."""
    return state.channel_busy_event.is_set() and not freedata_heard_recently(state, holdoff)


def wait_for_clear_channel(
    ctx,
    what,
    max_wait_setting="freedata_max_wait",
    holdoff_setting="freedata_holdoff",
    random_delay_setting="random_delay",
    stop_count=None,
):
    """Waits until `what` may key up; see the module docstring.

    Args:
        ctx: AppContext (state_manager, config_manager, TESTMODE).
        what (str): what is about to be sent, for the log.
        max_wait_setting (str): [CHANNEL] setting: s to wait while FreeDATA
            traffic goes on before dropping the transmission.
        holdoff_setting (str): [CHANNEL] setting: s after the last FreeDATA
            signal heard that the channel still counts as busy.
        random_delay_setting (str): [CHANNEL] setting: upper bound of the
            random delay.
        stop_count (int): state_manager.stop_count when the caller started;
            a Stop (stop_all) meanwhile ends the wait. Defaults to the current one.

    Returns:
        bool: True to send now; False when the transmission must not go out
        (FreeDATA traffic did not end, or Stop was pressed).
    """
    if getattr(ctx, "TESTMODE", False) or not setting(ctx, "enable_listen_before_talk"):
        return True
    result = _wait(
        ctx.state_manager,
        what,
        freedata_max_wait=setting(ctx, max_wait_setting),
        holdoff=setting(ctx, holdoff_setting),
        random_delay_max=setting(ctx, random_delay_setting),
        listen_time=setting(ctx, "listen_time"),
        other_busy_wait=setting(ctx, "other_busy_wait"),
        stop_count=stop_count,
    )
    return result in (CLEAR, SEND_ANYWAY)


def _wait(state, what, freedata_max_wait, holdoff, random_delay_max, listen_time, other_busy_wait, stop_count):
    if stop_count is None:
        stop_count = state.stop_count
    start = _now()
    clear_since = None
    # since when the channel has been busy from other signals only: FreeDATA
    # traffic resets it, short clear spells (a flickering signal) do not
    other_since = None
    logged = False

    while True:
        if state.stop_count != stop_count:
            log.info(f"[GATE] {what}: stopped while waiting for a clear channel")
            return STOPPED
        now = _now()
        freedata = freedata_heard_recently(state, holdoff, now)
        busy = freedata or not state.channel_busy_event.is_set()

        if not busy:
            if clear_since is None:
                clear_since = now
            if now - clear_since >= listen_time:
                delay = random.uniform(0, random_delay_max)
                if not _sleep_unless_stopped(state, stop_count, delay):
                    log.info(f"[GATE] {what}: stopped while waiting for a clear channel")
                    return STOPPED
                if channel_clear_now(state, holdoff):
                    if logged:
                        log.info(f"[GATE] {what}: channel clear after {_now() - start:.0f}s")
                    return CLEAR
                # someone else keyed up during the random delay: listen again
                clear_since = None
                continue
        else:
            clear_since = None
            if not logged:
                source = "another FreeDATA station" if freedata else "other signals"
                log.info(f"[GATE] {what}: channel busy ({source}), waiting")
                logged = True
            if freedata and now - start >= freedata_max_wait:
                log.warning(
                    f"[GATE] {what}: FreeDATA traffic for {now - start:.0f}s, not sent to avoid transmitting over it"
                )
                return DROPPED
            if freedata:
                other_since = None
            elif other_since is None:
                other_since = now
            elif now - other_since >= other_busy_wait:
                log.info(
                    f"[GATE] {what}: channel busy from other signals for {now - other_since:.0f}s"
                    f" (after {now - start:.0f}s in all), sending anyway"
                )
                return SEND_ANYWAY

        _sleep(POLL)


def _sleep_unless_stopped(state, stop_count, seconds):
    """Sleeps for seconds in POLL steps; False if Stop was pressed meanwhile."""
    end = _now() + seconds
    while _now() < end:
        if state.stop_count != stop_count:
            return False
        _sleep(min(POLL, end - _now()))
    return state.stop_count == stop_count
