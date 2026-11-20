import asyncio
import threading
from freedata_server.api.common import api_abort
from freedata_server.channel_gate import DROPPED, STOPPED
from freedata_server.context import AppContext
import structlog

logger = structlog.get_logger()


async def enqueue_tx_command(ctx: AppContext, cmd_class, params: dict = None) -> bool:
    """
    Enqueue a transmit command using the application context's managers.

    Args:
        ctx: AppContext containing config, state, event managers, etc.
        cmd_class: The command class to instantiate and run.
        params: A dict of parameters for the command (optional).

    Returns:
        bool: False if the command raised, or if its run() returned False
        (the commands that catch their own errors, such as ARQRawCommand and
        Norm, report a failure that way); True otherwise. Most commands
        return None on success.

    Raises:
        HTTPException: 503 when the channel gate dropped the transmission
        (FreeDATA traffic did not end), 409 when Stop ended its wait. Neither
        is a server error: nothing was sent, on purpose.
    """
    params = params or {}
    command = None
    try:
        # Instantiate the command with required components
        command = cmd_class(ctx, params)
        logger.info("Enqueueing transmit command", command=command.get_name())
        # Run in a thread to avoid blocking the event loop
        result = await asyncio.to_thread(command.run)
    except Exception as e:
        logger.error("Command execution failed", command=getattr(cmd_class, "__name__", str(cmd_class)), error=str(e))
        return False
    if result is False:
        reason = getattr(command, "not_sent_reason", None)
        if reason == DROPPED:
            api_abort("Not sent: the channel stayed busy with another FreeDATA station's traffic.", 503)
        if reason == STOPPED:
            api_abort("Not sent: stopped while waiting for a clear channel.", 409)
        logger.warning("Command did not start", command=command.get_name())
        return False
    return True


def _run_in_background(command):
    try:
        command.run()
    except Exception as e:
        logger.error("Command execution failed", command=command.get_name(), error=str(e))


async def start_tx_command(ctx: AppContext, cmd_class, params: dict = None) -> bool:
    """Creates a transmit command and runs it in the background.

    For a command whose outcome reaches the client another way, so the HTTP
    answer need not wait for the channel gate (up to freedata_max_wait plus
    the listening time): SendMessageCommand stores the message as queued when
    it is created, and its status changes go out as message-db events.

    Returns:
        bool: False if the command could not be created (bad parameters, the
        message could not be stored); True once it runs.
    """
    params = params or {}
    try:
        command = await asyncio.to_thread(cmd_class, ctx, params)
    except Exception as e:
        logger.error("Command creation failed", command=getattr(cmd_class, "__name__", str(cmd_class)), error=str(e))
        return False
    logger.info("Starting transmit command in the background", command=command.get_name())
    threading.Thread(target=_run_in_background, args=(command,), name=command.get_name(), daemon=True).start()
    return True
