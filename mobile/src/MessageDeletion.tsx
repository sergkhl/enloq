import Ionicons from "@expo/vector-icons/Ionicons";
import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  BackHandler,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
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
import { Refused } from "./api";
import { readCached, removeCached, writeCached } from "./cache";
import { useConnection } from "./connection";
import type { NativeMessage } from "./conversation";
import { usePalette, type Palette } from "./theme";

// Message Selection and deletion in the app (ADR 31, ADR 34, ADR 46 CU4):
// Select in an original's sheet in Originals, taps add and remove, a bar
// with "N selected", Delete and Cancel, a review sheet of exactly the
// reviewed originals, and operation cards. The service owns accepted intent
// and progress; the phone keeps what the owner reviewed and, across a
// restart, only the identities it submitted, as the web does.

type Review = {
  id: string;
  messages: NativeMessage[];
  retryOf?: string;
  selectionEpoch?: number;
  // Once sent, its identity and payload are fixed, including after a lost
  // answer: Delete again repeats only this same intent.
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

// Why a submission is unconfirmed until something more specific is known.
const LOST = "The answer to this deletion didn't arrive.";
const storageKey = (account: number, peer: string) =>
  `deletion:${account}/${peer}`;

export type DeletionState = {
  ownOnly: boolean;
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
  operations: DeletionOperation[];
  submissions: Submission[];
  busy: string | null;
  reviewing: boolean;
  submitting: boolean;
  error: string;
  notes: Record<string, string>;
  confirmation: Review | null;
  stale: boolean;
  allowed: boolean;
  cancel: () => void;
  close: () => void;
  reviewSelection: () => void;
  reviewUpdated: () => void;
  submit: () => void;
  check: (operation: DeletionOperation) => void;
  checkSubmission: (identity: string) => void;
  reviewSubmission: (identity: string) => void;
  retry: (operation: DeletionOperation) => void;
  // Hides deleted messages for good, from the sheet or the selection.
  hide: (messages: NativeMessage[]) => void;
  hideSelection: () => void;
};

export const DeletionContext = createContext<DeletionState | null>(null);

export function useMessageDeletion() {
  return useContext(DeletionContext);
}

export function useMessageDeletionState({
  account,
  peer,
  sources,
  operations,
  replyHolds,
  onChange,
  version,
  allowed = true,
  originals = true,
  ownOnly = false,
  retained = false,
}: {
  account: number;
  peer: string;
  sources: NativeMessage[];
  operations: DeletionOperation[];
  replyHolds: Record<number, string>;
  onChange: () => Promise<unknown> | void;
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
}): DeletionState {
  const { api } = useConnection();
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
  const operationRef = useRef<DeletionOperation[]>([]);
  const owner = `${account}/${peer}`;
  const ownerRef = useRef(owner);
  const path = `accounts/${account}/chats/${encodeURIComponent(peer)}`;
  selection.current = selected;
  reviewRef.current = confirmation;
  selectedActive.current = active;
  modeRef.current = mode;
  submissionRef.current = submissions;
  ownerRef.current = owner;
  refresh.current = onChange;
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
    // Only the identities survive a restart; accepted intent and progress
    // belong to the service.
    if (values.length)
      writeCached(
        storageKey(account, peer),
        values.map((value) => value.id),
      );
    else removeCached(storageKey(account, peer));
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

  function cancel() {
    ++reviewRequest.current;
    ++cacheRead.current;
    setActive(false);
    setMode("delete");
    modeRef.current = "delete";
    setSelected({});
    setError("");
    setReviewing(false);
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
      const result = await api<{
        messages: NativeMessage[];
        missing: number[];
      }>(`${path}/originals/current`, {
        body: { identities: unique.slice(offset, offset + 100) },
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

  useEffect(() => {
    alive.current = true;
    cancel();
    setConfirmation(null);
    setSubmitting(null);
    setLocalOperations([]);
    setNotes({});
    setSubmissions([]);
    checking.current = false;
    cache.current.clear();
    missingCache.current.clear();
    void readCached<unknown>(storageKey(account, peer)).then((stored) => {
      if (!valid()) return;
      const values = Array.isArray(stored)
        ? stored.filter(
            (value): value is string =>
              typeof value === "string" && /^[0-9a-f]{32}$/.test(value),
          )
        : [];
      if (!values.length) return;
      setSubmissions((prior) => [
        ...prior,
        ...values
          .filter((id) => !prior.some((value) => value.id === id))
          .map((id) => ({ id, messages: [], error: LOST })),
      ]);
      for (const identity of values)
        void api<DeletionOperation>(`${path}/deletions/${identity}`)
          .then((operation) => valid() && accepted(owned(operation, identity)))
          .catch(() => {
            // A missing record is no proof of deletion; the owner can check
            // this identity again, never replayed automatically.
          });
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

  // Android's Back ends the selection before it leaves the chat.
  useEffect(() => {
    if (!active) return;
    const subscription = BackHandler.addEventListener(
      "hardwareBackPress",
      () => {
        if (reviewRef.current) return false;
        cancel();
        return true;
      },
    );
    return () => subscription.remove();
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
    if (active && !currentOriginal(message)) return false;
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
      const reviewedOriginals = reviewed as NativeMessage[];
      if (active)
        setSelected((prior) => ({
          ...prior,
          ...Object.fromEntries(
            reviewedOriginals.map((message) => [
              message.message_id,
              message.revision,
            ]),
          ),
        }));
      setConfirmation({
        id: intentIdentity(),
        // The review keeps what the owner saw, apart from later reads.
        messages: reviewedOriginals.map((message) => ({ ...message })),
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
      messages: submission.messages.map((message) => ({ ...message })),
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
      const operation = await api<DeletionOperation>(
        `${path}/deletions/${identity}`,
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
          // The service refuses an identity it holds no record of.
          [identity]:
            cause instanceof Refused && cause.status < 500
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
      const value = await api<DeletionOperation>(
        `${path}/deletions/${operation.id}/check`,
        { body: {} },
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
    const timeout = new AbortController();
    const timer = setTimeout(() => timeout.abort(), 15000);
    try {
      const operation = await api<DeletionOperation>(`${path}/deletions`, {
        body: deletionRequest(value.id, value.messages, ownOnly, value.retryOf),
        signal: timeout.signal,
      });
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
      if (cause instanceof Refused && cause.status < 500) {
        resolvedSubmission(value.id);
        setConfirmation((prior) =>
          prior?.id === value.id ? { ...prior, posted: false } : prior,
        );
      } else {
        saveSubmission({
          id: value.id,
          messages: value.messages,
          retryOf: value.retryOf,
          error: timeout.signal.aborted
            ? "The server didn't answer in time."
            : (cause as Error).message,
          selectionEpoch: value.selectionEpoch,
        });
      }
      setError(
        timeout.signal.aborted
          ? "The server didn't answer in time."
          : (cause as Error).message,
      );
    } finally {
      clearTimeout(timer);
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
      await api(`${path}/hidden`, { body: { identities } });
      if (!valid()) return;
      if (selectedActive.current && modeRef.current === "hide") cancel();
      await refresh.current();
    } catch (cause) {
      if (valid()) setError((cause as Error).message);
    } finally {
      if (valid()) setBusy(null);
    }
  }

  function reviewUpdated() {
    if (!confirmation) return;
    void prepareReview(
      confirmation.messages
        .map((message) => current.current.get(message.message_id))
        .filter(
          (message): message is NativeMessage =>
            !!message && currentOriginal(message),
        ),
      confirmation.retryOf,
    );
  }

  return {
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
    submitting: !!submitting,
    error,
    notes,
    confirmation,
    stale: !!confirmation && !confirmation.posted && changed(confirmation),
    allowed,
    cancel,
    close: () => {
      ++reviewRequest.current;
      setConfirmation(null);
      setError("");
    },
    reviewSelection,
    reviewUpdated,
    submit: () => void submit(),
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
  };
}

// A row while selecting: the round mark beside the bubble, and the whole
// row adds or removes it instead of opening links or media.
export function Selectable({
  message,
  version = false,
  album,
  children,
}: {
  message: NativeMessage;
  // While an earlier version shows, the row isn't selectable.
  version?: boolean;
  // An album is selected as a whole: each of its items it may delete
  // (ADR 51 Q12).
  album?: NativeMessage[];
  children: React.ReactNode;
}) {
  const deletion = useMessageDeletion();
  const palette = usePalette();
  if (!deletion?.active || message.service) return <>{children}</>;
  const members = (album ?? [message]).filter((item) =>
    deletion.eligible(item),
  );
  const selectable = !version && members.length > 0;
  const chosen =
    selectable && members.every((item) => deletion.isSelected(item.message_id));
  const text = message.text.trim() || mediaLabel(message) || "Message";
  return (
    <View style={[styles.selectRow, selectable ? null : styles.ineligible]}>
      <Ionicons
        name={chosen ? "checkmark-circle" : "ellipse-outline"}
        size={26}
        color={selectable ? palette.accent : "transparent"}
        accessibilityElementsHidden
        importantForAccessibility="no"
      />
      <View style={styles.selectBody}>
        {children}
        {selectable ? (
          <Pressable
            style={StyleSheet.absoluteFill}
            accessibilityRole="checkbox"
            accessibilityLabel={`Select: ${text.length > 60 ? `${text.slice(0, 59)}…` : text}`}
            accessibilityState={{ checked: chosen }}
            onPress={() => {
              for (const item of members)
                if (deletion.isSelected(item.message_id) === chosen)
                  deletion.toggle(item);
            }}
          />
        ) : (
          // Rows that can't go only shield their links and media from taps.
          <View
            style={StyleSheet.absoluteFill}
            accessible={false}
            onStartShouldSetResponder={() => true}
          />
        )}
      </View>
    </View>
  );
}

// "Deleting…" under an original still being deleted.
export function DeletingMark({
  message,
  color,
}: {
  message: NativeMessage;
  color: string;
}) {
  const deletion = useMessageDeletion();
  if (!deletion?.pending(message.message_id)) return null;
  return <Text style={[styles.small, { color }]}>Deleting…</Text>;
}

// "N selected" with Delete and Cancel, in the composer's place.
export function SelectionBar({ deletion }: { deletion: DeletionState }) {
  const palette = usePalette();
  if (!deletion.active) return null;
  const off = !deletion.count || deletion.reviewing || !!deletion.busy;
  return (
    <>
      {deletion.error && !deletion.confirmation ? (
        <Text
          accessibilityRole="alert"
          style={[styles.barError, { color: palette.error }]}
        >
          {deletion.error}
        </Text>
      ) : null}
      <View
        accessibilityLabel="Message selection"
        style={[
          styles.bar,
          { borderColor: palette.line, backgroundColor: palette.background },
        ]}
      >
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Cancel selection"
          hitSlop={8}
          onPress={deletion.cancel}
          style={styles.barButton}
        >
          <Ionicons name="close" size={26} color={palette.accent} />
        </Pressable>
        <Text
          accessibilityLiveRegion="polite"
          style={[styles.count, { color: palette.text }]}
        >
          {`${deletion.count} selected`}
        </Text>
        {deletion.mode === "hide" ? (
          // Deleted messages leave this chat for good (ADR 52 Q11).
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Hide selected messages"
            accessibilityState={{ disabled: off }}
            disabled={off}
            hitSlop={8}
            onPress={deletion.hideSelection}
            style={[styles.barButton, { opacity: off ? 0.4 : 1 }]}
          >
            <Ionicons name="eye-off-outline" size={24} color={palette.accent} />
          </Pressable>
        ) : (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Delete selected messages"
            accessibilityState={{ disabled: off }}
            disabled={off}
            hitSlop={8}
            onPress={deletion.reviewSelection}
            style={[styles.barButton, { opacity: off ? 0.4 : 1 }]}
          >
            <Ionicons name="trash-outline" size={24} color={palette.error} />
          </Pressable>
        )}
      </View>
    </>
  );
}

function SheetButton({
  label,
  tone = "plain",
  disabled,
  onPress,
  palette,
}: {
  label: string;
  tone?: "plain" | "primary" | "danger";
  disabled?: boolean;
  onPress: () => void;
  palette: Palette;
}) {
  const fill =
    tone === "danger"
      ? palette.error
      : tone === "primary"
        ? palette.accent
        : palette.panel;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.sheetButton,
        {
          backgroundColor: fill,
          borderColor: tone === "plain" ? palette.line : fill,
          opacity: disabled ? 0.5 : pressed ? 0.8 : 1,
        },
      ]}
    >
      <Text
        style={[
          styles.sheetButtonLabel,
          { color: tone === "plain" ? palette.accent : palette.background },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

// The review: the account and chat, the exact messages, then Delete.
export function DeletionReview({
  deletion,
  accountName,
  chatName,
}: {
  deletion: DeletionState;
  accountName: string;
  chatName: string;
}) {
  const palette = usePalette();
  const review = deletion.confirmation;
  const busy = !!deletion.busy;
  return (
    <Modal
      visible={!!review}
      transparent
      animationType="fade"
      onRequestClose={() => !busy && deletion.close()}
    >
      <View style={styles.backdrop}>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Close"
          style={StyleSheet.absoluteFill}
          onPress={() => !busy && deletion.close()}
        />
        {review ? (
          <SafeAreaView
            edges={["bottom"]}
            style={[styles.sheet, { backgroundColor: palette.background }]}
          >
            <Text
              accessibilityRole="header"
              style={[styles.title, { color: palette.text }]}
            >
              Delete messages
            </Text>
            <View style={styles.destination}>
              <Text style={[styles.term, { color: palette.muted }]}>
                Account
              </Text>
              <Text style={[styles.value, { color: palette.text }]}>
                {accountName}
              </Text>
            </View>
            <View style={styles.destination}>
              <Text style={[styles.term, { color: palette.muted }]}>Chat</Text>
              <Text style={[styles.value, { color: palette.text }]}>
                {chatName}
              </Text>
            </View>
            <Text style={[styles.reviewCount, { color: palette.text }]}>
              {`${review.messages.length} ${review.messages.length === 1 ? "message" : "messages"}`}
            </Text>
            <ScrollView
              accessibilityLabel="Messages to delete"
              style={styles.previews}
            >
              {review.messages.map((message) => (
                <View
                  key={message.message_id}
                  style={[styles.preview, { backgroundColor: palette.panel }]}
                >
                  <Text style={[styles.small, { color: palette.muted }]}>
                    {deletion.ownOnly || message.outgoing ? "You" : chatName}
                  </Text>
                  {message.text ? (
                    <Text style={{ color: palette.text }}>{message.text}</Text>
                  ) : null}
                  {message.media ? (
                    <Text style={[styles.small, { color: palette.muted }]}>
                      {mediaLabel(message)}
                    </Text>
                  ) : null}
                </View>
              ))}
            </ScrollView>
            {deletion.stale ? (
              <Text
                accessibilityRole="alert"
                style={[styles.note, { color: palette.error }]}
              >
                {deletion.allowed
                  ? "A message changed. Review the updated messages before deleting."
                  : "Group access is unavailable. Deletion is paused."}
              </Text>
            ) : null}
            {deletion.error ? (
              <Text
                accessibilityRole="alert"
                style={[styles.note, { color: palette.error }]}
              >
                {deletion.error}
              </Text>
            ) : null}
            {review.posted && !busy ? (
              <Text style={[styles.note, { color: palette.muted }]}>
                Submission unconfirmed
              </Text>
            ) : null}
            <View style={styles.actions}>
              <SheetButton
                label="Cancel"
                disabled={busy}
                onPress={deletion.close}
                palette={palette}
              />
              {deletion.stale ? (
                <SheetButton
                  label="Review updated messages"
                  tone="primary"
                  disabled={!deletion.allowed || deletion.reviewing || busy}
                  onPress={deletion.reviewUpdated}
                  palette={palette}
                />
              ) : (
                <SheetButton
                  label={
                    deletion.submitting
                      ? "Deleting…"
                      : DELETE_LABEL(deletion.ownOnly)
                  }
                  tone="danger"
                  disabled={busy || deletion.reviewing}
                  onPress={deletion.submit}
                  palette={palette}
                />
              )}
            </View>
            {review.posted && !busy ? (
              <SheetButton
                label="Check submission"
                onPress={() => deletion.checkSubmission(review.id)}
                palette={palette}
              />
            ) : null}
          </SafeAreaView>
        ) : null}
      </View>
    </Modal>
  );
}

function CardButton({
  label,
  icon,
  disabled,
  onPress,
  palette,
}: {
  label: string;
  icon?: React.ComponentProps<typeof Ionicons>["name"];
  disabled?: boolean;
  onPress: () => void;
  palette: Palette;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled: !!disabled }}
      disabled={disabled}
      hitSlop={6}
      onPress={onPress}
      style={[styles.cardButton, { opacity: disabled ? 0.45 : 1 }]}
    >
      {icon ? <Ionicons name={icon} size={16} color={palette.accent} /> : null}
      <Text style={[styles.cardButtonLabel, { color: palette.accent }]}>
        {label}
      </Text>
    </Pressable>
  );
}

// Each unfinished deletion, the newest completed one and any unconfirmed
// submission, under the conversation's latest message.
export function DeletionCards({ deletion }: { deletion: DeletionState }) {
  const palette = usePalette();
  const shown = shownOperations(deletion.operations);
  if (!shown.length && !deletion.submissions.length && !deletion.error)
    return null;
  const card = [
    styles.card,
    { borderColor: palette.line, backgroundColor: palette.background },
  ];
  return (
    <View style={styles.cards}>
      {deletion.error && !deletion.active && !deletion.confirmation ? (
        <Text
          accessibilityRole="alert"
          style={[styles.note, { color: palette.error }]}
        >
          {deletion.error}
        </Text>
      ) : null}
      {deletion.submissions.map((submission) => (
        <View
          key={submission.id}
          accessibilityLabel="Unconfirmed deletion"
          style={card}
        >
          <Text style={[styles.cardStatus, { color: palette.text }]}>
            Submission unconfirmed
          </Text>
          <Text style={[styles.cardLine, { color: palette.muted }]}>
            {submission.error}
          </Text>
          <View style={styles.cardActions}>
            <CardButton
              label="Check submission"
              icon="search"
              disabled={!!deletion.busy}
              onPress={() => deletion.checkSubmission(submission.id)}
              palette={palette}
            />
            {submission.messages.length ? (
              <CardButton
                label="Review submission"
                disabled={!!deletion.busy || deletion.reviewing}
                onPress={() => deletion.reviewSubmission(submission.id)}
                palette={palette}
              />
            ) : null}
          </View>
          {deletion.notes[submission.id] ? (
            <Text
              accessibilityRole="alert"
              style={[styles.cardLine, { color: palette.error }]}
            >
              {deletion.notes[submission.id]}
            </Text>
          ) : null}
        </View>
      ))}
      {shown.map((operation) => (
        <View
          key={operation.id}
          accessibilityLabel="Deletion progress"
          style={card}
        >
          <Text
            accessibilityLiveRegion="polite"
            style={[styles.cardStatus, { color: palette.text }]}
          >
            {deletionStatus(operation)}
          </Text>
          {operationActive(operation) ? (
            <View
              accessibilityLabel="Messages deleted"
              accessibilityValue={{
                min: 0,
                max: operation.counts.total,
                now: operation.counts.acknowledged,
              }}
              style={[styles.track, { backgroundColor: palette.line }]}
            >
              <View
                style={[
                  styles.fill,
                  {
                    backgroundColor: palette.accent,
                    width: `${operation.counts.total ? (operation.counts.acknowledged / operation.counts.total) * 100 : 0}%`,
                  },
                ]}
              />
            </View>
          ) : null}
          {deletionDetails(operation, deletion.ownOnly).map((line) => (
            <Text
              key={line}
              style={[styles.cardLine, { color: palette.muted }]}
            >
              {line}
            </Text>
          ))}
          <View style={styles.cardActions}>
            {operation.checkable && operation.state !== "completed" ? (
              <CardButton
                label="Check result"
                icon="search"
                disabled={!!deletion.busy}
                onPress={() => deletion.check(operation)}
                palette={palette}
              />
            ) : null}
            {operation.retryable ? (
              <CardButton
                label="Retry remaining"
                disabled={!!deletion.busy || deletion.reviewing}
                onPress={() => deletion.retry(operation)}
                palette={palette}
              />
            ) : null}
          </View>
          {deletion.notes[operation.id] ? (
            <Text
              accessibilityRole="alert"
              style={[styles.cardLine, { color: palette.error }]}
            >
              {deletion.notes[operation.id]}
            </Text>
          ) : null}
        </View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  selectRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  selectBody: { flex: 1, minWidth: 0 },
  ineligible: { opacity: 0.55 },
  small: { fontSize: 12 },
  bar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: 56,
    paddingHorizontal: 10,
    borderTopWidth: StyleSheet.hairlineWidth,
  },
  barButton: {
    width: 44,
    height: 44,
    alignItems: "center",
    justifyContent: "center",
  },
  count: { flex: 1, fontSize: 17, fontWeight: "700" },
  barError: { paddingHorizontal: 16, paddingTop: 6, fontSize: 14 },
  backdrop: {
    flex: 1,
    justifyContent: "flex-end",
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  sheet: {
    gap: 10,
    maxHeight: "92%",
    paddingHorizontal: 18,
    paddingTop: 16,
    paddingBottom: 12,
    borderTopLeftRadius: 16,
    borderTopRightRadius: 16,
  },
  title: { fontSize: 19, fontWeight: "700" },
  destination: { flexDirection: "row", gap: 8 },
  term: { minWidth: 72, fontSize: 15 },
  value: { flex: 1, fontSize: 15 },
  reviewCount: { fontSize: 15, fontWeight: "600" },
  previews: { maxHeight: 280 },
  preview: { gap: 2, padding: 10, borderRadius: 10, marginBottom: 8 },
  note: { fontSize: 14 },
  actions: { flexDirection: "row", gap: 8 },
  sheetButton: {
    flex: 1,
    minHeight: 44,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderWidth: 1,
    borderRadius: 12,
  },
  sheetButtonLabel: { fontSize: 16, fontWeight: "600", textAlign: "center" },
  cards: { gap: 8, paddingVertical: 4 },
  card: {
    gap: 4,
    padding: 12,
    borderWidth: 1,
    borderRadius: 12,
  },
  cardStatus: { fontSize: 15, fontWeight: "700" },
  cardLine: { fontSize: 14 },
  track: { height: 4, borderRadius: 2, overflow: "hidden" },
  fill: { height: 4 },
  cardActions: { flexDirection: "row", flexWrap: "wrap", gap: 14 },
  cardButton: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    minHeight: 36,
  },
  cardButtonLabel: { fontSize: 15, fontWeight: "600" },
});
