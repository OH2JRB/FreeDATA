import DOMPurify from "dompurify";

// Messages and broadcasts come from other stations over the air. DOMPurify's
// default profile keeps <img src="https://...">, <video>, CSS url(...) and
// similar, so a station could make this browser fetch an address of its
// choice when the message is shown: that tells the other side the
// operator's IP address and that the message was read. Shown messages
// therefore load nothing from the network: only images embedded as
// data:image URIs are kept, and links work only when clicked.

// An own instance, so the hook below does not change other DOMPurify users
const purifier = DOMPurify(window);

// Attributes that can make the browser fetch something by themselves
const FETCHING_ATTRIBUTES = [
  "src",
  "srcset",
  "poster",
  "background",
  "href",
  "xlink:href",
  "action",
  "formaction",
  "data",
  "ping",
];

purifier.addHook("afterSanitizeAttributes", (node) => {
  for (const name of FETCHING_ATTRIBUTES) {
    if (!node.hasAttribute || !node.hasAttribute(name)) continue;
    const value = (node.getAttribute(name) || "").trim();
    const keep =
      name === "href"
        ? node.nodeName === "A" // a link, followed only when clicked
        : name === "src" &&
          node.nodeName === "IMG" &&
          /^data:image\//i.test(value);
    if (!keep) node.removeAttribute(name);
  }
  if (node.nodeName === "A" && node.hasAttribute("href")) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

const MESSAGE_CONFIG = {
  // style sheets and inline styles can load url(...); the media elements
  // and forms have their own ways to fetch or send
  FORBID_TAGS: [
    "style",
    "audio",
    "video",
    "source",
    "track",
    "picture",
    "image",
    "form",
    "input",
    "button",
  ],
  FORBID_ATTR: ["style"],
};

/**
 * Sanitize HTML of a message or broadcast for display: no scripts (DOMPurify)
 * and nothing that loads from the network when shown. Links open in a new tab.
 * @param {string} html HTML, as marked produces it from the message text
 * @returns {string} HTML safe to put into the page
 */
export function sanitizeMessageHtml(html) {
  return purifier.sanitize(html, MESSAGE_CONFIG);
}
