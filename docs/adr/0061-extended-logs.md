---
status: accepted
---

# Extended logs

## Context

The service's log carried fixed event codes and, at most, an exception's class,
and nothing recorded what a client saw. Refusals left no trace, and a crash on a
phone left none anywhere. A failing Ask this group showed two exception classes
in a week of log, so its causes had to be found by replaying a copy of the data.
The owner and coding agents can already read this service's data, as the Member
Agreement says (ADR 48 Q4). The owner chose logs without restrictions, which the
app can show and send.

## Decision

**What the service logs (Q1).** Each line has its time, level and logger, the
message with its arguments, and any traceback; no list of allowed words decides
what a line may say. There is a line per request, with method, path, status,
seconds, member and client, a line for each refusal a client is shown, each
question's outcome and the step it failed at, and a warning when the event loop
was away for a second or more. Telegram's per-request lines stay in the
session's pacing database, which keeps them for 72 hours.

**Credentials (Q2).** Logs stay free of credentials by construction: nothing
logs a request's headers or body, and a pairing route's path is cut after its
operation, because it names an attempt. Anything shaped like a bearer token or
a provider key is masked, since a library's error message could quote one. A
question's text is not logged either: ADR 59 Q9 stores nothing about it, and a
diagnosis needs its step and error instead.

**What a client keeps (Q3).** The Enloq app keeps its last 500 entries: every
request with its answer, and errors with their stacks. They live with the
offline copy and leave when the phone is forgotten. A browser keeps its log only
in the open tab, as it keeps nothing else durably.

**What a client sends (Q4).** Warnings and errors go to the service's log as
they happen, or once the service answers again, named by member, client, kind
and build; a crash's entry is on disk first, so the next start sends it. **Send
to server** sends the rest. The service bounds a post at 200 entries of 20,000
characters each, so a client in a loop cannot fill its disk; nothing else limits
what a post may say.

**Who reads them (Q5).** **Settings → Logs** shows a client its own log. The
owner also reads the service's last lines there, because they name other
members' chats, so a member does not; the service keeps its last 3,000 in memory.

**A screen that fails (Q6).** A screen that fails while drawing shows its error
in full, with **Try again** and **Back to chats**, instead of closing the app or
leaving an empty page. An error outside a render, in a press or a timer, is
logged and shown in an alert, so a JavaScript error does not close the app.

**Offline copies and builds (Q7).** A server's answer in the offline copy (ADR 38
Q20) belongs to the build that kept it: another build does not read it. A screen
draws its copy before the server answers, so a copy in an older shape crashed the
Reminders tab on every tap, before the fresh answer could replace it. What the
phone itself wrote survives a new build: its drafts, deletion records and log.

**A server's own failures (Q8).** Clients tell them apart. A 500 reads "The server
hit an error.", without calling the client offline. A store's lock outlasting its
10-second wait answers 503, "The server is busy. Try again in a moment.". The
edge's 502 and 504 keep "The server isn't answering." Whatever could be retried
still is: a draft stays on the phone until a save succeeds.

## Consequences

- Logs can hold private content, such as a message in a validation error or a
  chat's ID in a path. They are never committed, and a coding agent reads only
  what a diagnosis needs. Docker rotates the service's log at five files of 50 MB.
- Clients add a request now and then, and the service a line per request.
