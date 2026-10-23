# Shared modules

Pure TypeScript modules used by both the web app (`web/`) and the Enloq app
(`mobile/`), imported by relative path (ADR 38 Q32). They touch no DOM, React
Native or storage API, and each moves here when the app first needs it. The two
apps stay separate npm packages because Expo SDK 57 pins different React and
TypeScript versions from the web's.
