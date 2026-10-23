import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import { EyeOff, Search, Trash2, X } from "lucide-react";
import { ApiError } from "./accounts";
import { chatApi, type NativeMessage } from "./conversation-api";
import {
  DELETE_LABEL,
  deletable,
  deletionDetails,
  deletionRequest,
  deletionStatus,
  intentIdentity,
  matchesReview,
  mediaLabel,
  operationActive,
  shownOperations,
  targetPending,
  type DeletionOperation,
} from "../../shared/deletion";
import "./deletion.css";

// Message Selection and deletion in the app's design (ADR 31, ADR 34, ADR 46
// CU4): Select in an original's sheet in Originals, taps add and remove, a
// bar with "N selected", Delete and Cancel, a review sheet of exactly the
// reviewed originals, and operation cards. The service owns accepted intent
// and progress; the client keeps what the owner reviewed and, across a
// reload, only the opaque identities it submitted.

type Review = {
  id: string;
  messages: NativeMessage[];
  retryOf?: string;
  selectionEpoch?: number;
  // Once sent, its captured identity and payload are immutable, including
  // after a response is lost. Another Delete repeats only this same intent.
  posted: boolean;
};
type Submission = {
  id: string;
  messages: NativeMessage[];
  retryOf?: string;
  error: string;
  selectionEpoch?: number;
};
type CachedRead = {
  values: NativeMessage[];
  missing: number[];
  generation: number;
};

export type DeletionSelection = {
  active: boolean;
  // A selection holds one kind, set by its first pick (ADR 52 Q11): current
  // originals to delete, or deleted messages to hide.
  mode: "delete" | "hide";
  count: number;
  isSelected: (identity: number) => boolean;
  eligible: (message: NativeMessage) => boolean;
  select: (message: NativeMessage) => void;
  toggle: (message: NativeMessage) => void;
  pending: (identity: number) => boolean;
  // Hides deleted messages for good, from the sheet or the selection.
  hide: (messages: NativeMessage[]) => void;
};
type DeletionContext = DeletionSelection & {
  ownOnly: boolean;
  operations: DeletionOperation[];
  submissions: Submission[];
  busy: string | null;
  reviewing: boolean;
  error: string;
  cancel: () => void;
  reviewSelection: () => void;
  check: (operation: DeletionOperation) => void;
  checkSubmission: (identity: string) => void;
  reviewSubmission: (identity: string) => void;
  retry: (operation: DeletionOperation) => void;
  hideSelection: () => void;
  notes: Record<string, string>;
};
const Context = createContext<DeletionContext | null>(null);

export function useMessageDeletion(): DeletionSelection | null {
  return useContext(Context);
}

// Why a submission is unconfirmed until something more specific is known.
const LOST = "The answer to this deletion didn't arrive.";

function captured(message: NativeMessage): NativeMessage {
  // The review keeps the wording and media the owner saw rather than
  // sharing a live object with the next refresh.
  return structuredClone(message);
}

export function MessageDeletionProvider({
  account,
  peer,
  accountName,
  chatName,
  sources,
  operations,
  replyHolds,
  onChange,
  onSources,
  version,
  allowed = true,
  originals = true,
  ownOnly = false,
  retained = false,
  children,
}: {
  account: number;
  peer: string;
  accountName: string;
  chatName: string;
  sources: NativeMessage[];
  operations: DeletionOperation[];
  replyHolds: Record<number, "pending" | "unresolved" | "deleted">;
  onChange: () => Promise<void> | void;
  onSources?: (values: NativeMessage[], missing: number[]) => void;
  // The conversation's data, so a new read re-checks what is selected.
  version?: unknown;
  // Deletion is available here: a group needs its access (ADR 34).
  allowed?: boolean;
  // Selection starts in Originals; leaving the view ends it.
  originals?: boolean;
  // A group deletes only originals verified as the owner's (ADR 34).
  ownOnly?: boolean;
  // Retained group work starts nothing new (CU6).
  retained?: boolean;
  children: React.ReactNode;
}) {
  const [active, setActive] = useState(false);
  const [mode, setMode] = useState<"delete" | "hide">("delete");
  const [selected, setSelected] = useState<Record<number, number>>({});
  const [confirmation, setConfirmation] = useState<Review | null>(null);
  const [submitting, setSubmitting] = useState<Review | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [localOperations, setLocalOperations] = useState<DeletionOperation[]>(
    [],
  );
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [, setCacheRevision] = useState(0);
  const alive = useRef(true);
  const checking = useRef(false);
  const reviewRequest = useRef(0);
  const cacheRead = useRef(0);
  const snapshot = useRef(version);
  const selectionEpoch = useRef(0);
  const current = useRef(new Map<number, NativeMessage>());
  const cache = useRef(new Map<number, NativeMessage>());
  const missingCache = useRef(new Map<number, NativeMessage | undefined>());
  const selection = useRef(selected);
  const reviewRef = useRef(confirmation);
  const selectedActive = useRef(active);
  const modeRef = useRef(mode);
  const submissionRef = useRef(submissions);
  const refresh = useRef(onChange);
  const receive = useRef(onSources);
  const operationRef = useRef<DeletionOperation[]>([]);
  const owner = `${account}/${peer}`;
  const ownerRef = useRef(owner);
  const storageKey = `deletion-submission:${account}/${peer}`;
  selection.current = selected;
  reviewRef.current = confirmation;
  selectedActive.current = active;
  modeRef.current = mode;
  submissionRef.current = submissions;
  ownerRef.current = owner;
  refresh.current = onChange;
  receive.current = onSources;
  if (!Object.is(snapshot.current, version)) {
    snapshot.current = version;
    ++cacheRead.current;
  }
  current.current = new Map(
    sources.map((message) => [message.message_id, message]),
  );
  for (const [identity, previous] of missingCache.current) {
    const shown = current.current.get(identity);
    if (
      !shown ||
      !previous ||
      shown.revision > previous.revision ||
      shown.deleted !== previous.deleted
    )
      missingCache.current.delete(identity);
    else current.current.delete(identity);
  }
  for (const [identity, value] of cache.current) {
    const shown = current.current.get(identity);
    if (
      !shown ||
      value.revision > shown.revision ||
      (value.revision === shown.revision && value.deleted && !shown.deleted)
    )
      current.current.set(identity, value);
    else cache.current.delete(identity);
  }
  const allOperations = Array.from(
    new Map(
      [...operations, ...localOperations].map((operation) => [
        operation.id,
        operation,
      ]),
    ).values(),
  ).filter(
    (operation) => operation.account_id === account && operation.peer === peer,
  );
  operationRef.current = allOperations;

  function valid() {
    return alive.current && ownerRef.current === owner;
  }

  function currentOriginal(message: NativeMessage) {
    return deletable(message, account, ownOnly);
  }

  // A deleted message shown with what it last said (ADR 52 Q11).
  function hideable(message: NativeMessage) {
    return !!message.deleted && !message.service;
  }

  // What the current selection may hold.
  function fits(message: NativeMessage) {
    return modeRef.current === "hide"
      ? hideable(message)
      : currentOriginal(message);
  }

  function remember(values: Submission[]) {
    // Only opaque identities survive a reload; accepted intent and progress
    // belong to the service.
    try {
      if (values.length)
        sessionStorage.setItem(
          storageKey,
          JSON.stringify(values.map((value) => value.id)),
        );
      else sessionStorage.removeItem(storageKey);
    } catch {
      // Storage can be unavailable; the identity stays in memory.
    }
  }

  function saveSubmission(value: Submission) {
    setSubmissions((prior) => {
      const next = [...prior.filter((item) => item.id !== value.id), value];
      remember(next);
      return next;
    });
  }

  function resolvedSubmission(identity: string) {
    setSubmissions((prior) => {
      if (!prior.some((value) => value.id === identity)) return prior;
      const next = prior.filter((value) => value.id !== identity);
      remember(next);
      return next;
    });
  }

  function accepted(operation: DeletionOperation) {
    setLocalOperations((prior) => [
      ...prior.filter((value) => value.id !== operation.id),
      operation,
    ]);
    resolvedSubmission(operation.id);
  }

  function owned(operation: DeletionOperation, identity: string) {
    if (
      operation.id !== identity ||
      operation.account_id !== account ||
      operation.peer !== peer
    )
      throw new Error(
        "Deletion result belongs to another account or conversation.",
      );
    return operation;
  }

  // An accepted deletion ends the selection it came from; a selection
  // started since then stays.
  function endSelection(epoch?: number) {
    if (
      epoch !== undefined &&
      epoch === selectionEpoch.current &&
      selectedActive.current
    )
      cancel();
  }

  function submitted(operation: DeletionOperation, epoch?: number) {
    accepted(operation);
    endSelection(epoch);
    setConfirmation(null);
  }

  // Reads the current originals, 100 at a time, and keeps the answer only
  // while nothing newer was shown meanwhile.
  async function cached(identities: number[]): Promise<CachedRead | null> {
    const generation = ++cacheRead.current;
    const values: NativeMessage[] = [];
    const missing: number[] = [];
    const unique = [...new Set(identities)];
    const observed = new Map(
      unique.map((identity) => [identity, current.current.get(identity)]),
    );
    const fresh = () => valid() && generation === cacheRead.current;
    for (let offset = 0; offset < unique.length; offset += 100) {
      if (!fresh()) return null;
      const result = await chatApi<{
        messages: NativeMessage[];
        missing: number[];
      }>(account, peer, "/originals/current", {
        identities: unique.slice(offset, offset + 100),
      });
      values.push(...result.messages);
      missing.push(...result.missing);
    }
    if (!fresh()) return null;
    for (const message of values) {
      const shown = current.current.get(message.message_id);
      if (
        (!shown && observed.get(message.message_id)) ||
        (shown &&
          (shown.revision > message.revision ||
            (shown.deleted && !message.deleted) ||
            (shown.revision === message.revision && shown.date > message.date)))
      )
        return null;
    }
    for (const identity of missing) {
      const shown = current.current.get(identity);
      const previous = observed.get(identity);
      // Absence has no revision: it prunes only what this read started
      // with, never a later edit, tombstone or reappearance.
      if (
        shown &&
        (!previous ||
          shown.revision !== previous.revision ||
          shown.deleted !== previous.deleted ||
          shown.date !== previous.date)
      )
        return null;
    }
    for (const message of values) {
      cache.current.set(message.message_id, message);
      missingCache.current.delete(message.message_id);
      current.current.set(message.message_id, message);
    }
    for (const identity of missing) {
      missingCache.current.set(identity, current.current.get(identity));
      cache.current.delete(identity);
      current.current.delete(identity);
    }
    receive.current?.(values, missing);
    setSelected((prior) => {
      const result = { ...prior };
      for (const identity of missing) delete result[identity];
      for (const message of values)
        if (!fits(message)) delete result[message.message_id];
      return Object.keys(result).length === Object.keys(prior).length
        ? prior
        : result;
    });
    setCacheRevision((value) => value + 1);
    return { values, missing, generation };
  }

  function cancel() {
    ++reviewRequest.current;
    ++cacheRead.current;
    setActive(false);
    setMode("delete");
    modeRef.current = "delete";
    setSelected({});
    setError("");
    setReviewing(false);
    // An accepted or submitting operation keeps its own lifetime: this
    // clears the selection only.
  }

  useEffect(() => {
    alive.current = true;
    cancel();
    setConfirmation(null);
    setSubmitting(null);
    setLocalOperations([]);
    setNotes({});
    checking.current = false;
    cache.current.clear();
    missingCache.current.clear();
    let values: string[] = [];
    try {
      const stored: unknown = JSON.parse(
        sessionStorage.getItem(storageKey) || "[]",
      );
      if (Array.isArray(stored))
        values = stored.filter(
          (value): value is string =>
            typeof value === "string" && /^[0-9a-f]{32}$/.test(value),
        );
    } catch {
      remember([]);
    }
    setSubmissions(
      values.map((id) => ({
        id,
        messages: [],
        error: LOST,
      })),
    );
    for (const identity of values)
      void chatApi<DeletionOperation>(account, peer, `/deletions/${identity}`)
        .then((operation) => valid() && accepted(owned(operation, identity)))
        .catch(() => {
          // A missing record is no proof of deletion; the owner can check
          // this identity again, never replayed automatically.
        });
    return () => {
      alive.current = false;
      ++reviewRequest.current;
      ++cacheRead.current;
    };
  }, [account, peer]);

  useEffect(() => {
    // The conversation's own read becomes authoritative once it includes a
    // returned operation.
    const identities = new Set(operations.map((operation) => operation.id));
    setLocalOperations((prior) => {
      const next = prior.filter((operation) => !identities.has(operation.id));
      return next.length === prior.length ? prior : next;
    });
    const review = reviewRef.current;
    for (const operation of operations) {
      const known = submissionRef.current.find(
        (value) => value.id === operation.id,
      );
      endSelection(known?.selectionEpoch);
      resolvedSubmission(operation.id);
      if (review?.posted && operation.id === review.id)
        submitted(operation, review.selectionEpoch);
    }
  }, [operations]);

  useEffect(() => {
    // A message deleted through Enloq leaves at once (ADR 52 Q8), from
    // loaded history too.
    const gone = operations.flatMap((operation) =>
      operation.targets
        .filter((target) => target.outcome === "acknowledged")
        .map((target) => target.message_id),
    );
    if (gone.length) receive.current?.([], gone);
  }, [operations]);

  useEffect(() => {
    if (!allowed || !originals) cancel();
  }, [allowed, originals]);

  useEffect(() => {
    if (!active) return;
    setSelected((prior) => {
      const next = Object.fromEntries(
        Object.entries(prior).filter(([identity]) => {
          const message = current.current.get(Number(identity));
          return message && fits(message);
        }),
      );
      return Object.keys(next).length === Object.keys(prior).length
        ? prior
        : next;
    });
  }, [active, sources, ownOnly, account]);

  useEffect(() => {
    const identities = [
      ...Object.keys(selection.current).map(Number),
      ...(reviewRef.current?.messages.map((message) => message.message_id) ||
        []),
    ];
    if (!identities.length) return;
    let fresh = true;
    void cached(identities).catch((cause) => {
      if (valid() && fresh) setError((cause as Error).message);
    });
    return () => {
      fresh = false;
    };
  }, [version, active]);

  useEffect(() => {
    if (!active) return;
    const key = (event: KeyboardEvent) => {
      if (
        event.key === "Escape" &&
        !event.defaultPrevented &&
        !reviewRef.current
      )
        cancel();
    };
    document.addEventListener("keydown", key);
    return () => document.removeEventListener("keydown", key);
  }, [active]);

  function pending(identity: number) {
    return (
      replyHolds[identity] === "pending" ||
      !!submitting?.messages.some(
        (message) => message.message_id === identity,
      ) ||
      allOperations.some((operation) => targetPending(operation, identity))
    );
  }

  function eligible(message: NativeMessage) {
    // Before a selection starts either kind may begin one.
    const hiding = active ? mode === "hide" : hideable(message);
    if (hiding) return originals && hideable(message);
    return (
      allowed &&
      originals &&
      !retained &&
      currentOriginal(message) &&
      !missingCache.current.has(message.message_id) &&
      !pending(message.message_id) &&
      !replyHolds[message.message_id] &&
      !submissions.some((submission) =>
        submission.messages.some(
          (value) => value.message_id === message.message_id,
        ),
      )
    );
  }

  function select(message: NativeMessage) {
    if (!eligible(message)) return;
    if (!selectedActive.current) {
      ++selectionEpoch.current;
      const kind = hideable(message) ? "hide" : "delete";
      setMode(kind);
      modeRef.current = kind;
    }
    setActive(true);
    setError("");
    setSelected((prior) => ({
      ...prior,
      [message.message_id]: message.revision,
    }));
  }

  function toggle(message: NativeMessage) {
    if (!active || !eligible(message)) return;
    setError("");
    setSelected((prior) => {
      const next = { ...prior };
      if (next[message.message_id] !== undefined)
        delete next[message.message_id];
      else next[message.message_id] = message.revision;
      return next;
    });
  }

  async function prepareReview(messages: NativeMessage[], retryOf?: string) {
    if (
      !valid() ||
      !allowed ||
      (!retryOf && (retained || !originals)) ||
      !messages.length ||
      busy
    )
      return;
    const sequence = ++reviewRequest.current;
    setReviewing(true);
    setError("");
    try {
      const result = await cached(
        messages.map((message) => message.message_id),
      );
      if (!valid() || sequence !== reviewRequest.current) return;
      if (!result || result.generation !== cacheRead.current) {
        setError(
          "Originals changed while reviewing. Review the current originals again.",
        );
        return;
      }
      const latest = new Map(
        result.values.map((message) => [message.message_id, message]),
      );
      const retry = retryOf
        ? allOperations.find((operation) => operation.id === retryOf)
        : null;
      const retryTargets = new Map(
        retry?.retry_targets.map((target) => [
          target.message_id,
          target.revision,
        ]) || [],
      );
      const reviewed = messages.map((message) =>
        latest.get(message.message_id),
      );
      if (
        reviewed.some(
          (message) =>
            !message ||
            !currentOriginal(message) ||
            (retryOf
              ? retryTargets.get(message.message_id) !== message.revision
              : !eligible(message)),
        )
      ) {
        setError(
          retryOf
            ? "An original changed or is unavailable. Check result again before retrying."
            : "An original changed or is no longer eligible. Review the current originals again.",
        );
        return;
      }
      const originals = reviewed as NativeMessage[];
      if (active)
        setSelected((prior) => ({
          ...prior,
          ...Object.fromEntries(
            originals.map((message) => [message.message_id, message.revision]),
          ),
        }));
      setConfirmation({
        id: intentIdentity(),
        messages: originals.map(captured),
        retryOf,
        ...(selectedActive.current
          ? { selectionEpoch: selectionEpoch.current }
          : {}),
        posted: false,
      });
    } catch (cause) {
      if (valid() && sequence === reviewRequest.current)
        setError((cause as Error).message);
    } finally {
      if (valid() && sequence === reviewRequest.current) setReviewing(false);
    }
  }

  function reviewSelection() {
    void prepareReview(
      Object.keys(selected)
        .map((identity) => current.current.get(Number(identity)))
        .filter((message): message is NativeMessage => !!message)
        .sort((a, b) => a.message_id - b.message_id),
    );
  }

  function reviewSubmission(identity: string) {
    if (!valid() || checking.current) return;
    const submission = submissionRef.current.find(
      (value) => value.id === identity,
    );
    if (
      !submission?.messages.length ||
      operationRef.current.some((operation) => operation.id === identity)
    )
      return;
    ++reviewRequest.current;
    setReviewing(false);
    setError("");
    setConfirmation({
      id: submission.id,
      messages: submission.messages.map(captured),
      retryOf: submission.retryOf,
      selectionEpoch: submission.selectionEpoch,
      posted: true,
    });
  }

  async function checkSubmission(identity: string) {
    if (!valid() || checking.current) return;
    const known = submissionRef.current.find((value) => value.id === identity);
    checking.current = true;
    setBusy(identity);
    setNotes((prior) => ({ ...prior, [identity]: "" }));
    try {
      const operation = await chatApi<DeletionOperation>(
        account,
        peer,
        `/deletions/${identity}`,
      );
      if (!valid()) return;
      accepted(owned(operation, identity));
      endSelection(known?.selectionEpoch);
      if (reviewRef.current?.id === identity) setConfirmation(null);
      await refresh.current();
    } catch (cause) {
      if (valid())
        setNotes((prior) => ({
          ...prior,
          [identity]:
            // The service refuses an identity it holds no record of.
            cause instanceof ApiError &&
            cause.status >= 400 &&
            cause.status < 500
              ? "Submission not found. Its deletion result is unconfirmed."
              : (cause as Error).message,
        }));
    } finally {
      if (valid()) {
        checking.current = false;
        setBusy(null);
      }
    }
  }

  async function check(operation: DeletionOperation) {
    if (!valid() || checking.current || !operation.checkable) return;
    checking.current = true;
    setBusy(operation.id);
    setNotes((prior) => ({ ...prior, [operation.id]: "" }));
    try {
      const value = await chatApi<DeletionOperation>(
        account,
        peer,
        `/deletions/${operation.id}/check`,
        {},
      );
      if (!valid()) return;
      accepted(owned(value, operation.id));
      await refresh.current();
    } catch (cause) {
      if (valid())
        setNotes((prior) => ({
          ...prior,
          [operation.id]: (cause as Error).message,
        }));
    } finally {
      if (valid()) {
        checking.current = false;
        setBusy(null);
      }
    }
  }

  // Retry remaining reviews the operation's unresolved originals afresh;
  // a new Delete creates a new identity.
  async function retry(operation: DeletionOperation) {
    if (
      !valid() ||
      !operation.retryable ||
      !operation.retry_targets.length ||
      busy
    )
      return;
    const sequence = ++reviewRequest.current;
    setReviewing(true);
    setError("");
    try {
      const result = await cached(
        operation.retry_targets.map((target) => target.message_id),
      );
      if (!valid() || sequence !== reviewRequest.current) return;
      if (!result || result.generation !== cacheRead.current) {
        setError(
          "Originals changed while reviewing. Check result again before retrying.",
        );
        return;
      }
      setReviewing(false);
      await prepareReview(result.values, operation.id);
    } catch (cause) {
      if (valid() && sequence === reviewRequest.current) {
        setError((cause as Error).message);
        setReviewing(false);
      }
    } finally {
      if (valid() && sequence === reviewRequest.current) setReviewing(false);
    }
  }

  function changed(review: Review) {
    if (!allowed) return true;
    return review.messages.some((message) => {
      const live = current.current.get(message.message_id);
      return (
        !live ||
        !currentOriginal(live) ||
        live.revision !== message.revision ||
        (ownOnly && live.deletion_fingerprint !== message.deletion_fingerprint)
      );
    });
  }

  async function submit() {
    const value = reviewRef.current;
    if (
      !valid() ||
      !value ||
      checking.current ||
      (!value.posted && changed(value))
    )
      return;
    checking.current = true;
    const posted = { ...value, posted: true };
    setConfirmation(posted);
    setSubmitting(posted);
    setBusy(value.id);
    setError("");
    saveSubmission({
      id: value.id,
      messages: value.messages,
      retryOf: value.retryOf,
      error: LOST,
      selectionEpoch: value.selectionEpoch,
    });
    try {
      const operation = await chatApi<DeletionOperation>(
        account,
        peer,
        "/deletions",
        deletionRequest(value.id, value.messages, ownOnly, value.retryOf),
        AbortSignal.timeout(15000),
      );
      if (!valid()) return;
      if (!matchesReview(operation, value.id, account, peer, value.messages))
        throw new Error(
          "Deletion result does not match the reviewed originals.",
        );
      submitted(operation, value.selectionEpoch);
      try {
        await refresh.current();
      } catch (cause) {
        if (valid()) setError((cause as Error).message);
      }
    } catch (cause) {
      if (!valid()) return;
      const known = operationRef.current.find(
        (operation) => operation.id === value.id,
      );
      if (known) {
        submitted(known, value.selectionEpoch);
        return;
      }
      // A refusal is definite: the intent was not accepted. Anything else
      // leaves the submission unconfirmed until it is checked.
      const definite =
        cause instanceof ApiError && cause.status >= 400 && cause.status < 500;
      if (definite) {
        resolvedSubmission(value.id);
        setConfirmation((prior) =>
          prior?.id === value.id ? { ...prior, posted: false } : prior,
        );
      } else {
        saveSubmission({
          id: value.id,
          messages: value.messages,
          retryOf: value.retryOf,
          error: (cause as Error).message,
          selectionEpoch: value.selectionEpoch,
        });
      }
      setError((cause as Error).message);
    } finally {
      if (valid()) {
        checking.current = false;
        setSubmitting(null);
        setBusy(null);
      }
    }
  }

  async function hide(messages: NativeMessage[]) {
    const identities = messages
      .filter(hideable)
      .map((message) => message.message_id);
    if (!identities.length || busy) return;
    setBusy("hide");
    setError("");
    try {
      await chatApi(account, peer, "/hidden", { identities });
      if (!valid()) return;
      for (const identity of identities) cache.current.delete(identity);
      receive.current?.([], identities);
      if (selectedActive.current && modeRef.current === "hide") cancel();
      await refresh.current();
    } catch (cause) {
      if (valid()) setError((cause as Error).message);
    } finally {
      if (valid()) setBusy(null);
    }
  }

  const stale = confirmation && !confirmation.posted && changed(confirmation);
  const value: DeletionContext = {
    ownOnly,
    active,
    mode,
    count: Object.keys(selected).length,
    isSelected: (identity) => selected[identity] !== undefined,
    eligible,
    select,
    toggle,
    pending,
    operations: allOperations,
    submissions,
    busy,
    reviewing,
    error,
    cancel,
    reviewSelection,
    check: (operation) => void check(operation),
    checkSubmission: (identity) => void checkSubmission(identity),
    reviewSubmission,
    retry: (operation) => void retry(operation),
    hide: (messages) => void hide(messages),
    hideSelection: () =>
      void hide(
        Object.keys(selected)
          .map((identity) => current.current.get(Number(identity)))
          .filter((message): message is NativeMessage => !!message),
      ),
    notes,
  };
  const close = () => {
    ++reviewRequest.current;
    setConfirmation(null);
    setError("");
  };
  return (
    <Context.Provider value={value}>
      {children}
      {confirmation && (
        <div className="sheet-scrim" onClick={() => !busy && close()}>
          <div
            className="sheet-bottom deletion-review"
            role="dialog"
            aria-modal="true"
            aria-labelledby="deletion-review-title"
            onClick={(event) => event.stopPropagation()}
          >
            <h2 id="deletion-review-title">Delete messages</h2>
            <dl className="deletion-destination">
              <div>
                <dt>Account</dt>
                <dd>{accountName}</dd>
              </div>
              <div>
                <dt>Chat</dt>
                <dd>{chatName}</dd>
              </div>
            </dl>
            <p className="deletion-count">
              {confirmation.messages.length}{" "}
              {confirmation.messages.length === 1 ? "message" : "messages"}
            </p>
            <ol className="deletion-previews" aria-label="Messages to delete">
              {confirmation.messages.map((message) => (
                <li key={message.message_id}>
                  <small>
                    {ownOnly || message.outgoing ? "You" : chatName}
                  </small>
                  {message.text && <p>{message.text}</p>}
                  {message.media && (
                    <span className="deletion-media">
                      {mediaLabel(message)}
                    </span>
                  )}
                </li>
              ))}
            </ol>
            {stale && (
              <p className="deletion-error" role="alert">
                {allowed
                  ? "A message changed. Review the updated messages before deleting."
                  : "Group access is unavailable. Deletion is paused."}
              </p>
            )}
            {error && (
              <p className="deletion-error" role="alert">
                {error}
              </p>
            )}
            {confirmation.posted && !busy && (
              <p className="deletion-unconfirmed" role="status">
                Submission unconfirmed
              </p>
            )}
            <div className="deletion-actions">
              <button
                type="button"
                className="sheet-button"
                disabled={!!busy}
                onClick={close}
              >
                Cancel
              </button>
              {stale ? (
                <button
                  type="button"
                  className="sheet-button primary"
                  disabled={!allowed || reviewing || !!busy}
                  onClick={() =>
                    void prepareReview(
                      confirmation.messages
                        .map((message) =>
                          current.current.get(message.message_id),
                        )
                        .filter(
                          (message): message is NativeMessage =>
                            !!message && currentOriginal(message),
                        ),
                      confirmation.retryOf,
                    )
                  }
                >
                  Review updated messages
                </button>
              ) : (
                <button
                  type="button"
                  className="sheet-button danger"
                  disabled={!!busy || reviewing}
                  onClick={() => void submit()}
                >
                  {submitting ? "Deleting…" : DELETE_LABEL(ownOnly)}
                </button>
              )}
              {confirmation.posted && !busy && (
                <button
                  type="button"
                  className="sheet-button"
                  onClick={() => void checkSubmission(confirmation.id)}
                >
                  Check submission
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </Context.Provider>
  );
}

// The composer stays mounted, keeping its draft, while the selection bar
// takes its place.
export function ComposerSlot({ children }: { children: React.ReactNode }) {
  const deletion = useContext(Context);
  return (
    <div className="composer-slot" hidden={!!deletion?.active}>
      {children}
    </div>
  );
}

// "N selected" with Delete and Cancel, in the composer's place.
export function SelectionBar() {
  const deletion = useContext(Context);
  if (!deletion?.active) return null;
  return (
    <>
      {deletion.error && (
        <p className="selection-error" role="alert">
          {deletion.error}
        </p>
      )}
      <div
        className="selection-bar"
        role="toolbar"
        aria-label="Message selection"
      >
        <button
          type="button"
          aria-label="Cancel selection"
          onClick={deletion.cancel}
        >
          <X size={22} aria-hidden />
        </button>
        <strong aria-live="polite">{deletion.count} selected</strong>
        {deletion.mode === "hide" ? (
          // Deleted messages leave this chat for good (ADR 52 Q11).
          <button
            type="button"
            aria-label="Hide selected messages"
            disabled={!deletion.count || !!deletion.busy}
            onClick={deletion.hideSelection}
          >
            <EyeOff size={22} aria-hidden />
          </button>
        ) : (
          <button
            type="button"
            className="selection-delete"
            aria-label="Delete selected messages"
            disabled={!deletion.count || deletion.reviewing || !!deletion.busy}
            onClick={deletion.reviewSelection}
          >
            <Trash2 size={22} aria-hidden />
          </button>
        )}
      </div>
    </>
  );
}

// Each unfinished deletion, the newest completed one and any unconfirmed
// submission, under the conversation's latest message.
export function DeletionCards() {
  const deletion = useContext(Context);
  if (!deletion) return null;
  const shown = shownOperations(deletion.operations);
  if (!shown.length && !deletion.submissions.length && !deletion.error)
    return null;
  return (
    <div className="deletion-cards">
      {deletion.error && !deletion.active && (
        <p className="deletion-error" role="alert">
          {deletion.error}
        </p>
      )}
      {deletion.submissions.map((submission) => (
        <section
          className="deletion-card"
          key={submission.id}
          aria-label="Unconfirmed deletion"
        >
          <strong role="status">Submission unconfirmed</strong>
          <p>{submission.error}</p>
          <div className="deletion-card-actions">
            <button
              type="button"
              disabled={!!deletion.busy}
              onClick={() => deletion.checkSubmission(submission.id)}
            >
              <Search size={16} aria-hidden /> Check submission
            </button>
            {!!submission.messages.length && (
              <button
                type="button"
                disabled={!!deletion.busy || deletion.reviewing}
                onClick={() => deletion.reviewSubmission(submission.id)}
              >
                Review submission
              </button>
            )}
          </div>
          {deletion.notes[submission.id] && (
            <p role="alert">{deletion.notes[submission.id]}</p>
          )}
        </section>
      ))}
      {shown.map((operation) => (
        <section
          className="deletion-card"
          key={operation.id}
          aria-label="Deletion progress"
        >
          <strong role="status">{deletionStatus(operation)}</strong>
          {operationActive(operation) && (
            <progress
              aria-label="Messages deleted"
              max={operation.counts.total}
              value={operation.counts.acknowledged}
            />
          )}
          {deletionDetails(operation, deletion.ownOnly).map((line) => (
            <p key={line}>{line}</p>
          ))}
          <div className="deletion-card-actions">
            {operation.checkable && operation.state !== "completed" && (
              <button
                type="button"
                disabled={!!deletion.busy}
                onClick={() => deletion.check(operation)}
              >
                <Search size={16} aria-hidden /> Check result
              </button>
            )}
            {operation.retryable && (
              <button
                type="button"
                disabled={!!deletion.busy || deletion.reviewing}
                onClick={() => deletion.retry(operation)}
              >
                Retry remaining
              </button>
            )}
          </div>
          {deletion.notes[operation.id] && (
            <p role="alert">{deletion.notes[operation.id]}</p>
          )}
        </section>
      ))}
    </div>
  );
}
