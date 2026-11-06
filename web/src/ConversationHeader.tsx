import React from "react";
import { ChevronLeft } from "lucide-react";
import { RobotButton } from "./AiMenu";
import { Avatar } from "./Avatar";
import type { Emotion } from "./Emotion";
import type { ChatAi } from "../../shared/aiMenu";
import "./header.css";

// The app's emotion faces (mobile/app/chat/[account]/[peer].tsx): the emoji
// with an accessible name, without the web's details (ADR 46 Q9 moves them
// to the Correspondent Profile, CU3).
const FACES: Record<string, [string, string]> = {
  neutral: ["😐", "Neutral"],
  positive: ["🙂", "Positive or warm"],
  excited: ["🤩", "Excited"],
  sad: ["😔", "Sad"],
  worried: ["😟", "Worried"],
  frustrated: ["😤", "Frustrated"],
  angry: ["😠", "Angry"],
  confused: ["😕", "Confused"],
  unknown: ["😐", "Mixed or uncertain"],
};

// A conversation's header in the app's design (ADR 44 Q1, MW2): Back, the
// avatar, the name with the emotion right after it (ADR 47) and the robot
// that opens the chat's AI Menu, filled while AI runs there (ADR 62 Q1,
// Q14). The photo and name open the chat's profile, which holds Copy chat
// link since ADR 47 Q8.
export function ConversationHeader({
  account,
  peer,
  title,
  photoId,
  emotion,
  ai,
  onBack,
  backLabel = "Back to conversations",
  onProfile,
  onMenu,
}: {
  account: number;
  peer: string;
  title: string;
  photoId: string | null;
  emotion?: Emotion;
  // The chat's AI Menu state, null until its snapshot is read.
  ai?: ChatAi | null;
  onBack: () => void;
  // Where Back goes: the list, or an Ask this group answer (ADR 59 Q1).
  backLabel?: string;
  // The photo and name open the Correspondent Profile (CU3) or, in a group,
  // the Group Profile (ADR 47 Q7).
  onProfile?: () => void;
  // The AI Menu; Saved Messages has none.
  onMenu?: () => void;
}) {
  const face = emotion?.code ? FACES[emotion.code] : null;
  const emoji = face && (
    <span
      className="header-emotion"
      role="img"
      aria-label={`Emotion: ${face[1]}`}
    >
      {face[0]}
    </span>
  );
  return (
    <header className="conversation-header">
      <button
        type="button"
        className="header-icon"
        aria-label={backLabel}
        onClick={onBack}
      >
        <ChevronLeft size={26} aria-hidden />
      </button>
      {onProfile ? (
        <button
          type="button"
          className="header-person"
          // The button names the emotion, which its emoji only shows.
          aria-label={`${title}${face ? `, ${face[1]}` : ""}, open profile`}
          onClick={onProfile}
        >
          <Avatar
            account={account}
            peer={peer}
            name={title}
            photoId={photoId}
            className="header-avatar"
          />
          <h1 className="header-title">{title}</h1>
          {emoji}
        </button>
      ) : (
        <span className="header-person">
          <Avatar
            account={account}
            peer={peer}
            name={title}
            photoId={photoId}
            className="header-avatar"
          />
          <h1 className="header-title">{title}</h1>
          {emoji}
        </span>
      )}
      {onMenu && <RobotButton ai={ai} onOpen={onMenu} />}
    </header>
  );
}
