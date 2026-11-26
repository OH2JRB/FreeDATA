/**
 * Validate a call sign with SSID
 * @param {string} callsign callsign to check
 * @returns true or false if callsign appears to be valid with an SSID
 */
export function validateCallsignWithSSID(callsign) {
  const patt = new RegExp("^[A-Za-z0-9]{1,7}-[0-9]{1,3}$");
  if (!callsign || !patt.test(callsign)) {
    console.error(
      `Call sign given is not in correct format or missing; callsign passed is: ${callsign}`,
    );
    return false;
  }
  return true;
}

/**
 * Validate a call sign without SSID
 * @param {string} callsign callsign to check
 * @returns true or false if callsign appears to be valid without an SSID
 */
export function validateCallsignWithoutSSID(callsign) {
  const patt = new RegExp("^[A-Za-z0-9]{1,7}$");
  if (!callsign || !patt.test(callsign)) {
    console.error(
      `Call sign given is not in correct format or missing; callsign passed is: ${callsign}`,
    );
    return false;
  }
  return true;
}

/**
 * Set GUI Color Mode.
 * @param {string} colorMOde - The colormode, light, dark, auto.
 */
export function applyColorMode(colorMode) {
  // If set to "auto", detect the OS preference using matchMedia

  console.log(colorMode);
  if (colorMode === "auto") {
    colorMode =
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
  }
  // If it's an empty string, default to "light"
  else if (colorMode === "") {
    colorMode = "light";
  }

  // Apply the theme by setting the attribute on the document element
  document.documentElement.setAttribute("data-bs-theme", colorMode);
}
