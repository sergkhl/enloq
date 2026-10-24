import React, { useEffect, useState } from "react";
import { Link2, MessageCircle } from "lucide-react";
import { workspaceApi } from "./accounts";
import type { Chat } from "./conversation-api";
import { openPath } from "../../shared/newMessage";
import type { components } from "./api-schema";
import { Avatar } from "./Avatar";
import { chatApi } from "./conversation-api";
import { MemoryPanel } from "./MemoryPanel";
import {
  ChoiceRow,
  Explanation,
  Note,
  ScreenFrame,
  Section,
  SwitchRow,
} from "./screenUi";
import { TraitsPanel } from "./TraitsPanel";
import { chatLink } from "../../shared/chatRoute";
import { emotionDetails, type EmotionView } from "../../shared/emotion";
import {
  SUGGESTION_STYLES,
  type SuggestionStyle,
} from "../../shared/suggestions";

type Profile = components["schemas"]["CorrespondentProfile"];
type Calming = components["schemas"]["Calming"];
type Remembering = components["schemas"]["Remembering"];
type Suggesting = components["schemas"]["SuggestionSettings"];
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
  emotion: EmotionView | null;
  timezone: string;
  // A group member, opened from their avatar or name (ADR 51 Q14).
  member?: string;
  // Their private chat, opened by Message as New Message does.
  onMessage?: (chat: Chat) => void;
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

// Calm this chat (ADR 53 Q4): on unless turned off here. It applies to
// messages rewritten from then on; calmed ones stay calmed (Q9).
function CalmingSwitch({ account, peer }: { account: number; peer: string }) {
  const { value, error } = useRead<Calming>(account, peer, "/calming");
  const [saved, setSaved] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const enabled = saved ?? value?.enabled ?? null;
  async function change(next: boolean) {
    setBusy(true);
    setProblem("");
    try {
      const answer = await chatApi<Calming>(account, peer, "/calming", {
        enabled: next,
      });
      setSaved(answer.enabled);
    } catch (failure) {
      setProblem((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SwitchRow
        label="Soften hostile messages"
        explanation="Hostile wording is softened while keeping every fact, request, deadline and threat. Anger or frustration alone stays unchanged."
        checked={enabled ?? false}
        disabled={enabled === null || busy}
        onChange={(next) => void change(next)}
      />
      {(problem || error) && <Note>{problem || error}</Note>}
    </>
  );
}

// The chat's Suggestions choice (ADR 56 Q12 A): Off, On or, once it can be
// chosen, Idioms; On unless chosen here. A choice applies from the next
// message that qualifies; a set already written stays (Q5).
function SuggestionsChoice({
  account,
  peer,
}: {
  account: number;
  peer: string;
}) {
  const { value, error } = useRead<Suggesting>(
    account,
    peer,
    "/suggestions/settings",
  );
  const [saved, setSaved] = useState<Suggesting | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const current = saved ?? value;
  async function change(automatic: boolean, style: SuggestionStyle) {
    setBusy(true);
    setProblem("");
    try {
      setSaved(
        await chatApi<Suggesting>(account, peer, "/suggestions/settings", {
          automatic,
          style,
        }),
      );
    } catch (failure) {
      setProblem((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SwitchRow
        label="Automatic suggestions"
        checked={current?.automatic ?? true}
        disabled={!current || busy}
        explanation="Prepare suggestions automatically; manual generation stays available."
        onChange={(on) => current && void change(on, current.style)}
      />
      <ChoiceRow
        label="Style"
        options={SUGGESTION_STYLES}
        value={current?.style ?? null}
        explanation={
          SUGGESTION_STYLES.find((s) => s.value === current?.style)?.explanation
        }
        disabled={!current || busy}
        onChange={(style) => current && void change(current.automatic, style)}
      />
      {(problem || error) && <Note>{problem || error}</Note>}
    </>
  );
}

// Remember this chat (ADR 55 Q7, Q18): off unless turned on here since ADR
// 56 (Q9). Off, the chat teaches neither memory and nothing in it reads
// them; what's noted stays until cleared.
function RememberSwitch({ account, peer }: { account: number; peer: string }) {
  const { value, error } = useRead<Remembering>(account, peer, "/remember");
  const [saved, setSaved] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const enabled = saved ?? value?.enabled ?? null;
  async function change(next: boolean) {
    setBusy(true);
    setProblem("");
    try {
      const answer = await chatApi<Remembering>(account, peer, "/remember", {
        enabled: next,
      });
      setSaved(answer.enabled);
    } catch (failure) {
      setProblem((failure as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <SwitchRow
        label="Remember this chat"
        explanation="Off by default. On, Enloq notes what this person cares about and their traits, and what you care about from this chat. Off, this chat teaches and uses neither; what's noted stays until you clear it."
        checked={enabled ?? false}
        disabled={enabled === null || busy}
        onChange={(next) => void change(next)}
      />
      {(problem || error) && <Note>{problem || error}</Note>}
    </>
  );
}

// The Correspondent Profile: the person's photo and name, with their
// @username, a phone Telegram shares and bio; the emotion's details; Calm
// this chat, the Suggestions choice (ADR 56) and Remember this chat; what
// they care about and their traits (ADR 55); and Copy chat link.
function CorrespondentProfile({
  account,
  peer,
  title,
  photoId,
  emotion,
  timezone,
  onBack,
}: {
  account: number;
  peer: string;
  title: string;
  photoId: string | null;
  emotion: EmotionView | null;
  timezone: string;
  onBack: () => void;
}) {
  const { value: profile, error } = useRead<Profile>(account, peer, "/profile");
  const details = emotionDetails(emotion, timezone);
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
      <Section title="Emotion">
        <div className="screen-block profile-emotion">
          <strong>{details.label}</strong>
          <p>{details.reason}</p>
          <small>
            {details.source}
            {details.time ? ` · ${details.time}` : ""}
          </small>
        </div>
      </Section>
      <Section title="Soften hostile messages">
        <CalmingSwitch account={account} peer={peer} />
      </Section>
      <Section title="Suggestions">
        <SuggestionsChoice account={account} peer={peer} />
      </Section>
      <Section title="Memory">
        <RememberSwitch account={account} peer={peer} />
      </Section>
      <Section title="Cares about">
        <MemoryPanel account={account} peer={peer} />
      </Section>
      <Section title="Traits">
        <TraitsPanel account={account} peer={peer} />
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
// link. Group AI processing and Hide bots stay in the ⋮ sheet.
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
