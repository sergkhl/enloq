const accountKey = "rewriting-account";

function read(storage: () => Storage) {
  try {
    const identity = Number(storage().getItem(accountKey));
    return Number.isSafeInteger(identity) && identity > 0 ? identity : 0;
  } catch {
    return 0;
  }
}

// Reloading an existing tab preserves its independent selection. A fresh tab
// starts from the browser's last successfully opened account, then the server
// validates that it is still authorized.
export function startupAccount() {
  return read(() => sessionStorage) || read(() => localStorage);
}

export function rememberAccount(identity: number) {
  for (const storage of [() => sessionStorage, () => localStorage]) {
    try {
      storage().setItem(accountKey, String(identity));
    } catch {
      // Browser storage may be unavailable; ordinary account access still works.
    }
  }
}
