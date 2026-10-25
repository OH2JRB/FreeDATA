import asyncio

from fastapi import APIRouter, Depends
from freedata_server.api.common import api_abort
from freedata_server import api_validations as validations
from freedata_server.context import AppContext, get_ctx
from freedata_server.message_p2p import common_key_changed, encryption_log, EXPLORER_SETTINGS
from freedata_server.message_system_db_station import DatabaseManagerStations

router = APIRouter()


@router.get(
    "/",
    summary="Get Modem Configuration",
    tags=["Configuration"],
    responses={
        200: {
            "description": "Current modem configuration settings.",
            "content": {
                "application/json": {
                    "example": {
                        "AUDIO": {
                            "input_device": "2fc0",
                            "output_device": "3655",
                            "rx_audio_level": 0,
                            "tx_audio_level": 2,
                        },
                        "MESSAGES": {"enable_auto_repeat": True},
                        "MODEM": {
                            "enable_morse_identifier": False,
                            "enable_socket_interface": False,
                            "maximum_bandwidth": 2438,
                            "tx_delay": 200,
                        },
                        "NETWORK": {"modemaddress": "", "modemport": 5000},
                        "RADIO": {
                            "control": "rigctld",
                            "data_bits": 8,
                            "model_id": 1001,
                            "ptt_port": "ignore",
                            "ptt_type": "USB",
                            "serial_dcd": "NONE",
                            "serial_dtr": "OFF",
                            "serial_handshake": "ignore",
                            "serial_port": "/dev/cu.Bluetooth-Incoming-Port",
                            "serial_speed": 38400,
                            "stop_bits": 1,
                        },
                        "RIGCTLD": {
                            "arguments": "",
                            "command": "",
                            "ip": "127.0.0.1",
                            "path": "",
                            "port": 4532,
                        },
                        "SOCKET_INTERFACE": {
                            "cmd_port": 0,
                            "data_port": 0,
                            "enable": False,
                            "host": "",
                        },
                        "STATION": {
                            "enable_explorer": True,
                            "enable_stats": True,
                            "mycall": "LA3QMA",
                            "mygrid": "JP20ql",
                            "myssid": 0,
                            "ssid_list": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
                            "respond_to_cq": True,
                            "iaru_region": 1,
                        },
                    }
                }
            },
        },
        404: {
            "description": "The requested resource was not found.",
            "content": {"application/json": {"example": {"error": "Resource not found."}}},
        },
    },
)
async def get_config(ctx: AppContext = Depends(get_ctx)):
    """
    Retrieve the current modem configuration.

    Returns:
        dict: The modem configuration settings.
    """
    cfg = ctx.config_manager.config
    if cfg is None:
        api_abort("Configuration not found", 404)
    return cfg


@router.post(
    "/",
    summary="Update Modem Configuration",
    tags=["Configuration"],
    responses={
        200: {
            "description": "Modem configuration updated successfully.",
            "content": {
                "application/json": {
                    "example": {
                        "AUDIO": {
                            "input_device": "2fc0",
                            "output_device": "3655",
                            "rx_audio_level": 0,
                            "tx_audio_level": 2,
                        },
                        # ...
                    }
                }
            },
        },
        400: {
            "description": "Invalid configuration data.",
            "content": {"application/json": {"example": {"error": "Invalid config"}}},
        },
        500: {
            "description": "Error writing configuration.",
            "content": {"application/json": {"example": {"error": "Error writing config"}}},
        },
        404: {
            "description": "The requested resource was not found.",
            "content": {"application/json": {"example": {"error": "Resource not found."}}},
        },
    },
)
async def post_config(
    new_cfg: dict,
    ctx: AppContext = Depends(get_ctx),
):
    """
    Update the modem configuration with new settings.

    Parameters:
        new_cfg (dict): The new configuration payload.
        ctx (AppContext): Injected application context.

    Returns:
        dict: The updated modem configuration.

    Raises:
        HTTPException via api_abort on validation or write errors.
    """
    # Validate incoming data
    if not validations.validate_remote_config(new_cfg):
        api_abort("Invalid configuration", 400)

    # No Explorer or PSK Reporter reporting while encryption is in use: the
    # common key setting in this config, or encryption on for any station
    if new_cfg.get("MESSAGES", {}).get("enable_common_key") or DatabaseManagerStations(ctx).any_encryption_enabled():
        station = new_cfg.get("STATION", {})
        off = [key for key in EXPLORER_SETTINGS if station.get(key)]
        for key in off:
            station[key] = False
        if off:
            encryption_log.info("[ENCRYPTION] Explorer reporting kept off, encryption is in use", settings=off)
        if ctx.pskreporter.status()["enabled"]:
            ctx.pskreporter.set_enabled(False)
            encryption_log.info("[ENCRYPTION] PSK Reporter switched off, encryption is in use")

    # Read and compare
    old_cfg = ctx.config_manager.read()
    if old_cfg == new_cfg:
        return old_cfg

    # Write new config
    if not ctx.config_manager.write(new_cfg):
        api_abort("Error writing configuration", 500)

    updated = ctx.config_manager.read()

    common_was_on = bool(old_cfg.get("MESSAGES", {}).get("enable_common_key"))
    common_is_on = bool(updated.get("MESSAGES", {}).get("enable_common_key"))
    if common_is_on != common_was_on:
        encryption_log.info("[ENCRYPTION] Common key setting " + ("on" if common_is_on else "off"))
    # common key switched on: decrypt and send what waited for a key
    if common_is_on and not common_was_on:
        decrypted, requeued = await asyncio.to_thread(common_key_changed, ctx)
        if decrypted or requeued:
            encryption_log.info("[ENCRYPTION] Common key used", decrypted=decrypted, requeued=requeued)

    # Trigger modem restart, unless only the GUI section changed
    # (GUI-only keys such as distance_unit are not used by the modem)
    sections = (set(old_cfg) | set(updated)) - {"GUI"}
    if any(old_cfg.get(section) != updated.get(section) for section in sections):
        ctx.modem_service.put("restart")

    # Return updated configuration
    return updated
