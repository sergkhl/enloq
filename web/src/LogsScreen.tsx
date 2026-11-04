import React, { useEffect, useState } from "react";
import { workspaceApi } from "./accounts";
import type { components } from "./api-schema";
import { logEntries, sendLogs, subscribeLogs, type LogEntry } from "./logs";
import { useMe } from "./me";
import { Note, ScreenFrame, SmallButton } from "./screenUi";

type ServerLogs = components["schemas"]["ServerLogs"];

const moment = (time: number) =>
  new Date(time).toISOString().replace("T", " ").slice(0, 19);

// Settings → Logs (ADR 61), as the app's (mobile/app/settings/logs.tsx): this
// browser's log, which Send to server sends in full, and for the owner the
// service's last lines, from the newest down.
export function LogsScreen({ onBack }: { onBack: () => void }) {
  const me = useMe();
  const [mine, setMine] = useState(logEntries);
  const [server, setServer] = useState<string[] | null>(null);
  const [showServer, setShowServer] = useState(false);
  const [note, setNote] = useState("");
  useEffect(() => subscribeLogs(() => setMine(logEntries())), []);

  async function readServer() {
    setNote("");
    try {
      const value = await workspaceApi<ServerLogs>("logs?limit=500");
      setServer([...value.lines].reverse());
    } catch (problem) {
      setNote((problem as Error).message);
    }
  }

  async function send() {
    setNote("");
    setNote(
      (await sendLogs(true))
        ? "Sent to the server's log."
        : "Couldn't send them; they stay in this tab.",
    );
  }

  return (
    <ScreenFrame title="Logs" onBack={onBack}>
      <div className="log-actions">
        {me?.role === "owner" && (
          <>
            <SmallButton
              label="This browser"
              primary={!showServer}
              onClick={() => setShowServer(false)}
            />
            <SmallButton
              label="Server"
              primary={showServer}
              onClick={() => {
                setShowServer(true);
                void readServer();
              }}
            />
          </>
        )}
        <SmallButton label="Send to server" onClick={() => void send()} />
      </div>
      {note && <Note>{note}</Note>}
      {showServer ? (
        server === null ? (
          <p className="log-empty">Reading the server’s log…</p>
        ) : server.length ? (
          server.map((line, index) => (
            <pre key={index} className="log-line">
              {line}
            </pre>
          ))
        ) : (
          <p className="log-empty">Nothing yet.</p>
        )
      ) : mine.length ? (
        mine.map((item, index) => (
          <Entry key={`${item.time}/${index}`} item={item} />
        ))
      ) : (
        <p className="log-empty">Nothing yet.</p>
      )}
    </ScreenFrame>
  );
}

function Entry({ item }: { item: LogEntry }) {
  return (
    <pre className={`log-line log-${item.level}`}>
      {moment(item.time)} {item.source}: {item.message}
      {item.stack ? `\n${item.stack}` : ""}
    </pre>
  );
}
