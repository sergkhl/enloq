import { Refused, Revoked, Unreachable, request } from "../src/api";

const fetchMock = jest.fn();
global.fetch = fetchMock as unknown as typeof fetch;

function answer(status: number, body: unknown) {
  return Promise.resolve({
    status,
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  });
}

beforeEach(() => fetchMock.mockReset());

test("sends the bearer credential and no Origin or cookies", async () => {
  fetchMock.mockReturnValue(answer(200, { accounts: [] }));
  await request("https://s.example", "accounts", { token: "t0ken" });
  const [url, init] = fetchMock.mock.calls[0];
  expect(url).toBe("https://s.example/api/accounts");
  expect(init.method).toBe("GET");
  expect(init.headers).toEqual({
    Accept: "application/json",
    Authorization: "Bearer t0ken",
  });
  expect(init.credentials).toBe("omit");
});

test("posts JSON without a credential while pairing", async () => {
  fetchMock.mockReturnValue(answer(200, { code: "123456" }));
  await request("https://s.example", "auth/pairings/x/device", {
    body: { name: "Pixel" },
  });
  const [, init] = fetchMock.mock.calls[0];
  expect(init.method).toBe("POST");
  expect(init.body).toBe('{"name":"Pixel"}');
  expect(init.headers["Content-Type"]).toBe("application/json");
  expect(init.headers.Authorization).toBeUndefined();
});

test("separates a lost network, a forgotten device and a refusal", async () => {
  fetchMock.mockReturnValueOnce(Promise.reject(new TypeError("offline")));
  await expect(
    request("https://s.example", "accounts", { token: "t" }),
  ).rejects.toBeInstanceOf(Unreachable);
  fetchMock.mockReturnValueOnce(
    answer(401, { detail: "This device is no longer trusted." }),
  );
  await expect(
    request("https://s.example", "accounts", { token: "t" }),
  ).rejects.toBeInstanceOf(Revoked);
  fetchMock.mockReturnValueOnce(answer(502, null));
  await expect(
    request("https://s.example", "accounts", { token: "t" }),
  ).rejects.toBeInstanceOf(Unreachable);
  fetchMock.mockReturnValueOnce(
    answer(400, { detail: "This pairing was already used." }),
  );
  const refused = request("https://s.example", "auth/pairings/x/device", {
    body: {},
  });
  await expect(refused).rejects.toBeInstanceOf(Refused);
  await expect(refused).rejects.toThrow("already used");
  // Without a credential a 401 is an ordinary refusal, not a revocation.
  fetchMock.mockReturnValueOnce(answer(401, { detail: "Pair first" }));
  await expect(request("https://s.example", "accounts")).rejects.toBeInstanceOf(
    Refused,
  );
});
