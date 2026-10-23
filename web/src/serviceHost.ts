// ADR 40: the Mac instance is served over loopback HTTP and a VPS service over
// HTTPS, so the protocol tells which approval path and storage place to name.
export function onServer() {
  return window.location.protocol === "https:";
}

export function servicePlace() {
  return onServer() ? "the server" : "this Mac";
}
