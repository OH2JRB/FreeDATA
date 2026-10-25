// pinia store setup
import { setActivePinia } from "pinia";
import pinia from "../store/index";
setActivePinia(pinia);

import { useEncryptionStore } from "../store/encryptionStore.js";
const encryption = useEncryptionStore(pinia);

import {
  getStationEncryption,
  setStationEncryptionEnabled,
  setStationEncryptionKey,
  deleteStationEncryptionKey,
  getCommonEncryptionKey,
  setCommonEncryptionKey,
  deleteCommonEncryptionKey,
} from "./api";

export async function loadEncryptionStatus(callsign) {
  if (!callsign) return null;
  const result = await getStationEncryption(callsign);
  if (result) encryption.status[callsign] = result;
  return result;
}

// Opens the encryption dialog for a station.
export function openEncryptionDialog(callsign, reason = "user") {
  encryption.dialogCallsign = callsign;
  encryption.dialogReason = reason;
  encryption.dialogRequest++;
}

// Each function returns {ok, data, error} and updates the stored status.
function storeResult(callsign, result) {
  if (result.ok) encryption.status[callsign] = result.data;
  return result;
}

export async function enableEncryption(callsign, key) {
  // check the key first, so encryption is never on with a rejected key
  const keyResult = await setStationEncryptionKey(callsign, key);
  if (!keyResult.ok) return keyResult;
  const result = storeResult(callsign, await setStationEncryptionEnabled(callsign, true));
  if (result.ok) {
    result.data = {
      ...result.data,
      decrypted: keyResult.data.decrypted,
      requeued: keyResult.data.requeued,
    };
  }
  return result;
}

export async function disableEncryption(callsign) {
  return storeResult(callsign, await setStationEncryptionEnabled(callsign, false));
}

export async function enterEncryptionKey(callsign, key) {
  return storeResult(callsign, await setStationEncryptionKey(callsign, key));
}

export async function forgetEncryptionKey(callsign) {
  return storeResult(callsign, await deleteStationEncryptionKey(callsign));
}

export async function loadCommonKeyStatus() {
  const result = await getCommonEncryptionKey();
  if (result) encryption.common = result;
  return result;
}

// The common key changes the key (and encryption) of every station without
// its own key, so the cached station states are reloaded too.
async function commonKeyChanged(result) {
  if (result.ok) {
    await loadCommonKeyStatus();
    await Promise.all(Object.keys(encryption.status).map(loadEncryptionStatus));
  }
  return result;
}

export async function enterCommonKey(key) {
  return commonKeyChanged(await setCommonEncryptionKey(key));
}

export async function forgetCommonKey() {
  return commonKeyChanged(await deleteCommonEncryptionKey());
}

// After the common key setting was switched on or off.
export async function commonKeySettingChanged() {
  return commonKeyChanged({ ok: true });
}

// Random 256 bit key as 64 hex characters, to share with the other operator.
export function generateEncryptionKey() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
