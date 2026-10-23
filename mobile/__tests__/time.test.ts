import { messageTime, previewTime } from "../../shared/messageTime";

// 2023-11-14 22:13:20 UTC.
const STAMP = 1_700_000_000;

test("times follow the device's hour cycle when the app knows it", () => {
  expect(messageTime(STAMP, "UTC", "h23")).toBe("22:13");
  expect(messageTime(STAMP, "UTC", "h12")).toMatch(/10:13/);
  jest.useFakeTimers({ now: STAMP * 1000 });
  try {
    expect(previewTime(STAMP, "UTC", "h23")).toBe("22:13");
  } finally {
    jest.useRealTimers();
  }
});
