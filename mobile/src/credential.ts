import * as SecureStore from "expo-secure-store";

// The Trusted Device credential (ADR 38 Q3): kept in the Keychain or
// Keystore with the server it belongs to, never in plain storage.
export type Credential = { origin: string; token: string };

const KEY = "enloq.credential";

export async function loadCredential(): Promise<Credential | null> {
  const saved = await SecureStore.getItemAsync(KEY);
  if (!saved) return null;
  try {
    const value = JSON.parse(saved) as Partial<Credential>;
    if (typeof value.origin === "string" && typeof value.token === "string")
      return { origin: value.origin, token: value.token };
  } catch {
    // An unreadable entry is treated as no credential.
  }
  return null;
}

export function saveCredential(credential: Credential) {
  return SecureStore.setItemAsync(KEY, JSON.stringify(credential), {
    // Readable after the first unlock, never restored onto another phone.
    keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  });
}

export function clearCredential() {
  return SecureStore.deleteItemAsync(KEY);
}
