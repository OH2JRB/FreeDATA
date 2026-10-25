import { defineStore } from "pinia";
import { ref } from "vue";

export const useEncryptionStore = defineStore("encryptionStore", () => {
  // encryption status per callsign, as returned by the server
  const status = ref({});
  // common key status: {enabled, key_loaded, fingerprint}
  const common = ref(null);

  // station shown in the encryption dialog, and why it was opened
  // ("send", "receive", "wrong_key" or "user")
  const dialogCallsign = ref(null);
  const dialogReason = ref(null);
  // incremented to (re)open the dialog
  const dialogRequest = ref(0);

  return {
    status,
    common,
    dialogCallsign,
    dialogReason,
    dialogRequest,
  };
});
