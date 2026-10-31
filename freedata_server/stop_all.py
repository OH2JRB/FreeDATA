"""The Stop button: stops everything the server sends and empties its queues.

Before, Stop only aborted the ARQ sessions; queued messages and broadcasts went
out again from the scheduler, and the beacon, P2P connections and a ping, CQ or
broadcast on air went on. Queued and transmitting messages and broadcasts are
set to 'aborted' and only go out again when they are retransmitted by hand.
"""

import queue

import structlog

from freedata_server.message_system_db_broadcasts import DatabaseManagerBroadcasts
from freedata_server.message_system_db_messages import DatabaseManagerMessages

log = structlog.get_logger("stop_all")

# One ARQ_STOP attempt instead of ARQSessionISS.RETRIES_STOP: on FSK each one
# keeps the transmitter busy for about 30 s. If it is lost, the IRS gives up on
# its own timeout.
STOP_RETRIES = 1


def clear_queue(q):
    while True:
        try:
            q.get_nowait()
        except queue.Empty:
            return


def stop_all(ctx):
    """Stops all transmissions and empties the transmit queues.

    Returns:
        dict: How many messages and broadcasts were aborted.
    """
    state = ctx.state_manager
    # first, so a transmission waiting to start (SendMessageCommand, a NORM burst
    # loop) does not start after this
    state.stop_count += 1
    state.set("is_beacon_running", False)

    # the rest of what is on air now, whatever sent it
    if ctx.rf_modem:
        ctx.rf_modem.audio_out_queue.queue.clear()

    for session in list(state.p2p_connection_sessions.values()):
        try:
            session.abort_connection()
        except Exception as e:
            log.warning("[STOP] Error aborting P2P connection", session_id=session.session_id, error=e)
        clear_queue(session.p2p_data_tx_queue)
        clear_queue(session.p2p_data_rx_queue)
    clear_queue(ctx.p2p_data_queue)

    state.stop_transmission(stop_retries=STOP_RETRIES)

    messages = DatabaseManagerMessages(ctx).abort_unsent_messages()
    broadcasts = DatabaseManagerBroadcasts(ctx).abort_queued_broadcasts()
    log.warning("[STOP] Stopped all transmissions", messages_aborted=len(messages), broadcasts_aborted=len(broadcasts))
    return {"messages_aborted": len(messages), "broadcasts_aborted": len(broadcasts)}
