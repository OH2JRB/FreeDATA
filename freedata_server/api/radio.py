import asyncio

import structlog
from fastapi import APIRouter, Depends
from freedata_server.api.common import api_response, api_abort
from freedata_server.api.command_helpers import enqueue_tx_command
from freedata_server import command_transmit_sine, frequency_list
from freedata_server.context import AppContext, get_ctx
from freedata_server.rigctld import RadioClockError

router = APIRouter()
log = structlog.get_logger("api (radio)")


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
            log.warning("[RADIO] Radio control failed", action="tune", enable=True, error="Modem not running")
            api_abort("Modem not running", 503)
        if not await enqueue_tx_command(ctx, command_transmit_sine.TransmitSine):
            log.warning("[RADIO] Radio control failed", action="tune", enable=True, error="Failed to start tuning tone")
            api_abort("Failed to start tuning tone.", 500)
    else:
        ctx.rf_modem.stop_sine()
    log.info("[RADIO] Radio control applied", action="tune", enable=bool(enable))
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


FREQUENCY_PRESET_EXAMPLE = {
    "frequency": 14093000,
    "band": "20m",
    "notes": "EU / US",
    "schedule": {"days": ["sat", "sun"], "start": "00:00", "end": "00:00", "from": "", "to": ""},
}

FREQUENCIES_RESPONSE = {
    "description": (
        "The frequency presets. `slots` has all slots in order, null for an empty one; "
        "`frequencies` has the filled ones for the frequency selection, each with its "
        "`slot` index and `schedule_state` (null without a schedule)."
    ),
    "content": {
        "application/json": {
            "example": {
                "slots": [FREQUENCY_PRESET_EXAMPLE, None],
                "frequencies": [
                    {
                        **FREQUENCY_PRESET_EXAMPLE,
                        "slot": 0,
                        "schedule_state": {
                            "state": "upcoming",
                            "start": "2026-11-21T00:00:00+00:00",
                            "end": "2026-11-22T00:00:00+00:00",
                        },
                    }
                ],
                "slot_count": frequency_list.SLOT_COUNT,
            }
        }
    },
}


def iaru_region(ctx):
    region = (ctx.config_manager.config or {}).get("STATION", {}).get("iaru_region", 0)
    return region if region in (1, 2, 3) else 0


def frequencies_response(slots):
    return api_response({
        "slots": slots,
        "frequencies": frequency_list.picker_entries(slots),
        "slot_count": frequency_list.SLOT_COUNT,
    })


@router.get(
    "/frequencies",
    summary="Get Frequency Presets",
    tags=["Radio"],
    responses={200: FREQUENCIES_RESPONSE},
)
async def get_radio_frequencies(ctx: AppContext = Depends(get_ctx)):
    """
    Return the frequency presets from the config file (FREQUENCIES.presets).
    """
    return frequencies_response(frequency_list.config_slots(ctx.config_manager.config))


@router.post(
    "/frequencies",
    summary="Save Frequency Presets",
    tags=["Radio"],
    responses={
        200: FREQUENCIES_RESPONSE,
        400: {
            "description": "The presets are not valid.",
            "content": {
                "application/json": {
                    "example": {"detail": {"error": "slot 2: frequency must be a positive integer in Hz"}}
                }
            },
        },
        500: {
            "description": "The config file could not be written.",
            "content": {"application/json": {"example": {"detail": {"error": "the config file could not be written"}}}},
        },
    },
)
async def post_radio_frequencies(body: dict, ctx: AppContext = Depends(get_ctx)):
    """
    Save the frequency presets to the config file. The body is
    `{"slots": [...]}`: at most `slot_count` slots, each a preset or null for
    an empty slot; a shorter list is filled up with empty slots. An empty
    band label is filled in from the IARU band plan.
    """
    try:
        slots = frequency_list.save_slots(ctx.config_manager, body.get("slots"), iaru_region(ctx))
    except ValueError as err:
        api_abort(str(err), 400)
    except OSError as err:
        api_abort(str(err), 500)
    return frequencies_response(slots)


@router.post(
    "/frequencies/reset",
    summary="Reset Frequency Presets",
    tags=["Radio"],
    responses={200: FREQUENCIES_RESPONSE},
)
async def reset_radio_frequencies(ctx: AppContext = Depends(get_ctx)):
    """
    Replace the frequency presets with the default list shipped with the server.
    """
    try:
        slots = frequency_list.save_slots(ctx.config_manager, frequency_list.default_slots(), reason="reset")
    except OSError as err:
        api_abort(str(err), 500)
    return frequencies_response(slots)
