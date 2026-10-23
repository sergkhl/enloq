import { fireEvent, render, screen } from "@testing-library/react-native";
import React from "react";
import { mockRouter, mockStore, reset, serve } from "./setup";
import Chats from "../app/chats";
import Settings from "../app/settings";
import { ConnectionProvider } from "../src/connection";

// ADR 45: Settings behind the list header's gear, a menu since ADR 47 Q6. The
// test platform is iOS, which has no phone notifications yet, so the menu
// shows no Notifications row.
const SAVED = JSON.stringify({
  origin: "https://enloq.example.com",
  token: "t0ken",
});

function account(id: number, name: string) {
  return {
    account_id: id,
    name,
    username: "",
    phone: "",
    state: "authorized",
    connected: true,
    status: "",
    ai_enabled: true,
    photo_id: null,
  };
}

let online = true;

beforeEach(() => {
  reset();
  online = true;
  mockStore.set("enloq.credential", SAVED);
  serve((url) => {
    if (!online) return Promise.reject(new TypeError("offline"));
    if (url.endsWith("/api/accounts"))
      return {
        status: 200,
        body: {
          mode: "personal",
          timezone: "UTC",
          budget: [],
          login_available: true,
          accounts: [account(1, "Owner"), account(2, "Second")],
        },
      };
    if (url.endsWith("/preferences/reading-view"))
      return { status: 200, body: { reading_view: "ai" } };
    if (url.includes("/chats?kind=")) return { status: 200, body: [] };
    if (url.endsWith("/api/auth/browsers"))
      return {
        status: 200,
        body: [
          { id: "a".repeat(32), label: "Chromium", current: false },
          { id: "b".repeat(32), label: "This phone", current: true },
        ],
      };
    throw new Error(`unexpected ${url}`);
  });
});

const mount = (element: React.ReactElement) =>
  render(<ConnectionProvider>{element}</ConnectionProvider>);

test("the account sheet's last row opens Settings; the header has New message", async () => {
  // ADR 51 Q19-Q21: the gear left the header for the account sheet, and the
  // pencil stands in its place on Private only.
  await mount(<Chats />);
  expect(await screen.findByLabelText("New message")).toBeTruthy();
  expect(screen.queryByLabelText("Settings")).toBeNull();
  expect(screen.queryByLabelText("Notifications on this phone")).toBeNull();
  await fireEvent.press(screen.getByLabelText("New message"));
  expect(mockRouter.push).toHaveBeenCalledWith({
    pathname: "/new-message",
    params: { account: "1" },
  });
  await fireEvent.press(screen.getByRole("tab", { name: "Groups" }));
  expect(screen.queryByLabelText("New message")).toBeNull();
  await fireEvent.press(
    await screen.findByLabelText("Account: Owner. Switch account"),
  );
  await fireEvent.press(screen.getByRole("button", { name: "Settings" }));
  expect(mockRouter.push).toHaveBeenCalledWith("/settings");
});

test("the account sheet keeps the accounts, Manage accounts and Settings", async () => {
  await mount(<Chats />);
  await fireEvent.press(
    await screen.findByLabelText("Account: Owner. Switch account"),
  );
  expect(screen.getByTestId("account-2")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Manage accounts" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Settings" })).toBeTruthy();
  expect(screen.queryByText("Trusted browsers and devices")).toBeNull();
  expect(screen.queryByText(/^Disconnect from/)).toBeNull();
  expect(screen.queryByText(/^Enloq .* · /)).toBeNull();
});

test("Settings is a menu of Budget, AI and Privacy, with Disconnect and the build", async () => {
  await mount(<Settings />);
  const privacy = await screen.findByRole("button", { name: "Privacy, 2" });
  expect(screen.queryByRole("button", { name: /^Notifications/ })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Disconnect from enloq.example.com" }),
  ).toBeTruthy();
  expect(screen.getByText(/^Enloq .*development build$/)).toBeTruthy();
  await fireEvent.press(privacy);
  expect(mockRouter.push).toHaveBeenCalledWith("/settings/privacy");
  await fireEvent.press(screen.getByRole("button", { name: "Budget" }));
  expect(mockRouter.push).toHaveBeenCalledWith("/settings/budget");
  await fireEvent.press(screen.getByRole("button", { name: "AI" }));
  expect(mockRouter.push).toHaveBeenCalledWith("/settings/ai");
});

test("an unreachable server is named, and Disconnect still offered", async () => {
  online = false;
  await mount(<Settings />);
  expect(
    await screen.findByText("Settings can be changed once the server answers."),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Disconnect from enloq.example.com" }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Privacy" })).toBeTruthy();
});
