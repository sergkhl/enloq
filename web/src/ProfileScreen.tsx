import React, { useEffect, useState } from "react";
import { Link2, MessageCircle } from "lucide-react";
import { workspaceApi } from "./accounts";
import type { Chat } from "./conversation-api";
import { openPath } from "../../shared/newMessage";
import type { components } from "./api-schema";
import { Avatar } from "./Avatar";
import { chatApi } from "./conversation-api";
import { ProfileReminders } from "./Reminders";
import { Explanation, Note, ScreenFrame, Section } from "./screenUi";
import { chatLink } from "../../shared/chatRoute";

type Profile = components["schemas"]["CorrespondentProfile"];
type GroupProfile = components["schemas"]["GroupProfile"];
type MemberProfileValue = components["schemas"]["MemberProfile"];

// A chat's profile, opened from its header's photo and name: a person's
// Correspondent Profile (ADR 46 Q11, CU3) or a group's Group Profile (ADR 47
// Q7). Each reads Telegram on opening, keeps nothing and holds the chat's
// Copy chat link, which left the header (ADR 47 Q8).
export function ProfileScreen(props: {
  account: number;
  peer: string;
  title: string;
  photoId: string | null;
  // A group member, opened from their avatar or name (ADR 51 Q14).
  member?: string;
  // Their private chat, opened by Message as New Message does.
  onMessage?: (chat: Chat) => void;
  // A reminder's editor, or a new one's without `schedule` (ADR 58 Q12).
  onReminder?: (schedule?: string) => void;
  onBack: () => void;
}) {
  if (props.member && /^(chat|channel):/.test(props.peer))
    return (
      <MemberProfile
        account={props.account}
        group={props.peer}
        member={props.member}
        title={props.title}
        onMessage={props.onMessage}
        onBack={props.onBack}
      />
    );
  return /^(chat|channel):/.test(props.peer) ? (
    <GroupProfileScreen {...props} />
  ) : (
    <CorrespondentProfile {...props} />
  );
}

// One read on each opening.
function useRead<T>(account: number, peer: string, path: string) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    chatApi<T>(account, peer, path).then(
      (answer) => live && setValue(answer),
      (problem) => live && setError((problem as Error).message),
    );
    return () => {
      live = false;
    };
  }, [account, peer, path]);
  return { value, error };
}

function CopyChatLink({ account, peer }: { account: number; peer: string }) {
  const [copied, setCopied] = useState("");
  async function copyLink() {
    const link = chatLink(location.origin, location.pathname, account, peer);
    if (!link) return;
    try {
      await navigator.clipboard.writeText(link);
      setCopied("Chat link copied");
    } catch {
      setCopied("Copying is unavailable in this browser.");
    }
  }
  return (
    <div className="screen-block">
      <button type="button" className="wide-button" onClick={copyLink}>
        <Link2 size={18} aria-hidden /> Copy chat link
      </button>
      {copied && <p role="status">{copied}</p>}
    </div>
  );
}

// The Correspondent Profile: the person's photo and name, with their
// @username, a phone Telegram shares and bio; their Reminder Schedules (ADR
// 58 Q12); and Copy chat link. The chat's AI controls, the emotion's details,
// what the person cares about and their traits are in its AI Menu (ADR 62 Q4,
// Q12).
function CorrespondentProfile({
  account,
  peer,
  title,
  photoId,
  onReminder,
  onBack,
}: {
  account: number;
  peer: string;
  title: string;
  photoId: string | null;
  onReminder?: (schedule?: string) => void;
  onBack: () => void;
}) {
  const { value: profile, error } = useRead<Profile>(account, peer, "/profile");
  const name = profile?.name || title;
  return (
    <ScreenFrame title="Profile" onBack={onBack}>
      <div className="profile-head">
        <Avatar
          account={account}
          peer={peer}
          name={title}
          photoId={photoId}
          className="profile-avatar"
        />
        <h2>{name}</h2>
        {profile?.username && <p>@{profile.username}</p>}
        {profile?.phone && <p>+{profile.phone}</p>}
      </div>
      {profile?.bio && <p className="profile-bio">{profile.bio}</p>}
      {!profile && !error && <Explanation>Reading the profile…</Explanation>}
      {error && <Note>{error}</Note>}
      <Section title="Reminders">
        <ProfileReminders
          account={account}
          peer={peer}
          onEdit={(schedule) => onReminder?.(schedule)}
          onAdd={() => onReminder?.()}
        />
      </Section>
      <CopyChatLink account={account} peer={peer} />
    </ScreenFrame>
  );
}

// A group member's profile (ADR 51 Q14): their photo and name, @username, a
// phone Telegram shares and bio, read on opening and kept nowhere; no
// emotion or memory.
function MemberProfile({
  account,
  group,
  member,
  title,
  onMessage,
  onBack,
}: {
  account: number;
  group: string;
  member: string;
  title: string;
  onMessage?: (chat: Chat) => void;
  onBack: () => void;
}) {
  const [opening, setOpening] = useState("");
  async function message() {
    setOpening("Opening…");
    try {
      const chat = await workspaceApi<Chat>(openPath(account, member), {});
      setOpening("");
      onMessage?.(chat);
    } catch (problem) {
      setOpening((problem as Error).message);
    }
  }
  const { value: profile, error } = useRead<MemberProfileValue>(
    account,
    group,
    `/members/${encodeURIComponent(member)}/profile`,
  );
  const name = profile?.name || title;
  return (
    <ScreenFrame title="Profile" onBack={onBack}>
      <div className="profile-head">
        <Avatar
          account={account}
          peer={member}
          name={name}
          photoId={profile?.photo_id}
          className="profile-avatar"
        />
        <h2>{name}</h2>
        {profile?.username && <p>@{profile.username}</p>}
        {profile?.phone && <p>+{profile.phone}</p>}
      </div>
      {profile?.bio && <p className="profile-bio">{profile.bio}</p>}
      {!profile && !error && <Explanation>Reading the profile…</Explanation>}
      {error && <Note>{error}</Note>}
      {onMessage && (
        <div className="screen-block">
          <button
            type="button"
            className="wide-button"
            onClick={() => void message()}
          >
            <MessageCircle size={18} aria-hidden /> Message
          </button>
          {opening && <p role="status">{opening}</p>}
        </div>
      )}
    </ScreenFrame>
  );
}

// The Group Profile: the group's photo and title, its @username when public,
// its member count and description, with no member list, then Copy chat
// link. Group AI processing and Hide bots are in the group's AI Menu (ADR 62
// Q13).
function GroupProfileScreen({
  account,
  peer,
  title,
  photoId,
  onBack,
}: {
  account: number;
  peer: string;
  title: string;
  photoId: string | null;
  onBack: () => void;
}) {
  const { value: profile, error } = useRead<GroupProfile>(
    account,
    peer,
    "/group-profile",
  );
  const name = profile?.title || title;
  const members = profile?.members;
  return (
    <ScreenFrame title="Group" onBack={onBack}>
      <div className="profile-head">
        <Avatar
          account={account}
          peer={peer}
          name={title}
          photoId={photoId}
          className="profile-avatar"
        />
        <h2>{name}</h2>
        {profile?.username && <p>@{profile.username}</p>}
        {typeof members === "number" && (
          <p>{`${members.toLocaleString("en-US")} ${members === 1 ? "member" : "members"}`}</p>
        )}
      </div>
      {profile?.description && (
        <p className="profile-bio">{profile.description}</p>
      )}
      {!profile && !error && <Explanation>Reading the group…</Explanation>}
      {error && <Note>{error}</Note>}
      <CopyChatLink account={account} peer={peer} />
    </ScreenFrame>
  );
}
