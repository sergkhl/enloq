// What a client says for a server's own failure, shared by the web and the
// Enloq app. The service sends SERVER_BUSY itself (backend/src/enloq/api/server.py).
export const SERVER_ERROR = "The server hit an error.";
export const SERVER_BUSY = "The server is busy. Try again in a moment.";
