import React from "react";
import { linkSegments, type TextLink } from "../../shared/links";

// Message text with its links (shared/links.ts), opened in a new tab.
export function LinkedText({
  text,
  links = [],
}: {
  text: string;
  links?: TextLink[];
}) {
  return (
    <>
      {linkSegments(text, links).map((segment, index) =>
        segment.url ? (
          <a
            key={index}
            href={segment.url}
            target="_blank"
            rel="noopener noreferrer"
            title={segment.url}
          >
            {segment.text}
          </a>
        ) : (
          <React.Fragment key={index}>{segment.text}</React.Fragment>
        ),
      )}
    </>
  );
}
