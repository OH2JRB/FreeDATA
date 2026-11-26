import { getFreedataAttachmentBySha512 } from "./api";

// The message list is loaded without the attachments' data
// (GET /freedata/messages?attachment_data=false), because it is reloaded on
// every message-db event. An attachment shown in a chat (image preview) is
// fetched once by its SHA-512 hash and kept here for the session; the data of
// an attachment never changes for its hash.
const cache = new Map(); // hash -> Promise of the base64 data, or null

/**
 * The base64 data of an attachment, fetched at most once per session.
 * @param {string} hash the attachment's hash_sha512
 * @returns {Promise<string|null>} the data, or null if it could not be loaded
 */
export function attachmentData(hash) {
  if (!hash) return Promise.resolve(null);
  if (!cache.has(hash)) {
    const request = getFreedataAttachmentBySha512(hash).then(
      (res) => res?.data ?? null,
      () => null,
    );
    cache.set(hash, request);
    // a failed request is tried again the next time
    request.then((data) => {
      if (data === null) cache.delete(hash);
    });
  }
  return cache.get(hash);
}
