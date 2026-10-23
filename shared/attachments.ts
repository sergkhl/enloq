import { linkSegments } from "./links";

// Cards for a poll, a location, a contact and a link preview (ADR 46 CU2),
// the same in the web and the Enloq app. The service keeps no phone numbers,
// coordinates or link descriptions (ADR 16), so a contact shows its name, a
// location opens the maps app's search for its venue, and a preview opens
// the message's own link.

type Source = {
  media?: string | null;
  media_info?: {
    title?: string | null;
    site?: string | null;
    question?: string | null;
    options?: string[] | null;
    live?: boolean | null;
  } | null;
  text?: string;
  links?: { offset: number; length: number; url?: string | null }[] | null;
};

export type AttachmentCard =
  | { kind: "poll"; title: string; options: string[] }
  | { kind: "location"; title: string; place: string | null }
  | { kind: "contact"; title: string }
  | { kind: "link"; title: string; site: string | null; url: string | null };

// The message's first link, from Telegram's entities or the text itself.
function firstLink(message: Source) {
  const segments = linkSegments(
    message.text ?? "",
    (message.links ?? []) as Parameters<typeof linkSegments>[1],
  );
  return segments.find((segment) => segment.url)?.url ?? null;
}

export function attachmentCard(message: Source): AttachmentCard | null {
  const info = message.media_info ?? {};
  switch (message.media) {
    case "poll":
      return {
        kind: "poll",
        title: info.question || "Poll",
        options: info.options ?? [],
      };
    case "location":
      return {
        kind: "location",
        title: info.title || (info.live ? "Live location" : "Location"),
        place: info.title || null,
      };
    case "contact":
      return { kind: "contact", title: info.title || "Contact" };
    case "link": {
      const url = firstLink(message);
      return {
        kind: "link",
        title: info.title || info.site || url || "Link",
        site: info.site || null,
        url,
      };
    }
    default:
      return null;
  }
}
