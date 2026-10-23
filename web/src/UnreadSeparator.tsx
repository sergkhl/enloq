import React from "react";

// The Unread Separator (ADR 28): the app's wording for its local unseen state.
// Presentation only; it is never a scroll anchor and changes no viewing state.
export function UnreadSeparator() {
  return (
    <div
      className="unread-separator"
      role="separator"
      aria-label="Unread messages"
      data-unread-separator=""
    >
      <span aria-hidden="true">Unread messages</span>
    </div>
  );
}
