import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  ArrowDownUp,
  Check,
  ChevronDown,
  ChevronRight,
  LoaderCircle,
  RotateCw,
  TriangleAlert,
  Wallet,
} from "lucide-react";
import {
  chatApi,
  type DailyBriefing,
  type NativeMessage,
} from "./conversation-api";
import { OriginalBubble, type MenuTarget } from "./Bubble";
import { groupSender, messageRuns } from "../../shared/runs";
import { albumRuns } from "../../shared/gallery";
import { UnreadSeparator } from "./UnreadSeparator";
import { messageTime } from "../../shared/messageTime";
import {
  activePrefix,
  briefingSlot,
  dayKey,
  dayOf,
  groupDays,
  streamedTopics,
  transientSlot,
  unreadPlace,
  type BriefingSlot as Slot,
  type SlotView,
} from "../../shared/groupBriefings";
import { type useHideBots } from "./HideBots";
import { useWithheldViewing } from "./LocalViewing";
import "./briefings.css";

const SLOT_ICONS: Record<Slot["kind"], React.ReactNode> = {
  waiting: (
    <LoaderCircle size={14} className="work-spinner" aria-hidden="true" />
  ),
  update: <RotateCw size={14} aria-hidden="true" />,
  retry: <TriangleAlert size={14} aria-hidden="true" />,
  budget: <Wallet size={14} aria-hidden="true" />,
  syncing: <ArrowDownUp size={14} aria-hidden="true" />,
  up_to_date: <Check size={14} aria-hidden="true" />,
};

// Today's band slot (ADR 34): one icon and a word at most, with no
// tooltip. Green and amber act on a tap; grey only reports. Nothing shows
// while AI is Off or today has nothing eligible.
function BriefingSlot({
  slot,
  locked,
  onAct,
  onBudget,
}: {
  slot: Slot;
  locked: boolean;
  onAct: (path: string) => void;
  onBudget?: () => void;
}) {
  // A spent Group Briefing Budget opens it in Settings (ADR 45 Q8).
  if (slot.kind === "budget" && onBudget)
    return (
      <button
        type="button"
        className="briefing-slot"
        aria-label={slot.label}
        onClick={onBudget}
      >
        {SLOT_ICONS[slot.kind]}
        {slot.word}
      </button>
    );
  if (slot.path) {
    const path = slot.path;
    return (
      <button
        type="button"
        className={"briefing-slot " + (slot.kind === "retry" ? "retry" : "act")}
        aria-label={slot.label}
        disabled={locked}
        onClick={() => onAct(path)}
      >
        {SLOT_ICONS[slot.kind]}
        {slot.word}
      </button>
    );
  }
  return (
    <span className="briefing-slot" role="status" aria-label={slot.label}>
      {SLOT_ICONS[slot.kind]}
      {slot.word}
    </span>
  );
}

export function DailyBriefings({
  account,
  peer,
  title,
  briefings,
  messages,
  view,
  timezone,
  locked,
  updateView,
  hideBots,
  sent,
  unread,
  scroll,
  revealRef,
  onPause,
  onSources,
  onMenu,
  onMember,
  onBudget,
  onChange,
}: {
  account: number;
  peer: string;
  // The group's name, for its originals' author lines.
  title: string;
  briefings: DailyBriefing[];
  messages: NativeMessage[];
  view: "ai" | "originals";
  timezone: string;
  locked: boolean;
  updateView?: SlotView;
  hideBots: ReturnType<typeof useHideBots>;
  // Increments with each send from this conversation's composer.
  sent: number;
  unread: number | null;
  scroll: React.RefObject<HTMLDivElement | null>;
  // A Reply Quote's out-of-sight message is brought into view here (ADR 41).
  revealRef?: React.MutableRefObject<
    ((source: number) => "positioned" | "revealing" | false) | null
  >;
  onPause: () => void;
  onSources: (messages: NativeMessage[]) => void;
  // A long press opens the message's sheet (ADR 44 MW2).
  onMenu: (target: MenuTarget) => void;
  // A member's avatar or name opens their profile (ADR 51 Q14).
  onMember?: (message: NativeMessage) => void;
  onBudget?: () => void;
  onChange: () => Promise<void>;
}) {
  // Message Selection returns with CU4.
  const selecting = false;
  const root = useRef<HTMLDivElement>(null);
  const latest = useRef(briefings);
  latest.current = briefings;
  const mounted = useRef(true);
  const request = useRef(0);
  const [originalDays, setOriginalDays] = useState<Set<string>>(new Set());
  const [citation, setCitation] = useState<{
    day: string;
    source: number;
    revision: number;
    result: string;
    highlight: boolean;
  } | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [nearby, setNearby] = useState(false);
  const [working, setWorking] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  // Days whose "new messages" spoiler the owner opened; kept until the chat
  // closes. A send from the composer opens today's, so the message shows.
  const [opened, setOpened] = useState<Set<string>>(new Set());
  // A hidden post a Reply Quote revealed; Hide Bots itself is unchanged.
  const [revealed, setRevealed] = useState<number | null>(null);
  // A catch-up request in flight briefly blocks admission; the slot keeps its
  // last settled state through it rather than flickering to Syncing.
  const settled = useRef<SlotView | undefined>(undefined);
  const transient = transientSlot(updateView);
  const slotView = transient ? (settled.current ?? updateView) : updateView;
  useEffect(() => {
    if (!transient) settled.current = updateView;
  });
  useEffect(() => {
    setOpened(new Set());
    settled.current = undefined;
  }, [account, peer]);
  useEffect(() => {
    if (sent)
      setOpened(
        (previous) => new Set([...previous, dayKey(new Date(), timezone)]),
      );
  }, [sent]);
  const saved = useRef(new Map<string, number>());
  const positioned = useRef<string | null>(null);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      ++request.current;
    };
  }, []);
  useEffect(() => {
    ++request.current;
    setOriginalDays(new Set());
    setCitation(null);
    setRevealed(null);
    setBusy(null);
    saved.current.clear();
    positioned.current = null;
    setError("");
  }, [view, account, peer]);
  useEffect(() => {
    if (!selecting) return;
    ++request.current;
    setBusy(null);
  }, [selecting]);

  function article(day: string) {
    return Array.from(
      root.current?.querySelectorAll<HTMLElement>("[data-briefing-day]") || [],
    ).find((node) => node.dataset.briefingDay === day);
  }
  // The pinned date band covers the top of the stream; targets land below it.
  function clearance() {
    return (
      root.current
        ?.querySelector<HTMLElement>(".briefing-day-band")
        ?.getBoundingClientRect().height ?? 0
    );
  }
  function position(node: HTMLElement, offset = 16 + clearance()) {
    const element = scroll.current;
    if (!element) return;
    element.scrollTop +=
      node.getBoundingClientRect().top -
      element.getBoundingClientRect().top -
      offset;
    element.dispatchEvent(new Event("scroll"));
  }
  useLayoutEffect(() => {
    if (!citation || selecting) return;
    const current = messages.find(
      (message) => message.message_id === citation.source,
    );
    const final = briefings.find((brief) => brief.day === citation.day)?.final;
    if (
      !current ||
      current.deleted ||
      current.revision !== citation.revision ||
      final?.id !== citation.result
    ) {
      setCitation(null);
      setError("The cited source changed or is no longer available locally.");
      return;
    }
    const frame = requestAnimationFrame(() => {
      const key =
        citation.day + "/" + citation.source + "/" + citation.revision;
      if (positioned.current === key) return;
      const target = article(
        citation.day,
      )?.parentElement?.querySelector<HTMLElement>(
        '[data-source-id="' + citation.source + '"]',
      );
      if (target) {
        position(target);
        positioned.current = key;
      }
    });
    return () => cancelAnimationFrame(frame);
  }, [citation, messages, briefings, selecting]);

  async function reveal(
    brief: DailyBriefing,
    source: number,
    highlight = true,
  ) {
    if (!brief.final || activePrefix(brief)) return;
    const sequence = ++request.current;
    const result = brief.final.id;
    const element = scroll.current;
    const currentArticle = article(brief.day);
    if (element && currentArticle)
      saved.current.set(
        brief.day,
        currentArticle.getBoundingClientRect().top -
          element.getBoundingClientRect().top,
      );
    onPause();
    setBusy(brief.day);
    setError("");
    try {
      const page = await chatApi<{
        day: string;
        result: string;
        source: number;
        messages: NativeMessage[];
        before: boolean;
        after: boolean;
      }>(
        account,
        peer,
        "/briefings/" +
          encodeURIComponent(brief.day) +
          (highlight ? "/sources?result=" : "/originals?result=") +
          encodeURIComponent(result) +
          (highlight ? "&source=" + source : ""),
      );
      if (!mounted.current || sequence !== request.current) return;
      const fresh = latest.current.find((value) => value.day === brief.day);
      const target = page.messages.find(
        (message) => message.message_id === source,
      );
      if (
        page.day !== brief.day ||
        page.result !== result ||
        page.source !== source ||
        fresh?.final?.id !== result ||
        activePrefix(fresh) ||
        !target ||
        target.deleted
      )
        throw new Error(
          "The cited source changed or is no longer available locally.",
        );
      onSources(page.messages);
      positioned.current = null;
      setOriginalDays((previous) => new Set([...previous, brief.day]));
      setCitation({
        day: brief.day,
        source,
        revision: target.revision,
        result,
        highlight,
      });
      setNearby(page.before || page.after);
    } catch (e) {
      if (mounted.current && sequence === request.current)
        setError((e as Error).message);
    } finally {
      if (mounted.current && sequence === request.current) setBusy(null);
    }
  }

  // ADR 41 Q12: a folded message opens as a citation does; one under the
  // "new messages" fold opens it; a hidden bot post shows on its own.
  function revealForQuote(identity: number) {
    const message = messages.find((m) => m.message_id === identity);
    if (!message || selecting) return false;
    const day = dayOf(message, timezone);
    const brief = briefings.find((value) => value.day === day);
    const showing = view === "originals" || originalDays.has(day);
    if (
      !showing &&
      brief?.final &&
      !activePrefix(brief) &&
      brief.final.covered_ids.includes(identity)
    ) {
      void reveal(brief, identity);
      return "positioned" as const;
    }
    if (!showing && (brief?.final || activePrefix(brief)))
      setOpened((previous) => new Set([...previous, day]));
    setRevealed(identity);
    return "revealing" as const;
  }
  useEffect(() => {
    if (!revealRef) return;
    revealRef.current = revealForQuote;
    return () => {
      revealRef.current = null;
    };
  });

  function returnToBriefing(day: string) {
    ++request.current;
    onPause();
    setCitation(null);
    setError("");
    setOriginalDays((previous) => {
      const next = new Set(previous);
      next.delete(day);
      return next;
    });
    const offset = saved.current.get(day) ?? 16 + clearance();
    saved.current.delete(day);
    requestAnimationFrame(() => {
      const target = article(day);
      if (mounted.current && target) position(target, offset);
    });
  }

  // Update and Retry share the slot. A refused click only rereads the state,
  // which then shows why nothing started (Waiting, Budget, Syncing or ✓).
  async function act(path: string) {
    if (working || briefings.some((brief) => brief.attempt?.state === "active"))
      return;
    setWorking(true);
    try {
      await chatApi(account, peer, path, {});
    } catch {
      // The reread below carries the current state.
    }
    try {
      if (mounted.current) await onChange();
    } finally {
      if (mounted.current) setWorking(false);
    }
  }

  function toggleNew(day: string) {
    onPause();
    setOpened((previous) => {
      const next = new Set(previous);
      if (next.has(day)) next.delete(day);
      else next.add(day);
      return next;
    });
  }

  const today = dayKey(new Date(), timezone);
  const days = groupDays({
    messages,
    briefings,
    view,
    timezone,
    hideBots: hideBots.hideBots,
    today,
    originalDays,
    opened,
    cited: citation?.highlight ? citation.source : null,
    revealed,
    selecting,
  });
  // Messages under a closed spoiler are not viewed until it opens.
  useWithheldViewing(days.flatMap((value) => value.folded));
  return (
    <div ref={root} className="group-days">
      {error && <p role="alert">{error}</p>}
      {days.map((entry) => {
        const {
          day,
          brief,
          prefix,
          showingOriginals,
          hasWording,
          originals,
          summarized,
          fresh,
          label,
        } = entry;
        const slot =
          entry.today && view === "ai" && !selecting
            ? briefingSlot(slotView, { working, count: fresh.length })
            : null;
        const hidden = originals.filter(
          (message) => !entry.visible.has(message.message_id),
        );
        const anchors = hidden.map((message) => (
          <span
            key={message.message_id}
            data-source-id={message.message_id}
            data-source-ids={message.message_id}
            aria-hidden="true"
            className="briefing-hidden-source"
          />
        ));
        if (!entry.drawn)
          return <React.Fragment key={day}>{anchors}</React.Fragment>;
        return (
          <section key={day} className="briefing-day">
            <div className="briefing-day-band">
              <h3 className="briefing-day-heading">{label}</h3>
              {slot && (
                <BriefingSlot
                  slot={slot}
                  locked={locked}
                  onAct={(path) => void act(path)}
                  onBudget={onBudget}
                />
              )}
            </div>
            <article
              className="daily-briefing"
              data-briefing-day={day}
              data-source-ids={
                !showingOriginals ? Array.from(summarized).join(" ") : ""
              }
              aria-label={"Daily briefing for " + label}
            >
              {unreadPlace(entry, unread) === "card" && <UnreadSeparator />}
              {!selecting &&
                showingOriginals &&
                originalDays.has(day) &&
                view === "ai" && (
                  <div className="briefing-source-tools">
                    <button onClick={() => returnToBriefing(day)}>
                      Return to briefing
                    </button>
                    {nearby && (
                      <small>
                        Showing cached originals near the cited source.
                      </small>
                    )}
                  </div>
                )}
              {!showingOriginals && brief && (
                <>
                  {prefix ? (
                    <div className="briefing-text">
                      {streamedTopics(prefix).map((topic, index) => (
                        <div className="briefing-topic" key={index}>
                          <h4>{topic.title}</h4>
                          {topic.body && <p>{topic.body}</p>}
                        </div>
                      ))}
                    </div>
                  ) : brief.final ? (
                    <div className="briefing-text">
                      {brief.final.local_outcome ? (
                        <p>
                          {brief.final.local_outcome === "ads_skipped"
                            ? "Ads skipped"
                            : "Bot messages skipped"}
                        </p>
                      ) : brief.final.outcome === "no_substance" ? (
                        <p>No substantive updates in the covered messages.</p>
                      ) : (
                        brief.final.topics.map((topic, index) => {
                          const key = brief.final!.id + "/" + index;
                          const participants = topic.participants || [];
                          const names = expanded.has(key)
                            ? participants
                            : participants.slice(0, 3);
                          return (
                            <div className="briefing-topic" key={key}>
                              {topic.title && <h4>{topic.title}</h4>}
                              <p>
                                {topic.text}{" "}
                                <span className="briefing-citations">
                                  {topic.citations.map((identity) => (
                                    <button
                                      key={identity}
                                      aria-label={"Source " + identity}
                                      disabled={busy === day}
                                      onClick={() =>
                                        void reveal(brief, identity)
                                      }
                                    >
                                      #{identity}
                                    </button>
                                  ))}
                                </span>
                              </p>
                              {!!participants.length && (
                                <div className="briefing-participants">
                                  {names.map((participant) => (
                                    <span key={participant.peer}>
                                      {participant.name}
                                    </span>
                                  ))}
                                  {participants.length > 3 && (
                                    <button
                                      aria-expanded={expanded.has(key)}
                                      onClick={() =>
                                        setExpanded((previous) => {
                                          const next = new Set(previous);
                                          if (next.has(key)) next.delete(key);
                                          else next.add(key);
                                          return next;
                                        })
                                      }
                                    >
                                      {expanded.has(key)
                                        ? "Show fewer"
                                        : "+" +
                                          (participants.length - 3) +
                                          " more"}
                                    </button>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })
                      )}
                    </div>
                  ) : null}
                  {brief.final && !prefix && (
                    <small className="briefing-coverage">
                      {"Covered " +
                        messageTime(brief.final.covered_start, timezone) +
                        "–" +
                        messageTime(brief.final.covered_end, timezone)}
                      {brief.partial ? " · Partial coverage" : ""}
                      {brief.cached ? " · Cached briefing" : ""}
                    </small>
                  )}
                  {!prefix && hasWording && (
                    <button
                      className="briefing-originals"
                      disabled={busy === day}
                      onClick={() =>
                        void reveal(brief, brief.final!.covered_ids[0], false)
                      }
                    >
                      Show day originals
                    </button>
                  )}
                </>
              )}
              {brief && !prefix && (brief.partial || brief.gap) && (
                <small className="briefing-missing">
                  {brief.omissions.length
                    ? brief.omissions.length + " cached sources not covered. "
                    : ""}
                  {brief.intake_coverage.native_omitted
                    ? brief.intake_coverage.native_omitted +
                      " additional originals were outside the native intake limit. "
                    : ""}
                  {!brief.intake_coverage.range_complete || brief.gap
                    ? "The complete day's originals have not been verified."
                    : ""}
                </small>
              )}
            </article>
            {(() => {
              // Message Runs (ADR 51 Q13) among the visible originals of a
              // list; the fold between the listed and the new ones breaks them.
              const runsOf = (list: NativeMessage[]) => {
                const visible = list.filter(
                  (message) =>
                    entry.visible.has(message.message_id) &&
                    !inAlbum.has(message.message_id),
                );
                const places = messageRuns(
                  visible.map((message) => ({
                    sender: message.service
                      ? `service:${message.message_id}`
                      : groupSender(message, account),
                    date: message.date,
                    separated: unread === message.message_id,
                  })),
                  timezone,
                );
                return new Map(
                  visible.map((message, index) => [
                    message.message_id,
                    places[index]!,
                  ]),
                );
              };
              // Telegram albums among the visible originals, as one grid
              // (ADR 51 Q12).
              const albums = new Map<number, NativeMessage[]>();
              const inAlbum = new Set<number>();
              for (const list of [entry.listed, fresh])
                for (const album of albumRuns(
                  list.filter((message) =>
                    entry.visible.has(message.message_id),
                  ),
                ))
                  if (album.length > 1) {
                    albums.set(album[0]!.message_id, album);
                    for (const message of album.slice(1))
                      inAlbum.add(message.message_id);
                  }
              const listedRuns = runsOf(entry.listed);
              const freshRuns = runsOf(fresh);
              const shown = (message: NativeMessage) =>
                inAlbum.has(message.message_id) ? null : !entry.visible.has(
                    message.message_id,
                  ) ? (
                  <span
                    key={message.message_id}
                    data-source-id={message.message_id}
                    data-source-ids={message.message_id}
                    aria-hidden="true"
                    className="briefing-hidden-source"
                  />
                ) : (
                  <React.Fragment key={message.message_id}>
                    {unread === message.message_id && <UnreadSeparator />}
                    <OriginalBubble
                      message={message}
                      account={account}
                      peer={peer}
                      name={title}
                      group
                      run={
                        listedRuns.get(message.message_id) ??
                        freshRuns.get(message.message_id)
                      }
                      album={albums.get(message.message_id)}
                      onMember={onMember}
                      timezone={timezone}
                      originals={view === "originals"}
                      highlighted={
                        !!citation?.highlight &&
                        citation.day === day &&
                        citation.source === message.message_id
                      }
                      onMenu={onMenu}
                    />
                  </React.Fragment>
                );
              if (!entry.spoiler) return entry.listed.map(shown);
              // Messages the briefing does not cover yet fold under one spoiler
              // that lands with the Unread line above it when they start the
              // unseen run (ADR 34).
              const count = fresh.length;
              return (
                <>
                  {entry.listed.map(shown)}
                  <div
                    className="briefing-new"
                    data-source-ids={
                      entry.open
                        ? undefined
                        : fresh.map((message) => message.message_id).join(" ")
                    }
                  >
                    {unreadPlace(entry, unread) === "fold" && (
                      <UnreadSeparator />
                    )}
                    <button
                      type="button"
                      className="briefing-new-toggle"
                      aria-expanded={entry.open}
                      onClick={() => toggleNew(day)}
                    >
                      {entry.open ? (
                        <ChevronDown size={14} aria-hidden="true" />
                      ) : (
                        <ChevronRight size={14} aria-hidden="true" />
                      )}
                      {count} new {count === 1 ? "message" : "messages"}
                    </button>
                    {entry.open && fresh.map(shown)}
                  </div>
                </>
              );
            })()}
          </section>
        );
      })}
    </div>
  );
}
