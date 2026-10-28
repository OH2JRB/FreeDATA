import asyncio

from fastapi import APIRouter, Depends
from freedata_server.api.common import api_response, api_abort
from freedata_server.api.command_helpers import enqueue_tx_command
from freedata_server import command_transmit_sine
from freedata_server.context import AppContext, get_ctx
from freedata_server.rigctld import RadioClockError

router = APIRouter()


@router.get(
    "/",
    summary="Get Radio Parameters",
    tags=["Radio"],
    responses={
        200: {
            "description": "Current radio parameters.",
            "content": {
                "application/json": {
                    "example": {
                        "radio_frequency": "14093000",
                        "radio_mode": "PKTUSB",
                        "radio_rf_level": 100,
                        "radio_status": True,
                        "radio_swr": 0,
                        "radio_tuner": False,
                        "s_meter_strength": "20",
                    }
                }
            },
        },
        404: {
            "description": "The requested resource was not found.",
            "content": {"application/json": {"example": {"detail": {"error": "Resource not found."}}}},
        },
    },
)
async def get_radio(ctx: AppContext = Depends(get_ctx)):
    """
    Retrieve current radio parameters.

    Returns:
        dict: The current radio parameters.
    """
    params = ctx.state_manager.get_radio_status()
    if params is None:
        api_abort("Radio parameters not found", 404)
    return api_response(params)


@router.post(
    "/",
    summary="Set Radio Parameters",
    tags=["Radio"],
    responses={
        200: {
            "description": "Radio parameters updated successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "radio_frequency": "14093000",
                        "radio_mode": "PKTUSB",
                        "radio_rf_level": 100,
                        "radio_status": True,
                        "radio_swr": 0,
                        "radio_tuner": True,
                        "s_meter_strength": "20",
                    }
                }
            },
        },
        400: {
            "description": "Bad Request: The request was malformed or missing required parameters.",
            "content": {"application/json": {"example": {"detail": {"error": "Invalid parameters."}}}},
        },
        404: {
            "description": "The requested resource was not found.",
            "content": {"application/json": {"example": {"detail": {"error": "Resource not found."}}}},
        },
    },
)
async def post_radio(
    new_params: dict,
    ctx: AppContext = Depends(get_ctx),
):
    """
    Set radio parameters: frequency, mode, RF level, tuner state.

    Parameters:
        new_params (dict): The radio parameters to set.
        ctx (AppContext): Injected application context.

    Returns:
        dict: The applied radio parameters.
    """
    radio_manager = ctx.radio_manager
    if radio_manager is None:
        api_abort("Radio manager not running", 503)
    if "radio_frequency" in new_params:
        radio_manager.set_frequency(new_params["radio_frequency"])
    if "radio_mode" in new_params:
        radio_manager.set_mode(new_params["radio_mode"])
    if "radio_rf_level" in new_params:
        radio_manager.set_rf_level(int(new_params["radio_rf_level"]))
    if "radio_tuner" in new_params:
        radio_manager.set_tuner(new_params["radio_tuner"])
    return api_response(new_params)


@router.post(
    "/tune",
    summary="Enable/Disable Radio Tuning",
    tags=["Radio"],
    responses={
        200: {
            "description": "Radio tuning status updated successfully.",
            "content": {"application/json": {"example": {"enable_tuning": True}}},
        },
        400: {
            "description": "Bad Request: The request was malformed or missing required parameters.",
            "content": {"application/json": {"example": {"detail": {"error": "Invalid parameters."}}}},
        },
        404: {
            "description": "The requested resource was not found.",
            "content": {"application/json": {"example": {"detail": {"error": "Resource not found."}}}},
        },
        503: {
            "description": "Modem not running (when enabling tuning).",
            "content": {"application/json": {"example": {"detail": {"error": "Modem not running"}}}},
        },
    },
)
async def post_radio_tune(
    params: dict,
    ctx: AppContext = Depends(get_ctx),
):
    """
    Enable or disable radio tuning tone.

    Parameters:
        params (dict): {'enable_tuning': bool}
        ctx (AppContext): Injected application context.

    Raises:
        HTTPException via api_abort if modem not running or busy.
    """
    enable = params.get("enable_tuning")
    if enable:
        if not ctx.state_manager.is_modem_running:
            api_abort("Modem not running", 503)
        if not await enqueue_tx_command(ctx, command_transmit_sine.TransmitSine):
            api_abort("Failed to start tuning tone.", 500)
    else:
        ctx.rf_modem.stop_sine()
    return api_response(params)


# status code per RadioClockError.reason
CLOCK_ERROR_STATUS = {"not_connected": 409, "unsupported_backend": 409, "not_supported": 422}


@router.post(
    "/clock",
    summary="Set the Radio Clock to the Server's Local Time",
    tags=["Radio"],
    responses={
        200: {
            "description": "Clock set; the time sent and what the radio reports back.",
            "content": {
                "application/json": {
                    "example": {"set_to": "2026-10-28T14:05:30+0200", "radio_clock": "2026-10-28T14:05:30.000+02:00"}
                }
            },
        },
        409: {
            "description": "The radio is not connected, or not controlled through rigctld.",
            "content": {"application/json": {"example": {"detail": {"error": "Radio not connected"}}}},
        },
        422: {
            "description": "The radio's Hamlib backend cannot set its clock.",
            "content": {
                "application/json": {"example": {"detail": {"error": "The radio does not support setting its clock"}}}
            },
        },
        500: {
            "description": "rigctld reported an error.",
            "content": {
                "application/json": {
                    "example": {"detail": {"error": "rigctld did not set the clock (reply: 'RPRT -1')"}}
                }
            },
        },
        503: {
            "description": "Radio manager not running.",
            "content": {"application/json": {"example": {"detail": {"error": "Radio manager not running"}}}},
        },
    },
)
async def post_radio_clock(ctx: AppContext = Depends(get_ctx)):
    """
    Set the radio's date and clock to the server's local time and UTC offset,
    with Hamlib's set_clock through rigctld. Runs only when a user asks for
    it; nothing sets the clock on its own.
    """
    radio_manager = ctx.radio_manager
    if not radio_manager:
        api_abort("Radio manager not running", 503)
    try:
        # rigctld blocks on its socket: off the event loop
        result = await asyncio.to_thread(radio_manager.set_clock)
    except RadioClockError as err:
        api_abort(str(err), CLOCK_ERROR_STATUS.get(err.reason, 500))
    return api_response(result)
