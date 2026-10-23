// Chat Memory's entry bound (ADR 21): 300 UTF-8 bytes, so letters outside
// English count twice. The same in the web and the Enloq app (ADR 46 CU3).
export const MEMORY_BYTES = 300;

export function memoryBytes(text: string) {
  let bytes = 0;
  for (const char of text) {
    const code = char.codePointAt(0)!;
    bytes += code < 0x80 ? 1 : code < 0x800 ? 2 : code < 0x10000 ? 3 : 4;
  }
  return bytes;
}
