import React from "react";
import { recordError } from "./logs";

// A screen that fails while drawing shows its error instead of an empty page
// (ADR 61), as the app's does (mobile/src/ScreenBoundary.tsx).
type State = { error: Error | null; stack: string; attempt: number };

export class ScreenBoundary extends React.Component<
  { children: React.ReactNode },
  State
> {
  state: State = { error: null, stack: "", attempt: 0 };

  static getDerivedStateFromError(thrown: unknown) {
    return {
      error: thrown instanceof Error ? thrown : new Error(String(thrown)),
    };
  }

  componentDidCatch(thrown: unknown, info: React.ErrorInfo) {
    recordError("screen", thrown, info.componentStack ?? undefined);
    this.setState({
      stack: [
        thrown instanceof Error ? thrown.stack : undefined,
        info.componentStack,
      ]
        .filter(Boolean)
        .join("\n"),
    });
  }

  render() {
    const { error, stack, attempt } = this.state;
    if (error)
      return (
        <main className="rewriting-app">
          <section className="problem-screen">
            <h1>This screen hit an error</h1>
            <p role="alert">
              {error.name}: {error.message}
            </p>
            <p>It’s in Settings → Logs, and goes to the server’s log.</p>
            {stack && <pre className="log-line">{stack}</pre>}
            <button
              className="primary"
              onClick={() =>
                this.setState({ error: null, stack: "", attempt: attempt + 1 })
              }
            >
              Try again
            </button>
            <button
              className="secondary"
              onClick={() => window.location.assign("/")}
            >
              Back to chats
            </button>
          </section>
        </main>
      );
    return <React.Fragment key={attempt}>{this.props.children}</React.Fragment>;
  }
}
