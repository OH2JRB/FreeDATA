import asyncio
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
    """
    params = params or {}
    try:
        # Instantiate the command with required components
        command = cmd_class(ctx, params)
        logger.info("Enqueueing transmit command", command=command.get_name())
        # Run in a thread to avoid blocking the event loop
        result = await asyncio.to_thread(command.run)
        if result is False:
            logger.warning("Command did not start", command=command.get_name())
            return False
        return True
    except Exception as e:
        logger.error("Command execution failed", command=getattr(cmd_class, "__name__", str(cmd_class)), error=str(e))
        return False
