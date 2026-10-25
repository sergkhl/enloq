// A members fixture route (ADR 48), called as the owner would act in People:
// /fixture/requests seeds a Join Request, /fixture/approve and
// /fixture/decline decide the newest one. ROUTE, NAME and ACCOUNT come from
// the flow.
const answer = http.post(`http://127.0.0.1:8776${ROUTE}`, {
  headers: {
    "Content-Type": "application/json",
    Origin: "http://127.0.0.1:8776",
  },
  body: JSON.stringify({
    name: typeof NAME === "undefined" ? "Authored friend" : NAME,
    account_id: typeof ACCOUNT === "undefined" ? 77 : Number(ACCOUNT),
  }),
});
output.status = answer.status;
if (answer.status !== 200) {
  throw new Error(`Fixture setup failed (${answer.status}) for ${ROUTE}`);
}
