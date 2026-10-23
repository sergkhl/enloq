// The latest pairing code the fixture posted to its synthetic Owner
// Account's Saved Messages (ADR 43 IB4), for the journey to type.
const answer = http.get("http://127.0.0.1:8776/fixture/pairing-code");
output.code = json(answer.body).code;
